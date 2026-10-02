"""Score the commodity bot's plans against what the futures actually did afterwards.

  UPSTOX_ACCESS_TOKEN=$(cat token.txt) python3 -m commodity.score            # every logged night
  UPSTOX_ACCESS_TOKEN=$(cat token.txt) python3 -m commodity.score --days 14  # just the last 14 days

Read-only: it reads commodity_decisions.jsonl and 15-minute futures candles from Upstox, never
places orders. For every plan Claude gave (the main call if it was UP/DOWN, plus the conditional
call/put plans) it checks: did the trigger trade before the entry window closed, and after that
did the stop or the target come first (or the time exit)? When one 15-minute bar touches both the
stop and the target, it counts the stop (the cautious assumption). Option P&L is a rough estimate:
delta 0.5 x futures points x lot units, less charges.
"""
import json
import os
import sys
from datetime import datetime, timedelta

from commodity import engine, market
from commodity.config import CommodityConfig
from market_hours import IST
from upstox_api import API, Upstox, UpstoxError

DELTA = 0.5
LOT_UNITS = 100      # NSE CRUDEOIL: 100 barrels per lot
CHARGES = 120.0      # rough round trip for one option lot


def plans_for(entry, cfg):
    """(label, plan, armed) for every plan in one logged decision."""
    d, out = entry["decision"], []
    if d.get("direction") in ("UP", "DOWN") and d.get("entry_trigger"):
        out.append(("main " + d["direction"], d, bool(entry.get("passed_rules"))))
    atr = (entry.get("tech") or {}).get("atr14") or 0
    for p in engine.plans(d, entry.get("trade_future") or 0):
        armed = bool(atr) and engine.validate(p, p["entry_trigger"], atr, None, cfg)[0]
        out.append(("call plan" if p["direction"] == "UP" else "put plan", p, armed))
    return out


def simulate(plan, candles, start, cfg):
    """candles: [(hhmm, open, high, low, close)] in time order for that evening; start: 'HH:MM'.
    Returns {"result": "no trigger"|"target"|"stop"|"time", "entry", "exit", "points"}."""
    up = plan["direction"] == "UP"
    trig, stop, target = plan["entry_trigger"], plan["stop_level"], plan["target_level"]
    sign = 1 if up else -1
    bars = [c for c in candles if c[0] >= start[:3] + f"{int(start[3:]) // 15 * 15:02d}"]
    entered = None
    for hhmm, o, h, lo, c in bars:
        if entered is None:
            if hhmm >= cfg.entry_window[1]:
                break
            if (h >= trig) if up else (lo <= trig):
                entered = trig
                if (lo <= stop) if up else (h >= stop):
                    return {"result": "stop", "entry": trig, "exit": stop, "points": sign * (stop - trig)}
                if (h >= target) if up else (lo <= target):
                    return {"result": "target", "entry": trig, "exit": target, "points": sign * (target - trig)}
            continue
        if hhmm >= cfg.exit_by:
            return {"result": "time", "entry": entered, "exit": o, "points": sign * (o - entered)}
        if (lo <= stop) if up else (h >= stop):
            return {"result": "stop", "entry": entered, "exit": stop, "points": sign * (stop - entered)}
        if (h >= target) if up else (lo <= target):
            return {"result": "target", "entry": entered, "exit": target, "points": sign * (target - entered)}
    if entered is None:
        return {"result": "no trigger", "entry": None, "exit": None, "points": 0.0}
    last = bars[-1][4]
    return {"result": "time", "entry": entered, "exit": last, "points": sign * (last - entered)}


def rupees(points):
    return DELTA * points * LOT_UNITS - CHARGES


def summarise(results):
    """results: list of simulate() dicts. Freqtrade-style numbers for the triggered ones."""
    done = [r for r in results if r["result"] != "no trigger"]
    wins = [rupees(r["points"]) for r in done if rupees(r["points"]) > 0]
    losses = [rupees(r["points"]) for r in done if rupees(r["points"]) <= 0]
    return {"plans": len(results), "triggered": len(done), "wins": len(wins), "losses": len(losses),
            "win_rate": round(100 * len(wins) / len(done)) if done else 0,
            "avg_win": round(sum(wins) / len(wins)) if wins else 0,
            "avg_loss": round(sum(losses) / len(losses)) if losses else 0,
            "profit_factor": round(sum(wins) / -sum(losses), 2) if losses and sum(losses) < 0 else None,
            "total": round(sum(wins) + sum(losses))}


def candles_for(up, key, day):
    rows = up._req("GET", f"{API}/v3/historical-candle/{key}/minutes/15/{day}/{day}")["candles"]
    return [(c[0][11:16], c[1], c[2], c[3], c[4]) for c in sorted(rows, key=lambda c: c[0])]


def load_entries(path, days=None):
    if not os.path.exists(path):
        return []
    with open(path) as f:
        entries = [json.loads(line) for line in f if line.strip()]
    if days:
        cutoff = (datetime.now(IST) - timedelta(days=days)).date().isoformat()
        entries = [e for e in entries if e["time"][:10] >= cutoff]
    return entries


def line(s):
    pf = s["profit_factor"] if s["profit_factor"] is not None else "-"
    return (f"{s['plans']} plans, {s['triggered']} triggered: {s['wins']} won / {s['losses']} lost "
            f"(win rate {s['win_rate']}%), avg win ₹{s['avg_win']:,}, avg loss ₹{s['avg_loss']:,}, "
            f"profit factor {pf}, total ₹{s['total']:,}")


def main(up=None, out=print):
    cfg = CommodityConfig()
    days = int(sys.argv[sys.argv.index("--days") + 1]) if "--days" in sys.argv else None
    entries = load_entries(cfg.decisions_file, days)
    today = datetime.now(IST).date().isoformat()
    up = up or Upstox(os.environ.get("UPSTOX_ACCESS_TOKEN", ""))
    fallback_key, cache, seen, rows = None, {}, set(), []
    for e in entries:
        day, start = e["time"][:10], e["time"][11:16]
        if day >= today:
            continue  # tonight isn't finished yet
        for label, plan, armed in plans_for(e, cfg):
            sig = (day, plan["direction"], plan["entry_trigger"], plan["stop_level"], plan["target_level"])
            if sig in seen:
                continue  # the same plan logged again by a restart
            seen.add(sig)
            key = e.get("trade_key")
            if not key:
                if fallback_key is None:
                    fallback_key = market.nearest_future(market.load_rows(cfg.trade_exchange, cfg.underlying))["instrument_key"]
                key = fallback_key
            if (key, day) not in cache:
                try:
                    cache[(key, day)] = candles_for(up, key, day)
                except UpstoxError as err:
                    out(f"{day}: no candles ({err}); skipped")
                    cache[(key, day)] = []
            if not cache[(key, day)]:
                continue
            r = simulate(plan, cache[(key, day)], start, cfg)
            rows.append((day, start, label, plan, armed, r))
    if not rows:
        out("No finished plans to score yet.")
        return 0
    for day, start, label, plan, armed, r in rows:
        move = f"{r['entry']:g} -> {r['exit']:g}, {r['points']:+.0f} pts, ~₹{rupees(r['points']):,.0f}" \
            if r["entry"] is not None else ""
        out(f"{day} {start}  {engine.describe(plan) if 'plan' in label else label + ' ' + str(plan['entry_trigger'])}"
            f"  [{'armed' if armed else 'not armed'}]  {r['result']}  {move}")
    out("")
    out("ALL plans:   " + line(summarise([r for *_, r in rows])))
    out("ARMED only:  " + line(summarise([r for *_, armed, r in rows if armed])))
    out("(Rough: futures levels, 15-minute bars, option ~0.5 x futures move, ₹120 charges per trade.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
