"""Command line: python -m ema_bot {backtest,run} ..."""
from __future__ import annotations

import argparse
import logging
import os
import sys

from .backtest import run_backtest
from .data import fetch_candles, load_csv, make_exchange


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="ema_bot", description="9/21 EMA crossover trading bot")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp):
        sp.add_argument("--exchange", default="binance", help="ccxt exchange id (default: binance)")
        sp.add_argument("--symbol", default="BTC/USDT")
        sp.add_argument("--timeframe", default="15m")
        sp.add_argument("--fast", type=int, default=9)
        sp.add_argument("--slow", type=int, default=21)
        sp.add_argument("--fee", type=float, default=0.1, help="fee per side in %% (default: 0.1)")
        sp.add_argument("--stop-loss", type=float, default=None, help="stop-loss in %% below entry")

    bt = sub.add_parser("backtest", help="test the strategy on historical candles")
    common(bt)
    bt.add_argument("--csv", help="OHLCV CSV file (otherwise candles are fetched from the exchange)")
    bt.add_argument("--limit", type=int, default=1000, help="candles to fetch when not using --csv")
    bt.add_argument("--cash", type=float, default=10_000)
    bt.add_argument("--short", action="store_true", help="also go short on bearish crosses")
    bt.add_argument("--show-trades", action="store_true")

    run = sub.add_parser("run", help="run the bot (paper trading unless --live)")
    common(run)
    run.add_argument("--order-size", type=float, default=100, help="quote currency per entry (default: 100)")
    run.add_argument("--paper-cash", type=float, default=10_000, help="paper account starting balance")
    run.add_argument("--poll", type=int, default=30, help="seconds between polls")
    run.add_argument("--state", default="bot_state.json")
    run.add_argument("--live", action="store_true",
                     help="place REAL orders (needs EXCHANGE_API_KEY / EXCHANGE_API_SECRET)")

    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.cmd == "backtest":
        if args.csv:
            candles = load_csv(args.csv)
        else:
            candles = fetch_candles(make_exchange(args.exchange), args.symbol, args.timeframe, args.limit)
        result = run_backtest(candles, args.fast, args.slow, args.cash, args.fee, args.short, args.stop_loss)
        print(f"{len(candles)} candles, {candles.index[0]} -> {candles.index[-1]}")
        for key, value in result.stats().items():
            print(f"  {key:22} {value}")
        if args.show_trades:
            for t in result.trades:
                print(f"  {t.side:5} {t.entry_time} @ {t.entry_price:.4f} -> {t.exit_time} @ "
                      f"{t.exit_price:.4f}  {t.return_pct:+.2f}%  pnl {t.pnl:+.2f}  ({t.exit_reason})")
        return 0

    from .bot import Bot, load_state
    from .broker import CcxtBroker, PaperBroker

    state = load_state(args.state)
    mode = "live" if args.live else "paper"
    if state.setdefault("mode", mode) != mode:
        sys.exit(f"{args.state} belongs to a {state['mode']} session; use a different --state file")
    if args.live:
        key, secret = os.environ.get("EXCHANGE_API_KEY"), os.environ.get("EXCHANGE_API_SECRET")
        if not key or not secret:
            sys.exit("--live needs EXCHANGE_API_KEY and EXCHANGE_API_SECRET environment variables")
        exchange = make_exchange(args.exchange, key, secret)
        broker = CcxtBroker(exchange, args.symbol, state)
        logging.warning("LIVE MODE: real orders of ~%s quote on %s", args.order_size, args.exchange)
    else:
        exchange = make_exchange(args.exchange)
        broker = PaperBroker(state, args.paper_cash, args.fee)
    bot = Bot(exchange, broker, state, args.state, args.symbol, args.timeframe,
              args.fast, args.slow, args.order_size, args.stop_loss)
    try:
        bot.run(args.poll)
    except KeyboardInterrupt:
        print("stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
