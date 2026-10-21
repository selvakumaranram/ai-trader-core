from datetime import date

import pandas as pd

from aitrader.sources.yfinance_source import YFinanceSource


def frame():
    index = pd.to_datetime(["2026-10-19", "2026-10-20", "2026-10-21"]).tz_localize("Asia/Kolkata")
    return pd.DataFrame({
        "Open": [100.0, float("nan"), 50.5], "High": [101.0, float("nan"), 51.0],
        "Low": [99.0, float("nan"), 50.0], "Close": [100.5, float("nan"), 50.8],
        "Volume": [1000, 0, 2000], "Dividends": [0.0, 0.0, 0.0], "Stock Splits": [0.0, 0.0, 2.0],
    }, index=index)


def test_fetch_parses_bars_skips_nan_rows_and_reads_splits():
    calls = []

    def history(symbol, start, end):
        calls.append((symbol, start, end))
        return frame()

    source = YFinanceSource(history_fn=history)
    bars, actions = source.fetch("INFY.NS", date(2026, 10, 19), date(2026, 10, 21))
    assert [b.trade_date for b in bars] == [date(2026, 10, 19), date(2026, 10, 21)]
    assert bars[1].close == 50.8 and bars[1].volume == 2000
    assert [(a.ex_date, a.kind, a.ratio_from, a.ratio_to) for a in actions] == [
        (date(2026, 10, 21), "split", 1.0, 2.0)]
    assert calls == [("INFY.NS", date(2026, 10, 19), date(2026, 10, 22))]  # yfinance end is exclusive
    assert source.name == "yfinance" and source.price_basis == "split_adjusted"


def test_empty_frame_returns_nothing():
    source = YFinanceSource(history_fn=lambda s, a, b: pd.DataFrame())
    assert source.fetch("GONE.NS", date(2026, 10, 1), date(2026, 10, 2)) == ([], [])
