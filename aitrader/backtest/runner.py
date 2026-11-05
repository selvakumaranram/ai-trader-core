from __future__ import annotations

import logging
import math
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional, Set, Tuple

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from aitrader.backtest.bars import load_market
from aitrader.backtest.costs import CostModel
from aitrader.backtest.engine import FLAG_LOOKBACK, simulate
from aitrader.backtest.groups import load_groups, load_sectors
from aitrader.backtest.events import blocked_entry_days, load_rbi_dates
from aitrader.backtest.index_signals import index_signals, index_summary
from aitrader.backtest.indicators import MIN_BARS, MIN_TRADED_VALUE, add_indicators
from aitrader.backtest.metrics import benchmark_curve, clean_json, summarize
from aitrader.backtest.strategies import INDEX_STRATEGIES, STRATEGIES
from aitrader.backtest.strategies.breakout import Breakout
from aitrader.backtest.strategies.price_action import PriceAction
from aitrader.backtest.strategies.pullback import Pullback
from aitrader.backtest.types import IndexSignal, MoneyRules, Trade
from aitrader.db import repo

log = logging.getLogger(__name__)

SOURCE = "yfinance"
BENCHMARK = "NIFTY 50"
UNSEEN_YEARS = 2
REGIME_SMA = 200
NO_DATA = "Load the stock list and price history first (Data tab)"
INTERRUPTED = "interrupted (service restarted)"
RBI_DATES_PATH: Optional[Path] = None  # None = the shipped aitrader/data/rbi_policy_dates.yaml
_LOCK_KEY = 72_026_102  # pg advisory lock: one backtest creation at a time


class BacktestInputError(ValueError):
    """Bad request; the API answers 400."""


class BacktestBusyError(RuntimeError):
    """A backtest is already running; the API answers 409."""


class _NoData(Exception):
    pass


@dataclass(frozen=True)
class BacktestRequest:
    strategy: str
    start: Optional[date] = None
    end: Optional[date] = None
    money: MoneyRules = field(default_factory=MoneyRules)
    exclude: Tuple[str, ...] = ()  # stocks left out of the universe (robustness checks)
    strategy_params: dict = field(default_factory=dict)  # per-run overrides of the strategy's PARAMS
    costs: Optional[CostModel] = None  # None = the default cost model
    notes: Tuple[str, ...] = ()  # shown in the report (e.g. why a combined run was chosen)


def _years_before(day: date, years: int) -> date:
    try:
        return day.replace(year=day.year - years)
    except ValueError:  # 29 Feb
        return day.replace(year=day.year - years, day=28)


def validate(req: BacktestRequest) -> None:
    if req.strategy not in STRATEGIES:
        raise BacktestInputError(f"Unknown strategy {req.strategy!r}; choose one of {', '.join(sorted(STRATEGIES))}")
    m = req.money
    if min(m.capital, m.risk_per_trade, m.daily_loss_limit, m.max_position_value) <= 0 or m.max_positions < 1:
        raise BacktestInputError("Money settings must be positive and allow at least one position")
    if m.risk_per_trade > m.capital:
        raise BacktestInputError("Risk per trade cannot exceed starting capital")
    if m.risk_mode not in ("fixed", "percent"):
        raise BacktestInputError("Risk mode must be 'fixed' or 'percent'")
    for label, v in (("Risk %", m.risk_pct), ("Daily loss %", m.daily_loss_pct),
                     ("Drawdown pause", m.max_drawdown_pause), ("Max charges ÷ risk", m.max_charge_to_risk)):
        if v is not None and not 0 < v < 1:
            raise BacktestInputError(f"{label} must be between 0 and 1 (e.g. 0.01 for 1%)")
    if m.pause_min_days < 0:
        raise BacktestInputError("Pause days cannot be negative")
    if req.start and req.end and req.end <= req.start:
        raise BacktestInputError("End date must be after the start date")


def resolve_range(conn: Connection, req: BacktestRequest) -> Optional[Tuple[date, date, date]]:
    inst = repo.instrument_by_symbol(conn, BENCHMARK)
    dates = repo.bar_dates(conn, inst["id"], SOURCE) if inst else []
    if len(dates) < MIN_BARS:
        return None
    earliest = dates[MIN_BARS - 1]
    start = req.start or earliest
    if start < earliest:
        raise BacktestInputError(f"Start must be on or after {earliest} (200 days of history are needed first)")
    end = min(req.end or dates[-1], dates[-1])
    if end <= start:
        raise BacktestInputError("End date must be after the start date")
    return start, end, max(start, _years_before(end, UNSEEN_YEARS))


def params_of(req: BacktestRequest) -> dict:
    return clean_json({"money": asdict(req.money), "costs": asdict(req.costs or CostModel()),
                       "strategy": {**STRATEGIES[req.strategy].PARAMS, **req.strategy_params},
                       "strategy_overrides": dict(req.strategy_params), "notes": list(req.notes),
                       "eligibility": {"min_bars": MIN_BARS, "min_traded_value": MIN_TRADED_VALUE},
                       "unseen_years": UNSEEN_YEARS, "exclude_symbols": sorted(req.exclude)})


def create_run(engine: Engine, req: BacktestRequest) -> int:
    validate(req)
    with engine.begin() as conn:
        conn.execute(text("select pg_advisory_xact_lock(:k)"), {"k": _LOCK_KEY})
        if repo.backtest_running(conn):
            raise BacktestBusyError("A backtest is already running — wait for it to finish")
        start, end, unseen_from = resolve_range(conn, req) or (None, None, None)
        return repo.insert_backtest_run(conn, strategy=req.strategy, start_date=start, end_date=end,
                                        unseen_from=unseen_from, params=params_of(req))


def execute_run(engine: Engine, run_id: int) -> None:
    try:
        _execute(engine, run_id)
    except _NoData:
        _fail(engine, run_id, NO_DATA)
    except Exception as exc:  # noqa: BLE001 - a run must never be left "running"
        log.exception("backtest %s failed", run_id)
        _fail(engine, run_id, f"backtest failed: {type(exc).__name__}")


def _fail(engine: Engine, run_id: int, message: str) -> None:
    with engine.begin() as conn:
        repo.finish_backtest_run(conn, run_id, status="failed", message=message)


def index_strategy_for(strategy_key: str):
    """The rules behind a run's NIFTY/BANKNIFTY research table: each family's original index rules."""
    return {"breakout": lambda: Breakout(use_volume=False), "breakout_c2": lambda: Breakout(use_volume=False),
            "breakout_c3": lambda: Breakout(use_volume=False),
            "price_action": lambda: PriceAction(use_volume=False)}.get(strategy_key, Pullback)()


def regime_by_day(nifty_close, window: int = REGIME_SMA) -> dict:
    """NIFTY 50 above its `window`-day simple average of closes, by date (days without enough closes are absent)."""
    sma = nifty_close.rolling(window, min_periods=window).mean()
    return {d.date(): bool(c > s) for d, c, s in zip(nifty_close.index, nifty_close, sma) if s == s}


def _trade_row(t: Trade, ids: dict, unseen_from: date) -> dict:
    return dict(instrument_id=ids.get(t.symbol), symbol=t.symbol, signal_date=t.signal_date,
                entry_date=t.entry_date, entry_price=float(t.entry_price), shares=int(t.shares), stop=float(t.stop),
                target=float(t.target) if math.isfinite(t.target) else None, exit_date=t.exit_date,
                exit_price=float(t.exit_price),
                exit_reason=t.exit_reason, charges=float(t.charges), pnl=float(t.pnl),
                r_multiple=float(t.r_multiple), reason=t.reason, facts=clean_json(t.facts),
                period="unseen" if t.entry_date >= unseen_from else "earlier")


def _signal_row(s: IndexSignal, unseen_from: date) -> dict:
    return dict(symbol=s.symbol, direction=s.direction, signal_date=s.signal_date, entry_date=s.entry_date,
                entry=float(s.entry), stop=float(s.stop), target=float(s.target), exit_date=s.exit_date,
                exit=float(s.exit), exit_reason=s.exit_reason, r_multiple=float(s.r_multiple), reason=s.reason,
                period="unseen" if s.entry_date >= unseen_from else "earlier")


def _data_warnings(data_flags: list, scanned_at, ingested_at) -> list:
    out = []
    if scanned_at is None or (ingested_at is not None and scanned_at < ingested_at):
        out.append("The full-history price-jump scan has not run since the latest data load — click 'Scan full "
                   "history for price jumps' (Data tab) and re-run this backtest; unadjusted splits/bonuses may "
                   "distort these results.")
    if data_flags:
        adj = sum(1 for f in data_flags if f["kind"] == "adjustment")
        out.append(f"{len(data_flags)} one-day price jumps over 35% fall in or just before this period: {adj} look "
                   "like unadjusted splits/bonuses (no entries for ~300 days after them; open trades closed at "
                   f"the last good price) and {len(data_flags) - adj} look like real moves and were kept as they "
                   "happened. See the data-flags table.")
    return out


def _event_days(strategy_key: str, days, start: date, end: date) -> Tuple[Set[date], Optional[str]]:
    """Strategy D's RBI-day entry blocks, plus a warning when the dates file doesn't cover the run."""
    if strategy_key != "price_action":
        return set(), None
    dates, covered_from, covered_to = load_rbi_dates(RBI_DATES_PATH)
    blocked = blocked_entry_days(dates, [d.date() for d in days])
    if covered_from is None:
        return blocked, f"RBI policy dates missing for {start}–{end}; event filter off"
    gaps = []
    if start < covered_from:
        gaps.append(f"{start}–{covered_from}")
    if end > covered_to:
        gaps.append(f"{covered_to}–{end}")
    warning = f"RBI policy dates missing for {', '.join(gaps)}; event filter partly off" if gaps else None
    return blocked, warning


def _execute(engine: Engine, run_id: int) -> None:
    with engine.connect() as conn:
        run = repo.backtest_run_row(conn, run_id)
    if run is None or run["start_date"] is None:
        raise _NoData
    start, end, unseen_from = run["start_date"], run["end_date"], run["unseen_from"]
    money = MoneyRules(**run["params"]["money"])
    costs = CostModel(**run["params"]["costs"])
    market = load_market(engine, SOURCE)
    for symbol in run["params"].get("exclude_symbols") or ():
        market.stocks.pop(symbol, None)
    if not market.stocks or BENCHMARK not in market.indices:
        raise _NoData
    nifty = market.indices[BENCHMARK]
    days = [d for d in nifty.index if start <= d.date() <= end]
    strategy = STRATEGIES[run["strategy"]]()
    overrides = run["params"].get("strategy_overrides") or {}
    if overrides:
        strategy.PARAMS = {**strategy.PARAMS, **overrides}
    prepare = getattr(strategy, "prepare", lambda df: df)
    frames = {s: prepare(add_indicators(f)) for s, f in market.stocks.items()}
    blocked, rbi_warning = _event_days(run["strategy"], days, start, end)
    with engine.connect() as conn:
        flag_rows = [f for f in repo.history_jump_flags(conn) if f["symbol"] in market.stocks]
        scanned_at = repo.last_finished_run_at(conn, "scan_history")
        ingested_at = repo.latest_bar_ingested_at(conn, SOURCE)
    # Only likely unadjusted splits/bonuses (an overnight gap matching a split factor, visible at the open)
    # change trades; large real-looking moves are kept so real crashes count in full.
    flags: dict = {}
    for f in flag_rows:
        if f["check_name"] == "history_adjustment":
            flags.setdefault(f["symbol"], set()).add(f["trade_date"])
    nifty_close = nifty["close"]
    if hasattr(strategy, "nifty_ret126"):  # B2's relative-strength filter
        ret126 = nifty_close / nifty_close.shift(126) - 1
        strategy.nifty_ret126 = {d.date(): float(v) for d, v in ret126.dropna().items()}
    sim = simulate(frames, days, strategy, money, costs, data_flags=flags, blocked_entry_days=blocked,
                   regime=regime_by_day(nifty_close), regime50=regime_by_day(nifty_close, 50),
                   groups=load_groups(), sectors=load_sectors())
    bench = benchmark_curve(nifty["close"], days, money.capital)
    summary = summarize(sim, bench, money, unseen_from)
    signals = []
    if run["strategy"] in INDEX_STRATEGIES:
        idx_strategy = index_strategy_for(run["strategy"])
        idx_prepare = getattr(idx_strategy, "prepare", lambda df: df)
        for symbol, f in sorted(market.indices.items()):
            signals += index_signals(idx_prepare(add_indicators(f)), symbol, days, idx_strategy, money,
                                     blocked_entry_days=blocked)
    if rbi_warning:
        summary["warnings"].append(rbi_warning)
    summary["index_signals"] = index_summary(signals, unseen_from)
    summary["data_flags"] = [
        {"symbol": f["symbol"], "date": f["trade_date"].isoformat(),
         "kind": "adjustment" if f["check_name"] == "history_adjustment" else "large_move", "detail": f["detail"]}
        for f in flag_rows if start - FLAG_LOOKBACK <= f["trade_date"] <= end]
    summary["warnings"] += _data_warnings(summary["data_flags"], scanned_at, ingested_at)
    with engine.begin() as conn:
        repo.insert_backtest_trades(conn, run_id, [_trade_row(t, market.instrument_ids, unseen_from)
                                                   for t in sim.trades])
        repo.insert_backtest_equity(conn, run_id, [
            dict(trade_date=p.day, cash=float(p.cash), invested_value=float(p.invested_value),
                 equity=float(p.equity), benchmark_equity=float(b)) for p, b in zip(sim.equity, bench)])
        repo.insert_backtest_index_signals(conn, run_id, [_signal_row(s, unseen_from) for s in signals])
        repo.finish_backtest_run(conn, run_id, status="ok", summary=clean_json(summary))
