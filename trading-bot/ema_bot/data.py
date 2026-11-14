"""Candle data loading: CSV files or any exchange supported by ccxt."""
from __future__ import annotations

import pandas as pd

COLUMNS = ["open", "high", "low", "close", "volume"]


def load_csv(path: str) -> pd.DataFrame:
    """Load OHLCV candles from a CSV.

    Needs open/high/low/close columns (any letter case) plus a time column
    named timestamp, date, datetime or time. Volume is optional.
    """
    df = pd.read_csv(path)
    df.columns = [c.strip().lower() for c in df.columns]
    time_col = next((c for c in ("timestamp", "datetime", "date", "time") if c in df.columns), None)
    if time_col is None:
        raise ValueError(f"{path}: no timestamp/datetime/date/time column found")
    missing = [c for c in ("open", "high", "low", "close") if c not in df.columns]
    if missing:
        raise ValueError(f"{path}: missing columns {missing}")
    if "volume" not in df.columns:
        df["volume"] = 0.0
    df[time_col] = pd.to_datetime(df[time_col])
    df = df.set_index(time_col).sort_index()
    df.index.name = "timestamp"
    return df[COLUMNS].astype(float)


def make_exchange(name: str, api_key: str | None = None, secret: str | None = None):
    import ccxt  # imported lazily so CSV backtests work without it

    if not hasattr(ccxt, name):
        raise ValueError(f"unknown exchange '{name}' (see ccxt.exchanges for valid ids)")
    config = {"enableRateLimit": True}
    if api_key:
        config.update(apiKey=api_key, secret=secret)
    return getattr(ccxt, name)(config)


def fetch_candles(exchange, symbol: str, timeframe: str, limit: int = 500) -> pd.DataFrame:
    """Fetch the most recent ``limit`` candles. The last row is usually still forming."""
    rows = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    df = pd.DataFrame(rows, columns=["timestamp", *COLUMNS])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    return df.set_index("timestamp").astype(float)
