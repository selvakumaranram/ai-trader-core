from datetime import datetime, timedelta, timezone

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select

from aitrader.auth import IST, SessionStore, session_expiry
from aitrader.db.schema import kite_session
from aitrader.kite.gateway import KiteSessionData


def test_morning_login_expires_next_day_at_six():
    assert session_expiry(datetime(2026, 10, 21, 9, 15)) == datetime(2026, 10, 22, 6, 0, tzinfo=IST)


def test_login_after_midnight_expires_same_day_at_six():
    assert session_expiry(datetime(2026, 10, 21, 5, 30)) == datetime(2026, 10, 21, 6, 0, tzinfo=IST)


def test_store_requires_a_key(engine):
    with pytest.raises(ValueError):
        SessionStore(engine, "")


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


def make_store(engine, now):
    return SessionStore(engine, Fernet.generate_key().decode(), clock=Clock(now))


def test_saved_token_is_encrypted_and_readable(engine):
    store = make_store(engine, datetime(2026, 10, 21, 4, 0, tzinfo=timezone.utc))  # 09:30 IST
    expires = store.save(KiteSessionData("tok-secret-123", "AB1234", datetime(2026, 10, 21, 9, 30)))
    with engine.connect() as conn:
        raw = conn.execute(select(kite_session.c.access_token_encrypted)).scalar_one()
    assert b"tok-secret-123" not in raw
    active = store.active()
    assert active.access_token == "tok-secret-123" and active.expires_at == expires
    assert "tok-secret-123" not in repr(active)


def test_status_never_contains_token(engine):
    store = make_store(engine, datetime(2026, 10, 21, 4, 0, tzinfo=timezone.utc))
    store.save(KiteSessionData("tok-secret-123", "AB1234", datetime(2026, 10, 21, 9, 30)))
    status = store.status()
    assert status["connected"] is True and status["kite_user_id"] == "AB1234"
    assert "tok-secret-123" not in str(status)


def test_expired_or_revoked_sessions_are_not_active(engine):
    clock = Clock(datetime(2026, 10, 21, 4, 0, tzinfo=timezone.utc))
    store = SessionStore(engine, Fernet.generate_key().decode(), clock=clock)
    store.save(KiteSessionData("tok", "AB1234", datetime(2026, 10, 21, 9, 30)))
    clock.now += timedelta(days=1)  # past 06:00 IST next day
    assert store.active() is None and store.status()["connected"] is False
    clock.now -= timedelta(days=1)
    store.revoke()
    assert store.active() is None


def test_new_login_replaces_previous_session(engine):
    store = make_store(engine, datetime(2026, 10, 21, 4, 0, tzinfo=timezone.utc))
    store.save(KiteSessionData("old", "AB1234", datetime(2026, 10, 21, 9, 30)))
    store.save(KiteSessionData("new", "AB1234", datetime(2026, 10, 21, 9, 31)))
    assert store.active().access_token == "new"
    with engine.connect() as conn:
        open_rows = conn.execute(select(kite_session).where(kite_session.c.revoked_at.is_(None))).all()
    assert len(open_rows) == 1
