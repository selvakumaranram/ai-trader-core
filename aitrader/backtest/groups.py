"""Symbol → business group and symbol → NSE industry, for the per-group / per-sector position limits."""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Dict

import yaml

DATA = Path(__file__).resolve().parents[1] / "data"


def load_groups(path: Path = DATA / "business_groups.yaml") -> Dict[str, str]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {str(symbol): str(group) for group, symbols in raw.items() for symbol in symbols}


def load_sectors(path: Path = DATA / "nse_sectors.csv") -> Dict[str, str]:
    """NSE industry from the Nifty 200 list (today's classification, used for the whole backtest)."""
    with path.open(encoding="utf-8") as f:
        return {r["symbol"]: r["industry"] for r in csv.DictReader(f)}
