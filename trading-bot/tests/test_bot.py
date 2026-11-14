import numpy as np
import pandas as pd
import pytest

from ema_bot.backtest import run_backtest
from ema_bot.bot import Bot
from ema_bot.broker import PaperBroker
from ema_bot.strategy import BUY, SELL, add_signals


def candles(closes, freq="15min"):
    closes = np.asarray(closes, dtype=float)
    opens = np.concatenate([[closes[0]], closes[:-1]])
    idx = pd.date_range("2025-01-01", periods=len(closes), freq=freq, tz="UTC")
    return pd.DataFrame({"open": opens, "high": np.maximum(opens, closes), "low": np.minimum(opens, closes),
                         "close": closes, "volume": 1.0}, index=idx)


# flat, down, strong up, strong down: one bullish cross then one bearish cross
V_SHAPE = np.concatenate([np.full(25, 100.0), np.linspace(100, 80, 30),
                          np.linspace(80, 130, 50), np.linspace(130, 70, 50)])


def test_ema_matches_manual_recursion():
    df = add_signals(candles(V_SHAPE))
    alpha = 2 / (9 + 1)
    ema = V_SHAPE[0]
    for price in V_SHAPE[1:]:
        ema = alpha * price + (1 - alpha) * ema
    assert df["ema_fast"].iloc[-1] == pytest.approx(ema)


def test_crossovers_detected_once_each():
    df = add_signals(candles(V_SHAPE))
    signals = df["signal"][df["signal"] != 0]
    assert list(signals) == [BUY, SELL]
    buy_at = signals.index[0]
    assert df.loc[buy_at, "ema_fast"] > df.loc[buy_at, "ema_slow"]
    prev = df.index[df.index.get_loc(buy_at) - 1]
    assert df.loc[prev, "ema_fast"] <= df.loc[prev, "ema_slow"]


def test_no_signal_during_warmup_and_bad_periods_rejected():
    df = add_signals(candles(np.linspace(100, 200, 60)))
    assert (df["signal"].iloc[:21] == 0).all()
    with pytest.raises(ValueError):
        add_signals(df, fast=21, slow=9)


def test_backtest_fills_at_next_open_and_accounts_for_fees():
    data = candles(V_SHAPE)
    sig = add_signals(data)
    buy_i = list(sig["signal"]).index(BUY)
    sell_i = list(sig["signal"]).index(SELL)
    result = run_backtest(data, initial_cash=1000, fee_pct=0.1)
    (trade,) = result.trades
    assert trade.entry_time == data.index[buy_i + 1]
    assert trade.entry_price == data["open"].iloc[buy_i + 1]
    assert trade.exit_price == data["open"].iloc[sell_i + 1]
    expected = 1000 / (trade.entry_price * 1.001) * trade.exit_price * 0.999
    assert result.equity.iloc[-1] == pytest.approx(expected)
    assert trade.pnl == pytest.approx(expected - 1000)


def test_short_side_profits_from_fall():
    result = run_backtest(candles(V_SHAPE), fee_pct=0, allow_short=True)
    assert [t.side for t in result.trades] == ["long"]  # the short is still open at the end
    long_only = run_backtest(candles(V_SHAPE), fee_pct=0)
    assert result.equity.iloc[-1] > long_only.equity.iloc[-1]


def test_stop_loss_exits_early():
    crash = np.concatenate([V_SHAPE[:95], [60.0] * 30])  # sudden collapse mid-uptrend
    result = run_backtest(candles(crash), fee_pct=0, stop_loss_pct=5)
    (trade,) = result.trades
    assert trade.exit_reason == "stop"
    assert trade.exit_price == pytest.approx(trade.entry_price * 0.95)


class FakeExchange:
    def __init__(self, closes):
        self.rows = [[int(t.timestamp() * 1000), r.open, r.high, r.low, r.close, r.volume]
                     for t, r in candles(closes).iterrows()]
        self.n = 0

    def fetch_ohlcv(self, symbol, timeframe, limit):
        return self.rows[: self.n][-limit:]


def test_live_bot_trades_once_per_cross(tmp_path):
    exchange = FakeExchange(V_SHAPE)
    state = {}
    broker = PaperBroker(state, starting_cash=1000, fee_pct=0)
    bot = Bot(exchange, broker, state, str(tmp_path / "state.json"), "X/Y", "15m", order_size=500)
    held = []
    for n in range(30, len(V_SHAPE) + 1):
        exchange.n = n
        bot.step()
        bot.step()  # polling twice within one candle must not double-trade
        held.append(broker.position() > 0)
    assert held[0] is False and any(held) and held[-1] is False
    assert sum(1 for a, b in zip(held, held[1:]) if a != b) == 2  # one buy, one sell
    assert state["paper_cash"] > 1000  # bought low, sold higher
