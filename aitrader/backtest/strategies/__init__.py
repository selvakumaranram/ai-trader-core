from aitrader.backtest.strategies.breakout import Breakout
from aitrader.backtest.strategies.breakout_c2 import BreakoutC2
from aitrader.backtest.strategies.momentum import Momentum
from aitrader.backtest.strategies.momentum_a2 import MomentumA2
from aitrader.backtest.strategies.price_action import PriceAction
from aitrader.backtest.strategies.pullback import Pullback
from aitrader.backtest.strategies.pullback_b2 import PullbackB2
from aitrader.backtest.strategies.pullback_b3 import PullbackB3

STRATEGIES = {"momentum": Momentum, "pullback": Pullback, "breakout": Breakout, "price_action": PriceAction,
              "momentum_a2": MomentumA2, "pullback_b2": PullbackB2,
              "breakout_c2": BreakoutC2, "pullback_b3": PullbackB3}
INDEX_STRATEGIES = {"pullback", "breakout", "price_action", "pullback_b2", "breakout_c2", "pullback_b3"}
