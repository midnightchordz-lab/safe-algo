"""Backtest the strategy on daily closes.

Usage:
  python backtest.py                      # synthetic stress scenarios (no internet needed)
  python backtest.py --csv NIFTYBEES=nifty.csv GOLDBEES=gold.csv
      CSV needs a 'close' column (and optionally 'date'), oldest row first.
  UPSTOX_ACCESS_TOKEN=... python backtest.py --upstox --days 2500
"""
import argparse
import csv
import math
import random

from config import Config
from engine import apply_fill, decide, equity, halt, new_state, update_peaks


def run(series, cfg, verbose=False):
    """series: {symbol: [closes]} all the same length. Returns a result dict."""
    n = min(len(v) for v in series.values())
    state = new_state(cfg)
    curve, trades = [], 0
    warmup = cfg.trend_sma + 1
    for t in range(warmup, n):
        closes = {s: v[: t + 1] for s, v in series.items()}
        prices = {s: c[-1] for s, c in closes.items()}
        update_peaks(state, prices)
        for o in decide(state, closes, cfg):
            if o["side"] == "HALT":
                halt(state, o["reason"])
                continue
            apply_fill(state, o["symbol"], o["side"], o["qty"], prices[o["symbol"]], cfg)
            trades += 1
            if verbose:
                print(f"day {t:5d} {o['side']:4s} {o['qty']:4d} {o['symbol']:10s} @ {prices[o['symbol']]:9.2f}  ({o['reason']})")
        curve.append(equity(state, prices))
    peak, mdd = -math.inf, 0.0
    for v in curve:
        peak = max(peak, v)
        mdd = max(mdd, (peak - v) / peak)
    return {"final": curve[-1] if curve else cfg.budget, "min": min(curve, default=cfg.budget),
            "max_drawdown": mdd, "trades": trades, "halted": state["halted"],
            "halt_reason": state["halt_reason"]}


def buy_and_hold(series, cfg):
    """Benchmark: split the budget equally on the first tradable day and never sell."""
    start = cfg.trend_sma + 1
    n = min(len(v) for v in series.values())
    per = cfg.budget / len(series)
    qtys = {s: int((per - cfg.brokerage_per_order) / (v[start] * (1 + cfg.other_charges_pct)))
            for s, v in series.items()}
    cash = cfg.budget - sum(q * series[s][start] for s, q in qtys.items())
    curve = [cash + sum(q * series[s][t] for s, q in qtys.items()) for t in range(start, n)]
    peak, mdd = -math.inf, 0.0
    for v in curve:
        peak = max(peak, v)
        mdd = max(mdd, (peak - v) / peak)
    return {"final": curve[-1], "min": min(curve), "max_drawdown": mdd, "trades": len(qtys),
            "halted": False, "halt_reason": ""}


def synthetic(kind, days=1500, start=100.0, seed=0):
    rnd = random.Random(seed)
    drift = {"bull": 0.12, "bear": -0.25, "sideways": 0.0, "crash": 0.08}[kind] / 252
    vol = 0.18 / math.sqrt(252)
    px, out = start, []
    for d in range(days):
        r = rnd.gauss(drift, vol)
        if kind == "crash" and d == days // 2:
            r = -0.35  # overnight 35% gap down: the stop can't save you here, the floor must
        px *= math.exp(r)
        out.append(px)
    return out


def load_csv(path):
    with open(path, newline="") as f:
        return [float(row["close"]) for row in csv.DictReader(f)]


def print_result(name, r, cfg):
    flag = "HALTED" if r["halted"] else "ok"
    print(f"{name:28s} final ₹{r['final']:9.2f}  lowest ₹{r['min']:9.2f}  "
          f"maxDD {r['max_drawdown']*100:5.1f}%  trades {r['trades']:3d}  {flag}")
    if r["halted"]:
        print(f"{'':28s} -> {r['halt_reason']}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", nargs="*", help="SYMBOL=path.csv pairs")
    ap.add_argument("--upstox", action="store_true")
    ap.add_argument("--days", type=int, default=2500)
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    cfg = Config()

    if args.csv:
        series = dict(pair.split("=", 1) for pair in args.csv)
        series = {s: load_csv(p) for s, p in series.items()}
        cfg.symbols = tuple(series)
        print_result("csv", run(series, cfg, args.verbose), cfg)
        print_result("buy & hold (benchmark)", buy_and_hold(series, cfg), cfg)
    elif args.upstox:
        from upstox_api import Upstox, resolve_instrument_keys
        api = Upstox(cfg.access_token)
        keys = resolve_instrument_keys(cfg.symbols, cfg.instrument_keys)
        series = {s: api.daily_closes(k, args.days, include_today=True) for s, k in keys.items()}
        n = min(map(len, series.values()))
        series = {s: v[-n:] for s, v in series.items()}
        print_result(f"upstox {n} days", run(series, cfg, args.verbose), cfg)
        print_result("buy & hold (benchmark)", buy_and_hold(series, cfg), cfg)
    else:
        print(f"Budget ₹{cfg.budget:.0f}, floor ₹{cfg.capital_floor:.0f}. Synthetic stress tests:\n")
        for kind in ("bull", "sideways", "bear", "crash"):
            results = []
            for seed in range(20):
                series = {s: synthetic(kind, seed=seed * 7 + i) for i, s in enumerate(cfg.symbols)}
                results.append(run(series, cfg))
            finals = sorted(r["final"] for r in results)
            worst = min(results, key=lambda r: r["min"])
            print(f"{kind}: median final ₹{finals[len(finals) // 2]:.2f} over 20 runs")
            print_result("  worst run", worst, cfg)


if __name__ == "__main__":
    main()
