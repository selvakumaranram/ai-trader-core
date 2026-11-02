import pandas as pd
import pytest

from aitrader.backtest.strategies import STRATEGIES
from aitrader.backtest.strategies.breakout import Breakout
from aitrader.backtest.strategies.momentum import Momentum
from aitrader.backtest.strategies.pullback import Pullback
from aitrader.backtest.types import LONG, SHORT

DAY = pd.Timestamp("2025-03-03")


def rows(**by_symbol):
    return pd.DataFrame.from_dict(by_symbol, orient="index")


def pull_row(**over):
    base = dict(open=99.5, high=101.5, low=99.8, close=101.0, volume=1e6, sma20=100.0, sma50=95.0, sma200=90.0,
                low3=98.0, high3=103.0)
    return {**base, **over}


def test_pullback_long_triggers_with_reason_and_stop():
    out = Pullback().candidates(DAY, rows(AAA=pull_row()))
    assert len(out) == 1
    c = out[0]
    assert (c.symbol, c.direction, c.stop, c.signal_close) == ("AAA", LONG, 98.0, 101.0)
    assert c.score == pytest.approx(5 / 90) and "20-day" in c.reason and c.signal_date == DAY.date()


@pytest.mark.parametrize("over", [
    dict(sma50=89.0),            # no uptrend (sma50 < sma200)
    dict(low=101.2),             # never dipped to the 20-day average
    dict(close=99.9, open=99.0), # closed below the 20-day average
    dict(open=101.2),            # red candle
    dict(low3=100.5),            # stop closer than 1%
    dict(low3=92.0),             # stop further than 8%
])
def test_pullback_near_misses(over):
    assert Pullback().candidates(DAY, rows(AAA=pull_row(**over))) == []


def test_pullback_short_mirror():
    r = rows(IDX=dict(open=100.5, high=99.5, low=98.0, close=98.5, volume=0, sma20=100.0, sma50=105.0,
                      sma200=110.0, low3=97.0, high3=101.0))
    r.loc["IDX", "high"] = 99.2  # touched within 1% of the 20-day average
    out = Pullback().signals(DAY, r, SHORT)
    assert len(out) == 1 and out[0].direction == SHORT and out[0].stop == 101.0


def brk_row(**over):
    base = dict(open=100.5, high=103.5, low=100.0, close=103.0, volume=6e6, vol20_prior=2e6, sma50=100.0,
                high20_prior=101.0, low20_prior=99.0, atr14=2.0)
    return {**base, **over}


def test_breakout_long_triggers():
    out = Breakout().candidates(DAY, rows(AAA=brk_row()))
    assert len(out) == 1 and out[0].stop == pytest.approx(99.0) and out[0].score == pytest.approx(3.0)


@pytest.mark.parametrize("over", [
    dict(close=100.9),     # not above the 20-day high
    dict(volume=2.9e6),    # volume below 1.5x
    dict(sma50=104.0),     # below the 50-day average
    dict(atr14=12.0),      # stop more than 8% away
])
def test_breakout_near_misses(over):
    assert Breakout().candidates(DAY, rows(AAA=brk_row(**over))) == []


def test_breakout_without_volume_and_short():
    r = rows(IDX=brk_row(volume=0, vol20_prior=0, close=97.0, sma50=100.0))
    out = Breakout(use_volume=False).signals(DAY, r, SHORT)
    assert len(out) == 1 and out[0].stop == pytest.approx(101.0)


def mom_row(r21, r5, r3, r1, vol5, rsi, close=110.0, sma50=100.0, atr=2.0):
    return dict(ret21=r21, ret5=r5, ret3=r3, ret1=r1, vol5=vol5, vol50=1.0, rsi14=rsi, close=close, sma50=sma50,
                atr14=atr, open=close, high=close, low=close, volume=1.0)


def test_momentum_ranks_and_stops():
    r = rows(AAA=mom_row(0.3, 0.1, 0.05, 0.02, 3.0, 60),
             BBB=mom_row(0.2, 0.05, 0.03, 0.01, 2.0, 75),
             CCC=mom_row(0.1, 0.01, 0.01, 0.0, 1.0, 40))
    out = Momentum().candidates(DAY, r)
    assert [c.symbol for c in out] == ["AAA", "BBB", "CCC"]
    assert out[0].score == pytest.approx(100.0)
    assert out[1].score == pytest.approx(6000 / 95)
    assert out[0].stop == pytest.approx(106.0) and "Ranked #1 of 3" in out[0].reason


def test_momentum_requires_close_above_sma50_and_complete_data():
    r = rows(AAA=mom_row(0.3, 0.1, 0.05, 0.02, 3.0, 60, close=90.0),
             BBB=mom_row(float("nan"), 0.1, 0.05, 0.02, 3.0, 60))
    assert Momentum().candidates(DAY, r) == []


def test_empty_rows_and_registry():
    empty = pd.DataFrame()
    assert all(cls().candidates(DAY, empty) == [] for cls in STRATEGIES.values())
    assert set(STRATEGIES) == {"momentum", "pullback", "breakout", "price_action", "momentum_a2", "pullback_b2",
                               "breakout_c2", "pullback_b3", "breakout_c3"}
