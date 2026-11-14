"""Candle data: CSV files or the Dhan (DhanHQ v2) historical data API."""
from __future__ import annotations

import os
import time

import pandas as pd

COLUMNS = ["open", "high", "low", "close", "volume"]
IST = "Asia/Kolkata"
SCRIP_MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"
SEGMENTS = {"NSE": "NSE_EQ", "BSE": "BSE_EQ"}
# timeframe -> Dhan intraday interval in minutes (None = daily candles)
TIMEFRAMES = {"1m": 1, "5m": 5, "15m": 15, "25m": 25, "60m": 60, "1h": 60, "1d": None}
MAX_INTRADAY_DAYS_PER_REQUEST = 85  # Dhan allows roughly 90 days of intraday data per call


def timeframe_delta(timeframe: str) -> pd.Timedelta:
    minutes = _interval(timeframe)
    return pd.Timedelta(minutes=minutes) if minutes else pd.Timedelta(days=1)


def _interval(timeframe: str):
    if timeframe not in TIMEFRAMES:
        raise ValueError(f"unsupported timeframe '{timeframe}'; choose from {', '.join(TIMEFRAMES)}")
    return TIMEFRAMES[timeframe]


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


def make_dhan(client_id: str, access_token: str):
    from dhanhq import DhanContext, dhanhq  # imported lazily so CSV backtests work without it

    return dhanhq(DhanContext(client_id, access_token))


def check(response: dict, what: str):
    """Return the ``data`` of a Dhan SDK response, raising on failure."""
    if not isinstance(response, dict) or response.get("status") != "success":
        remarks = response.get("remarks") if isinstance(response, dict) else response
        raise RuntimeError(f"Dhan {what} failed: {remarks}")
    return response.get("data")


def resolve_security_id(symbol: str, exchange: str = "NSE", cache: str = "dhan_scrip_master.csv") -> str:
    """Look up Dhan's numeric security id for an equity symbol (e.g. RELIANCE on NSE).

    Dhan's instrument list is downloaded once a day and cached in ``cache``.
    """
    if not os.path.exists(cache) or time.time() - os.path.getmtime(cache) > 86_400:
        import requests

        resp = requests.get(SCRIP_MASTER_URL, timeout=120)
        resp.raise_for_status()
        with open(cache, "wb") as f:
            f.write(resp.content)
    master = pd.read_csv(cache, low_memory=False, dtype=str)
    need = ["SEM_EXM_EXCH_ID", "SEM_SEGMENT", "SEM_SMST_SECURITY_ID", "SEM_TRADING_SYMBOL"]
    if any(c not in master.columns for c in need):
        raise RuntimeError(f"{cache} has an unexpected layout; pass --security-id instead "
                           "(find it in Dhan's instrument list)")
    rows = master[(master["SEM_EXM_EXCH_ID"].str.strip() == exchange.upper())
                  & (master["SEM_SEGMENT"].str.strip() == "E")
                  & (master["SEM_TRADING_SYMBOL"].str.strip() == symbol.upper())]
    if "SEM_SERIES" in rows.columns and (rows["SEM_SERIES"].str.strip() == "EQ").any():
        rows = rows[rows["SEM_SERIES"].str.strip() == "EQ"]
    if rows.empty:
        raise ValueError(f"symbol '{symbol}' not found on {exchange}; check the spelling or pass --security-id")
    return str(rows["SEM_SMST_SECURITY_ID"].iloc[0]).strip()


def candles_from_response(data: dict) -> pd.DataFrame:
    """Convert Dhan's column-array chart payload into an IST-indexed OHLCV frame."""
    if not data or not data.get("timestamp"):
        return pd.DataFrame(columns=COLUMNS, index=pd.DatetimeIndex([], tz=IST, name="timestamp"))
    df = pd.DataFrame({c: data[c] for c in COLUMNS}, dtype=float)
    df.index = pd.to_datetime(data["timestamp"], unit="s", utc=True).tz_convert(IST)
    df.index.name = "timestamp"
    return df


def fetch_candles(dhan, security_id: str, exchange: str, timeframe: str, days: int) -> pd.DataFrame:
    """Fetch the last ``days`` calendar days of candles. The newest row may still be forming."""
    interval = _interval(timeframe)
    segment = SEGMENTS[exchange.upper()]
    end = pd.Timestamp.now(tz=IST).normalize() + pd.Timedelta(days=1)
    start = end - pd.Timedelta(days=days + 1)
    if interval is None:
        frames = [candles_from_response(check(dhan.historical_daily_data(
            security_id, segment, "EQUITY", start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")), "daily candles"))]
    else:
        frames = []
        while start < end:
            stop = min(start + pd.Timedelta(days=MAX_INTRADAY_DAYS_PER_REQUEST), end)
            frames.append(candles_from_response(check(dhan.intraday_minute_data(
                security_id, segment, "EQUITY", start.strftime("%Y-%m-%d"), stop.strftime("%Y-%m-%d"),
                interval), "intraday candles")))
            start = stop
    df = pd.concat(frames).sort_index()
    return df[~df.index.duplicated(keep="last")]
