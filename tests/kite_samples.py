"""Synthetic Kite responses shaped like the documented Kite Connect v3 payloads."""
from __future__ import annotations

from datetime import date


def nse_rows():
    return [
        {"instrument_token": 408065, "tradingsymbol": "INFY", "name": "INFOSYS", "expiry": "", "strike": 0.0,
         "lot_size": 1, "instrument_type": "EQ", "segment": "NSE", "exchange": "NSE"},
        {"instrument_token": 256265, "tradingsymbol": "NIFTY 50", "name": "NIFTY 50", "expiry": "", "strike": 0.0,
         "lot_size": 0, "instrument_type": "EQ", "segment": "INDICES", "exchange": "NSE"},
        {"instrument_token": 260105, "tradingsymbol": "NIFTY BANK", "name": "NIFTY BANK", "expiry": "",
         "strike": 0.0, "lot_size": 0, "instrument_type": "EQ", "segment": "INDICES", "exchange": "NSE"},
    ]


def nfo_option_rows(underlying: str, expiry: date, center: int, step: int, n: int, lot: int):
    rows, token = [], 10_000_000 + (0 if underlying == "NIFTY" else 5_000_000) + expiry.toordinal() % 1000 * 1000
    for i in range(-n, n + 1):
        strike = center + i * step
        for kind in ("CE", "PE"):
            token += 1
            rows.append({"instrument_token": token, "tradingsymbol": f"{underlying}{expiry:%y%m%d}{strike}{kind}",
                         "name": underlying, "expiry": expiry, "strike": float(strike), "lot_size": lot,
                         "instrument_type": kind, "segment": "NFO-OPT", "exchange": "NFO"})
    return rows


def nfo_noise_rows():
    return [
        {"instrument_token": 1, "tradingsymbol": "NIFTY26OCTFUT", "name": "NIFTY", "expiry": date(2026, 10, 27),
         "strike": 0.0, "lot_size": 75, "instrument_type": "FUT", "segment": "NFO-FUT", "exchange": "NFO"},
        {"instrument_token": 2, "tradingsymbol": "INFY26OCT1500CE", "name": "INFY", "expiry": date(2026, 10, 27),
         "strike": 1500.0, "lot_size": 400, "instrument_type": "CE", "segment": "NFO-OPT", "exchange": "NFO"},
        {"instrument_token": 3, "tradingsymbol": "WEIRD", "name": "NIFTY", "expiry": "", "strike": 0.0,
         "lot_size": 75, "instrument_type": "CE", "segment": "NFO-OPT", "exchange": "NFO"},
    ]


def index_quotes(nifty: float, banknifty: float):
    return {"NSE:NIFTY 50": {"last_price": nifty}, "NSE:NIFTY BANK": {"last_price": banknifty}}


def option_quotes(rows):
    quotes = {}
    for r in rows:
        quotes[f"NFO:{r['tradingsymbol']}"] = {
            "last_price": 120.5, "volume": 5000, "oi": 150000,
            "depth": {"buy": [{"price": 120.0, "quantity": 75}], "sell": [{"price": 121.0, "quantity": 75}]},
        }
    return quotes
