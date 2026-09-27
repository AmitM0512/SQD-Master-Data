# NSE ETF Strategy — Zerodha Kite Connect

Everything lives in one file: `etf_strategy.py`. It covers the backtest, paper trading, Kite login and the ETF universe.
**It never places real orders.**

## Rules (the backtest and paper trading use the same rules)
| Rule | Default | Config key (`strategy.*`) |
|---|---|---|
| Buy when price is ≥ 1.25% **below** previous close | 1.25 / down | `entry_move_pct`, `entry_direction` (`"up"` to buy gains) |
| 20-day avg volume (prior 20 sessions) must be > 50,000 | 50000 | `min_avg_volume`, `volume_lookback_days` |
| Each buy = 8% of total portfolio value (cash + holdings) | 8.0 | `allocation_pct` |
| Sell each lot at +5.5% from its entry | 5.5 | `target_pct` |
| No re-buy on the next trading day (or the same day); allowed again the day after | 1 | `cooldown_trading_days` |

Every buy is a separate lot with its own target, so adding to an ETF after the cooldown is allowed. You can cap it with `max_lots_per_symbol`.
When cash is short, the biggest movers are bought first. If 8% can't be funded, the signal is skipped.

## Setup
```bash
cd etf_algo
pip install -r requirements.txt
cp config/kite_config.example.json config/kite_config.json
chmod 600 config/kite_config.json      # then fill api_key / api_secret
```
You can also set `KITE_API_KEY`, `KITE_API_SECRET` and `KITE_ACCESS_TOKEN` as environment variables instead.
`config/kite_config.json` and the saved token are git-ignored.

## Run
```bash
python etf_strategy.py login                 # once per day (Kite tokens expire ~6 AM)
python etf_strategy.py universe --save       # see / save all NSE ETFs + tokens
python etf_strategy.py backtest --start 2022-01-01 --capital 1000000
python etf_strategy.py paper                 # runs through today's session, polls every 60s
python etf_strategy.py paper --once          # single evaluation
python etf_strategy.py status                # paper holdings + live P&L
```

## Outputs
- `output/backtest_<timestamp>/` has these files:
  - `summary.json`: CAGR, drawdown, Sharpe, win rate and skipped-signal counts
  - `trades.csv`, `equity_curve.csv`, `open_positions.csv`, `per_symbol.csv`
  - `equity_curve.png`
- `data/paper/` has the paper-trading files:
  - `paper_state.json`: the persistent portfolio
  - `paper_trades.csv`: the buy/sell journal
  - `paper_equity.csv`: equity snapshots
- `logs/etf_strategy.log` is the rotating log.

## Backtest assumptions (daily candles)
- **Entry fill:** entries fill at the trigger price, or at the open if the market gaps through the trigger.
- **Exit fill:** exits fill at the target, or at the open if the market gaps above it.
- **Exit timing:** a lot is not sold on its entry day, because a daily candle can't show whether the high came before or after the dip. Set `allow_same_day_exit: true` to allow it. The backtest then uses the close.
- **Costs:** 0.1% per side is deducted to cover brokerage, STT and exchange charges (`transaction_cost_pct`).
- **Universe:** the universe is today's ETF list, so ETFs that were delisted during the test period are missing. Expect some survivorship bias.
