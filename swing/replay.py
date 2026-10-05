"""Replay the swing screen over the last few trading days (read-only, places no orders).

  UPSTOX_ACCESS_TOKEN=$(cat token.txt) .venv/bin/python -m swing.replay            # last 10 days
  UPSTOX_ACCESS_TOKEN=$(cat token.txt) .venv/bin/python -m swing.replay --days 20

For each day it shows how many Nifty 50 stocks passed each step of the entry rule and which
ones gave a signal, using the same code as the bot. Downloads fresh daily candles.
"""
import argparse
import time

from swing.config import SwingConfig
from swing.indicators import compute
from swing.strategy import screen_summary, signal


def replay(data, cfg, days):
    data = {s: c for s, c in data.items() if s != "NIFTYBEES" and len(c) > 210}
    inds = {s: compute(c) for s, c in data.items()}
    dates = sorted({c.date for cands in data.values() for c in cands[-days:]})[-days:]
    where = {s: {c.date: i for i, c in enumerate(cands)} for s, cands in data.items()}
    out = []
    for d in dates:
        idx = {s: where[s][d] for s in data if d in where[s]}
        line, watch = screen_summary(inds, idx, cfg)
        hits = sorted(s for s, i in idx.items() if signal(inds[s], i, cfg))
        out.append((d, line, hits, watch))
    return out


def download(cfg, days=420):
    """Fresh daily candles for the swing universe (doesn't touch the backtest cache)."""
    from upstox_api import Upstox, resolve_instrument_keys
    api = Upstox(cfg.access_token)
    keys = resolve_instrument_keys(cfg.symbols, cfg.instrument_keys, strict=False)
    data = {}
    for n, (s, k) in enumerate(keys.items(), 1):
        print(f"\rDownloading {n}/{len(keys)} {s:12s}", end="", flush=True)
        data[s] = api.daily_candles(k, days, include_today=True)
        time.sleep(0.1)
    print()
    return data


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=10)
    args = ap.parse_args()
    cfg = SwingConfig()
    data = download(cfg)
    for d, line, hits, watch in replay(data, cfg, args.days):
        print(f"{d}  {line.split(': ', 1)[1]}")
        print(f"            signals: {', '.join(hits) or '-'}   watch: {', '.join(watch[:8]) or '-'}")


if __name__ == "__main__":
    main()
