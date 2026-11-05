"""AI picks: Gemini chooses among the day's C3 stock signals (pretend portfolio only), using only our facts."""
import json
from datetime import datetime

import pytest

from aitrader.auth import IST
from aitrader.db import repo
from aitrader.jobs import run_job
from aitrader.kite.fake import FakeKiteGateway
from aitrader.shadow import ai_picks as A
from test_shadow_picks import MORNING
from test_shadow_stocks import NEXT_DAY, RUN_DAY, deps, seed, stock_signals

EVENING = datetime(2026, 10, 21, 17, 0, tzinfo=IST)


class FakeAI:
    model = "fake-model"

    def __init__(self, answer):
        self.answer, self.prompts = answer, []

    def ask(self, prompt):
        self.prompts.append(prompt)
        return self.answer(prompt) if callable(self.answer) else self.answer


def pick_all(prompt):
    ids = [int(x) for x in A.ID_PATTERN.findall(prompt)]
    return json.dumps({"picks": [{"id": i, "pick": True, "reason": "strong volume"} for i in ids]})


def with_ai(engine, ai, now=EVENING):
    d = deps(engine, now=now)
    d.ai = ai
    return d


def scanned(engine):
    seed(engine)
    run_job("shadow_scan", deps(engine), RUN_DAY)
    (s,) = stock_signals(engine)
    return s


# --- answer parsing --------------------------------------------------------------------------------------------

def test_parse_keeps_known_ids_and_at_most_five_picks_in_the_ai_order():
    text = json.dumps({"picks": [{"id": i, "pick": True, "reason": f"r{i}"} for i in range(1, 8)]
                       + [{"id": 99, "pick": True, "reason": "unknown"}, {"id": 8, "pick": False, "reason": "weak"}]})
    out = A.parse_answer(text, set(range(1, 9)))
    assert [i for i, (p, _) in out.items() if p] == [1, 2, 3, 4, 5]
    assert out[6] == (False, "r6 (not picked: only 5 slots)") and out[8] == (False, "weak") and 99 not in out


@pytest.mark.parametrize("bad", ["not json", json.dumps({"nope": []}), json.dumps({"picks": "x"})])
def test_parse_rejects_malformed_answers(bad):
    with pytest.raises(ValueError):
        A.parse_answer(bad, {1})


def test_prompt_carries_only_our_facts_and_statistics():
    signals = [{"id": 7, "underlying": "ACME", "close": 1035.0, "stop": 1000.0,
                "facts": {"volume_surge": 4.5, "atr14": 10.0, "level": 1005.0}}]
    stats = {7: [{"label": "All C3 breakouts", "n": 390, "win_rate": 0.34, "avg_r": 0.3, "big_winners": 0.1,
                  "note": ""}]}
    p = A.build_prompt(signals, stats, {"ACME": "Finance"})
    assert "[id 7] ACME" in p and "Finance" in p and "4.5" in p and "390" in p and "JSON" in p
    assert "news" in p.lower()  # it is told it has no news and must not invent any


# --- the job ---------------------------------------------------------------------------------------------------

def test_job_records_the_ai_view_reason_and_model(engine):
    s = scanned(engine)
    r = run_job("ai_picks", with_ai(engine, FakeAI(pick_all)), RUN_DAY)
    (s,) = stock_signals(engine)
    assert r.status == "ok" and r.counts == {"asked": 1, "picked": 1}
    assert (s["ai_view"], s["ai_reason"], s["ai_model"]) == ("pick", "strong volume", "fake-model")


def test_job_skips_without_a_key_and_never_asks_twice(engine):
    scanned(engine)
    assert run_job("ai_picks", deps(engine), RUN_DAY).status == "skipped"
    ai = FakeAI(pick_all)
    run_job("ai_picks", with_ai(engine, ai), RUN_DAY)
    r = run_job("ai_picks", with_ai(engine, ai), RUN_DAY)
    assert len(ai.prompts) == 1 and r.counts == {"asked": 0, "picked": 0}


def test_job_fails_cleanly_on_a_bad_answer(engine):
    scanned(engine)
    r = run_job("ai_picks", with_ai(engine, FakeAI("garbage")), RUN_DAY)
    (s,) = stock_signals(engine)
    assert r.status == "failed" and s["ai_view"] is None


# --- the AI portfolio ------------------------------------------------------------------------------------------

def enter_next_morning(engine):
    kite = FakeKiteGateway(quotes={"NSE:NIFTY 50": {"last_price": 22600.0}, "NSE:ACME": {"last_price": 1020.0}})
    run_job("shadow_enter", deps(engine, kite, MORNING), NEXT_DAY)
    with engine.connect() as conn:
        return [t for t in repo.shadow_trades_list(conn) if t["kind"] == "stock"]


def test_an_ai_pick_is_bought_in_the_ai_portfolio(engine):
    scanned(engine)
    run_job("ai_picks", with_ai(engine, FakeAI(pick_all)), RUN_DAY)
    trades = enter_next_morning(engine)
    (s,) = stock_signals(engine)
    assert sorted(t["portfolio"] for t in trades) == ["ai", "rule"]
    assert s["ai_note"] == "bought" and s["status"] == "entered" and s["pick_note"] is None


def test_an_ai_skip_is_not_bought(engine):
    s = scanned(engine)
    ai = FakeAI(json.dumps({"picks": [{"id": s["id"], "pick": False, "reason": "too weak"}]}))
    run_job("ai_picks", with_ai(engine, ai), RUN_DAY)
    assert [t["portfolio"] for t in enter_next_morning(engine)] == ["rule"]


def test_scoreboard_has_an_ai_portfolio():
    from aitrader.shadow.live import scoreboard
    sig = {"id": 1, "rule": "c3_stock", "status": "entered", "user_view": None, "ai_view": "pick"}
    t = {"signal_id": 1, "rule": "c3_stock", "portfolio": "ai", "status": "closed", "pnl_per_lot": 300.0,
         "r_multiple": 0.5}
    board = scoreboard([sig], [t])
    assert board["portfolios"]["ai"]["total_pnl"] == 300.0 and board["portfolios"]["ai"]["signals"] == 1
    assert board["rules"]["c3_stock"]["closed"] == 0


def test_ai_picks_is_scheduled_after_the_scan():
    from aitrader import scheduler
    names = [n for n, _, _ in scheduler.SCHEDULE]
    assert names.index("ai_picks") > names.index("shadow_scan")
    assert "ai_picks" not in scheduler.NEEDS_LOGIN


def test_gemini_client_turns_a_timeout_into_a_clean_error(monkeypatch):
    def slow(*args, **kwargs):
        raise TimeoutError("The read operation timed out")
    monkeypatch.setattr(A.urllib.request, "urlopen", slow)
    with pytest.raises(RuntimeError, match="timed out"):
        A.GeminiClient("k", "m").ask("p")


def test_default_model_is_one_google_still_offers():
    from aitrader.config import Settings
    assert Settings(_env_file=None).ai_model == "gemini-3.8-flash"
