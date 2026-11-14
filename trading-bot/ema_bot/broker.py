"""Order execution: a simulated paper broker and a real ccxt spot broker."""
from __future__ import annotations

import logging

log = logging.getLogger("ema_bot")


class PaperBroker:
    """Simulated fills at the last traded price. Balances live in the bot's state file."""

    def __init__(self, state: dict, starting_cash: float, fee_pct: float):
        self.state = state
        self.fee = fee_pct / 100
        state.setdefault("paper_cash", starting_cash)
        state.setdefault("paper_units", 0.0)

    def position(self) -> float:
        return self.state["paper_units"]

    def buy(self, quote_amount: float, price: float) -> float:
        spend = min(quote_amount, self.state["paper_cash"])
        units = spend / (price * (1 + self.fee))
        self.state["paper_cash"] -= spend
        self.state["paper_units"] += units
        return units

    def sell_all(self, price: float) -> float:
        units = self.state["paper_units"]
        self.state["paper_cash"] += units * price * (1 - self.fee)
        self.state["paper_units"] = 0.0
        return units

    def describe(self, price: float) -> str:
        cash, units = self.state["paper_cash"], self.state["paper_units"]
        return f"cash={cash:.2f} units={units:.6f} equity={cash + units * price:.2f}"


class CcxtBroker:
    """Real market orders on a spot exchange. The position is tracked in the
    bot's state file so the bot only ever sells what it bought itself."""

    def __init__(self, exchange, symbol: str, state: dict):
        self.exchange = exchange
        self.symbol = symbol
        self.state = state
        state.setdefault("live_units", 0.0)
        exchange.load_markets()

    def position(self) -> float:
        return self.state["live_units"]

    def buy(self, quote_amount: float, price: float) -> float:
        amount = float(self.exchange.amount_to_precision(self.symbol, quote_amount / price))
        order = self.exchange.create_market_buy_order(self.symbol, amount)
        filled = float(order.get("filled") or amount)
        self.state["live_units"] += filled
        return filled

    def sell_all(self, price: float) -> float:
        base = self.symbol.split("/")[0]
        free = float(self.exchange.fetch_balance().get(base, {}).get("free") or 0)
        # Fees may have been taken in the base asset, so never sell more than is free.
        amount = float(self.exchange.amount_to_precision(self.symbol, min(self.state["live_units"], free)))
        if amount > 0:
            self.exchange.create_market_sell_order(self.symbol, amount)
        self.state["live_units"] = 0.0
        return amount

    def describe(self, price: float) -> str:
        return f"units={self.state['live_units']:.6f} (~{self.state['live_units'] * price:.2f} quote)"
