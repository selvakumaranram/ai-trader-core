"""Day-by-day portfolio simulation. Decisions at day t use rows <= t; entries fill at t+1's open."""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Collection, Dict, List, Optional, Sequence, Tuple, Union

import pandas as pd

from aitrader.backtest.costs import CostModel
from aitrader.backtest.indicators import MIN_BARS, MIN_TRADED_VALUE
from aitrader.backtest.rules import (
    DrawdownGuard, RiskScaler, atr_trail_stop, exit_check, gap_skip, next_trail_stop, partial_trail_check, planned_target,
    risk_shares, round_trip_estimate, size_position, week_signal_days,
)
from aitrader.backtest.types import LONG, Candidate, EquityPoint, MoneyRules, Trade

# A flagged price jump (e.g. an unadjusted bonus) corrupts ~200 trading days of indicators that follow it.
FLAG_LOOKBACK = timedelta(days=300)
MIN_FILL_FRACTION = 0.5  # skip entries that cash can fund at less than half the intended size
STOP_EXITS = {"stop", "stop_gap", "stop_same_day"}  # exits that start a re-entry cooldown (not trail/target/time)
OPEN_EXITS = {"stop_gap", "target_gap", "rank_exit", "below_sma200"}  # sold at the open: that day's high came after the exit
_EMPTY = pd.DataFrame(columns=["open", "high", "low", "close", "volume", "bars", "value20"])


@dataclass
class SimResult:
    trades: List[Trade]
    equity: List[EquityPoint]
    skips: Dict[str, int]
    notes: Dict[str, int]
    pauses: List[dict] = field(default_factory=list)
    charge_ratios_skipped: List[float] = field(default_factory=list)
    half_risk_days: int = 0


@dataclass
class _Position:
    cand: Candidate
    entry_date: date
    entry_price: float
    shares: int
    target: float
    entry_charges: float
    last_close: float
    days_held: int = 0
    stop: float = 0.0  # current stop (moves under the partial/trail and ATR-trail policies)
    partial_done: bool = False
    sold_shares: int = 0
    sold_value: float = 0.0  # proceeds of the partial sale, after slippage
    sold_charges: float = 0.0
    partial: Optional[dict] = None
    sizing: dict = field(default_factory=dict)
    highest_close: float = 0.0  # highest close since entry (the entry day's close included, not the fill)
    rank_exit: Union[bool, str] = False  # exit reason set at a weekly signal day's close: sell at the next open
    max_high: float = 0.0  # highest high while held (for MFE)


def day_slices(frames: Dict[str, pd.DataFrame], days: Sequence[pd.Timestamp]) -> Dict[pd.Timestamp, pd.DataFrame]:
    """One frame per day, indexed by symbol, holding every symbol that has a bar that day."""
    if not frames or not days:
        return {}
    panel = pd.concat(frames, names=["symbol", "date"])
    dates = panel.index.get_level_values("date")
    panel = panel[(dates >= days[0]) & (dates <= days[-1])]
    return {day: g.droplevel("date") for day, g in panel.groupby(level="date")}


def _close(pos: _Position, day: date, price: float, reason: str, costs: CostModel) -> Tuple[Trade, float]:
    fill = price * (1 - costs.slippage)
    value = pos.shares * fill
    sell = costs.sell_charges(value)
    total = pos.shares + pos.sold_shares
    charges = pos.entry_charges + pos.sold_charges + sell
    pnl = pos.sold_value + value - charges - total * pos.entry_price
    risk = total * (pos.entry_price - pos.cand.stop)
    exit_price = fill if not pos.sold_shares else (pos.sold_value + value) / total
    facts = {**pos.cand.facts, **pos.sizing}
    if pos.max_high > 0:  # max favourable excursion: the best high reached while held, in R of the initial risk
        facts["mfe_r"] = round((pos.max_high - pos.entry_price) / (pos.entry_price - pos.cand.stop), 4)
    if pos.partial:
        facts["partial"] = pos.partial
    trade = Trade(pos.cand.symbol, pos.cand.signal_date, pos.entry_date, pos.entry_price, total, pos.cand.stop,
                  pos.target, day, exit_price, reason, charges, pnl, pnl / risk, pos.cand.reason, facts)
    return trade, value - sell


def simulate(frames: Dict[str, pd.DataFrame], days: Sequence[pd.Timestamp], strategy, money: MoneyRules,
             costs: CostModel, data_flags: Optional[Dict[str, Collection[date]]] = None,
             blocked_entry_days: Optional[Collection[date]] = None,
             regime: Optional[Dict[date, bool]] = None,
             regime50: Optional[Dict[date, bool]] = None, groups: Optional[Dict[str, str]] = None,
             sectors: Optional[Dict[str, str]] = None) -> SimResult:
    """`regime` / `regime50`: NIFTY 50 above its 200- / 50-day average, by date (missing = below)."""
    flags = data_flags or {}
    blocked = set(blocked_entry_days or ())
    policy = getattr(strategy, "exit_policy", "fixed_target")
    trail, atr_trail = policy == "partial_trail", policy == "atr_trail"
    params = getattr(strategy, "PARAMS", {})
    reward = params["partial_r"] if trail else (math.inf if atr_trail else money.reward_multiple)
    time_stop = params["time_stop_days"] if trail or atr_trail else money.time_stop_days
    if time_stop is None:  # time stop switched off
        time_stop = math.inf
    signal_days = (week_signal_days(days, params.get("signal_weekday"))
                   if getattr(strategy, "signal_frequency", "daily") == "weekly" else None)
    groups, sectors = groups or {}, sectors or {}
    entry_check = getattr(strategy, "entry_check", None)  # strategy-specific check once the fill is known
    cooldown = (money.reentry_cooldown_days if money.reentry_cooldown_days is not None
                else params.get("reentry_cooldown_days", 0))
    short_filter = (money.regime_filter_short if money.regime_filter_short is not None
                    else params.get("regime_filter_short", False))
    scaler = RiskScaler() if money.dd_risk_scaling else None
    if scaler:
        scaler.update(money.capital)
    half_risk_days = 0
    last_stop: Dict[str, int] = {}  # symbol → day index of its latest stop exit
    signal_vol: Dict[str, float] = {}  # symbol → ATR(14) ÷ close on the signal day
    regime = regime or {}
    above50 = regime50 or {}
    guard = (DrawdownGuard(money.max_drawdown_pause, money.pause_min_days, money.capital)
             if money.max_drawdown_pause else None)

    def near_flag(symbol: str, day: date) -> bool:
        return any(day - FLAG_LOOKBACK <= f <= day for f in flags.get(symbol, ()))

    def regime_on(day: date) -> bool:
        return bool(regime.get(day, False))

    by_day = day_slices(frames, days)
    cash = money.capital
    prev_equity = money.capital  # equity at the previous close
    positions: Dict[str, _Position] = {}
    pending: List[Candidate] = []
    pause = False
    trades: List[Trade] = []
    equity: List[EquityPoint] = []
    skips: Counter = Counter()
    notes: Counter = Counter()
    charge_ratios_skipped: List[float] = []
    last = len(days) - 1

    for i, day in enumerate(days):
        today = by_day.get(day, _EMPTY)
        d = day.date()
        risk_amount = money.risk_pct * prev_equity if money.risk_mode == "percent" else money.risk_per_trade
        loss_limit = money.daily_loss_pct * prev_equity if money.daily_loss_pct else money.daily_loss_limit
        if scaler and scaler.factor < 1.0:
            risk_amount *= scaler.factor
            half_risk_days += 1

        # 1. entries at the open, decided at yesterday's close
        for cand in sorted(pending, key=lambda c: (-c.score, c.symbol)):
            if pause:
                skips["daily_loss_pause"] += 1
                continue
            if guard and guard.paused:
                skips["drawdown_pause"] += 1
                continue
            if cand.symbol in positions:
                skips["already_held"] += 1
                continue
            if cooldown and cand.symbol in last_stop and i - last_stop[cand.symbol] <= cooldown:
                skips["cooldown_after_stop"] += 1
                continue
            if len(positions) >= money.max_positions:
                skips["no_slot"] += 1
                continue
            if _over_group_limit(cand.symbol, positions, groups, sectors, money):
                skips["group_sector_limit"] += 1
                continue
            if cand.symbol not in today.index:
                skips["missing_bar"] += 1
                continue
            if d in blocked:
                skips["event_day"] += 1
                continue
            if near_flag(cand.symbol, d):
                skips["data_problem"] += 1
                continue
            open_ = float(today.at[cand.symbol, "open"])
            gap = gap_skip(LONG, open_, cand.stop, planned_target(cand.signal_close, cand.stop, reward))
            if gap:
                skips[gap] += 1
                continue
            fill = open_ * (1 + costs.slippage)
            if trail and not (params["min_stop"] <= (fill - cand.stop) / fill <= params["max_stop"]):
                skips["stop_distance"] += 1
                continue
            if entry_check is not None:
                reason = entry_check(cand, fill)
                if reason:
                    skips[reason] += 1
                    continue
            wanted = size_position(fill, cand.stop, money, risk_amount)
            if wanted < 1:
                skips["size_zero"] += 1
                continue
            # charges are sub-additive, so this never overspends cash
            affordable = math.floor(cash / (fill + costs.buy_charges(fill)))
            shares = min(wanted, affordable)
            if shares < max(1, math.ceil(wanted * MIN_FILL_FRACTION)):
                skips["no_cash"] += 1  # a leftover-cash crumb would distort R and pay fixed charges for nothing
                continue
            # estimated round-trip charges + slippage ÷ ₹ risked, at the actual entry and the final share count
            charge_ratio = round_trip_estimate(costs, shares, fill, open_) / (shares * (fill - cand.stop))
            if money.max_charge_to_risk is not None and charge_ratio > money.max_charge_to_risk:
                skips["charges_too_high"] += 1
                charge_ratios_skipped.append(charge_ratio)
                continue
            intended = risk_shares(fill, cand.stop, risk_amount)
            cut = "cash" if shares < wanted else ("cap" if wanted < intended else None)
            charges = costs.buy_charges(shares * fill)
            cash -= shares * fill + charges
            positions[cand.symbol] = _Position(
                cand, d, fill, shares, planned_target(fill, cand.stop, reward), charges, fill, stop=cand.stop,
                sizing={"risk_amount": round(risk_amount, 2), "intended_shares": intended, "size_cut": cut,
                        "regime_on": regime_on(cand.signal_date), "charge_to_risk": round(charge_ratio, 5),
                        "entry_open": open_, "signal_close": cand.signal_close,
                        "atr_pct": signal_vol.get(cand.symbol),
                        "nifty_above_sma50": above50.get(cand.signal_date) if regime50 is not None else None,
                        "group": groups.get(cand.symbol), "sector": sectors.get(cand.symbol)})
        pending = []

        # 2. exits
        realised = 0.0
        for symbol in list(positions):
            pos = positions[symbol]
            if symbol not in today.index:
                notes["missing_bar_days"] += 1
                continue
            if d in flags.get(symbol, ()):  # the price series breaks here: close at the last good price
                trade, proceeds = _close(pos, d, pos.last_close, "data_problem", costs)
                cash += proceeds
                realised += trade.pnl
                trades.append(trade)
                del positions[symbol]
                continue
            bar = tuple(float(today.at[symbol, k]) for k in ("open", "high", "low", "close"))
            entry_day = pos.entry_date == d
            if not entry_day:
                pos.days_held += 1
            pos.last_close = bar[3]
            if trail:
                partial_cash, hit = _trail_day(pos, d, bar, entry_day, time_stop, costs)
                cash += partial_cash
                if hit is None and i < last:
                    pos.stop = next_trail_stop(LONG, pos.stop, pos.entry_price, pos.partial_done,
                                               _col(today, symbol, "low5"), _col(today, symbol, "high5"))
            elif atr_trail:
                hit = _atr_trail_day(pos, bar, entry_day, time_stop)
                if hit is None and i < last:
                    pos.highest_close = max(pos.highest_close, bar[3])
                    pos.stop = atr_trail_stop(pos.stop, pos.highest_close, _col(today, symbol, "atr14"),
                                              params["trail_atr"])
            else:
                hit = exit_check(LONG, bar, pos.cand.stop, pos.target, entry_day=entry_day,
                                 days_held=pos.days_held, time_stop=time_stop)
            if hit is None and i == last:
                hit = (bar[3], "end_of_test")
            if not (hit and hit[1] in OPEN_EXITS):
                pos.max_high = max(pos.max_high, bar[1])
            if hit:
                if hit[1] in STOP_EXITS:
                    last_stop[symbol] = i
                trade, proceeds = _close(pos, d, hit[0], hit[1], costs)
                cash += proceeds
                realised += trade.pnl
                trades.append(trade)
                del positions[symbol]
        if i == last:  # positions with no bar on the last day close at their last known close
            for symbol in list(positions):
                pos = positions.pop(symbol)
                trade, proceeds = _close(pos, d, pos.last_close, "end_of_test", costs)
                cash += proceeds
                realised += trade.pnl
                trades.append(trade)

        # 3. daily loss pause, 4. equity, drawdown pause
        pause = realised <= -loss_limit
        invested = sum(p.shares * p.last_close for p in positions.values())
        equity.append(EquityPoint(d, cash, invested, cash + invested))
        prev_equity = cash + invested
        if guard:
            guard.update(i, d, prev_equity, regime_on(d))
        if scaler:
            scaler.update(prev_equity)

        # 5. candidates (and A2's weekly rank check) at the close
        if i < last and (signal_days is None or day in signal_days):
            eligible = today[(today["bars"] >= MIN_BARS) & (today["value20"] >= MIN_TRADED_VALUE)]
            if atr_trail and positions:  # A2's weekly exits (rank, or below the 200-day average)
                reasons = _weekly_exits(strategy, day, eligible, today, positions)
                if reasons is not None:
                    for symbol, pos in positions.items():
                        pos.rank_exit = reasons.get(symbol) or False
            pending = strategy.candidates(day, eligible)
            signal_vol = {c.symbol: _col(today, c.symbol, "atr14") / float(today.at[c.symbol, "close"])
                          for c in pending if c.symbol in today.index}
            if money.regime_filter and not regime_on(d):
                blocked_longs = [c for c in pending if c.direction == LONG]
                skips["regime_off"] += len(blocked_longs)
                pending = [c for c in pending if c.direction != LONG]
            if short_filter and not (regime_on(d) and above50.get(d, False)):
                short_blocked = [c for c in pending if c.direction == LONG]
                skips["short_regime_off"] += len(short_blocked)
                pending = [c for c in pending if c.direction != LONG]

    skips.update(getattr(strategy, "skips", {}))  # signals a strategy filtered itself (e.g. B2's weaker_than_nifty)
    return SimResult(trades, equity, {k: v for k, v in skips.items() if v}, dict(notes), guard.finish(last) if guard else [],
                     charge_ratios_skipped, half_risk_days)


def _over_group_limit(symbol: str, positions, groups: Dict[str, str], sectors: Dict[str, str],
                      money: MoneyRules) -> bool:
    """Business group first, then NSE industry."""
    group = groups.get(symbol)
    if money.max_per_group and group and sum(groups.get(s) == group for s in positions) >= money.max_per_group:
        return True
    sector = sectors.get(symbol)
    return bool(money.max_per_sector and sector
                and sum(sectors.get(s) == sector for s in positions) >= money.max_per_sector)


def _weekly_exits(strategy, day, eligible, today, positions) -> Optional[Dict[str, str]]:
    if hasattr(strategy, "weekly_exits"):
        return strategy.weekly_exits(day, eligible, today, set(positions))
    if hasattr(strategy, "rank_keep"):
        keep = strategy.rank_keep(day, eligible)
        return {s: "rank_exit" for s in positions if s not in keep}
    return None


def _col(today: pd.DataFrame, symbol: str, column: str) -> float:
    return float(today.at[symbol, column]) if column in today.columns else float("nan")


def _atr_trail_day(pos: _Position, bar, entry_day: bool, time_stop: int) -> Optional[Tuple[float, str]]:
    """One day of A2's exits: a rank exit sells at the open (a stop gap if the open is already through the stop),
    otherwise the usual stop/gap/time rules against the trailing stop."""
    o = bar[0]
    if pos.rank_exit and not entry_day:
        reason = pos.rank_exit if isinstance(pos.rank_exit, str) else "rank_exit"
        return (o, "stop_gap") if o <= pos.stop else (o, reason)
    hit = exit_check(LONG, bar, pos.stop, math.inf, entry_day=entry_day, days_held=pos.days_held,
                     time_stop=time_stop)
    if hit and hit[1] == "stop" and pos.stop > pos.cand.stop:
        return hit[0], "trail_stop"
    return hit


def _trail_day(pos: _Position, day: date, bar, entry_day: bool, time_stop: int,
               costs: CostModel) -> Tuple[float, Optional[Tuple[float, str]]]:
    """One day of the partial/trail policy: (cash from a partial sale, (price, reason) if the rest closes)."""
    act = partial_trail_check(LONG, bar, stop=pos.stop, partial_level=pos.target, partial_done=pos.partial_done,
                              entry_day=entry_day, days_held=pos.days_held, time_stop=time_stop)
    if act is None:
        return 0.0, None
    kind, price, reason = act
    if kind == "exit":
        return 0.0, (price, reason)
    fill = price * (1 - costs.slippage)
    pos.partial_done = True
    if pos.shares == 1:  # nothing to split: the partial closes the whole position
        pos.partial = {"date": day.isoformat(), "price": round(fill, 4), "shares": 1}
        return 0.0, (price, "partial")
    n = pos.shares // 2
    value = n * fill
    sell = costs.sell_charges(value)
    pos.shares -= n
    pos.sold_shares, pos.sold_value, pos.sold_charges = n, value, sell
    pos.partial = {"date": day.isoformat(), "price": round(fill, 4), "shares": n}
    # The rest keeps the original stop for the remainder of this day (breakeven starts tomorrow).
    again = partial_trail_check(LONG, bar, stop=pos.stop, partial_level=pos.target, partial_done=True,
                                entry_day=True, days_held=pos.days_held, time_stop=time_stop)
    return value - sell, (None if again is None else (again[1], again[2]))
