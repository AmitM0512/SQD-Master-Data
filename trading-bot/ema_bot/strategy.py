"""EMA crossover signal logic. Pure functions, no I/O."""
from __future__ import annotations

import pandas as pd

BUY, SELL, HOLD = 1, -1, 0


def add_signals(df: pd.DataFrame, fast: int = 9, slow: int = 21) -> pd.DataFrame:
    """Return a copy of ``df`` with ``ema_fast``, ``ema_slow`` and ``signal`` columns.

    signal is BUY on the bar where the fast EMA closes above the slow EMA
    (having been at or below it on the previous bar), SELL on the opposite
    cross, HOLD otherwise. The first ``slow`` bars are warm-up and never signal.

    A signal is only known once its bar has closed, so callers must act on the
    *next* bar (the backtester fills at the next open; the live bot only looks
    at completed candles).
    """
    if fast >= slow:
        raise ValueError(f"fast period ({fast}) must be smaller than slow period ({slow})")
    out = df.copy()
    out["ema_fast"] = out["close"].ewm(span=fast, adjust=False).mean()
    out["ema_slow"] = out["close"].ewm(span=slow, adjust=False).mean()

    above = out["ema_fast"] > out["ema_slow"]
    prev_above = above.shift(1, fill_value=False)
    signal = pd.Series(HOLD, index=out.index, dtype="int64")
    signal[above & ~prev_above] = BUY
    signal[~above & prev_above] = SELL
    signal.iloc[:slow] = HOLD
    out["signal"] = signal
    return out
