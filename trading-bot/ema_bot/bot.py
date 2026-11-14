"""Live loop: poll candles, act once per newly closed candle."""
from __future__ import annotations

import json
import logging
import os
import time

from .data import fetch_candles
from .strategy import BUY, SELL, add_signals

log = logging.getLogger("ema_bot")


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
    def __init__(self, exchange, broker, state: dict, state_path: str, symbol: str,
                 timeframe: str, fast: int = 9, slow: int = 21, order_size: float = 100.0,
                 stop_loss_pct: float | None = None):
        self.exchange = exchange
        self.broker = broker
        self.state = state
        self.state_path = state_path
        self.symbol = symbol
        self.timeframe = timeframe
        self.fast, self.slow = fast, slow
        self.order_size = order_size
        self.stop_loss_pct = stop_loss_pct

    def step(self) -> None:
        """One polling cycle. Safe to call as often as you like."""
        candles = fetch_candles(self.exchange, self.symbol, self.timeframe, limit=max(300, self.slow * 10))
        last_price = float(candles["close"].iloc[-1])
        closed = add_signals(candles.iloc[:-1], self.fast, self.slow)  # drop the still-forming candle
        if len(closed) <= self.slow:
            log.warning("only %d closed candles; need more than %d", len(closed), self.slow)
            return

        # Stop-loss is checked on every poll against the latest price.
        entry = self.state.get("entry_price")
        if self.stop_loss_pct and self.broker.position() > 0 and entry:
            if last_price <= entry * (1 - self.stop_loss_pct / 100):
                units = self.broker.sell_all(last_price)
                self.state["entry_price"] = None
                save_state(self.state_path, self.state)
                log.info("STOP-LOSS sold %.6f @ ~%.4f | %s", units, last_price, self.broker.describe(last_price))

        candle_time = closed.index[-1].isoformat()
        if self.state.get("last_candle") == candle_time:
            return  # already handled this candle
        row = closed.iloc[-1]
        signal = int(row.signal)
        first_run = "last_candle" not in self.state
        self.state["last_candle"] = candle_time
        log.info("candle %s close=%.4f ema%d=%.4f ema%d=%.4f signal=%s", candle_time, row.close,
                 self.fast, row.ema_fast, self.slow, row.ema_slow,
                 {BUY: "BUY", SELL: "SELL"}.get(signal, "hold"))

        if first_run:
            # A cross on the candle we happen to start on may be stale; wait for a fresh one.
            log.info("first run: waiting for the next crossover before trading")
        elif signal == BUY and self.broker.position() == 0:
            units = self.broker.buy(self.order_size, last_price)
            self.state["entry_price"] = last_price
            log.info("BUY %.6f @ ~%.4f | %s", units, last_price, self.broker.describe(last_price))
        elif signal == SELL and self.broker.position() > 0:
            units = self.broker.sell_all(last_price)
            self.state["entry_price"] = None
            log.info("SELL %.6f @ ~%.4f | %s", units, last_price, self.broker.describe(last_price))
        save_state(self.state_path, self.state)

    def run(self, poll_seconds: int = 30) -> None:
        log.info("running %s %s EMA %d/%d, polling every %ds (Ctrl+C to stop)",
                 self.symbol, self.timeframe, self.fast, self.slow, poll_seconds)
        while True:
            try:
                self.step()
            except KeyboardInterrupt:
                raise
            except Exception:
                log.exception("cycle failed; retrying next poll")
            time.sleep(poll_seconds)
