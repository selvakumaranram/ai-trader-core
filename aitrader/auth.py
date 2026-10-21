from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
from typing import Callable, Optional
from zoneinfo import ZoneInfo

from cryptography.fernet import Fernet
from sqlalchemy import insert, select, update
from sqlalchemy.engine import Engine

from aitrader.db.schema import kite_session
from aitrader.kite.gateway import KiteSessionData

IST = ZoneInfo("Asia/Kolkata")


def session_expiry(login_time: datetime) -> datetime:
    """Kite access tokens expire at the next 06:00 IST after login."""
    local = (login_time if login_time.tzinfo else login_time.replace(tzinfo=IST)).astimezone(IST)
    day = local.date() + timedelta(days=1) if local.time() >= time(6) else local.date()
    return datetime.combine(day, time(6), tzinfo=IST)


@dataclass(frozen=True)
class ActiveSession:
    access_token: str = field(repr=False)
    user_id: str
    expires_at: datetime


class SessionStore:
    def __init__(self, engine: Engine, encryption_key: str,
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        if not encryption_key:
            raise ValueError("TOKEN_ENCRYPTION_KEY is not set")
        self._fernet = Fernet(encryption_key.encode())
        self._engine = engine
        self._clock = clock

    def save(self, session: KiteSessionData) -> datetime:
        now = self._clock()
        expires = session_expiry(session.login_time)
        with self._engine.begin() as conn:
            conn.execute(update(kite_session).where(kite_session.c.revoked_at.is_(None)).values(revoked_at=now))
            conn.execute(insert(kite_session).values(
                kite_user_id=session.user_id,
                access_token_encrypted=self._fernet.encrypt(session.access_token.encode()),
                created_at=now, expires_at=expires))
        return expires

    def _active_row(self):
        with self._engine.connect() as conn:
            return conn.execute(
                select(kite_session)
                .where(kite_session.c.revoked_at.is_(None), kite_session.c.expires_at > self._clock())
                .order_by(kite_session.c.created_at.desc()).limit(1)
            ).first()

    def active(self) -> Optional[ActiveSession]:
        row = self._active_row()
        if row is None:
            return None
        token = self._fernet.decrypt(row.access_token_encrypted).decode()
        return ActiveSession(token, row.kite_user_id, row.expires_at.astimezone(IST))

    def status(self) -> dict:
        row = self._active_row()
        return {
            "configured": True,
            "connected": row is not None,
            "kite_user_id": row.kite_user_id if row else None,
            "expires_at": row.expires_at.astimezone(IST).isoformat() if row else None,
        }

    def revoke(self) -> None:
        with self._engine.begin() as conn:
            conn.execute(update(kite_session).where(kite_session.c.revoked_at.is_(None))
                         .values(revoked_at=self._clock()))
