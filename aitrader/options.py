from __future__ import annotations

from collections import Counter
from datetime import date
from typing import Iterable, List, Sequence

from aitrader.models import OptionContract


def strike_step(strikes: Iterable[float]) -> float:
    unique = sorted(set(strikes))
    if len(unique) < 2:
        raise ValueError("need at least two distinct strikes to infer the strike step")
    gaps = Counter(round(b - a, 4) for a, b in zip(unique, unique[1:]))
    best = max(gaps.values())
    return min(g for g, count in gaps.items() if count == best)


def atm_strike(strikes: Iterable[float], price: float) -> float:
    unique = sorted(set(strikes))
    if not unique:
        raise ValueError("no strikes available")
    return min(unique, key=lambda s: (abs(s - price), s))


def select_contracts(contracts: Sequence[OptionContract], underlying: str, underlying_price: float,
                     as_of: date, expiries: int = 2, width: int = 10) -> List[OptionContract]:
    mine = [c for c in contracts if c.underlying == underlying and c.expiry >= as_of]
    selected: List[OptionContract] = []
    for expiry in sorted({c.expiry for c in mine})[:expiries]:
        in_expiry = [c for c in mine if c.expiry == expiry]
        strikes = [c.strike for c in in_expiry]
        step = strike_step(strikes)
        atm = atm_strike(strikes, underlying_price)
        low, high = atm - width * step - 1e-6, atm + width * step + 1e-6
        selected.extend(c for c in in_expiry if low <= c.strike <= high)
    return sorted(selected, key=lambda c: (c.expiry, c.strike, c.option_type))
