from datetime import date

import pytest

from aitrader.models import OptionContract
from aitrader.options import atm_strike, select_contracts, strike_step


def chain(underlying, expiry, center, step, n, lot=75):
    out = []
    for i in range(-n, n + 1):
        strike = center + i * step
        for kind in ("CE", "PE"):
            out.append(OptionContract(underlying, expiry, float(strike), kind, lot,
                                      f"{underlying}{expiry:%y%b}{strike}{kind}".upper(), 1000 + len(out)))
    return out


def test_strike_step_is_most_common_gap():
    assert strike_step([100, 150, 200, 250, 300, 400, 500]) == 50


def test_strike_step_needs_two_strikes():
    with pytest.raises(ValueError):
        strike_step([100])


def test_atm_tie_rounds_down():
    assert atm_strike([24900, 24950, 25000], 24975) == 24950
    assert atm_strike([24900, 24950, 25000], 24990) == 25000


def test_select_two_nearest_unexpired_expiries_within_ten_steps():
    expired = chain("NIFTY", date(2026, 10, 13), 25000, 50, 15)
    near = chain("NIFTY", date(2026, 10, 27), 25000, 50, 15)
    nxt = chain("NIFTY", date(2026, 11, 3), 25000, 50, 15)
    far = chain("NIFTY", date(2026, 11, 24), 25000, 50, 15)
    bank = chain("BANKNIFTY", date(2026, 10, 27), 56000, 100, 15)
    picked = select_contracts(expired + near + nxt + far + bank, "NIFTY", 25010.0, date(2026, 10, 21))
    assert {c.expiry for c in picked} == {date(2026, 10, 27), date(2026, 11, 3)}
    assert {c.underlying for c in picked} == {"NIFTY"}
    assert len(picked) == 2 * 21 * 2  # 2 expiries x (ATM +-10 = 21 strikes) x CE/PE
    strikes = sorted({c.strike for c in picked})
    assert strikes[0] == 24500 and strikes[-1] == 25500
