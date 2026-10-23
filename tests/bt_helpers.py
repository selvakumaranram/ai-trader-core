from __future__ import annotations

import pandas as pd


def frame(rows, start="2024-01-01"):
    """rows: (open, high, low, close, volume) tuples on consecutive business days."""
    idx = pd.bdate_range(start, periods=len(rows))
    return pd.DataFrame(rows, index=idx, columns=["open", "high", "low", "close", "volume"], dtype=float)


def flat(n, price=100.0, volume=2_000_000):
    return [(price, price + 1, price - 1, price, volume)] * n
