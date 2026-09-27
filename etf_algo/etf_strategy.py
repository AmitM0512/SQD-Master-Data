#!/usr/bin/env python3
"""
NSE ETF dip-buy strategy on Zerodha Kite Connect: backtest + paper trading.

Strategy rules (identical in backtest and paper mode):
  1. ENTRY   : Buy an ETF when its price is 1.25% or more away from the previous
               close (default: BELOW it, i.e. a dip; set strategy.entry_direction
               to "up" in the config to buy strength instead).
  2. FILTER  : 20-day average daily volume (last 20 completed sessions) > 50,000.
  3. SIZING  : Each buy uses 8% of total portfolio value (cash + holdings at market).
  4. EXIT    : Each lot is sold at +5.5% from its own entry price.
  5. COOLDOWN: After buying an ETF, it is not bought again on the next trading
               day (nor again the same day). It may be bought again from the
               following trading day if the conditions match.

Every buy is tracked as a separate "lot" with its own target, so averaging into
an ETF on a later dip is allowed (cap it with strategy.max_lots_per_symbol).

NOTHING in this file places real orders. Paper mode only simulates fills on
live Kite prices.

Commands (run from anywhere; paths are relative to this file's folder):
  python etf_strategy.py login                  # daily: generate Kite access token
  python etf_strategy.py universe [--save]      # list the NSE ETF universe
  python etf_strategy.py backtest [--start 2022-01-01 --end 2026-09-25 --capital 1000000]
  python etf_strategy.py paper [--once]         # run paper trading for today's session
  python etf_strategy.py status                 # paper portfolio, holdings and P&L
  python etf_strategy.py reset-paper            # start the paper portfolio afresh

Credentials are read from config/kite_config.json (git-ignored) or from the
environment variables KITE_API_KEY / KITE_API_SECRET / KITE_ACCESS_TOKEN.
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import math
import os
import re
import stat
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from datetime import time as dtime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

try:
    import pandas as pd
except ImportError:  # pragma: no cover
    sys.exit("pandas is required: pip install -r requirements.txt")

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG = BASE_DIR / "config" / "kite_config.json"
IST = ZoneInfo("Asia/Kolkata")
MARKET_OPEN = dtime(9, 15)
MARKET_CLOSE = dtime(15, 30)

log = logging.getLogger("etf_strategy")


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class StrategyParams:
    entry_move_pct: float = 1.25          # move from previous close that triggers a buy
    entry_direction: str = "down"         # "down" = buy dips, "up" = buy gains
    min_avg_volume: float = 50_000        # 20-day average volume must be ABOVE this
    volume_lookback_days: int = 20
    allocation_pct: float = 8.0           # % of total portfolio value per buy
    target_pct: float = 5.5               # sell at +5.5% from entry
    cooldown_trading_days: int = 1        # blocked trading days after a buy
    max_lots_per_symbol: Optional[int] = None   # None = unlimited
    max_new_buys_per_day: Optional[int] = None  # None = unlimited
    allow_same_day_exit: bool = False     # sell a lot on the day it was bought?
    transaction_cost_pct: float = 0.10    # per side (brokerage/STT/charges estimate)

    def validate(self) -> None:
        if self.entry_direction not in ("down", "up"):
            raise ValueError("strategy.entry_direction must be 'down' or 'up'")
        for name in ("entry_move_pct", "allocation_pct", "target_pct"):
            if getattr(self, name) <= 0:
                raise ValueError(f"strategy.{name} must be > 0")
        if not 0 < self.allocation_pct <= 100:
            raise ValueError("strategy.allocation_pct must be in (0, 100]")
        if self.volume_lookback_days < 1 or self.cooldown_trading_days < 0:
            raise ValueError("invalid volume_lookback_days / cooldown_trading_days")


@dataclass
class Config:
    api_key: str = ""
    api_secret: str = ""
    access_token: str = ""
    strategy: StrategyParams = field(default_factory=StrategyParams)
    universe: Dict[str, Any] = field(default_factory=dict)
    backtest: Dict[str, Any] = field(default_factory=dict)
    paper: Dict[str, Any] = field(default_factory=dict)
    paths: Dict[str, str] = field(default_factory=dict)
    config_path: Optional[Path] = None

    def path(self, key: str, default: str) -> Path:
        p = Path(self.paths.get(key, default))
        return p if p.is_absolute() else BASE_DIR / p

    @property
    def cache_dir(self) -> Path:
        return self.path("cache_dir", "data/cache")

    @property
    def output_dir(self) -> Path:
        return self.path("output_dir", "output")

    @property
    def log_dir(self) -> Path:
        return self.path("log_dir", "logs")

    @property
    def token_file(self) -> Path:
        return self.path("token_file", "config/.access_token.json")

    @property
    def paper_state_file(self) -> Path:
        return self.path("paper_state_file", "data/paper/paper_state.json")


def _warn_if_world_readable(path: Path) -> None:
    if os.name != "posix":
        return
    mode = path.stat().st_mode
    if mode & (stat.S_IRGRP | stat.S_IROTH):
        log.warning("%s is readable by other users; run: chmod 600 %s", path, path)


def load_config(path: Optional[Path]) -> Config:
    """Load config JSON; environment variables override credentials."""
    cfg_path = Path(path) if path else DEFAULT_CONFIG
    raw: Dict[str, Any] = {}
    if cfg_path.exists():
        _warn_if_world_readable(cfg_path)
        try:
            raw = json.loads(cfg_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise SystemExit(f"Invalid JSON in {cfg_path}: {exc}") from exc
    else:
        log.warning("Config %s not found; using defaults + environment variables. "
                    "Copy config/kite_config.example.json to create it.", cfg_path)

    kite = raw.get("kite", {})
    known = set(StrategyParams.__dataclass_fields__)
    strat_raw = {k: v for k, v in raw.get("strategy", {}).items() if k in known}
    unknown = set(raw.get("strategy", {})) - known
    if unknown:
        log.warning("Ignoring unknown strategy keys: %s", ", ".join(sorted(unknown)))
    params = StrategyParams(**strat_raw)
    params.validate()

    return Config(
        api_key=os.environ.get("KITE_API_KEY") or kite.get("api_key", ""),
        api_secret=os.environ.get("KITE_API_SECRET") or kite.get("api_secret", ""),
        access_token=os.environ.get("KITE_ACCESS_TOKEN") or kite.get("access_token", ""),
        strategy=params,
        universe=raw.get("universe", {}),
        backtest=raw.get("backtest", {}),
        paper=raw.get("paper", {}),
        paths=raw.get("paths", {}),
        config_path=cfg_path,
    )


def setup_logging(log_dir: Path, verbose: bool = False) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s | %(levelname)-7s | %(message)s", "%Y-%m-%d %H:%M:%S")
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.DEBUG)
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.DEBUG if verbose else logging.INFO)
    console.setFormatter(fmt)
    fileh = RotatingFileHandler(log_dir / "etf_strategy.log", maxBytes=5_000_000,
                                backupCount=5, encoding="utf-8")
    fileh.setLevel(logging.DEBUG)
    fileh.setFormatter(fmt)
    root.addHandler(console)
    root.addHandler(fileh)
    for noisy in ("urllib3", "kiteconnect"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def now_ist() -> datetime:
    return datetime.now(IST)


# ---------------------------------------------------------------------------
# Kite Connect access (auth, instruments, historical data, quotes)
# ---------------------------------------------------------------------------

class AuthError(RuntimeError):
    """Raised when the Kite session is missing/expired: run `login`."""


class RateLimiter:
    def __init__(self, min_interval: float):
        self.min_interval = min_interval
        self._last = 0.0

    def wait(self) -> None:
        delay = self._last + self.min_interval - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        self._last = time.monotonic()


class KiteClient:
    """Thin, defensive wrapper around KiteConnect (read-only calls only)."""

    HIST_CHUNK_DAYS = 1900   # Kite allows max 2000 days per 'day' candle request
    QUOTE_BATCH = 250        # Kite allows max 500 instruments per quote call

    def __init__(self, cfg: Config):
        try:
            from kiteconnect import KiteConnect
            from kiteconnect import exceptions as kex
        except ImportError as exc:  # pragma: no cover
            raise SystemExit("kiteconnect is required: pip install -r requirements.txt") from exc
        if not cfg.api_key:
            raise SystemExit("Kite api_key missing. Set it in config/kite_config.json "
                             "or the KITE_API_KEY environment variable.")
        self.cfg = cfg
        self.kex = kex
        self.kite = KiteConnect(api_key=cfg.api_key, timeout=15)
        self._hist_rl = RateLimiter(0.35)   # historical API: 3 req/s
        self._quote_rl = RateLimiter(1.05)  # quote API: 1 req/s
        self._instruments: Optional[pd.DataFrame] = None

    # ---- authentication ------------------------------------------------------
    def login_interactive(self, request_token: Optional[str] = None) -> None:
        if not self.cfg.api_secret:
            raise SystemExit("Kite api_secret missing (config or KITE_API_SECRET).")
        if not request_token:
            print("\n1. Open this URL in a browser and log in to Zerodha:\n")
            print("   " + self.kite.login_url() + "\n")
            print("2. After login you are redirected to your app's redirect URL.")
            request_token = input("3. Paste the full redirect URL (or just request_token): ").strip()
        if request_token.startswith("http"):
            qs = parse_qs(urlparse(request_token).query)
            if "request_token" not in qs:
                raise SystemExit("No request_token found in that URL.")
            request_token = qs["request_token"][0]
        try:
            session = self.kite.generate_session(request_token, api_secret=self.cfg.api_secret)
        except self.kex.KiteException as exc:
            raise SystemExit(f"Login failed: {exc}") from exc
        token = session["access_token"]
        self._save_token(token)
        self.kite.set_access_token(token)
        log.info("Logged in as %s. Access token saved to %s (valid until ~6 AM tomorrow).",
                 session.get("user_id", "?"), self.cfg.token_file)

    def _save_token(self, token: str) -> None:
        path = self.cfg.token_file
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"access_token": token, "created_at": now_ist().isoformat()}
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)

    def _load_saved_token(self) -> str:
        path = self.cfg.token_file
        if not path.exists():
            return ""
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return ""
        created = datetime.fromisoformat(data.get("created_at", "1970-01-01T00:00:00+05:30"))
        # Kite tokens expire at ~06:00 IST the next day.
        expiry = datetime.combine(created.date() + timedelta(days=1), dtime(6, 0), IST)
        if created.time() < dtime(6, 0):
            expiry -= timedelta(days=1)
        if now_ist() >= expiry:
            log.info("Saved access token has expired; run: python etf_strategy.py login")
            return ""
        return data.get("access_token", "")

    def connect(self) -> None:
        token = self.cfg.access_token or self._load_saved_token()
        if not token:
            raise AuthError("No valid Kite access token. Run: python etf_strategy.py login")
        self.kite.set_access_token(token)
        try:
            profile = self._call(self.kite.profile)
        except AuthError:
            raise AuthError("Kite rejected the access token (expired?). "
                            "Run: python etf_strategy.py login")
        log.info("Connected to Kite as %s", profile.get("user_id", "?"))

    # ---- resilient call helper ----------------------------------------------
    def _call(self, fn: Callable, *args, limiter: Optional[RateLimiter] = None,
              retries: int = 4, **kwargs):
        import requests  # dependency of kiteconnect
        for attempt in range(1, retries + 1):
            if limiter:
                limiter.wait()
            try:
                return fn(*args, **kwargs)
            except (self.kex.TokenException, self.kex.PermissionException) as exc:
                raise AuthError(str(exc)) from exc
            except self.kex.InputException:
                raise
            except (self.kex.NetworkException, self.kex.DataException,
                    self.kex.GeneralException, requests.exceptions.RequestException) as exc:
                if attempt == retries:
                    raise
                wait = 2 ** attempt
                log.warning("Kite call %s failed (%s); retry %d/%d in %ds",
                            getattr(fn, "__name__", fn), exc, attempt, retries - 1, wait)
                time.sleep(wait)

    # ---- instruments -----------------------------------------------------------
    def instruments(self) -> pd.DataFrame:
        """NSE instrument dump, cached on disk once per day."""
        if self._instruments is not None:
            return self._instruments
        cache = self.cfg.cache_dir / f"instruments_NSE_{now_ist().date()}.csv"
        if cache.exists():
            df = pd.read_csv(cache, dtype={"tradingsymbol": str, "name": str})
        else:
            log.info("Downloading NSE instrument list from Kite...")
            rows = self._call(self.kite.instruments, "NSE")
            df = pd.DataFrame(rows)[["instrument_token", "tradingsymbol", "name",
                                     "instrument_type", "segment", "lot_size", "tick_size"]]
            cache.parent.mkdir(parents=True, exist_ok=True)
            for old in cache.parent.glob("instruments_NSE_*.csv"):
                old.unlink(missing_ok=True)
            df.to_csv(cache, index=False)
        df["name"] = df["name"].fillna("")
        self._instruments = df
        return df

    # ---- historical data ------------------------------------------------------
    def daily_history(self, token: int, start: date, end: date) -> pd.DataFrame:
        """Daily OHLCV for [start, end], served from an incremental disk cache."""
        hist_dir = self.cfg.cache_dir / "hist"
        hist_dir.mkdir(parents=True, exist_ok=True)
        csv_path = hist_dir / f"day_{token}.csv"
        meta_path = hist_dir / f"day_{token}.meta.json"

        cached = pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
        cov_from = cov_to = None
        if csv_path.exists() and meta_path.exists():
            try:
                cached = pd.read_csv(csv_path, parse_dates=["date"])
                cached["date"] = cached["date"].dt.date
                meta = json.loads(meta_path.read_text())
                cov_from = date.fromisoformat(meta["from"])
                cov_to = date.fromisoformat(meta["to"])
            except Exception as exc:  # corrupt cache -> refetch
                log.warning("Discarding bad cache for %s: %s", token, exc)
                cached = cached.iloc[0:0]
                cov_from = cov_to = None

        ranges: List[Tuple[date, date]] = []
        if cov_from is None:
            ranges.append((start, end))
        else:
            if start < cov_from:
                ranges.append((start, cov_from - timedelta(days=1)))
            if end > cov_to:
                ranges.append((cov_to + timedelta(days=1), end))

        frames = [cached] + [self._fetch_daily(token, a, b) for a, b in ranges]
        if ranges:
            non_empty = [f for f in frames if not f.empty]
            df = pd.concat(non_empty, ignore_index=True) if non_empty else cached
            df = df.drop_duplicates("date", keep="last").sort_values("date")
            df.to_csv(csv_path, index=False)
            new_from = min(start, cov_from) if cov_from else start
            new_to = max(end, cov_to) if cov_to else end
            meta_path.write_text(json.dumps({"from": str(new_from), "to": str(new_to)}))
        else:
            df = cached
        df = df[(df["date"] >= start) & (df["date"] <= end)]
        return df.reset_index(drop=True)

    def _fetch_daily(self, token: int, start: date, end: date) -> pd.DataFrame:
        rows: List[dict] = []
        cur = start
        while cur <= end:
            chunk_end = min(end, cur + timedelta(days=self.HIST_CHUNK_DAYS))
            data = self._call(self.kite.historical_data, token, cur, chunk_end, "day",
                              limiter=self._hist_rl)
            rows.extend(data or [])
            cur = chunk_end + timedelta(days=1)
        if not rows:
            return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
        df = pd.DataFrame(rows)[["date", "open", "high", "low", "close", "volume"]]
        df["date"] = pd.to_datetime(df["date"]).dt.date
        return df

    # ---- live quotes -----------------------------------------------------------
    def quotes(self, symbols: Iterable[str]) -> Dict[str, dict]:
        """Full quotes keyed by trading symbol (NSE)."""
        symbols = list(symbols)
        out: Dict[str, dict] = {}
        for i in range(0, len(symbols), self.QUOTE_BATCH):
            batch = [f"NSE:{s}" for s in symbols[i:i + self.QUOTE_BATCH]]
            data = self._call(self.kite.quote, batch, limiter=self._quote_rl) or {}
            for key, q in data.items():
                out[key.split(":", 1)[1]] = q
        return out


# ---------------------------------------------------------------------------
# ETF universe
# ---------------------------------------------------------------------------

_ETF_NAME_RE = re.compile(r"\bETF\b|\bBEES\b")
DEFAULT_EXCLUDE_KEYWORDS = ["LIQUID", "OVERNIGHT", "MONEY MARKET", "1D RATE", "GILT", "SDL"]


def build_universe(instruments: pd.DataFrame, ucfg: Dict[str, Any]) -> Dict[str, int]:
    """Return {tradingsymbol: instrument_token} for the ETF universe.

    universe.source:
      "auto" - every NSE ETF found in the Kite instrument dump (default)
      "file" - only symbols listed in universe.symbols_file (one per line / 'symbol' column)
      "both" - union of the two
    """
    source = ucfg.get("source", "auto")
    eq = instruments[(instruments["segment"] == "NSE") & (instruments["instrument_type"] == "EQ")]
    by_symbol = dict(zip(eq["tradingsymbol"], eq["instrument_token"].astype(int)))

    chosen: Dict[str, int] = {}
    if source in ("auto", "both"):
        excl_kw = [k.upper() for k in ucfg.get("exclude_name_keywords", DEFAULT_EXCLUDE_KEYWORDS)]
        for sym, name, tok in zip(eq["tradingsymbol"], eq["name"], eq["instrument_token"]):
            sym_u, name_u = str(sym).upper(), str(name).upper()
            if "-" in sym_u:              # skip non-EQ series suffixes like -BE / -RR
                continue
            is_etf = ("ETF" in sym_u or sym_u.endswith("BEES") or _ETF_NAME_RE.search(name_u))
            if not is_etf:
                continue
            if any(k in name_u or k in sym_u for k in excl_kw):
                continue
            chosen[sym_u] = int(tok)

    if source in ("file", "both"):
        fpath = Path(ucfg.get("symbols_file", "config/etf_universe.csv"))
        fpath = fpath if fpath.is_absolute() else BASE_DIR / fpath
        if not fpath.exists():
            raise SystemExit(f"universe.symbols_file not found: {fpath}")
        for sym in _read_symbols_file(fpath):
            if sym in by_symbol:
                chosen[sym] = by_symbol[sym]
            else:
                log.warning("Universe file symbol %s not found on NSE; skipped", sym)

    for sym in ucfg.get("extra_symbols", []):
        sym = sym.strip().upper()
        if sym in by_symbol:
            chosen[sym] = by_symbol[sym]
        else:
            log.warning("extra_symbols entry %s not found on NSE; skipped", sym)
    for sym in ucfg.get("exclude_symbols", []):
        chosen.pop(sym.strip().upper(), None)

    if not chosen:
        raise SystemExit("ETF universe is empty; check the 'universe' section of the config.")
    log.info("ETF universe: %d instruments (source=%s)", len(chosen), source)
    return dict(sorted(chosen.items()))


def _read_symbols_file(path: Path) -> List[str]:
    syms: List[str] = []
    with path.open(encoding="utf-8") as fh:
        reader = csv.reader(fh)
        for row in reader:
            if not row or row[0].strip().startswith("#"):
                continue
            s = row[0].strip().upper()
            if s and s not in ("SYMBOL", "TRADINGSYMBOL"):
                syms.append(s)
    return syms


# ---------------------------------------------------------------------------
# Strategy rules (pure logic shared by backtest and paper trading)
# ---------------------------------------------------------------------------

class Strategy:
    def __init__(self, p: StrategyParams):
        self.p = p

    def entry_trigger(self, prev_close: float) -> float:
        m = self.p.entry_move_pct / 100.0
        return prev_close * (1 - m) if self.p.entry_direction == "down" else prev_close * (1 + m)

    def is_entry(self, price: float, prev_close: float) -> bool:
        trig = self.entry_trigger(prev_close)
        return price <= trig if self.p.entry_direction == "down" else price >= trig

    def move_pct(self, price: float, prev_close: float) -> float:
        return (price / prev_close - 1) * 100.0

    def signal_strength(self, price: float, prev_close: float) -> float:
        """Larger = stronger signal (used to rank when cash is limited)."""
        mv = self.move_pct(price, prev_close)
        return -mv if self.p.entry_direction == "down" else mv

    def daily_bar_fill(self, o: float, h: float, lo: float, prev_close: float) -> Optional[float]:
        """Backtest fill price for a daily bar, or None if the trigger wasn't hit.
        A gap through the trigger fills at the open (conservative)."""
        trig = self.entry_trigger(prev_close)
        if self.p.entry_direction == "down":
            return min(o, trig) if lo <= trig else None
        return max(o, trig) if h >= trig else None

    def volume_ok(self, avg_volume: Optional[float]) -> bool:
        return avg_volume is not None and not math.isnan(avg_volume) \
            and avg_volume > self.p.min_avg_volume

    def target_price(self, entry: float) -> float:
        return round(entry * (1 + self.p.target_pct / 100.0), 4)

    def position_qty(self, portfolio_value: float, price: float) -> int:
        if price <= 0:
            return 0
        return int((portfolio_value * self.p.allocation_pct / 100.0) // price)


# ---------------------------------------------------------------------------
# Portfolio / position tracking
# ---------------------------------------------------------------------------

@dataclass
class Lot:
    lot_id: int
    symbol: str
    qty: int
    entry_price: float
    entry_date: str
    target_price: float
    entry_cost: float
    entry_time: str = ""


@dataclass
class ClosedTrade:
    lot_id: int
    symbol: str
    qty: int
    entry_date: str
    entry_price: float
    exit_date: str
    exit_price: float
    pnl: float
    return_pct: float
    holding_days: int
    exit_reason: str


class Portfolio:
    def __init__(self, cash: float, cost_pct: float):
        self.initial_capital = cash
        self.cash = cash
        self.cost_pct = cost_pct
        self.lots: List[Lot] = []
        self.closed: List[ClosedTrade] = []
        self.last_buy: Dict[str, str] = {}   # symbol -> ISO date of most recent buy
        self._next_id = 1

    # ---- valuation ------------------------------------------------------------
    def holdings_value(self, prices: Dict[str, float]) -> float:
        return sum(l.qty * prices.get(l.symbol, l.entry_price) for l in self.lots)

    def equity(self, prices: Dict[str, float]) -> float:
        return self.cash + self.holdings_value(prices)

    def lots_for(self, symbol: str) -> int:
        return sum(1 for l in self.lots if l.symbol == symbol)

    # ---- trading --------------------------------------------------------------
    def cost_of(self, qty: int, price: float) -> float:
        return qty * price * self.cost_pct / 100.0

    def can_afford(self, qty: int, price: float) -> bool:
        return qty * price + self.cost_of(qty, price) <= self.cash + 1e-9

    def buy(self, symbol: str, qty: int, price: float, day: date, target: float,
            ts: str = "") -> Lot:
        if qty <= 0 or not self.can_afford(qty, price):
            raise ValueError(f"cannot buy {qty} {symbol} @ {price:.2f} with cash {self.cash:.2f}")
        cost = self.cost_of(qty, price)
        self.cash -= qty * price + cost
        lot = Lot(self._next_id, symbol, qty, round(price, 4), day.isoformat(),
                  target, round(cost, 4), ts)
        self._next_id += 1
        self.lots.append(lot)
        self.last_buy[symbol] = day.isoformat()
        return lot

    def sell(self, lot: Lot, price: float, day: date, reason: str) -> ClosedTrade:
        cost = self.cost_of(lot.qty, price)
        self.cash += lot.qty * price - cost
        pnl = lot.qty * (price - lot.entry_price) - lot.entry_cost - cost
        invested = lot.qty * lot.entry_price
        trade = ClosedTrade(
            lot.lot_id, lot.symbol, lot.qty, lot.entry_date, lot.entry_price,
            day.isoformat(), round(price, 4), round(pnl, 2),
            round(pnl / invested * 100.0, 3) if invested else 0.0,
            (day - date.fromisoformat(lot.entry_date)).days, reason)
        self.lots.remove(lot)
        self.closed.append(trade)
        return trade

    # ---- persistence ----------------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "initial_capital": self.initial_capital, "cash": self.cash,
            "cost_pct": self.cost_pct, "next_id": self._next_id,
            "last_buy": self.last_buy,
            "lots": [asdict(l) for l in self.lots],
            "closed": [asdict(t) for t in self.closed],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Portfolio":
        pf = cls(d["initial_capital"], d.get("cost_pct", 0.0))
        pf.cash = d["cash"]
        pf._next_id = d.get("next_id", 1)
        pf.last_buy = d.get("last_buy", {})
        pf.lots = [Lot(**x) for x in d.get("lots", [])]
        pf.closed = [ClosedTrade(**x) for x in d.get("closed", [])]
        return pf


def cooldown_ok(last_buy: Optional[str], recent_sessions: List[date], today: date,
                cooldown: int) -> bool:
    """True if a symbol may be bought today.

    recent_sessions: trading days strictly before `today`, ascending.
    Blocked if bought today, or on any of the last `cooldown` sessions.
    """
    if not last_buy:
        return True
    lb = date.fromisoformat(last_buy)
    if lb >= today:
        return False
    if cooldown <= 0:
        return True
    window = recent_sessions[-cooldown:]
    return not window or lb < window[0]


# ---------------------------------------------------------------------------
# Backtest
# ---------------------------------------------------------------------------

def prepare_bars(df: pd.DataFrame, lookback: int) -> pd.DataFrame:
    """Add prev_close and avg_vol (mean of the previous `lookback` sessions)."""
    df = df.sort_values("date").drop_duplicates("date").reset_index(drop=True)
    df = df[(df["close"] > 0)].copy()
    df["prev_close"] = df["close"].shift(1)
    df["avg_vol"] = df["volume"].shift(1).rolling(lookback, min_periods=lookback).mean()
    return df


class Backtester:
    """Daily-bar event loop. Per day: exits -> entries -> (same-day exits) -> mark-to-market."""

    def __init__(self, params: StrategyParams, capital: float):
        self.p = params
        self.strat = Strategy(params)
        self.capital = capital

    def run(self, data: Dict[str, pd.DataFrame], start: date, end: date) -> dict:
        bars: Dict[str, Dict[date, tuple]] = {}
        for sym, df in data.items():
            df = prepare_bars(df, self.p.volume_lookback_days)
            bars[sym] = {r.date: (r.open, r.high, r.low, r.close, r.prev_close, r.avg_vol)
                         for r in df.itertuples(index=False)}
        calendar = sorted({d for b in bars.values() for d in b})
        if not calendar:
            raise SystemExit("No historical data to backtest.")
        pf = Portfolio(self.capital, self.p.transaction_cost_pct)
        last_close: Dict[str, float] = {}
        day_index: Dict[str, int] = {}          # symbol -> calendar index of last buy
        equity_rows: List[dict] = []
        stats = {"signals": 0, "skipped_cash": 0, "skipped_cooldown": 0,
                 "skipped_volume": 0, "skipped_max_lots": 0, "skipped_qty0": 0,
                 "skipped_daily_cap": 0}

        for i, day in enumerate(calendar):
            # keep the warm-up period for price/volume history only
            if day < start:
                for sym, b in bars.items():
                    if day in b:
                        last_close[sym] = b[day][3]
                continue
            if day > end:
                break

            # 1) exits for lots bought on earlier days
            for lot in list(pf.lots):
                b = bars[lot.symbol].get(day)
                if b is None or lot.entry_date == day.isoformat():
                    continue
                o, h = b[0], b[1]
                if o >= lot.target_price:
                    pf.sell(lot, o, day, "target_gap_open")
                elif h >= lot.target_price:
                    pf.sell(lot, lot.target_price, day, "target")

            # 2) entries, sized on start-of-day portfolio value
            equity_sod = pf.equity(last_close)
            cands = []
            for sym, b in bars.items():
                bar = b.get(day)
                if bar is None:
                    continue
                o, h, lo, c, pc, av = bar
                if pc is None or math.isnan(pc):
                    continue
                fill = self.strat.daily_bar_fill(o, h, lo, pc)
                if fill is None:
                    continue
                stats["signals"] += 1
                if not self.strat.volume_ok(av):
                    stats["skipped_volume"] += 1
                    continue
                last_i = day_index.get(sym)
                if last_i is not None and i - last_i <= self.p.cooldown_trading_days:
                    stats["skipped_cooldown"] += 1
                    continue
                if self.p.max_lots_per_symbol and pf.lots_for(sym) >= self.p.max_lots_per_symbol:
                    stats["skipped_max_lots"] += 1
                    continue
                cands.append((self.strat.signal_strength(fill, pc), sym, fill))
            cands.sort(reverse=True)
            bought = 0
            for _, sym, fill in cands:
                if self.p.max_new_buys_per_day and bought >= self.p.max_new_buys_per_day:
                    stats["skipped_daily_cap"] += 1
                    continue
                qty = self.strat.position_qty(equity_sod, fill)
                if qty < 1:
                    stats["skipped_qty0"] += 1
                    continue
                if not pf.can_afford(qty, fill):
                    stats["skipped_cash"] += 1
                    continue
                pf.buy(sym, qty, fill, day, self.strat.target_price(fill))
                day_index[sym] = i
                bought += 1

            # 3) optional same-day exit (only knowable at the close on daily bars)
            if self.p.allow_same_day_exit:
                for lot in list(pf.lots):
                    if lot.entry_date == day.isoformat():
                        c = bars[lot.symbol][day][3]
                        if c >= lot.target_price:
                            pf.sell(lot, c, day, "target_same_day_close")

            # 4) mark to market
            for sym, b in bars.items():
                if day in b:
                    last_close[sym] = b[day][3]
            hv = pf.holdings_value(last_close)
            equity_rows.append({"date": day, "cash": round(pf.cash, 2),
                                "holdings": round(hv, 2), "equity": round(pf.cash + hv, 2),
                                "open_lots": len(pf.lots)})

        equity = pd.DataFrame(equity_rows)
        trades = pd.DataFrame([asdict(t) for t in pf.closed])
        open_lots = pd.DataFrame([{**asdict(l),
                                   "last_close": last_close.get(l.symbol, l.entry_price),
                                   "unrealized_pnl": round(l.qty * (last_close.get(
                                       l.symbol, l.entry_price) - l.entry_price), 2)}
                                  for l in pf.lots])
        return {"portfolio": pf, "equity": equity, "trades": trades,
                "open_lots": open_lots, "stats": stats}


def summarize_backtest(res: dict, capital: float) -> dict:
    eq: pd.DataFrame = res["equity"]
    trades: pd.DataFrame = res["trades"]
    if eq.empty:
        return {"error": "no equity rows (date range outside data?)"}
    final = float(eq["equity"].iloc[-1])
    days = max((eq["date"].iloc[-1] - eq["date"].iloc[0]).days, 1)
    rets = eq["equity"].pct_change().dropna()
    dd = eq["equity"] / eq["equity"].cummax() - 1
    s = {
        "start": str(eq["date"].iloc[0]), "end": str(eq["date"].iloc[-1]),
        "initial_capital": capital, "final_equity": round(final, 2),
        "total_return_pct": round((final / capital - 1) * 100, 2),
        "cagr_pct": round(((final / capital) ** (365.0 / days) - 1) * 100, 2),
        "max_drawdown_pct": round(float(dd.min()) * 100, 2),
        "sharpe": round(float(rets.mean() / rets.std() * math.sqrt(252)), 2)
        if len(rets) > 1 and rets.std() > 0 else None,
        "avg_exposure_pct": round(float((eq["holdings"] / eq["equity"]).mean() * 100), 2),
        "closed_trades": int(len(trades)),
        "realized_pnl": round(float(trades["pnl"].sum()), 2) if not trades.empty else 0.0,
        "win_rate_pct": round(float((trades["pnl"] > 0).mean() * 100), 2) if not trades.empty else None,
        "avg_holding_days": round(float(trades["holding_days"].mean()), 1) if not trades.empty else None,
        "max_holding_days": int(trades["holding_days"].max()) if not trades.empty else None,
        "open_lots": int(len(res["open_lots"])),
        "unrealized_pnl": round(float(res["open_lots"]["unrealized_pnl"].sum()), 2)
        if not res["open_lots"].empty else 0.0,
        **{f"signal_{k}": v for k, v in res["stats"].items()},
    }
    return s


def load_backtest_data(client: KiteClient, universe: Dict[str, int], start: date,
                       end: date, lookback: int) -> Dict[str, pd.DataFrame]:
    warmup_start = start - timedelta(days=int(lookback * 2.2) + 15)
    data: Dict[str, pd.DataFrame] = {}
    n = len(universe)
    for k, (sym, tok) in enumerate(universe.items(), 1):
        try:
            df = client.daily_history(tok, warmup_start, end)
        except AuthError:
            raise
        except Exception as exc:
            log.error("History fetch failed for %s (%s): %s — skipped", sym, tok, exc)
            continue
        if df.empty:
            log.debug("No data for %s", sym)
            continue
        data[sym] = df
        if k % 25 == 0 or k == n:
            log.info("Loaded history %d/%d", k, n)
    return data


def last_completed_session_day() -> date:
    """Today if the market has closed (after 16:00 IST), else yesterday."""
    now = now_ist()
    return now.date() if now.time() >= dtime(16, 0) else now.date() - timedelta(days=1)


def cmd_backtest(cfg: Config, args) -> None:
    bcfg = cfg.backtest
    start = date.fromisoformat(args.start or bcfg.get("start", "2022-01-01"))
    end_s = args.end or bcfg.get("end")
    end = date.fromisoformat(end_s) if end_s else last_completed_session_day()
    end = min(end, last_completed_session_day())
    capital = float(args.capital or bcfg.get("initial_capital", 1_000_000))
    if start >= end:
        raise SystemExit("backtest start must be before end")

    client = KiteClient(cfg)
    client.connect()
    universe = build_universe(client.instruments(), cfg.universe)
    log.info("Backtest %s -> %s, capital %.0f, %d ETFs", start, end, capital, len(universe))
    data = load_backtest_data(client, universe, start, end, cfg.strategy.volume_lookback_days)
    run_and_report_backtest(cfg, data, start, end, capital)


def run_and_report_backtest(cfg: Config, data: Dict[str, pd.DataFrame], start: date,
                            end: date, capital: float) -> Path:
    res = Backtester(cfg.strategy, capital).run(data, start, end)
    summary = summarize_backtest(res, capital)
    summary["params"] = asdict(cfg.strategy)
    summary["universe_size"] = len(data)

    out = cfg.output_dir / f"backtest_{now_ist():%Y%m%d_%H%M%S}"
    out.mkdir(parents=True, exist_ok=True)
    res["trades"].to_csv(out / "trades.csv", index=False)
    res["equity"].to_csv(out / "equity_curve.csv", index=False)
    res["open_lots"].to_csv(out / "open_positions.csv", index=False)
    (out / "summary.json").write_text(json.dumps(summary, indent=2, default=str))
    if not res["trades"].empty:
        per_sym = res["trades"].groupby("symbol").agg(
            trades=("pnl", "size"), pnl=("pnl", "sum"),
            avg_hold_days=("holding_days", "mean")).sort_values("pnl", ascending=False)
        per_sym.round(2).to_csv(out / "per_symbol.csv")
    _plot_equity(res["equity"], out / "equity_curve.png")

    print("\n" + "=" * 60 + "\n BACKTEST SUMMARY\n" + "=" * 60)
    for k, v in summary.items():
        if k != "params":
            print(f" {k:<28} {v}")
    print("=" * 60)
    print(f" Reports written to: {out}\n")
    return out


def _plot_equity(eq: pd.DataFrame, path: Path) -> None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        log.info("matplotlib not installed; skipping equity chart")
        return
    if eq.empty:
        return
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(11, 6), sharex=True,
                                   gridspec_kw={"height_ratios": [3, 1]})
    ax1.plot(pd.to_datetime(eq["date"]), eq["equity"], color="#2a6fdb", lw=1.4)
    ax1.set_title("ETF strategy — equity curve")
    ax1.set_ylabel("Equity (₹)")
    ax1.grid(alpha=0.3)
    dd = (eq["equity"] / eq["equity"].cummax() - 1) * 100
    ax2.fill_between(pd.to_datetime(eq["date"]), dd, 0, color="#d94f4f", alpha=0.5)
    ax2.set_ylabel("Drawdown %")
    ax2.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


# ---------------------------------------------------------------------------
# Paper trading
# ---------------------------------------------------------------------------

class PaperTrader:
    """Simulates the strategy on live Kite quotes. Never sends orders."""

    def __init__(self, cfg: Config, client: KiteClient):
        self.cfg = cfg
        self.client = client
        self.p = cfg.strategy
        self.strat = Strategy(self.p)
        pcfg = cfg.paper
        self.poll_seconds = max(int(pcfg.get("poll_seconds", 60)), 5)
        self.entry_start = dtime.fromisoformat(pcfg.get("entry_start", "09:20"))
        self.entry_end = dtime.fromisoformat(pcfg.get("entry_end", "15:25"))
        self.state_file = cfg.paper_state_file
        self.pf = self._load_state(float(pcfg.get("initial_capital", 1_000_000)))
        self.universe: Dict[str, int] = {}
        self.avg_vol: Dict[str, float] = {}
        self.sessions: List[date] = []     # trading days before today
        self.prices: Dict[str, float] = {}
        self.today = now_ist().date()

    # ---- state -----------------------------------------------------------------
    def _load_state(self, capital: float) -> Portfolio:
        if self.state_file.exists():
            try:
                pf = Portfolio.from_dict(json.loads(self.state_file.read_text()))
                log.info("Loaded paper portfolio: cash %.2f, %d open lots, %d closed trades",
                         pf.cash, len(pf.lots), len(pf.closed))
                return pf
            except Exception as exc:
                raise SystemExit(f"Paper state {self.state_file} is corrupt ({exc}). "
                                 "Fix it or run `reset-paper`.") from exc
        log.info("New paper portfolio with capital %.2f", capital)
        return Portfolio(capital, self.p.transaction_cost_pct)

    def save_state(self) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.pf.to_dict(), indent=2))
        tmp.replace(self.state_file)   # atomic replace: no half-written state

    def _journal(self, row: dict) -> None:
        path = self.state_file.parent / "paper_trades.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        new = not path.exists()
        with path.open("a", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(row))
            if new:
                w.writeheader()
            w.writerow(row)

    # ---- daily preparation -------------------------------------------------------
    def prepare_day(self) -> None:
        """Load universe, 20-day average volumes and recent session dates."""
        self.universe = build_universe(self.client.instruments(), self.cfg.universe)
        self.today = now_ist().date()
        cache = self.cfg.cache_dir / f"paper_prep_{self.today}.json"
        if cache.exists():
            d = json.loads(cache.read_text())
            self.avg_vol = d["avg_vol"]
            self.sessions = [date.fromisoformat(x) for x in d["sessions"]]
            log.info("Loaded today's prepared volume data (%d ETFs)", len(self.avg_vol))
            return
        lookback = self.p.volume_lookback_days
        start = self.today - timedelta(days=int(lookback * 2.2) + 15)
        end = self.today - timedelta(days=1)
        sessions: set = set()
        log.info("Preparing %d-day average volume for %d ETFs (first run of the day "
                 "takes a minute)...", lookback, len(self.universe))
        for k, (sym, tok) in enumerate(self.universe.items(), 1):
            try:
                df = self.client.daily_history(tok, start, end)
            except AuthError:
                raise
            except Exception as exc:
                log.error("History fetch failed for %s: %s — excluded today", sym, exc)
                continue
            df = df[df["date"] < self.today]
            sessions.update(df["date"])
            vols = df["volume"].tail(lookback)
            if len(vols) == lookback:
                self.avg_vol[sym] = float(vols.mean())
            if k % 25 == 0:
                log.info("  prepared %d/%d", k, len(self.universe))
        self.sessions = sorted(sessions)
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({"avg_vol": self.avg_vol,
                                     "sessions": [str(s) for s in self.sessions]}))
        eligible = sum(1 for v in self.avg_vol.values() if self.strat.volume_ok(v))
        log.info("Volume filter: %d of %d ETFs have %d-day avg volume > %s",
                 eligible, len(self.universe), lookback, f"{self.p.min_avg_volume:,.0f}")

    # ---- one evaluation cycle ----------------------------------------------------
    def tick(self, now: Optional[datetime] = None) -> None:
        now = now or now_ist()
        today = now.date()
        symbols = set(self.universe) | {l.symbol for l in self.pf.lots}
        quotes = self.client.quotes(sorted(symbols))
        changed = False

        for sym, q in quotes.items():
            ltp = q.get("last_price")
            if ltp:
                self.prices[sym] = float(ltp)

        # exits first (frees cash for new entries)
        for lot in list(self.pf.lots):
            ltp = self.prices.get(lot.symbol)
            if ltp is None:
                continue
            if lot.entry_date == today.isoformat() and not self.p.allow_same_day_exit:
                continue
            if ltp >= lot.target_price:
                tr = self.pf.sell(lot, ltp, today, "target")
                changed = True
                log.info("PAPER SELL %-12s qty %d @ %.2f (entry %.2f) P&L %.2f (%.2f%%)",
                         tr.symbol, tr.qty, tr.exit_price, tr.entry_price, tr.pnl, tr.return_pct)
                self._journal({"time": now.isoformat(timespec="seconds"), "side": "SELL",
                               "symbol": tr.symbol, "qty": tr.qty, "price": tr.exit_price,
                               "lot_id": tr.lot_id, "pnl": tr.pnl, "reason": tr.exit_reason})

        # entries
        if self.entry_start <= now.time() <= self.entry_end:
            equity = self.pf.equity(self.prices)
            cands = []
            for sym in self.universe:
                q = quotes.get(sym)
                if not q:
                    continue
                ltp = q.get("last_price")
                prev_close = (q.get("ohlc") or {}).get("close")
                if not ltp or not prev_close:
                    continue
                if not self.strat.is_entry(ltp, prev_close):
                    continue
                if not self.strat.volume_ok(self.avg_vol.get(sym)):
                    continue
                if not cooldown_ok(self.pf.last_buy.get(sym), self.sessions, today,
                                   self.p.cooldown_trading_days):
                    continue
                if self.p.max_lots_per_symbol and \
                        self.pf.lots_for(sym) >= self.p.max_lots_per_symbol:
                    continue
                cands.append((self.strat.signal_strength(ltp, prev_close), sym, ltp, prev_close))
            cands.sort(reverse=True)
            bought_today = sum(1 for l in self.pf.lots if l.entry_date == today.isoformat()) + \
                sum(1 for t in self.pf.closed if t.entry_date == today.isoformat())
            for _, sym, ltp, pc in cands:
                if self.p.max_new_buys_per_day and bought_today >= self.p.max_new_buys_per_day:
                    break
                qty = self.strat.position_qty(equity, ltp)
                if qty < 1:
                    log.info("Signal %s @ %.2f skipped: 8%% allocation buys 0 units", sym, ltp)
                    continue
                if not self.pf.can_afford(qty, ltp):
                    log.info("Signal %s @ %.2f skipped: insufficient cash (%.2f)",
                             sym, ltp, self.pf.cash)
                    continue
                lot = self.pf.buy(sym, qty, ltp, today, self.strat.target_price(ltp),
                                  now.isoformat(timespec="seconds"))
                bought_today += 1
                changed = True
                log.info("PAPER BUY  %-12s qty %d @ %.2f (prev close %.2f, %+.2f%%) target %.2f",
                         sym, qty, ltp, pc, self.strat.move_pct(ltp, pc), lot.target_price)
                self._journal({"time": now.isoformat(timespec="seconds"), "side": "BUY",
                               "symbol": sym, "qty": qty, "price": round(ltp, 2), "lot_id": lot.lot_id,
                               "pnl": "", "reason": f"{self.strat.move_pct(ltp, pc):+.2f}% vs prev close"})

        if changed:
            self.save_state()

    # ---- main loop -----------------------------------------------------------------
    def run(self, once: bool = False) -> None:
        self.prepare_day()
        if once:
            self.tick()
            self.report()
            return
        now = now_ist()
        if now.weekday() >= 5:
            log.info("Weekend — market closed. Showing status only.")
            self.report()
            return
        if now.time() >= MARKET_CLOSE:
            log.info("Market already closed for today. Showing status only.")
            self.report()
            return
        if now.time() < MARKET_OPEN:
            wait = (datetime.combine(now.date(), MARKET_OPEN, IST) - now).total_seconds()
            log.info("Waiting %.0f min for market open...", wait / 60)
            time.sleep(max(wait, 0))
        log.info("Paper trading started (poll every %ds). Ctrl+C to stop.", self.poll_seconds)
        errors = 0
        try:
            while now_ist().time() < MARKET_CLOSE:
                try:
                    self.tick()
                    errors = 0
                except AuthError:
                    raise
                except Exception as exc:
                    errors += 1
                    log.exception("Tick failed (%d in a row): %s", errors, exc)
                    if errors >= 10:
                        log.error("Too many consecutive errors; stopping.")
                        break
                time.sleep(self.poll_seconds)
        except KeyboardInterrupt:
            log.info("Stopped by user.")
        finally:
            self.save_state()
            self.report()

    def report(self) -> None:
        pf = self.pf
        prices = self.prices
        hv = pf.holdings_value(prices)
        eq = pf.cash + hv
        realized = sum(t.pnl for t in pf.closed)
        print("\n" + "=" * 78)
        print(f" PAPER PORTFOLIO  {now_ist():%Y-%m-%d %H:%M} IST")
        print("=" * 78)
        print(f" Initial capital : {pf.initial_capital:>14,.2f}")
        print(f" Cash            : {pf.cash:>14,.2f}")
        print(f" Holdings (MTM)  : {hv:>14,.2f}")
        print(f" Total equity    : {eq:>14,.2f}  ({(eq / pf.initial_capital - 1) * 100:+.2f}%)")
        print(f" Realized P&L    : {realized:>14,.2f}  over {len(pf.closed)} closed trades")
        if pf.lots:
            print("-" * 78)
            print(f" {'Lot':>4} {'Symbol':<13} {'Qty':>6} {'Entry':>9} {'Date':<11} "
                  f"{'LTP':>9} {'Target':>9} {'Unrl P&L':>10}")
            for l in sorted(pf.lots, key=lambda x: x.entry_date):
                ltp = prices.get(l.symbol)
                up = f"{l.qty * (ltp - l.entry_price):>10,.2f}" if ltp else f"{'n/a':>10}"
                ltp_s = f"{ltp:>9.2f}" if ltp else f"{'n/a':>9}"
                print(f" {l.lot_id:>4} {l.symbol:<13} {l.qty:>6} {l.entry_price:>9.2f} "
                      f"{l.entry_date:<11} {ltp_s} {l.target_price:>9.2f} {up}")
        print("=" * 78 + "\n")
        snap = self.state_file.parent / "paper_equity.csv"
        snap.parent.mkdir(parents=True, exist_ok=True)
        new = not snap.exists()
        with snap.open("a", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            if new:
                w.writerow(["timestamp", "cash", "holdings", "equity", "open_lots", "realized_pnl"])
            w.writerow([now_ist().isoformat(timespec="seconds"), round(pf.cash, 2),
                        round(hv, 2), round(eq, 2), len(pf.lots), round(realized, 2)])


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def cmd_login(cfg: Config, args) -> None:
    KiteClient(cfg).login_interactive(args.request_token)


def cmd_universe(cfg: Config, args) -> None:
    client = KiteClient(cfg)
    client.connect()
    inst = client.instruments()
    uni = build_universe(inst, cfg.universe)
    names = dict(zip(inst["tradingsymbol"], inst["name"]))
    for sym, tok in uni.items():
        print(f"{sym:<16} {tok:>10}  {names.get(sym, '')}")
    if args.save:
        path = BASE_DIR / "config" / "etf_universe.csv"
        with path.open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["symbol", "instrument_token", "name"])
            for sym, tok in uni.items():
                w.writerow([sym, tok, names.get(sym, "")])
        print(f"\nSaved {len(uni)} symbols to {path}. Edit it and set universe.source "
              f"to \"file\" to trade a fixed list.")


def cmd_paper(cfg: Config, args) -> None:
    client = KiteClient(cfg)
    client.connect()
    PaperTrader(cfg, client).run(once=args.once)


def cmd_status(cfg: Config, args) -> None:
    client = KiteClient(cfg)
    trader = PaperTrader(cfg, client)
    if trader.pf.lots:
        try:
            client.connect()
            quotes = client.quotes({l.symbol for l in trader.pf.lots})
            trader.prices = {s: float(q["last_price"]) for s, q in quotes.items()
                             if q.get("last_price")}
        except AuthError as exc:
            log.warning("%s — showing holdings without live prices.", exc)
    trader.report()


def cmd_reset_paper(cfg: Config, args) -> None:
    f = cfg.paper_state_file
    if f.exists():
        if not args.yes and input(f"Delete paper state {f}? [y/N] ").strip().lower() != "y":
            print("Aborted.")
            return
        backup = f.with_name(f"paper_state_backup_{now_ist():%Y%m%d_%H%M%S}.json")
        f.replace(backup)
        print(f"Paper state moved to {backup}")
    else:
        print("No paper state to reset.")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="NSE ETF strategy (Zerodha Kite) — backtest & paper trading")
    ap.add_argument("--config", type=Path, default=None, help="config JSON path")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("login", help="generate today's Kite access token")
    p.add_argument("--request-token", help="request_token or full redirect URL")
    p.set_defaults(fn=cmd_login)

    p = sub.add_parser("universe", help="list the ETF universe")
    p.add_argument("--save", action="store_true", help="write config/etf_universe.csv")
    p.set_defaults(fn=cmd_universe)

    p = sub.add_parser("backtest", help="run the backtest on Kite daily history")
    p.add_argument("--start")
    p.add_argument("--end")
    p.add_argument("--capital", type=float)
    p.set_defaults(fn=cmd_backtest)

    p = sub.add_parser("paper", help="paper trade today's session on live quotes")
    p.add_argument("--once", action="store_true", help="evaluate once and exit")
    p.set_defaults(fn=cmd_paper)

    sub.add_parser("status", help="show paper portfolio").set_defaults(fn=cmd_status)
    p = sub.add_parser("reset-paper", help="archive paper state and start fresh")
    p.add_argument("--yes", action="store_true")
    p.set_defaults(fn=cmd_reset_paper)

    args = ap.parse_args(argv)
    # logging must exist before config loading so its warnings are visible
    setup_logging(BASE_DIR / "logs", args.verbose)
    cfg = load_config(args.config)
    if cfg.log_dir != BASE_DIR / "logs":
        setup_logging(cfg.log_dir, args.verbose)
    try:
        args.fn(cfg, args)
    except AuthError as exc:
        log.error("%s", exc)
        return 2
    except KeyboardInterrupt:
        log.info("Interrupted.")
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
