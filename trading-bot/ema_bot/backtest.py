"""Bar-by-bar backtester for the EMA crossover strategy.

Signals are computed on a bar's close and filled at the NEXT bar's open, so
there is no look-ahead. Each entry uses the full account equity, rounded down
to whole shares. With ``exit_time`` set (intraday/MIS style) no trade is opened
at or after that time of day and any open position is squared off.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

import pandas as pd

from .strategy import BUY, SELL, add_signals


@dataclass
class Trade:
    side: str
    entry_time: pd.Timestamp
    entry_price: float
    exit_time: pd.Timestamp
    exit_price: float
    pnl: float  # net of fees, in account currency
    exit_reason: str

    @property
    def return_pct(self) -> float:
        direction = 1 if self.side == "long" else -1
        return direction * (self.exit_price / self.entry_price - 1) * 100


@dataclass
class Result:
    equity: pd.Series
    trades: list[Trade] = field(default_factory=list)
    buy_hold_return_pct: float = 0.0

    def stats(self) -> dict:
        eq = self.equity
        wins = [t.pnl for t in self.trades if t.pnl > 0]
        losses = [t.pnl for t in self.trades if t.pnl <= 0]
        drawdown = (eq / eq.cummax() - 1).min() * 100
        return {
            "start_equity": round(eq.iloc[0], 2),
            "end_equity": round(eq.iloc[-1], 2),
            "total_return_pct": round((eq.iloc[-1] / eq.iloc[0] - 1) * 100, 2),
            "buy_hold_return_pct": round(self.buy_hold_return_pct, 2),
            "max_drawdown_pct": round(drawdown, 2),
            "trades": len(self.trades),
            "win_rate_pct": round(100 * len(wins) / len(self.trades), 1) if self.trades else 0.0,
            "profit_factor": round(sum(wins) / -sum(losses), 2) if losses and sum(losses) < 0 else None,
        }


def run_backtest(
    df: pd.DataFrame,
    fast: int = 9,
    slow: int = 21,
    initial_cash: float = 10_000.0,
    fee_pct: float = 0.1,
    allow_short: bool = False,
    stop_loss_pct: float | None = None,
    whole_shares: bool = True,
    exit_time: dt.time | None = None,
) -> Result:
    """Backtest on OHLCV candles. ``fee_pct`` is charged per side on notional."""
    data = add_signals(df, fast, slow)
    fee = fee_pct / 100
    cash = initial_cash
    units = 0.0  # signed: >0 long, <0 short
    entry_price = entry_equity = 0.0
    entry_time = None
    trades: list[Trade] = []
    equity = [initial_cash]

    def open_position(direction: int, price: float, time) -> None:
        nonlocal cash, units, entry_price, entry_time, entry_equity
        size = cash / (price * (1 + fee))
        if whole_shares:
            size = float(int(size))
        if size <= 0:
            return  # cannot afford a single share
        entry_equity = cash
        cash -= direction * size * price + size * price * fee
        units = direction * size
        entry_price, entry_time = price, time

    def close_position(price: float, time, reason: str) -> None:
        nonlocal cash, units
        cash += units * price - abs(units) * price * fee
        trades.append(Trade("long" if units > 0 else "short", entry_time, entry_price,
                            time, price, cash - entry_equity, reason))
        units = 0.0

    signals = data["signal"].to_numpy()
    for i in range(1, len(data)):
        bar = data.iloc[i]
        time = data.index[i]
        sig = signals[i - 1]  # decided on the previous close, filled at this open

        if exit_time is not None and time.time() >= exit_time:
            if units != 0:
                close_position(bar.open, time, "square-off")
        elif sig == BUY:
            if units < 0:
                close_position(bar.open, time, "signal")
            if units == 0:
                open_position(1, bar.open, time)
        elif sig == SELL:
            if units > 0:
                close_position(bar.open, time, "signal")
            if units == 0 and allow_short:
                open_position(-1, bar.open, time)

        if stop_loss_pct and units != 0:
            if units > 0:
                stop = entry_price * (1 - stop_loss_pct / 100)
                if bar.low <= stop:
                    close_position(min(bar.open, stop), time, "stop")  # gap-down fills at open
            else:
                stop = entry_price * (1 + stop_loss_pct / 100)
                if bar.high >= stop:
                    close_position(max(bar.open, stop), time, "stop")

        equity.append(cash + units * bar.close)

    closes = data["close"]
    return Result(
        equity=pd.Series(equity, index=data.index, name="equity"),
        trades=trades,
        buy_hold_return_pct=(closes.iloc[-1] / closes.iloc[0] - 1) * 100,
    )
