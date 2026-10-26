from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Optional, Tuple

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from aitrader.backtest.bars import load_market
from aitrader.backtest.costs import CostModel
from aitrader.backtest.engine import FLAG_LOOKBACK, simulate
from aitrader.backtest.index_signals import index_signals, index_summary
from aitrader.backtest.indicators import MIN_BARS, MIN_TRADED_VALUE, add_indicators
from aitrader.backtest.metrics import benchmark_curve, clean_json, summarize
from aitrader.backtest.strategies import INDEX_STRATEGIES, STRATEGIES
from aitrader.backtest.strategies.breakout import Breakout
from aitrader.backtest.strategies.pullback import Pullback
from aitrader.backtest.types import IndexSignal, MoneyRules, Trade
from aitrader.db import repo

log = logging.getLogger(__name__)

SOURCE = "yfinance"
BENCHMARK = "NIFTY 50"
UNSEEN_YEARS = 2
NO_DATA = "Load the stock list and price history first (Data tab)"
INTERRUPTED = "interrupted (service restarted)"
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
    return clean_json({"money": asdict(req.money), "costs": asdict(CostModel()),
                       "strategy": STRATEGIES[req.strategy].PARAMS,
                       "eligibility": {"min_bars": MIN_BARS, "min_traded_value": MIN_TRADED_VALUE},
                       "unseen_years": UNSEEN_YEARS})


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


def _trade_row(t: Trade, ids: dict, unseen_from: date) -> dict:
    return dict(instrument_id=ids.get(t.symbol), symbol=t.symbol, signal_date=t.signal_date,
                entry_date=t.entry_date, entry_price=float(t.entry_price), shares=int(t.shares), stop=float(t.stop),
                target=float(t.target), exit_date=t.exit_date, exit_price=float(t.exit_price),
                exit_reason=t.exit_reason, charges=float(t.charges), pnl=float(t.pnl),
                r_multiple=float(t.r_multiple), reason=t.reason, facts=clean_json(t.facts),
                period="unseen" if t.entry_date >= unseen_from else "earlier")


def _signal_row(s: IndexSignal, unseen_from: date) -> dict:
    return dict(symbol=s.symbol, direction=s.direction, signal_date=s.signal_date, entry_date=s.entry_date,
                entry=float(s.entry), stop=float(s.stop), target=float(s.target), exit_date=s.exit_date,
                exit=float(s.exit), exit_reason=s.exit_reason, r_multiple=float(s.r_multiple), reason=s.reason,
                period="unseen" if s.entry_date >= unseen_from else "earlier")


def _execute(engine: Engine, run_id: int) -> None:
    with engine.connect() as conn:
        run = repo.backtest_run_row(conn, run_id)
    if run is None or run["start_date"] is None:
        raise _NoData
    start, end, unseen_from = run["start_date"], run["end_date"], run["unseen_from"]
    money = MoneyRules(**run["params"]["money"])
    costs = CostModel(**run["params"]["costs"])
    market = load_market(engine, SOURCE)
    if not market.stocks or BENCHMARK not in market.indices:
        raise _NoData
    nifty = market.indices[BENCHMARK]
    days = [d for d in nifty.index if start <= d.date() <= end]
    frames = {s: add_indicators(f) for s, f in market.stocks.items()}
    with engine.connect() as conn:
        flag_rows = [f for f in repo.history_jump_flags(conn) if f["symbol"] in market.stocks]
    flags: dict = {}
    for f in flag_rows:
        flags.setdefault(f["symbol"], set()).add(f["trade_date"])
    sim = simulate(frames, days, STRATEGIES[run["strategy"]](), money, costs, data_flags=flags)
    bench = benchmark_curve(nifty["close"], days, money.capital)
    summary = summarize(sim, bench, money, unseen_from)
    signals = []
    if run["strategy"] in INDEX_STRATEGIES:
        idx_strategy = Breakout(use_volume=False) if run["strategy"] == "breakout" else Pullback()
        for symbol, f in sorted(market.indices.items()):
            signals += index_signals(add_indicators(f), symbol, days, idx_strategy, money)
    summary["index_signals"] = index_summary(signals, unseen_from)
    summary["data_flags"] = [{"symbol": f["symbol"], "date": f["trade_date"].isoformat(), "detail": f["detail"]}
                             for f in flag_rows if start - FLAG_LOOKBACK <= f["trade_date"] <= end]
    with engine.begin() as conn:
        repo.insert_backtest_trades(conn, run_id, [_trade_row(t, market.instrument_ids, unseen_from)
                                                   for t in sim.trades])
        repo.insert_backtest_equity(conn, run_id, [
            dict(trade_date=p.day, cash=float(p.cash), invested_value=float(p.invested_value),
                 equity=float(p.equity), benchmark_equity=float(b)) for p, b in zip(sim.equity, bench)])
        repo.insert_backtest_index_signals(conn, run_id, [_signal_row(s, unseen_from) for s in signals])
        repo.finish_backtest_run(conn, run_id, status="ok", summary=clean_json(summary))
