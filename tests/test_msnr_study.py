"""MSNR v2 research study: NIFTY 50 universe, period metrics, pre-registered variant selection."""
from datetime import date

import pytest

from aitrader.backtest import msnr_study as M


def test_nifty50_list_has_fifty_unique_symbols():
    syms = M.load_nifty50()
    assert len(syms) == 50 == len(set(syms)) and "RELIANCE" in syms and "TMPV" in syms


def test_research_money_follows_the_brief():
    m = M.RESEARCH_MONEY
    assert (m.capital, m.risk_mode, m.risk_pct, m.max_positions, m.max_position_value, m.daily_loss_pct) == \
        (100_000.0, "percent", 0.005, 3, 25_000.0, 0.015)
    assert (m.reward_multiple, m.time_stop_days, m.regime_filter) == (2.0, 20, True)


def test_periods_are_chronological_and_contiguous():
    (n1, s1, e1), (n2, s2, e2), (n3, s3, e3) = M.PERIODS
    assert (n1, n2, n3) == ("train", "validation", "holdout")
    assert (s1, e1, s2, e2, s3) == (date(2017, 8, 14), date(2021, 12, 31), date(2022, 1, 1), date(2023, 12, 31),
                                    date(2024, 1, 1))


def eq(day, equity, bench):
    return {"trade_date": day, "equity": equity, "benchmark_equity": bench}


def tr(day, r, pnl, charges=10.0, ratio=0.05):
    return {"entry_date": day, "r_multiple": r, "pnl": pnl, "charges": charges, "facts": {"charge_to_risk": ratio}}


def test_period_metrics_use_only_that_period():
    run = {"equity": [eq(date(2021, 12, 30), 100_000, 100_000), eq(date(2021, 12, 31), 110_000, 105_000),
                      eq(date(2022, 1, 3), 99_000, 105_000), eq(date(2022, 6, 1), 121_000, 115_500)],
           "trades": [tr(date(2021, 12, 30), 2.0, 900), tr(date(2022, 1, 3), -1.0, -500),
                      tr(date(2022, 2, 1), 3.0, 1500), tr(date(2022, 3, 1), -1.0, -500)]}
    m = M.period_metrics(run, date(2022, 1, 1), date(2022, 12, 31))
    assert m["trades"] == 3 and m["win_rate"] == pytest.approx(1 / 3)
    assert m["expectancy_r"] == pytest.approx(1 / 3) and m["profit_factor"] == pytest.approx(1.5)
    assert m["avg_win_r"] == 3.0 and m["avg_loss_r"] == -1.0 and m["charges"] == 30.0
    assert m["cost_to_risk"] == pytest.approx(0.05)
    assert m["return_pct"] == pytest.approx(0.10)              # from 110,000 (the last value before) to 121,000
    assert m["max_drawdown_pct"] == pytest.approx(0.10)        # 110,000 → 99,000
    assert m["bench_return_pct"] == pytest.approx(0.10)


def test_period_metrics_with_no_trades():
    m = M.period_metrics({"equity": [], "trades": []}, date(2022, 1, 1), date(2022, 12, 31))
    assert m["trades"] == 0 and m["expectancy_r"] is None and m["profit_factor"] is None


def P(exp_r, pf=1.0, trades=100, dd=0.2):
    return {"expectancy_r": exp_r, "profit_factor": pf, "trades": trades, "max_drawdown_pct": dd}


def test_selection_needs_better_training_stable_neighbours_and_validation():
    base = {"train": P(-0.10, 0.85), "validation": P(-0.05, 0.9)}
    rows = [
        {"label": "a1", "exp": 1, "group": "a", "train": P(0.00, 1.0), "validation": P(0.02, 1.0)},
        {"label": "a2", "exp": 1, "group": "a", "train": P(-0.05, 0.9), "validation": P(-0.2, 0.7)},
        {"label": "b", "exp": 1, "group": None, "train": P(0.05, 1.1), "validation": P(-0.08, 0.8)},  # fails val
        {"label": "c", "exp": 2, "group": "c", "train": P(0.10, 1.2), "validation": P(0.1, 1.2)},     # lone peak
        {"label": "c2", "exp": 2, "group": "c", "train": P(-0.20, 0.7), "validation": P(0.1, 1.2)},
        {"label": "d", "exp": 3, "group": None, "train": P(0.20, 1.3, trades=20), "validation": P(0.1, 1.2)},
        {"label": "e", "exp": 4, "group": None, "train": P(0.20, 1.3, dd=0.4), "validation": P(0.1, 1.2)},
    ]
    chosen, notes = M.select_variants(base, rows)
    assert chosen == {1: "a1"}
    text = "\n".join(notes)
    assert "b:" in text and "validation" in text and "c:" in text and "neighbour" in text
    assert "d:" in text and "too few" in text and "e:" in text and "drawdown" in text


def test_feature_buckets_split_by_fixed_edges():
    trades = [{"r_multiple": r, "facts": {"moved_atr": m}} for r, m in
              [(2.0, 0.5), (-1.0, 0.5), (-1.0, 1.5), (2.0, 2.5), (2.0, 3.0), (-1.0, None)]]
    rows = M.feature_buckets(trades, "moved_atr", [1.0, 2.0])
    assert [(b["bucket"], b["n"]) for b in rows] == [("< 1", 2), ("1 – 2", 1), ("≥ 2", 2), ("unknown", 1)]
    assert rows[2]["expectancy_r"] == 2.0 and rows[0]["win_rate"] == 0.5
