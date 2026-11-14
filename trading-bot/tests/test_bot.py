import datetime as dt

import numpy as np
import pandas as pd
import pytest

from ema_bot.backtest import run_backtest
from ema_bot.bot import Bot, market_is_open
from ema_bot.broker import DhanBroker, PaperBroker
from ema_bot.data import candles_from_response, fetch_candles, resolve_security_id
from ema_bot.strategy import BUY, SELL, add_signals

IST = "Asia/Kolkata"


def candles(closes, start="2025-01-06 09:15", freq="15min"):
    closes = np.asarray(closes, dtype=float)
    opens = np.concatenate([[closes[0]], closes[:-1]])
    idx = pd.date_range(start, periods=len(closes), freq=freq, tz=IST)
    return pd.DataFrame({"open": opens, "high": np.maximum(opens, closes), "low": np.minimum(opens, closes),
                         "close": closes, "volume": 1.0}, index=idx)


# flat, down, strong up, strong down: one bullish cross then one bearish cross
V_SHAPE = np.concatenate([np.full(25, 100.0), np.linspace(100, 80, 30),
                          np.linspace(80, 130, 50), np.linspace(130, 70, 50)])


# ---------- strategy ----------

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


# ---------- backtest ----------

def test_backtest_fills_at_next_open_and_accounts_for_fees():
    data = candles(V_SHAPE)
    sig = add_signals(data)
    buy_i = list(sig["signal"]).index(BUY)
    sell_i = list(sig["signal"]).index(SELL)
    result = run_backtest(data, initial_cash=1000, fee_pct=0.1, whole_shares=False)
    (trade,) = result.trades
    assert trade.entry_time == data.index[buy_i + 1]
    assert trade.entry_price == data["open"].iloc[buy_i + 1]
    assert trade.exit_price == data["open"].iloc[sell_i + 1]
    expected = 1000 / (trade.entry_price * 1.001) * trade.exit_price * 0.999
    assert result.equity.iloc[-1] == pytest.approx(expected)
    assert trade.pnl == pytest.approx(expected - 1000)


def test_backtest_buys_whole_shares_only():
    result = run_backtest(candles(V_SHAPE), initial_cash=1000, fee_pct=0)
    (trade,) = result.trades
    shares = int(1000 // trade.entry_price)
    assert trade.pnl == pytest.approx(shares * (trade.exit_price - trade.entry_price))
    assert run_backtest(candles(V_SHAPE), initial_cash=50, fee_pct=0).trades == []  # cannot afford one share


def test_short_side_profits_from_fall():
    result = run_backtest(candles(V_SHAPE), fee_pct=0, allow_short=True)
    long_only = run_backtest(candles(V_SHAPE), fee_pct=0)
    assert result.equity.iloc[-1] > long_only.equity.iloc[-1]


def test_stop_loss_exits_early():
    crash = np.concatenate([V_SHAPE[:95], [60.0] * 30])  # sudden collapse mid-uptrend
    result = run_backtest(candles(crash), fee_pct=0, stop_loss_pct=5)
    (trade,) = result.trades
    assert trade.exit_reason == "stop"
    assert trade.exit_price == pytest.approx(trade.entry_price * 0.95)


def test_intraday_backtest_never_holds_overnight():
    result = run_backtest(candles(V_SHAPE), fee_pct=0, exit_time=dt.time(15, 10))
    assert result.trades
    for t in result.trades:
        assert t.entry_time.date() == t.exit_time.date()
        assert t.entry_time.time() < dt.time(15, 10)


# ---------- Dhan data ----------

class FakeDhan:
    def __init__(self):
        self.orders, self.calls = [], []
        self.status = "TRADED"

    def intraday_minute_data(self, security_id, segment, instrument, from_date, to_date, interval=1):
        self.calls.append((security_id, segment, instrument, from_date, to_date, interval))
        ts = int(pd.Timestamp("2025-01-06 09:15", tz=IST).timestamp())
        return {"status": "success", "remarks": "", "data": {
            "open": [100, 101], "high": [102, 103], "low": [99, 100], "close": [101, 102],
            "volume": [10, 20], "timestamp": [ts, ts + 900]}}

    def place_order(self, **kw):
        self.orders.append(kw)
        return {"status": "success", "remarks": "", "data": {"orderId": "42", "orderStatus": "TRANSIT"}}

    def get_order_by_id(self, order_id):
        return {"status": "success", "remarks": "", "data": [
            {"orderId": order_id, "orderStatus": self.status, "filledQty": 7, "averageTradedPrice": 101.5,
             "omsErrorDescription": "insufficient funds"}]}


def test_dhan_candles_are_parsed_into_ist():
    dhan = FakeDhan()
    df = fetch_candles(dhan, "2885", "NSE", "15m", days=5)
    assert list(df["close"]) == [101.0, 102.0]
    assert str(df.index[0]) == "2025-01-06 09:15:00+05:30"
    assert dhan.calls[0][:3] == ("2885", "NSE_EQ", "EQUITY") and dhan.calls[0][5] == 15
    assert candles_from_response({}).empty
    with pytest.raises(RuntimeError, match="DH-901"):
        failing = FakeDhan()
        failing.intraday_minute_data = lambda *a, **k: {"status": "failure", "remarks": {"error_code": "DH-901"}}
        fetch_candles(failing, "2885", "NSE", "15m", days=5)


def test_security_id_lookup(tmp_path):
    cache = tmp_path / "master.csv"
    cache.write_text(
        "SEM_EXM_EXCH_ID,SEM_SEGMENT,SEM_SMST_SECURITY_ID,SEM_INSTRUMENT_NAME,SEM_TRADING_SYMBOL,SEM_SERIES\n"
        "NSE,D,55555,FUTSTK,RELIANCE,\n"
        "NSE,E,9999,EQUITY,RELIANCE,BE\n"
        "NSE,E,2885,EQUITY,RELIANCE,EQ\n"
        "BSE,E,500325,EQUITY,RELIANCE,A\n")
    assert resolve_security_id("reliance", "NSE", str(cache)) == "2885"
    assert resolve_security_id("RELIANCE", "BSE", str(cache)) == "500325"
    with pytest.raises(ValueError):
        resolve_security_id("NOPE", "NSE", str(cache))


# ---------- Dhan broker ----------

def test_dhan_broker_places_market_orders_and_tracks_fills(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    dhan, state = FakeDhan(), {}
    broker = DhanBroker(dhan, "2885", "NSE", "INTRADAY", state)
    assert broker.buy(10, 100.0) == (7, 101.5)  # uses the exchange-reported fill
    assert broker.position() == 7
    assert dhan.orders[0] == dict(security_id="2885", exchange_segment="NSE_EQ", transaction_type="BUY",
                                  quantity=10, order_type="MARKET", product_type="INTRADAY", price=0)
    broker.sell_all(103.0)
    assert dhan.orders[1]["transaction_type"] == "SELL" and dhan.orders[1]["quantity"] == 7
    assert broker.position() == 0


def test_dhan_broker_rejected_order_leaves_position_unchanged(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    dhan, state = FakeDhan(), {}
    dhan.status = "REJECTED"
    broker = DhanBroker(dhan, "2885", "NSE", "CNC", state)
    with pytest.raises(RuntimeError, match="insufficient funds"):
        broker.buy(10, 100.0)
    assert broker.position() == 0


# ---------- live loop ----------

def make_bot(tmp_path, data, clock, **kw):
    state = {}
    broker = PaperBroker(state, starting_cash=10_000, fee_pct=0)
    feed = {"n": 0}
    bot = Bot(lambda: data.iloc[: feed["n"]], broker, state, str(tmp_path / "state.json"),
              pd.Timedelta(minutes=15), capital=5_000, now=lambda: clock["now"], **kw)
    return bot, broker, state, feed


def test_live_bot_trades_once_per_cross(tmp_path):
    data, clock = candles(V_SHAPE), {}
    bot, broker, state, feed = make_bot(tmp_path, data, clock, intraday=False)
    held = []
    for n in range(30, len(data) + 1):
        feed["n"] = n
        clock["now"] = data.index[n - 1] + pd.Timedelta(minutes=1)  # newest candle is still forming
        bot.step()
        bot.step()  # polling twice within one candle must not double-trade
        held.append(broker.position())
    assert held[0] == 0 and held[-1] == 0
    assert sum(1 for a, b in zip(held, held[1:]) if a != b) == 2  # one buy, one sell
    assert max(held) == int(max(held)) and 0 < max(held) * 80 <= 5_000  # whole shares within capital
    assert state["paper_cash"] > 10_000  # bought low, sold higher


def test_live_bot_ignores_candle_still_forming(tmp_path):
    data, clock = candles(V_SHAPE), {}
    bot, broker, state, feed = make_bot(tmp_path, data, clock, intraday=False)
    feed["n"] = 40
    clock["now"] = data.index[39] + pd.Timedelta(minutes=5)
    bot.step()
    assert state["last_candle"] == data.index[38].isoformat()
    clock["now"] = data.index[39] + pd.Timedelta(minutes=15)  # market shut: last candle is complete
    bot.step()
    assert state["last_candle"] == data.index[39].isoformat()


def test_intraday_bot_squares_off_and_stops_entering(tmp_path):
    # 25 flat candles then a rally: the bullish cross lands mid-session on day one.
    closes = np.concatenate([np.full(25, 100.0), np.linspace(100, 80, 30), np.linspace(80, 130, 50)])
    idx = pd.DatetimeIndex([])
    day = pd.Timestamp("2025-01-06 09:15", tz=IST)
    while len(idx) < len(closes):  # real NSE session shape: 25 candles a day
        idx = idx.append(pd.date_range(day, periods=25, freq="15min"))
        day += pd.Timedelta(days=1)
    data = candles(closes)
    data.index = idx[: len(closes)]
    clock = {}
    bot, broker, state, feed = make_bot(tmp_path, data, clock, intraday=True, exit_time=dt.time(15, 10))
    overnight = []
    for n in range(30, len(data) + 1):
        feed["n"] = n
        clock["now"] = data.index[n - 1] + pd.Timedelta(minutes=1)
        bot.step()
        if clock["now"].time() >= dt.time(15, 10):
            overnight.append(broker.position())
    assert overnight and all(p == 0 for p in overnight)


def test_market_hours():
    assert market_is_open(pd.Timestamp("2025-01-06 09:15", tz=IST))       # Monday open
    assert not market_is_open(pd.Timestamp("2025-01-06 09:14", tz=IST))
    assert not market_is_open(pd.Timestamp("2025-01-06 15:30", tz=IST))
    assert not market_is_open(pd.Timestamp("2025-01-04 11:00", tz=IST))   # Saturday
