from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Sequence

import pandas as pd
from sqlalchemy.engine import Engine

from aitrader.db import repo
from aitrader.models import Bar

COLUMNS = ["open", "high", "low", "close", "volume"]


def frame_from_bars(bars: Sequence[Bar]) -> pd.DataFrame:
    df = pd.DataFrame([(pd.Timestamp(b.trade_date), b.open, b.high, b.low, b.close, b.volume) for b in bars],
                      columns=["date", *COLUMNS]).set_index("date")
    return df.astype(float)


@dataclass
class Market:
    stocks: Dict[str, pd.DataFrame] = field(default_factory=dict)
    indices: Dict[str, pd.DataFrame] = field(default_factory=dict)
    instrument_ids: Dict[str, int] = field(default_factory=dict)


def load_market(engine: Engine, source: str) -> Market:
    market = Market()
    with engine.connect() as conn:
        for inst in repo.active_instruments(conn):
            bars = repo.load_bars(conn, inst["id"], source=source)
            if not bars:
                continue
            target = market.indices if inst["kind"] == "index" else market.stocks
            target[inst["symbol"]] = frame_from_bars(bars)
            market.instrument_ids[inst["symbol"]] = inst["id"]
    return market
