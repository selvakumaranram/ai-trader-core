"""What happened to similar C3 breakouts in the backtest: shown on each stock signal so a pick is backed by numbers.

Grouped by type of setup (all breakouts, the same volume size, the same sector), not by the single stock: one stock
has too few past breakouts to judge on its own."""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence

MIN_TRADES = 15  # below this, no percentages at all
RELIABLE_TRADES = 30  # below this, the numbers carry a "small sample" warning
BIG_WINNER_R = 3.0


def volume_bucket(surge: Optional[float]) -> Optional[str]:
    if surge is None:
        return None
    if surge < 2.0:
        return "1.5–2×"
    if surge < 3.0:
        return "2–3×"
    return "3× or more"


def group_stats(rs: Sequence[float]) -> Dict[str, Optional[float]]:
    """n, share of trades that made money after charges, average R, share of +3R-or-better trades."""
    if not rs:
        return {"n": 0, "win_rate": None, "avg_r": None, "big_winners": None}
    n = len(rs)
    return {"n": n, "win_rate": sum(1 for r in rs if r > 0) / n, "avg_r": sum(rs) / n,
            "big_winners": sum(1 for r in rs if r >= BIG_WINNER_R) / n}


def _group(label: str, rs: List[float]) -> dict:
    g = {"label": label, **group_stats(rs), "note": ""}
    if g["n"] < MIN_TRADES:
        g.update(win_rate=None, avg_r=None, big_winners=None,
                 note=f"too few past trades ({g['n']}) to judge")
    elif g["n"] < RELIABLE_TRADES:
        g["note"] = f"small sample ({g['n']} trades): treat with care"
    return g


def signal_stats(facts: dict, sector: Optional[str], past: Sequence[dict]) -> List[dict]:
    """`past`: backtest trades with `r_multiple` and `facts` (volume_surge, sector)."""
    def r(t):
        return float(t["r_multiple"])

    groups = [_group("All C3 breakouts", [r(t) for t in past])]
    bucket = volume_bucket(facts.get("volume_surge"))
    if bucket:
        groups.append(_group(f"Volume {bucket} normal", [r(t) for t in past
                                                          if volume_bucket((t["facts"] or {}).get("volume_surge"))
                                                          == bucket]))
    if sector:
        groups.append(_group(f"Sector: {sector}", [r(t) for t in past if (t["facts"] or {}).get("sector") == sector]))
    return groups


def stats_for_signals(conn, signals: Sequence[dict]):
    """(source run summary or None, {signal id: groups}) for the stock signals, from the latest plain C3 run."""
    from aitrader.backtest.groups import load_sectors
    from aitrader.db import repo
    from aitrader.shadow.live import STOCK_RULE

    run = repo.latest_plain_run(conn, "breakout_c3")
    if run is None:
        return None, {}
    past = repo.backtest_trade_facts(conn, run["id"])
    sectors = load_sectors()
    out = {s["id"]: signal_stats(s["facts"] or {}, sectors.get(s["underlying"]), past)
           for s in signals if s["rule"] == STOCK_RULE}
    source = {"run_id": run["id"], "strategy": run["strategy"], "from": run["start_date"].isoformat(),
              "to": run["end_date"].isoformat(), "trades": len(past)}
    return source, out
