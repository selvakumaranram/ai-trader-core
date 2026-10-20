from __future__ import annotations


class FakeBarSource:
    name = "yfinance"
    price_basis = "split_adjusted"

    def __init__(self, data):
        self.data = data
        self.calls = []

    def fetch(self, symbol, start, end):
        self.calls.append((symbol, start, end))
        value = self.data.get(symbol, ([], []))
        if isinstance(value, Exception):
            raise value
        bars, actions = value
        return [b for b in bars if start <= b.trade_date <= end], actions
