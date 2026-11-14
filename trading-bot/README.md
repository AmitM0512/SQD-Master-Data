# 9/21 EMA Crossover Trading Bot — NSE/BSE via Dhan

A small Python trading bot for Indian stocks, built around one rule:

- **Buy** when the 9-period EMA crosses **above** the 21-period EMA.
- **Sell** when the 9-period EMA crosses back **below** it.

It includes a backtester, a paper-trading mode (simulated money, the default),
and an opt-in live mode that places real orders in your [Dhan](https://dhan.co) account
through the official `dhanhq` API.

## Setup

```bash
cd trading-bot
pip install -r requirements.txt
```

Get your **Client ID** and an **Access Token** from Dhan (web.dhan.co → My Profile → Access DhanHQ APIs), then:

```bash
export DHAN_CLIENT_ID=your_client_id
export DHAN_ACCESS_TOKEN=your_access_token
```

On Windows use `set` instead of `export`. Things to know about Dhan's API:

- Access tokens are short-lived, so you will need to generate a fresh one regularly.
- Candle data comes from Dhan's **Data API**, which may need a separate subscription on your account.
- Dhan requires a **static IP** to be registered before the order APIs will accept orders (needed for `--live` only).

## 1. Backtest

On the bundled CSV (no Dhan account needed):

```bash
python -m ema_bot backtest --csv sample_data.csv --show-trades
```

`sample_data.csv` is randomly generated demo data, only there so the command works out of the box.
Any CSV with `open, high, low, close` and a `date`/`timestamp` column works.

On real history from Dhan:

```bash
python -m ema_bot backtest --symbol RELIANCE --timeframe 15m --days 60
```

Options: `--exchange NSE|BSE`, `--timeframe 1m|5m|15m|25m|1h|1d`, `--product intraday|delivery`,
`--stop-loss 1` (percent), `--fee 0.05` (estimated cost per side, percent), `--short`, `--cash 100000`.

Signals are taken from a candle's close and filled at the next candle's open, so the backtest
does not peek into the future. Quantities are whole shares.

## 2. Paper trade

```bash
python -m ema_bot run --symbol RELIANCE --timeframe 15m --capital 10000
```

Uses live Dhan candles with a simulated balance; no orders are sent. Leave it running during market hours.

## 3. Live trade (real money)

```bash
python -m ema_bot run --live --symbol RELIANCE --timeframe 15m --capital 10000 --stop-loss 1
```

Live mode is long-only and sends MARKET orders. Use `--quantity 5` for a fixed share count instead of `--capital`.

## How the bot behaves

- Works only during the regular session, **09:15–15:30 IST, Monday–Friday**; it sleeps otherwise.
  It does not know exchange holidays — on a holiday no candles arrive, so it does nothing.
- Acts only on **completed** candles, once per candle, and waits for a fresh crossover after starting.
- `--product intraday` (default, Dhan INTRADAY/MIS): no new buys from `--exit-time` (default **15:10**)
  and any open position is sold at that time, before the broker's own auto square-off.
- `--product delivery` (Dhan CNC): positions can be held overnight. Selling shares bought on an
  earlier day needs DDPI/eDIS authorisation to be active on your Dhan account.
- The stop-loss is checked by the bot on each poll (every 20 s); it is not an order resting at the
  exchange, so it only works while the bot is running.
- The position is saved in `<SYMBOL>_<paper|live>_state.json`, so the bot can be restarted, and it
  only sells shares it bought itself. Run one bot per symbol.
- If the symbol lookup fails, pass Dhan's numeric id directly with `--security-id`.

## Tests

```bash
python -m pytest
```

## Layout

| File | Purpose |
| --- | --- |
| `ema_bot/strategy.py` | EMA calculation and crossover signals |
| `ema_bot/backtest.py` | Backtester and performance stats |
| `ema_bot/bot.py` | Live polling loop, market hours, square-off |
| `ema_bot/broker.py` | Paper broker and Dhan (real order) broker |
| `ema_bot/data.py` | CSV loading, Dhan candles, symbol → security id lookup |

## Risk warning

EMA crossovers follow trends; in sideways markets they produce repeated small losses, and brokerage,
STT and other charges add up. `--fee` is a rough estimate, not Dhan's exact charges. A good backtest
does not guarantee future results. Paper trade first, start small, and never risk money you cannot
afford to lose. This is not financial advice.
