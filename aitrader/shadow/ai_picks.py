"""AI picks: after the evening scan, an AI model (Gemini for now) chooses among the day's C3 stock signals.

It sees only our own facts and backtest statistics: no news, no prices beyond these. Its picks go into a separate
pretend portfolio ("ai") so the scoreboard can show whether it chooses better than the rule or than you. It never
places an order and never affects the rule's or your trades."""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from datetime import date
from typing import Dict, Optional, Set, Tuple

from aitrader.backtest.groups import load_sectors
from aitrader.db import repo
from aitrader.jobs import JobDeps, RunResult

MAX_PICKS = 5  # the AI portfolio's slots
ID_PATTERN = re.compile(r"\[id (\d+)\]")
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

INSTRUCTIONS = f"""You help choose stocks for a PRETEND (paper) swing-trading portfolio on India's NSE. No real money.
Strategy C3 found the breakout signals below after today's close. Each would be bought at tomorrow's open, with the
stop shown and a trailing stop afterwards (no fixed target). The portfolio holds at most {MAX_PICKS} stocks.

Use ONLY the facts and past statistics given here. You have no news, results dates or live prices: do not invent
any. "Past statistics" are from a 9-year backtest of the same strategy, grouped by similar setups; small samples are
unreliable. C3 usually wins only 30-40% of trades and relies on a few big winners, so a low win rate alone is normal.

Decide for EACH signal: pick it or skip it. Pick at most {MAX_PICKS}, best first. Give one plain-English sentence of
reason each, based on the numbers.

Answer with JSON only, exactly in this shape:
{{"picks": [{{"id": <signal id>, "pick": true or false, "reason": "<one sentence>"}}]}}
"""


def _pct(x: Optional[float]) -> str:
    return "n/a" if x is None else f"{x * 100:.0f}%"


def build_prompt(signals, stats: Dict[int, list], sectors: Dict[str, str]) -> str:
    lines = [INSTRUCTIONS, "Signals:"]
    for s in signals:
        f = s["facts"] or {}
        close, stop = float(s["close"]), float(s["stop"])
        atr = f.get("atr14")
        lines.append(
            f"[id {s['id']}] {s['underlying']} (sector: {sectors.get(s['underlying'], 'unknown')}): closed "
            f"{close:.2f} above its 20-day high {f.get('level', 'n/a')}; volume {f.get('volume_surge', 'n/a')}x "
            f"normal; stop {stop:.2f} ({(close - stop) / close * 100:.1f}% below the close"
            + (f", ATR(14) {atr / close * 100:.1f}% of the price" if atr else "") + ").")
        for g in stats.get(s["id"], []):
            if g["win_rate"] is None:
                lines.append(f"    past: {g['label']}: {g['note']}")
            else:
                lines.append(f"    past: {g['label']}: {g['n']} trades, made money {_pct(g['win_rate'])}, average "
                             f"{g['avg_r']:+.2f}R, big winners (+3R) {_pct(g['big_winners'])}"
                             + (f" ({g['note']})" if g["note"] else ""))
    return "\n".join(lines)


def parse_answer(text: str, ids: Set[int]) -> Dict[int, Tuple[bool, str]]:
    """{signal id: (picked, reason)} in the AI's order; unknown ids dropped; picks beyond MAX_PICKS turned to skips."""
    try:
        data = json.loads(text)
        items = data["picks"]
        if not isinstance(items, list):
            raise TypeError("picks is not a list")
    except (ValueError, KeyError, TypeError) as exc:
        raise ValueError(f"the AI answer is not the expected JSON ({exc.__class__.__name__})") from None
    out: Dict[int, Tuple[bool, str]] = {}
    picked = 0
    for item in items:
        try:
            sid, pick, reason = int(item["id"]), bool(item["pick"]), str(item.get("reason", "")).strip()
        except (KeyError, TypeError, ValueError):
            continue
        if sid not in ids or sid in out:
            continue
        if pick and picked >= MAX_PICKS:
            pick, reason = False, f"{reason} (not picked: only {MAX_PICKS} slots)"
        picked += pick
        out[sid] = (pick, reason[:500])
    return out


class GeminiClient:
    """Google Gemini over plain HTTPS. The key travels in a header (never in the URL) and is never logged."""

    def __init__(self, api_key: str, model: str, timeout: float = 120.0):
        self._key, self.model, self._timeout = api_key, model, timeout

    def ask(self, prompt: str) -> str:
        body = json.dumps({"contents": [{"parts": [{"text": prompt}]}],
                           "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"}})
        req = urllib.request.Request(GEMINI_URL.format(model=self.model), data=body.encode("utf-8"), method="POST",
                                     headers={"Content-Type": "application/json", "x-goog-api-key": self._key})
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"Gemini returned HTTP {exc.code}") from None
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Gemini not reachable ({exc.reason.__class__.__name__})") from None
        except (TimeoutError, OSError):
            raise RuntimeError(f"Gemini timed out after {self._timeout:.0f}s") from None
        try:
            return data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError, TypeError):
            raise RuntimeError("Gemini returned no answer text") from None


def ai_picks(deps: JobDeps, run_date: date, run_id: int) -> RunResult:
    from aitrader.shadow.live import STOCK_RULE
    from aitrader.shadow.stats import stats_for_signals

    ai = getattr(deps, "ai", None)
    if ai is None:
        return RunResult("skipped", {}, "no AI key set (GEMINI_API_KEY in the core .env)")
    with deps.engine.connect() as conn:
        signals = [s for s in repo.new_shadow_signals(conn, run_date)
                   if s["rule"] == STOCK_RULE and s["ai_view"] is None]
        if not signals:
            return RunResult("ok", {"asked": 0, "picked": 0})
        _, stats = stats_for_signals(conn, signals)
    prompt = build_prompt(signals, stats, load_sectors())
    try:
        answer = parse_answer(ai.ask(prompt), {s["id"] for s in signals})
    except (RuntimeError, ValueError) as exc:
        return RunResult("failed", {"asked": len(signals)}, str(exc))
    with deps.engine.begin() as conn:
        for sid, (pick, reason) in answer.items():
            repo.set_shadow_ai_view(conn, sid, "pick" if pick else "skip", reason, ai.model)
    return RunResult("ok", {"asked": len(signals), "picked": sum(1 for p, _ in answer.values() if p)})
