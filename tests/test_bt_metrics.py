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


def _seq_trades(pnls):
    from datetime import timedelta
    start = date(2024, 1, 1)
    out = []
    for i, pnl in enumerate(pnls, start=1):
        exit_day = start + timedelta(days=10 * i)
        t = trade(pnl, float(pnl), entry=exit_day - timedelta(days=5))
        t.exit_date = exit_day
        out.append(t)
    return out


def test_loss_streak_alerts():
    from aitrader.backtest.metrics import loss_streak_alerts
    got = loss_streak_alerts(_seq_trades([-1, -1, -1, 2, -1, -1, 1]))
    assert got["alerts"] == 2 and got["after_trades"] == 3
    assert got["after_win_rate"] == pytest.approx(2 / 3) and got["after_avg_r"] == pytest.approx(2 / 3)


def test_no_trades_no_alerts():
    from aitrader.backtest.metrics import loss_streak_alerts
    assert loss_streak_alerts([]) == {"alerts": 0, "after_trades": 0, "after_win_rate": None, "after_avg_r": None}


def test_summary_has_loss_streaks():
    pts = [EquityPoint(date(2024, 1, 2), 20000, 0, 20000), EquityPoint(date(2024, 3, 2), 20000, 0, 20000)]
    s = summarize(sim(pts, _seq_trades([-1, -1, 1])), [20000, 20000], MoneyRules(), date(2024, 1, 1))
    assert s["loss_streaks"]["whole"]["alerts"] == 1 and "unseen" in s["loss_streaks"]
