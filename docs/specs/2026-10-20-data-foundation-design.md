# AI Trader Core: Data Foundation (Part 1) Design

| | |
|---|---|
| Date | 2026-10-20 |
| Status | Draft for user review |
| Repo | `D:\ai-trader-core` (Python package `aitrader`) |
| Product brief | `D:\ai-trader\AI_Trader_README.md` (§11 Phase 1, §12 decisions) |
| Discovery report | `D:\ai-trader\docs\AI_TRADER_PROJECT_DISCOVERY.md` |
| Scope | Part 1 of 4: **data foundation**. Parts 2–4 (backtest + strategies, teaching layer, real-time paper signals + notifications) each get their own spec. |

> No live order placement exists anywhere in this part. Nothing here claims or implies that any strategy is profitable.

---

## 1. Purpose

The user is a beginner who already invests in stocks. They want AI Trader to do the following, in this order:

1. backtest clearly defined **swing-trading** strategies on historical data;
2. then run them on **real-time** data as paper signals;
3. **teach** each strategy and signal in plain language;
4. give signals with entry, stop-loss and target.

Part 1 builds the data every later part depends on: trustworthy, source-tagged, quality-checked daily history, the Zerodha Kite connection, and an end-of-day option record.

**Success means:**
- Five years of daily history for the stock universe, NIFTY 50 and NIFTY BANK can be loaded with one action.
- Daily updates run on their own, and re-running any load produces identical data.
- Gaps, bad candles, unrecorded splits and missed option snapshots are detected and shown, never silently ignored.
- Kite secrets and tokens never reach the browser, the logs, git, or any API response.
- Everything is testable offline before the user's Kite subscription is active.

## 2. Decisions this design rests on

All of these are the user's choices (2026-10-14 to 2026-10-20) unless marked as a design choice.

| Topic | Decision |
|---|---|
| Trading style | Swing trading: hold for days to weeks, on **daily bars**. Signals are produced after the close, for the next session. |
| Instruments | **NSE stocks** (the Nifty 200 universe), plus **NIFTY 50 / NIFTY BANK swing signals traded as index options** (buy CE when bullish, buy PE when bearish). |
| Historical source | **yfinance now, Kite later.** Every bar records its source. Kite becomes the preferred source once the user's ₹500/month data plan is active. |
| Historical options | Licensed **vendor** history, to be purchased later. The vendor isn't chosen yet and is out of scope here. Meanwhile we **record our own end-of-day option snapshots** from Kite. |
| Repo | A separate repository, `D:\ai-trader-core`. The existing `D:\ai-trader` dashboard gains only a "Data" tab (§9). |
| Runtime | A local FastAPI service on `127.0.0.1`, with an in-process scheduler. |
| Database | Postgres 16 in Docker (docker-compose), with Alembic migrations. |
| Kite client | The official `kiteconnect` Python library, behind our own `KiteGateway` interface (design choice). |

## 3. Market facts this design depends on

These were verified on 2026-10-20 from the sources noted. They must be re-checked if behaviour looks wrong.

- **Expiries:** since 20 Nov 2024, NSE offers weekly expiry only on **NIFTY 50**. **NIFTY BANK is monthly-only.** NSE index expiries moved to **Tuesday** from 1 Sep 2025.
  - Sources: Zerodha, https://zerodha.com/z-connect/updates/sebis-new-rules-on-weekly-index-derivatives-expiry and https://support.zerodha.com/category/trading-and-markets/trading-faqs/f-otrading/articles/weekly-expiries-discontinued-for-certain-index-derivatives
  - **Design rule:** expiry dates always come from Kite's instrument list. They are never computed from weekdays.
- **Kite constraints:**
  - The access token expires at 06:00 the next day, and the docs describe no automated login.
  - Historical API: 3 req/s. Quote API: 1 req/s, up to 500 instruments per call. Other endpoints: 10 req/s.
  - The instrument list is regenerated daily, and **expired contracts disappear from it**. Tokens can be reused, so the key is exchange plus tradingsymbol.
  - Historical data for expired options is not available.
  - Source: https://kite.trade/docs/connect/v3/ (user, exceptions, market-quotes and historical pages).
- **yfinance:** "intended for personal use only" and for research and education. It is used only for this user's personal research. Source: https://github.com/ranaroussi/yfinance

## 4. Architecture

```
React dashboard (D:\ai-trader, `npm run dev`) ── "Data" tab
        │  /local/*   (Vite dev proxy → 127.0.0.1:8000)
        ▼
aitrader service (FastAPI, bound to 127.0.0.1 only)
  ├── api/          JSON endpoints for the Data tab (§9)
  ├── auth/         Kite login + encrypted session store
  ├── kite/         KiteGateway (interface) ─┬─ RealKiteGateway (kiteconnect)
  │                                         └─ FakeKiteGateway (recorded fixtures)
  │                 RateLimiter (per endpoint class) + retry/backoff
  ├── sources/      YFinanceSource (daily bars + splits/bonuses) behind a DailyBarSource interface
  ├── universe/     instruments, Nifty 200 membership, option-contract selection
  ├── ingest/       jobs: instrument sync, option snapshot, daily bars, history backfill
  ├── quality/      checks → data_quality_issues
  ├── calendar/     NSE trading days (versioned holiday file + observed data)
  ├── scheduler/    APScheduler jobs + start-up catch-up
  └── db/           SQLAlchemy Core tables, Alembic migrations, repositories
        ▼
Postgres 16 (Docker, docker-compose.yml, named volume)
```

**Unit boundaries (each one testable on its own):**

- **`KiteGateway` interface.** Methods:
  - `login_url()`
  - `exchange_request_token(request_token) -> KiteSession`
  - `instruments(exchange) -> list[InstrumentRow]`
  - `quote(keys) -> dict`
  - `historical_daily(token, from, to) -> list[Bar]`
  - `profile()`

  It has **no order methods**, and a test enforces that.
- **`DailyBarSource` interface.** Implementations are `YFinanceSource` and `KiteDailySource` (enabled once the plan is active). Methods: `daily_bars(symbol, from, to) -> list[Bar]` and `corporate_actions(symbol) -> list[Action]`.
- **Jobs.** Each job is a function `(deps, run_date) -> RunResult`. It is pure orchestration: it calls gateways and repositories, then records an `ingestion_runs` row.
- **Quality checks.** Pure functions `(bars, calendar, actions) -> list[Issue]`. They need no database.

## 5. Data model (Postgres)

**Conventions:**
- All prices are `NUMERIC(14,4)`, stored **exactly as the source provides them**. Each bar records its basis in `price_basis`:
  - `yfinance` is **split-adjusted**. This was verified on 2026-10-20: with `auto_adjust=False`, AAPL's close before its 4:1 split of 2020-08-31 is 124.81, not about 499.
  - `kite`'s basis is confirmed by the live check (§11) before Kite bars are trusted alongside yfinance.
  - `corporate_actions` still records splits and bonuses, so the two bases can be reconciled.
- Dates are `DATE` in exchange-local terms; timestamps are `TIMESTAMPTZ`.
- Every row created by ingestion carries `source` and `ingested_at`.

| Table | Columns (key ones) | Constraints / notes |
|---|---|---|
| `instruments` | `id`, `kind` (`stock`\|`index`), `exchange`, `symbol`, `name`, `yf_symbol`, `kite_token` (nullable), `active` | Unique (`exchange`, `symbol`). Indices: NIFTY 50 (`^NSEI`), NIFTY BANK (`^NSEBANK`). |
| `daily_bars` | `instrument_id`, `trade_date`, `open`, `high`, `low`, `close`, `volume`, `source` (`yfinance`\|`kite`\|`vendor`), `price_basis` (`split_adjusted`\|`raw`), `ingested_at` | PK (`instrument_id`, `trade_date`, `source`). An upsert replaces the same key, so re-runs are idempotent. Because yfinance re-bases its history after a new split, re-ingesting updates older rows; the job's counts show how many rows changed. |
| `corporate_actions` | `instrument_id`, `ex_date`, `kind` (`split`\|`bonus`), `ratio_from`, `ratio_to`, `source` | Unique (`instrument_id`, `ex_date`, `kind`, `source`). Used to compute adjusted prices on read. |
| `index_membership` | `index_name`, `instrument_id`, `valid_from`, `valid_to` (null = current), `is_backfilled_assumption` | Starts from the current Nifty 200 list. Earlier periods are explicitly flagged as assumptions (§10). |
| `trading_calendar` | `trade_date`, `is_trading_day`, `note`, `source` (`holiday_file`\|`observed`) | Separates "closed" from "missing". |
| `option_contracts` | `id`, `underlying` (`NIFTY`\|`BANKNIFTY`), `expiry`, `strike`, `option_type` (`CE`\|`PE`), `lot_size`, `tradingsymbol`, `kite_token`, `first_seen`, `last_seen` | Unique (`tradingsymbol`, `expiry`). **Never deleted**, because Kite drops expired contracts. |
| `option_snapshots` | `contract_id`, `snapshot_at`, `last_price`, `bid`, `ask`, `volume`, `oi`, `underlying_price`, `source` | PK (`contract_id`, `snapshot_at`). End-of-day only in Part 1. |
| `kite_session` | `id`, `kite_user_id`, `access_token_encrypted` (bytea), `created_at`, `expires_at`, `revoked_at` | Fernet encryption with the key from `.env` `TOKEN_ENCRYPTION_KEY`. At most one active row. |
| `ingestion_runs` | `id`, `job`, `run_date`, `started_at`, `finished_at`, `status` (`ok`\|`partial`\|`skipped`\|`failed`), `source`, `counts` (JSONB), `message` | Every job execution writes exactly one row. |
| `data_quality_issues` | `id`, `run_id`, `instrument_id` / `contract_id` (nullable), `trade_date`, `check`, `severity` (`info`\|`warn`\|`error`), `detail` | Shown in the Data tab. |

**Expected size:**
- Daily bars: about 200 stocks plus 2 indices × about 250 days × 5 years ≈ 250k rows per source.
- Option snapshots: about 2 underlyings × 2 expiries × about 42 contracts ≈ 170 rows per day.

## 6. Jobs and daily flow (IST, trading days)

| Time | Job | Needs Kite | Behaviour |
|---|---|---|---|
| 08:30 | `sync_instruments` | yes | Pulls NSE equity rows for tracked stocks and NFO rows for NIFTY/BANKNIFTY options. Upserts `option_contracts` and fills `instruments.kite_token`. |
| 15:35 | `snapshot_options` | yes | Takes the underlying's last price, selects ATM ±10 strike steps for the **2 nearest expiries** (from `option_contracts`), and calls `quote` in batches of ≤500 at ≤1 req/s. Writes `option_snapshots`. |
| 16:30 | `ingest_daily_bars` | no | For every active instrument, fetches today's bar plus corporate actions from the active `DailyBarSource`(s). Per-instrument failures make the run `partial`, not `failed`. |
| 16:45 | `run_quality_checks` | no | Runs the checks in §7 over newly loaded data. |
| on demand | `backfill_history(years=5)` | no | One-time load for the whole universe, resumable: it skips (instrument, date) pairs that already exist. |

**ATM strike selection** (pure function, unit-tested):
- The strike step is derived from the contracts present for that expiry (the most common gap between adjacent strikes), never hard-coded.
- ATM is the strike nearest the underlying's price. Ties round down.

**If Kite is not logged in:** Kite jobs record `status=skipped` with message "Kite not logged in". Missed `snapshot_options` runs also create a `missed_snapshot` quality issue, because past snapshots can't be fetched later.

**Start-up catch-up:** when the service starts, it runs `ingest_daily_bars` for every trading day since the last `ok` or `partial` run, up to 30 days back. Older gaps go through `backfill_history`.

**Kite errors:**
- On HTTP 429 or a network error: exponential backoff, at most 5 attempts.
- On a token or permission error: mark `kite_session.revoked_at`, stop the job with `status=failed` and message "Kite session expired — log in again", and do not retry.

## 7. Data-quality checks

| Check | Rule | Severity |
|---|---|---|
| `missing_bar` | Trading day per the calendar, but no bar for an active instrument | warn |
| `ohlc_invalid` | `low > min(open, close)`, `high < max(open, close)`, any price ≤ 0, or volume < 0 | error |
| `unexplained_jump` | Close-to-open or close-to-close move above 20% with no corporate action within 1 day | warn |
| `stale_repeat` | Identical OHLCV for 3 or more consecutive trading days | warn |
| `source_mismatch` | Kite and yfinance bars for the same day differ by more than 0.5% on close (once both exist) | info |
| `missed_snapshot` | `snapshot_options` didn't produce rows on a trading day | warn |
| `unknown_closed_day` | No bars at all for any instrument on a weekday missing from the holiday file. Recorded as `observed` closed and flagged for review. | info |

**Calendar:** `calendar/nse_holidays_<year>.yaml` is a versioned file updated once a year by the user, from NSE's published holiday list. Weekends are always closed.

## 8. Security

- The service binds to `127.0.0.1` and **refuses to start** if configured otherwise.
- **Secrets** come only from `.env`, which is git-ignored, with a committed `.env.example` alongside it:
  - `KITE_API_KEY`
  - `KITE_API_SECRET`
  - `TOKEN_ENCRYPTION_KEY`
  - `DATABASE_URL`
- **Login flow:** `/local/kite/login` redirects to Kite, and `/local/kite/callback` exchanges the request token server-side.
  - The checksum is computed server-side.
  - The token is stored Fernet-encrypted.
  - The callback validates a one-time `state` value to prevent login CSRF.
- **No endpoint ever returns** the API secret, the access token, or the encryption key. `GET /local/kite/status` returns only `{connected, kite_user_id, expires_at}`.
- **Logging:** a filter redacts the values of the secret env vars and any `access_token`/`api_secret` fields.
- **No order code:** neither `KiteGateway` nor `RealKiteGateway` exposes order placement, and a test enforces this.

## 9. Local API and the "Data" tab

**Endpoints** (all JSON, all under `/local`):

| Endpoint | Purpose |
|---|---|
| `GET /local/status` | Coverage (instrument count, earliest and latest date per kind), last run per job, and issue counts by severity |
| `GET /local/kite/status` | Connection status only (see §8) |
| `GET /local/kite/login` | Redirects to the Kite login page |
| `GET /local/kite/callback` | Completes the login, then redirects back to the dashboard |
| `POST /local/jobs/{job}/run` | Runs a job now (`sync_instruments`, `snapshot_options`, `ingest_daily_bars`, `run_quality_checks`, `backfill_history`) |
| `GET /local/runs?limit=` | Recent runs |
| `GET /local/issues?severity=&limit=` | Recent quality issues |

**In `D:\ai-trader`:**
- A new "Data" tab component.
- A Vite dev proxy entry, `/local` → `http://127.0.0.1:8000`.
- The tab appears **only** when `/local/status` responds, so on the deployed Vercel site it stays hidden.

## 10. Known limitations (stated in every later backtest report)

1. **Survivorship bias.** yfinance and Kite list only currently traded stocks, so delisted or merged companies are missing. `index_membership` before Part 1's first run is a flagged assumption, built from the current list.
2. **yfinance quality.** It's unofficial and can have gaps or late corrections. That's why bars are source-tagged, carry an explicit `price_basis` (yfinance is split-adjusted, not raw), are stored alongside explicit corporate actions, and are re-checked against Kite later (`source_mismatch`, once both sources are on the same basis).
3. **No historical option prices** until the vendor data is bought. Option snapshots only start once the Kite plan is active and the user logs in each trading day.
4. **End-of-day snapshots only.** Intraday option data is out of scope for swing trading on daily bars.

## 11. Testing

- **Offline by default:**
  - `FakeKiteGateway` serves recorded fixtures for instruments, quote, historical, session exchange, 429 responses and token errors.
  - A fake `DailyBarSource` serves sample data containing a split, a missing day, a high<low bar and a stale run.
- **Database tests** use a real Postgres from docker-compose, with a per-run test database created and dropped, and Alembic migrations applied.
- **Pure-logic unit tests** cover the quality checks, ATM/strike-step selection, adjustment math, calendar, rate limiter and redaction filter.
- **Required tests:**
  - loading the same day twice leaves identical rows;
  - a missed snapshot creates an issue;
  - jobs skip when not logged in;
  - a session expiring mid-job fails cleanly;
  - no endpoint exposes the token;
  - binding to a non-loopback address refuses to start;
  - the gateway has no order methods.
- **One manual live check** (`python -m aitrader live-check`), run by the user once Kite is active. It does a read-only profile, instruments and quote round-trip.
- **CI:** GitHub Actions with a Postgres service container, `pytest`, and `ruff`.

## 12. Out of scope for Part 1

Out of scope here:
- strategies and indicators;
- the backtest engine;
- Claude/LLM calls;
- notifications;
- paper-trade simulation;
- intraday data;
- the vendor option import;
- historical index-membership reconstruction;
- order placement of any kind;
- any change to the deployed Vercel API.

## 13. Open items for later parts (not blocking Part 1)

- Paper-trading capital, and confirming whether the 2026-10-14 risk limits still apply to stock and index-option swings: ₹1,000 per trade, ₹3,000 per day, 3 open positions, 1:2 minimum risk/reward.
- The evaluation period and the accept/reject criteria for strategies (Part 2).
- Vendor selection for historical NIFTY/BANKNIFTY option prices (before option backtests in Part 2).
- Notification channel (Part 4).
