from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CostModel:
    """Zerodha equity-delivery charges (defaults; re-check against zerodha.com/charges) plus slippage."""
    brokerage: float = 0.0
    stt_rate: float = 0.001
    exchange_rate: float = 0.0000297
    sebi_rate: float = 10 / 1e7
    stamp_rate: float = 0.00015
    gst_rate: float = 0.18
    dp_charge: float = 15.93
    slippage: float = 0.0005

    def _common(self, value: float) -> float:
        exchange, sebi = value * self.exchange_rate, value * self.sebi_rate
        return self.brokerage + value * self.stt_rate + exchange + sebi + self.gst_rate * (
            self.brokerage + exchange + sebi)

    def buy_charges(self, value: float) -> float:
        return self._common(value) + value * self.stamp_rate

    def sell_charges(self, value: float) -> float:
        return self._common(value) + self.dp_charge
