from datetime import datetime

from aitrader.auth import IST
from aitrader.backtest import study
from aitrader.backtest.types import NEW_RUN_MONEY, MoneyRules
from aitrader.calendar import TradingCalendar
from aitrader.db import repo
from aitrader.jobs import JobDeps
from bt_helpers import flat
from fakes import FakeBarSource
from test_bt_runner import breakout_series, seed


def test_study_sets_use_the_given_settings():
    a = {label: (strategy, money) for label, strategy, money, _ in study.STUDIES["a"]}
    assert a["A-base"] == ("momentum", MoneyRules()) and a["A-fix"] == ("momentum", NEW_RUN_MONEY)
    assert a["A2-100k"] == ("momentum_a2", NEW_RUN_MONEY)
    assert a["A2-20k"][1].capital == 20_000 and a["A2-20k"][1].max_position_value == 7_000
    assert a["A2-20k"][1].regime_filter and a["A2-20k"][1].risk_mode == "percent"
    b = {label: strategy for label, strategy, _, _ in study.STUDIES["b"]}
    assert b == {"B-base": "pullback", "B-fix": "pullback", "B2-100k": "pullback_b2", "B2-20k": "pullback_b2"}
    old = MoneyRules()
    assert (old.capital, old.risk_per_trade, old.max_positions, old.max_position_value, old.daily_loss_limit,
            old.regime_filter, old.max_charge_to_risk, old.max_drawdown_pause) == (
        20_000, 400, 3, 7_000, 1_000, False, None, None)


def test_study_runs_scan_first_writes_files_and_compares(engine, tmp_path):
    series = {"NIFTY 50": flat(280, volume=0), "BRK": breakout_series()}
    series.update({f"FL{i}": flat(280) for i in range(1, 5)})
    seed(engine, series)
    deps = JobDeps(engine=engine, kite=lambda: None, bar_source=FakeBarSource({}), calendar=TradingCalendar(),
                   now=lambda: datetime(2026, 10, 30, 18, 0, tzinfo=IST))
    lines = []
    result = study.run_study(deps, ["b"], tmp_path, log=lines.append)
    with engine.connect() as conn:
        assert repo.last_run_date(conn, "scan_history") is not None
    assert [label for label, _ in result["b"]] == ["B-base", "B-fix", "B2-100k", "B2-20k"]
    names = {p.name for p in tmp_path.iterdir()}
    assert "comparison-B.md" in names and "comparison.md" in names
    for label in ("B-base", "B2-20k"):
        stem = next(n for n in names if n.startswith(label) and n.endswith(".md"))[:-3]
        assert {f"{stem}-trades.csv", f"{stem}-equity.csv"} <= names
    text = (tmp_path / "comparison.md").read_text(encoding="utf-8")
    assert text.count("| B2-100k |") == 2 and "Unseen years" in text
    assert any("| B-base |" in line for line in lines)


def test_reproduction_check_compares_trades_and_return():
    ok = study.reproduction_line("A-base", {"whole": {"trades": 524, "return_pct": -0.35720}},
                                 21, {"whole": {"trades": 524, "return_pct": -0.357204}})
    bad = study.reproduction_line("A-base", {"whole": {"trades": 520, "return_pct": -0.30}},
                                  21, {"whole": {"trades": 524, "return_pct": -0.357204}})
    assert ok.startswith("✅") and "524 trades" in ok and "-35.7%" in ok
    assert bad.startswith("❌")


def test_study_c_set_copies_run_33_settings_and_reruns_a_b_with_the_new_default(engine):
    labels = [(label, strategy) for label, strategy, _, _ in study.STUDIES["c"]]
    assert labels == [("C-base", "breakout"), ("C-fix", "breakout"), ("C2-100k", "breakout_c2"),
                      ("C2-20k", "breakout_c2"), ("A-fix", "momentum"), ("A2-100k", "momentum_a2"),
                      ("B-fix", "pullback"), ("B2-100k", "pullback_b2")]
    stored = {**__import__("dataclasses").asdict(NEW_RUN_MONEY), "max_charge_to_risk": 0.05}
    with engine.begin() as conn:
        run_id = repo.insert_backtest_run(conn, strategy="breakout", start_date=None, end_date=None,
                                          unseen_from=None, params={"money": stored})
    specs = {label: money for label, _, money, _ in study.STUDIES["c"]}
    with engine.connect() as conn:
        base = study.resolve_money(conn, specs["C-base"], remap={33: run_id})
        fix = study.resolve_money(conn, specs["C-fix"], remap={33: run_id})
        assert study.resolve_money(conn, specs["A-fix"], remap={33: run_id}).max_charge_to_risk == 0.15
        small = study.resolve_money(conn, specs["C2-20k"], remap={33: run_id})
    assert base == MoneyRules(**stored) and fix == MoneyRules(**{**stored, "max_charge_to_risk": 0.15})
    assert (small.capital, small.max_position_value, small.max_charge_to_risk) == (20_000, 7_000, 0.15)
    assert study.STUDIES["c"][0][3] == 33  # C-base must reproduce run #33


def test_study_b3_set_copies_run_36_settings(engine):
    labels = [(label, strategy) for label, strategy, _, _ in study.STUDIES["b3"]]
    assert labels == [("B2-base", "pullback_b2"), ("B2-c15", "pullback_b2"), ("B3-100k", "pullback_b3"),
                      ("B3-20k", "pullback_b3")]
    assert study.STUDIES["b3"][0][3] == 36
    stored = {**__import__("dataclasses").asdict(NEW_RUN_MONEY), "max_charge_to_risk": 0.05}
    stored.pop("reentry_cooldown_days")  # run #36 predates the cooldown setting
    with engine.begin() as conn:
        run_id = repo.insert_backtest_run(conn, strategy="pullback_b2", start_date=None, end_date=None,
                                          unseen_from=None, params={"money": stored})
    specs = {label: money for label, _, money, _ in study.STUDIES["b3"]}
    with engine.connect() as conn:
        base = study.resolve_money(conn, specs["B2-base"], remap={36: run_id})
        c15 = study.resolve_money(conn, specs["B2-c15"], remap={36: run_id})
        small = study.resolve_money(conn, specs["B3-20k"], remap={36: run_id})
    assert base.max_charge_to_risk == 0.05 and c15.max_charge_to_risk == 0.15 and base.reentry_cooldown_days is None
    assert (small.capital, small.max_position_value, small.max_charge_to_risk) == (20_000, 7_000, 0.15)



def test_exclude_top5_uses_the_earlier_period_only():
    def tr(sym, pnl, period):
        return {"symbol": sym, "pnl": pnl, "period": period}
    run = {"trades": [tr("A", 900, "unseen"), tr("B", 500, "earlier"), tr("C", 400, "earlier"),
                      tr("D", 300, "earlier"), tr("E", 200, "earlier"), tr("F", 100, "earlier"),
                      tr("G", 50, "earlier"), tr("B", 100, "earlier")]}
    assert study.top_earlier_symbols(run) == ["B", "C", "D", "E", "F"]  # A only made money in the unseen years


def test_study_c3_set():
    entries = study.STUDIES["c3"]
    assert [(e[0], e[1]) for e in entries] == [
        ("C-fix", "breakout"), ("C2-100k", "breakout_c2"), ("C3-100k", "breakout_c3"), ("C3-scaled", "breakout_c3"),
        ("C3-half", "breakout_c3"), ("C3-20k", "breakout_c3"), ("C3-ex-top5", "breakout_c3")]
    money = {e[0]: e[2] for e in entries}
    assert money["C-fix"] == ("run", 51, {}) and entries[0][3] == 51
    assert money["C3-scaled"].dd_risk_scaling is True and money["C3-100k"].dd_risk_scaling is False
    assert money["C3-half"].risk_pct == 0.005 and money["C3-20k"].capital == 20_000
    assert entries[-1][4] == {"exclude_top5_from": "C3-100k"}


def test_runner_excludes_symbols(engine):
    from aitrader.backtest.runner import BacktestRequest, create_run, execute_run
    series = {"NIFTY 50": flat(280, volume=0), "BRK": breakout_series()}
    series.update({f"FL{i}": flat(280) for i in range(1, 5)})
    seed(engine, series)
    run_id = create_run(engine, BacktestRequest("breakout", exclude=("BRK",)))
    execute_run(engine, run_id)
    with engine.connect() as conn:
        run = repo.get_backtest_run(conn, run_id)
    assert run["status"] == "ok" and run["trades"] == [] and run["params"]["exclude_symbols"] == ["BRK"]



def _summary(net_earlier, dd_earlier, net_unseen=0.0, dd_unseen=0.0):
    return {"earlier": {"expectancy_r": net_earlier, "max_drawdown_pct": dd_earlier},
            "unseen": {"expectancy_r": net_unseen, "max_drawdown_pct": dd_unseen}}


def test_a3_best_selection_uses_only_earlier_period_numbers():
    base = _summary(0.35, 0.179)
    singles = [("A3-notime", "time", _summary(0.40, 0.19, net_unseen=-5.0)),     # better, +1.1pt dd: in
               ("A3-time120", "time", _summary(0.38, 0.18, net_unseen=9.0)),      # better, but notime is better
               ("A3-rank50", "rank", _summary(0.36, 0.205)),                      # +2.6pt dd: out
               ("A3-norank", "rank", _summary(0.30, 0.15, net_unseen=9.0)),       # worse R: out (unseen ignored)
               ("A3-group", "group", _summary(0.351, 0.199)),                     # +2.0pt dd exactly: in
               ("A3-8pos", "pos", _summary(0.35, 0.17))]                          # not better: out
    chosen, notes = study.select_a3_best(base, singles)
    assert chosen == ["A3-notime", "A3-group"]
    assert any("A3-time120" in n for n in notes) and all("unseen" not in n.lower() for n in notes)
    # flipping every unseen number changes nothing
    flipped = [(l, f, {**s, "unseen": {"expectancy_r": -s["unseen"]["expectancy_r"], "max_drawdown_pct": 0.9}})
               for l, f, s in singles]
    assert study.select_a3_best(base, flipped)[0] == chosen


def test_a3_study_specs():
    labels = [s[0] for s in study.A3_SINGLES]
    assert labels == ["A3-notime", "A3-time120", "A3-rank50", "A3-norank", "A3-group", "A3-8pos"]
    spec = {s[0]: s for s in study.A3_SINGLES}
    assert spec["A3-notime"][2] == {"time_stop_days": None} and spec["A3-norank"][2] == {"keep_top": None}
    assert spec["A3-group"][3] == {"max_per_group": 1, "max_per_sector": 2}
    assert spec["A3-8pos"][3] == {"max_positions": 8, "risk_pct": 0.0075, "max_position_value": 20_000.0}


def test_runner_stores_strategy_overrides_costs_and_notes(engine):
    from aitrader.backtest.costs import CostModel
    from aitrader.backtest.runner import BacktestRequest, create_run
    series = {"NIFTY 50": flat(280, volume=0), "BRK": breakout_series()}
    seed(engine, series)
    run_id = create_run(engine, BacktestRequest("momentum_a2", strategy_params={"time_stop_days": None},
                                                costs=CostModel(slippage=0.001), notes=("chosen because…",)))
    with engine.connect() as conn:
        params = repo.backtest_run_row(conn, run_id)["params"]
    assert params["strategy"]["time_stop_days"] is None and params["strategy_overrides"] == {"time_stop_days": None}
    assert params["costs"]["slippage"] == 0.001 and params["notes"] == ["chosen because…"]


def test_a3_selection_says_tie_when_two_variants_are_equal():
    base = _summary(0.35, 0.179)
    singles = [("A3-notime", "time", _summary(0.361, 0.194)), ("A3-time120", "time", _summary(0.361, 0.194))]
    chosen, notes = study.select_a3_best(base, singles)
    assert chosen == ["A3-notime"]
    assert any("same earlier net R" in n and "first listed" in n for n in notes)
    assert not any("has the better earlier net R" in n for n in notes)



def spec_of(label):
    s = {x[0]: x for x in study.C3X_SINGLES}[label]
    return s[1], s[2]


def test_c3x_study_specs():
    labels = [s[0] for s in study.C3X_SINGLES]
    assert labels == ["C3-confirm", "C3-aplus", "C3-failedbo", "C3-distrib"]
    assert spec_of("C3-failedbo") == ("exit", {"failed_breakout_days": 5})
    assert spec_of("C3-distrib") == ("exit", {"distribution_days": 2})
    spec = {s[0]: s for s in study.C3X_SINGLES}
    assert spec["C3-confirm"][2] == {"entry_confirm": True} and spec["C3-aplus"][2] == {"volume_surge": 3.0}
