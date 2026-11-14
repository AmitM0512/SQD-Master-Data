# 9/21 EMA Crossover Trading Bot

A small Python trading bot built around one rule:

- **Buy** when the 9-period EMA crosses **above** the 21-period EMA.
- **Sell** when the 9-period EMA crosses back **below** it.

It includes a backtester, a paper-trading mode (simulated money, the default),
and an opt-in live mode that places real spot market orders on any exchange
supported by [ccxt](https://github.com/ccxt/ccxt).

## Setup

```bash
cd trading-bot
pip install -r requirements.txt
```

## 1. Backtest

On a CSV of candles (needs `open, high, low, close` and a `date`/`timestamp` column — works for stocks, forex, crypto):

```bash
python -m ema_bot backtest --csv sample_data.csv --show-trades
```

`sample_data.csv` is randomly generated demo data, only there so the command works out of the box.

Or on recent candles pulled straight from an exchange (no API key needed):

```bash
python -m ema_bot backtest --exchange binance --symbol BTC/USDT --timeframe 1h --limit 1000
```

Options: `--fast 9 --slow 21`, `--fee 0.1` (percent per side), `--stop-loss 2` (percent), `--short` (also short bearish crosses), `--cash 10000`.

Signals are taken from a candle's close and filled at the next candle's open, so the backtest does not peek into the future.

## 2. Paper trade

```bash
python -m ema_bot run --exchange binance --symbol BTC/USDT --timeframe 15m --order-size 100
```

Uses real market data with a simulated balance. Progress is saved in `bot_state.json`, so the bot can be stopped and restarted. It only acts on completed candles, once per candle, and waits for a fresh crossover after starting.

## 3. Live trade (real money)

```bash
export EXCHANGE_API_KEY=...
export EXCHANGE_API_SECRET=...
python -m ema_bot run --live --state live_state.json --exchange binance --symbol BTC/USDT --timeframe 15m --order-size 25 --stop-loss 2
```

Live mode is long-only spot: it buys `--order-size` of quote currency on a bullish cross and sells what it bought on a bearish cross or stop-loss. Use an API key with trading enabled and **withdrawals disabled**, and start small.

## Tests

```bash
python -m pytest
```

## Layout

| File | Purpose |
| --- | --- |
| `ema_bot/strategy.py` | EMA calculation and crossover signals |
| `ema_bot/backtest.py` | Backtester and performance stats |
| `ema_bot/bot.py` | Live polling loop |
| `ema_bot/broker.py` | Paper broker and ccxt (real order) broker |
| `ema_bot/data.py` | CSV loading and exchange candle fetching |

## Risk warning

EMA crossovers follow trends; in sideways markets they produce repeated small losses, and fees add up. A good backtest does not guarantee future results. Paper trade first and never risk money you cannot afford to lose. This is not financial advice.
