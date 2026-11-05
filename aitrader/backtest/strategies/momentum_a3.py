"""Strategy A3 — A2 with the A3-best settings chosen in the A3 study (earlier-period numbers only):
no time stop, and a holding stays while it is in the top 50 by score (rank exit outside the top 50)."""
from __future__ import annotations

from aitrader.backtest.strategies.momentum_a2 import MomentumA2


class MomentumA3(MomentumA2):
    key = "momentum_a3"
    name = "Medium-term momentum (A3-best)"
    PARAMS = {**MomentumA2.PARAMS, "time_stop_days": None, "keep_top": 50}
