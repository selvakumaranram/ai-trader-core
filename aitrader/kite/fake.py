from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence

from aitrader.kite.gateway import KiteAuthError, KiteSessionData


class FakeKiteGateway:
    """In-memory Kite stand-in for tests. Same public surface as RealKiteGateway."""

    def __init__(self, *, instruments_by_exchange: Optional[Dict[str, List[dict]]] = None,
                 quotes: Optional[Dict[str, Dict[str, Any]]] = None,
                 historical: Optional[Dict[int, List[dict]]] = None,
                 session: Optional[KiteSessionData] = None,
                 auth_fail_on: Iterable[str] = frozenset()):
        self._instruments = instruments_by_exchange or {}
        self._quotes = quotes or {}
        self._historical = historical or {}
        self._session = session
        self._auth_fail_on = frozenset(auth_fail_on)
        self.calls: List[str] = []

    def _record(self, name: str) -> None:
        self.calls.append(name)
        if name in self._auth_fail_on:
            raise KiteAuthError("fake: session expired")

    def login_url(self, state: str) -> str:
        return f"https://kite.example/connect/login?state={state}"

    def exchange_request_token(self, request_token: str) -> KiteSessionData:
        self._record("exchange_request_token")
        if request_token != "good-request-token" or self._session is None:
            raise KiteAuthError("fake: invalid request token")
        return self._session

    def profile(self) -> Dict[str, Any]:
        self._record("profile")
        return {"user_id": self._session.user_id if self._session else "FAKE"}

    def instruments(self, exchange: str) -> List[Dict[str, Any]]:
        self._record("instruments")
        return list(self._instruments.get(exchange, []))

    def quote(self, keys: Sequence[str]) -> Dict[str, Dict[str, Any]]:
        self._record("quote")
        return {k: self._quotes[k] for k in keys if k in self._quotes}

    def historical_daily(self, instrument_token: int, start, end) -> List[Dict[str, Any]]:
        self._record("historical_daily")
        return list(self._historical.get(instrument_token, []))
