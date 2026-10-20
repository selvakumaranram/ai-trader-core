from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Dict, List, Protocol, Sequence

ALLOWED_METHODS = frozenset(
    {"login_url", "exchange_request_token", "profile", "instruments", "quote", "historical_daily"}
)


class KiteAuthError(Exception):
    """Token invalid/expired or permission denied. The user must log in again; never retried."""


class KiteTransientError(Exception):
    """Rate limit, network or upstream failure. Safe to retry."""


@dataclass(frozen=True)
class KiteSessionData:
    access_token: str = field(repr=False)
    user_id: str
    login_time: datetime


class KiteGateway(Protocol):
    """Read-only Kite access. Deliberately has no order methods (enforced by tests)."""

    def login_url(self, state: str) -> str: ...

    def exchange_request_token(self, request_token: str) -> KiteSessionData: ...

    def profile(self) -> Dict[str, Any]: ...

    def instruments(self, exchange: str) -> List[Dict[str, Any]]: ...

    def quote(self, keys: Sequence[str]) -> Dict[str, Dict[str, Any]]: ...

    def historical_daily(self, instrument_token: int, start: date, end: date) -> List[Dict[str, Any]]: ...
