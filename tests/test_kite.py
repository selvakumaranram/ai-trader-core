from datetime import date, datetime
from urllib.parse import parse_qs, urlparse

import pytest
from kiteconnect import exceptions as kex

from aitrader.kite.fake import FakeKiteGateway
from aitrader.kite.gateway import ALLOWED_METHODS, KiteAuthError, KiteSessionData, KiteTransientError
from aitrader.kite.real import RealKiteGateway


def _public_methods(cls):
    return {n for n in dir(cls) if not n.startswith("_") and callable(getattr(cls, n))}


@pytest.mark.parametrize("cls", [RealKiteGateway, FakeKiteGateway])
def test_gateways_expose_no_order_methods(cls):
    methods = _public_methods(cls)
    assert methods == set(ALLOWED_METHODS)
    assert not any("order" in m or "gtt" in m for m in methods)


class StubClient:
    def __init__(self, failures=()):
        self.failures = list(failures)
        self.calls = []
        self.access_token = None

    def set_access_token(self, token):
        self.access_token = token

    def login_url(self):
        return "https://kite.zerodha.com/connect/login?api_key=k&v=3"

    def _maybe_fail(self):
        if self.failures:
            raise self.failures.pop(0)

    def profile(self):
        self.calls.append("profile")
        self._maybe_fail()
        return {"user_id": "AB1234"}

    def historical_data(self, token, start, end, interval):
        self.calls.append(("historical", token, interval))
        return [{"date": datetime(2026, 10, 20), "open": 1, "high": 2, "low": 1, "close": 2, "volume": 3}]

    def generate_session(self, request_token, api_secret):
        return {"access_token": "tok-xyz", "user_id": "AB1234", "login_time": datetime(2026, 10, 21, 9, 0)}


def gateway(client):
    return RealKiteGateway("k", "s", client=client, sleep=lambda s: None)


def test_login_url_carries_state_via_redirect_params():
    url = gateway(StubClient()).login_url("state-abc")
    redirect_params = parse_qs(urlparse(url).query)["redirect_params"][0]
    assert parse_qs(redirect_params) == {"state": ["state-abc"]}


def test_token_errors_become_auth_errors_without_retry():
    client = StubClient([kex.TokenException("expired")])
    with pytest.raises(KiteAuthError):
        gateway(client).profile()
    assert client.calls == ["profile"]


def test_network_and_429_errors_are_retried():
    client = StubClient([kex.NetworkException("down"), kex.GeneralException("Too many requests", code=429)])
    assert gateway(client).profile() == {"user_id": "AB1234"}
    assert client.calls == ["profile"] * 3


def test_retries_exhaust_into_transient_error():
    client = StubClient([kex.NetworkException("down")] * 5)
    with pytest.raises(KiteTransientError):
        gateway(client).profile()


def test_input_errors_propagate_unchanged():
    client = StubClient([kex.InputException("bad param")])
    with pytest.raises(kex.InputException):
        gateway(client).profile()


def test_historical_requests_daily_interval():
    client = StubClient()
    gateway(client).historical_daily(256265, date(2026, 10, 1), date(2026, 10, 20))
    assert client.calls == [("historical", 256265, "day")]


def test_exchange_returns_session_with_hidden_token():
    session = gateway(StubClient()).exchange_request_token("rt")
    assert session.user_id == "AB1234" and session.access_token == "tok-xyz"
    assert "tok-xyz" not in repr(session)


def test_fake_auth_failure_and_call_log():
    fake = FakeKiteGateway(quotes={"NSE:NIFTY 50": {"last_price": 1.0}}, auth_fail_on={"instruments"})
    assert fake.quote(["NSE:NIFTY 50", "NSE:MISSING"]) == {"NSE:NIFTY 50": {"last_price": 1.0}}
    with pytest.raises(KiteAuthError):
        fake.instruments("NFO")
    assert fake.calls == ["quote", "instruments"]


def test_fake_session_exchange_requires_good_token():
    fake = FakeKiteGateway(session=KiteSessionData("tok", "AB1234", datetime(2026, 10, 21, 9)))
    assert fake.exchange_request_token("good-request-token").user_id == "AB1234"
    with pytest.raises(KiteAuthError):
        fake.exchange_request_token("bad")
