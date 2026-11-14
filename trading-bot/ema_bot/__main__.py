"""Command line: python -m ema_bot {backtest,run} ..."""
from __future__ import annotations

import argparse
import datetime as dt
import logging
import os
import sys

from .backtest import run_backtest
from .data import TIMEFRAMES, fetch_candles, load_csv, make_dhan, resolve_security_id, timeframe_delta


def clock(text: str) -> dt.time:
    return dt.datetime.strptime(text, "%H:%M").time()


def connect(args):
    """Return (dhan client, security id) using credentials from the environment."""
    client_id, token = os.environ.get("DHAN_CLIENT_ID"), os.environ.get("DHAN_ACCESS_TOKEN")
    if not client_id or not token:
        sys.exit("set the DHAN_CLIENT_ID and DHAN_ACCESS_TOKEN environment variables first")
    security_id = args.security_id or resolve_security_id(args.symbol, args.exchange)
    return make_dhan(client_id, token), security_id


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="ema_bot", description="9/21 EMA crossover bot for NSE/BSE stocks via Dhan")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp):
        sp.add_argument("--symbol", default="RELIANCE", help="trading symbol, e.g. RELIANCE, TCS, SBIN")
        sp.add_argument("--exchange", default="NSE", choices=["NSE", "BSE"])
        sp.add_argument("--security-id", help="Dhan security id (skips the symbol lookup)")
        sp.add_argument("--timeframe", default="15m", choices=list(TIMEFRAMES))
        sp.add_argument("--fast", type=int, default=9)
        sp.add_argument("--slow", type=int, default=21)
        sp.add_argument("--product", default="intraday", choices=["intraday", "delivery"],
                        help="intraday (MIS, squared off daily) or delivery (CNC, can hold overnight)")
        sp.add_argument("--exit-time", type=clock, default=dt.time(15, 10),
                        help="intraday only: square off and stop entering at this IST time (default 15:10)")
        sp.add_argument("--fee", type=float, default=0.05,
                        help="estimated cost per side in %% of trade value (default 0.05)")
        sp.add_argument("--stop-loss", type=float, default=None, help="stop-loss in %% below entry")

    bt = sub.add_parser("backtest", help="test the strategy on historical candles")
    common(bt)
    bt.add_argument("--csv", help="OHLCV CSV file (otherwise candles are fetched from Dhan)")
    bt.add_argument("--days", type=int, default=60, help="calendar days of history to fetch from Dhan")
    bt.add_argument("--cash", type=float, default=100_000, help="starting capital in rupees")
    bt.add_argument("--short", action="store_true", help="also go short on bearish crosses")
    bt.add_argument("--show-trades", action="store_true")

    run = sub.add_parser("run", help="run the bot (paper trading unless --live)")
    common(run)
    run.add_argument("--capital", type=float, default=10_000, help="rupees to deploy per trade (default 10000)")
    run.add_argument("--quantity", type=int, help="fixed number of shares per trade (overrides --capital)")
    run.add_argument("--paper-cash", type=float, default=100_000, help="paper account starting balance")
    run.add_argument("--poll", type=int, default=20, help="seconds between polls")
    run.add_argument("--state", help="state file (default: <SYMBOL>_<paper|live>_state.json)")
    run.add_argument("--live", action="store_true", help="place REAL orders in your Dhan account")

    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    intraday = args.product == "intraday"
    if intraday and args.timeframe == "1d":
        sys.exit("daily candles need --product delivery")

    if args.cmd == "backtest":
        if args.csv:
            candles = load_csv(args.csv)
        else:
            dhan, security_id = connect(args)
            candles = fetch_candles(dhan, security_id, args.exchange, args.timeframe, args.days)
        if candles.empty:
            sys.exit("no candles to backtest")
        result = run_backtest(candles, args.fast, args.slow, args.cash, args.fee, args.short, args.stop_loss,
                              exit_time=args.exit_time if intraday else None)
        print(f"{len(candles)} candles, {candles.index[0]} -> {candles.index[-1]}  ({args.product})")
        for key, value in result.stats().items():
            print(f"  {key:22} {value}")
        if args.show_trades:
            for t in result.trades:
                print(f"  {t.side:5} {t.entry_time} @ {t.entry_price:.2f} -> {t.exit_time} @ "
                      f"{t.exit_price:.2f}  {t.return_pct:+.2f}%  pnl {t.pnl:+.2f}  ({t.exit_reason})")
        return 0

    from .bot import Bot, load_state
    from .broker import DhanBroker, PaperBroker

    dhan, security_id = connect(args)
    mode = "live" if args.live else "paper"
    state_path = args.state or f"{args.symbol.upper()}_{mode}_state.json"
    state = load_state(state_path)
    if state.setdefault("mode", mode) != mode:
        sys.exit(f"{state_path} belongs to a {state['mode']} session; use a different --state file")
    if args.live:
        broker = DhanBroker(dhan, security_id, args.exchange, "INTRADAY" if intraday else "CNC", state)
        logging.warning("LIVE MODE: real %s orders for %s (security id %s) in your Dhan account",
                        args.product, args.symbol, security_id)
    else:
        broker = PaperBroker(state, args.paper_cash, args.fee)
        logging.info("paper mode: %s (security id %s), no real orders", args.symbol, security_id)

    history_days = 400 if args.timeframe == "1d" else 10
    bot = Bot(lambda: fetch_candles(dhan, security_id, args.exchange, args.timeframe, history_days),
              broker, state, state_path, timeframe_delta(args.timeframe), args.fast, args.slow,
              args.capital, args.quantity, args.stop_loss, intraday, args.exit_time)
    try:
        bot.run(args.poll)
    except KeyboardInterrupt:
        print("stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
