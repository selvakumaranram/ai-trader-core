# AI Trader Core: Data Foundation (Part 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the local `aitrader` service that loads, stores and quality-checks daily NSE stock and NIFTY/BANKNIFTY data plus end-of-day index-option snapshots, with a secure Kite login. It must be fully testable offline.

**Architecture:**
- **Service:** a Python package `aitrader` in `D:\ai-trader-core`. A FastAPI app is bound to 127.0.0.1, and an in-process APScheduler runs four daily jobs.
- **Storage:** Postgres 16 in Docker, through SQLAlchemy Core, with Alembic migrations.
- **External sources:** Kite and yfinance sit behind small interfaces (`KiteGateway`, `DailyBarSource`) that have fakes for tests.
- **Logic:** all decision logic (quality checks, strike selection, calendar, rate limiting, redaction) is pure functions.
- **Dashboard:** the existing React dashboard in `D:\ai-trader` gets a "Data" tab that talks to `/local/*` through the Vite dev proxy.

**Tech Stack:**
- Python ≥3.12 (local 3.14; CI 3.12)
- FastAPI, uvicorn
- SQLAlchemy 2 Core, psycopg 3, Alembic
- pydantic-settings, cryptography (Fernet), APScheduler 3, kiteconnect 5.2, yfinance, PyYAML, tzdata
- pytest, httpx, ruff
- Docker Compose
- React 18 / Vite 5 (dashboard tab)

**Spec:** `D:\ai-trader-core\docs\specs\2026-10-20-data-foundation-design.md` (product brief: `D:\ai-trader\AI_Trader_README.md`).

## Global Constraints

These come from the spec:
- **No order placement anywhere.** The `KiteGateway` exposes exactly: `login_url`, `exchange_request_token`, `profile`, `instruments`, `quote`, `historical_daily`.
- **Loopback only.** The service binds to `127.0.0.1` and refuses any non-loopback host.
- **Secrets** come only from `.env` (git-ignored): `KITE_API_KEY`, `KITE_API_SECRET`, `TOKEN_ENCRYPTION_KEY`, `DATABASE_URL`. Never print, log, return or commit their values.
- **Token at rest:** the access token is stored Fernet-encrypted. `GET /local/kite/status` returns only `{connected, kite_user_id, expires_at}` (plus `configured`).
- **Kite session expiry:** 06:00 IST next day.
- **Kite rate limits:** historical 3 req/s, quote 1 req/s with ≤500 instruments per call, everything else 10 req/s. Retry 429/network/5xx with exponential backoff, max 5 attempts. Never retry auth errors.
- **Expiry dates** always come from Kite's instrument list, never from weekdays.
- **Option contract rows** are never deleted.
- **Prices** are stored as the source provides them, with a per-bar `price_basis`. yfinance = `split_adjusted`.
- **Idempotent bars:** `daily_bars` PK is (`instrument_id`, `trade_date`, `source`), and re-loading the same data changes nothing.
- **Option snapshot selection:** ATM ±10 strike steps, 2 nearest expiries, NIFTY and BANKNIFTY, CE and PE.
- **Job schedule (IST, Mon–Fri, trading days):** 08:30 `sync_instruments`, 15:35 `snapshot_options`, 16:30 `ingest_daily_bars`, 16:45 `run_quality_checks`. Start-up catch-up looks back at most 30 days.
- **Quality checks:**
  - `missing_bar` (warn)
  - `ohlc_invalid` (error)
  - `unexplained_jump` >20% (warn)
  - `stale_repeat` ≥3 days (warn)
  - `source_mismatch` >0.5% (info)
  - `missed_snapshot` (warn)
  - `unknown_closed_day` (info)
- **Plan-level naming decisions** (deviations from the spec's wording, same meaning):
  - The issue column is `check_name`, because `check` is an SQL reserved word.
  - `ingestion_runs.status` also allows `running` while a job is in progress.
- **Universe source:** the Nifty 200 constituent CSV is **downloaded manually by the user** from niftyindices.com and passed to `seed-universe`. No automated NSE or niftyindices downloads (NSE's terms prohibit automated collection).
- **Fixtures:** Kite test data is synthetic but shaped like the documented Kite responses, because no recorded responses exist before the user's subscription.
- **Calendar table:** the spec's `trading_calendar` table is created but not populated in Part 1. The versioned holiday YAML file is the source of truth, and observed closures are detected at check time. The table is reserved for persisting observed closures in a later part.

## Review Focus

1. **Non-option rows in Kite's NFO instrument list** (futures `FUT`, other underlyings, rows with `expiry=""`) must be ignored by `sync_instruments`, never stored as contracts. Tested in Task 12.
2. **A stock that returns an empty frame from yfinance** (renamed or delisted symbol) must be reported in the run counts and message as `empty`, not silently counted as success. Tested in Task 12.
3. **A login shortly after midnight** (for example 05:30 IST) must expire at 06:00 *the same day*, not the next day. Tested in Task 8.
4. **The Kite callback hit twice** (browser refresh or back button): the second hit returns a friendly 400, and the session saved by the first hit stays intact. Tested in Task 14.
5. **A constituent CSV with a UTF-8 BOM, padded headers or lower-case header names** must still parse. Tested in Task 11.

---

## File structure (new repo `D:\ai-trader-core`)

| Path | Responsibility |
|---|---|
| `pyproject.toml`, `requirements.lock` | Package metadata, dependencies, and the frozen lock |
| `.env.example`, `.gitignore` | Config template; `.env` is never committed |
| `docker-compose.yml` | Postgres 16 on `127.0.0.1:5433` |
| `alembic.ini`, `migrations/env.py`, `migrations/versions/*.py` | Schema migrations |
| `aitrader/config.py` | `Settings` (env), loopback validation |
| `aitrader/models.py` | Shared dataclasses: `Bar`, `CorporateAction`, `Issue`, `OptionContract` |
| `aitrader/calendar.py`, `aitrader/data/nse_holidays_2026.yaml` | NSE trading days |
| `aitrader/quality.py` | Pure quality checks |
| `aitrader/options.py` | Strike-step / ATM / contract selection |
| `aitrader/ratelimit.py` | `RateLimiter`, `retry` |
| `aitrader/redaction.py` | Log redaction |
| `aitrader/kite/gateway.py`, `real.py`, `fake.py` | Kite interface, real wrapper, fake |
| `aitrader/auth.py` | Session expiry and encrypted `SessionStore` |
| `aitrader/db/schema.py`, `engine.py`, `repo.py` | Tables, engine, all SQL |
| `aitrader/sources/yfinance_source.py` | `DailyBarSource` and `YFinanceSource` |
| `aitrader/universe.py` | Constituent CSV parsing and seeding |
| `aitrader/jobs.py` | The five jobs and `run_job` |
| `aitrader/scheduler.py` | Cron jobs and catch-up |
| `aitrader/api.py` | FastAPI app |
| `aitrader/__main__.py` | CLI: `serve`, `migrate`, `seed-universe`, `run-job`, `live-check` |
| `tests/conftest.py`, `tests/kite_samples.py`, `tests/fakes.py` | DB fixtures and fakes |
| `.github/workflows/ci.yml` | CI with a Postgres service |
| `README.md` | Setup and daily use |

In `D:\ai-trader`: `vercel-demo/src/components/DataTab.jsx` (new), `vercel-demo/src/App.jsx`, `vercel-demo/vite.config.js`, `README.md`.

---

### Task 1: Repository scaffold and settings

**Files:**
- Create: `pyproject.toml`, `.env.example`, `docker-compose.yml`, `README.md`, `aitrader/__init__.py`, `aitrader/config.py`, `tests/test_config.py`
- Modify: `.gitignore`

**Interfaces:**
- Produces:
  - `aitrader.config.Settings`, with fields `database_url: str`, `host: str`, `port: int`, `kite_api_key: str`, `kite_api_secret: SecretStr`, `token_encryption_key: SecretStr`, `dashboard_url: str`, plus `kite_configured() -> bool`;
  - `aitrader.config.LOOPBACK_HOSTS`.

- [ ] **Step 1: Create the scaffold files**

`pyproject.toml`:

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "aitrader"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
  "fastapi>=0.115",
  "uvicorn>=0.30",
  "sqlalchemy>=2.0",
  "psycopg[binary]>=3.2",
  "alembic>=1.13",
  "pydantic-settings>=2.4",
  "cryptography>=43",
  "apscheduler>=3.10,<4",
  "kiteconnect>=5.2",
  "yfinance>=1.5",
  "pyyaml>=6",
  "tzdata>=2024.1",
]

[project.optional-dependencies]
dev = ["pytest>=8", "httpx>=0.27", "ruff>=0.6"]

[tool.setuptools.packages.find]
include = ["aitrader*"]

[tool.setuptools.package-data]
"aitrader.data" = ["*.yaml"]

[tool.pytest.ini_options]
testpaths = ["tests"]

[tool.ruff]
line-length = 110
```

`.env.example`:

```text
DATABASE_URL=postgresql+psycopg://aitrader:aitrader@127.0.0.1:5433/aitrader
KITE_API_KEY=
KITE_API_SECRET=
# Generate once with:
#   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
TOKEN_ENCRYPTION_KEY=
DASHBOARD_URL=http://localhost:5173
```

`docker-compose.yml`:

```yaml
services:
  db:
    image: postgres:16
    environment:
      POSTGRES_USER: aitrader
      POSTGRES_PASSWORD: aitrader
      POSTGRES_DB: aitrader
    ports:
      - "127.0.0.1:5433:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U aitrader"]
      interval: 5s
      timeout: 5s
      retries: 10
volumes:
  pgdata: {}
```

Append to `.gitignore`:

```text
*.egg-info/
build/
```

`aitrader/__init__.py`:

```python
"""AI Trader core: local market-data service (no order placement)."""
```

`README.md` (expanded in Task 15):

```markdown
# ai-trader-core

Local market-data service for AI Trader. See `docs/specs/` for the design.
No order placement exists in this codebase.
```

- [ ] **Step 2: Create the virtualenv and install**

Run (Git Bash, repo root):

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"
```

Expected: ends with `Successfully installed ... aitrader-0.1.0 ...`. If any package fails to build on Python 3.14 (most likely Twisted, pulled in by `kiteconnect`), record a ledger ruling and retry with the newest compatible version. Don't drop the dependency silently.

- [ ] **Step 3: Write the failing test** in `tests/test_config.py`

```python
import pytest
from pydantic import ValidationError

from aitrader.config import Settings


def test_defaults_are_loopback_and_local_db(monkeypatch):
    for key in ("HOST", "DATABASE_URL", "KITE_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    settings = Settings(_env_file=None)
    assert settings.host == "127.0.0.1"
    assert settings.database_url.startswith("postgresql+psycopg://")
    assert settings.kite_configured() is False


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.5", "example.com"])
def test_non_loopback_host_is_refused(host):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, host=host)


def test_secrets_never_appear_in_repr():
    settings = Settings(
        _env_file=None,
        kite_api_key="key123",
        kite_api_secret="super-secret-value",
        token_encryption_key="enc-key-value",
    )
    assert "super-secret-value" not in repr(settings)
    assert "enc-key-value" not in str(settings)
    assert settings.kite_configured() is True
```

- [ ] **Step 4: Run it to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_config.py -q`
Expected: collection error `ModuleNotFoundError: No module named 'aitrader.config'`.

- [ ] **Step 5: Implement** `aitrader/config.py`

```python
from __future__ import annotations

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql+psycopg://aitrader:aitrader@127.0.0.1:5433/aitrader"
    host: str = "127.0.0.1"
    port: int = 8000
    kite_api_key: str = ""
    kite_api_secret: SecretStr = SecretStr("")
    token_encryption_key: SecretStr = SecretStr("")
    dashboard_url: str = "http://localhost:5173"

    @field_validator("host")
    @classmethod
    def _loopback_only(cls, value: str) -> str:
        if value not in LOOPBACK_HOSTS:
            raise ValueError(f"host must be one of {sorted(LOOPBACK_HOSTS)} (local only), got {value!r}")
        return value

    def kite_configured(self) -> bool:
        return bool(
            self.kite_api_key
            and self.kite_api_secret.get_secret_value()
            and self.token_encryption_key.get_secret_value()
        )
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_config.py -q`
Expected: `5 passed`.

- [ ] **Step 7: Freeze dependencies and commit**

```bash
.venv/Scripts/python -m pip freeze --exclude-editable > requirements.lock
git add pyproject.toml requirements.lock .env.example docker-compose.yml README.md .gitignore aitrader/__init__.py aitrader/config.py tests/test_config.py
git commit -m "Scaffold aitrader package, settings with loopback-only host, docker-compose Postgres"
```

---

### Task 2: Database schema, Alembic migration and test DB fixture

**Files:**
- Create: `aitrader/db/__init__.py`, `aitrader/db/schema.py`, `aitrader/db/engine.py`, `alembic.ini`, `migrations/env.py`, `migrations/script.py.mako`, `migrations/versions/<generated>_initial_schema.py`, `tests/conftest.py`, `tests/test_schema.py`

**Interfaces:**
- Produces:
  - `aitrader.db.schema.metadata` and the table objects `instruments`, `daily_bars`, `corporate_actions`, `index_membership`, `trading_calendar`, `option_contracts`, `option_snapshots`, `kite_session`, `ingestion_runs`, `data_quality_issues`;
  - `aitrader.db.engine.make_engine(url: str) -> Engine`;
  - pytest fixtures `db_url` (session-scoped) and `engine` (per test, tables truncated afterwards).

- [ ] **Step 1: Start Postgres**

Run: `docker compose up -d` (Docker Desktop must be running).
Then run: `docker compose ps`
Expected: service `db` is `healthy`.

- [ ] **Step 2: Write the schema** in `aitrader/db/schema.py`

```python
from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    MetaData,
    Numeric,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB

metadata = MetaData()
PRICE = Numeric(14, 4)

instruments = Table(
    "instruments",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("kind", String(10), nullable=False),
    Column("exchange", String(10), nullable=False),
    Column("symbol", String(40), nullable=False),
    Column("name", String(200)),
    Column("yf_symbol", String(40)),
    Column("kite_token", BigInteger),
    Column("active", Boolean, nullable=False, server_default=text("true")),
    UniqueConstraint("exchange", "symbol", name="uq_instruments_exchange_symbol"),
    CheckConstraint("kind in ('stock', 'index')", name="ck_instruments_kind"),
)

daily_bars = Table(
    "daily_bars",
    metadata,
    Column("instrument_id", Integer, ForeignKey("instruments.id"), primary_key=True),
    Column("trade_date", Date, primary_key=True),
    Column("source", String(10), primary_key=True),
    Column("open", PRICE, nullable=False),
    Column("high", PRICE, nullable=False),
    Column("low", PRICE, nullable=False),
    Column("close", PRICE, nullable=False),
    Column("volume", BigInteger, nullable=False),
    Column("price_basis", String(20), nullable=False),
    Column("ingested_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint("source in ('yfinance', 'kite', 'vendor')", name="ck_daily_bars_source"),
    CheckConstraint("price_basis in ('split_adjusted', 'raw')", name="ck_daily_bars_price_basis"),
)

corporate_actions = Table(
    "corporate_actions",
    metadata,
    Column("instrument_id", Integer, ForeignKey("instruments.id"), primary_key=True),
    Column("ex_date", Date, primary_key=True),
    Column("kind", String(10), primary_key=True),
    Column("source", String(10), primary_key=True),
    Column("ratio_from", Numeric(12, 4), nullable=False),
    Column("ratio_to", Numeric(12, 4), nullable=False),
    CheckConstraint("kind in ('split', 'bonus')", name="ck_corporate_actions_kind"),
)

index_membership = Table(
    "index_membership",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("index_name", String(40), nullable=False),
    Column("instrument_id", Integer, ForeignKey("instruments.id"), nullable=False),
    Column("valid_from", Date, nullable=False),
    Column("valid_to", Date),
    Column("is_backfilled_assumption", Boolean, nullable=False),
    UniqueConstraint("index_name", "instrument_id", "valid_from", name="uq_index_membership"),
)

trading_calendar = Table(
    "trading_calendar",
    metadata,
    Column("trade_date", Date, primary_key=True),
    Column("is_trading_day", Boolean, nullable=False),
    Column("note", Text),
    Column("source", String(20), nullable=False),
)

option_contracts = Table(
    "option_contracts",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("underlying", String(20), nullable=False),
    Column("expiry", Date, nullable=False),
    Column("strike", Numeric(12, 2), nullable=False),
    Column("option_type", String(2), nullable=False),
    Column("lot_size", Integer, nullable=False),
    Column("tradingsymbol", String(60), nullable=False),
    Column("kite_token", BigInteger, nullable=False),
    Column("first_seen", Date, nullable=False),
    Column("last_seen", Date, nullable=False),
    UniqueConstraint("tradingsymbol", "expiry", name="uq_option_contracts_symbol_expiry"),
    CheckConstraint("option_type in ('CE', 'PE')", name="ck_option_contracts_type"),
)

option_snapshots = Table(
    "option_snapshots",
    metadata,
    Column("contract_id", Integer, ForeignKey("option_contracts.id"), primary_key=True),
    Column("snapshot_at", DateTime(timezone=True), primary_key=True),
    Column("last_price", PRICE, nullable=False),
    Column("bid", PRICE),
    Column("ask", PRICE),
    Column("volume", BigInteger),
    Column("oi", BigInteger),
    Column("underlying_price", PRICE, nullable=False),
    Column("source", String(10), nullable=False),
)

kite_session = Table(
    "kite_session",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("kite_user_id", String(40), nullable=False),
    Column("access_token_encrypted", LargeBinary, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("revoked_at", DateTime(timezone=True)),
)

ingestion_runs = Table(
    "ingestion_runs",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("job", String(40), nullable=False),
    Column("run_date", Date, nullable=False),
    Column("started_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("finished_at", DateTime(timezone=True)),
    Column("status", String(10), nullable=False),
    Column("source", String(20)),
    Column("counts", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column("message", Text, nullable=False, server_default=text("''")),
    CheckConstraint(
        "status in ('running', 'ok', 'partial', 'skipped', 'failed')", name="ck_ingestion_runs_status"
    ),
)

data_quality_issues = Table(
    "data_quality_issues",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("run_id", Integer, ForeignKey("ingestion_runs.id"), nullable=False),
    Column("instrument_id", Integer, ForeignKey("instruments.id")),
    Column("contract_id", Integer, ForeignKey("option_contracts.id")),
    Column("trade_date", Date, nullable=False),
    Column("check_name", String(40), nullable=False),
    Column("severity", String(10), nullable=False),
    Column("detail", Text, nullable=False),
    CheckConstraint("severity in ('info', 'warn', 'error')", name="ck_issues_severity"),
)
```

`aitrader/db/__init__.py`: an empty file.

`aitrader/db/engine.py`:

```python
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine


def make_engine(url: str) -> Engine:
    return create_engine(url, pool_pre_ping=True)
```

- [ ] **Step 3: Initialise Alembic**

Run: `.venv/Scripts/alembic init migrations`
Expected: it creates `alembic.ini`, `migrations/env.py` and `migrations/script.py.mako`.

Then edit `alembic.ini`, changing the `sqlalchemy.url = ...` line to:

```ini
sqlalchemy.url =
```

Replace `migrations/env.py` entirely with:

```python
from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from aitrader.config import Settings
from aitrader.db.schema import metadata

config = context.config
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name)

if not config.get_main_option("sqlalchemy.url"):
    config.set_main_option("sqlalchemy.url", Settings().database_url)

target_metadata = metadata


def run_migrations_offline() -> None:
    context.configure(url=config.get_main_option("sqlalchemy.url"), target_metadata=target_metadata,
                      literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(config.get_section(config.config_ini_section, {}),
                                     prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

- [ ] **Step 4: Generate the initial migration**

Run: `.venv/Scripts/alembic revision --autogenerate -m "initial schema"`
Expected: it creates `migrations/versions/<hash>_initial_schema.py` with `op.create_table` calls for all 10 tables. Open it and confirm that every table and every named constraint from Step 2 appears. Autogenerate output is what gets committed.

Then run: `.venv/Scripts/alembic upgrade head`
Expected: `Running upgrade  -> <hash>, initial schema`.

- [ ] **Step 5: Write the DB fixtures** in `tests/conftest.py`

```python
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
```

- [ ] **Step 6: Write the test** in `tests/test_schema.py`

```python
from sqlalchemy import inspect

EXPECTED = {
    "instruments", "daily_bars", "corporate_actions", "index_membership", "trading_calendar",
    "option_contracts", "option_snapshots", "kite_session", "ingestion_runs", "data_quality_issues",
}


def test_migration_creates_every_table(engine):
    assert EXPECTED <= set(inspect(engine).get_table_names())


def test_daily_bars_primary_key_includes_source(engine):
    pk = inspect(engine).get_pk_constraint("daily_bars")["constrained_columns"]
    assert pk == ["instrument_id", "trade_date", "source"]
```

- [ ] **Step 7: Run the tests**

Run: `.venv/Scripts/python -m pytest -q`
Expected: `7 passed` (5 config + 2 schema). Watch for a "skipped" line, which means Postgres wasn't reachable; fix that before continuing.

- [ ] **Step 8: Commit**

```bash
git add aitrader/db alembic.ini migrations tests/conftest.py tests/test_schema.py
git commit -m "Add Postgres schema, initial Alembic migration and per-run test database fixture"
```

---

### Task 3: Shared models and trading calendar

**Files:**
- Create: `aitrader/models.py`, `aitrader/calendar.py`, `aitrader/data/__init__.py`, `aitrader/data/nse_holidays_2026.yaml`, `tests/test_calendar.py`

**Interfaces:**
- Produces:
  - `Bar(trade_date: date, open: float, high: float, low: float, close: float, volume: int)`;
  - `CorporateAction(ex_date: date, kind: str, ratio_from: float, ratio_to: float)`;
  - `Issue(check_name: str, severity: str, trade_date: date, detail: str, instrument_id: int | None = None, contract_id: int | None = None)`;
  - `OptionContract(underlying: str, expiry: date, strike: float, option_type: str, lot_size: int, tradingsymbol: str, kite_token: int)`. All are frozen dataclasses.
  - `TradingCalendar(holidays: frozenset[date])`, with `.is_trading_day(d) -> bool`, `.trading_days(start, end) -> list[date]` (inclusive), `.from_yaml_texts(texts) -> TradingCalendar` and `.bundled() -> TradingCalendar`.

- [ ] **Step 1: Write the failing test** in `tests/test_calendar.py`

```python
from datetime import date

from aitrader.calendar import TradingCalendar

YAML = """
year: 2026
holidays:
  - {date: 2026-01-26, name: Republic Day}
"""


def test_weekends_are_closed():
    cal = TradingCalendar()
    assert cal.is_trading_day(date(2026, 10, 23)) is True   # Friday
    assert cal.is_trading_day(date(2026, 10, 24)) is False  # Saturday
    assert cal.is_trading_day(date(2026, 10, 25)) is False  # Sunday


def test_holiday_from_yaml_is_closed():
    cal = TradingCalendar.from_yaml_texts([YAML])
    assert cal.is_trading_day(date(2026, 1, 26)) is False
    assert cal.is_trading_day(date(2026, 1, 27)) is True


def test_trading_days_is_inclusive_and_skips_closed_days():
    cal = TradingCalendar.from_yaml_texts([YAML])
    days = cal.trading_days(date(2026, 1, 23), date(2026, 1, 27))
    assert days == [date(2026, 1, 23), date(2026, 1, 27)]


def test_bundled_calendar_loads():
    assert isinstance(TradingCalendar.bundled(), TradingCalendar)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_calendar.py -q`
Expected: `ModuleNotFoundError: No module named 'aitrader.calendar'`.

- [ ] **Step 3: Implement**

`aitrader/models.py`:

```python
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
```

`aitrader/calendar.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from importlib import resources
from typing import Iterable, List

import yaml


@dataclass(frozen=True)
class TradingCalendar:
    holidays: frozenset = field(default_factory=frozenset)

    def is_trading_day(self, day: date) -> bool:
        return day.weekday() < 5 and day not in self.holidays

    def trading_days(self, start: date, end: date) -> List[date]:
        days, current = [], start
        while current <= end:
            if self.is_trading_day(current):
                days.append(current)
            current += timedelta(days=1)
        return days

    @classmethod
    def from_yaml_texts(cls, texts: Iterable[str]) -> "TradingCalendar":
        holidays = set()
        for text_ in texts:
            document = yaml.safe_load(text_) or {}
            for entry in document.get("holidays") or []:
                holidays.add(date.fromisoformat(str(entry["date"])))
        return cls(frozenset(holidays))

    @classmethod
    def bundled(cls) -> "TradingCalendar":
        files = resources.files("aitrader.data")
        texts = [
            f.read_text(encoding="utf-8")
            for f in files.iterdir()
            if f.name.startswith("nse_holidays_") and f.name.endswith(".yaml")
        ]
        return cls.from_yaml_texts(texts)
```

`aitrader/data/__init__.py`: an empty file.

`aitrader/data/nse_holidays_2026.yaml`:

```yaml
# NSE equity trading holidays for 2026.
# Fill in BY HAND from NSE's official "Trading Holidays" circular for 2026
# (do not script-download nseindia.com — its terms prohibit automated collection).
# Format, one per line:
#   - {date: 2026-01-26, name: Republic Day}
# Until a holiday is listed here, a day with no bars for any instrument is
# reported as `unknown_closed_day` instead of a wall of `missing_bar` warnings.
year: 2026
holidays: []
```

- [ ] **Step 4: Run the tests**

Run: `.venv/Scripts/python -m pytest tests/test_calendar.py -q`
Expected: `4 passed`.

- [ ] **Step 5: Commit**

```bash
git add aitrader/models.py aitrader/calendar.py aitrader/data tests/test_calendar.py
git commit -m "Add shared data models and NSE trading calendar"
```

---

### Task 4: Data-quality checks (pure)

**Files:**
- Create: `aitrader/quality.py`, `tests/test_quality.py`

**Interfaces:**
- Consumes: `Bar` and `Issue` from Task 3.
- Produces:
  - `check_ohlc(bars, instrument_id=None) -> list[Issue]`
  - `check_missing(bars, expected_days, closed_days=frozenset(), instrument_id=None) -> list[Issue]`
  - `check_jumps(bars, action_dates, threshold=0.20, instrument_id=None) -> list[Issue]`
  - `check_stale(bars, run_length=3, instrument_id=None) -> list[Issue]`
  - `check_source_mismatch(primary, secondary, tolerance=0.005, instrument_id=None) -> list[Issue]`
  - `find_unknown_closed_days(days_with_any_bar, expected_days) -> list[date]`
  - `BAR_CHECKS: tuple[str, ...]`

- [ ] **Step 1: Write the failing test** in `tests/test_quality.py`

```python
from datetime import date

from aitrader.models import Bar
from aitrader.quality import (
    BAR_CHECKS,
    check_jumps,
    check_missing,
    check_ohlc,
    check_source_mismatch,
    check_stale,
    find_unknown_closed_days,
)


def bar(day, o=100.0, h=101.0, low=99.0, c=100.5, v=1000):
    return Bar(date(2026, 10, day), o, h, low, c, v)


def test_ohlc_invalid_detects_each_problem():
    issues = check_ohlc([bar(1), bar(2, low=101.0), bar(5, h=99.5), bar(6, o=0.0), bar(7, v=-1)], instrument_id=7)
    assert [i.trade_date.day for i in issues] == [2, 5, 6, 7]
    assert all(i.check_name == "ohlc_invalid" and i.severity == "error" and i.instrument_id == 7 for i in issues)


def test_missing_bar_ignores_known_closed_days():
    expected = [date(2026, 10, d) for d in (1, 2, 5)]
    issues = check_missing([bar(1)], expected, closed_days={date(2026, 10, 2)})
    assert [(i.check_name, i.trade_date.day) for i in issues] == [("missing_bar", 5)]


def test_jump_flagged_unless_corporate_action_nearby():
    bars = [bar(1, c=100.0),
            bar(2, o=130.0, h=131.0, low=129.0, c=130.0),
            bar(5, o=130.0, h=131.0, low=129.0, c=130.5)]
    assert [i.trade_date.day for i in check_jumps(bars, [])] == [2]
    assert check_jumps(bars, [date(2026, 10, 2)]) == []


def test_stale_repeat_after_three_identical_days():
    bars = [bar(1), bar(2), bar(5), bar(6, c=101.0)]
    issues = check_stale(bars)
    assert [(i.check_name, i.trade_date.day) for i in issues] == [("stale_repeat", 5)]


def test_source_mismatch_over_half_percent():
    primary = [bar(1, c=100.0), bar(2, c=100.0)]
    secondary = [bar(1, c=100.4), bar(2, c=101.0)]
    issues = check_source_mismatch(primary, secondary)
    assert [(i.check_name, i.severity, i.trade_date.day) for i in issues] == [("source_mismatch", "info", 2)]


def test_unknown_closed_days_are_expected_days_with_no_bars_anywhere():
    expected = [date(2026, 10, d) for d in (1, 2, 5)]
    assert find_unknown_closed_days({date(2026, 10, 1), date(2026, 10, 5)}, expected) == [date(2026, 10, 2)]


def test_bar_checks_lists_every_bar_level_check():
    assert set(BAR_CHECKS) == {"missing_bar", "ohlc_invalid", "unexplained_jump", "stale_repeat",
                               "source_mismatch", "unknown_closed_day"}
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_quality.py -q`
Expected: `ModuleNotFoundError: No module named 'aitrader.quality'`.

- [ ] **Step 3: Implement** `aitrader/quality.py`

```python
from __future__ import annotations

from datetime import date, timedelta
from typing import Collection, Iterable, List, Optional, Sequence, Set

from aitrader.models import Bar, Issue

BAR_CHECKS = ("missing_bar", "ohlc_invalid", "unexplained_jump", "stale_repeat",
              "source_mismatch", "unknown_closed_day")


def _ordered(bars: Iterable[Bar]) -> List[Bar]:
    return sorted(bars, key=lambda b: b.trade_date)


def check_ohlc(bars: Sequence[Bar], instrument_id: Optional[int] = None) -> List[Issue]:
    issues = []
    for b in _ordered(bars):
        problems = []
        if min(b.open, b.high, b.low, b.close) <= 0:
            problems.append("non-positive price")
        if b.low > min(b.open, b.close):
            problems.append("low above open/close")
        if b.high < max(b.open, b.close):
            problems.append("high below open/close")
        if b.volume < 0:
            problems.append("negative volume")
        if problems:
            issues.append(Issue("ohlc_invalid", "error", b.trade_date, "; ".join(problems), instrument_id))
    return issues


def check_missing(bars: Sequence[Bar], expected_days: Iterable[date],
                  closed_days: Collection[date] = frozenset(),
                  instrument_id: Optional[int] = None) -> List[Issue]:
    have = {b.trade_date for b in bars}
    return [
        Issue("missing_bar", "warn", d, "no bar on a trading day", instrument_id)
        for d in sorted(expected_days)
        if d not in have and d not in closed_days
    ]


def check_jumps(bars: Sequence[Bar], action_dates: Iterable[date], threshold: float = 0.20,
                instrument_id: Optional[int] = None) -> List[Issue]:
    near_action: Set[date] = set()
    for d in action_dates:
        near_action |= {d - timedelta(days=1), d, d + timedelta(days=1)}
    ordered = _ordered(bars)
    issues = []
    for prev, cur in zip(ordered, ordered[1:]):
        if cur.trade_date in near_action or prev.close <= 0:
            continue
        for label, move in (("close-to-open", cur.open / prev.close - 1),
                            ("close-to-close", cur.close / prev.close - 1)):
            if abs(move) > threshold:
                issues.append(Issue("unexplained_jump", "warn", cur.trade_date,
                                    f"{label} move {move:+.1%} with no recorded split/bonus", instrument_id))
                break
    return issues


def check_stale(bars: Sequence[Bar], run_length: int = 3,
                instrument_id: Optional[int] = None) -> List[Issue]:
    ordered = _ordered(bars)
    issues, streak = [], 1
    for prev, cur in zip(ordered, ordered[1:]):
        same = (prev.open, prev.high, prev.low, prev.close, prev.volume) == (
            cur.open, cur.high, cur.low, cur.close, cur.volume)
        streak = streak + 1 if same else 1
        if streak == run_length:
            issues.append(Issue("stale_repeat", "warn", cur.trade_date,
                                f"identical OHLCV for {run_length} consecutive trading days", instrument_id))
    return issues


def check_source_mismatch(primary: Sequence[Bar], secondary: Sequence[Bar], tolerance: float = 0.005,
                          instrument_id: Optional[int] = None) -> List[Issue]:
    other = {b.trade_date: b for b in secondary}
    issues = []
    for b in _ordered(primary):
        o = other.get(b.trade_date)
        if o is None or b.close <= 0:
            continue
        diff = o.close / b.close - 1
        if abs(diff) > tolerance:
            issues.append(Issue("source_mismatch", "info", b.trade_date,
                                f"close differs by {diff:+.2%} between sources", instrument_id))
    return issues


def find_unknown_closed_days(days_with_any_bar: Collection[date], expected_days: Iterable[date]) -> List[date]:
    return sorted(d for d in expected_days if d not in days_with_any_bar)
```

- [ ] **Step 4: Run the tests**

Run: `.venv/Scripts/python -m pytest tests/test_quality.py -q`
Expected: `7 passed`.

- [ ] **Step 5: Commit**

```bash
git add aitrader/quality.py tests/test_quality.py
git commit -m "Add pure data-quality checks"
```

---

### Task 5: Option contract selection (pure)

**Files:**
- Create: `aitrader/options.py`, `tests/test_options.py`

**Interfaces:**
- Consumes: `OptionContract` (Task 3).
- Produces:
  - `strike_step(strikes) -> float`
  - `atm_strike(strikes, price) -> float` (ties go to the lower strike)
  - `select_contracts(contracts, underlying, underlying_price, as_of, expiries=2, width=10) -> list[OptionContract]`, sorted by (expiry, strike, option_type).

- [ ] **Step 1: Write the failing test** in `tests/test_options.py`

```python
from datetime import date

import pytest

from aitrader.models import OptionContract
from aitrader.options import atm_strike, select_contracts, strike_step


def chain(underlying, expiry, center, step, n, lot=75):
    out = []
    for i in range(-n, n + 1):
        strike = center + i * step
        for kind in ("CE", "PE"):
            out.append(OptionContract(underlying, expiry, float(strike), kind, lot,
                                      f"{underlying}{expiry:%y%b}{strike}{kind}".upper(), 1000 + len(out)))
    return out


def test_strike_step_is_most_common_gap():
    assert strike_step([100, 150, 200, 250, 300, 400, 500]) == 50


def test_strike_step_needs_two_strikes():
    with pytest.raises(ValueError):
        strike_step([100])


def test_atm_tie_rounds_down():
    assert atm_strike([24900, 24950, 25000], 24975) == 24950
    assert atm_strike([24900, 24950, 25000], 24990) == 25000


def test_select_two_nearest_unexpired_expiries_within_ten_steps():
    expired = chain("NIFTY", date(2026, 10, 13), 25000, 50, 15)
    near = chain("NIFTY", date(2026, 10, 27), 25000, 50, 15)
    nxt = chain("NIFTY", date(2026, 11, 3), 25000, 50, 15)
    far = chain("NIFTY", date(2026, 11, 24), 25000, 50, 15)
    bank = chain("BANKNIFTY", date(2026, 10, 27), 56000, 100, 15)
    picked = select_contracts(expired + near + nxt + far + bank, "NIFTY", 25010.0, date(2026, 10, 21))
    assert {c.expiry for c in picked} == {date(2026, 10, 27), date(2026, 11, 3)}
    assert {c.underlying for c in picked} == {"NIFTY"}
    assert len(picked) == 2 * 21 * 2  # 2 expiries x (ATM +-10 = 21 strikes) x CE/PE
    strikes = sorted({c.strike for c in picked})
    assert strikes[0] == 24500 and strikes[-1] == 25500
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_options.py -q`
Expected: `ModuleNotFoundError: No module named 'aitrader.options'`.

- [ ] **Step 3: Implement** `aitrader/options.py`

```python
from __future__ import annotations

from collections import Counter
from datetime import date
from typing import Iterable, List, Sequence

from aitrader.models import OptionContract


def strike_step(strikes: Iterable[float]) -> float:
    unique = sorted(set(strikes))
    if len(unique) < 2:
        raise ValueError("need at least two distinct strikes to infer the strike step")
    gaps = Counter(round(b - a, 4) for a, b in zip(unique, unique[1:]))
    best = max(gaps.values())
    return min(g for g, count in gaps.items() if count == best)


def atm_strike(strikes: Iterable[float], price: float) -> float:
    unique = sorted(set(strikes))
    if not unique:
        raise ValueError("no strikes available")
    return min(unique, key=lambda s: (abs(s - price), s))


def select_contracts(contracts: Sequence[OptionContract], underlying: str, underlying_price: float,
                     as_of: date, expiries: int = 2, width: int = 10) -> List[OptionContract]:
    mine = [c for c in contracts if c.underlying == underlying and c.expiry >= as_of]
    selected: List[OptionContract] = []
    for expiry in sorted({c.expiry for c in mine})[:expiries]:
        in_expiry = [c for c in mine if c.expiry == expiry]
        strikes = [c.strike for c in in_expiry]
        step = strike_step(strikes)
        atm = atm_strike(strikes, underlying_price)
        low, high = atm - width * step - 1e-6, atm + width * step + 1e-6
        selected.extend(c for c in in_expiry if low <= c.strike <= high)
    return sorted(selected, key=lambda c: (c.expiry, c.strike, c.option_type))
```

- [ ] **Step 4: Run the tests**

Run: `.venv/Scripts/python -m pytest tests/test_options.py -q`
Expected: `4 passed`.

- [ ] **Step 5: Commit**

```bash
git add aitrader/options.py tests/test_options.py
git commit -m "Add option strike-step, ATM and contract selection"
```

---

### Task 6: Rate limiter, retry and log redaction (pure)

**Files:**
- Create: `aitrader/ratelimit.py`, `aitrader/redaction.py`, `tests/test_ratelimit.py`, `tests/test_redaction.py`

**Interfaces:**
- Produces:
  - `RateLimiter(per_second: float, clock=time.monotonic, sleep=time.sleep)` with `.acquire() -> None`;
  - `retry(fn, *, attempts=5, base_delay=0.5, is_transient=..., sleep=time.sleep)`;
  - `RedactingFilter(secrets)` with `.redact(text) -> str`;
  - `redact_text(text) -> str` (pattern-only);
  - `configure_logging(secrets) -> None`.

- [ ] **Step 1: Write the failing tests**

`tests/test_ratelimit.py`:

```python
import pytest

from aitrader.ratelimit import RateLimiter, retry


class FakeTime:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(round(seconds, 6))
        self.now += seconds


def test_limiter_spaces_calls():
    t = FakeTime()
    limiter = RateLimiter(2, clock=t.clock, sleep=t.sleep)
    for _ in range(3):
        limiter.acquire()
    assert t.sleeps == [0.5, 0.5]


def test_retry_retries_transient_then_succeeds():
    t = FakeTime()
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise TimeoutError("transient")
        return "ok"

    assert retry(flaky, is_transient=lambda e: isinstance(e, TimeoutError), sleep=t.sleep) == "ok"
    assert len(calls) == 3 and t.sleeps == [0.5, 1.0]


def test_retry_does_not_retry_permanent_errors():
    t = FakeTime()
    with pytest.raises(ValueError):
        retry(lambda: (_ for _ in ()).throw(ValueError("bad")), is_transient=lambda e: False, sleep=t.sleep)
    assert t.sleeps == []


def test_retry_gives_up_after_attempts():
    t = FakeTime()
    with pytest.raises(TimeoutError):
        retry(lambda: (_ for _ in ()).throw(TimeoutError()), attempts=3,
              is_transient=lambda e: True, sleep=t.sleep)
    assert t.sleeps == [0.5, 1.0]
```

`tests/test_redaction.py`:

```python
import io
import logging

from aitrader.redaction import RedactingFilter, redact_text


def test_redact_text_masks_token_fields_in_urls_and_json():
    line = 'GET /local/kite/callback?request_token=abc123&status=success {"access_token": "tok999"}'
    out = redact_text(line)
    assert "abc123" not in out and "tok999" not in out
    assert "request_token=***" in out


def test_filter_masks_known_secret_values_in_formatted_records():
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(RedactingFilter(["my-api-secret-value"]))
    log = logging.getLogger("redaction-test")
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    log.info("secret is %s", "my-api-secret-value")
    log.removeHandler(handler)
    assert "my-api-secret-value" not in stream.getvalue()
    assert "***" in stream.getvalue()
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_ratelimit.py tests/test_redaction.py -q`
Expected: two `ModuleNotFoundError`s.

- [ ] **Step 3: Implement**

`aitrader/ratelimit.py`:

```python
from __future__ import annotations

import threading
import time
from typing import Callable, TypeVar

T = TypeVar("T")


class RateLimiter:
    """Allows at most `per_second` acquisitions per second, spacing them evenly."""

    def __init__(self, per_second: float, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep):
        self._interval = 1.0 / per_second
        self._clock = clock
        self._sleep = sleep
        self._next_free = None
        self._lock = threading.Lock()

    def acquire(self) -> None:
        with self._lock:
            now = self._clock()
            start = now if self._next_free is None else max(now, self._next_free)
            if start > now:
                self._sleep(start - now)
            self._next_free = start + self._interval


def retry(fn: Callable[[], T], *, attempts: int = 5, base_delay: float = 0.5,
          is_transient: Callable[[BaseException], bool] = lambda exc: False,
          sleep: Callable[[float], None] = time.sleep) -> T:
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - classification is delegated to is_transient
            if attempt == attempts or not is_transient(exc):
                raise
            sleep(base_delay * 2 ** (attempt - 1))
    raise AssertionError("unreachable")
```

`aitrader/redaction.py`:

```python
from __future__ import annotations

import logging
import re
from typing import Iterable

_FIELD_RE = re.compile(
    r"(?i)(access_token|api_secret|request_token|enctoken|checksum)(['\"]?\s*[:=]\s*['\"]?)([^'\"&\s,}]+)"
)


def redact_text(text: str) -> str:
    return _FIELD_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}***", text)


class RedactingFilter(logging.Filter):
    def __init__(self, secrets: Iterable[str] = ()):
        super().__init__()
        self._secrets = [s for s in secrets if s and len(s) >= 4]

    def redact(self, text: str) -> str:
        for secret in self._secrets:
            text = text.replace(secret, "***")
        return redact_text(text)

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self.redact(record.getMessage())
        record.args = None
        return True


def configure_logging(secrets: Iterable[str]) -> None:
    """Root handler with redaction. Run uvicorn with log_config=None so its loggers propagate here."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    redactor = RedactingFilter(secrets)
    for handler in logging.getLogger().handlers:
        handler.addFilter(redactor)
```

- [ ] **Step 4: Run the tests**

Run: `.venv/Scripts/python -m pytest tests/test_ratelimit.py tests/test_redaction.py -q`
Expected: `6 passed`.

- [ ] **Step 5: Commit**

```bash
git add aitrader/ratelimit.py aitrader/redaction.py tests/test_ratelimit.py tests/test_redaction.py
git commit -m "Add rate limiter, retry with backoff, and secret-redacting log filter"
```

---

### Task 7: Kite gateway interface, real wrapper and fake

**Files:**
- Create: `aitrader/kite/__init__.py`, `aitrader/kite/gateway.py`, `aitrader/kite/real.py`, `aitrader/kite/fake.py`, `tests/kite_samples.py`, `tests/test_kite.py`

**Interfaces:**
- Consumes: `RateLimiter` and `retry` (Task 6).
- Produces:
  - `KiteAuthError` and `KiteTransientError`;
  - `KiteSessionData(access_token: str, user_id: str, login_time: datetime)`, with the token excluded from `repr`;
  - the `KiteGateway` Protocol and `ALLOWED_METHODS`;
  - `RealKiteGateway(api_key, api_secret, access_token=None, client=None, sleep=time.sleep)`;
  - `FakeKiteGateway(*, instruments_by_exchange=None, quotes=None, historical=None, session=None, auth_fail_on=frozenset())`, with `.calls: list[str]`;
  - `tests/kite_samples.py`: `nse_rows()`, `nfo_option_rows(underlying, expiry, center, step, n, lot)`, `nfo_noise_rows()`, `index_quotes(nifty, banknifty)`, `option_quotes(rows)`.

- [ ] **Step 1: Write the samples and the failing test**

`tests/kite_samples.py`:

```python
"""Synthetic Kite responses shaped like the documented Kite Connect v3 payloads."""
from __future__ import annotations

from datetime import date


def nse_rows():
    return [
        {"instrument_token": 408065, "tradingsymbol": "INFY", "name": "INFOSYS", "expiry": "", "strike": 0.0,
         "lot_size": 1, "instrument_type": "EQ", "segment": "NSE", "exchange": "NSE"},
        {"instrument_token": 256265, "tradingsymbol": "NIFTY 50", "name": "NIFTY 50", "expiry": "", "strike": 0.0,
         "lot_size": 0, "instrument_type": "EQ", "segment": "INDICES", "exchange": "NSE"},
        {"instrument_token": 260105, "tradingsymbol": "NIFTY BANK", "name": "NIFTY BANK", "expiry": "",
         "strike": 0.0, "lot_size": 0, "instrument_type": "EQ", "segment": "INDICES", "exchange": "NSE"},
    ]


def nfo_option_rows(underlying: str, expiry: date, center: int, step: int, n: int, lot: int):
    rows, token = [], 10_000_000 + (0 if underlying == "NIFTY" else 5_000_000) + expiry.toordinal() % 1000 * 1000
    for i in range(-n, n + 1):
        strike = center + i * step
        for kind in ("CE", "PE"):
            token += 1
            rows.append({"instrument_token": token, "tradingsymbol": f"{underlying}{expiry:%y%m%d}{strike}{kind}",
                         "name": underlying, "expiry": expiry, "strike": float(strike), "lot_size": lot,
                         "instrument_type": kind, "segment": "NFO-OPT", "exchange": "NFO"})
    return rows


def nfo_noise_rows():
    return [
        {"instrument_token": 1, "tradingsymbol": "NIFTY26OCTFUT", "name": "NIFTY", "expiry": date(2026, 10, 27),
         "strike": 0.0, "lot_size": 75, "instrument_type": "FUT", "segment": "NFO-FUT", "exchange": "NFO"},
        {"instrument_token": 2, "tradingsymbol": "INFY26OCT1500CE", "name": "INFY", "expiry": date(2026, 10, 27),
         "strike": 1500.0, "lot_size": 400, "instrument_type": "CE", "segment": "NFO-OPT", "exchange": "NFO"},
        {"instrument_token": 3, "tradingsymbol": "WEIRD", "name": "NIFTY", "expiry": "", "strike": 0.0,
         "lot_size": 75, "instrument_type": "CE", "segment": "NFO-OPT", "exchange": "NFO"},
    ]


def index_quotes(nifty: float, banknifty: float):
    return {"NSE:NIFTY 50": {"last_price": nifty}, "NSE:NIFTY BANK": {"last_price": banknifty}}


def option_quotes(rows):
    quotes = {}
    for r in rows:
        quotes[f"NFO:{r['tradingsymbol']}"] = {
            "last_price": 120.5, "volume": 5000, "oi": 150000,
            "depth": {"buy": [{"price": 120.0, "quantity": 75}], "sell": [{"price": 121.0, "quantity": 75}]},
        }
    return quotes
```

`tests/test_kite.py`:

```python
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
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_kite.py -q`
Expected: `ModuleNotFoundError: No module named 'aitrader.kite'`.

- [ ] **Step 3: Implement**

`aitrader/kite/__init__.py`: an empty file.

`aitrader/kite/gateway.py`:

```python
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
```

`aitrader/kite/real.py`:

```python
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
```

`aitrader/kite/fake.py`:

```python
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
```

- [ ] **Step 4: Run the tests**

Run: `.venv/Scripts/python -m pytest tests/test_kite.py -q`
Expected: `11 passed`.

- [ ] **Step 5: Commit**

```bash
git add aitrader/kite tests/kite_samples.py tests/test_kite.py
git commit -m "Add read-only Kite gateway (real + fake) with rate limits, retries and error mapping"
```

---

### Task 8: Session expiry and encrypted session store

**Files:**
- Create: `aitrader/auth.py`, `tests/test_auth.py`

**Interfaces:**
- Consumes: `kite_session` (Task 2) and `KiteSessionData` (Task 7).
- Produces:
  - `IST: ZoneInfo`;
  - `session_expiry(login_time: datetime) -> datetime` (aware, IST);
  - `ActiveSession(access_token, user_id, expires_at)`, with the token hidden from repr;
  - `SessionStore(engine, encryption_key: str, clock=...)`, with `.save(session) -> datetime`, `.active() -> ActiveSession | None`, `.status() -> dict` and `.revoke() -> None`.

- [ ] **Step 1: Write the failing test** in `tests/test_auth.py`

```python
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
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_auth.py -q`
Expected: `ModuleNotFoundError: No module named 'aitrader.auth'`.

- [ ] **Step 3: Implement** `aitrader/auth.py`

```python
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
```

- [ ] **Step 4: Run the tests**

Run: `.venv/Scripts/python -m pytest tests/test_auth.py -q`
Expected: `7 passed`.

- [ ] **Step 5: Commit**

```bash
git add aitrader/auth.py tests/test_auth.py
git commit -m "Add Kite session expiry and Fernet-encrypted session store"
```

---

### Task 9: Repository layer (all SQL)

**Files:**
- Create: `aitrader/db/repo.py`, `tests/test_repo.py`

**Interfaces:**
- Consumes: the tables (Task 2) and the models (Task 3).
- Produces (each function takes a SQLAlchemy `Connection` as its first argument):

| Function | Returns |
|---|---|
| `upsert_instrument(conn, *, kind, exchange, symbol, name, yf_symbol)` | `int` id |
| `deactivate_stocks_except(conn, exchange, keep_symbols)` | `list[int]` |
| `active_instruments(conn)` | `list[dict]` with keys id, kind, exchange, symbol, yf_symbol, kite_token |
| `set_kite_tokens(conn, tokens: dict[(exchange, symbol), int])` | `int` |
| `upsert_daily_bars(conn, instrument_id, bars, *, source, price_basis)` | `(inserted, updated)` |
| `load_bars(conn, instrument_id, *, source, start=None, end=None)` | `list[Bar]` |
| `bar_date_range(conn, instrument_id, source)` | `(min, max)` |
| `price_basis_of(conn, instrument_id, source)` | `str \| None` |
| `days_with_any_bar(conn, start, end, source)` | `set[date]` |
| `upsert_corporate_actions(conn, instrument_id, actions, *, source)` | `int` |
| `action_dates(conn, instrument_id)` | `list[date]` |
| `upsert_option_contracts(conn, contracts, *, seen_on)` | `int` |
| `live_option_contracts(conn, as_of)` | `list[(id, OptionContract)]` |
| `insert_option_snapshots(conn, rows)` | `int` |
| `start_run(conn, job, run_date, source=None)` | `int` |
| `finish_run(conn, run_id, status, counts, message="")` | `None` |
| `last_run_date(conn, job, statuses=("ok", "partial"))` | `date \| None` |
| `insert_issues(conn, run_id, issues)` | `int` |
| `delete_issues(conn, check_names, start, end)` | `int` |
| `recent_runs(conn, limit)` | `list[dict]` |
| `recent_issues(conn, severity, limit)` | `list[dict]` |
| `coverage(conn)` | `dict` |
| `issue_counts(conn)` | `dict[str, int]` |
| `last_run_per_job(conn)` | `dict[str, dict]` |
| `has_membership(conn, index_name, instrument_id)` | `bool` |
| `has_open_membership(conn, index_name, instrument_id)` | `bool` |
| `add_membership(conn, *, index_name, instrument_id, valid_from, valid_to, assumption)` | `None` |
| `close_open_memberships(conn, index_name, instrument_ids, valid_to)` | `int` |

- [ ] **Step 1: Write the failing test** in `tests/test_repo.py`

```python
from datetime import date

from aitrader.db import repo
from aitrader.models import Bar, CorporateAction, Issue, OptionContract


def b(day, c=100.0):
    return Bar(date(2026, 10, day), c - 1, c + 1, c - 2, c, 1000)


def make_stock(conn, symbol="INFY"):
    return repo.upsert_instrument(conn, kind="stock", exchange="NSE", symbol=symbol, name=symbol,
                                  yf_symbol=f"{symbol}.NS")


def test_upsert_instrument_is_stable(engine):
    with engine.begin() as conn:
        first = make_stock(conn)
        second = make_stock(conn)
    assert first == second


def test_daily_bars_idempotent_and_detect_real_changes(engine):
    with engine.begin() as conn:
        iid = make_stock(conn)
        assert repo.upsert_daily_bars(conn, iid, [b(1), b(2)], source="yfinance",
                                      price_basis="split_adjusted") == (2, 0)
        assert repo.upsert_daily_bars(conn, iid, [b(1), b(2)], source="yfinance",
                                      price_basis="split_adjusted") == (0, 0)
        assert repo.upsert_daily_bars(conn, iid, [b(2, c=50.0)], source="yfinance",
                                      price_basis="split_adjusted") == (0, 1)
        bars = repo.load_bars(conn, iid, source="yfinance")
    assert [x.close for x in bars] == [100.0, 50.0]


def test_duplicate_dates_in_one_batch_keep_the_last(engine):
    with engine.begin() as conn:
        iid = make_stock(conn)
        repo.upsert_daily_bars(conn, iid, [b(1, c=10.0), b(1, c=11.0)], source="yfinance",
                               price_basis="split_adjusted")
        assert [x.close for x in repo.load_bars(conn, iid, source="yfinance")] == [11.0]


def test_option_contracts_keep_first_seen(engine):
    c = OptionContract("NIFTY", date(2026, 10, 27), 25000.0, "CE", 75, "NIFTY26102725000CE", 1)
    with engine.begin() as conn:
        repo.upsert_option_contracts(conn, [c], seen_on=date(2026, 10, 20))
        repo.upsert_option_contracts(conn, [c], seen_on=date(2026, 10, 21))
        live = repo.live_option_contracts(conn, date(2026, 10, 21))
        assert [x for _, x in live] == [c]
        assert repo.live_option_contracts(conn, date(2026, 10, 28)) == []


def test_runs_and_issues(engine):
    with engine.begin() as conn:
        iid = make_stock(conn)
        run_id = repo.start_run(conn, "ingest_daily_bars", date(2026, 10, 20))
        repo.finish_run(conn, run_id, "ok", {"bars": 2})
        repo.insert_issues(conn, run_id, [Issue("missing_bar", "warn", date(2026, 10, 20), "x", iid)])
        assert repo.last_run_date(conn, "ingest_daily_bars") == date(2026, 10, 20)
        assert repo.issue_counts(conn) == {"warn": 1}
        assert repo.recent_issues(conn, None, 10)[0]["symbol"] == "INFY"
        assert repo.delete_issues(conn, ["missing_bar"], date(2026, 10, 1), date(2026, 10, 31)) == 1
        assert repo.last_run_per_job(conn)["ingest_daily_bars"]["status"] == "ok"


def test_coverage_and_actions(engine):
    with engine.begin() as conn:
        iid = make_stock(conn)
        repo.upsert_daily_bars(conn, iid, [b(1), b(2)], source="yfinance", price_basis="split_adjusted")
        repo.upsert_corporate_actions(conn, iid, [CorporateAction(date(2026, 10, 2), "split", 1, 2)],
                                      source="yfinance")
        cov = repo.coverage(conn)
        assert cov["bars"]["stock"]["instruments"] == 1
        assert cov["bars"]["stock"]["first_date"] == date(2026, 10, 1)
        assert repo.action_dates(conn, iid) == [date(2026, 10, 2)]
        assert repo.price_basis_of(conn, iid, "yfinance") == "split_adjusted"
        assert repo.days_with_any_bar(conn, date(2026, 10, 1), date(2026, 10, 31), "yfinance") == {
            date(2026, 10, 1), date(2026, 10, 2)}
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_repo.py -q`
Expected: `ImportError: cannot import name 'repo' from 'aitrader.db'`.

- [ ] **Step 3: Implement** `aitrader/db/repo.py`

```python
from __future__ import annotations

from datetime import date
from typing import Collection, Dict, List, Optional, Sequence, Set, Tuple

from sqlalchemy import delete, func, literal_column, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection

from aitrader.db.schema import (
    corporate_actions,
    daily_bars,
    data_quality_issues,
    index_membership,
    ingestion_runs,
    instruments,
    option_contracts,
    option_snapshots,
)
from aitrader.models import Bar, CorporateAction, Issue, OptionContract

_BAR_VALUES = ("open", "high", "low", "close", "volume", "price_basis")


# --- instruments ------------------------------------------------------------

def upsert_instrument(conn: Connection, *, kind: str, exchange: str, symbol: str, name: str,
                      yf_symbol: Optional[str]) -> int:
    stmt = pg_insert(instruments).values(kind=kind, exchange=exchange, symbol=symbol, name=name,
                                         yf_symbol=yf_symbol, active=True)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_instruments_exchange_symbol",
        set_={"kind": stmt.excluded.kind, "name": stmt.excluded.name,
              "yf_symbol": stmt.excluded.yf_symbol, "active": True},
    ).returning(instruments.c.id)
    return conn.execute(stmt).scalar_one()


def deactivate_stocks_except(conn: Connection, exchange: str, keep_symbols: Collection[str]) -> List[int]:
    stmt = (update(instruments)
            .where(instruments.c.kind == "stock", instruments.c.exchange == exchange,
                   instruments.c.active.is_(True), instruments.c.symbol.not_in(list(keep_symbols)))
            .values(active=False).returning(instruments.c.id))
    return list(conn.execute(stmt).scalars())


def active_instruments(conn: Connection) -> List[dict]:
    rows = conn.execute(
        select(instruments.c.id, instruments.c.kind, instruments.c.exchange, instruments.c.symbol,
               instruments.c.yf_symbol, instruments.c.kite_token)
        .where(instruments.c.active.is_(True)).order_by(instruments.c.kind, instruments.c.symbol)
    ).mappings()
    return [dict(r) for r in rows]


def set_kite_tokens(conn: Connection, tokens: Dict[Tuple[str, str], int]) -> int:
    count = 0
    for (exchange, symbol), token in tokens.items():
        count += conn.execute(update(instruments)
                              .where(instruments.c.exchange == exchange, instruments.c.symbol == symbol)
                              .values(kite_token=token)).rowcount
    return count


# --- daily bars & corporate actions -------------------------------------------

def upsert_daily_bars(conn: Connection, instrument_id: int, bars: Sequence[Bar], *, source: str,
                      price_basis: str) -> Tuple[int, int]:
    latest = {b.trade_date: b for b in bars}  # one row per date; last one wins
    if not latest:
        return 0, 0
    rows = [dict(instrument_id=instrument_id, trade_date=b.trade_date, open=b.open, high=b.high, low=b.low,
                 close=b.close, volume=b.volume, source=source, price_basis=price_basis)
            for b in latest.values()]
    stmt = pg_insert(daily_bars).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=[daily_bars.c.instrument_id, daily_bars.c.trade_date, daily_bars.c.source],
        set_={**{c: stmt.excluded[c] for c in _BAR_VALUES}, "ingested_at": func.now()},
        where=tuple_(*[daily_bars.c[c] for c in _BAR_VALUES]).is_distinct_from(
            tuple_(*[stmt.excluded[c] for c in _BAR_VALUES])),
    ).returning(literal_column("(xmax = 0)").label("inserted"))
    flags = list(conn.execute(stmt).scalars())
    inserted = sum(1 for f in flags if f)
    return inserted, len(flags) - inserted


def load_bars(conn: Connection, instrument_id: int, *, source: str, start: Optional[date] = None,
              end: Optional[date] = None) -> List[Bar]:
    q = select(daily_bars).where(daily_bars.c.instrument_id == instrument_id, daily_bars.c.source == source)
    if start:
        q = q.where(daily_bars.c.trade_date >= start)
    if end:
        q = q.where(daily_bars.c.trade_date <= end)
    return [Bar(r.trade_date, float(r.open), float(r.high), float(r.low), float(r.close), int(r.volume))
            for r in conn.execute(q.order_by(daily_bars.c.trade_date))]


def bar_date_range(conn: Connection, instrument_id: int, source: str) -> Tuple[Optional[date], Optional[date]]:
    row = conn.execute(select(func.min(daily_bars.c.trade_date), func.max(daily_bars.c.trade_date))
                       .where(daily_bars.c.instrument_id == instrument_id, daily_bars.c.source == source)).one()
    return row[0], row[1]


def price_basis_of(conn: Connection, instrument_id: int, source: str) -> Optional[str]:
    return conn.execute(select(daily_bars.c.price_basis)
                        .where(daily_bars.c.instrument_id == instrument_id, daily_bars.c.source == source)
                        .limit(1)).scalar()


def days_with_any_bar(conn: Connection, start: date, end: date, source: str) -> Set[date]:
    return set(conn.execute(select(daily_bars.c.trade_date).distinct()
                            .where(daily_bars.c.source == source,
                                   daily_bars.c.trade_date.between(start, end))).scalars())


def upsert_corporate_actions(conn: Connection, instrument_id: int, actions: Sequence[CorporateAction], *,
                             source: str) -> int:
    if not actions:
        return 0
    rows = [dict(instrument_id=instrument_id, ex_date=a.ex_date, kind=a.kind, source=source,
                 ratio_from=a.ratio_from, ratio_to=a.ratio_to) for a in actions]
    return conn.execute(pg_insert(corporate_actions).values(rows).on_conflict_do_nothing()).rowcount


def action_dates(conn: Connection, instrument_id: int) -> List[date]:
    return list(conn.execute(select(corporate_actions.c.ex_date).distinct()
                             .where(corporate_actions.c.instrument_id == instrument_id)
                             .order_by(corporate_actions.c.ex_date)).scalars())


# --- options -----------------------------------------------------------------

def upsert_option_contracts(conn: Connection, contracts: Sequence[OptionContract], *, seen_on: date) -> int:
    unique = {(c.tradingsymbol, c.expiry): c for c in contracts}
    if not unique:
        return 0
    rows = [dict(underlying=c.underlying, expiry=c.expiry, strike=c.strike, option_type=c.option_type,
                 lot_size=c.lot_size, tradingsymbol=c.tradingsymbol, kite_token=c.kite_token,
                 first_seen=seen_on, last_seen=seen_on) for c in unique.values()]
    stmt = pg_insert(option_contracts).values(rows)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_option_contracts_symbol_expiry",
        set_={"last_seen": stmt.excluded.last_seen, "kite_token": stmt.excluded.kite_token,
              "lot_size": stmt.excluded.lot_size},
    )
    return conn.execute(stmt).rowcount


def live_option_contracts(conn: Connection, as_of: date) -> List[Tuple[int, OptionContract]]:
    rows = conn.execute(select(option_contracts).where(option_contracts.c.expiry >= as_of)
                        .order_by(option_contracts.c.expiry, option_contracts.c.strike,
                                  option_contracts.c.option_type))
    return [(r.id, OptionContract(r.underlying, r.expiry, float(r.strike), r.option_type, r.lot_size,
                                  r.tradingsymbol, r.kite_token)) for r in rows]


def insert_option_snapshots(conn: Connection, rows: Sequence[dict]) -> int:
    if not rows:
        return 0
    return conn.execute(pg_insert(option_snapshots).values(list(rows)).on_conflict_do_nothing()).rowcount


# --- runs & issues -------------------------------------------------------------

def start_run(conn: Connection, job: str, run_date: date, source: Optional[str] = None) -> int:
    return conn.execute(ingestion_runs.insert().values(job=job, run_date=run_date, status="running",
                                                       source=source).returning(ingestion_runs.c.id)).scalar_one()


def finish_run(conn: Connection, run_id: int, status: str, counts: dict, message: str = "") -> None:
    conn.execute(update(ingestion_runs).where(ingestion_runs.c.id == run_id)
                 .values(status=status, counts=counts, message=message, finished_at=func.now()))


def last_run_date(conn: Connection, job: str, statuses: Collection[str] = ("ok", "partial")) -> Optional[date]:
    return conn.execute(select(func.max(ingestion_runs.c.run_date))
                        .where(ingestion_runs.c.job == job, ingestion_runs.c.status.in_(list(statuses)))).scalar()


def insert_issues(conn: Connection, run_id: int, issues: Sequence[Issue]) -> int:
    if not issues:
        return 0
    conn.execute(data_quality_issues.insert(), [
        dict(run_id=run_id, instrument_id=i.instrument_id, contract_id=i.contract_id, trade_date=i.trade_date,
             check_name=i.check_name, severity=i.severity, detail=i.detail) for i in issues])
    return len(issues)


def delete_issues(conn: Connection, check_names: Collection[str], start: date, end: date) -> int:
    return conn.execute(delete(data_quality_issues)
                        .where(data_quality_issues.c.check_name.in_(list(check_names)),
                               data_quality_issues.c.trade_date.between(start, end))).rowcount


def recent_runs(conn: Connection, limit: int) -> List[dict]:
    rows = conn.execute(select(ingestion_runs).order_by(ingestion_runs.c.id.desc()).limit(limit)).mappings()
    return [dict(r) for r in rows]


def recent_issues(conn: Connection, severity: Optional[str], limit: int) -> List[dict]:
    q = (select(data_quality_issues, instruments.c.symbol, option_contracts.c.tradingsymbol)
         .select_from(data_quality_issues
                      .outerjoin(instruments, instruments.c.id == data_quality_issues.c.instrument_id)
                      .outerjoin(option_contracts, option_contracts.c.id == data_quality_issues.c.contract_id))
         .order_by(data_quality_issues.c.trade_date.desc(), data_quality_issues.c.id.desc()).limit(limit))
    if severity:
        q = q.where(data_quality_issues.c.severity == severity)
    return [dict(r) for r in conn.execute(q).mappings()]


def coverage(conn: Connection) -> dict:
    bars = {}
    for kind, n, first, last in conn.execute(
            select(instruments.c.kind, func.count(func.distinct(daily_bars.c.instrument_id)),
                   func.min(daily_bars.c.trade_date), func.max(daily_bars.c.trade_date))
            .select_from(daily_bars.join(instruments, instruments.c.id == daily_bars.c.instrument_id))
            .group_by(instruments.c.kind)):
        bars[kind] = {"instruments": n, "first_date": first, "last_date": last}
    active = dict(conn.execute(select(instruments.c.kind, func.count())
                               .where(instruments.c.active.is_(True)).group_by(instruments.c.kind)).all())
    snaps = conn.execute(select(func.count(), func.min(option_snapshots.c.snapshot_at),
                                func.max(option_snapshots.c.snapshot_at))).one()
    return {"active_instruments": active, "bars": bars,
            "option_snapshots": {"rows": snaps[0], "first_at": snaps[1], "last_at": snaps[2]}}


def issue_counts(conn: Connection) -> Dict[str, int]:
    return dict(conn.execute(select(data_quality_issues.c.severity, func.count())
                             .group_by(data_quality_issues.c.severity)).all())


def last_run_per_job(conn: Connection) -> Dict[str, dict]:
    rows = conn.execute(select(ingestion_runs).distinct(ingestion_runs.c.job)
                        .order_by(ingestion_runs.c.job, ingestion_runs.c.id.desc())).mappings()
    return {r["job"]: dict(r) for r in rows}


# --- index membership ----------------------------------------------------------

def has_membership(conn: Connection, index_name: str, instrument_id: int) -> bool:
    return conn.execute(select(func.count()).select_from(index_membership)
                        .where(index_membership.c.index_name == index_name,
                               index_membership.c.instrument_id == instrument_id)).scalar_one() > 0


def has_open_membership(conn: Connection, index_name: str, instrument_id: int) -> bool:
    return conn.execute(select(func.count()).select_from(index_membership)
                        .where(index_membership.c.index_name == index_name,
                               index_membership.c.instrument_id == instrument_id,
                               index_membership.c.valid_to.is_(None))).scalar_one() > 0


def add_membership(conn: Connection, *, index_name: str, instrument_id: int, valid_from: date,
                   valid_to: Optional[date], assumption: bool) -> None:
    conn.execute(pg_insert(index_membership).values(
        index_name=index_name, instrument_id=instrument_id, valid_from=valid_from, valid_to=valid_to,
        is_backfilled_assumption=assumption).on_conflict_do_nothing())


def close_open_memberships(conn: Connection, index_name: str, instrument_ids: Collection[int],
                           valid_to: date) -> int:
    if not instrument_ids:
        return 0
    return conn.execute(update(index_membership)
                        .where(index_membership.c.index_name == index_name,
                               index_membership.c.instrument_id.in_(list(instrument_ids)),
                               index_membership.c.valid_to.is_(None))
                        .values(valid_to=valid_to)).rowcount
```

- [ ] **Step 4: Run the tests**

Run: `.venv/Scripts/python -m pytest tests/test_repo.py -q`
Expected: `6 passed`.

- [ ] **Step 5: Commit**

```bash
git add aitrader/db/repo.py tests/test_repo.py
git commit -m "Add repository layer: idempotent bar upserts with change detection, options, runs, issues"
```

---

### Task 10: yfinance daily bar source

**Files:**
- Create: `aitrader/sources/__init__.py`, `aitrader/sources/yfinance_source.py`, `tests/test_yfinance_source.py`

**Interfaces:**
- Consumes: `Bar` and `CorporateAction` (Task 3).
- Produces:
  - the `DailyBarSource` Protocol, with attributes `name: str` and `price_basis: str` and the method `fetch(symbol, start, end) -> tuple[list[Bar], list[CorporateAction]]` (`end` inclusive);
  - `YFinanceSource(history_fn=None)`, with `name="yfinance"` and `price_basis="split_adjusted"`.

- [ ] **Step 1: Write the failing test** in `tests/test_yfinance_source.py`

```python
from datetime import date

import pandas as pd

from aitrader.sources.yfinance_source import YFinanceSource


def frame():
    index = pd.to_datetime(["2026-10-19", "2026-10-20", "2026-10-21"]).tz_localize("Asia/Kolkata")
    return pd.DataFrame({
        "Open": [100.0, float("nan"), 50.5], "High": [101.0, float("nan"), 51.0],
        "Low": [99.0, float("nan"), 50.0], "Close": [100.5, float("nan"), 50.8],
        "Volume": [1000, 0, 2000], "Dividends": [0.0, 0.0, 0.0], "Stock Splits": [0.0, 0.0, 2.0],
    }, index=index)


def test_fetch_parses_bars_skips_nan_rows_and_reads_splits():
    calls = []

    def history(symbol, start, end):
        calls.append((symbol, start, end))
        return frame()

    source = YFinanceSource(history_fn=history)
    bars, actions = source.fetch("INFY.NS", date(2026, 10, 19), date(2026, 10, 21))
    assert [b.trade_date for b in bars] == [date(2026, 10, 19), date(2026, 10, 21)]
    assert bars[1].close == 50.8 and bars[1].volume == 2000
    assert [(a.ex_date, a.kind, a.ratio_from, a.ratio_to) for a in actions] == [
        (date(2026, 10, 21), "split", 1.0, 2.0)]
    assert calls == [("INFY.NS", date(2026, 10, 19), date(2026, 10, 22))]  # yfinance end is exclusive
    assert source.name == "yfinance" and source.price_basis == "split_adjusted"


def test_empty_frame_returns_nothing():
    source = YFinanceSource(history_fn=lambda s, a, b: pd.DataFrame())
    assert source.fetch("GONE.NS", date(2026, 10, 1), date(2026, 10, 2)) == ([], [])
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_yfinance_source.py -q`
Expected: `ModuleNotFoundError: No module named 'aitrader.sources'`.

- [ ] **Step 3: Implement**

`aitrader/sources/__init__.py`: an empty file.

`aitrader/sources/yfinance_source.py`:

```python
from __future__ import annotations

from datetime import date, timedelta
from typing import Callable, List, Protocol, Tuple

from aitrader.models import Bar, CorporateAction


class DailyBarSource(Protocol):
    name: str
    price_basis: str

    def fetch(self, symbol: str, start: date, end: date) -> Tuple[List[Bar], List[CorporateAction]]: ...


def _yf_history(symbol: str, start: date, end_exclusive: date):
    import yfinance as yf

    return yf.Ticker(symbol).history(start=start.isoformat(), end=end_exclusive.isoformat(), interval="1d",
                                     auto_adjust=False, actions=True)


def _is_nan(value) -> bool:
    return value != value  # NaN is the only value not equal to itself


class YFinanceSource:
    """Yahoo daily bars. Close with auto_adjust=False is split-adjusted (verified 2026-10-20)."""

    name = "yfinance"
    price_basis = "split_adjusted"

    def __init__(self, history_fn: Callable = _yf_history):
        self._history = history_fn

    def fetch(self, symbol: str, start: date, end: date) -> Tuple[List[Bar], List[CorporateAction]]:
        frame = self._history(symbol, start, end + timedelta(days=1))
        if frame is None or frame.empty:
            return [], []
        bars, actions = [], []
        for ts, row in frame.iterrows():
            day = ts.date()
            prices = [row["Open"], row["High"], row["Low"], row["Close"]]
            if not any(_is_nan(p) for p in prices):
                volume = row.get("Volume", 0)
                bars.append(Bar(day, float(prices[0]), float(prices[1]), float(prices[2]), float(prices[3]),
                                0 if _is_nan(volume) else int(volume)))
            split = row.get("Stock Splits", 0.0)
            if split and not _is_nan(split):
                actions.append(CorporateAction(day, "split", 1.0, float(split)))
        return bars, actions
```

- [ ] **Step 4: Run the tests**

Run: `.venv/Scripts/python -m pytest tests/test_yfinance_source.py -q`
Expected: `2 passed`.

- [ ] **Step 5: Commit**

```bash
git add aitrader/sources tests/test_yfinance_source.py
git commit -m "Add yfinance daily bar source with split extraction (split-adjusted basis)"
```

---

### Task 11: Universe seeding from a manually downloaded constituent CSV

**Files:**
- Create: `aitrader/universe.py`, `tests/test_universe.py`

**Interfaces:**
- Consumes: the repo functions `upsert_instrument`, `has_membership`, `has_open_membership`, `add_membership`, `deactivate_stocks_except` and `close_open_memberships` (Task 9).
- Produces:
  - `UNIVERSE_INDEX = "NIFTY 200"`;
  - `INDICES = (("NIFTY 50", "^NSEI"), ("NIFTY BANK", "^NSEBANK"))`;
  - `parse_constituents_csv(text) -> list[tuple[str, str]]`;
  - `seed_universe(engine, csv_text, *, as_of, history_years=5) -> dict`.

- [ ] **Step 1: Write the failing test** in `tests/test_universe.py`

```python
from datetime import date

import pytest
from sqlalchemy import select

from aitrader.db import repo
from aitrader.db.schema import index_membership
from aitrader.universe import parse_constituents_csv, seed_universe

CSV = "Company Name,Industry,Symbol,Series,ISIN Code\nInfosys Ltd.,IT,INFY,EQ,INE009A01021\nM&M Ltd.,Auto,M&M,EQ,X\n"


def test_parse_handles_bom_padding_and_lowercase_headers():
    text = "\ufeff company name , industry , symbol \nInfosys Ltd., IT , infy \n"
    assert parse_constituents_csv(text) == [("INFY", "Infosys Ltd.")]


def test_parse_rejects_missing_symbol_column_and_bad_symbols():
    with pytest.raises(ValueError):
        parse_constituents_csv("Name\nInfosys\n")
    with pytest.raises(ValueError):
        parse_constituents_csv("Symbol\n<script>\n")
    with pytest.raises(ValueError):
        parse_constituents_csv("Symbol\n\n")


def test_seed_creates_indices_stocks_and_flagged_memberships(engine):
    counts = seed_universe(engine, CSV, as_of=date(2026, 10, 21))
    assert counts == {"stocks": 2, "indices": 2, "deactivated": 0}
    with engine.connect() as conn:
        symbols = {(i["kind"], i["symbol"], i["yf_symbol"]) for i in repo.active_instruments(conn)}
        rows = conn.execute(select(index_membership)).all()
    assert ("stock", "INFY", "INFY.NS") in symbols and ("index", "NIFTY BANK", "^NSEBANK") in symbols
    assumed = [r for r in rows if r.is_backfilled_assumption]
    current = [r for r in rows if not r.is_backfilled_assumption]
    assert len(assumed) == 2 and all(r.valid_from == date(2021, 10, 21) and r.valid_to == date(2026, 10, 20)
                                     for r in assumed)
    assert len(current) == 2 and all(r.valid_to is None for r in current)


def test_reseed_is_idempotent_and_deactivates_removed(engine):
    seed_universe(engine, CSV, as_of=date(2026, 10, 21))
    seed_universe(engine, CSV, as_of=date(2026, 10, 21))
    counts = seed_universe(engine, "Symbol\nINFY\n", as_of=date(2026, 11, 1))
    assert counts["deactivated"] == 1
    with engine.connect() as conn:
        assert "M&M" not in {i["symbol"] for i in repo.active_instruments(conn)}
        open_rows = conn.execute(select(index_membership).where(index_membership.c.valid_to.is_(None))).all()
    assert len(open_rows) == 1
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_universe.py -q`
Expected: `ModuleNotFoundError: No module named 'aitrader.universe'`.

- [ ] **Step 3: Implement** `aitrader/universe.py`

```python
from __future__ import annotations

import csv
import io
import re
from datetime import date, timedelta
from typing import List, Tuple

from sqlalchemy.engine import Engine

from aitrader.db import repo

UNIVERSE_INDEX = "NIFTY 200"
INDICES = (("NIFTY 50", "^NSEI"), ("NIFTY BANK", "^NSEBANK"))
_SYMBOL_RE = re.compile(r"^[A-Z0-9&\-]{1,20}$")


def parse_constituents_csv(text: str) -> List[Tuple[str, str]]:
    reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
    if not reader.fieldnames:
        raise ValueError("CSV is empty")
    columns = {name.strip().lower(): name for name in reader.fieldnames}
    symbol_col, name_col = columns.get("symbol"), columns.get("company name")
    if symbol_col is None:
        raise ValueError(f"CSV has no 'Symbol' column (found: {reader.fieldnames})")
    result, bad = [], []
    for row in reader:
        symbol = (row.get(symbol_col) or "").strip().upper()
        if not symbol:
            continue
        if not _SYMBOL_RE.match(symbol):
            bad.append(symbol)
            continue
        result.append((symbol, (row.get(name_col) or "").strip() if name_col else ""))
    if bad:
        raise ValueError(f"CSV contains invalid symbols: {bad[:5]}")
    if not result:
        raise ValueError("CSV contains no symbols")
    return result


def _years_before(day: date, years: int) -> date:
    try:
        return day.replace(year=day.year - years)
    except ValueError:  # 29 Feb
        return day.replace(year=day.year - years, day=28)


def seed_universe(engine: Engine, csv_text: str, *, as_of: date, history_years: int = 5) -> dict:
    constituents = parse_constituents_csv(csv_text)
    assumed_from = _years_before(as_of, history_years)
    day_before = as_of - timedelta(days=1)
    with engine.begin() as conn:
        for symbol, yf_symbol in INDICES:
            repo.upsert_instrument(conn, kind="index", exchange="NSE", symbol=symbol, name=symbol,
                                   yf_symbol=yf_symbol)
        for symbol, name in constituents:
            iid = repo.upsert_instrument(conn, kind="stock", exchange="NSE", symbol=symbol, name=name or symbol,
                                         yf_symbol=f"{symbol}.NS")
            if not repo.has_membership(conn, UNIVERSE_INDEX, iid):
                # Membership before the first seed is unknown: record it as an explicit assumption.
                repo.add_membership(conn, index_name=UNIVERSE_INDEX, instrument_id=iid, valid_from=assumed_from,
                                    valid_to=day_before, assumption=True)
            if not repo.has_open_membership(conn, UNIVERSE_INDEX, iid):
                repo.add_membership(conn, index_name=UNIVERSE_INDEX, instrument_id=iid, valid_from=as_of,
                                    valid_to=None, assumption=False)
        removed = repo.deactivate_stocks_except(conn, "NSE", [s for s, _ in constituents])
        repo.close_open_memberships(conn, UNIVERSE_INDEX, removed, day_before)
    return {"stocks": len(constituents), "indices": len(INDICES), "deactivated": len(removed)}
```

- [ ] **Step 4: Run the tests**

Run: `.venv/Scripts/python -m pytest tests/test_universe.py -q`
Expected: `4 passed`.

- [ ] **Step 5: Commit**

```bash
git add aitrader/universe.py tests/test_universe.py
git commit -m "Add universe seeding from a manually downloaded Nifty 200 CSV with flagged history"
```

---

### Task 12: Jobs

**Files:**
- Create: `aitrader/jobs.py`, `tests/fakes.py`, `tests/test_jobs.py`

**Interfaces:**
- Consumes everything from Tasks 3–11.
- Produces:
  - `JobDeps(engine, kite: Callable[[], KiteGateway | None], bar_source: DailyBarSource, calendar: TradingCalendar, now: Callable[[], datetime], on_auth_error: Callable[[], None])`;
  - `RunResult(status: str, counts: dict, message: str = "")`;
  - `JOB_NAMES: tuple[str, ...]`;
  - `run_job(name: str, deps: JobDeps, run_date: date, **kwargs) -> RunResult` (raises `ValueError` for unknown names).
  - `tests/fakes.py`: `FakeBarSource(data: dict[str, tuple[list[Bar], list[CorporateAction]] | Exception])`.

- [ ] **Step 1: Write the fakes and the failing test**

`tests/fakes.py`:

```python
from __future__ import annotations


class FakeBarSource:
    name = "yfinance"
    price_basis = "split_adjusted"

    def __init__(self, data):
        self.data = data
        self.calls = []

    def fetch(self, symbol, start, end):
        self.calls.append((symbol, start, end))
        value = self.data.get(symbol, ([], []))
        if isinstance(value, Exception):
            raise value
        bars, actions = value
        return [b for b in bars if start <= b.trade_date <= end], actions
```

`tests/test_jobs.py`:

```python
from datetime import date, datetime

import pytest
from sqlalchemy import func, select

from aitrader.auth import IST
from aitrader.calendar import TradingCalendar
from aitrader.db import repo
from aitrader.db.schema import daily_bars, option_snapshots
from aitrader.jobs import JobDeps, run_job
from aitrader.kite.fake import FakeKiteGateway
from aitrader.models import Bar
from fakes import FakeBarSource
from kite_samples import index_quotes, nfo_noise_rows, nfo_option_rows, nse_rows, option_quotes

RUN_DAY = date(2026, 10, 21)  # Wednesday


def deps(engine, kite=None, bars=None, auth_errors=None):
    return JobDeps(
        engine=engine,
        kite=lambda: kite,
        bar_source=FakeBarSource(bars or {}),
        calendar=TradingCalendar(),
        now=lambda: datetime(2026, 10, 21, 15, 35, tzinfo=IST),
        on_auth_error=(lambda: auth_errors.append(1)) if auth_errors is not None else (lambda: None),
    )


def seed(engine):
    with engine.begin() as conn:
        for kind, symbol, yf in (("stock", "INFY", "INFY.NS"), ("stock", "TCS", "TCS.NS"),
                                 ("index", "NIFTY 50", "^NSEI"), ("index", "NIFTY BANK", "^NSEBANK")):
            repo.upsert_instrument(conn, kind=kind, exchange="NSE", symbol=symbol, name=symbol, yf_symbol=yf)


def option_universe():
    return (nfo_option_rows("NIFTY", date(2026, 10, 27), 25000, 50, 15)
            + nfo_option_rows("NIFTY", date(2026, 11, 3), 25000, 50, 15)
            + nfo_option_rows("BANKNIFTY", date(2026, 10, 27), 56000, 100, 15)
            + nfo_option_rows("BANKNIFTY", date(2026, 11, 24), 56000, 100, 15))


def last_run(engine, job):
    with engine.connect() as conn:
        return repo.last_run_per_job(conn)[job]


def test_unknown_job_is_rejected(engine):
    with pytest.raises(ValueError):
        run_job("place_order", deps(engine), RUN_DAY)


def test_kite_jobs_skip_when_not_logged_in_and_snapshot_records_gap(engine):
    seed(engine)
    assert run_job("sync_instruments", deps(engine), RUN_DAY).status == "skipped"
    result = run_job("snapshot_options", deps(engine), RUN_DAY)
    assert result.status == "skipped" and "not logged in" in result.message
    with engine.connect() as conn:
        issues = repo.recent_issues(conn, None, 10)
    assert [(i["check_name"], i["trade_date"]) for i in issues] == [("missed_snapshot", RUN_DAY)]


def test_sync_stores_tokens_and_only_index_option_contracts(engine):
    seed(engine)
    kite = FakeKiteGateway(instruments_by_exchange={"NSE": nse_rows(),
                                                    "NFO": option_universe() + nfo_noise_rows()})
    result = run_job("sync_instruments", deps(engine, kite), RUN_DAY)
    assert result.status == "ok"
    with engine.connect() as conn:
        live = repo.live_option_contracts(conn, RUN_DAY)
        tokens = {i["symbol"]: i["kite_token"] for i in repo.active_instruments(conn)}
    assert {c.underlying for _, c in live} == {"NIFTY", "BANKNIFTY"}
    assert all(c.option_type in ("CE", "PE") for _, c in live)
    assert len(live) == 4 * 31 * 2
    assert tokens["INFY"] == 408065 and tokens["NIFTY 50"] == 256265 and tokens["TCS"] is None


def test_snapshot_selects_atm_window_for_two_expiries(engine):
    seed(engine)
    rows = option_universe()
    kite = FakeKiteGateway(instruments_by_exchange={"NSE": nse_rows(), "NFO": rows},
                           quotes={**index_quotes(25010.0, 56040.0), **option_quotes(rows)})
    run_job("sync_instruments", deps(engine, kite), RUN_DAY)
    result = run_job("snapshot_options", deps(engine, kite), RUN_DAY)
    assert result.status == "ok"
    assert result.counts["snapshots"] == 2 * 2 * 21 * 2
    with engine.connect() as conn:
        bid, ask = conn.execute(select(option_snapshots.c.bid, option_snapshots.c.ask).limit(1)).one()
    assert (float(bid), float(ask)) == (120.0, 121.0)


def test_snapshot_partial_when_quotes_missing(engine):
    seed(engine)
    rows = option_universe()
    some_quotes = dict(list(option_quotes(rows).items())[::2])
    kite = FakeKiteGateway(instruments_by_exchange={"NSE": nse_rows(), "NFO": rows},
                           quotes={**index_quotes(25010.0, 56040.0), **some_quotes})
    run_job("sync_instruments", deps(engine, kite), RUN_DAY)
    assert run_job("snapshot_options", deps(engine, kite), RUN_DAY).status == "partial"


def test_auth_failure_mid_job_fails_cleanly_and_revokes(engine):
    seed(engine)
    calls = []
    kite = FakeKiteGateway(instruments_by_exchange={"NSE": nse_rows()}, auth_fail_on={"instruments"})
    result = run_job("sync_instruments", deps(engine, kite, auth_errors=calls), RUN_DAY)
    assert result.status == "failed" and "log in again" in result.message
    assert calls == [1]
    assert last_run(engine, "sync_instruments")["status"] == "failed"


def bars_for(days, close=100.0):
    return [Bar(date(2026, 10, d), close - 1, close + 1, close - 2, close, 1000) for d in days]


def test_ingest_is_idempotent_and_reports_empty_and_failed_symbols(engine):
    seed(engine)
    data = {"INFY.NS": (bars_for([21]), []), "^NSEI": (bars_for([21]), []),
            "^NSEBANK": RuntimeError("yahoo down"), "TCS.NS": ([], [])}
    first = run_job("ingest_daily_bars", deps(engine, bars=data), RUN_DAY)
    assert first.status == "partial"
    assert first.counts == {"instruments": 4, "inserted": 2, "updated": 0, "empty": 1, "failed": 1}
    assert "TCS: empty" in first.message and "NIFTY BANK: RuntimeError" in first.message
    second = run_job("ingest_daily_bars", deps(engine, bars=data), RUN_DAY)
    assert second.counts["inserted"] == 0 and second.counts["updated"] == 0
    with engine.connect() as conn:
        assert conn.execute(select(func.count()).select_from(daily_bars)).scalar_one() == 2


def test_backfill_skips_instruments_already_covered(engine):
    seed(engine)
    full = {sym: (bars_for(range(1, 22)), []) for sym in ("INFY.NS", "TCS.NS", "^NSEI", "^NSEBANK")}
    source_deps = deps(engine, bars=full)
    run_job("backfill_history", source_deps, RUN_DAY, years=0)
    calls_after_first = len(source_deps.bar_source.calls)
    second = run_job("backfill_history", source_deps, RUN_DAY, years=0)
    assert second.counts["skipped"] == 4
    assert len(source_deps.bar_source.calls) == calls_after_first


def test_quality_checks_are_replaced_not_duplicated(engine):
    seed(engine)
    bad = [Bar(date(2026, 10, 20), 100, 99, 98, 100, 10), *bars_for([21])]   # high < open/close on 20th
    data = {"INFY.NS": (bad, []), "TCS.NS": (bars_for([20, 21]), []),
            "^NSEI": (bars_for([20, 21]), []), "^NSEBANK": (bars_for([20, 21]), [])}
    d = deps(engine, bars=data)
    run_job("ingest_daily_bars", d, RUN_DAY, start=date(2026, 10, 20))
    run_job("run_quality_checks", d, RUN_DAY)
    run_job("run_quality_checks", d, RUN_DAY)
    with engine.connect() as conn:
        issues = repo.recent_issues(conn, "error", 50)
    assert [(i["check_name"], i["symbol"], i["trade_date"]) for i in issues] == [
        ("ohlc_invalid", "INFY", date(2026, 10, 20))]


def test_unknown_closed_day_detected_instead_of_missing_bars(engine):
    seed(engine)
    days = [19, 21]  # no bars anywhere on Tuesday 20th
    data = {s: (bars_for(days), []) for s in ("INFY.NS", "TCS.NS", "^NSEI", "^NSEBANK")}
    d = deps(engine, bars=data)
    run_job("ingest_daily_bars", d, RUN_DAY, start=date(2026, 10, 19))
    run_job("run_quality_checks", d, RUN_DAY)
    with engine.connect() as conn:
        checks = {(i["check_name"], i["trade_date"]) for i in repo.recent_issues(conn, None, 50)}
    assert ("unknown_closed_day", date(2026, 10, 20)) in checks
    assert not any(c == "missing_bar" for c, _ in checks)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_jobs.py -q`
Expected: `ModuleNotFoundError: No module named 'aitrader.jobs'`.

- [ ] **Step 3: Implement** `aitrader/jobs.py`

```python
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Callable, Dict, List, Optional

from sqlalchemy.engine import Engine

from aitrader.auth import IST
from aitrader.calendar import TradingCalendar
from aitrader.db import repo
from aitrader.kite.gateway import KiteAuthError, KiteGateway
from aitrader.models import Issue, OptionContract
from aitrader.options import select_contracts
from aitrader.quality import (
    BAR_CHECKS,
    check_jumps,
    check_missing,
    check_ohlc,
    check_source_mismatch,
    check_stale,
    find_unknown_closed_days,
)
from aitrader.redaction import redact_text
from aitrader.sources.yfinance_source import DailyBarSource

log = logging.getLogger(__name__)

UNDERLYING_QUOTE_KEYS = {"NIFTY": "NSE:NIFTY 50", "BANKNIFTY": "NSE:NIFTY BANK"}
QUOTE_BATCH = 500
QUALITY_LOOKBACK_DAYS = 60
NOT_LOGGED_IN = "Kite not logged in"
SESSION_EXPIRED = "Kite session expired — log in again"


@dataclass
class JobDeps:
    engine: Engine
    kite: Callable[[], Optional[KiteGateway]]
    bar_source: DailyBarSource
    calendar: TradingCalendar
    now: Callable[[], datetime] = field(default=lambda: datetime.now(IST))
    on_auth_error: Callable[[], None] = field(default=lambda: None)


@dataclass
class RunResult:
    status: str
    counts: dict
    message: str = ""


def _record_missed_snapshot(deps: JobDeps, run_id: int, run_date: date, reason: str) -> None:
    with deps.engine.begin() as conn:
        repo.insert_issues(conn, run_id, [Issue("missed_snapshot", "warn", run_date,
                                                f"no option snapshot: {reason} (cannot be fetched later)")])


def sync_instruments(deps: JobDeps, run_date: date, run_id: int) -> RunResult:
    kite = deps.kite()
    if kite is None:
        return RunResult("skipped", {}, NOT_LOGGED_IN)
    nse, nfo = kite.instruments("NSE"), kite.instruments("NFO")
    contracts = [
        OptionContract(r["name"], r["expiry"], float(r["strike"]), r["instrument_type"], int(r["lot_size"]),
                       r["tradingsymbol"], int(r["instrument_token"]))
        for r in nfo
        if r.get("name") in UNDERLYING_QUOTE_KEYS and r.get("instrument_type") in ("CE", "PE")
        and r.get("segment") == "NFO-OPT" and isinstance(r.get("expiry"), date)
    ]
    with deps.engine.begin() as conn:
        known = {(i["exchange"], i["symbol"]) for i in repo.active_instruments(conn)}
        tokens = {("NSE", r["tradingsymbol"]): int(r["instrument_token"]) for r in nse
                  if ("NSE", r["tradingsymbol"]) in known}
        updated = repo.set_kite_tokens(conn, tokens)
        stored = repo.upsert_option_contracts(conn, contracts, seen_on=run_date)
    return RunResult("ok", {"instrument_tokens": updated, "option_contracts": stored})


def _best_price(levels) -> Optional[float]:
    if levels and levels[0].get("price"):
        return float(levels[0]["price"])
    return None


def snapshot_options(deps: JobDeps, run_date: date, run_id: int) -> RunResult:
    kite = deps.kite()
    if kite is None:
        _record_missed_snapshot(deps, run_id, run_date, NOT_LOGGED_IN)
        return RunResult("skipped", {}, NOT_LOGGED_IN)
    with deps.engine.connect() as conn:
        live = repo.live_option_contracts(conn, run_date)
    if not live:
        _record_missed_snapshot(deps, run_id, run_date, "no option contracts synced")
        return RunResult("failed", {}, "no option contracts synced — run sync_instruments first")
    ids = {c.tradingsymbol: cid for cid, c in live}
    contracts = [c for _, c in live]
    underlying_quotes = kite.quote(list(UNDERLYING_QUOTE_KEYS.values()))
    snapshot_at = deps.now()
    rows: List[dict] = []
    selected_total = 0
    for underlying, key in UNDERLYING_QUOTE_KEYS.items():
        quote = underlying_quotes.get(key)
        if not quote:
            continue
        price = float(quote["last_price"])
        selected = select_contracts(contracts, underlying, price, run_date)
        selected_total += len(selected)
        for start in range(0, len(selected), QUOTE_BATCH):
            batch = selected[start:start + QUOTE_BATCH]
            quotes = kite.quote([f"NFO:{c.tradingsymbol}" for c in batch])
            for c in batch:
                data = quotes.get(f"NFO:{c.tradingsymbol}")
                if not data:
                    continue
                depth = data.get("depth") or {}
                rows.append(dict(contract_id=ids[c.tradingsymbol], snapshot_at=snapshot_at,
                                 last_price=data["last_price"], bid=_best_price(depth.get("buy")),
                                 ask=_best_price(depth.get("sell")), volume=data.get("volume"),
                                 oi=data.get("oi"), underlying_price=price, source="kite"))
    with deps.engine.begin() as conn:
        stored = repo.insert_option_snapshots(conn, rows)
    counts = {"selected": selected_total, "snapshots": stored}
    if stored == 0:
        _record_missed_snapshot(deps, run_id, run_date, "no option quotes returned")
        return RunResult("failed", counts, "no option quotes returned")
    return RunResult("ok" if stored == selected_total else "partial", counts)


def _ingest(deps: JobDeps, instruments: List[dict], start: date, end: date) -> Dict[str, object]:
    counts = {"instruments": len(instruments), "inserted": 0, "updated": 0, "empty": 0, "failed": 0}
    notes: List[str] = []
    for inst in instruments:
        try:
            bars, actions = deps.bar_source.fetch(inst["yf_symbol"], start, end)
        except Exception as exc:  # noqa: BLE001 - one bad symbol must not stop the run
            counts["failed"] += 1
            notes.append(f"{inst['symbol']}: {type(exc).__name__}")
            continue
        if not bars:
            counts["empty"] += 1
            notes.append(f"{inst['symbol']}: empty")
            continue
        with deps.engine.begin() as conn:
            inserted, updated = repo.upsert_daily_bars(conn, inst["id"], bars, source=deps.bar_source.name,
                                                       price_basis=deps.bar_source.price_basis)
            repo.upsert_corporate_actions(conn, inst["id"], actions, source=deps.bar_source.name)
        counts["inserted"] += inserted
        counts["updated"] += updated
    return {"counts": counts, "notes": notes}


def _status_for(counts: dict) -> str:
    problems = counts["failed"] + counts["empty"]
    if problems == 0:
        return "ok"
    return "failed" if problems >= counts["instruments"] else "partial"


def ingest_daily_bars(deps: JobDeps, run_date: date, run_id: int, start: Optional[date] = None) -> RunResult:
    with deps.engine.connect() as conn:
        instruments = [i for i in repo.active_instruments(conn) if i["yf_symbol"]]
    result = _ingest(deps, instruments, start or run_date, run_date)
    counts = result["counts"]
    return RunResult(_status_for(counts), counts, "; ".join(result["notes"][:20]))


def _years_before(day: date, years: int) -> date:
    try:
        return day.replace(year=day.year - years)
    except ValueError:
        return day.replace(year=day.year - years, day=28)


def backfill_history(deps: JobDeps, run_date: date, run_id: int, years: int = 5) -> RunResult:
    start = _years_before(run_date, years)
    with deps.engine.connect() as conn:
        todo, skipped = [], 0
        for inst in repo.active_instruments(conn):
            if not inst["yf_symbol"]:
                continue
            first, last = repo.bar_date_range(conn, inst["id"], deps.bar_source.name)
            if first and last and first <= start + timedelta(days=7) and last >= run_date - timedelta(days=4):
                skipped += 1
            else:
                todo.append(inst)
    result = _ingest(deps, todo, start, run_date)
    counts = {**result["counts"], "skipped": skipped}
    status = "ok" if not todo else _status_for(result["counts"])
    return RunResult(status, counts, "; ".join(result["notes"][:20]))


def run_quality_checks(deps: JobDeps, run_date: date, run_id: int) -> RunResult:
    window_start = run_date - timedelta(days=QUALITY_LOOKBACK_DAYS)
    expected = deps.calendar.trading_days(window_start, run_date)
    source = deps.bar_source.name
    issues: List[Issue] = []
    checked = no_data = 0
    with deps.engine.connect() as conn:
        any_days = repo.days_with_any_bar(conn, window_start, run_date, source)
        first_any = min(any_days) if any_days else None
        closed = set(find_unknown_closed_days(any_days, [d for d in expected if first_any and d >= first_any]))
        issues += [Issue("unknown_closed_day", "info", d,
                         "no bars for any instrument; add to the holiday file if NSE was closed")
                   for d in sorted(closed)]
        for inst in repo.active_instruments(conn):
            bars = repo.load_bars(conn, inst["id"], source=source, start=window_start, end=run_date)
            if not bars:
                no_data += 1
                continue
            checked += 1
            iid = inst["id"]
            expected_here = [d for d in expected if d >= bars[0].trade_date]
            issues += check_missing(bars, expected_here, closed, iid)
            issues += check_ohlc(bars, iid)
            issues += check_jumps(bars, repo.action_dates(conn, iid), instrument_id=iid)
            issues += check_stale(bars, instrument_id=iid)
            if source != "kite" and repo.price_basis_of(conn, iid, "kite") == deps.bar_source.price_basis:
                kite_bars = repo.load_bars(conn, iid, source="kite", start=window_start, end=run_date)
                issues += check_source_mismatch(bars, kite_bars, instrument_id=iid)
    with deps.engine.begin() as conn:
        repo.delete_issues(conn, BAR_CHECKS, window_start, run_date)
        repo.insert_issues(conn, run_id, issues)
    return RunResult("ok", {"instruments_checked": checked, "no_data": no_data, "issues": len(issues)})


_JOBS: Dict[str, Callable[..., RunResult]] = {
    "sync_instruments": sync_instruments,
    "snapshot_options": snapshot_options,
    "ingest_daily_bars": ingest_daily_bars,
    "run_quality_checks": run_quality_checks,
    "backfill_history": backfill_history,
}
JOB_NAMES = tuple(_JOBS)


def run_job(name: str, deps: JobDeps, run_date: date, **kwargs) -> RunResult:
    if name not in _JOBS:
        raise ValueError(f"unknown job {name!r}; expected one of {JOB_NAMES}")
    with deps.engine.begin() as conn:
        run_id = repo.start_run(conn, name, run_date, source=getattr(deps.bar_source, "name", None))
    try:
        result = _JOBS[name](deps, run_date, run_id, **kwargs)
    except KiteAuthError:
        deps.on_auth_error()
        result = RunResult("failed", {}, SESSION_EXPIRED)
    except Exception as exc:  # noqa: BLE001 - every failure is recorded on the run row
        log.exception("job %s failed", name)
        result = RunResult("failed", {}, redact_text(f"{type(exc).__name__}: {exc}")[:500])
    with deps.engine.begin() as conn:
        repo.finish_run(conn, run_id, result.status, result.counts, result.message)
    return result
```

- [ ] **Step 4: Run the tests**

Run: `.venv/Scripts/python -m pytest tests/test_jobs.py -q`
Expected: `10 passed`.

- [ ] **Step 5: Commit**

```bash
git add aitrader/jobs.py tests/fakes.py tests/test_jobs.py
git commit -m "Add ingestion, snapshot, sync, backfill and quality jobs with run recording"
```

---

### Task 13: Scheduler and start-up catch-up

**Files:**
- Create: `aitrader/scheduler.py`, `tests/test_scheduler.py`

**Interfaces:**
- Consumes: `JobDeps`, `run_job` (Task 12) and `repo.last_run_date` (Task 9).
- Produces:
  - `SCHEDULE: tuple[tuple[str, int, int], ...]`;
  - `CATCH_UP_MAX_DAYS = 30`;
  - `run_if_trading_day(name, deps) -> RunResult | None`;
  - `catch_up(deps) -> list[date]`;
  - `build_scheduler(deps) -> BackgroundScheduler` (not started).

- [ ] **Step 1: Write the failing test** in `tests/test_scheduler.py`

```python
from datetime import date, datetime

from aitrader import scheduler
from aitrader.auth import IST
from aitrader.calendar import TradingCalendar
from aitrader.jobs import JobDeps


def make_deps(now):
    return JobDeps(engine=None, kite=lambda: None, bar_source=None, calendar=TradingCalendar(), now=lambda: now)


def test_weekend_runs_nothing(monkeypatch):
    called = []
    monkeypatch.setattr(scheduler, "run_job", lambda *a, **k: called.append(a))
    assert scheduler.run_if_trading_day("ingest_daily_bars", make_deps(datetime(2026, 10, 24, 16, 30,
                                                                                 tzinfo=IST))) is None
    assert called == []


def test_trading_day_runs_job_for_today(monkeypatch):
    called = []
    monkeypatch.setattr(scheduler, "run_job", lambda name, deps, day: called.append((name, day)) or "done")
    deps = make_deps(datetime(2026, 10, 21, 16, 30, tzinfo=IST))
    assert scheduler.run_if_trading_day("ingest_daily_bars", deps) == "done"
    assert called == [("ingest_daily_bars", date(2026, 10, 21))]


def test_catch_up_fills_trading_days_since_last_success(monkeypatch):
    ran = []
    monkeypatch.setattr(scheduler, "_last_ingest_date", lambda deps: date(2026, 10, 15))
    monkeypatch.setattr(scheduler, "run_job", lambda name, deps, day: ran.append(day))
    deps = make_deps(datetime(2026, 10, 21, 10, 0, tzinfo=IST))  # before 16:30: today not final yet
    assert scheduler.catch_up(deps) == [date(2026, 10, 16), date(2026, 10, 19), date(2026, 10, 20)]
    assert ran == [date(2026, 10, 16), date(2026, 10, 19), date(2026, 10, 20)]


def test_catch_up_without_history_does_nothing(monkeypatch):
    monkeypatch.setattr(scheduler, "_last_ingest_date", lambda deps: None)
    assert scheduler.catch_up(make_deps(datetime(2026, 10, 21, 18, 0, tzinfo=IST))) == []


def test_scheduler_registers_four_weekday_jobs():
    sched = scheduler.build_scheduler(make_deps(datetime(2026, 10, 21, 9, 0, tzinfo=IST)))
    assert sorted(job.id for job in sched.get_jobs()) == sorted(name for name, _, _ in scheduler.SCHEDULE)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_scheduler.py -q`
Expected: `ImportError: cannot import name 'scheduler' from 'aitrader'`.

- [ ] **Step 3: Implement** `aitrader/scheduler.py`

```python
from __future__ import annotations

import logging
from datetime import date, time, timedelta
from typing import List, Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from aitrader.db import repo
from aitrader.jobs import JobDeps, RunResult, run_job

log = logging.getLogger(__name__)

TIMEZONE = "Asia/Kolkata"
SCHEDULE = (("sync_instruments", 8, 30), ("snapshot_options", 15, 35),
            ("ingest_daily_bars", 16, 30), ("run_quality_checks", 16, 45))
CATCH_UP_MAX_DAYS = 30
DAILY_DATA_FINAL_AT = time(16, 30)


def run_if_trading_day(name: str, deps: JobDeps) -> Optional[RunResult]:
    today = deps.now().date()
    if not deps.calendar.is_trading_day(today):
        log.info("skipping %s: %s is not a trading day", name, today)
        return None
    return run_job(name, deps, today)


def _last_ingest_date(deps: JobDeps) -> Optional[date]:
    with deps.engine.connect() as conn:
        return repo.last_run_date(conn, "ingest_daily_bars")


def catch_up(deps: JobDeps) -> List[date]:
    last = _last_ingest_date(deps)
    if last is None:
        return []  # nothing loaded yet: the user runs "Load history" (backfill_history) first
    now = deps.now()
    today = now.date()
    final_through = today if now.time() >= DAILY_DATA_FINAL_AT else today - timedelta(days=1)
    start = max(last + timedelta(days=1), today - timedelta(days=CATCH_UP_MAX_DAYS))
    days = deps.calendar.trading_days(start, final_through)
    for day in days:
        run_job("ingest_daily_bars", deps, day)
    return days


def build_scheduler(deps: JobDeps) -> BackgroundScheduler:
    sched = BackgroundScheduler(timezone=TIMEZONE)
    for name, hour, minute in SCHEDULE:
        sched.add_job(run_if_trading_day, CronTrigger(day_of_week="mon-fri", hour=hour, minute=minute,
                                                      timezone=TIMEZONE),
                      args=[name, deps], id=name, max_instances=1, coalesce=True, misfire_grace_time=3600)
    return sched
```

- [ ] **Step 4: Run the tests**

Run: `.venv/Scripts/python -m pytest tests/test_scheduler.py -q`
Expected: `5 passed`.

- [ ] **Step 5: Commit**

```bash
git add aitrader/scheduler.py tests/test_scheduler.py
git commit -m "Add weekday job scheduler (IST) and start-up catch-up"
```

---

### Task 14: Local HTTP API with the Kite login flow

**Files:**
- Create: `aitrader/api.py`, `tests/test_api.py`

**Interfaces:**
- Consumes: `JobDeps`, `JOB_NAMES`, `run_job` (Task 12); `SessionStore` (Task 8); `KiteGateway` and `KiteAuthError` (Task 7); the `repo` read functions (Task 9).
- Produces: `create_app(*, deps, sessions, gateway_factory, dashboard_url, clock=time.monotonic) -> FastAPI`, where `gateway_factory: Callable[[Optional[str]], KiteGateway] | None` takes an access token or `None`.

- [ ] **Step 1: Write the failing test** in `tests/test_api.py`

```python
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
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_api.py -q`
Expected: `ModuleNotFoundError: No module named 'aitrader.api'`.

- [ ] **Step 3: Implement** `aitrader/api.py`

```python
from __future__ import annotations

import secrets
import time
from typing import Callable, Dict, Optional

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from fastapi.responses import RedirectResponse

from aitrader.auth import SessionStore
from aitrader.db import repo
from aitrader.jobs import JOB_NAMES, JobDeps, run_job
from aitrader.kite.gateway import KiteAuthError, KiteGateway

STATE_TTL_SECONDS = 600
NOT_CONFIGURED = "Kite is not configured: set KITE_API_KEY, KITE_API_SECRET and TOKEN_ENCRYPTION_KEY in .env"


def create_app(*, deps: JobDeps, sessions: Optional[SessionStore],
               gateway_factory: Optional[Callable[[Optional[str]], KiteGateway]], dashboard_url: str,
               clock: Callable[[], float] = time.monotonic) -> FastAPI:
    app = FastAPI(title="aitrader local service", docs_url=None, redoc_url=None, openapi_url=None)
    pending_states: Dict[str, float] = {}

    def kite_status() -> dict:
        if sessions is None:
            return {"configured": False, "connected": False, "kite_user_id": None, "expires_at": None}
        return sessions.status()

    @app.get("/local/status")
    def status():
        with deps.engine.connect() as conn:
            return {"coverage": repo.coverage(conn), "last_runs": repo.last_run_per_job(conn),
                    "issue_counts": repo.issue_counts(conn), "kite": kite_status()}

    @app.get("/local/kite/status")
    def kite_status_endpoint():
        return kite_status()

    @app.get("/local/kite/login")
    def kite_login():
        if sessions is None or gateway_factory is None:
            raise HTTPException(503, NOT_CONFIGURED)
        now = clock()
        for stale in [s for s, expiry in pending_states.items() if expiry < now]:
            pending_states.pop(stale, None)
        state = secrets.token_urlsafe(24)
        pending_states[state] = now + STATE_TTL_SECONDS
        return RedirectResponse(gateway_factory(None).login_url(state), status_code=302)

    @app.get("/local/kite/callback")
    def kite_callback(request_token: str = "", status: str = "", state: str = ""):
        if sessions is None or gateway_factory is None:
            raise HTTPException(503, NOT_CONFIGURED)
        expiry = pending_states.pop(state, None) if state else None
        if expiry is None or expiry < clock():
            raise HTTPException(400, "Invalid or expired login state — start the login again")
        if status != "success" or not request_token:
            raise HTTPException(400, "Kite login was not completed")
        try:
            session = gateway_factory(None).exchange_request_token(request_token)
        except KiteAuthError:
            raise HTTPException(400, "Kite rejected the login — start the login again") from None
        sessions.save(session)
        return RedirectResponse(f"{dashboard_url}/?kite=connected", status_code=302)

    @app.post("/local/jobs/{job}/run", status_code=202)
    def run(job: str, background: BackgroundTasks):
        if job not in JOB_NAMES:
            raise HTTPException(404, f"Unknown job {job!r}")
        background.add_task(run_job, job, deps, deps.now().date())
        return {"accepted": job}

    @app.get("/local/runs")
    def runs(limit: int = Query(20, ge=1, le=200)):
        with deps.engine.connect() as conn:
            return repo.recent_runs(conn, limit)

    @app.get("/local/issues")
    def issues(severity: Optional[str] = Query(None, pattern="^(info|warn|error)$"),
               limit: int = Query(50, ge=1, le=500)):
        with deps.engine.connect() as conn:
            return repo.recent_issues(conn, severity, limit)

    return app
```

- [ ] **Step 4: Run the tests**

Run: `.venv/Scripts/python -m pytest tests/test_api.py -q`
Expected: `7 passed`.

- [ ] **Step 5: Commit**

```bash
git add aitrader/api.py tests/test_api.py
git commit -m "Add local API: status, Kite login with one-time state, job triggers, runs and issues"
```

---

### Task 15: CLI (`serve`, `migrate`, `seed-universe`, `run-job`, `live-check`) and README

**Files:**
- Create: `aitrader/__main__.py`, `tests/test_cli.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: everything above.
- Produces:
  - `aitrader.__main__.build_runtime(settings) -> (JobDeps, SessionStore | None, gateway_factory | None)`;
  - `aitrader.__main__.main(argv=None) -> int`.

- [ ] **Step 1: Write the failing test** in `tests/test_cli.py`

```python
import pytest

from aitrader import __main__ as cli


def test_serve_refuses_non_loopback_host(monkeypatch, capsys):
    monkeypatch.setenv("HOST", "0.0.0.0")
    monkeypatch.setattr(cli, "uvicorn", None)  # would crash if serve got as far as starting
    assert cli.main(["serve"]) == 2
    assert "local only" in capsys.readouterr().err


def test_unknown_job_is_rejected_by_parser():
    with pytest.raises(SystemExit):
        cli.main(["run-job", "place_order"])


def test_live_check_without_kite_config_explains(monkeypatch, capsys):
    for key in ("KITE_API_KEY", "KITE_API_SECRET", "TOKEN_ENCRYPTION_KEY"):
        monkeypatch.setenv(key, "")
    assert cli.main(["live-check"]) == 1
    assert "not configured" in capsys.readouterr().out
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_cli.py -q`
Expected: an `ImportError`, or an attribute error for `main`, because `aitrader/__main__.py` doesn't exist yet.

- [ ] **Step 3: Implement** `aitrader/__main__.py`

```python
from __future__ import annotations

import argparse
import sys
import threading
from datetime import date, datetime
from pathlib import Path

import uvicorn
from pydantic import ValidationError

from aitrader.auth import IST, SessionStore
from aitrader.calendar import TradingCalendar
from aitrader.config import Settings
from aitrader.db.engine import make_engine
from aitrader.jobs import JOB_NAMES, JobDeps, run_job
from aitrader.kite.real import RealKiteGateway
from aitrader.redaction import configure_logging
from aitrader.sources.yfinance_source import YFinanceSource

REPO_ROOT = Path(__file__).resolve().parents[1]


def build_runtime(settings: Settings):
    engine = make_engine(settings.database_url)
    sessions = (SessionStore(engine, settings.token_encryption_key.get_secret_value())
                if settings.kite_configured() else None)

    def gateway_factory(access_token):
        return RealKiteGateway(settings.kite_api_key, settings.kite_api_secret.get_secret_value(), access_token)

    def kite():
        active = sessions.active() if sessions else None
        return gateway_factory(active.access_token) if active else None

    deps = JobDeps(engine=engine, kite=kite, bar_source=YFinanceSource(), calendar=TradingCalendar.bundled(),
                   on_auth_error=sessions.revoke if sessions else (lambda: None))
    return deps, sessions, (gateway_factory if sessions else None)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m aitrader")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("serve", help="run the local API + scheduler on 127.0.0.1")
    sub.add_parser("migrate", help="apply database migrations")
    seed = sub.add_parser("seed-universe", help="load the Nifty 200 list from a CSV you downloaded")
    seed.add_argument("--csv", required=True, type=Path)
    job = sub.add_parser("run-job", help="run one job now")
    job.add_argument("name", choices=JOB_NAMES)
    job.add_argument("--date", type=date.fromisoformat, default=None)
    job.add_argument("--years", type=int, default=5, help="backfill_history only")
    sub.add_parser("live-check", help="read-only Kite round-trip once your plan is active")
    return parser


def _live_check(deps, sessions) -> int:
    if sessions is None:
        print("Kite is not configured: set KITE_API_KEY, KITE_API_SECRET and TOKEN_ENCRYPTION_KEY in .env")
        return 1
    kite = deps.kite()
    if kite is None:
        print("Not logged in: open the dashboard Data tab and click 'Log in to Kite' first.")
        return 1
    print(f"Profile OK: user {kite.profile().get('user_id')}")
    nse = kite.instruments("NSE")
    nifty = next((r for r in nse if r["tradingsymbol"] == "NIFTY 50"), None)
    print(f"NSE instruments: {len(nse)}")
    print(f"NIFTY 50 last price: {kite.quote(['NSE:NIFTY 50'])['NSE:NIFTY 50']['last_price']}")
    if nifty:
        today = datetime.now(IST).date()
        candles = kite.historical_daily(nifty["instrument_token"], date(today.year, 1, 1), today)
        print(f"NIFTY 50 daily candles this year: {len(candles)}; last close {candles[-1]['close'] if candles else '-'}")
        print("Compare a few closes with yfinance (^NSEI) to confirm Kite's price basis before trusting Kite bars.")
    return 0


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    try:
        settings = Settings()
    except ValidationError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    configure_logging([settings.kite_api_secret.get_secret_value(), settings.token_encryption_key.get_secret_value()])

    if args.command == "migrate":
        from alembic import command
        from alembic.config import Config

        cfg = Config(str(REPO_ROOT / "alembic.ini"))
        cfg.set_main_option("script_location", str(REPO_ROOT / "migrations"))
        command.upgrade(cfg, "head")
        return 0

    deps, sessions, gateway_factory = build_runtime(settings)

    if args.command == "seed-universe":
        from aitrader.universe import seed_universe

        counts = seed_universe(deps.engine, args.csv.read_text(encoding="utf-8"), as_of=datetime.now(IST).date())
        print(counts)
        return 0
    if args.command == "run-job":
        kwargs = {"years": args.years} if args.name == "backfill_history" else {}
        result = run_job(args.name, deps, args.date or datetime.now(IST).date(), **kwargs)
        print(f"{args.name}: {result.status} {result.counts} {result.message}")
        return 0 if result.status in ("ok", "partial", "skipped") else 1
    if args.command == "live-check":
        return _live_check(deps, sessions)

    # serve
    from aitrader.api import create_app
    from aitrader.scheduler import build_scheduler, catch_up

    app = create_app(deps=deps, sessions=sessions, gateway_factory=gateway_factory,
                     dashboard_url=settings.dashboard_url)
    sched = build_scheduler(deps)
    sched.start()
    threading.Thread(target=catch_up, args=(deps,), daemon=True, name="catch-up").start()
    try:
        uvicorn.run(app, host=settings.host, port=settings.port, log_config=None)
    finally:
        sched.shutdown(wait=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run the tests**

Run: `.venv/Scripts/python -m pytest tests/test_cli.py -q`
Expected: `3 passed`.

- [ ] **Step 5: Replace `README.md`**

````markdown
# ai-trader-core

The local market-data service for **AI Trader**: daily NSE stock and NIFTY/BANKNIFTY data, end-of-day index-option snapshots, a secure Zerodha Kite login, and data-quality checks.
**This codebase contains no order placement.**

- Design: `docs/specs/2026-10-20-data-foundation-design.md`
- Product brief: `D:\ai-trader\AI_Trader_README.md`

## One-time setup (Windows, Git Bash or PowerShell)

1. Install and start **Docker Desktop**. Then run `docker compose up -d`, which starts Postgres on 127.0.0.1:5433.
2. Create the Python environment:
   ```
   python -m venv .venv
   .venv/Scripts/python -m pip install -e ".[dev]"
   ```
3. Copy `.env.example` to `.env`. Generate a `TOKEN_ENCRYPTION_KEY` with the command shown in the file. Fill in `KITE_API_KEY` and `KITE_API_SECRET` once you have a Kite Connect app. **Never commit `.env`.**
4. In the Kite developer console, set the app's redirect URL to `http://127.0.0.1:8000/local/kite/callback`.
5. Create the tables: `.venv/Scripts/python -m aitrader migrate`
6. Download the **Nifty 200 constituents CSV** by hand from niftyindices.com (Indices → Broad Market → NIFTY 200 → "Download Index Constituents"). Then run:
   `.venv/Scripts/python -m aitrader seed-universe --csv path/to/ind_nifty200list.csv`
7. Load about 5 years of daily history (it takes a while): `.venv/Scripts/python -m aitrader run-job backfill_history`
8. Fill in `aitrader/data/nse_holidays_2026.yaml` from NSE's official holiday circular.

## Daily use

- Start the service: `.venv/Scripts/python -m aitrader serve`. It serves http://127.0.0.1:8000 and runs these jobs on weekdays (IST):

  | Time | Job |
  |---|---|
  | 08:30 | instrument sync |
  | 15:35 | option snapshot |
  | 16:30 | daily bars |
  | 16:45 | quality checks |

- Open the dashboard (`D:\ai-trader`, `npm run dev`) and go to the **Data** tab. Click **Log in to Kite** each trading morning, before 15:35, so the option snapshot can run.
- Once your Kite data plan is active, run: `.venv/Scripts/python -m aitrader live-check`

## Tests

`docker compose up -d` and then `.venv/Scripts/python -m pytest -q`. Database tests are skipped, with a reason, if Postgres isn't running. CI requires them.

## Known limitations

- **Survivorship bias.** Delisted stocks are missing from free data, and Nifty 200 membership before the first seed is an explicit assumption.
- **yfinance prices are split-adjusted** and meant for personal use. Kite becomes the main source later.
- **No historical option prices** until vendor data is bought. Snapshots only build up from the day Kite is active.
````

- [ ] **Step 6: Run the full suite and commit**

Run: `.venv/Scripts/python -m pytest -q`
Expected: every test passes, with no skips while Postgres is running.

```bash
git add aitrader/__main__.py tests/test_cli.py README.md
git commit -m "Add CLI (serve, migrate, seed-universe, run-job, live-check) and setup README"
```

---

### Task 16: CI

**Files:**
- Create: `.github/workflows/ci.yml`

**Interfaces:** none.

- [ ] **Step 1: Write the workflow**

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:

permissions:
  contents: read

jobs:
  test:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:16
        env:
          POSTGRES_USER: aitrader
          POSTGRES_PASSWORD: aitrader
          POSTGRES_DB: aitrader
        ports:
          - 5433:5432
        options: >-
          --health-cmd "pg_isready -U aitrader"
          --health-interval 5s
          --health-timeout 5s
          --health-retries 10
    env:
      AITRADER_REQUIRE_DB: "1"
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install -e ".[dev]"
      - run: ruff check .
      - run: python -m pytest -q
```

- [ ] **Step 2: Lint locally**

Run: `.venv/Scripts/ruff check .`
Expected: `All checks passed!` Fix anything it reports in the files you wrote, then re-run the full suite.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "Add CI: ruff + pytest against a Postgres service (DB tests required)"
```

---

### Task 17: "Data" tab in the existing dashboard (`D:\ai-trader`)

**Files (repo `D:\ai-trader`, new branch `data-tab`):**
- Create: `vercel-demo/src/components/DataTab.jsx`
- Modify: `vercel-demo/src/App.jsx`, `vercel-demo/vite.config.js`, `README.md`

**Interfaces:**
- Consumes the local API from Task 14: `GET /local/status`, `GET /local/runs`, `GET /local/issues`, `POST /local/jobs/{job}/run`, and the `/local/kite/login` link.

- [ ] **Step 1: Create the branch**

```bash
cd /d/ai-trader && git checkout -b data-tab
```

- [ ] **Step 2: Add the Vite proxy entry** in `vercel-demo/vite.config.js`. Replace the `proxy` line with:

```js
    proxy: {
      "/api": "http://127.0.0.1:3001",
      "/local": "http://127.0.0.1:8000",
    },
```

- [ ] **Step 3: Create** `vercel-demo/src/components/DataTab.jsx`

```jsx
import { useEffect, useState } from "react";

const JOBS = [
  { key: "backfill_history", label: "Load history (5y)" },
  { key: "ingest_daily_bars", label: "Load today's bars" },
  { key: "run_quality_checks", label: "Run quality checks" },
  { key: "sync_instruments", label: "Sync Kite instruments" },
  { key: "snapshot_options", label: "Snapshot options" },
];

async function getJson(path) {
  const res = await fetch(path);
  if (!res.ok) throw new Error(`${path} returned ${res.status}`);
  return res.json();
}

function KiteBox({ kite }) {
  if (!kite.configured) {
    return <p className="warning-text">Kite is not configured. Add your Kite keys to ai-trader-core's .env.</p>;
  }
  if (kite.connected) {
    return (
      <p>
        Kite: <strong>connected</strong> as {kite.kite_user_id} until{" "}
        {new Date(kite.expires_at).toLocaleString("en-IN")}
      </p>
    );
  }
  return (
    <p>
      Kite: <strong>not connected</strong> — <a href="/local/kite/login">Log in to Kite</a> (needed each trading
      day before 15:35 for the option snapshot)
    </p>
  );
}

export default function DataTab() {
  const [status, setStatus] = useState(null);
  const [runs, setRuns] = useState([]);
  const [issues, setIssues] = useState([]);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState("");

  const load = () =>
    Promise.all([getJson("/local/status"), getJson("/local/runs?limit=20"), getJson("/local/issues?limit=50")])
      .then(([s, r, i]) => {
        setStatus(s);
        setRuns(r);
        setIssues(i);
        setError(null);
      })
      .catch((e) => setError(e.message));

  useEffect(() => {
    load();
  }, []);

  const runJob = async (job) => {
    setBusy(job);
    try {
      const res = await fetch(`/local/jobs/${job}/run`, { method: "POST" });
      if (!res.ok) throw new Error(`${job} returned ${res.status}`);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy("");
      setTimeout(load, 2000);
    }
  };

  if (error && !status) return <p className="error-text">Local data service unavailable: {error}</p>;
  if (!status) return <p>Loading data status…</p>;

  const bars = status.coverage.bars;
  return (
    <div className="data-tab">
      {error && <p className="error-text">{error}</p>}
      <KiteBox kite={status.kite} />

      <h3>Coverage</h3>
      <ul>
        {Object.entries(bars).map(([kind, c]) => (
          <li key={kind}>
            {kind}: {c.instruments} instruments, {c.first_date} → {c.last_date}
          </li>
        ))}
        <li>option snapshots: {status.coverage.option_snapshots.rows} rows</li>
      </ul>

      <h3>Actions</h3>
      <div className="data-actions">
        {JOBS.map((j) => (
          <button key={j.key} type="button" disabled={busy !== ""} onClick={() => runJob(j.key)}>
            {busy === j.key ? "Starting…" : j.label}
          </button>
        ))}
      </div>

      <h3>Recent runs</h3>
      <table>
        <tbody>
          {runs.map((r) => (
            <tr key={r.id}>
              <td>{r.job}</td>
              <td>{r.run_date}</td>
              <td>{r.status}</td>
              <td>{JSON.stringify(r.counts)}</td>
              <td>{r.message}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <h3>Quality issues ({Object.entries(status.issue_counts).map(([k, v]) => `${k}: ${v}`).join(", ") || "none"})</h3>
      <table>
        <tbody>
          {issues.map((i) => (
            <tr key={i.id}>
              <td>{i.severity}</td>
              <td>{i.trade_date}</td>
              <td>{i.symbol || i.tradingsymbol || "—"}</td>
              <td>{i.check_name}</td>
              <td>{i.detail}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
```

- [ ] **Step 4: Wire the tab into** `vercel-demo/src/App.jsx`. Make these edits:
  - Add `import DataTab from "./components/DataTab.jsx";` next to the other component imports.
  - Inside `App`, after the existing `useState` lines, add:

```jsx
  const [localService, setLocalService] = useState(false);

  useEffect(() => {
    fetch("/local/status")
      .then((res) => setLocalService(res.ok && (res.headers.get("content-type") || "").includes("json")))
      .catch(() => setLocalService(false));
  }, []);

  const tabs = localService ? [...TABS, { key: "data", label: "Data" }] : TABS;
```

  - In the `<nav className="tabs">` block, change `{TABS.map((tab) => (` to `{tabs.map((tab) => (`.
  - Change the opening of the tab-content conditional from `{activeTab === "holdings" ? (` to:

```jsx
      {activeTab === "data" ? (
        <DataTab />
      ) : activeTab === "holdings" ? (
```

- [ ] **Step 5: Verify the build and run a manual end-to-end check**

Run: `cd vercel-demo && npm run build`
Expected: `✓ built`.

Manual check:
1. Start `python -m aitrader serve` in `D:\ai-trader-core`.
2. Run `npm run dev` in `vercel-demo`.
3. Open http://localhost:5173. A **Data** tab appears.
4. Click "Run quality checks". A new run row appears within a few seconds.
5. Stop the aitrader service and reload. The Data tab disappears.

- [ ] **Step 6: Document it in `D:\ai-trader\README.md`.** Under "Run the dashboard locally", add:

```markdown
**Data tab:** if the AI Trader core service (`D:\ai-trader-core`, `python -m aitrader serve`) is running on
127.0.0.1:8000, a **Data** tab appears (Kite login, data coverage, job buttons, quality issues). It never appears on the
deployed Vercel site.
```

- [ ] **Step 7: Commit**

```bash
git add vercel-demo/src/components/DataTab.jsx vercel-demo/src/App.jsx vercel-demo/vite.config.js README.md
git commit -m "Add Data tab for the local AI Trader core service (hidden unless the service is running)"
```

---

## Self-review notes (completed while writing)

- **Spec coverage:**

  | Spec section | Where it's covered |
  |---|---|
  | §4 units | Tasks 3–14 |
  | §5 tables | Task 2, all 10 tables, plus `price_basis` |
  | §6 jobs, catch-up and errors | Tasks 12–13 |
  | §7 checks | Tasks 4 and 12 |
  | §8 security | Tasks 1, 6, 7, 8 and 14 (loopback, `.env`, encrypted token, state, redaction, no order methods) |
  | §9 API and Data tab | Tasks 14 and 17 |
  | §10 limitations | Task 15 README |
  | §11 testing | Every task, with CI in Task 16 and live-check in Task 15 |

  `trading_calendar` table population is deferred, as noted in Global Constraints.
- **Placeholders:** none. The two generated artifacts (the Alembic revision file and `requirements.lock`) come from named commands with stated expected output.
- **Type consistency:**
  - Every job takes `(deps, run_date, run_id, **kwargs)`, and `run_job` passes `run_id`.
  - `repo.upsert_daily_bars` returns `(inserted, updated)`, which Task 12 consumes.
  - `FakeKiteGateway` and `RealKiteGateway` share `ALLOWED_METHODS`.
  - `Issue.check_name` matches the DB column `check_name`.
