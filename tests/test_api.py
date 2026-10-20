from datetime import datetime, timezone

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from aitrader.api import create_app
from aitrader.auth import IST, SessionStore
from aitrader.calendar import TradingCalendar
from aitrader.jobs import JobDeps
from aitrader.kite.fake import FakeKiteGateway
from aitrader.kite.gateway import KiteSessionData
from fakes import FakeBarSource

TOKEN = "tok-very-secret-123"


def build(engine, configured=True):
    deps = JobDeps(engine=engine, kite=lambda: None, bar_source=FakeBarSource({}), calendar=TradingCalendar(),
                   now=lambda: datetime(2026, 10, 21, 17, 0, tzinfo=IST))
    sessions = SessionStore(engine, Fernet.generate_key().decode(),
                            clock=lambda: datetime(2026, 10, 21, 4, 0, tzinfo=timezone.utc)) if configured else None
    fake = FakeKiteGateway(session=KiteSessionData(TOKEN, "AB1234", datetime(2026, 10, 21, 9, 30)))
    app = create_app(deps=deps, sessions=sessions, gateway_factory=(lambda token: fake) if configured else None,
                     dashboard_url="http://localhost:5173")
    return TestClient(app), sessions


def login_state(client):
    response = client.get("/local/kite/login", follow_redirects=False)
    assert response.status_code == 302
    return response.headers["location"].split("state=")[1]


def test_status_shape_and_no_token(engine):
    client, _ = build(engine)
    body = client.get("/local/status").json()
    assert set(body) == {"coverage", "last_runs", "issue_counts", "kite"}
    assert body["kite"]["connected"] is False


def test_login_callback_saves_session_without_exposing_token(engine):
    client, sessions = build(engine)
    state = login_state(client)
    response = client.get("/local/kite/callback",
                          params={"request_token": "good-request-token", "status": "success", "state": state},
                          follow_redirects=False)
    assert response.status_code == 302 and response.headers["location"].endswith("/?kite=connected")
    status = client.get("/local/kite/status").json()
    assert status["connected"] is True and status["kite_user_id"] == "AB1234"
    for path in ("/local/status", "/local/kite/status", "/local/runs", "/local/issues"):
        assert TOKEN not in client.get(path).text


def test_callback_replay_is_rejected_and_keeps_session(engine):
    client, sessions = build(engine)
    state = login_state(client)
    params = {"request_token": "good-request-token", "status": "success", "state": state}
    assert client.get("/local/kite/callback", params=params, follow_redirects=False).status_code == 302
    replay = client.get("/local/kite/callback", params=params, follow_redirects=False)
    assert replay.status_code == 400 and "start the login again" in replay.json()["detail"]
    assert sessions.status()["connected"] is True


def test_callback_rejects_bad_state_failed_status_and_bad_token(engine):
    client, _ = build(engine)
    assert client.get("/local/kite/callback", params={"request_token": "x", "status": "success",
                                                      "state": "forged"}).status_code == 400
    state = login_state(client)
    assert client.get("/local/kite/callback", params={"request_token": "x", "status": "cancelled",
                                                      "state": state}).status_code == 400
    state = login_state(client)
    assert client.get("/local/kite/callback", params={"request_token": "bad", "status": "success",
                                                      "state": state}).status_code == 400


def test_login_unconfigured_returns_503(engine):
    client, _ = build(engine, configured=False)
    assert client.get("/local/kite/login", follow_redirects=False).status_code == 503
    assert client.get("/local/kite/status").json() == {"configured": False, "connected": False,
                                                       "kite_user_id": None, "expires_at": None}


def test_jobs_endpoint(engine):
    client, _ = build(engine)
    assert client.post("/local/jobs/place_order/run").status_code == 404
    response = client.post("/local/jobs/run_quality_checks/run")
    assert response.status_code == 202 and response.json() == {"accepted": "run_quality_checks"}
    runs = client.get("/local/runs").json()
    assert runs[0]["job"] == "run_quality_checks" and runs[0]["status"] == "ok"


def test_issue_filter_validation(engine):
    client, _ = build(engine)
    assert client.get("/local/issues", params={"severity": "panic"}).status_code == 422
    assert client.get("/local/issues", params={"severity": "warn"}).status_code == 200
