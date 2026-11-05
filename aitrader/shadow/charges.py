"""Approximate Zerodha charges for buying and then selling one lot of an index option (shadow P&L only)."""
from __future__ import annotations

BROKERAGE_PER_ORDER = 20.0
STT_SELL = 0.001  # on the sell premium
EXCHANGE = 0.0003503  # on premium turnover
SEBI = 10 / 1e7
STAMP_BUY = 0.00003
GST = 0.18


def option_round_trip_charges(entry: float, exit_: float, lot_size: int) -> float:
    buy, sell = entry * lot_size, exit_ * lot_size
    brokerage = 2 * BROKERAGE_PER_ORDER
    exchange = EXCHANGE * (buy + sell)
    sebi = SEBI * (buy + sell)
    return brokerage + STT_SELL * sell + exchange + sebi + STAMP_BUY * buy + GST * (brokerage + exchange + sebi)
