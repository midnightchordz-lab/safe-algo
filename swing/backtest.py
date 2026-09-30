"""Backtest the swing bot on daily candles.

  python3 -m swing.backtest --upstox            # real Nifty 50 history (needs today's token)
  python3 -m swing.backtest --upstox --refresh  # re-download instead of using the cache
  python3 -m swing.backtest                     # synthetic data: plumbing check only, no edge

Runs every entry rule x stop width, so we pick the setup from evidence, not hope.
Fills: entry at the signal day's close; stop/target intraday with gap handling (see engine).
"""
import argparse
import json
import math
import os
import random
import time
from dataclasses import replace
from datetime import date, timedelta

from swing.config import HERE, SwingConfig
from swing.engine import (check_exit, close_position, end_of_day, equity, floor_action,
                          new_state, open_position, pick_entries, stats)
from swing.indicators import compute
from upstox_api import Candle

CACHE = os.path.join(HERE, "swing_data.json")


def run(data, cfg):
    inds = {s: compute(c) for s, c in data.items()}
    where = {s: {c.date: i for i, c in enumerate(cands)} for s, cands in data.items()}
    dates = sorted({c.date for cands in data.values() for c in cands})
    state, prices, curve = new_state(cfg), {}, []
    for d in dates[210:]:
        today = {s: w[d] for s, w in where.items() if d in w}
        for s, i in today.items():
            prices[s] = data[s][i].close
        for s in list(state["positions"]):
            pos = state["positions"][s]
            if s in today and pos["entry_date"] != d:
                ex = check_exit(pos, data[s][today[s]], cfg)
                if ex:
                    close_position(state, s, ex[0], ex[1], d, cfg)
        eq = equity(state, prices)
        fa = floor_action(state, eq, cfg)
        if fa == "hard" and state["positions"]:
            for s in list(state["positions"]):
                close_position(state, s, prices[s], "hard_floor", d, cfg)
        if fa and not state["halted"]:
            state["halted"], state["halt_reason"] = True, f"{fa} floor: equity {eq:.0f}"
        for e in pick_entries(state, inds, today, prices, eq, cfg):
            open_position(state, e["symbol"], e["qty"], e["price"], e["stop"], e["target"], d, cfg)
        end_of_day(state)
        curve.append((d, equity(state, prices)))
    return state, curve


def summarize(name, state, curve, cfg):
    st = stats(state["trades"])
    final = curve[-1][1]
    years = max((date.fromisoformat(curve[-1][0]) - date.fromisoformat(curve[0][0])).days / 365.25, 0.1)
    cagr = (final / cfg.budget) ** (1 / years) - 1
    peak, mdd = -math.inf, 0.0
    for _, v in curve:
        peak = max(peak, v)
        mdd = max(mdd, (peak - v) / peak)
    half = st["trades"] // 2
    r1 = sum(t["r"] for t in state["trades"][:half]) / half if half else 0
    r2 = sum(t["r"] for t in state["trades"][half:]) / (st["trades"] - half) if st["trades"] - half else 0
    line = f"{name:22s} ₹{final:9.0f}  {cagr * 100:5.1f}%/yr  maxDD {mdd * 100:4.1f}%  "
    if st["trades"]:
        line += (f"trades {st['trades']:3d}  win {st['win_rate'] * 100:4.1f}%  avgR {st['avg_r']:+.2f} "
                 f"(1st half {r1:+.2f} / 2nd {r2:+.2f})  PF {st['profit_factor']:.2f}  "
                 f"T/S/time {st['targets']}/{st['stops']}/{st['timeouts']}")
    else:
        line += "no trades"
    if state["halted"]:
        line += f"  HALTED ({state['halt_reason']})"
    print(line)


def benchmark(candles, cfg, start_date):
    c = [x for x in candles if x.date >= start_date]
    qty = int(cfg.budget / c[0].close)
    final = cfg.budget - qty * c[0].close + qty * c[-1].close
    years = (date.fromisoformat(c[-1].date) - date.fromisoformat(c[0].date)).days / 365.25
    print(f"{'NIFTYBEES buy & hold':22s} ₹{final:9.0f}  {((final / cfg.budget) ** (1 / years) - 1) * 100:5.1f}%/yr")


def load_upstox(cfg, days, refresh):
    if os.path.exists(CACHE) and not refresh:
        with open(CACHE) as f:
            raw = json.load(f)
        if raw.get("fetched") == date.today().isoformat() or not refresh:
            print(f"Using cached data from {raw['fetched']} (--refresh to re-download)")
            return {s: [Candle(*c) for c in v] for s, v in raw["data"].items()}
    from upstox_api import Upstox, resolve_instrument_keys
    api = Upstox(cfg.access_token)
    keys = resolve_instrument_keys(list(cfg.symbols) + ["NIFTYBEES"], strict=False)
    missing = [s for s in cfg.symbols if s not in keys]
    if missing:
        print(f"Skipping symbols Upstox doesn't know: {missing}")
    data = {}
    for n, (s, k) in enumerate(keys.items(), 1):
        print(f"\rDownloading {n}/{len(keys)} {s:12s}", end="", flush=True)
        data[s] = api.daily_candles(k, days, include_today=True)
        time.sleep(0.2)
    print()
    with open(CACHE, "w") as f:
        json.dump({"fetched": date.today().isoformat(), "data": data}, f)
    return data


def synthetic(cfg, days=1800):
    rnd, data = random.Random(1), {}
    start = date(2019, 1, 1)
    for s in list(cfg.symbols)[:30] + ["NIFTYBEES"]:
        px, out = 100.0, []
        for d in range(days):
            o = px * math.exp(rnd.gauss(0, 0.005))
            c = o * math.exp(rnd.gauss(0.0004, 0.015))
            h, l = max(o, c) * (1 + abs(rnd.gauss(0, 0.006))), min(o, c) * (1 - abs(rnd.gauss(0, 0.006)))
            out.append(Candle((start + timedelta(days=d)).isoformat(), o, h, l, c, 0))
            px = c
        data[s] = out
    return data


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--upstox", action="store_true")
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--days", type=int, default=2500)
    ap.add_argument("--trades", action="store_true", help="list every trade of the default setup")
    args = ap.parse_args()
    cfg = SwingConfig()
    data = load_upstox(cfg, args.days, args.refresh) if args.upstox else synthetic(cfg)
    bench = data.pop("NIFTYBEES", None)
    print(f"Budget ₹{cfg.budget:.0f}, {len(data)} stocks, risk {cfg.risk_per_trade_pct * 100:.0f}%/trade, "
          f"reward:risk {cfg.reward_risk:.0f}:1\n")
    start = None
    for entry in ("breakout", "pullback"):
        for mult in (1.5, 2.0):
            c = replace(cfg, entry=entry, atr_stop_mult=mult)
            state, curve = run(data, c)
            start = curve[0][0]
            summarize(f"{entry} {mult}xATR", state, curve, c)
            if args.trades and entry == cfg.entry and mult == cfg.atr_stop_mult:
                for t in state["trades"]:
                    print("   ", t)
    if bench:
        benchmark(bench, cfg, start)


if __name__ == "__main__":
    main()
