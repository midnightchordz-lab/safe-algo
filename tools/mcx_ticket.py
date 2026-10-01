"""Manual commodity option order ticket (NSE commodities/NCO by default, or --exchange MCX).
YOU confirm every order; nothing is sent without typing YES.

  python3 -m tools.mcx_ticket search CRUDEOIL PE     # NSE commodities: futures price + nearby strikes
  python3 -m tools.mcx_ticket --exchange MCX search CRUDEOILM PE
  python3 -m tools.mcx_ticket analyze CRUDEOIL      # read-only trend/momentum read on the futures
  python3 -m tools.mcx_ticket buy "<TRADING SYMBOL>" --lots 1 --budget 5000 --sl 40
  python3 -m tools.mcx_ticket exit "<TRADING SYMBOL>" --lots 1

Safety:
  * BUY options only (never sells an option you don't hold); intraday product, so the broker
    squares off anything still open before MCX closes.
  * Quantity is sent as a number of LOTS (1-2). If Upstox expected units instead, such a small
    number is rejected rather than becoming a huge order.
  * Refuses if the estimated cost exceeds --budget, after 23:00 IST, or within 3 days of expiry.
  * `exit` cancels this tool's pending stop-loss first, so you can't end up short an option.
Not used by the bots. Every action is logged to manual_trades.log.
"""
import argparse
import gzip
import io
import json
import math
import os
import sys
import time
from datetime import datetime

import requests

import envfile

envfile.load()
from market_hours import IST  # noqa: E402
from upstox_api import Upstox, UpstoxError  # noqa: E402

INSTRUMENT_FILES = {
    "MCX": "https://assets.upstox.com/market-quote/instruments/exchange/MCX.json.gz",
    "NSE": "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz",
}
NOT_COMMODITY = {"NSE_EQ", "NSE_FO", "NSE_INDEX", "NCD_FO", "BSE_EQ", "BSE_FO", "BSE_INDEX"}
EXCHANGE = "NSE"  # set from --exchange; MCX API orders are currently disabled by Upstox
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(HERE, "manual_trades.log")
TAG = "manual-ticket"
MAX_LOTS = 2


def log(msg):
    line = f"{datetime.now(IST):%Y-%m-%d %H:%M:%S} {msg}"
    print(line)
    with open(LOG, "a") as f:
        f.write(line + "\n")


def api():
    with open(os.path.join(HERE, "token.txt")) as f:
        return Upstox(f.read().strip())


_ROWS = []


def is_commodity(r):
    seg = r.get("segment", "")
    return seg == "MCX_FO" if EXCHANGE == "MCX" else (seg.startswith("NSE") or seg.startswith("NCO")) and seg not in NOT_COMMODITY


def mcx_rows():
    """Commodity derivative rows for the chosen exchange (name kept for compatibility)."""
    if not _ROWS:
        raw = requests.get(INSTRUMENT_FILES[EXCHANGE], timeout=60).content
        _ROWS.extend(r for r in json.load(gzip.GzipFile(fileobj=io.BytesIO(raw))) if is_commodity(r))
    return _ROWS


def options():
    return [r for r in mcx_rows() if r.get("instrument_type") in ("CE", "PE")]


def futures_price(name, up):
    """Live price of the nearest-expiry future whose symbol starts with `name` (e.g. CRUDEOILM)."""
    futs = [r for r in mcx_rows() if r.get("instrument_type") == "FUT"
            and r.get("trading_symbol", "").upper().split(" ")[0] == name.upper()]
    futs.sort(key=lambda r: r.get("expiry") or 0)
    for r in futs[:2]:
        p = up.ltp([r["instrument_key"]]).get(r["instrument_key"])
        if p:
            return r["trading_symbol"], p
    return None, None


def expiry_of(rec):
    e = rec.get("expiry")
    return datetime.fromtimestamp(e / 1000, IST) if isinstance(e, (int, float)) else None


def units_per_lot(rec):
    return max(float(rec.get("lot_size") or 1), float(rec.get("qty_multiplier") or 1))


def tick_round(price, tick, up):
    steps = price / tick
    return round((math.ceil(steps) if up else math.floor(steps)) * tick, 2)


def confirm(prompt):
    return input(f"\n{prompt}\nType YES to send, anything else to cancel: ").strip() == "YES"


def cmd_search(args):
    words = [w.upper() for w in args.words]
    found = [r for r in options() if all(w in r.get("trading_symbol", "").upper() for w in words)]
    if not args.around and found:
        fut, px = futures_price(words[0], api())
        if px:
            print(f"Futures {fut}: ₹{px:,.2f}  <- check this matches the Upstox app")
            args.around = px
        else:
            print("Couldn't read the futures price; showing contracts from the lowest strike.")
    if args.around:
        nearest = min((r.get("expiry") or 0) for r in found) if found else 0
        found = [r for r in found if (r.get("expiry") or 0) == nearest] if not args.all_expiries else found
        found.sort(key=lambda r: abs((r.get("strike_price") or 0) - args.around))
        found = found[: args.limit if args.limit != 40 else 12]
    found.sort(key=lambda r: (r.get("expiry") or 0, r.get("strike_price") or 0))
    found = found[: args.limit]
    if not found:
        print(f"No matching {EXCHANGE} commodity option contracts. Try fewer words, e.g. `search CRUDEOIL`.")
        segs = sorted({r.get("segment") for r in mcx_rows()})
        print(f"(Commodity segments found on {EXCHANGE}: {segs or 'none'})")
        return
    print(f"Exchange: {EXCHANGE}  segment: {found[0].get('segment')}")
    prices = api().ltp([r["instrument_key"] for r in found])
    print(f"{'TRADING SYMBOL':34s} {'EXPIRY':11s} {'LOT':>7s} {'PREMIUM':>9s} {'COST/LOT':>10s}")
    for r in found:
        p = prices.get(r["instrument_key"])
        exp = expiry_of(r)
        cost = f"₹{p * units_per_lot(r):,.0f}" if p else "-"
        print(f"{r['trading_symbol']:34s} {exp:%d-%b-%Y}  {units_per_lot(r):7.0f} "
              f"{(f'{p:.2f}' if p else '-'):>9s} {cost:>10s}" if exp else r["trading_symbol"])
    print("\nCopy the exact TRADING SYMBOL into: python3 -m tools.mcx_ticket buy \"<symbol>\" --lots 1 --budget 5000")


def find(symbol):
    for r in options():
        if r.get("trading_symbol", "").upper() == symbol.upper():
            return r
    sys.exit(f"'{symbol}' not found. Use `search` and copy the TRADING SYMBOL exactly.")


def wait_fill(up, order_id, wait_s=30):
    deadline, d = time.time() + wait_s, {}
    while time.time() < deadline:
        d = up.order_details(order_id)
        if d.get("status") in ("complete", "rejected", "cancelled"):
            return d
        time.sleep(2)
    try:
        up.cancel_order(order_id)
        log(f"Order {order_id} not filled in {wait_s}s; cancelled the rest.")
    except UpstoxError:
        pass
    time.sleep(2)
    return up.order_details(order_id)


def cmd_buy(args):
    now = datetime.now(IST)
    if now.weekday() > 4 or not (9 <= now.hour < 23):
        sys.exit("The ticket only works Mon-Fri 09:00-23:00 IST (leaves time to exit before close).")
    if not 1 <= args.lots <= MAX_LOTS:
        sys.exit(f"--lots must be 1-{MAX_LOTS}.")
    rec = find(args.symbol)
    exp = expiry_of(rec)
    if exp and (exp - now).days < 3:
        sys.exit(f"Expires {exp:%d-%b}. Too close to expiry (time decay is brutal). Pick a later expiry.")
    up = api()
    ltp = up.ltp([rec["instrument_key"]]).get(rec["instrument_key"])
    if not ltp:
        sys.exit("No live premium for this contract right now (illiquid or market closed).")
    tick = float(rec.get("tick_size") or 0.05)
    tick = tick / 100 if tick >= 1 and ltp < tick * 10 else tick  # some files store tick in paise
    limit = tick_round(ltp * 1.01, tick, up=True)
    upl = units_per_lot(rec)
    cost = limit * upl * args.lots
    sl_trigger = tick_round(limit * (1 - args.sl / 100), tick, up=False)
    max_loss = (limit - sl_trigger) * upl * args.lots

    print("\n========== ORDER TICKET (check every line) ==========")
    print(f"Exchange      : {EXCHANGE}  (segment {rec.get('segment')})")
    print(f"Contract      : {rec['trading_symbol']}  ({rec.get('instrument_type')})")
    print(f"Expiry        : {exp:%d-%b-%Y}" if exp else "Expiry        : ?")
    print(f"Action        : BUY {args.lots} lot(s), INTRADAY, LIMIT ₹{limit:.2f}  (live premium ₹{ltp:.2f})")
    print(f"Units per lot : {upl:g}   (lot_size={rec.get('lot_size')}, qty_multiplier={rec.get('qty_multiplier')})")
    print(f"Est. cost     : ₹{cost:,.0f}   (budget ₹{args.budget:,.0f})")
    print(f"Stop-loss plan: trigger ₹{sl_trigger:.2f} (-{args.sl:.0f}%), est. max loss ₹{max_loss:,.0f}")
    print(f"Sent to Upstox: quantity={args.lots} (lots), instrument_key={rec['instrument_key']}")
    print("======================================================")
    print("Compare the cost with the Upstox app's order screen for the same contract.")
    if cost > args.budget:
        sys.exit(f"Refused: estimated cost ₹{cost:,.0f} is above your budget ₹{args.budget:,.0f}.")
    if not confirm("Send this BUY order?"):
        print("Cancelled. Nothing was sent.")
        return

    log(f"BUY sent {EXCHANGE} {rec['trading_symbol']} lots={args.lots} limit={limit}")
    try:
        oid = up.place_custom_order(rec["instrument_key"], "BUY", args.lots, "LIMIT", limit, product="I", tag=TAG)
    except UpstoxError as e:
        log(f"BUY rejected: {e}")
        return
    d = wait_fill(up, oid)
    filled, avg = int(d.get("filled_quantity") or 0), float(d.get("average_price") or 0)
    log(f"BUY result: status={d.get('status')} filled_quantity={filled} avg=₹{avg:.2f} message={d.get('status_message')}")
    if not filled:
        print("Not filled, so you hold nothing. No stop-loss needed.")
        return

    trigger = tick_round(avg * (1 - args.sl / 100), tick, up=False)
    price = tick_round(trigger * 0.97, tick, up=False)
    print(f"\nFilled at ₹{avg:.2f}. Upstox reports filled_quantity={filled}.")
    if confirm(f"Place STOP-LOSS: SELL {args.lots} lot(s) if premium falls to ₹{trigger:.2f} (limit ₹{price:.2f})?"):
        try:
            sl_id = up.place_custom_order(rec["instrument_key"], "SELL", args.lots, "SL", price, trigger,
                                          product="I", tag=TAG)
            log(f"STOP-LOSS placed {rec['trading_symbol']} trigger={trigger} limit={price} id={sl_id}")
        except UpstoxError as e:
            log(f"STOP-LOSS rejected: {e}  ->  SET ONE MANUALLY IN THE APP NOW.")
    print("\nTo take profit or exit early, use:  python3 -m tools.mcx_ticket exit \"%s\" --lots %d" % (rec["trading_symbol"], args.lots))
    print("(It cancels the stop-loss first. If you exit in the app instead, CANCEL THE STOP-LOSS there too.)")


def cmd_exit(args):
    rec = find(args.symbol)
    up = api()
    pending = [o for o in up.order_book() if o.get("instrument_token") == rec["instrument_key"]
               and o.get("tag") == TAG and o.get("transaction_type") == "SELL"
               and o.get("status") in ("open", "trigger pending", "pending")]
    ltp = up.ltp([rec["instrument_key"]]).get(rec["instrument_key"]) or 0
    tick = float(rec.get("tick_size") or 0.05)
    tick = tick / 100 if tick >= 1 and ltp and ltp < tick * 10 else tick
    limit = tick_round(ltp * 0.99, tick, up=False)
    print(f"Pending stop-loss orders to cancel first: {len(pending)}")
    if not confirm(f"Cancel those and SELL {args.lots} lot(s) of {rec['trading_symbol']} at limit ₹{limit:.2f} "
                   f"(live ₹{ltp:.2f})?"):
        print("Cancelled. Nothing was sent.")
        return
    for o in pending:
        up.cancel_order(o["order_id"])
        log(f"Cancelled stop-loss {o['order_id']}")
    try:
        oid = up.place_custom_order(rec["instrument_key"], "SELL", args.lots, "LIMIT", limit, product="I", tag=TAG)
    except UpstoxError as e:
        log(f"EXIT rejected: {e}  ->  EXIT MANUALLY IN THE APP.")
        return
    d = wait_fill(up, oid)
    log(f"EXIT result: status={d.get('status')} filled_quantity={d.get('filled_quantity')} "
        f"avg=₹{float(d.get('average_price') or 0):.2f}")


def cmd_analyze(args):
    """Read-only market read on the nearest futures contract (daily trend + today's 15-minute action)."""
    from swing.indicators import atr, ema
    from upstox_api import API, Candle
    futs = [r for r in mcx_rows() if r.get("instrument_type") == "FUT"
            and r.get("trading_symbol", "").upper().split(" ")[0] == args.name.upper()]
    futs.sort(key=lambda r: r.get("expiry") or 0)
    if not futs:
        sys.exit(f"No {args.name} futures found on {EXCHANGE}.")
    fut = futs[0]
    up, key = api(), fut["instrument_key"]
    to = datetime.now(IST).date()
    frm = to.replace(year=to.year - 1)
    rows = up._req("GET", f"{API}/v3/historical-candle/{key}/days/1/{to.isoformat()}/{frm.isoformat()}")["candles"]
    daily = [Candle(c[0][:10], c[1], c[2], c[3], c[4], c[5]) for c in sorted(rows, key=lambda c: c[0])]
    try:
        intra = up._req("GET", f"{API}/v3/historical-candle/intraday/{key}/minutes/15")["candles"]
        intra = [Candle(c[0][11:16], c[1], c[2], c[3], c[4], c[5]) for c in sorted(intra, key=lambda c: c[0])]
    except UpstoxError:
        intra = []
    ltp = up.ltp([key]).get(key) or (intra[-1].close if intra else daily[-1].close)
    if len(daily) < 60:
        sys.exit(f"Only {len(daily)} daily candles available; not enough history for a read.")
    closes = [c.close for c in daily]
    if daily[-1].date != to.isoformat():
        closes.append(ltp)
    e20, e50 = ema(closes, 20)[-1], ema(closes, 50)[-1]
    a14 = atr(daily, 14)[-1]
    gains = [max(closes[i] - closes[i - 1], 0) for i in range(len(closes) - 14, len(closes))]
    losses = [max(closes[i - 1] - closes[i], 0) for i in range(len(closes) - 14, len(closes))]
    rsi = 100 - 100 / (1 + (sum(gains) / max(sum(losses), 1e-9)))
    hi20, lo20 = max(c.high for c in daily[-20:]), min(c.low for c in daily[-20:])

    def pct(a, b):
        return (a / b - 1) * 100

    print(f"\n===== {fut['trading_symbol']} ({EXCHANGE}) read at {datetime.now(IST):%d-%b %H:%M} =====")
    print(f"Price now          : {ltp:,.2f}")
    print(f"vs 20-day avg      : {e20:,.2f}  ({pct(ltp, e20):+.2f}%)")
    print(f"vs 50-day avg      : {e50:,.2f}  ({pct(ltp, e50):+.2f}%)")
    print(f"5-day / 20-day move: {pct(ltp, closes[-6]):+.2f}% / {pct(ltp, closes[-21]):+.2f}%")
    print(f"20-day range       : {lo20:,.2f} - {hi20:,.2f}  (price at {100 * (ltp - lo20) / max(hi20 - lo20, 1e-9):.0f}% of range)")
    print(f"RSI(14, daily)     : {rsi:.0f}")
    print(f"Typical daily move : ±{a14:,.0f} ({100 * a14 / ltp:.1f}%)")
    exp = futs[0].get("expiry")
    if exp:
        days = max((datetime.fromtimestamp(exp / 1000, IST).date() - to).days, 1)
        print(f"Typical move by {datetime.fromtimestamp(exp / 1000, IST):%d-%b} : about ±{a14 * days ** 0.5 * 0.8:,.0f}")
    if intra:
        d_open, d_hi, d_lo = intra[0].open, max(c.high for c in intra), min(c.low for c in intra)
        avg = sum(c.close for c in intra) / len(intra)
        last4 = [c.close for c in intra[-4:]]
        print(f"Today              : open {d_open:,.2f}, high {d_hi:,.2f}, low {d_lo:,.2f}  "
              f"({pct(ltp, d_open):+.2f}% vs open)")
        print(f"Today's avg price  : {avg:,.2f}  (price {'above' if ltp > avg else 'below'} it)")
        print(f"Last hour (15m)    : {' -> '.join(f'{x:,.0f}' for x in last4)}")
    else:
        print("Today's 15-minute candles not available.")
    print("==========================================================")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--exchange", choices=("NSE", "MCX"), default="NSE",
                    help="NSE commodities (NCO, default) or MCX (Upstox has MCX API orders disabled for now)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("search")
    s.add_argument("words", nargs="+")
    s.add_argument("--limit", type=int, default=40)
    s.add_argument("--around", type=float, help="show the strikes nearest this price (e.g. the futures price)")
    s.add_argument("--all-expiries", action="store_true", help="with --around: include later expiries too")
    b = sub.add_parser("buy")
    b.add_argument("symbol")
    b.add_argument("--lots", type=int, default=1)
    b.add_argument("--budget", type=float, default=5000)
    b.add_argument("--sl", type=float, default=40, help="stop-loss %% below fill price")
    an = sub.add_parser("analyze")
    an.add_argument("name", nargs="?", default="CRUDEOIL")
    e = sub.add_parser("exit")
    e.add_argument("symbol")
    e.add_argument("--lots", type=int, default=1)
    args = ap.parse_args()
    global EXCHANGE
    EXCHANGE = args.exchange
    {"search": cmd_search, "buy": cmd_buy, "exit": cmd_exit, "analyze": cmd_analyze}[args.cmd](args)


if __name__ == "__main__":
    main()
