from __future__ import annotations

import time
from urllib.parse import urlencode

import requests
from kiteconnect import KiteConnect
from kiteconnect import exceptions as kex

from aitrader.kite.gateway import KiteAuthError, KiteSessionData, KiteTransientError
from aitrader.ratelimit import RateLimiter, retry


def _translate(exc: Exception) -> Exception:
    if isinstance(exc, (kex.TokenException, kex.PermissionException)):
        return KiteAuthError(str(exc))
    code = getattr(exc, "code", None)
    if (isinstance(exc, (kex.NetworkException, kex.DataException, requests.RequestException))
            or code == 429 or (isinstance(code, int) and code >= 500)):
        return KiteTransientError(str(exc))
    return exc


class RealKiteGateway:
    def __init__(self, api_key: str, api_secret: str, access_token: str | None = None,
                 client=None, sleep=time.sleep):
        self._client = client if client is not None else KiteConnect(api_key=api_key)
        self._secret = api_secret
        if access_token:
            self._client.set_access_token(access_token)
        self._sleep = sleep
        self._limits = {
            "historical": RateLimiter(3, sleep=sleep),
            "quote": RateLimiter(1, sleep=sleep),
            "default": RateLimiter(10, sleep=sleep),
        }

    def _call(self, bucket, fn):
        def attempt():
            self._limits[bucket].acquire()
            try:
                return fn()
            except Exception as exc:  # noqa: BLE001 - translated below
                translated = _translate(exc)
                if translated is exc:
                    raise
                raise translated from exc

        return retry(attempt, is_transient=lambda e: isinstance(e, KiteTransientError), sleep=self._sleep)

    def login_url(self, state: str) -> str:
        return f"{self._client.login_url()}&{urlencode({'redirect_params': urlencode({'state': state})})}"

    def exchange_request_token(self, request_token: str) -> KiteSessionData:
        data = self._call("default", lambda: self._client.generate_session(request_token, api_secret=self._secret))
        return KiteSessionData(access_token=data["access_token"], user_id=data["user_id"],
                               login_time=data["login_time"])

    def profile(self):
        return self._call("default", self._client.profile)

    def instruments(self, exchange: str):
        return self._call("default", lambda: self._client.instruments(exchange))

    def quote(self, keys):
        return self._call("quote", lambda: self._client.quote(list(keys)))

    def historical_daily(self, instrument_token: int, start, end):
        return self._call("historical",
                          lambda: self._client.historical_data(instrument_token, start, end, "day"))
