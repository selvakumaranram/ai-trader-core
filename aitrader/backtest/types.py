from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

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
