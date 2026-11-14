"""Live loop: poll candles during NSE/BSE hours, act once per newly closed candle."""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import time

import pandas as pd

from .data import IST
from .strategy import BUY, SELL, add_signals

log = logging.getLogger("ema_bot")

MARKET_OPEN, MARKET_CLOSE = dt.time(9, 15), dt.time(15, 30)


def now_ist() -> pd.Timestamp:
    return pd.Timestamp.now(tz=IST)


def market_is_open(now: pd.Timestamp) -> bool:
    """Regular session, Monday-Friday. Exchange holidays are not known here;
    on a holiday no new candles arrive, so the bot simply does nothing."""
    return now.weekday() < 5 and MARKET_OPEN <= now.time() < MARKET_CLOSE


def load_state(path: str) -> dict:
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {}


def save_state(path: str, state: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, path)


class Bot:
    """``fetch`` returns the recent candles (IST index, newest last, possibly
    including the candle still forming). ``interval`` is the candle length."""

    def __init__(self, fetch, broker, state: dict, state_path: str, interval: pd.Timedelta,
                 fast: int = 9, slow: int = 21, capital: float = 10_000.0, quantity: int | None = None,
                 stop_loss_pct: float | None = None, intraday: bool = True,
                 exit_time: dt.time = dt.time(15, 10), now=now_ist):
        self.fetch = fetch
        self.broker = broker
        self.state = state
        self.state_path = state_path
        self.interval = interval
        self.fast, self.slow = fast, slow
        self.capital, self.quantity = capital, quantity
        self.stop_loss_pct = stop_loss_pct
        self.intraday = intraday
        self.exit_time = exit_time
        self.now = now

    def _sell(self, label: str, price: float) -> None:
        quantity, avg = self.broker.sell_all(price)
        self.state["entry_price"] = None
        save_state(self.state_path, self.state)
        log.info("%s %d @ ~%.2f | %s", label, quantity, avg, self.broker.describe(price))

    def step(self) -> None:
        """One polling cycle. Safe to call as often as you like."""
        now = self.now()
        candles = self.fetch()
        if candles.empty:
            log.warning("no candles returned")
            return
        last_price = float(candles["close"].iloc[-1])
        today = now.date().isoformat()

        # An intraday position from an earlier day was already squared off by the broker.
        if self.intraday and self.broker.position() > 0 and self.state.get("entry_date") != today:
            log.warning("dropping stale intraday position from %s", self.state.get("entry_date"))
            self.broker.drop_stale(last_price)
            self.state["entry_price"] = None
            save_state(self.state_path, self.state)

        past_exit = self.intraday and now.time() >= self.exit_time
        if past_exit and self.broker.position() > 0:
            self._sell("SQUARE-OFF sold", last_price)

        # Stop-loss is checked on every poll against the latest price.
        entry = self.state.get("entry_price")
        if self.stop_loss_pct and self.broker.position() > 0 and entry:
            if last_price <= entry * (1 - self.stop_loss_pct / 100):
                self._sell("STOP-LOSS sold", last_price)

        closed = add_signals(candles[candles.index + self.interval <= now], self.fast, self.slow)
        if len(closed) <= self.slow:
            log.warning("only %d closed candles; need more than %d", len(closed), self.slow)
            return
        candle_time = closed.index[-1].isoformat()
        if self.state.get("last_candle") == candle_time:
            return  # already handled this candle
        row = closed.iloc[-1]
        signal = int(row.signal)
        first_run = "last_candle" not in self.state
        self.state["last_candle"] = candle_time
        log.info("candle %s close=%.2f ema%d=%.2f ema%d=%.2f signal=%s", candle_time, row.close,
                 self.fast, row.ema_fast, self.slow, row.ema_slow,
                 {BUY: "BUY", SELL: "SELL"}.get(signal, "hold"))

        # An intraday candle that closed long ago (e.g. yesterday's last one) is not actionable.
        stale = self.interval < pd.Timedelta(days=1) and now - (closed.index[-1] + self.interval) > self.interval

        if signal == SELL and self.broker.position() > 0:
            self._sell("SELL", last_price)
        elif signal == BUY and self.broker.position() == 0:
            if first_run:
                log.info("first run: waiting for the next crossover before trading")
            elif stale:
                log.info("skipping BUY: the signal candle is old")
            elif past_exit:
                log.info("skipping BUY: past the intraday exit time")
            else:
                quantity = self.quantity or int(self.capital // last_price)
                if quantity < 1:
                    log.warning("skipping BUY: capital Rs%.2f is less than one share (Rs%.2f)",
                                self.capital, last_price)
                else:
                    filled, avg = self.broker.buy(quantity, last_price)
                    if filled:
                        self.state["entry_price"], self.state["entry_date"] = avg, today
                    log.info("BUY %d @ ~%.2f | %s", filled, avg, self.broker.describe(last_price))
        save_state(self.state_path, self.state)

    def run(self, poll_seconds: int = 20) -> None:
        log.info("EMA %d/%d bot started, polling every %ds during market hours (Ctrl+C to stop)",
                 self.fast, self.slow, poll_seconds)
        waiting = False
        while True:
            if not market_is_open(self.now()):
                if not waiting:
                    log.info("market closed; waiting for 09:15 IST on the next trading day")
                waiting = True
                time.sleep(60)
                continue
            waiting = False
            try:
                self.step()
            except Exception:
                log.exception("cycle failed; retrying next poll")
            time.sleep(poll_seconds)
