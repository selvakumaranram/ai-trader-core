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
