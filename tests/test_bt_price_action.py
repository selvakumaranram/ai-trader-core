import math

import pandas as pd
import pytest

from aitrader.backtest.strategies import INDEX_STRATEGIES, STRATEGIES
from aitrader.backtest.strategies.price_action import PriceAction
from aitrader.backtest.types import LONG, SHORT

DAY = pd.Timestamp("2025-03-03")
NAN = float("nan")


def row(**over):
    base = dict(open=101.0, high=103.5, low=100.4, close=103.0, volume=1e6, atr14=2.0, ema20=101.0,
                sh1=104.0, sh2=106.0, sl1=95.0, sl2=99.0, trend=1, support=100.0, support_touches=3,
                resistance=115.0, resistance_touches=2, hammer=True, shooting_star=False, bull_engulf=False,
                bear_engulf=False, choppy=False, retest_long=False, retest_short=False, retest_level=NAN)
    return {**base, **over}


def rows(**by_symbol):
    return pd.DataFrame.from_dict(by_symbol, orient="index")


def test_pullback_to_support_with_hammer():
    (c,) = PriceAction().candidates(DAY, rows(AAA=row()))
    assert (c.symbol, c.direction, c.stop, c.facts["setup"]) == ("AAA", LONG, 100.4, "pullback")
    for word in ("Uptrend", "support", "hammer"):
        assert word in c.reason
    assert c.facts["room_r"] == pytest.approx((115 - 103) / 2.6, abs=1e-3)


@pytest.mark.parametrize("over", [
    dict(trend=0),
    dict(low=102.0, ema20=99.0),                          # 2% from support, 3% from the 20-day EMA
    dict(hammer=False),                                   # no rejection candle
    dict(ema20=103.5, low=103.0, support=103.2),          # close not above the 20-day EMA
    dict(low=102.5, support=102.4),                       # stop closer than 1%
    dict(low=94.0, support=94.0),                         # stop further than 8%
    dict(choppy=True),
])
def test_pullback_near_misses(over):
    assert PriceAction().candidates(DAY, rows(AAA=row(**over))) == []


def test_room_to_resistance_below_2r_is_skipped():
    assert PriceAction().candidates(DAY, rows(AAA=row(resistance=103 + 1.5 * 2.6))) == []


def test_no_level_in_the_way_scores_five():
    (c,) = PriceAction().candidates(DAY, rows(AAA=row(resistance=NAN)))
    assert c.facts["room_r"] is None and c.score == 50


def test_retest_candidate_and_stop():
    r = row(trend=0, hammer=False, support=NAN, resistance=NAN, retest_long=True, retest_level=101.0, low=100.6)
    (c,) = PriceAction().candidates(DAY, rows(AAA=r))
    assert c.facts["setup"] == "retest" and c.stop == pytest.approx(100.0) and c.score == 51


def test_ranking_prefers_room_then_retest():
    pull = row(resistance=NAN)
    retest = row(trend=0, hammer=False, support=NAN, resistance=NAN, retest_long=True, retest_level=101.0,
                 low=100.6)
    out = PriceAction().candidates(DAY, rows(AAA=pull, BBB=retest))
    assert max(out, key=lambda c: c.score).symbol == "BBB"


def test_short_mirror_for_index():
    r = row(trend=-1, high=99.6, low=96.5, close=97.0, open=98.5, ema20=99.0, resistance=100.0, support=NAN,
            hammer=False, shooting_star=True, sh1=104.0, sh2=101.0, sl1=98.0, sl2=96.0)
    (c,) = PriceAction(use_volume=False).signals(DAY, rows(NIFTY=r), SHORT)
    assert c.direction == SHORT and c.stop == 99.6 and "Downtrend" in c.reason


def test_registry_and_defaults():
    assert STRATEGIES["price_action"] is PriceAction and "price_action" in INDEX_STRATEGIES
    assert PriceAction.DEFAULT_MONEY == {"risk_per_trade": 300.0, "daily_loss_limit": 600.0}
    assert PriceAction.exit_policy == "partial_trail" and PriceAction().candidates(DAY, pd.DataFrame()) == []
    assert not math.isnan(PriceAction.PARAMS["partial_r"])
