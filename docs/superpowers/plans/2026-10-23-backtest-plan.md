# Part 2 — Swing Backtest Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Backtest three swing strategies (momentum, trend pullback, breakout) on stored daily bars under the user's ₹20,000 money rules, store every run, and show results trade by trade in a new dashboard "Backtest" tab.

**Architecture:** A new `aitrader.backtest` package: pure indicator functions → strategies that turn one day's cross-section of prepared bars into `Candidate`s → a day-by-day portfolio engine (fills at next open, Zerodha costs, stops/targets, money rules) → metrics → a runner that persists results to four new Postgres tables. The FastAPI service exposes run/list/detail/bars endpoints; the React dashboard renders them with `lightweight-charts`.

**Tech Stack:** Python 3.12, pandas 3 / numpy (already installed via yfinance; made explicit), SQLAlchemy 2 Core + psycopg3, Alembic, FastAPI; React 18 + Vite, `lightweight-charts@5.2.1`.

**Spec:** `docs/specs/2026-10-22-backtest-design.md`

## Global Constraints

- Defaults: capital ₹20,000; risk per trade ₹400; daily loss stop ₹1,000; max 3 open positions; max ₹7,000 per position; reward 2 × risk; time stop 20 trading days.
- Signals use bars up to and including day *t*; entries fill at day *t+1*'s open. No decision may depend on later bars.
- Eligibility: ≥ 200 bars up to *t* and 20-day average traded value (close × volume) ≥ ₹10 crore (1e8).
- Stocks long only. Index signals (NIFTY 50, NIFTY BANK) for strategies B and C only, both directions, scored in R, no costs, labelled "Index signal quality in R — not option profit."
- Costs (defaults, stored per run): brokerage ₹0; STT 0.1% buy and sell; exchange 0.00297%; SEBI ₹10/crore; stamp 0.015% on buy; GST 18% of (brokerage + exchange + SEBI); DP ₹15.93 per sell; slippage 0.05% per side.
- Unseen period = last 2 years of the run range; trades belong to the period of their entry date.
- Never claim or imply profit. Checklist copy must say "not a promise of profit".
- No order placement anywhere. Service stays loopback-only.
- Bar source is `yfinance` (`price_basis` split-adjusted); benchmark is the `NIFTY 50` instrument.
- JSON written to JSONB must contain no NaN/inf and no numpy scalar types.
- Ruff rules unchanged (`E4,E7,E9,F`, line length 110). Python via `.venv/Scripts/python`.

## Review Focus

1. A run whose range is shorter than 2 years (earlier period empty) — summary must still build with `earlier: null`, and the checklist must fail "positive expectancy" cleanly rather than crash. (Task 7 test `test_summarize_without_earlier_period`.)
2. Summaries with zero trades or zero losses (no profit factor, no expectancy) — must persist to JSONB without NaN/inf. (Task 7 test `test_summary_with_no_trades_is_strict_json`.)
3. Index bars from Yahoo carry volume 0 — index breakout signals must still fire without a volume condition, and indices must never enter the stock portfolio. (Task 6 test `test_index_breakout_ignores_zero_volume`.)
4. A stock whose single share costs more than ₹7,000 — skipped as `size_zero`, never a crash or a fractional share. (Task 5 test `test_share_above_position_cap_is_size_zero`.)
5. Only the index has been loaded (stock history still missing) — run fails with the "Load the stock list and price history first (Data tab)" message. (Task 9 test `test_run_without_stocks_fails_with_guidance`.)

---

## File Structure

`D:\ai-trader-core`:

| File | Responsibility |
|---|---|
| `aitrader/backtest/__init__.py` | Package marker (empty) |
| `aitrader/backtest/types.py` | `Candidate`, `MoneyRules`, `Trade`, `EquityPoint`, `IndexSignal`, `LONG`/`SHORT` |
| `aitrader/backtest/indicators.py` | SMA, Wilder smoothing, ATR, RSI, prior rolling high/low, `add_indicators` |
| `aitrader/backtest/bars.py` | `frame_from_bars`, `Market`, `load_market` |
| `aitrader/backtest/strategies/__init__.py` | `STRATEGIES` registry |
| `aitrader/backtest/strategies/pullback.py` | Strategy B |
| `aitrader/backtest/strategies/breakout.py` | Strategy C |
| `aitrader/backtest/strategies/momentum.py` | Strategy A |
| `aitrader/backtest/costs.py` | `CostModel` |
| `aitrader/backtest/rules.py` | `planned_target`, `gap_skip`, `exit_check`, `size_position` |
| `aitrader/backtest/engine.py` | `simulate`, `SimResult` |
| `aitrader/backtest/index_signals.py` | `index_signals`, `index_summary` |
| `aitrader/backtest/metrics.py` | drawdown, CAGR, stats, period split, checklist, `summarize`, `benchmark_curve`, `clean_json` |
| `aitrader/backtest/runner.py` | `BacktestRequest`, `create_run`, `execute_run`, errors |
| `aitrader/db/schema.py` (modify) | Four new tables |
| `migrations/versions/c4d8e2f1a9b3_backtest_tables.py` | Migration |
| `aitrader/db/repo.py` (modify) | Backtest SQL |
| `aitrader/api.py` (modify) | Endpoints + interrupted-run recovery |
| `aitrader/jobs.py`, `aitrader/universe.py`, `aitrader/__main__.py` (modify) | 10-year history default |
| `tests/bt_helpers.py` | Synthetic frame builders |
| `tests/test_bt_*.py` | Tests per unit |

`D:\ai-trader\vercel-demo`:

| File | Responsibility |
|---|---|
| `src/components/BacktestTab.jsx` | Run panel, runs list, run view, trades |
| `src/components/backtest/EquityChart.jsx` | Equity vs NIFTY chart |
| `src/components/backtest/TradeChart.jsx` | Candles + entry/stop/target/exit |
| `src/components/backtest/format.js` | Number/date formatters |
| `src/App.jsx` (modify) | Add the tab |
| `src/components/DataTab.jsx` (modify) | Label "Load history (10y)" |

---

### Task 1: Ten-year history default (Part 1 change, spec §13)

**Files:**
- Modify: `aitrader/jobs.py` (`backfill_history` signature), `aitrader/universe.py` (`seed_universe` signature), `aitrader/__main__.py` (`--years` default), `README.md` (step 7)
- Modify: `D:\ai-trader\vercel-demo\src\components\DataTab.jsx` (JOBS label)
- Test: `tests/test_jobs.py`, `tests/test_universe.py`

**Interfaces:**
- Produces: `backfill_history(deps, run_date, run_id, years: int = 10)`, `seed_universe(engine, csv_text, *, as_of, history_years: int = 10)`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_jobs.py`:

```python
def test_backfill_defaults_to_ten_years(engine):
    seed(engine)
    d = deps(engine)
    run_job("backfill_history", d, RUN_DAY)
    assert d.bar_source.calls
    assert all(start == date(2016, 10, 21) for _, start, _ in d.bar_source.calls)
```

In `tests/test_universe.py`, change the assumed-membership assertion in `test_seed_creates_indices_stocks_and_flagged_memberships` from `date(2021, 10, 21)` to `date(2016, 10, 21)`.

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_jobs.py::test_backfill_defaults_to_ten_years tests/test_universe.py -q`
Expected: 2 FAIL (starts are 2021-10-21).

- [ ] **Step 3: Implement**

- `aitrader/jobs.py`: `def backfill_history(deps: JobDeps, run_date: date, run_id: int, years: int = 10) -> RunResult:`
- `aitrader/universe.py`: `def seed_universe(engine: Engine, csv_text: str, *, as_of: date, history_years: int = 10) -> dict:`
- `aitrader/__main__.py`: `job.add_argument("--years", type=int, default=10, help="backfill_history only")`
- `README.md` step 7: "Load about 10 years of daily history (it takes a while): …"
- `DataTab.jsx`: `{ key: "backfill_history", label: "Load history (10y)" },`

- [ ] **Step 4: Run the full suite**

Run: `.venv/Scripts/python -m pytest -q`
Expected: all pass (91).

- [ ] **Step 5: Commit** (both repos)

```bash
git -C D:/ai-trader-core add -A && git -C D:/ai-trader-core commit -m "Default history load to 10 years for backtests"
git -C D:/ai-trader add vercel-demo/src/components/DataTab.jsx && git -C D:/ai-trader commit -m "Data tab: history load is now 10 years"
```

---

### Task 2: Types, indicators, bar frames

**Files:**
- Modify: `pyproject.toml` (add `"pandas>=2.2"`, `"numpy>=1.26"` to dependencies)
- Create: `aitrader/backtest/__init__.py` (empty), `aitrader/backtest/types.py`, `aitrader/backtest/indicators.py`, `aitrader/backtest/bars.py`, `tests/bt_helpers.py`
- Test: `tests/test_bt_indicators.py`

**Interfaces:**
- Produces:
  - `types.LONG = "long"`, `types.SHORT = "short"`
  - `Candidate(symbol, direction, signal_date: date, signal_close: float, stop: float, score: float, reason: str, facts: dict)`
  - `MoneyRules(capital=20000.0, risk_per_trade=400.0, daily_loss_limit=1000.0, max_positions=3, max_position_value=7000.0, reward_multiple=2.0, time_stop_days=20)`
  - `Trade(...)`, `EquityPoint(day, cash, invested_value, equity)`, `IndexSignal(...)` — fields below
  - `indicators.MIN_BARS = 200`, `MIN_TRADED_VALUE = 1e8`, `sma(s, n)`, `wilder(s, n)`, `true_range(df)`, `atr(df, n=14)`, `rsi(close, n=14)`, `prior_max(s, n)`, `prior_min(s, n)`, `add_indicators(df) -> DataFrame` adding columns `bars, sma20, sma50, sma200, atr14, rsi14, ret1, ret3, ret5, ret21, vol5, vol50, vol20_prior, high20_prior, low20_prior, low3, high3, value20`
  - `bars.frame_from_bars(bars: Sequence[Bar]) -> DataFrame` (index `pd.Timestamp`, columns `open, high, low, close, volume`), `Market(stocks, indices, instrument_ids)`, `load_market(engine, source) -> Market`
  - `tests/bt_helpers.frame(rows, start="2024-01-01")`, `flat(n, price=100.0, volume=2_000_000)`

- [ ] **Step 1: Write `tests/bt_helpers.py` and the failing tests**

```python
# tests/bt_helpers.py
from __future__ import annotations

import pandas as pd


def frame(rows, start="2024-01-01"):
    """rows: (open, high, low, close, volume) tuples on consecutive business days."""
    idx = pd.bdate_range(start, periods=len(rows))
    return pd.DataFrame(rows, index=idx, columns=["open", "high", "low", "close", "volume"], dtype=float)


def flat(n, price=100.0, volume=2_000_000):
    return [(price, price + 1, price - 1, price, volume)] * n
```

```python
# tests/test_bt_indicators.py
import math

import pandas as pd
import pytest

from aitrader.backtest.bars import frame_from_bars
from aitrader.backtest.indicators import add_indicators, atr, prior_max, prior_min, rsi, sma, wilder
from aitrader.models import Bar
from bt_helpers import flat, frame


def s(values):
    return pd.Series(values, dtype=float)


def test_sma_needs_full_window():
    out = sma(s([1, 2, 3, 4, 5]), 3)
    assert math.isnan(out[1]) and list(out[2:]) == [2, 3, 4]


def test_wilder_seeds_with_mean_then_smooths():
    out = wilder(s([1, 2, 3, 4, 5]), 3)
    assert math.isnan(out[1])
    assert out[2] == pytest.approx(2) and out[3] == pytest.approx(8 / 3) and out[4] == pytest.approx(31 / 9)


def test_atr_uses_true_range():
    df = frame([(9, 10, 8, 9, 1), (10, 11, 9, 10, 1), (11, 12, 10, 11, 1), (8, 11, 7, 8, 1)])
    out = atr(df, 3)
    assert out.iloc[2] == pytest.approx(2) and out.iloc[3] == pytest.approx(8 / 3)


def test_rsi_wilder():
    out = rsi(s([1, 2, 3, 2, 3]), 2)
    assert out[2] == pytest.approx(100) and out[3] == pytest.approx(50) and out[4] == pytest.approx(75)


def test_prior_window_excludes_today():
    assert list(prior_max(s([1, 5, 3, 4]), 2)[2:]) == [5, 5]
    assert list(prior_min(s([4, 1, 3, 2]), 2)[2:]) == [1, 1]


def test_add_indicators_columns_and_counts():
    df = add_indicators(frame(flat(210)))
    row = df.iloc[-1]
    assert row["bars"] == 210 and row["sma200"] == pytest.approx(100)
    assert row["atr14"] == pytest.approx(2) and row["high20_prior"] == 101 and row["low20_prior"] == 99
    assert row["value20"] == pytest.approx(2e8)


def test_frame_from_bars():
    from datetime import date
    df = frame_from_bars([Bar(date(2024, 1, 2), 1, 2, 0.5, 1.5, 10)])
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert df.index[0] == pd.Timestamp("2024-01-02") and df.iloc[0]["close"] == 1.5
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_bt_indicators.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'aitrader.backtest'`.

- [ ] **Step 3: Implement**

```python
# aitrader/backtest/types.py
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

LONG, SHORT = "long", "short"


@dataclass(frozen=True)
class Candidate:
    symbol: str
    direction: str
    signal_date: date
    signal_close: float
    stop: float
    score: float
    reason: str
    facts: dict = field(default_factory=dict, compare=False, hash=False)


@dataclass(frozen=True)
class MoneyRules:
    capital: float = 20_000.0
    risk_per_trade: float = 400.0
    daily_loss_limit: float = 1_000.0
    max_positions: int = 3
    max_position_value: float = 7_000.0
    reward_multiple: float = 2.0
    time_stop_days: int = 20


@dataclass
class Trade:
    symbol: str
    signal_date: date
    entry_date: date
    entry_price: float
    shares: int
    stop: float
    target: float
    exit_date: date
    exit_price: float
    exit_reason: str
    charges: float
    pnl: float
    r_multiple: float
    reason: str
    facts: dict


@dataclass
class EquityPoint:
    day: date
    cash: float
    invested_value: float
    equity: float


@dataclass
class IndexSignal:
    symbol: str
    direction: str
    signal_date: date
    entry_date: date
    entry: float
    stop: float
    target: float
    exit_date: date
    exit: float
    exit_reason: str
    r_multiple: float
    reason: str
```

```python
# aitrader/backtest/indicators.py
"""Causal price indicators: every value at row t uses rows <= t only."""
from __future__ import annotations

import numpy as np
import pandas as pd

MIN_BARS = 200
MIN_TRADED_VALUE = 1e8  # ₹10 crore average daily traded value


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).mean()


def wilder(values: pd.Series, n: int) -> pd.Series:
    """Wilder smoothing: seeded with the mean of the first n valid values, then (prev*(n-1) + x) / n."""
    v = values.to_numpy(dtype=float)
    out = np.full(len(v), np.nan)
    run, start = 0, None
    for i, x in enumerate(v):
        run = run + 1 if not np.isnan(x) else 0
        if run == n:
            start = i
            break
    if start is not None:
        out[start] = v[start - n + 1:start + 1].mean()
        for i in range(start + 1, len(v)):
            out[i] = out[i - 1] if np.isnan(v[i]) else (out[i - 1] * (n - 1) + v[i]) / n
    return pd.Series(out, index=values.index)


def true_range(df: pd.DataFrame) -> pd.Series:
    prev = df["close"].shift(1)
    return pd.concat([df["high"] - df["low"], (df["high"] - prev).abs(), (df["low"] - prev).abs()],
                     axis=1).max(axis=1)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    return wilder(true_range(df), n)


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    delta = close.diff()
    avg_gain = wilder(delta.clip(lower=0), n)
    avg_loss = wilder((-delta).clip(lower=0), n)
    with np.errstate(divide="ignore", invalid="ignore"):
        out = 100 - 100 / (1 + avg_gain / avg_loss)
    return out.mask((avg_loss == 0) & avg_gain.notna(), 100.0)


def prior_max(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).max().shift(1)


def prior_min(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).min().shift(1)


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    close, volume = out["close"], out["volume"].astype(float)
    out["bars"] = np.arange(1, len(out) + 1)
    for n in (20, 50, 200):
        out[f"sma{n}"] = sma(close, n)
    out["atr14"] = atr(out, 14)
    out["rsi14"] = rsi(close, 14)
    for n in (1, 3, 5, 21):
        out[f"ret{n}"] = close / close.shift(n) - 1
    out["vol5"] = sma(volume, 5)
    out["vol50"] = sma(volume, 50)
    out["vol20_prior"] = sma(volume, 20).shift(1)
    out["high20_prior"] = prior_max(out["high"], 20)
    out["low20_prior"] = prior_min(out["low"], 20)
    out["low3"] = out["low"].rolling(3, min_periods=3).min()
    out["high3"] = out["high"].rolling(3, min_periods=3).max()
    out["value20"] = sma(close * volume, 20)
    return out
```

```python
# aitrader/backtest/bars.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Sequence

import pandas as pd
from sqlalchemy.engine import Engine

from aitrader.db import repo
from aitrader.models import Bar

COLUMNS = ["open", "high", "low", "close", "volume"]


def frame_from_bars(bars: Sequence[Bar]) -> pd.DataFrame:
    df = pd.DataFrame([(pd.Timestamp(b.trade_date), b.open, b.high, b.low, b.close, b.volume) for b in bars],
                      columns=["date", *COLUMNS]).set_index("date")
    return df.astype(float)


@dataclass
class Market:
    stocks: Dict[str, pd.DataFrame] = field(default_factory=dict)
    indices: Dict[str, pd.DataFrame] = field(default_factory=dict)
    instrument_ids: Dict[str, int] = field(default_factory=dict)


def load_market(engine: Engine, source: str) -> Market:
    market = Market()
    with engine.connect() as conn:
        for inst in repo.active_instruments(conn):
            bars = repo.load_bars(conn, inst["id"], source=source)
            if not bars:
                continue
            target = market.indices if inst["kind"] == "index" else market.stocks
            target[inst["symbol"]] = frame_from_bars(bars)
            market.instrument_ids[inst["symbol"]] = inst["id"]
    return market
```

- [ ] **Step 4: Run tests**

Run: `.venv/Scripts/python -m pytest tests/test_bt_indicators.py -q`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml aitrader/backtest tests/bt_helpers.py tests/test_bt_indicators.py
git commit -m "Backtest: types, causal indicators, bar frames"
```

---

### Task 3: Strategies A, B, C

**Files:**
- Create: `aitrader/backtest/strategies/__init__.py`, `pullback.py`, `breakout.py`, `momentum.py`
- Test: `tests/test_bt_strategies.py`

**Interfaces:**
- Consumes: `Candidate`, `LONG`, `SHORT` (Task 2); prepared columns from `add_indicators`.
- Produces: classes with `key: str`, `name: str`, `PARAMS: dict`, `candidates(day: pd.Timestamp, rows: pd.DataFrame) -> List[Candidate]` (rows indexed by symbol, already eligibility-filtered; empty frame → `[]`). `Pullback` and `Breakout` also expose `signals(day, rows, direction) -> List[Candidate]`. `Breakout(use_volume: bool = True)`. `STRATEGIES = {"momentum": Momentum, "pullback": Pullback, "breakout": Breakout}`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_bt_strategies.py
import pandas as pd
import pytest

from aitrader.backtest.strategies import STRATEGIES
from aitrader.backtest.strategies.breakout import Breakout
from aitrader.backtest.strategies.momentum import Momentum
from aitrader.backtest.strategies.pullback import Pullback
from aitrader.backtest.types import LONG, SHORT

DAY = pd.Timestamp("2025-03-03")


def rows(**by_symbol):
    return pd.DataFrame.from_dict(by_symbol, orient="index")


def pull_row(**over):
    base = dict(open=99.5, high=101.5, low=99.8, close=101.0, volume=1e6, sma20=100.0, sma50=95.0, sma200=90.0,
                low3=98.0, high3=103.0)
    return {**base, **over}


def test_pullback_long_triggers_with_reason_and_stop():
    out = Pullback().candidates(DAY, rows(AAA=pull_row()))
    assert len(out) == 1
    c = out[0]
    assert (c.symbol, c.direction, c.stop, c.signal_close) == ("AAA", LONG, 98.0, 101.0)
    assert c.score == pytest.approx(5 / 90) and "20-day" in c.reason and c.signal_date == DAY.date()


@pytest.mark.parametrize("over", [
    dict(sma50=89.0),            # no uptrend (sma50 < sma200)
    dict(low=101.2),             # never dipped to the 20-day average
    dict(close=99.9, open=99.0), # closed below the 20-day average
    dict(open=101.2),            # red candle
    dict(low3=100.5),            # stop closer than 1%
    dict(low3=92.0),             # stop further than 8%
])
def test_pullback_near_misses(over):
    assert Pullback().candidates(DAY, rows(AAA=pull_row(**over))) == []


def test_pullback_short_mirror():
    r = rows(IDX=dict(open=100.5, high=99.5, low=98.0, close=98.5, volume=0, sma20=100.0, sma50=105.0,
                      sma200=110.0, low3=97.0, high3=101.0))
    r.loc["IDX", "high"] = 99.2  # touched within 1% of the 20-day average
    out = Pullback().signals(DAY, r, SHORT)
    assert len(out) == 1 and out[0].direction == SHORT and out[0].stop == 101.0


def brk_row(**over):
    base = dict(open=100.5, high=103.5, low=100.0, close=103.0, volume=6e6, vol20_prior=2e6, sma50=100.0,
                high20_prior=101.0, low20_prior=99.0, atr14=2.0)
    return {**base, **over}


def test_breakout_long_triggers():
    out = Breakout().candidates(DAY, rows(AAA=brk_row()))
    assert len(out) == 1 and out[0].stop == pytest.approx(99.0) and out[0].score == pytest.approx(3.0)


@pytest.mark.parametrize("over", [
    dict(close=100.9),     # not above the 20-day high
    dict(volume=2.9e6),    # volume below 1.5x
    dict(sma50=104.0),     # below the 50-day average
    dict(atr14=12.0),      # stop more than 8% away
])
def test_breakout_near_misses(over):
    assert Breakout().candidates(DAY, rows(AAA=brk_row(**over))) == []


def test_breakout_without_volume_and_short():
    r = rows(IDX=brk_row(volume=0, vol20_prior=0, close=97.0, sma50=100.0))
    out = Breakout(use_volume=False).signals(DAY, r, SHORT)
    assert len(out) == 1 and out[0].stop == pytest.approx(101.0)


def mom_row(r21, r5, r3, r1, vol5, rsi, close=110.0, sma50=100.0, atr=2.0):
    return dict(ret21=r21, ret5=r5, ret3=r3, ret1=r1, vol5=vol5, vol50=1.0, rsi14=rsi, close=close, sma50=sma50,
                atr14=atr, open=close, high=close, low=close, volume=1.0)


def test_momentum_ranks_and_stops():
    r = rows(AAA=mom_row(0.3, 0.1, 0.05, 0.02, 3.0, 60),
             BBB=mom_row(0.2, 0.05, 0.03, 0.01, 2.0, 75),
             CCC=mom_row(0.1, 0.01, 0.01, 0.0, 1.0, 40))
    out = Momentum().candidates(DAY, r)
    assert [c.symbol for c in out] == ["AAA", "BBB", "CCC"]
    assert out[0].score == pytest.approx(100.0)
    assert out[1].score == pytest.approx(6000 / 95)
    assert out[0].stop == pytest.approx(106.0) and "Ranked #1 of 3" in out[0].reason


def test_momentum_requires_close_above_sma50_and_complete_data():
    r = rows(AAA=mom_row(0.3, 0.1, 0.05, 0.02, 3.0, 60, close=90.0),
             BBB=mom_row(float("nan"), 0.1, 0.05, 0.02, 3.0, 60))
    assert Momentum().candidates(DAY, r) == []


def test_empty_rows_and_registry():
    empty = pd.DataFrame()
    assert all(cls().candidates(DAY, empty) == [] for cls in STRATEGIES.values())
    assert set(STRATEGIES) == {"momentum", "pullback", "breakout"}
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_bt_strategies.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'aitrader.backtest.strategies'`.

- [ ] **Step 3: Implement**

```python
# aitrader/backtest/strategies/pullback.py
from __future__ import annotations

from typing import List

import pandas as pd

from aitrader.backtest.types import LONG, Candidate


class Pullback:
    key = "pullback"
    name = "Trend pullback"
    PARAMS = {"near_sma20": 0.01, "min_stop": 0.01, "max_stop": 0.08, "stop": "lowest low of last 3 bars"}

    def candidates(self, day: pd.Timestamp, rows: pd.DataFrame) -> List[Candidate]:
        return self.signals(day, rows, LONG)

    def signals(self, day: pd.Timestamp, rows: pd.DataFrame, direction: str) -> List[Candidate]:
        if rows.empty:
            return []
        c, o, s20, s50, s200 = rows["close"], rows["open"], rows["sma20"], rows["sma50"], rows["sma200"]
        if direction == LONG:
            mask = (c > s50) & (s50 > s200) & (rows["low"] <= s20 * 1.01) & (c > s20) & (c > o)
            stop, dist, score = rows["low3"], (c - rows["low3"]) / c, (s50 - s200) / s200
        else:
            mask = (c < s50) & (s50 < s200) & (rows["high"] >= s20 * 0.99) & (c < s20) & (c < o)
            stop, dist, score = rows["high3"], (rows["high3"] - c) / c, (s200 - s50) / s200
        mask &= (dist >= self.PARAMS["min_stop"]) & (dist <= self.PARAMS["max_stop"])
        out = []
        for symbol in sorted(rows.index[mask]):
            r = rows.loc[symbol]
            trend = "Uptrend" if direction == LONG else "Downtrend"
            side = "dipped to" if direction == LONG else "rallied to"
            reason = (f"{trend}: close {r['close']:.2f}, 50-day avg {r['sma50']:.2f}, 200-day avg {r['sma200']:.2f}; "
                      f"price {side} the 20-day avg {r['sma20']:.2f} and turned. Stop at {stop[symbol]:.2f} "
                      f"(3-day {'low' if direction == LONG else 'high'}).")
            facts = {k: round(float(r[k]), 4) for k in ("close", "sma20", "sma50", "sma200")}
            out.append(Candidate(str(symbol), direction, day.date(), float(r["close"]), float(stop[symbol]),
                                 float(score[symbol]), reason, facts))
        return out
```

```python
# aitrader/backtest/strategies/breakout.py
from __future__ import annotations

from typing import List

import numpy as np
import pandas as pd

from aitrader.backtest.types import LONG, Candidate


class Breakout:
    key = "breakout"
    name = "Breakout"
    PARAMS = {"lookback": 20, "volume_surge": 1.5, "atr_mult": 1.0, "max_stop": 0.08}

    def __init__(self, use_volume: bool = True):
        self.use_volume = use_volume

    def candidates(self, day: pd.Timestamp, rows: pd.DataFrame) -> List[Candidate]:
        return self.signals(day, rows, LONG)

    def signals(self, day: pd.Timestamp, rows: pd.DataFrame, direction: str) -> List[Candidate]:
        if rows.empty:
            return []
        c, atr = rows["close"], rows["atr14"]
        with np.errstate(divide="ignore", invalid="ignore"):
            surge = rows["volume"] / rows["vol20_prior"].where(rows["vol20_prior"] > 0)
        if direction == LONG:
            level = rows["high20_prior"]
            stop = level - self.PARAMS["atr_mult"] * atr
            mask = (c > level) & (c > rows["sma50"]) & (stop < c) & ((c - stop) / c <= self.PARAMS["max_stop"])
        else:
            level = rows["low20_prior"]
            stop = level + self.PARAMS["atr_mult"] * atr
            mask = (c < level) & (c < rows["sma50"]) & (stop > c) & ((stop - c) / c <= self.PARAMS["max_stop"])
        if self.use_volume:
            mask &= surge >= self.PARAMS["volume_surge"]
            score = surge
        else:
            score = (c - level).abs() / atr
        out = []
        for symbol in sorted(rows.index[mask]):
            r = rows.loc[symbol]
            word = "above its 20-day high" if direction == LONG else "below its 20-day low"
            vol = f" on {surge[symbol]:.1f}× normal volume" if self.use_volume else ""
            reason = (f"Closed at {r['close']:.2f}, {word} {level[symbol]:.2f}{vol}. "
                      f"Stop {stop[symbol]:.2f} (1×ATR beyond the breakout level).")
            facts = {"close": round(float(r["close"]), 4), "level": round(float(level[symbol]), 4),
                     "atr14": round(float(atr[symbol]), 4)}
            if self.use_volume:
                facts["volume_surge"] = round(float(surge[symbol]), 4)
            out.append(Candidate(str(symbol), direction, day.date(), float(r["close"]), float(stop[symbol]),
                                 float(score[symbol]), reason, facts))
        return out
```

```python
# aitrader/backtest/strategies/momentum.py
from __future__ import annotations

from typing import List

import numpy as np
import pandas as pd

from aitrader.backtest.types import LONG, Candidate

WEIGHTS = {"ret21": 30, "ret5": 25, "ret3": 15, "ret1": 10, "vol_surge": 10}
RSI_WEIGHT, TOTAL_WEIGHT = 5, 95  # the doc's delivery-% weight (5) has no data source yet


class Momentum:
    key = "momentum"
    name = "Momentum"
    PARAMS = {"weights": WEIGHTS, "rsi_weight": RSI_WEIGHT, "rsi_band": [55, 70], "top_n": 10, "atr_mult": 2.0}

    def candidates(self, day: pd.Timestamp, rows: pd.DataFrame) -> List[Candidate]:
        if rows.empty:
            return []
        with np.errstate(divide="ignore", invalid="ignore"):
            surge = rows["vol5"] / rows["vol50"].where(rows["vol50"] > 0)
        df = rows[["ret21", "ret5", "ret3", "ret1", "rsi14", "atr14", "sma50", "close"]].assign(vol_surge=surge)
        df = df.replace([np.inf, -np.inf], np.nan).dropna().sort_index()
        if df.empty:
            return []
        score = sum(w * df[k].rank(pct=True) * 100 for k, w in WEIGHTS.items())
        lo, hi = self.PARAMS["rsi_band"]
        score = score + RSI_WEIGHT * df["rsi14"].between(lo, hi).astype(float) * 100
        df = df.assign(score=score / TOTAL_WEIGHT).sort_values("score", ascending=False, kind="mergesort")
        out = []
        for pos, (symbol, r) in enumerate(df.head(self.PARAMS["top_n"]).iterrows(), start=1):
            stop = r["close"] - self.PARAMS["atr_mult"] * r["atr14"]
            if not (r["close"] > r["sma50"]) or not (0 < stop < r["close"]):
                continue
            reason = (f"Ranked #{pos} of {len(df)} on momentum (21-day {r['ret21']:+.1%}, 5-day {r['ret5']:+.1%}, "
                      f"volume {r['vol_surge']:.1f}× normal, RSI {r['rsi14']:.0f}); above its 50-day average. "
                      f"Stop 2×ATR below at {stop:.2f}.")
            facts = {k: round(float(r[k]), 4) for k in ("ret21", "ret5", "ret3", "ret1", "vol_surge", "rsi14", "score")}
            facts["rank"] = pos
            out.append(Candidate(str(symbol), LONG, day.date(), float(r["close"]), float(stop), float(r["score"]),
                                 reason, facts))
        return out
```

```python
# aitrader/backtest/strategies/__init__.py
from aitrader.backtest.strategies.breakout import Breakout
from aitrader.backtest.strategies.momentum import Momentum
from aitrader.backtest.strategies.pullback import Pullback

STRATEGIES = {"momentum": Momentum, "pullback": Pullback, "breakout": Breakout}
INDEX_STRATEGIES = {"pullback", "breakout"}
```

- [ ] **Step 4: Run tests**

Run: `.venv/Scripts/python -m pytest tests/test_bt_strategies.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add aitrader/backtest/strategies tests/test_bt_strategies.py
git commit -m "Backtest: momentum, pullback and breakout strategies"
```

---

### Task 4: Costs and trade rules

**Files:**
- Create: `aitrader/backtest/costs.py`, `aitrader/backtest/rules.py`
- Test: `tests/test_bt_rules.py`

**Interfaces:**
- Produces:
  - `CostModel(brokerage=0.0, stt_rate=0.001, exchange_rate=0.0000297, sebi_rate=1e-6, stamp_rate=0.00015, gst_rate=0.18, dp_charge=15.93, slippage=0.0005)` with `buy_charges(value) -> float`, `sell_charges(value) -> float`
  - `planned_target(ref, stop, multiple) -> float` (works for both directions)
  - `gap_skip(direction, open_, stop, target) -> Optional[str]` → `"gap_through_stop"` / `"gap_past_target"` / None
  - `exit_check(direction, bar: Tuple[o,h,l,c], stop, target, *, entry_day: bool, days_held: int, time_stop: int) -> Optional[Tuple[float, str]]` with reasons `stop_gap, target_gap, stop_same_day, stop, target, time`
  - `size_position(fill, stop, money: MoneyRules) -> int`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_bt_rules.py
import pytest

from aitrader.backtest.costs import CostModel
from aitrader.backtest.rules import exit_check, gap_skip, planned_target, size_position
from aitrader.backtest.types import LONG, SHORT, MoneyRules


def test_zerodha_delivery_charges_worked_example():
    costs = CostModel()
    assert costs.buy_charges(7000) == pytest.approx(7 + 0.2079 + 0.007 + 1.05 + 0.18 * (0.2079 + 0.007))
    assert costs.sell_charges(7700) == pytest.approx(7.7 + 0.22869 + 0.0077 + 0.18 * (0.22869 + 0.0077) + 15.93)


def test_planned_target_both_directions():
    assert planned_target(100, 95, 2) == 110
    assert planned_target(100, 105, 2) == 90


@pytest.mark.parametrize("direction,open_,expected", [
    (LONG, 94, "gap_through_stop"), (LONG, 111, "gap_past_target"), (LONG, 100, None),
    (SHORT, 106, "gap_through_stop"), (SHORT, 89, "gap_past_target"), (SHORT, 100, None),
])
def test_gap_skip(direction, open_, expected):
    stop, target = (95, 110) if direction == LONG else (105, 90)
    assert gap_skip(direction, open_, stop, target) == expected


def check(bar, entry_day=False, days_held=1, direction=LONG, stop=95, target=110):
    return exit_check(direction, bar, stop, target, entry_day=entry_day, days_held=days_held, time_stop=20)


def test_exit_rules_long():
    assert check((90, 92, 88, 91)) == (90, "stop_gap")
    assert check((112, 113, 111, 112)) == (112, "target_gap")
    assert check((100, 111, 94, 100)) == (95, "stop_same_day")
    assert check((100, 101, 94, 96)) == (95, "stop")
    assert check((100, 111, 99, 108)) == (110, "target")
    assert check((100, 101, 99, 100), days_held=20) == (100, "time")
    assert check((100, 101, 99, 100)) is None


def test_entry_day_ignores_gap_rules():
    assert check((90, 92, 88, 91), entry_day=True, days_held=0) == (95, "stop")


def test_exit_rules_short():
    assert check((106, 107, 104, 106), direction=SHORT, stop=105, target=90) == (106, "stop_gap")
    assert check((100, 106, 89, 100), direction=SHORT, stop=105, target=90) == (105, "stop_same_day")
    assert check((100, 101, 89, 95), direction=SHORT, stop=105, target=90) == (90, "target")


def test_size_position_caps():
    money = MoneyRules()
    assert size_position(100.05, 95, money) == 69   # ₹7,000 cap beats 79 by risk
    assert size_position(100.05, 90, money) == 39   # risk cap: 400 / 10.05
    assert size_position(8000, 7900, money) == 0    # one share above the position cap
    assert size_position(100, 100, money) == 0      # no risk distance
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_bt_rules.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'aitrader.backtest.costs'`.

- [ ] **Step 3: Implement**

```python
# aitrader/backtest/costs.py
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CostModel:
    """Zerodha equity-delivery charges (defaults; re-check against zerodha.com/charges) plus slippage."""
    brokerage: float = 0.0
    stt_rate: float = 0.001
    exchange_rate: float = 0.0000297
    sebi_rate: float = 10 / 1e7
    stamp_rate: float = 0.00015
    gst_rate: float = 0.18
    dp_charge: float = 15.93
    slippage: float = 0.0005

    def _common(self, value: float) -> float:
        exchange, sebi = value * self.exchange_rate, value * self.sebi_rate
        return self.brokerage + value * self.stt_rate + exchange + sebi + self.gst_rate * (
            self.brokerage + exchange + sebi)

    def buy_charges(self, value: float) -> float:
        return self._common(value) + value * self.stamp_rate

    def sell_charges(self, value: float) -> float:
        return self._common(value) + self.dp_charge
```

```python
# aitrader/backtest/rules.py
from __future__ import annotations

import math
from typing import Optional, Tuple

from aitrader.backtest.types import LONG, MoneyRules

Bar4 = Tuple[float, float, float, float]


def planned_target(ref: float, stop: float, multiple: float) -> float:
    return ref + multiple * (ref - stop)


def gap_skip(direction: str, open_: float, stop: float, target: float) -> Optional[str]:
    if direction == LONG:
        if open_ <= stop:
            return "gap_through_stop"
        if open_ >= target:
            return "gap_past_target"
    else:
        if open_ >= stop:
            return "gap_through_stop"
        if open_ <= target:
            return "gap_past_target"
    return None


def exit_check(direction: str, bar: Bar4, stop: float, target: float, *, entry_day: bool, days_held: int,
               time_stop: int) -> Optional[Tuple[float, str]]:
    o, h, low, c = bar
    if direction == LONG:
        if not entry_day:
            if o <= stop:
                return o, "stop_gap"
            if o >= target:
                return o, "target_gap"
        hit_stop, hit_target = low <= stop, h >= target
    else:
        if not entry_day:
            if o >= stop:
                return o, "stop_gap"
            if o <= target:
                return o, "target_gap"
        hit_stop, hit_target = h >= stop, low <= target
    if hit_stop and hit_target:
        return stop, "stop_same_day"  # daily bars cannot tell which came first: assume the loss
    if hit_stop:
        return stop, "stop"
    if hit_target:
        return target, "target"
    if days_held >= time_stop:
        return c, "time"
    return None


def size_position(fill: float, stop: float, money: MoneyRules) -> int:
    risk_per_share = fill - stop
    if risk_per_share <= 0:
        return 0
    return max(0, min(math.floor(money.risk_per_trade / risk_per_share),
                      math.floor(money.max_position_value / fill)))
```

- [ ] **Step 4: Run tests**

Run: `.venv/Scripts/python -m pytest tests/test_bt_rules.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add aitrader/backtest/costs.py aitrader/backtest/rules.py tests/test_bt_rules.py
git commit -m "Backtest: Zerodha cost model and entry/exit rules"
```

---

### Task 5: Portfolio engine

**Files:**
- Create: `aitrader/backtest/engine.py`
- Test: `tests/test_bt_engine.py`

**Interfaces:**
- Consumes: `add_indicators`, `MIN_BARS`, `MIN_TRADED_VALUE` (Task 2); strategy objects with `candidates(day, rows)` (Task 3); `CostModel`, `planned_target`, `gap_skip`, `exit_check`, `size_position` (Task 4).
- Produces: `simulate(frames: Dict[str, DataFrame], days: Sequence[pd.Timestamp], strategy, money: MoneyRules, costs: CostModel) -> SimResult`; `SimResult(trades: List[Trade], equity: List[EquityPoint], skips: Dict[str, int], notes: Dict[str, int])`. Frames must already have `add_indicators` columns. Skip reasons: `daily_loss_pause, already_held, no_slot, missing_bar, gap_through_stop, gap_past_target, size_zero, no_cash`. Note key: `missing_bar_days`. Exit reasons add `end_of_test`. `Trade.exit_price` is the fill after slippage; `Trade.r_multiple = pnl ÷ (shares × (entry − stop))`.

Day loop order (spec §4): (1) entries at today's open from yesterday's candidates, best score first, using start-of-day cash/slots; (2) exit checks for every open position (entry-day rules for positions opened today; last day forces `end_of_test`); (3) daily-loss pause flag for tomorrow; (4) equity point; (5) candidates at today's close from eligible rows (not on the last day).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_bt_engine.py
import numpy as np
import pytest

from aitrader.backtest.costs import CostModel
from aitrader.backtest.engine import simulate
from aitrader.backtest.indicators import add_indicators
from aitrader.backtest.strategies.momentum import Momentum
from aitrader.backtest.types import LONG, Candidate, MoneyRules
from bt_helpers import frame

FLAT = (100, 101, 99, 100, 1e6)
COSTS = CostModel()


class Scripted:
    key = name = "scripted"

    def __init__(self, plan):
        self.plan = plan

    def candidates(self, day, rows):
        return list(self.plan.get(day, []))


def prep(rows):
    return add_indicators(frame(rows))


def run(frames, plan_by_index, money=MoneyRules(), spine=None):
    days = list(frames[spine or next(iter(frames))].index)
    plan = {days[i]: [Candidate(sym, LONG, days[i].date(), close, stop, score, f"test {sym}", {})
                      for sym, close, stop, score in cands]
            for i, cands in plan_by_index.items()}
    return simulate(frames, days, Scripted(plan), money, COSTS), days


def expected_pnl(shares, entry, exit_raw):
    buy = shares * entry
    sell = shares * exit_raw * (1 - COSTS.slippage)
    return sell - COSTS.sell_charges(sell) - buy - COSTS.buy_charges(buy)


def test_target_hit_next_day_with_costs_and_equity():
    res, days = run({"AAA": prep([FLAT, (100, 102, 99, 101, 1e6), (101, 111, 100, 110, 1e6), FLAT])},
                    {0: [("AAA", 100, 95, 1.0)]})
    (t,) = res.trades
    assert (t.entry_date, t.exit_date, t.shares, t.exit_reason) == (days[1].date(), days[2].date(), 69, "target")
    assert t.entry_price == pytest.approx(100.05) and t.target == pytest.approx(110.15)
    assert t.exit_price == pytest.approx(110.15 * 0.9995)
    assert t.pnl == pytest.approx(expected_pnl(69, 100.05, 110.15))
    assert t.r_multiple == pytest.approx(t.pnl / (69 * 5.05))
    assert len(res.equity) == 4 and res.equity[-1].equity == pytest.approx(20_000 + t.pnl)


def test_stop_and_target_same_day_counts_as_loss():
    res, _ = run({"AAA": prep([FLAT, FLAT, (101, 111, 94, 100, 1e6), FLAT])}, {0: [("AAA", 100, 95, 1.0)]})
    assert res.trades[0].exit_reason == "stop_same_day" and res.trades[0].exit_price == pytest.approx(95 * 0.9995)


def test_gap_below_stop_after_entry_exits_at_open():
    res, _ = run({"AAA": prep([FLAT, FLAT, (90, 91, 89, 90, 1e6), FLAT])}, {0: [("AAA", 100, 95, 1.0)]})
    assert res.trades[0].exit_reason == "stop_gap" and res.trades[0].exit_price == pytest.approx(90 * 0.9995)


@pytest.mark.parametrize("open_,reason", [(94, "gap_through_stop"), (111, "gap_past_target")])
def test_entry_gaps_are_skipped(open_, reason):
    res, _ = run({"AAA": prep([FLAT, (open_, open_ + 1, open_ - 1, open_, 1e6), FLAT])}, {0: [("AAA", 100, 95, 1.0)]})
    assert res.trades == [] and res.skips == {reason: 1}


def test_risk_cap_sizes_down():
    res, _ = run({"AAA": prep([FLAT] * 4)}, {0: [("AAA", 100, 90, 1.0)]})
    assert res.trades[0].shares == 39


def test_share_above_position_cap_is_size_zero():
    res, _ = run({"MRF": prep([(8000, 8010, 7990, 8000, 1e6)] * 3)}, {0: [("MRF", 8000, 7900, 1.0)]})
    assert res.trades == [] and res.skips == {"size_zero": 1}


def test_not_enough_cash():
    res, _ = run({"AAA": prep([FLAT] * 3)}, {0: [("AAA", 100, 95, 1.0)]}, money=MoneyRules(capital=50))
    assert res.trades == [] and res.skips == {"no_cash": 1}


def test_slots_go_to_the_best_score():
    res, _ = run({"AAA": prep([FLAT] * 3), "BBB": prep([FLAT] * 3)},
                 {0: [("BBB", 100, 95, 1.0), ("AAA", 100, 95, 2.0)]}, money=MoneyRules(max_positions=1))
    assert [t.symbol for t in res.trades] == ["AAA"] and res.skips == {"no_slot": 1}


def test_already_held_is_skipped():
    res, _ = run({"AAA": prep([FLAT] * 4)}, {0: [("AAA", 100, 95, 1.0)], 1: [("AAA", 100, 95, 1.0)]})
    assert len(res.trades) == 1 and res.skips == {"already_held": 1}


def test_daily_loss_pause_blocks_next_day_entries():
    res, _ = run({"AAA": prep([FLAT, (100, 101, 94, 95, 1e6), FLAT, FLAT]), "BBB": prep([FLAT] * 4)},
                 {0: [("AAA", 100, 95, 1.0)], 1: [("BBB", 100, 95, 1.0)]},
                 money=MoneyRules(daily_loss_limit=300))
    assert [t.exit_reason for t in res.trades] == ["stop"] and res.skips == {"daily_loss_pause": 1}


def test_time_stop_after_twenty_days():
    res, days = run({"AAA": prep([FLAT] * 25)}, {0: [("AAA", 100, 95, 1.0)]})
    assert res.trades[0].exit_reason == "time" and res.trades[0].exit_date == days[21].date()


def test_end_of_test_closes_open_positions():
    res, days = run({"AAA": prep([FLAT] * 5)}, {0: [("AAA", 100, 95, 1.0)]})
    assert res.trades[0].exit_reason == "end_of_test" and res.trades[0].exit_date == days[4].date()


def test_missing_bar_waits_for_next_bar():
    full = frame([FLAT] * 6)
    frames = {"SPN": add_indicators(full), "AAA": add_indicators(full.drop(full.index[2]))}
    res, days = run(frames, {0: [("AAA", 100, 95, 1.0)]}, spine="SPN")
    assert res.notes == {"missing_bar_days": 1}
    assert res.trades[0].exit_reason == "end_of_test" and res.trades[0].exit_date == days[5].date()


def walk(rng, n=320):
    closes = 1000 * np.cumprod(1 + rng.normal(0.001, 0.02, n))
    return [(c * 0.998, c * 1.01, c * 0.99, c, 1_000_000) for c in closes]


def test_no_lookahead_changing_future_bars_changes_no_earlier_decision():
    rng = np.random.default_rng(7)
    raw = {s: frame(walk(rng)) for s in ("AAA", "BBB", "CCC")}
    days = list(raw["AAA"].index)
    cut = days[280]

    def sim(frames):
        return simulate({s: add_indicators(f) for s, f in frames.items()}, days, Momentum(), MoneyRules(), COSTS)

    changed = {}
    for s, f in raw.items():
        g = f.copy()
        g.loc[g.index > cut, ["open", "high", "low", "close"]] *= 1.5
        changed[s] = g
    base, other = sim(raw), sim(changed)

    def entries(res):
        return sorted((t.symbol, t.signal_date, t.entry_date, t.entry_price, t.shares, t.stop)
                      for t in res.trades if t.entry_date <= cut.date())

    assert entries(base) and entries(base) == entries(other)
    upto = [p for p in base.equity if p.day <= cut.date()]
    assert upto == other.equity[:len(upto)]
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_bt_engine.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'aitrader.backtest.engine'`.

- [ ] **Step 3: Implement**

```python
# aitrader/backtest/engine.py
"""Day-by-day portfolio simulation. Decisions at day t use rows <= t; entries fill at t+1's open."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date
from typing import Dict, List, Sequence, Tuple

import pandas as pd

from aitrader.backtest.costs import CostModel
from aitrader.backtest.indicators import MIN_BARS, MIN_TRADED_VALUE
from aitrader.backtest.rules import exit_check, gap_skip, planned_target, size_position
from aitrader.backtest.types import LONG, Candidate, EquityPoint, MoneyRules, Trade

_EMPTY = pd.DataFrame(columns=["open", "high", "low", "close", "volume", "bars", "value20"])


@dataclass
class SimResult:
    trades: List[Trade]
    equity: List[EquityPoint]
    skips: Dict[str, int]
    notes: Dict[str, int]


@dataclass
class _Position:
    cand: Candidate
    entry_date: date
    entry_price: float
    shares: int
    target: float
    entry_charges: float
    last_close: float
    days_held: int = 0


def day_slices(frames: Dict[str, pd.DataFrame], days: Sequence[pd.Timestamp]) -> Dict[pd.Timestamp, pd.DataFrame]:
    """One frame per day, indexed by symbol, holding every symbol that has a bar that day."""
    if not frames or not days:
        return {}
    panel = pd.concat(frames, names=["symbol", "date"])
    dates = panel.index.get_level_values("date")
    panel = panel[(dates >= days[0]) & (dates <= days[-1])]
    return {day: g.droplevel("date") for day, g in panel.groupby(level="date")}


def _close(pos: _Position, day: date, price: float, reason: str, costs: CostModel) -> Tuple[Trade, float]:
    fill = price * (1 - costs.slippage)
    value = pos.shares * fill
    sell = costs.sell_charges(value)
    pnl = value - sell - (pos.shares * pos.entry_price + pos.entry_charges)
    risk = pos.shares * (pos.entry_price - pos.cand.stop)
    trade = Trade(pos.cand.symbol, pos.cand.signal_date, pos.entry_date, pos.entry_price, pos.shares, pos.cand.stop,
                  pos.target, day, fill, reason, pos.entry_charges + sell, pnl, pnl / risk, pos.cand.reason,
                  dict(pos.cand.facts))
    return trade, value - sell


def simulate(frames: Dict[str, pd.DataFrame], days: Sequence[pd.Timestamp], strategy, money: MoneyRules,
             costs: CostModel) -> SimResult:
    by_day = day_slices(frames, days)
    cash = money.capital
    positions: Dict[str, _Position] = {}
    pending: List[Candidate] = []
    pause = False
    trades: List[Trade] = []
    equity: List[EquityPoint] = []
    skips: Counter = Counter()
    notes: Counter = Counter()
    last = len(days) - 1

    for i, day in enumerate(days):
        today = by_day.get(day, _EMPTY)
        d = day.date()

        # 1. entries at the open, decided at yesterday's close
        for cand in sorted(pending, key=lambda c: (-c.score, c.symbol)):
            if pause:
                skips["daily_loss_pause"] += 1
                continue
            if cand.symbol in positions:
                skips["already_held"] += 1
                continue
            if len(positions) >= money.max_positions:
                skips["no_slot"] += 1
                continue
            if cand.symbol not in today.index:
                skips["missing_bar"] += 1
                continue
            open_ = float(today.at[cand.symbol, "open"])
            gap = gap_skip(LONG, open_, cand.stop,
                           planned_target(cand.signal_close, cand.stop, money.reward_multiple))
            if gap:
                skips[gap] += 1
                continue
            fill = open_ * (1 + costs.slippage)
            shares = size_position(fill, cand.stop, money)
            if shares < 1:
                skips["size_zero"] += 1
                continue
            while shares > 0 and shares * fill + costs.buy_charges(shares * fill) > cash:
                shares -= 1
            if shares < 1:
                skips["no_cash"] += 1
                continue
            charges = costs.buy_charges(shares * fill)
            cash -= shares * fill + charges
            positions[cand.symbol] = _Position(cand, d, fill, shares,
                                               planned_target(fill, cand.stop, money.reward_multiple), charges, fill)
        pending = []

        # 2. exits
        realised = 0.0
        for symbol in list(positions):
            pos = positions[symbol]
            if symbol not in today.index:
                notes["missing_bar_days"] += 1
                continue
            bar = tuple(float(today.at[symbol, k]) for k in ("open", "high", "low", "close"))
            entry_day = pos.entry_date == d
            if not entry_day:
                pos.days_held += 1
            pos.last_close = bar[3]
            hit = exit_check(LONG, bar, pos.cand.stop, pos.target, entry_day=entry_day, days_held=pos.days_held,
                             time_stop=money.time_stop_days)
            if hit is None and i == last:
                hit = (bar[3], "end_of_test")
            if hit:
                trade, proceeds = _close(pos, d, hit[0], hit[1], costs)
                cash += proceeds
                realised += trade.pnl
                trades.append(trade)
                del positions[symbol]
        if i == last:  # positions with no bar on the last day close at their last known close
            for symbol in list(positions):
                pos = positions.pop(symbol)
                trade, proceeds = _close(pos, d, pos.last_close, "end_of_test", costs)
                cash += proceeds
                realised += trade.pnl
                trades.append(trade)

        # 3. daily loss pause, 4. equity
        pause = realised <= -money.daily_loss_limit
        invested = sum(p.shares * p.last_close for p in positions.values())
        equity.append(EquityPoint(d, cash, invested, cash + invested))

        # 5. candidates at the close
        if i < last:
            eligible = today[(today["bars"] >= MIN_BARS) & (today["value20"] >= MIN_TRADED_VALUE)]
            pending = strategy.candidates(day, eligible)

    return SimResult(trades, equity, dict(skips), dict(notes))
```

- [ ] **Step 4: Run tests**

Run: `.venv/Scripts/python -m pytest tests/test_bt_engine.py -q`
Expected: all pass. (`test_missing_bar_waits_for_next_bar`: AAA has a bar on the last day, so it exits through the normal `end_of_test` path.)

- [ ] **Step 5: Commit**

```bash
git add aitrader/backtest/engine.py tests/test_bt_engine.py
git commit -m "Backtest: day-by-day portfolio engine with money rules"
```

---

### Task 6: Index signals (R-scored)

**Files:**
- Create: `aitrader/backtest/index_signals.py`
- Test: `tests/test_bt_index_signals.py`

**Interfaces:**
- Consumes: `Pullback.signals` / `Breakout.signals` (Task 3), `gap_skip`, `exit_check`, `planned_target` (Task 4), `IndexSignal`, `LONG`, `SHORT` (Task 2).
- Produces: `index_signals(frame: DataFrame (with indicators), symbol: str, days, strategy, money: MoneyRules) -> List[IndexSignal]` sorted by `(signal_date, direction)`; `index_summary(signals, unseen_from: date) -> List[dict]` with keys `symbol, direction, period ("whole"|"unseen"), signals, win_rate, avg_r`.

Rules: one open signal per direction; entry at the raw next open (no slippage, no costs); exits per `exit_check`; last day forces `end_of_test`; R = (exit − entry) ÷ (entry − stop) for long, mirrored for short.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_bt_index_signals.py
from datetime import date

import pytest

from aitrader.backtest.index_signals import index_signals, index_summary
from aitrader.backtest.indicators import add_indicators
from aitrader.backtest.strategies.breakout import Breakout
from aitrader.backtest.types import LONG, SHORT, IndexSignal, MoneyRules
from bt_helpers import flat, frame


def signals_for(rows):
    f = add_indicators(frame(rows))
    return index_signals(f, "NIFTY 50", list(f.index), Breakout(use_volume=False), MoneyRules())


def test_index_breakout_ignores_zero_volume():
    rows = flat(60, volume=0) + [(100, 103.5, 100, 103, 0), (103.2, 104, 102.5, 103.8, 0),
                                 (104, 112, 103.9, 111, 0)] + [(111, 111.5, 110.5, 111, 0)] * 5
    (s,) = signals_for(rows)
    assert (s.direction, s.exit_reason, s.entry) == (LONG, "target", 103.2)
    assert s.stop == pytest.approx(101 - 29.5 / 14)
    assert s.r_multiple == pytest.approx(2.0)


def test_index_breakdown_short():
    rows = flat(60, volume=0) + [(99.5, 100, 96.5, 97, 0), (96.8, 97.5, 96, 96.2, 0),
                                 (96, 96.1, 88, 89, 0)] + [(89, 89.5, 88.5, 89, 0)] * 5
    (s,) = signals_for(rows)
    assert (s.direction, s.exit_reason, s.entry) == (SHORT, "target", 96.8)
    assert s.stop == pytest.approx(99 + 29.5 / 14)
    assert s.r_multiple == pytest.approx(2.0)


def test_index_summary_whole_and_unseen():
    def sig(direction, r, day):
        return IndexSignal("NIFTY 50", direction, day, day, 100, 95, 110, day, 100, "target", r, "x")
    rows = index_summary([sig(LONG, 2.0, date(2024, 1, 2)), sig(LONG, -1.0, date(2025, 1, 2))], date(2025, 1, 1))
    assert {"symbol": "NIFTY 50", "direction": LONG, "period": "whole", "signals": 2, "win_rate": 0.5,
            "avg_r": 0.5} in rows
    assert {"symbol": "NIFTY 50", "direction": LONG, "period": "unseen", "signals": 1, "win_rate": 0.0,
            "avg_r": -1.0} in rows
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_bt_index_signals.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'aitrader.backtest.index_signals'`.

- [ ] **Step 3: Implement**

```python
# aitrader/backtest/index_signals.py
"""NIFTY / BANKNIFTY signal quality in R. Not option profit: no premiums, no costs."""
from __future__ import annotations

from datetime import date
from typing import List, Optional

import pandas as pd

from aitrader.backtest.rules import exit_check, gap_skip, planned_target
from aitrader.backtest.types import LONG, SHORT, IndexSignal, MoneyRules


def index_signals(frame: pd.DataFrame, symbol: str, days, strategy, money: MoneyRules) -> List[IndexSignal]:
    days_here = [d for d in days if d in frame.index]
    out: List[IndexSignal] = []
    for direction in (LONG, SHORT):
        out.extend(_walk(frame, symbol, days_here, strategy, direction, money))
    return sorted(out, key=lambda s: (s.signal_date, s.direction))


def _walk(frame, symbol, days, strategy, direction, money) -> List[IndexSignal]:
    out: List[IndexSignal] = []
    pending = None
    pos: Optional[dict] = None
    last = len(days) - 1
    for i, day in enumerate(days):
        o, h, low, c = (float(frame.at[day, k]) for k in ("open", "high", "low", "close"))
        d = day.date()
        if pending is not None:
            planned = planned_target(pending.signal_close, pending.stop, money.reward_multiple)
            if gap_skip(direction, o, pending.stop, planned) is None:
                pos = {"cand": pending, "entry_date": d, "entry": o, "held": 0,
                       "target": planned_target(o, pending.stop, money.reward_multiple)}
            pending = None
        if pos is not None:
            entry_day = pos["entry_date"] == d
            if not entry_day:
                pos["held"] += 1
            hit = exit_check(direction, (o, h, low, c), pos["cand"].stop, pos["target"], entry_day=entry_day,
                             days_held=pos["held"], time_stop=money.time_stop_days)
            if hit is None and i == last:
                hit = (c, "end_of_test")
            if hit:
                out.append(_finish(symbol, direction, pos, d, hit[0], hit[1]))
                pos = None
        if pos is None and i < last:
            row = frame.loc[[day]].copy()
            row.index = [symbol]
            found = strategy.signals(day, row, direction)
            pending = found[0] if found else None
    return out


def _finish(symbol: str, direction: str, pos: dict, day: date, price: float, reason: str) -> IndexSignal:
    cand, entry = pos["cand"], pos["entry"]
    if direction == LONG:
        r = (price - entry) / (entry - cand.stop)
    else:
        r = (entry - price) / (cand.stop - entry)
    return IndexSignal(symbol, direction, cand.signal_date, pos["entry_date"], entry, cand.stop, pos["target"], day,
                       price, reason, r, cand.reason)


def index_summary(signals: List[IndexSignal], unseen_from: date) -> List[dict]:
    out = []
    for symbol, direction in sorted({(s.symbol, s.direction) for s in signals}):
        mine = [s for s in signals if s.symbol == symbol and s.direction == direction]
        for period, chosen in (("whole", mine), ("unseen", [s for s in mine if s.entry_date >= unseen_from])):
            rs = [s.r_multiple for s in chosen]
            out.append({"symbol": symbol, "direction": direction, "period": period, "signals": len(rs),
                        "win_rate": sum(r > 0 for r in rs) / len(rs) if rs else None,
                        "avg_r": sum(rs) / len(rs) if rs else None})
    return out
```

- [ ] **Step 4: Run tests**

Run: `.venv/Scripts/python -m pytest tests/test_bt_index_signals.py -q`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add aitrader/backtest/index_signals.py tests/test_bt_index_signals.py
git commit -m "Backtest: NIFTY/BANKNIFTY signal quality in R"
```

---

### Task 7: Metrics, checklist, benchmark

**Files:**
- Create: `aitrader/backtest/metrics.py`
- Test: `tests/test_bt_metrics.py`

**Interfaces:**
- Consumes: `SimResult` (Task 5), `Trade`, `EquityPoint`, `MoneyRules` (Task 2).
- Produces:
  - `max_drawdown(values) -> (pct, amount)`, `cagr(start_value, end_value, start: date, end: date) -> Optional[float]`, `longest_losing_streak(pnls) -> int`, `worst_month(points, start_value) -> Optional[float]`
  - `trade_stats(trades) -> dict` keys: `trades, win_rate, avg_win, avg_loss, avg_win_r, avg_loss_r, expectancy_r, profit_factor, total_charges, charges_pct_of_gross, longest_losing_streak`
  - `period_summary(points, trades, start_value) -> Optional[dict]` = trade_stats keys + `start_value, end_value, return_pct, cagr, max_drawdown_pct, max_drawdown_amount, worst_month, pct_days_invested`
  - `benchmark_curve(close: pd.Series, days, capital) -> List[float]`
  - `evidence_checklist(earlier, unseen, whole, bench_unseen) -> List[{"key","label","passed"}]` (4 items: `unseen_trades, positive_expectancy, drawdown, beats_benchmark`)
  - `summarize(sim, benchmark: List[float], money, unseen_from: date) -> dict` keys: `unseen_from, whole, earlier, unseen, benchmark {whole, unseen}, years, checklist, skips, notes, warnings`
  - `clean_json(obj)` — NaN/inf → None, numpy scalars → Python, dates → ISO strings, recursively
  - `WARNINGS: List[str]`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_bt_metrics.py
import json
import math
from datetime import date

import numpy as np
import pandas as pd
import pytest

from aitrader.backtest.engine import SimResult
from aitrader.backtest.metrics import (
    benchmark_curve, cagr, clean_json, evidence_checklist, longest_losing_streak, max_drawdown, period_summary,
    summarize, trade_stats,
)
from aitrader.backtest.types import EquityPoint, MoneyRules, Trade


def trade(pnl, r, charges=10.0, entry=date(2024, 1, 2)):
    return Trade("AAA", entry, entry, 100, 10, 95, 110, entry, 100, "target", charges, pnl, r, "x", {})


def test_max_drawdown():
    assert max_drawdown([100, 120, 90, 130, 117]) == (pytest.approx(0.25), pytest.approx(30))


def test_cagr():
    assert cagr(100, 121, date(2020, 1, 1), date(2022, 1, 1)) == pytest.approx(0.1, rel=1e-3)
    assert cagr(100, 121, date(2020, 1, 1), date(2020, 1, 1)) is None


def test_longest_losing_streak():
    assert longest_losing_streak([5, -1, -2, 3, -1, -1, -1, 4]) == 3


def test_trade_stats():
    s = trade_stats([trade(100, 1.0), trade(-50, -0.5), trade(-50, -0.5)])
    assert s["trades"] == 3 and s["win_rate"] == pytest.approx(1 / 3)
    assert (s["avg_win"], s["avg_loss"], s["expectancy_r"], s["profit_factor"]) == (100, -50, 0, 1.0)
    assert s["total_charges"] == 30 and s["charges_pct_of_gross"] == pytest.approx(1.0)
    assert s["longest_losing_streak"] == 2


def test_period_summary():
    pts = [EquityPoint(date(2024, 1, 2), 20000, 0, 20000), EquityPoint(date(2024, 1, 31), 14000, 7000, 21000),
           EquityPoint(date(2024, 2, 1), 19950, 0, 19950), EquityPoint(date(2024, 2, 29), 22000, 0, 22000)]
    s = period_summary(pts, [], 20000)
    assert s["return_pct"] == pytest.approx(0.1)
    assert s["max_drawdown_pct"] == pytest.approx(0.05) and s["max_drawdown_amount"] == pytest.approx(1050)
    assert s["worst_month"] == pytest.approx(22000 / 21000 - 1)
    assert s["pct_days_invested"] == pytest.approx(0.25) and s["trades"] == 0


def test_benchmark_curve_forward_fills():
    days = list(pd.bdate_range("2024-01-01", periods=3))
    close = pd.Series([100.0, 110.0], index=days[:2])
    assert benchmark_curve(close, days, 20000) == [20000, 22000, 22000]


def test_checklist():
    earlier = {"expectancy_r": 0.2, "trades": 40, "return_pct": 0.1, "max_drawdown_pct": 0.1}
    unseen = {"expectancy_r": 0.1, "trades": 31, "return_pct": 0.05, "max_drawdown_pct": 0.08}
    whole = {"max_drawdown_pct": 0.12}
    items = evidence_checklist(earlier, unseen, whole, {"return_pct": 0.2, "max_drawdown_pct": 0.1})
    assert [i["passed"] for i in items] == [True, True, True, True]
    items = evidence_checklist(None, {**unseen, "trades": 5}, {"max_drawdown_pct": 0.3},
                               {"return_pct": 0.2, "max_drawdown_pct": 0.05})
    assert [i["passed"] for i in items] == [False, False, False, False]


def sim(points, trades=()):
    return SimResult(list(trades), points, {"no_slot": 2}, {})


def test_summarize_without_earlier_period():
    pts = [EquityPoint(date(2024, 1, 2), 20000, 0, 20000), EquityPoint(date(2024, 1, 3), 20100, 0, 20100)]
    s = summarize(sim(pts, [trade(100, 1.0, entry=date(2024, 1, 2))]), [20000, 20200], MoneyRules(), date(2023, 1, 1))
    assert s["earlier"] is None and s["unseen"]["trades"] == 1 and s["whole"]["trades"] == 1
    assert s["benchmark"]["unseen"]["return_pct"] == pytest.approx(0.01)
    assert next(i for i in s["checklist"] if i["key"] == "positive_expectancy")["passed"] is False
    assert s["skips"] == {"no_slot": 2} and s["unseen_from"] == "2023-01-01" and s["warnings"]


def test_summary_with_no_trades_is_strict_json():
    pts = [EquityPoint(date(2024, 1, 2), 20000, 0, 20000), EquityPoint(date(2025, 6, 2), 20000, 0, 20000)]
    s = summarize(sim(pts), [20000, 20000], MoneyRules(), date(2025, 1, 1))
    json.dumps(clean_json(s), allow_nan=False)  # must not raise


def test_clean_json():
    out = clean_json({"a": np.float64(1.5), "b": float("nan"), "c": [np.int64(3), math.inf], "d": date(2024, 1, 2)})
    assert out == {"a": 1.5, "b": None, "c": [3, None], "d": "2024-01-02"}
    assert type(out["c"][0]) is int
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_bt_metrics.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'aitrader.backtest.metrics'`.

- [ ] **Step 3: Implement**

```python
# aitrader/backtest/metrics.py
from __future__ import annotations

import math
from datetime import date
from typing import List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from aitrader.backtest.types import EquityPoint, MoneyRules, Trade

WARNINGS = [
    "Survivorship bias: today's Nifty 200 list is applied to past years, so stocks that later dropped out are "
    "missing and results look better than reality.",
    "NIFTY/BANKNIFTY results are index signal quality in R — not option profit.",
    "Benchmark is the NIFTY 50 price index — it excludes dividends.",
    "These are past results only — not a promise of future profit.",
]


def max_drawdown(values: Sequence[float]) -> Tuple[float, float]:
    peak, worst_pct, worst_amt = None, 0.0, 0.0
    for v in values:
        peak = v if peak is None else max(peak, v)
        if peak > 0 and (peak - v) / peak > worst_pct:
            worst_pct, worst_amt = (peak - v) / peak, peak - v
    return worst_pct, worst_amt


def cagr(start_value: float, end_value: float, start: date, end: date) -> Optional[float]:
    days = (end - start).days
    if days <= 0 or start_value <= 0 or end_value <= 0:
        return None
    return (end_value / start_value) ** (365.25 / days) - 1


def longest_losing_streak(pnls: Sequence[float]) -> int:
    best = run = 0
    for p in pnls:
        run = run + 1 if p <= 0 else 0
        best = max(best, run)
    return best


def worst_month(points: Sequence[EquityPoint], start_value: float) -> Optional[float]:
    month_end = {}
    for p in points:
        month_end[(p.day.year, p.day.month)] = p.equity
    prev, worst = start_value, None
    for key in sorted(month_end):
        r = month_end[key] / prev - 1
        worst = r if worst is None else min(worst, r)
        prev = month_end[key]
    return worst


def _mean(xs: Sequence[float]) -> Optional[float]:
    return sum(xs) / len(xs) if xs else None


def trade_stats(trades: Sequence[Trade]) -> dict:
    ordered = sorted(trades, key=lambda t: (t.exit_date, t.entry_date, t.symbol))
    wins = [t for t in ordered if t.pnl > 0]
    losses = [t for t in ordered if t.pnl <= 0]
    gross_loss = -sum(t.pnl for t in losses)
    charges = sum(t.charges for t in ordered)
    gross_before = sum(t.pnl + t.charges for t in ordered)
    return {
        "trades": len(ordered),
        "win_rate": len(wins) / len(ordered) if ordered else None,
        "avg_win": _mean([t.pnl for t in wins]),
        "avg_loss": _mean([t.pnl for t in losses]),
        "avg_win_r": _mean([t.r_multiple for t in wins]),
        "avg_loss_r": _mean([t.r_multiple for t in losses]),
        "expectancy_r": _mean([t.r_multiple for t in ordered]),
        "profit_factor": sum(t.pnl for t in wins) / gross_loss if gross_loss > 0 else None,
        "total_charges": charges,
        "charges_pct_of_gross": charges / gross_before if gross_before > 0 else None,
        "longest_losing_streak": longest_losing_streak([t.pnl for t in ordered]),
    }


def period_summary(points: Sequence[EquityPoint], trades: Sequence[Trade], start_value: float) -> Optional[dict]:
    if not points:
        return None
    end_value = points[-1].equity
    dd_pct, dd_amt = max_drawdown([start_value] + [p.equity for p in points])
    return {
        "start_value": start_value,
        "end_value": end_value,
        "return_pct": end_value / start_value - 1,
        "cagr": cagr(start_value, end_value, points[0].day, points[-1].day),
        "max_drawdown_pct": dd_pct,
        "max_drawdown_amount": dd_amt,
        "worst_month": worst_month(points, start_value),
        "pct_days_invested": sum(1 for p in points if p.invested_value > 0) / len(points),
        **trade_stats(trades),
    }


def benchmark_curve(close: pd.Series, days, capital: float) -> List[float]:
    s = close.reindex(days).ffill().bfill()
    first = float(s.iloc[0])
    return [capital * float(v) / first for v in s]


def _bench(values: Sequence[float], start_value: float) -> Optional[dict]:
    if not values:
        return None
    return {"return_pct": values[-1] / start_value - 1,
            "max_drawdown_pct": max_drawdown([start_value, *values])[0]}


def yearly(points: Sequence[EquityPoint], trades: Sequence[Trade], start_value: float) -> List[dict]:
    out, prev = [], start_value
    for year in sorted({p.day.year for p in points}):
        mine = [p for p in points if p.day.year == year]
        out.append({"year": year, "return_pct": mine[-1].equity / prev - 1,
                    "max_drawdown_pct": max_drawdown([prev] + [p.equity for p in mine])[0],
                    "trades": sum(1 for t in trades if t.entry_date.year == year)})
        prev = mine[-1].equity
    return out


def evidence_checklist(earlier, unseen, whole, bench_unseen) -> List[dict]:
    def positive(p):
        return p is not None and p.get("expectancy_r") is not None and p["expectancy_r"] > 0

    items = [
        ("unseen_trades", "At least 30 trades in the unseen period", unseen is not None and unseen["trades"] >= 30),
        ("positive_expectancy", "Positive expectancy after charges in both periods",
         positive(earlier) and positive(unseen)),
        ("drawdown", "Biggest fall at most 20% over the whole run",
         whole is not None and whole["max_drawdown_pct"] <= 0.20),
        ("beats_benchmark", "Unseen period beats NIFTY 50 on return or on biggest fall",
         unseen is not None and bench_unseen is not None
         and (unseen["return_pct"] > bench_unseen["return_pct"]
              or unseen["max_drawdown_pct"] < bench_unseen["max_drawdown_pct"])),
    ]
    return [{"key": k, "label": label, "passed": bool(ok)} for k, label, ok in items]


def summarize(sim, benchmark: List[float], money: MoneyRules, unseen_from: date) -> dict:
    pts = sim.equity
    k = sum(1 for p in pts if p.day < unseen_from)
    earlier_pts, unseen_pts = pts[:k], pts[k:]
    earlier_trades = [t for t in sim.trades if t.entry_date < unseen_from]
    unseen_trades = [t for t in sim.trades if t.entry_date >= unseen_from]
    unseen_start = earlier_pts[-1].equity if earlier_pts else money.capital
    bench_unseen_start = benchmark[k - 1] if k > 0 else money.capital
    whole = period_summary(pts, sim.trades, money.capital)
    earlier = period_summary(earlier_pts, earlier_trades, money.capital)
    unseen = period_summary(unseen_pts, unseen_trades, unseen_start)
    bench = {"whole": _bench(benchmark, money.capital), "unseen": _bench(benchmark[k:], bench_unseen_start)}
    return {
        "unseen_from": unseen_from.isoformat(),
        "whole": whole,
        "earlier": earlier,
        "unseen": unseen,
        "benchmark": bench,
        "years": yearly(pts, sim.trades, money.capital),
        "checklist": evidence_checklist(earlier, unseen, whole, bench["unseen"]),
        "skips": dict(sim.skips),
        "notes": dict(sim.notes),
        "warnings": list(WARNINGS),
    }


def clean_json(obj):
    if isinstance(obj, dict):
        return {str(k): clean_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [clean_json(v) for v in obj]
    if isinstance(obj, np.generic):
        obj = obj.item()
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    if isinstance(obj, date):
        return obj.isoformat()
    return obj
```

- [ ] **Step 4: Run tests**

Run: `.venv/Scripts/python -m pytest tests/test_bt_metrics.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add aitrader/backtest/metrics.py tests/test_bt_metrics.py
git commit -m "Backtest: metrics, period split, evidence checklist, benchmark"
```

---

### Task 8: Storage — tables, migration, repo

**Files:**
- Modify: `aitrader/db/schema.py` (append four tables), `aitrader/db/repo.py` (append backtest section, extend the schema import)
- Create: `migrations/versions/c4d8e2f1a9b3_backtest_tables.py`
- Test: `tests/test_bt_repo.py`; modify `tests/test_schema.py` `EXPECTED`

**Interfaces:**
- Produces (repo):
  - `instrument_by_symbol(conn, symbol, exchange="NSE") -> Optional[dict]` (`id, kind, symbol`)
  - `bar_dates(conn, instrument_id, source) -> List[date]`
  - `backtest_running(conn) -> bool`
  - `insert_backtest_run(conn, *, strategy, start_date, end_date, unseen_from, params) -> int` (status `running`)
  - `backtest_run_row(conn, run_id) -> Optional[dict]`
  - `finish_backtest_run(conn, run_id, *, status, summary=None, message="") -> None`
  - `fail_interrupted_backtests(conn, message) -> int`
  - `insert_backtest_trades(conn, run_id, rows)`, `insert_backtest_equity(conn, run_id, rows)`, `insert_backtest_index_signals(conn, run_id, rows)` (rows: list of column dicts without `run_id`)
  - `list_backtest_runs(conn, limit) -> List[dict]` (summary trimmed to `whole` + `checklist`)
  - `get_backtest_run(conn, run_id) -> Optional[dict]` (run columns + `trades`, `equity`, `index_signals` lists)
  - `daily_bars_for_symbol(conn, symbol, *, source, start=None, end=None) -> Optional[List[dict]]` (None if the symbol is unknown)

- [ ] **Step 1: Write the failing tests**

In `tests/test_schema.py` add `"backtest_runs", "backtest_trades", "backtest_equity", "backtest_index_signals"` to `EXPECTED`.

```python
# tests/test_bt_repo.py
from datetime import date

from aitrader.db import repo
from aitrader.models import Bar


def new_run(conn, strategy="pullback"):
    return repo.insert_backtest_run(conn, strategy=strategy, start_date=date(2024, 1, 1), end_date=date(2025, 1, 1),
                                    unseen_from=date(2024, 6, 1), params={"money": {"capital": 20000.0}})


def test_run_lifecycle_and_listing(engine):
    with engine.begin() as conn:
        run_id = new_run(conn)
        assert repo.backtest_running(conn) is True
        repo.insert_backtest_trades(conn, run_id, [dict(
            instrument_id=None, symbol="AAA", signal_date=date(2024, 2, 1), entry_date=date(2024, 2, 2),
            entry_price=100.05, shares=69, stop=95, target=110.15, exit_date=date(2024, 2, 5), exit_price=110.09,
            exit_reason="target", charges=33.2, pnl=650.5, r_multiple=1.86, reason="why", facts={"close": 100.0},
            period="earlier")])
        repo.insert_backtest_equity(conn, run_id, [dict(trade_date=date(2024, 2, 2), cash=13000, invested_value=7000,
                                                        equity=20000, benchmark_equity=20000)])
        repo.insert_backtest_index_signals(conn, run_id, [dict(
            symbol="NIFTY 50", direction="short", signal_date=date(2024, 3, 1), entry_date=date(2024, 3, 4),
            entry=22000, stop=22300, target=21400, exit_date=date(2024, 3, 8), exit=21400, exit_reason="target",
            r_multiple=2.0, reason="why", period="unseen")])
        repo.finish_backtest_run(conn, run_id, status="ok",
                                 summary={"whole": {"return_pct": 0.1}, "checklist": [], "years": [1]})
        assert repo.backtest_running(conn) is False
    with engine.connect() as conn:
        (listed,) = repo.list_backtest_runs(conn, 10)
        assert listed["id"] == run_id and listed["summary"] == {"whole": {"return_pct": 0.1}, "checklist": []}
        full = repo.get_backtest_run(conn, run_id)
        assert full["status"] == "ok" and full["finished_at"] is not None
        assert full["trades"][0]["facts"] == {"close": 100.0} and float(full["trades"][0]["pnl"]) == 650.5
        assert len(full["equity"]) == 1 and full["index_signals"][0]["direction"] == "short"
        assert repo.get_backtest_run(conn, run_id + 99) is None


def test_fail_interrupted(engine):
    with engine.begin() as conn:
        run_id = new_run(conn)
        assert repo.fail_interrupted_backtests(conn, "interrupted") == 1
        row = repo.backtest_run_row(conn, run_id)
        assert (row["status"], row["message"]) == ("failed", "interrupted")


def test_bars_by_symbol_and_dates(engine):
    with engine.begin() as conn:
        iid = repo.upsert_instrument(conn, kind="index", exchange="NSE", symbol="NIFTY 50", name="NIFTY 50",
                                     yf_symbol="^NSEI")
        repo.upsert_daily_bars(conn, iid, [Bar(date(2024, 1, 2), 1, 2, 0.5, 1.5, 0),
                                           Bar(date(2024, 1, 3), 1.5, 2, 1, 1.8, 0)],
                               source="yfinance", price_basis="split_adjusted")
    with engine.connect() as conn:
        assert repo.instrument_by_symbol(conn, "NIFTY 50")["id"] == iid
        assert repo.bar_dates(conn, iid, "yfinance") == [date(2024, 1, 2), date(2024, 1, 3)]
        rows = repo.daily_bars_for_symbol(conn, "NIFTY 50", source="yfinance", start=date(2024, 1, 3))
        assert [r["trade_date"] for r in rows] == [date(2024, 1, 3)] and float(rows[0]["close"]) == 1.8
        assert repo.daily_bars_for_symbol(conn, "NOPE", source="yfinance") is None
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_bt_repo.py tests/test_schema.py -q`
Expected: FAIL — `AttributeError: module 'aitrader.db.repo' has no attribute 'insert_backtest_run'` and the schema test missing tables.

- [ ] **Step 3: Implement**

Append to `aitrader/db/schema.py`:

```python
backtest_runs = Table(
    "backtest_runs",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("strategy", String(20), nullable=False),
    Column("start_date", Date),
    Column("end_date", Date),
    Column("unseen_from", Date),
    Column("params", JSONB, nullable=False),
    Column("status", String(10), nullable=False),
    Column("message", Text, nullable=False, server_default=text("''")),
    Column("summary", JSONB),
    Column("started_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("finished_at", DateTime(timezone=True)),
    CheckConstraint("status in ('running', 'ok', 'failed')", name="ck_backtest_runs_status"),
)

backtest_trades = Table(
    "backtest_trades",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("run_id", Integer, ForeignKey("backtest_runs.id", ondelete="CASCADE"), nullable=False, index=True),
    Column("instrument_id", Integer, ForeignKey("instruments.id")),
    Column("symbol", String(40), nullable=False),
    Column("signal_date", Date, nullable=False),
    Column("entry_date", Date, nullable=False),
    Column("entry_price", PRICE, nullable=False),
    Column("shares", Integer, nullable=False),
    Column("stop", PRICE, nullable=False),
    Column("target", PRICE, nullable=False),
    Column("exit_date", Date, nullable=False),
    Column("exit_price", PRICE, nullable=False),
    Column("exit_reason", String(20), nullable=False),
    Column("charges", PRICE, nullable=False),
    Column("pnl", PRICE, nullable=False),
    Column("r_multiple", Numeric(12, 4), nullable=False),
    Column("reason", Text, nullable=False),
    Column("facts", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column("period", String(10), nullable=False),
)

backtest_equity = Table(
    "backtest_equity",
    metadata,
    Column("run_id", Integer, ForeignKey("backtest_runs.id", ondelete="CASCADE"), primary_key=True),
    Column("trade_date", Date, primary_key=True),
    Column("cash", PRICE, nullable=False),
    Column("invested_value", PRICE, nullable=False),
    Column("equity", PRICE, nullable=False),
    Column("benchmark_equity", PRICE, nullable=False),
)

backtest_index_signals = Table(
    "backtest_index_signals",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("run_id", Integer, ForeignKey("backtest_runs.id", ondelete="CASCADE"), nullable=False, index=True),
    Column("symbol", String(40), nullable=False),
    Column("direction", String(5), nullable=False),
    Column("signal_date", Date, nullable=False),
    Column("entry_date", Date, nullable=False),
    Column("entry", PRICE, nullable=False),
    Column("stop", PRICE, nullable=False),
    Column("target", PRICE, nullable=False),
    Column("exit_date", Date, nullable=False),
    Column("exit", PRICE, nullable=False),
    Column("exit_reason", String(20), nullable=False),
    Column("r_multiple", Numeric(12, 4), nullable=False),
    Column("reason", Text, nullable=False),
    Column("period", String(10), nullable=False),
)
```

Create `migrations/versions/c4d8e2f1a9b3_backtest_tables.py`:

```python
"""backtest tables

Revision ID: c4d8e2f1a9b3
Revises: 91e795b9cd26
Create Date: 2026-10-23 09:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c4d8e2f1a9b3"
down_revision: Union[str, Sequence[str], None] = "91e795b9cd26"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

PRICE = sa.Numeric(14, 4)


def upgrade() -> None:
    op.create_table(
        "backtest_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("strategy", sa.String(20), nullable=False),
        sa.Column("start_date", sa.Date()),
        sa.Column("end_date", sa.Date()),
        sa.Column("unseen_from", sa.Date()),
        sa.Column("params", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("message", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("summary", postgresql.JSONB()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("status in ('running', 'ok', 'failed')", name="ck_backtest_runs_status"),
    )
    op.create_table(
        "backtest_trades",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("backtest_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("instrument_id", sa.Integer(), sa.ForeignKey("instruments.id")),
        sa.Column("symbol", sa.String(40), nullable=False),
        sa.Column("signal_date", sa.Date(), nullable=False),
        sa.Column("entry_date", sa.Date(), nullable=False),
        sa.Column("entry_price", PRICE, nullable=False),
        sa.Column("shares", sa.Integer(), nullable=False),
        sa.Column("stop", PRICE, nullable=False),
        sa.Column("target", PRICE, nullable=False),
        sa.Column("exit_date", sa.Date(), nullable=False),
        sa.Column("exit_price", PRICE, nullable=False),
        sa.Column("exit_reason", sa.String(20), nullable=False),
        sa.Column("charges", PRICE, nullable=False),
        sa.Column("pnl", PRICE, nullable=False),
        sa.Column("r_multiple", sa.Numeric(12, 4), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("facts", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("period", sa.String(10), nullable=False),
    )
    op.create_index("ix_backtest_trades_run_id", "backtest_trades", ["run_id"])
    op.create_table(
        "backtest_equity",
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("backtest_runs.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("trade_date", sa.Date(), primary_key=True),
        sa.Column("cash", PRICE, nullable=False),
        sa.Column("invested_value", PRICE, nullable=False),
        sa.Column("equity", PRICE, nullable=False),
        sa.Column("benchmark_equity", PRICE, nullable=False),
    )
    op.create_table(
        "backtest_index_signals",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("backtest_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("symbol", sa.String(40), nullable=False),
        sa.Column("direction", sa.String(5), nullable=False),
        sa.Column("signal_date", sa.Date(), nullable=False),
        sa.Column("entry_date", sa.Date(), nullable=False),
        sa.Column("entry", PRICE, nullable=False),
        sa.Column("stop", PRICE, nullable=False),
        sa.Column("target", PRICE, nullable=False),
        sa.Column("exit_date", sa.Date(), nullable=False),
        sa.Column("exit", PRICE, nullable=False),
        sa.Column("exit_reason", sa.String(20), nullable=False),
        sa.Column("r_multiple", sa.Numeric(12, 4), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("period", sa.String(10), nullable=False),
    )
    op.create_index("ix_backtest_index_signals_run_id", "backtest_index_signals", ["run_id"])


def downgrade() -> None:
    op.drop_index("ix_backtest_index_signals_run_id", table_name="backtest_index_signals")
    op.drop_table("backtest_index_signals")
    op.drop_table("backtest_equity")
    op.drop_index("ix_backtest_trades_run_id", table_name="backtest_trades")
    op.drop_table("backtest_trades")
    op.drop_table("backtest_runs")
```

In `aitrader/db/repo.py`, extend the schema import with `backtest_equity, backtest_index_signals, backtest_runs, backtest_trades` and append:

```python
# --- backtests -------------------------------------------------------------------

def instrument_by_symbol(conn: Connection, symbol: str, exchange: str = "NSE") -> Optional[dict]:
    row = conn.execute(select(instruments.c.id, instruments.c.kind, instruments.c.symbol)
                       .where(instruments.c.exchange == exchange, instruments.c.symbol == symbol)).mappings().first()
    return dict(row) if row else None


def bar_dates(conn: Connection, instrument_id: int, source: str) -> List[date]:
    return list(conn.execute(select(daily_bars.c.trade_date)
                             .where(daily_bars.c.instrument_id == instrument_id, daily_bars.c.source == source)
                             .order_by(daily_bars.c.trade_date)).scalars())


def backtest_running(conn: Connection) -> bool:
    return conn.execute(select(func.count()).select_from(backtest_runs)
                        .where(backtest_runs.c.status == "running")).scalar_one() > 0


def insert_backtest_run(conn: Connection, *, strategy: str, start_date: Optional[date], end_date: Optional[date],
                        unseen_from: Optional[date], params: dict) -> int:
    return conn.execute(backtest_runs.insert().values(
        strategy=strategy, start_date=start_date, end_date=end_date, unseen_from=unseen_from, params=params,
        status="running").returning(backtest_runs.c.id)).scalar_one()


def backtest_run_row(conn: Connection, run_id: int) -> Optional[dict]:
    row = conn.execute(select(backtest_runs).where(backtest_runs.c.id == run_id)).mappings().first()
    return dict(row) if row else None


def finish_backtest_run(conn: Connection, run_id: int, *, status: str, summary: Optional[dict] = None,
                        message: str = "") -> None:
    conn.execute(update(backtest_runs).where(backtest_runs.c.id == run_id)
                 .values(status=status, summary=summary, message=message, finished_at=func.now()))


def fail_interrupted_backtests(conn: Connection, message: str) -> int:
    return conn.execute(update(backtest_runs).where(backtest_runs.c.status == "running")
                        .values(status="failed", message=message, finished_at=func.now())).rowcount


def _insert_for_run(conn: Connection, table, run_id: int, rows: Sequence[dict]) -> None:
    if rows:
        conn.execute(table.insert(), [dict(run_id=run_id, **r) for r in rows])


def insert_backtest_trades(conn: Connection, run_id: int, rows: Sequence[dict]) -> None:
    _insert_for_run(conn, backtest_trades, run_id, rows)


def insert_backtest_equity(conn: Connection, run_id: int, rows: Sequence[dict]) -> None:
    _insert_for_run(conn, backtest_equity, run_id, rows)


def insert_backtest_index_signals(conn: Connection, run_id: int, rows: Sequence[dict]) -> None:
    _insert_for_run(conn, backtest_index_signals, run_id, rows)


def list_backtest_runs(conn: Connection, limit: int) -> List[dict]:
    out = []
    for r in conn.execute(select(backtest_runs).order_by(backtest_runs.c.id.desc()).limit(limit)).mappings():
        row = dict(r)
        row.pop("params", None)
        if row["summary"]:
            row["summary"] = {"whole": row["summary"].get("whole"), "checklist": row["summary"].get("checklist")}
        out.append(row)
    return out


def get_backtest_run(conn: Connection, run_id: int) -> Optional[dict]:
    run = backtest_run_row(conn, run_id)
    if run is None:
        return None
    run["trades"] = [dict(r) for r in conn.execute(
        select(backtest_trades).where(backtest_trades.c.run_id == run_id)
        .order_by(backtest_trades.c.entry_date, backtest_trades.c.id)).mappings()]
    run["equity"] = [dict(r) for r in conn.execute(
        select(backtest_equity).where(backtest_equity.c.run_id == run_id)
        .order_by(backtest_equity.c.trade_date)).mappings()]
    run["index_signals"] = [dict(r) for r in conn.execute(
        select(backtest_index_signals).where(backtest_index_signals.c.run_id == run_id)
        .order_by(backtest_index_signals.c.signal_date, backtest_index_signals.c.id)).mappings()]
    return run


def daily_bars_for_symbol(conn: Connection, symbol: str, *, source: str, start: Optional[date] = None,
                          end: Optional[date] = None) -> Optional[List[dict]]:
    inst = instrument_by_symbol(conn, symbol)
    if inst is None:
        return None
    q = (select(daily_bars.c.trade_date, daily_bars.c.open, daily_bars.c.high, daily_bars.c.low,
                daily_bars.c.close, daily_bars.c.volume)
         .where(daily_bars.c.instrument_id == inst["id"], daily_bars.c.source == source))
    if start:
        q = q.where(daily_bars.c.trade_date >= start)
    if end:
        q = q.where(daily_bars.c.trade_date <= end)
    return [dict(r) for r in conn.execute(q.order_by(daily_bars.c.trade_date)).mappings()]
```

- [ ] **Step 4: Run tests**

Run: `.venv/Scripts/python -m pytest tests/test_bt_repo.py tests/test_schema.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add aitrader/db migrations/versions/c4d8e2f1a9b3_backtest_tables.py tests/test_bt_repo.py tests/test_schema.py
git commit -m "Backtest: tables, migration and repository functions"
```

---

### Task 9: Runner (create + execute a run)

**Files:**
- Create: `aitrader/backtest/runner.py`
- Test: `tests/test_bt_runner.py`

**Interfaces:**
- Consumes: everything above.
- Produces:
  - `BacktestRequest(strategy: str, start: Optional[date] = None, end: Optional[date] = None, money: MoneyRules = MoneyRules())`
  - `BacktestInputError(ValueError)` (→ 400), `BacktestBusyError(RuntimeError)` (→ 409)
  - `create_run(engine, req) -> int` — validates, takes a Postgres advisory lock, refuses if a run is `running`, resolves the range, inserts the row
  - `execute_run(engine, run_id) -> None` — never raises; ends with status `ok` or `failed`
  - Constants `NO_DATA`, `INTERRUPTED = "interrupted (service restarted)"`, `SOURCE = "yfinance"`, `BENCHMARK = "NIFTY 50"`

Range rules (spec §9, §11): earliest start = date of the 200th NIFTY 50 bar; default start = earliest; explicit start before earliest → 400; end defaults to (and is capped at) the last NIFTY 50 bar; `unseen_from = max(start, end − 2 years)`. If NIFTY 50 has fewer than 200 bars, the run row is created with null dates and `execute_run` fails it with `NO_DATA`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_bt_runner.py
import pandas as pd
import pytest

from aitrader.backtest import runner
from aitrader.backtest.runner import (
    NO_DATA, BacktestBusyError, BacktestInputError, BacktestRequest, create_run, execute_run,
)
from aitrader.backtest.types import MoneyRules
from aitrader.db import repo
from aitrader.models import Bar
from bt_helpers import flat


def seed(engine, series):
    n = max(len(rows) for rows in series.values())
    days = pd.bdate_range("2024-01-01", periods=n)
    with engine.begin() as conn:
        for symbol, rows in series.items():
            kind = "index" if symbol.startswith("NIFTY") else "stock"
            iid = repo.upsert_instrument(conn, kind=kind, exchange="NSE", symbol=symbol, name=symbol,
                                         yf_symbol=f"{symbol}.NS")
            repo.upsert_daily_bars(conn, iid, [Bar(d.date(), r[0], r[1], r[2], r[3], int(r[4]))
                                               for d, r in zip(days, rows)],
                                   source="yfinance", price_basis="split_adjusted")
    return days


def breakout_series():
    return (flat(250) + [(100.5, 103.5, 100, 103, 6e6), (103.2, 104, 102.5, 103.8, 2e6),
                         (103.8, 105.5, 103.5, 105, 2e6), (105, 107.5, 104.8, 107, 2e6),
                         (107, 109.5, 106.8, 109, 2e6), (109, 111.5, 108.8, 111, 2e6),
                         (111, 112.5, 110, 112, 2e6)] + [(111, 111.5, 110.5, 111, 2e6)] * 23)


def test_breakout_end_to_end(engine):
    series = {"NIFTY 50": flat(280, volume=0), "BRK": breakout_series()}
    series.update({f"FL{i}": flat(280) for i in range(1, 5)})
    days = seed(engine, series)
    run_id = create_run(engine, BacktestRequest("breakout"))
    execute_run(engine, run_id)
    with engine.connect() as conn:
        run = repo.get_backtest_run(conn, run_id)
    assert run["status"] == "ok", run["message"]
    assert run["start_date"] == days[199].date() and run["end_date"] == days[279].date()
    (t,) = run["trades"]
    assert (t["symbol"], t["shares"], t["exit_reason"], t["period"]) == ("BRK", 67, "target", "unseen")
    assert float(t["entry_price"]) == pytest.approx(103.2516, abs=1e-3)
    assert float(t["exit_price"]) == pytest.approx((103.2516 + 2 * (103.2516 - (101 - 29.5 / 14))) * 0.9995,
                                                    abs=1e-3)
    assert len(run["equity"]) == 81
    assert float(run["equity"][-1]["equity"]) == pytest.approx(20000 + float(t["pnl"]), abs=0.01)
    assert run["summary"]["whole"]["trades"] == 1 and run["summary"]["index_signals"] == []
    assert run["index_signals"] == []


def test_run_with_empty_database_fails_with_guidance(engine):
    run_id = create_run(engine, BacktestRequest("momentum"))
    execute_run(engine, run_id)
    with engine.connect() as conn:
        row = repo.backtest_run_row(conn, run_id)
    assert (row["status"], row["message"]) == ("failed", NO_DATA)


def test_run_without_stocks_fails_with_guidance(engine):
    seed(engine, {"NIFTY 50": flat(210, volume=0)})
    run_id = create_run(engine, BacktestRequest("pullback"))
    execute_run(engine, run_id)
    with engine.connect() as conn:
        row = repo.backtest_run_row(conn, run_id)
    assert (row["status"], row["message"]) == ("failed", NO_DATA)


def test_start_before_warm_up_is_rejected(engine):
    days = seed(engine, {"NIFTY 50": flat(210, volume=0)})
    with pytest.raises(BacktestInputError, match="200 days"):
        create_run(engine, BacktestRequest("pullback", start=days[0].date()))


@pytest.mark.parametrize("req", [
    BacktestRequest("magic"),
    BacktestRequest("pullback", money=MoneyRules(risk_per_trade=50_000)),
    BacktestRequest("pullback", money=MoneyRules(max_positions=0)),
    BacktestRequest("pullback", money=MoneyRules(capital=-1)),
    BacktestRequest("pullback", start=pd.Timestamp("2025-01-02").date(), end=pd.Timestamp("2025-01-01").date()),
])
def test_bad_requests_are_rejected(engine, req):
    with pytest.raises(BacktestInputError):
        create_run(engine, req)


def test_second_run_while_one_is_running_is_refused(engine):
    create_run(engine, BacktestRequest("pullback"))
    with pytest.raises(BacktestBusyError):
        create_run(engine, BacktestRequest("breakout"))


def test_unexpected_error_marks_run_failed(engine, monkeypatch):
    seed(engine, {"NIFTY 50": flat(210, volume=0), "AAA": flat(210)})
    run_id = create_run(engine, BacktestRequest("pullback"))

    def boom(*args, **kwargs):
        raise ZeroDivisionError("x")

    monkeypatch.setattr(runner, "simulate", boom)
    execute_run(engine, run_id)
    with engine.connect() as conn:
        row = repo.backtest_run_row(conn, run_id)
    assert (row["status"], row["message"]) == ("failed", "backtest failed: ZeroDivisionError")
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_bt_runner.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'aitrader.backtest.runner'`.

- [ ] **Step 3: Implement**

```python
# aitrader/backtest/runner.py
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Optional, Tuple

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from aitrader.backtest.bars import load_market
from aitrader.backtest.costs import CostModel
from aitrader.backtest.engine import simulate
from aitrader.backtest.index_signals import index_signals, index_summary
from aitrader.backtest.indicators import MIN_BARS, MIN_TRADED_VALUE, add_indicators
from aitrader.backtest.metrics import benchmark_curve, clean_json, summarize
from aitrader.backtest.strategies import INDEX_STRATEGIES, STRATEGIES
from aitrader.backtest.strategies.breakout import Breakout
from aitrader.backtest.strategies.pullback import Pullback
from aitrader.backtest.types import IndexSignal, MoneyRules, Trade
from aitrader.db import repo

log = logging.getLogger(__name__)

SOURCE = "yfinance"
BENCHMARK = "NIFTY 50"
UNSEEN_YEARS = 2
NO_DATA = "Load the stock list and price history first (Data tab)"
INTERRUPTED = "interrupted (service restarted)"
_LOCK_KEY = 72_026_102  # pg advisory lock: one backtest creation at a time


class BacktestInputError(ValueError):
    """Bad request; the API answers 400."""


class BacktestBusyError(RuntimeError):
    """A backtest is already running; the API answers 409."""


class _NoData(Exception):
    pass


@dataclass(frozen=True)
class BacktestRequest:
    strategy: str
    start: Optional[date] = None
    end: Optional[date] = None
    money: MoneyRules = field(default_factory=MoneyRules)


def _years_before(day: date, years: int) -> date:
    try:
        return day.replace(year=day.year - years)
    except ValueError:  # 29 Feb
        return day.replace(year=day.year - years, day=28)


def validate(req: BacktestRequest) -> None:
    if req.strategy not in STRATEGIES:
        raise BacktestInputError(f"Unknown strategy {req.strategy!r}; choose one of {', '.join(sorted(STRATEGIES))}")
    m = req.money
    if min(m.capital, m.risk_per_trade, m.daily_loss_limit, m.max_position_value) <= 0 or m.max_positions < 1:
        raise BacktestInputError("Money settings must be positive and allow at least one position")
    if m.risk_per_trade > m.capital:
        raise BacktestInputError("Risk per trade cannot exceed starting capital")
    if req.start and req.end and req.end <= req.start:
        raise BacktestInputError("End date must be after the start date")


def resolve_range(conn: Connection, req: BacktestRequest) -> Optional[Tuple[date, date, date]]:
    inst = repo.instrument_by_symbol(conn, BENCHMARK)
    dates = repo.bar_dates(conn, inst["id"], SOURCE) if inst else []
    if len(dates) < MIN_BARS:
        return None
    earliest = dates[MIN_BARS - 1]
    start = req.start or earliest
    if start < earliest:
        raise BacktestInputError(f"Start must be on or after {earliest} (200 days of history are needed first)")
    end = min(req.end or dates[-1], dates[-1])
    if end <= start:
        raise BacktestInputError("End date must be after the start date")
    return start, end, max(start, _years_before(end, UNSEEN_YEARS))


def params_of(req: BacktestRequest) -> dict:
    return clean_json({"money": asdict(req.money), "costs": asdict(CostModel()),
                       "strategy": STRATEGIES[req.strategy].PARAMS,
                       "eligibility": {"min_bars": MIN_BARS, "min_traded_value": MIN_TRADED_VALUE},
                       "unseen_years": UNSEEN_YEARS})


def create_run(engine: Engine, req: BacktestRequest) -> int:
    validate(req)
    with engine.begin() as conn:
        conn.execute(text("select pg_advisory_xact_lock(:k)"), {"k": _LOCK_KEY})
        if repo.backtest_running(conn):
            raise BacktestBusyError("A backtest is already running — wait for it to finish")
        start, end, unseen_from = resolve_range(conn, req) or (None, None, None)
        return repo.insert_backtest_run(conn, strategy=req.strategy, start_date=start, end_date=end,
                                        unseen_from=unseen_from, params=params_of(req))


def execute_run(engine: Engine, run_id: int) -> None:
    try:
        _execute(engine, run_id)
    except _NoData:
        _fail(engine, run_id, NO_DATA)
    except Exception as exc:  # noqa: BLE001 - a run must never be left "running"
        log.exception("backtest %s failed", run_id)
        _fail(engine, run_id, f"backtest failed: {type(exc).__name__}")


def _fail(engine: Engine, run_id: int, message: str) -> None:
    with engine.begin() as conn:
        repo.finish_backtest_run(conn, run_id, status="failed", message=message)


def _trade_row(t: Trade, ids: dict, unseen_from: date) -> dict:
    return dict(instrument_id=ids.get(t.symbol), symbol=t.symbol, signal_date=t.signal_date,
                entry_date=t.entry_date, entry_price=float(t.entry_price), shares=int(t.shares), stop=float(t.stop),
                target=float(t.target), exit_date=t.exit_date, exit_price=float(t.exit_price),
                exit_reason=t.exit_reason, charges=float(t.charges), pnl=float(t.pnl),
                r_multiple=float(t.r_multiple), reason=t.reason, facts=clean_json(t.facts),
                period="unseen" if t.entry_date >= unseen_from else "earlier")


def _signal_row(s: IndexSignal, unseen_from: date) -> dict:
    return dict(symbol=s.symbol, direction=s.direction, signal_date=s.signal_date, entry_date=s.entry_date,
                entry=float(s.entry), stop=float(s.stop), target=float(s.target), exit_date=s.exit_date,
                exit=float(s.exit), exit_reason=s.exit_reason, r_multiple=float(s.r_multiple), reason=s.reason,
                period="unseen" if s.entry_date >= unseen_from else "earlier")


def _execute(engine: Engine, run_id: int) -> None:
    with engine.connect() as conn:
        run = repo.backtest_run_row(conn, run_id)
    if run is None or run["start_date"] is None:
        raise _NoData
    start, end, unseen_from = run["start_date"], run["end_date"], run["unseen_from"]
    money = MoneyRules(**run["params"]["money"])
    costs = CostModel(**run["params"]["costs"])
    market = load_market(engine, SOURCE)
    if not market.stocks or BENCHMARK not in market.indices:
        raise _NoData
    nifty = market.indices[BENCHMARK]
    days = [d for d in nifty.index if start <= d.date() <= end]
    frames = {s: add_indicators(f) for s, f in market.stocks.items()}
    sim = simulate(frames, days, STRATEGIES[run["strategy"]](), money, costs)
    bench = benchmark_curve(nifty["close"], days, money.capital)
    summary = summarize(sim, bench, money, unseen_from)
    signals = []
    if run["strategy"] in INDEX_STRATEGIES:
        idx_strategy = Breakout(use_volume=False) if run["strategy"] == "breakout" else Pullback()
        for symbol, f in sorted(market.indices.items()):
            signals += index_signals(add_indicators(f), symbol, days, idx_strategy, money)
    summary["index_signals"] = index_summary(signals, unseen_from)
    with engine.begin() as conn:
        repo.insert_backtest_trades(conn, run_id, [_trade_row(t, market.instrument_ids, unseen_from)
                                                   for t in sim.trades])
        repo.insert_backtest_equity(conn, run_id, [
            dict(trade_date=p.day, cash=float(p.cash), invested_value=float(p.invested_value),
                 equity=float(p.equity), benchmark_equity=float(b)) for p, b in zip(sim.equity, bench)])
        repo.insert_backtest_index_signals(conn, run_id, [_signal_row(s, unseen_from) for s in signals])
        repo.finish_backtest_run(conn, run_id, status="ok", summary=clean_json(summary))
```

- [ ] **Step 4: Run tests**

Run: `.venv/Scripts/python -m pytest tests/test_bt_runner.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add aitrader/backtest/runner.py tests/test_bt_runner.py
git commit -m "Backtest: runner with range rules, one-at-a-time lock and failure capture"
```

---

### Task 10: API endpoints, restart recovery, README

**Files:**
- Modify: `aitrader/api.py`, `README.md`
- Test: `tests/test_bt_api.py`

**Interfaces:**
- Consumes: `create_run`, `execute_run`, `BacktestRequest`, `BacktestInputError`, `BacktestBusyError`, `INTERRUPTED`, `SOURCE` (Task 9); repo functions (Task 8).
- Produces: `POST /local/backtests` (202 `{"id"}`, 400, 409, 422 for wrong JSON types), `GET /local/backtests?limit=`, `GET /local/backtests/{id}` (404), `GET /local/bars/{symbol}?start=&end=` (404). `create_app` marks leftover `running` backtests as failed with `INTERRUPTED` when it builds the app.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_bt_api.py
from datetime import date, datetime

from fastapi.testclient import TestClient

from aitrader.api import create_app
from aitrader.auth import IST
from aitrader.backtest.runner import INTERRUPTED, NO_DATA
from aitrader.calendar import TradingCalendar
from aitrader.db import repo
from aitrader.jobs import JobDeps
from aitrader.models import Bar
from fakes import FakeBarSource


def client(engine):
    deps = JobDeps(engine=engine, kite=lambda: None, bar_source=FakeBarSource({}), calendar=TradingCalendar(),
                   now=lambda: datetime(2026, 10, 21, 17, 0, tzinfo=IST))
    return TestClient(create_app(deps=deps, sessions=None, gateway_factory=None, dashboard_url="http://x"))


def running_run(engine):
    with engine.begin() as conn:
        return repo.insert_backtest_run(conn, strategy="pullback", start_date=None, end_date=None,
                                        unseen_from=None, params={})


def test_run_lifecycle_without_data(engine):
    c = client(engine)
    response = c.post("/local/backtests", json={"strategy": "pullback"})
    assert response.status_code == 202
    run_id = response.json()["id"]
    body = c.get(f"/local/backtests/{run_id}").json()
    assert (body["status"], body["message"]) == ("failed", NO_DATA)
    assert c.get("/local/backtests").json()[0]["id"] == run_id


def test_bad_input_is_400(engine):
    c = client(engine)
    assert c.post("/local/backtests", json={"strategy": "magic"}).status_code == 400
    assert c.post("/local/backtests", json={"strategy": "pullback", "risk_per_trade": 50000}).status_code == 400
    assert c.post("/local/backtests", json={"strategy": "pullback", "capital": "lots"}).status_code == 422


def test_second_run_is_409(engine):
    c = client(engine)
    running_run(engine)
    assert c.post("/local/backtests", json={"strategy": "breakout"}).status_code == 409


def test_unknown_run_is_404(engine):
    assert client(engine).get("/local/backtests/999").status_code == 404


def test_interrupted_runs_fail_on_startup(engine):
    run_id = running_run(engine)
    body = client(engine).get(f"/local/backtests/{run_id}").json()
    assert (body["status"], body["message"]) == ("failed", INTERRUPTED)


def test_bars_endpoint(engine):
    with engine.begin() as conn:
        iid = repo.upsert_instrument(conn, kind="stock", exchange="NSE", symbol="INFY", name="Infosys",
                                     yf_symbol="INFY.NS")
        repo.upsert_daily_bars(conn, iid, [Bar(date(2024, 1, 2), 1, 2, 0.5, 1.5, 10),
                                           Bar(date(2024, 1, 3), 1.5, 2, 1, 1.8, 12)],
                               source="yfinance", price_basis="split_adjusted")
    c = client(engine)
    rows = c.get("/local/bars/INFY", params={"start": "2024-01-03"}).json()
    assert rows == [{"trade_date": "2024-01-03", "open": 1.5, "high": 2.0, "low": 1.0, "close": 1.8, "volume": 12}]
    assert c.get("/local/bars/NOPE").status_code == 404
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_bt_api.py -q`
Expected: FAIL — 404/405 on `/local/backtests` (route missing) and the startup-recovery test.

- [ ] **Step 3: Implement**

In `aitrader/api.py` add imports:

```python
from datetime import date

from pydantic import BaseModel

from aitrader.backtest.runner import (
    INTERRUPTED, SOURCE, BacktestBusyError, BacktestInputError, BacktestRequest, create_run, execute_run,
)
from aitrader.backtest.types import MoneyRules
```

Above `create_app`:

```python
_DEFAULT_MONEY = MoneyRules()


class BacktestBody(BaseModel):
    strategy: str
    start: Optional[date] = None
    end: Optional[date] = None
    capital: float = _DEFAULT_MONEY.capital
    risk_per_trade: float = _DEFAULT_MONEY.risk_per_trade
    daily_loss_limit: float = _DEFAULT_MONEY.daily_loss_limit
    max_positions: int = _DEFAULT_MONEY.max_positions
    max_position_value: float = _DEFAULT_MONEY.max_position_value
```

Inside `create_app`, right after `pending_states` is created:

```python
    with deps.engine.begin() as conn:
        repo.fail_interrupted_backtests(conn, INTERRUPTED)
```

Before `return app`:

```python
    @app.post("/local/backtests", status_code=202)
    def start_backtest(body: BacktestBody, background: BackgroundTasks):
        money = MoneyRules(capital=body.capital, risk_per_trade=body.risk_per_trade,
                           daily_loss_limit=body.daily_loss_limit, max_positions=body.max_positions,
                           max_position_value=body.max_position_value)
        try:
            run_id = create_run(deps.engine, BacktestRequest(body.strategy, body.start, body.end, money))
        except BacktestInputError as exc:
            raise HTTPException(400, str(exc)) from None
        except BacktestBusyError as exc:
            raise HTTPException(409, str(exc)) from None
        background.add_task(execute_run, deps.engine, run_id)
        return {"id": run_id}

    @app.get("/local/backtests")
    def list_backtests(limit: int = Query(20, ge=1, le=200)):
        with deps.engine.connect() as conn:
            return repo.list_backtest_runs(conn, limit)

    @app.get("/local/backtests/{run_id}")
    def get_backtest(run_id: int):
        with deps.engine.connect() as conn:
            run = repo.get_backtest_run(conn, run_id)
        if run is None:
            raise HTTPException(404, f"No backtest {run_id}")
        return run

    @app.get("/local/bars/{symbol}")
    def bars(symbol: str, start: Optional[date] = None, end: Optional[date] = None):
        with deps.engine.connect() as conn:
            rows = repo.daily_bars_for_symbol(conn, symbol, source=SOURCE, start=start, end=end)
        if rows is None:
            raise HTTPException(404, f"Unknown symbol {symbol!r}")
        return rows
```

`README.md` — add after the Data tab section:

```markdown
## Backtests (Part 2)

Open the dashboard's **Backtest** tab (visible while this service runs), pick a strategy
(A Momentum, B Trend pullback, C Breakout), optionally a date range and money settings, and click Run.
Defaults: ₹20,000 capital, ₹400 risk per trade, ₹1,000 daily loss stop, 3 positions, ₹7,000 per position,
1:2 risk/reward, 20-day time stop, Zerodha delivery charges + 0.05% slippage.

- Needs the stock list and about 10 years of history loaded first (Data tab → Load history).
- The last 2 years are reported separately as the "unseen" period; judge strategies there.
- NIFTY/BANKNIFTY results are index signal quality in R — not option profit.
- Results use today's Nifty 200 list (survivorship bias) and are past behaviour only — not a promise of profit.
- API: `POST /local/backtests`, `GET /local/backtests`, `GET /local/backtests/{id}`, `GET /local/bars/{symbol}`.
```

- [ ] **Step 4: Run tests and lint**

Run: `.venv/Scripts/python -m pytest -q && .venv/Scripts/ruff check .`
Expected: whole suite passes; ruff clean.

- [ ] **Step 5: Commit**

```bash
git add aitrader/api.py README.md tests/test_bt_api.py
git commit -m "Backtest: API endpoints and restart recovery"
```

---

### Task 11: Backtest tab in the dashboard

**Files (in `D:\ai-trader`):**
- Modify: `vercel-demo/package.json` (+ lock) via `npm install --save-exact lightweight-charts@5.2.1`
- Create: `vercel-demo/src/components/BacktestTab.jsx`, `vercel-demo/src/components/backtest/format.js`, `vercel-demo/src/components/backtest/EquityChart.jsx`, `vercel-demo/src/components/backtest/TradeChart.jsx`
- Modify: `vercel-demo/src/App.jsx`, `README.md`

**Interfaces:**
- Consumes: the Task 10 endpoints. JSON numbers arrive as numbers (FastAPI encodes `Decimal` as float); dates as `YYYY-MM-DD`.
- Produces: tab key `backtest`, shown only when `localService` is true.

There is no JS test runner in this repo; the gate is `npm run build` plus a manual check (Step 4).

- [ ] **Step 1: Install the chart library**

Run (in `D:\ai-trader\vercel-demo`): `npm install --save-exact lightweight-charts@5.2.1`
Expected: `package.json` lists `"lightweight-charts": "5.2.1"`.

- [ ] **Step 2: Write the components**

```js
// src/components/backtest/format.js
const isNum = (v) => v !== null && v !== undefined && v !== "" && !Number.isNaN(Number(v));

export const rupees = (v) =>
  isNum(v) ? `₹${Number(v).toLocaleString("en-IN", { maximumFractionDigits: 0 })}` : "—";
export const pct = (v) => (isNum(v) ? `${(Number(v) * 100).toFixed(1)}%` : "—");
export const num = (v, digits = 2) => (isNum(v) ? Number(v).toFixed(digits) : "—");
export const count = (v) => (isNum(v) ? String(v) : "—");

export function shiftDate(iso, days) {
  const d = new Date(`${iso}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

export const chartOptions = {
  autoSize: true,
  layout: { background: { color: "transparent" }, textColor: "#8a8f98" },
  grid: { vertLines: { color: "rgba(128,128,128,0.15)" }, horzLines: { color: "rgba(128,128,128,0.15)" } },
  rightPriceScale: { borderVisible: false },
  timeScale: { borderVisible: false },
};
```

```jsx
// src/components/backtest/EquityChart.jsx
import { useEffect, useRef } from "react";
import { LineSeries, createChart, createSeriesMarkers } from "lightweight-charts";
import { chartOptions } from "./format.js";

export default function EquityChart({ equity, unseenFrom }) {
  const ref = useRef(null);

  useEffect(() => {
    if (!ref.current || equity.length === 0) return undefined;
    const chart = createChart(ref.current, chartOptions);
    const strategy = chart.addSeries(LineSeries, { color: "#2563eb", lineWidth: 2, title: "Strategy" });
    strategy.setData(equity.map((p) => ({ time: p.trade_date, value: Number(p.equity) })));
    const bench = chart.addSeries(LineSeries, { color: "#9ca3af", lineWidth: 1, title: "NIFTY 50" });
    bench.setData(equity.map((p) => ({ time: p.trade_date, value: Number(p.benchmark_equity) })));
    const boundary = equity.find((p) => p.trade_date >= unseenFrom);
    if (boundary && boundary.trade_date > equity[0].trade_date) {
      createSeriesMarkers(strategy, [
        { time: boundary.trade_date, position: "aboveBar", color: "#f59e0b", shape: "arrowDown",
          text: "unseen period starts" },
      ]);
    }
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [equity, unseenFrom]);

  return <div ref={ref} style={{ width: "100%", height: 300 }} />;
}
```

```jsx
// src/components/backtest/TradeChart.jsx
import { useEffect, useRef, useState } from "react";
import { CandlestickSeries, createChart, createSeriesMarkers } from "lightweight-charts";
import { chartOptions, num, rupees, shiftDate } from "./format.js";

export default function TradeChart({ trade, onClose }) {
  const ref = useRef(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let chart = null;
    let cancelled = false;
    setError(null);
    const start = shiftDate(trade.signal_date, -90); // about 60 trading days before the signal
    const end = shiftDate(trade.exit_date, 15); // about 10 trading days after the exit
    fetch(`/local/bars/${encodeURIComponent(trade.symbol)}?start=${start}&end=${end}`)
      .then((res) => {
        if (!res.ok) throw new Error(`bars returned ${res.status}`);
        return res.json();
      })
      .then((bars) => {
        if (cancelled || !ref.current) return;
        chart = createChart(ref.current, chartOptions);
        const series = chart.addSeries(CandlestickSeries);
        series.setData(bars.map((b) => ({ time: b.trade_date, open: Number(b.open), high: Number(b.high),
                                          low: Number(b.low), close: Number(b.close) })));
        [["entry", trade.entry_price, "#2563eb"], ["stop", trade.stop, "#dc2626"],
         ["target", trade.target, "#16a34a"]].forEach(([title, price, color]) =>
          series.createPriceLine({ price: Number(price), color, lineWidth: 1, lineStyle: 2, title }));
        const won = Number(trade.pnl) >= 0;
        createSeriesMarkers(series, [
          { time: trade.entry_date, position: "belowBar", color: "#2563eb", shape: "arrowUp",
            text: `Buy ${num(trade.entry_price)}` },
          { time: trade.exit_date, position: "aboveBar", color: won ? "#16a34a" : "#dc2626", shape: "arrowDown",
            text: `Sell ${num(trade.exit_price)} (${trade.exit_reason})` },
        ]);
        chart.timeScale().fitContent();
      })
      .catch((e) => setError(e.message));
    return () => {
      cancelled = true;
      if (chart) chart.remove();
    };
  }, [trade]);

  return (
    <div className="trade-chart">
      <p>
        <strong>{trade.symbol}</strong> — {trade.reason} Result: {rupees(trade.pnl)} ({num(trade.r_multiple)}R).{" "}
        <button type="button" onClick={onClose}>Close chart</button>
      </p>
      {error && <p className="error-text">{error}</p>}
      <div ref={ref} style={{ width: "100%", height: 360 }} />
    </div>
  );
}
```

```jsx
// src/components/BacktestTab.jsx
import { useEffect, useState } from "react";
import EquityChart from "./backtest/EquityChart.jsx";
import TradeChart from "./backtest/TradeChart.jsx";
import { count, num, pct, rupees } from "./backtest/format.js";

const STRATEGIES = [
  { key: "momentum", label: "A — Momentum" },
  { key: "pullback", label: "B — Trend pullback" },
  { key: "breakout", label: "C — Breakout" },
];
const MONEY = [
  { key: "capital", label: "Starting capital (₹)", def: 20000 },
  { key: "risk_per_trade", label: "Risk per trade (₹)", def: 400 },
  { key: "daily_loss_limit", label: "Daily loss stop (₹)", def: 1000 },
  { key: "max_positions", label: "Max open positions", def: 3 },
  { key: "max_position_value", label: "Max per position (₹)", def: 7000 },
];
const METRICS = [
  ["return_pct", "Return", pct],
  ["cagr", "Yearly return (CAGR)", pct],
  ["max_drawdown_pct", "Biggest fall", pct],
  ["max_drawdown_amount", "Biggest fall (₹)", rupees],
  ["worst_month", "Worst month", pct],
  ["trades", "Trades", count],
  ["win_rate", "Win rate", pct],
  ["avg_win", "Average win", rupees],
  ["avg_loss", "Average loss", rupees],
  ["avg_win_r", "Average win (R)", num],
  ["avg_loss_r", "Average loss (R)", num],
  ["expectancy_r", "Expectancy (R per trade, after charges)", num],
  ["profit_factor", "Profit factor", num],
  ["total_charges", "Charges paid", rupees],
  ["charges_pct_of_gross", "Charges ÷ gross P&L", pct],
  ["longest_losing_streak", "Longest losing streak", count],
  ["pct_days_invested", "Days with money invested", pct],
];
const strategyLabel = (key) => STRATEGIES.find((s) => s.key === key)?.label ?? key;
const muted = { color: "var(--text-muted)" };

async function getJson(path) {
  const res = await fetch(path);
  if (!res.ok) throw new Error(`${path} returned ${res.status}`);
  return res.json();
}

function errorText(body, status) {
  if (body && typeof body.detail === "string") return body.detail;
  if (body && body.detail) return JSON.stringify(body.detail);
  return `request failed (${status})`;
}

function RunsTable({ runs, onOpen }) {
  if (runs.length === 0) return <p style={muted}>No backtests yet.</p>;
  return (
    <table>
      <thead>
        <tr>
          <th>#</th><th>Strategy</th><th>Period</th><th>Status</th><th>Return</th><th>Biggest fall</th>
          <th>Checklist</th><th />
        </tr>
      </thead>
      <tbody>
        {runs.map((r) => {
          const whole = r.summary?.whole;
          const passed = (r.summary?.checklist || []).filter((c) => c.passed).length;
          return (
            <tr key={r.id}>
              <td>{r.id}</td>
              <td>{strategyLabel(r.strategy)}</td>
              <td>{r.start_date ?? "—"} → {r.end_date ?? "—"}</td>
              <td>{r.status}</td>
              <td>{pct(whole?.return_pct)}</td>
              <td>{pct(whole?.max_drawdown_pct)}</td>
              <td>{r.summary ? `${passed}/4` : "—"}</td>
              <td><button type="button" onClick={() => onOpen(r.id)}>Open</button></td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

function RunView({ run }) {
  const [trade, setTrade] = useState(null);
  const s = run.summary;
  const title = `Run #${run.id} — ${strategyLabel(run.strategy)}`;
  if (run.status !== "ok" || !s) {
    return (
      <section>
        <h3>{title}</h3>
        <p className={run.status === "failed" ? "error-text" : ""}>
          Status: {run.status}{run.message ? ` — ${run.message}` : ""}
        </p>
      </section>
    );
  }
  const bench = s.benchmark || {};
  const skips = Object.entries(s.skips || {});
  return (
    <section>
      <h3>{title} ({run.start_date} → {run.end_date})</h3>
      {s.warnings.map((w) => <p key={w} className="warning-text">{w}</p>)}

      <h4>Minimum-evidence checklist (not a promise of profit)</h4>
      <ul>{s.checklist.map((c) => <li key={c.key}>{c.passed ? "✓" : "✗"} {c.label}</li>)}</ul>

      <h4>Results</h4>
      <table>
        <thead>
          <tr><th /><th>Earlier years</th><th>Unseen years (from {run.unseen_from})</th><th>Whole run</th></tr>
        </thead>
        <tbody>
          {METRICS.map(([key, label, fmt]) => (
            <tr key={key}>
              <td>{label}</td>
              <td>{fmt(s.earlier?.[key])}</td>
              <td>{fmt(s.unseen?.[key])}</td>
              <td>{fmt(s.whole?.[key])}</td>
            </tr>
          ))}
          <tr>
            <td>NIFTY 50 buy &amp; hold — return</td><td />
            <td>{pct(bench.unseen?.return_pct)}</td><td>{pct(bench.whole?.return_pct)}</td>
          </tr>
          <tr>
            <td>NIFTY 50 buy &amp; hold — biggest fall</td><td />
            <td>{pct(bench.unseen?.max_drawdown_pct)}</td><td>{pct(bench.whole?.max_drawdown_pct)}</td>
          </tr>
        </tbody>
      </table>

      <h4>Account value vs NIFTY 50</h4>
      <EquityChart equity={run.equity} unseenFrom={run.unseen_from} />

      <h4>By year</h4>
      <table>
        <thead><tr><th>Year</th><th>Return</th><th>Biggest fall</th><th>Trades</th></tr></thead>
        <tbody>
          {s.years.map((y) => (
            <tr key={y.year}>
              <td>{y.year}</td><td>{pct(y.return_pct)}</td><td>{pct(y.max_drawdown_pct)}</td><td>{y.trades}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <h4>Skipped signals</h4>
      {skips.length === 0 ? <p style={muted}>None.</p> : (
        <ul>{skips.map(([reason, n]) => <li key={reason}>{reason.replaceAll("_", " ")}: {n}</li>)}</ul>
      )}

      {s.index_signals.length > 0 && (
        <>
          <h4>NIFTY / BANKNIFTY signals — index signal quality in R, not option profit</h4>
          <table>
            <thead><tr><th>Index</th><th>Idea</th><th>Period</th><th>Signals</th><th>Win rate</th><th>Avg R</th></tr></thead>
            <tbody>
              {s.index_signals.map((r) => (
                <tr key={`${r.symbol}-${r.direction}-${r.period}`}>
                  <td>{r.symbol}</td><td>{r.direction === "long" ? "Bullish (CE)" : "Bearish (PE)"}</td>
                  <td>{r.period}</td><td>{r.signals}</td><td>{pct(r.win_rate)}</td><td>{num(r.avg_r)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      <h4>Trades ({run.trades.length}) — click a row to see its chart</h4>
      {trade && <TradeChart trade={trade} onClose={() => setTrade(null)} />}
      <table>
        <thead>
          <tr>
            <th>Symbol</th><th>Entry</th><th>Exit</th><th>Buy</th><th>Stop</th><th>Target</th><th>Sell</th>
            <th>Why it ended</th><th>Result</th><th>R</th><th>Why it was taken</th>
          </tr>
        </thead>
        <tbody>
          {run.trades.map((t) => (
            <tr key={t.id} onClick={() => setTrade(t)} style={{ cursor: "pointer" }}>
              <td>{t.symbol}</td><td>{t.entry_date}</td><td>{t.exit_date}</td><td>{num(t.entry_price)}</td>
              <td>{num(t.stop)}</td><td>{num(t.target)}</td><td>{num(t.exit_price)}</td>
              <td>{t.exit_reason.replaceAll("_", " ")}</td><td>{rupees(t.pnl)}</td><td>{num(t.r_multiple)}</td>
              <td>{t.reason}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

export default function BacktestTab() {
  const [form, setForm] = useState({
    strategy: "pullback", start: "", end: "", ...Object.fromEntries(MONEY.map((m) => [m.key, m.def])),
  });
  const [runs, setRuns] = useState([]);
  const [run, setRun] = useState(null);
  const [runningId, setRunningId] = useState(null);
  const [error, setError] = useState(null);

  const loadRuns = () => getJson("/local/backtests?limit=50").then(setRuns).catch((e) => setError(e.message));
  const openRun = (id) => getJson(`/local/backtests/${id}`).then(setRun).catch((e) => setError(e.message));
  const set = (key) => (event) => setForm({ ...form, [key]: event.target.value });

  useEffect(() => {
    loadRuns();
  }, []);

  useEffect(() => {
    if (runningId === null) return undefined;
    const timer = setInterval(() => {
      getJson(`/local/backtests/${runningId}`)
        .then((r) => {
          if (r.status !== "running") {
            setRunningId(null);
            setRun(r);
            loadRuns();
          }
        })
        .catch((e) => {
          setRunningId(null);
          setError(e.message);
        });
    }, 2000);
    return () => clearInterval(timer);
  }, [runningId]);

  const submit = async (event) => {
    event.preventDefault();
    setError(null);
    const body = { strategy: form.strategy, start: form.start || null, end: form.end || null };
    MONEY.forEach((m) => {
      body[m.key] = Number(form[m.key]);
    });
    try {
      const res = await fetch("/local/backtests", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await res.json().catch(() => null);
      if (!res.ok) throw new Error(errorText(data, res.status));
      setRun(null);
      setRunningId(data.id);
    } catch (e) {
      setError(e.message);
    }
  };

  return (
    <div className="backtest-tab">
      <form onSubmit={submit} style={{ display: "flex", flexWrap: "wrap", gap: "0.75rem", alignItems: "end" }}>
        <label>
          Strategy{" "}
          <select value={form.strategy} onChange={set("strategy")}>
            {STRATEGIES.map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}
          </select>
        </label>
        <label>From <input type="date" value={form.start} onChange={set("start")} /></label>
        <label>To <input type="date" value={form.end} onChange={set("end")} /></label>
        {MONEY.map((m) => (
          <label key={m.key}>
            {m.label} <input type="number" min="1" value={form[m.key]} onChange={set(m.key)} style={{ width: "7rem" }} />
          </label>
        ))}
        <button type="submit" disabled={runningId !== null}>{runningId !== null ? "Running…" : "Run backtest"}</button>
      </form>
      <p style={muted}>
        Leave the dates empty to use all loaded history. Results show past behaviour only — not a promise of profit.
      </p>
      {error && <p className="error-text">{error}</p>}
      {runningId !== null && <p>Backtest #{runningId} is running…</p>}
      {run && <RunView run={run} />}
      <h3>Past runs</h3>
      <RunsTable runs={runs} onOpen={openRun} />
    </div>
  );
}
```

`src/App.jsx`:
- add `import BacktestTab from "./components/BacktestTab.jsx";` next to the `DataTab` import;
- change the tabs line to
  `const tabs = localService ? [...TABS, { key: "data", label: "Data" }, { key: "backtest", label: "Backtest" }] : TABS;`
- add a render branch before `activeTab === "data"`: `{activeTab === "backtest" ? (<BacktestTab />) : activeTab === "data" ? (` … keeping the rest of the chain unchanged.

`README.md` (ai-trader), in the Data tab note: add "A **Backtest** tab appears too: it runs strategy backtests on the local service and shows each trade on a chart."

- [ ] **Step 3: Build**

Run (in `D:\ai-trader\vercel-demo`): `npm run build`
Expected: build succeeds with no errors.

- [ ] **Step 4: Manual check**

With the core service (`python -m aitrader serve`, restarted to pick up the new code), the dashboard dev server, and Docker Postgres running, open `http://localhost:5173`, go to **Backtest**, click **Run backtest**. With no history loaded the run must end as `failed — Load the stock list and price history first (Data tab)`. With history loaded it must show results, the chart, and a clickable trades table. Also run `python -m pytest -q` in `D:\ai-trader`.

- [ ] **Step 5: Commit**

```bash
git -C D:/ai-trader add vercel-demo/package.json vercel-demo/package-lock.json vercel-demo/src README.md
git -C D:/ai-trader commit -m "Add Backtest tab for the local AI Trader core service"
```

---

## Self-review notes

- Spec coverage: §3 units → Tasks 2–10; §4 rules → Tasks 4–5; §5 → Task 3; §6 → Task 6; §7 → Task 4; §8 → Tasks 8–10; §9 → Task 7; §10 → Task 11; §11 → Tasks 9–10; §12 → tests throughout; §13 → Task 1.
- Branches: `part2-backtest` in `D:\ai-trader-core` from `main`; `backtest-tab` in `D:\ai-trader` from `master` (Task 1's DataTab label change and Task 11 land there).
