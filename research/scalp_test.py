"""Curiosity test: does 1-minute scalping on Nifty survive costs? Research only, never trades.

  UPSTOX_ACCESS_TOKEN=$(cat token.txt) python3 -m research.scalp_test --upstox --months 6
  python3 -m research.scalp_test            # synthetic data (plumbing check only)

Model: trade one lot of an at-the-money Nifty option. Its premium moves ~delta (0.5) x the
index move, and minutes-long holds make time decay negligible. Every result is shown three
ways: before costs, after fees, and after fees + slippage (paying the bid/ask spread and
moving price against you on each fill).
"""
import argparse
import math
import random
import time
from collections import defaultdict
from datetime import date, datetime, timedelta

LOT = 75                 # Nifty lot size (units); change with --lot if NSE revises it
DELTA = 0.5              # ATM option moves ~half as much as the index
FEES_PER_TRIP = 80.0     # 2 orders x ₹20 brokerage + STT, exchange, GST, stamp (approx.)
SLIP_PTS_PER_SIDE = 0.5  # premium points lost per fill to spread/slippage (often 0.5-1.5)
START, LAST_ENTRY, FLAT = "09:20", "15:00", "15:10"


def ema(prev, value, n):
    return value if prev is None else prev + (value - prev) * 2 / (n + 1)


def momentum(day, target=6, stop=6):
    """Enter on a 9/21 EMA cross, exit at +/- target/stop index points or end of day."""
    trades, fast, slow, pos = [], None, None, None
    for t, close in day:
        pf, ps = fast, slow
        fast, slow = ema(fast, close, 9), ema(slow, close, 21)
        if pos:
            move = (close - pos[1]) * pos[0]
            if move >= target or move <= -stop or t >= FLAT:
                trades.append(move)
                pos = None
            continue
        if pf is None or not (START <= t <= LAST_ENTRY):
            continue
        if pf <= ps and fast > slow:
            pos = (1, close)
        elif pf >= ps and fast < slow:
            pos = (-1, close)
    return trades


def mean_reversion(day, n=20, k=2.0, stop=8):
    """Fade a close more than k standard deviations from its 20-minute mean; exit at the mean."""
    trades, window, pos = [], [], None
    for t, close in day:
        window = (window + [close])[-n:]
        if pos:
            move = (close - pos[1]) * pos[0]
            mean = sum(window) / len(window)
            back = (pos[0] == 1 and close >= mean) or (pos[0] == -1 and close <= mean)
            if back or move <= -stop or t >= FLAT:
                trades.append(move)
                pos = None
            continue
        if len(window) < n or not (START <= t <= LAST_ENTRY):
            continue
        mean = sum(window) / n
        sd = math.sqrt(sum((x - mean) ** 2 for x in window) / n)
        if sd and close > mean + k * sd:
            pos = (-1, close)
        elif sd and close < mean - k * sd:
            pos = (1, close)
    return trades


def random_entries(day, target=6, stop=6, seed=0):
    """Baseline: coin-flip direction at random times, same exits as momentum."""
    rnd = random.Random(seed + len(day))
    trades, pos = [], None
    for t, close in day:
        if pos:
            move = (close - pos[1]) * pos[0]
            if move >= target or move <= -stop or t >= FLAT:
                trades.append(move)
                pos = None
        elif START <= t <= LAST_ENTRY and rnd.random() < 0.02:
            pos = (rnd.choice((1, -1)), close)
    return trades


def report(name, trades_by_day, lot):
    moves = [m for day in trades_by_day.values() for m in day]
    if not moves:
        print(f"{name:16s} no trades")
        return
    n = len(moves)
    gross = sum(moves) * DELTA * lot
    fees = n * FEES_PER_TRIP
    slip = n * 2 * SLIP_PTS_PER_SIDE * lot
    wins = sum(m > 0 for m in moves) / n
    months = max(len(trades_by_day) / 21, 1e-9)
    print(f"{name:16s} trades {n:5d} ({n / len(trades_by_day):4.1f}/day)  win {wins * 100:4.1f}%  "
          f"before costs ₹{gross:+10,.0f}  after fees ₹{gross - fees:+10,.0f}  "
          f"after fees+slippage ₹{gross - fees - slip:+10,.0f}  (₹{(gross - fees - slip) / months:+,.0f}/month)")


def load_upstox(months):
    import os
    from upstox_api import API, Upstox
    api = Upstox(os.environ.get("UPSTOX_ACCESS_TOKEN", ""))
    key = "NSE_INDEX|Nifty 50"
    days = defaultdict(list)
    end = date.today()
    for m in range(months):
        to, frm = end - timedelta(days=30 * m), end - timedelta(days=30 * (m + 1) - 1)
        url = f"{API}/v3/historical-candle/{key}/minutes/1/{to.isoformat()}/{frm.isoformat()}"
        for c in api._req("GET", url)["candles"]:
            ts = datetime.fromisoformat(c[0])
            days[ts.date().isoformat()].append((ts.strftime("%H:%M"), c[4]))
        print(f"\rDownloaded month {m + 1}/{months}", end="", flush=True)
        time.sleep(0.3)
    print()
    return {d: sorted(v) for d, v in sorted(days.items())}


def synthetic(n_days=120):
    rnd, days, px = random.Random(7), {}, 25_000.0
    for d in range(n_days):
        bars, t = [], datetime(2026, 1, 1, 9, 15)
        for _ in range(375):
            px += rnd.gauss(0, 6)
            bars.append((t.strftime("%H:%M"), px))
            t += timedelta(minutes=1)
        days[f"day{d:03d}"] = bars
    return days


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--upstox", action="store_true", help="use real Nifty 1-minute data")
    ap.add_argument("--months", type=int, default=6)
    ap.add_argument("--lot", type=int, default=LOT)
    args = ap.parse_args()
    data = load_upstox(args.months) if args.upstox else synthetic()
    print(f"{len(data)} trading days of 1-minute Nifty data. One lot ({args.lot} units) of an ATM option, "
          f"fees ₹{FEES_PER_TRIP:.0f}/round trip, slippage {SLIP_PTS_PER_SIDE} pts/side.\n")
    for name, fn in (("momentum", momentum), ("mean-reversion", mean_reversion), ("random (baseline)", random_entries)):
        report(name, {d: fn(bars) for d, bars in data.items()}, args.lot)


if __name__ == "__main__":
    main()
