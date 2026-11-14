"""Order execution: a simulated paper broker and the real Dhan broker.

Both are long-only and track the bot's own position in its state file, so the
bot never sells shares it did not buy itself.
"""
from __future__ import annotations

import logging
import time

from .data import SEGMENTS, check

log = logging.getLogger("ema_bot")


class PaperBroker:
    """Simulated fills at the last traded price."""

    def __init__(self, state: dict, starting_cash: float, fee_pct: float):
        self.state = state
        self.fee = fee_pct / 100
        state.setdefault("paper_cash", starting_cash)
        state.setdefault("units", 0)

    def position(self) -> int:
        return self.state["units"]

    def buy(self, quantity: int, price: float) -> tuple[int, float]:
        quantity = min(quantity, int(self.state["paper_cash"] // (price * (1 + self.fee))))
        self.state["paper_cash"] -= quantity * price * (1 + self.fee)
        self.state["units"] += quantity
        return quantity, price

    def sell_all(self, price: float) -> tuple[int, float]:
        quantity = self.state["units"]
        self.state["paper_cash"] += quantity * price * (1 - self.fee)
        self.state["units"] = 0
        return quantity, price

    def drop_stale(self, price: float) -> None:
        self.sell_all(price)

    def describe(self, price: float) -> str:
        cash, units = self.state["paper_cash"], self.state["units"]
        return f"cash=Rs{cash:.2f} shares={units} equity=Rs{cash + units * price:.2f}"


class DhanBroker:
    """Real market orders through Dhan. ``product`` is 'INTRADAY' or 'CNC' (delivery)."""

    FINAL_BAD = {"REJECTED", "CANCELLED", "EXPIRED"}

    def __init__(self, dhan, security_id: str, exchange: str, product: str, state: dict,
                 confirm_seconds: float = 10.0):
        self.dhan = dhan
        self.security_id = str(security_id)
        self.segment = SEGMENTS[exchange.upper()]
        self.product = product
        self.state = state
        self.confirm_seconds = confirm_seconds
        state.setdefault("units", 0)

    def position(self) -> int:
        return self.state["units"]

    def _order(self, side: str, quantity: int, price: float) -> tuple[int, float]:
        placed = check(self.dhan.place_order(
            security_id=self.security_id, exchange_segment=self.segment, transaction_type=side,
            quantity=quantity, order_type="MARKET", product_type=self.product, price=0), f"{side} order")
        order_id = placed.get("orderId")
        status, detail = placed.get("orderStatus", ""), {}
        deadline = time.time() + self.confirm_seconds
        while status != "TRADED" and status not in self.FINAL_BAD and time.time() < deadline:
            time.sleep(1)
            try:
                detail = check(self.dhan.get_order_by_id(order_id), "order status")
            except RuntimeError as err:
                log.warning("%s", err)
                continue
            if isinstance(detail, list):
                detail = detail[0] if detail else {}
            status = detail.get("orderStatus", status)
        if status in self.FINAL_BAD:
            reason = detail.get("omsErrorDescription") or detail.get("remarks") or ""
            raise RuntimeError(f"{side} order {order_id} was {status}: {reason}")
        if status != "TRADED":
            log.warning("%s order %s not confirmed yet (status %s); assuming it fills - CHECK YOUR DHAN APP",
                        side, order_id, status or "unknown")
            return quantity, price
        filled = int(detail.get("filledQty") or quantity)
        return filled, float(detail.get("averageTradedPrice") or price)

    def buy(self, quantity: int, price: float) -> tuple[int, float]:
        filled, avg = self._order("BUY", quantity, price)
        self.state["units"] += filled
        return filled, avg

    def sell_all(self, price: float) -> tuple[int, float]:
        filled, avg = self._order("SELL", self.state["units"], price)
        self.state["units"] = 0
        return filled, avg

    def drop_stale(self, price: float) -> None:
        self.state["units"] = 0  # Dhan already squared the intraday position off

    def describe(self, price: float) -> str:
        return f"shares={self.state['units']} (~Rs{self.state['units'] * price:.2f})"
