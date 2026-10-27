from aitrader.backtest.strategies.breakout import Breakout
from aitrader.backtest.strategies.momentum import Momentum
from aitrader.backtest.strategies.price_action import PriceAction
from aitrader.backtest.strategies.pullback import Pullback

STRATEGIES = {"momentum": Momentum, "pullback": Pullback, "breakout": Breakout, "price_action": PriceAction}
INDEX_STRATEGIES = {"pullback", "breakout", "price_action"}
