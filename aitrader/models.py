from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional


@dataclass(frozen=True)
class Bar:
    trade_date: date
    open: float
    high: float
    low: float
    close: float
    volume: int


@dataclass(frozen=True)
class CorporateAction:
    ex_date: date
    kind: str  # "split" | "bonus"
    ratio_from: float
    ratio_to: float


@dataclass(frozen=True)
class Issue:
    check_name: str
    severity: str  # "info" | "warn" | "error"
    trade_date: date
    detail: str
    instrument_id: Optional[int] = None
    contract_id: Optional[int] = None


@dataclass(frozen=True)
class OptionContract:
    underlying: str  # "NIFTY" | "BANKNIFTY"
    expiry: date
    strike: float
    option_type: str  # "CE" | "PE"
    lot_size: int
    tradingsymbol: str
    kite_token: int
