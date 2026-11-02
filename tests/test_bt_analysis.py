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


def gap_trade(open_, close=100.0, period="earlier", pnl=40.0):
    t = trade(period=period, pnl=pnl)
    t["facts"].update(entry_open=open_, signal_close=close)
    return t


def test_entry_gap_buckets_from_entry_open_vs_signal_close():
    from aitrader.backtest.analysis import entry_gap_table, gap_bucket
    assert [gap_bucket(o, 100) for o in (99.5, 100, 100.9, 101, 102.9, 103, 105, 106)] == [
        "<0%", "0–1%", "0–1%", "1–3%", "1–3%", "3–5%", ">5%", ">5%"]
    rows = entry_gap_table([gap_trade(106), gap_trade(106, period="unseen", pnl=-50), gap_trade(99), trade()])
    by = {(r["bucket"], r["period"]): r for r in rows}
    assert by[(">5%", "earlier")]["trades"] == 1 and by[(">5%", "unseen")]["win_rate"] == 0.0
    assert by[("<0%", "earlier")]["gross_r"] == pytest.approx(1.0) and ("unknown", "earlier") in by


def test_checklist_result_counts_na():
    from aitrader.backtest.analysis import checklist_result
    s = {"checklist": [{"passed": False}, {"passed": None, "na": True}, {"passed": None, "na": True},
                       {"passed": None, "na": True}]}
    assert checklist_result(s) == "0/4 (3 n/a)"


def test_comparison_shows_days_invested():
    md = comparison_markdown([("C-base", {"summary": {**summary(0.1), "earlier": {**summary(0.1)["earlier"],
                                                                                  "pct_days_invested": 0.148}}})])
    assert "% days invested" in md and "14.8%" in md


def vol_trade(atr_pct, symbol="AAA", period="earlier", pnl=40.0, mfe=None, exit_reason="stop"):
    t = trade(period=period, pnl=pnl)
    t.update(symbol=symbol, exit_reason=exit_reason)
    t["facts"].update(atr_pct=atr_pct, mfe_r=mfe)
    return t


def test_volatility_table_buckets_atr_over_close_at_entry():
    from aitrader.backtest.analysis import volatility_table, vol_bucket
    assert [vol_bucket(v) for v in (0.019, 0.02, 0.035, 0.05, 0.061)] == ["<2%", "2–3%", "3–4%", "4–6%", ">6%"]
    rows = volatility_table([vol_trade(0.07), vol_trade(0.07, period="unseen", pnl=-50), vol_trade(None)])
    by = {(r["bucket"], r["period"]): r for r in rows}
    assert by[(">6%", "earlier")]["trades"] == 1 and by[(">6%", "unseen")]["win_rate"] == 0.0
    assert ("unknown", "earlier") in by


def test_top_stocks_and_top_five_share():
    from aitrader.backtest.analysis import top_stocks
    ts = ([vol_trade(0.03, "PRESTIGE", pnl=100)] * 3 + [vol_trade(0.03, "IDEA", pnl=-40)] * 2
          + [vol_trade(0.03, f"S{i}", pnl=10) for i in range(10)])
    out = top_stocks(ts)
    assert [r["symbol"] for r in out["rows"]][:2] == ["PRESTIGE", "IDEA"] and len(out["rows"]) == 10
    assert out["rows"][0]["trades"] == 3 and out["rows"][0]["pnl"] == 300 and out["rows"][1]["win_rate"] == 0.0
    total = 300 - 80 + 100
    top5 = 300 + 10 + 10 + 10 + 10  # the five biggest contributors by ₹ P&L
    assert out["top5_pnl"] == top5 and out["total_pnl"] == total and out["top5_share"] == pytest.approx(top5 / total)


def test_mfe_by_exit_reason():
    from aitrader.backtest.analysis import mfe_by_exit
    rows = mfe_by_exit([vol_trade(0.03, mfe=2.0, exit_reason="time"), vol_trade(0.03, mfe=1.0, exit_reason="time"),
                        vol_trade(0.03, mfe=0.2, exit_reason="stop"), vol_trade(0.03, exit_reason="stop")])
    by = {r["exit_reason"]: r for r in rows}
    assert by["time"]["avg_mfe_r"] == pytest.approx(1.5) and by["time"]["trades"] == 2
    assert by["stop"]["avg_mfe_r"] == pytest.approx(0.2) and by["stop"]["with_mfe"] == 1
