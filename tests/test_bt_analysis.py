import pytest

from aitrader.backtest.analysis import (
    comparison_markdown, r_split, regime_table, sizing, stop_bucket, stop_distance_table,
)


def trade(period="earlier", entry=100.0, stop=95.0, shares=10, pnl=40.0, charges=10.0, regime=True, cut=None):
    r = pnl / (shares * (entry - stop))
    return {"period": period, "entry_price": entry, "stop": stop, "shares": shares, "pnl": pnl, "charges": charges,
            "r_multiple": r, "facts": {"regime_on": regime, "size_cut": cut, "risk_amount": 50.0}}


def test_r_split_gross_charges_net():
    s = r_split([trade(pnl=40, charges=10), trade(pnl=-60, charges=10)])  # risk ₹50 each
    assert s["trades"] == 2
    assert s["gross_r"] == pytest.approx(((50 / 50) + (-50 / 50)) / 2)
    assert s["charges_r"] == pytest.approx(0.2) and s["net_r"] == pytest.approx(-0.2)


def test_stop_buckets_use_the_actual_entry_price():
    assert [stop_bucket(100, s) for s in (98.5, 98, 97.5, 95, 92, 85)] == [
        "<2%", "2–3%", "2–3%", "4–6%", "6–10%", ">10%"]
    rows = stop_distance_table([trade(stop=99), trade(stop=99, period="unseen"), trade(stop=95)])
    first = rows[0]
    assert (first["bucket"], first["period"], first["trades"]) == ("<2%", "earlier", 1)
    assert first["avg_position_value"] == pytest.approx(1000) and first["avg_risk"] == pytest.approx(10)


def test_regime_table_splits_by_signal_day_regime_and_period():
    rows = regime_table([trade(regime=True), trade(regime=False, pnl=-50), trade(regime=False, period="unseen")])
    by = {(r["period"], r["regime"]): r for r in rows}
    assert by[("earlier", "below")]["trades"] == 1 and by[("earlier", "below")]["pnl"] == -50
    assert by[("earlier", "above")]["win_rate"] == 1.0 and by[("unseen", "below")]["trades"] == 1


def test_sizing_counts_cuts_by_cap_or_cash():
    s = sizing([trade(cut="cap"), trade(cut="cash"), trade(), trade()])
    assert s["pct_cut"] == pytest.approx(0.5) and s["avg_position_value"] == pytest.approx(1000)


def summary(ret):
    p = {"return_pct": ret, "cagr": 0.01, "max_drawdown_pct": 0.2, "worst_month": -0.05, "trades": 10,
         "win_rate": 0.4, "expectancy_r": 0.1, "profit_factor": 1.2, "total_charges": 500,
         "charges_pct_of_gross": 0.3}
    return {"earlier": p, "unseen": dict(p, return_pct=ret / 2),
            "checklist": [{"passed": True}, {"passed": False}, {"passed": True}, {"passed": False}]}


def ranked(trades, checks_passed, whole_exp, unseen_exp):
    return {"summary": {"whole": {"trades": trades, "expectancy_r": whole_exp},
                        "unseen": {"trades": 1, "expectancy_r": unseen_exp},
                        "checklist": [{"passed": i < checks_passed} for i in range(4)]}}


def test_best_run_ignores_tiny_samples_then_prefers_checklist_then_whole_expectancy():
    from aitrader.backtest.analysis import best_run
    rows = [("tiny", ranked(5, 4, 2.0, 0.9)),          # too few trades, however good it looks
            ("one-unseen", ranked(40, 2, -0.16, 0.87)),  # great unseen R from a single trade
            ("steady", ranked(300, 2, 0.07, -0.26)),
            ("base", ranked(800, 1, 0.5, 0.5))]
    assert best_run(rows)[0] == "steady"
    assert best_run([("tiny", ranked(5, 4, 2.0, 0.9))])[0] == "tiny"  # nothing qualifies: fall back to all


def test_comparison_has_a_row_per_run_and_earlier_and_unseen_tables():
    md = comparison_markdown([("A-base", {"summary": summary(-0.3)}), ("A2-100k", {"summary": summary(0.2)})])
    assert "## Earlier years" in md and "## Unseen years" in md
    assert md.count("| A-base |") == 2 and "2/4" in md and "-30.0%" in md and "-15.0%" in md
