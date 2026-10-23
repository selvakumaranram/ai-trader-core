from aitrader.backtest.strategies.breakout import Breakout
from aitrader.backtest.strategies.momentum import Momentum
from aitrader.backtest.strategies.pullback import Pullback

STRATEGIES = {"momentum": Momentum, "pullback": Pullback, "breakout": Breakout}
INDEX_STRATEGIES = {"pullback", "breakout"}
