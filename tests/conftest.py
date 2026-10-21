from __future__ import annotations

import os
import uuid

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from aitrader.db.schema import metadata

ADMIN_URL = os.environ.get(
    "AITRADER_TEST_ADMIN_URL", "postgresql+psycopg://aitrader:aitrader@127.0.0.1:5433/postgres"
)
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(scope="session")
def db_url():
    admin = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    try:
        admin.connect().close()
    except Exception as exc:  # noqa: BLE001 - any connection failure means "no DB here"
        if os.environ.get("AITRADER_REQUIRE_DB") == "1":
            raise
        pytest.skip(f"Postgres not reachable at {ADMIN_URL}; run `docker compose up -d` ({exc.__class__.__name__})")
    name = f"aitrader_test_{uuid.uuid4().hex[:8]}"
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    url = make_url(ADMIN_URL).set(database=name).render_as_string(hide_password=False)
    cfg = Config(os.path.join(REPO_ROOT, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(REPO_ROOT, "migrations"))
    cfg.set_main_option("sqlalchemy.url", url)
    cfg.attributes["configure_logger"] = False
    command.upgrade(cfg, "head")
    yield url
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
    admin.dispose()


@pytest.fixture
def engine(db_url):
    eng = create_engine(db_url)
    yield eng
    tables = ", ".join(t.name for t in reversed(metadata.sorted_tables))
    with eng.begin() as conn:
        conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    eng.dispose()
