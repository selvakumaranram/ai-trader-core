from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional

LONG, SHORT = "long", "short"


@dataclass(frozen=True)
class Candidate:
    symbol: str
    direction: str
    signal_date: date
    signal_close: float
    stop: float
    score: float
    reason: str
    facts: dict = field(default_factory=dict, compare=False, hash=False)


@dataclass(frozen=True)
class MoneyRules:
    capital: float = 20_000.0
    risk_per_trade: float = 400.0
    daily_loss_limit: float = 1_000.0
    max_positions: int = 3
    max_position_value: float = 7_000.0
    reward_multiple: float = 2.0
    time_stop_days: int = 20
    # Defaults keep the original behaviour so stored params of old runs reproduce exactly; NEW_RUN_MONEY holds
    # the defaults for new runs.
    risk_mode: str = "fixed"  # "fixed": risk_per_trade ₹; "percent": risk_pct × equity at the previous close
    risk_pct: float = 0.01
    daily_loss_pct: Optional[float] = None  # set: daily loss stop = this × equity at the previous close
    regime_filter: bool = False  # no new long entries when NIFTY 50 closes at or below its 200-day average
    max_charge_to_risk: Optional[float] = None  # skip when estimated round-trip charges ÷ ₹ risked exceed this
    max_drawdown_pause: Optional[float] = None  # stop new entries this far below peak equity
    pause_min_days: int = 10
    reentry_cooldown_days: Optional[int] = None  # None = the strategy's default (B3: 10); 0 = off
    regime_filter_short: Optional[bool] = None  # also need NIFTY 50 above its 50-day avg; None = strategy default
    dd_risk_scaling: bool = False  # half risk while equity is >10% below its all-time peak (back within 5%)


NEW_RUN_MONEY = MoneyRules(capital=100_000.0, risk_per_trade=1_000.0, daily_loss_limit=3_000.0, max_positions=5,
                           max_position_value=25_000.0, risk_mode="percent", risk_pct=0.01, daily_loss_pct=0.03,
                           regime_filter=True, max_charge_to_risk=0.15, max_drawdown_pause=0.15)


@dataclass
class Trade:
    symbol: str
    signal_date: date
    entry_date: date
    entry_price: float
    shares: int
    stop: float
    target: float
    exit_date: date
    exit_price: float
    exit_reason: str
    charges: float
    pnl: float
    r_multiple: float
    reason: str
    facts: dict


@dataclass
class EquityPoint:
    day: date
    cash: float
    invested_value: float
    equity: float


@dataclass
class IndexSignal:
    symbol: str
    direction: str
    signal_date: date
    entry_date: date
    entry: float
    stop: float
    target: float
    exit_date: date
    exit: float
    exit_reason: str
    r_multiple: float
    reason: str
