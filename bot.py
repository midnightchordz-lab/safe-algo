"""Run once per trading day, around 2:50 PM IST (orders stop at 3:10 PM, before the closing auction).

  python bot.py              # PAPER mode (default): reads real prices, places no orders
  ALGO_LIVE=1 python bot.py  # LIVE mode: places real delivery orders on Upstox

Requires UPSTOX_ACCESS_TOKEN (Upstox tokens expire daily, generate a fresh one each morning).
"""
import json
import logging
import os
import sys
from datetime import datetime

from config import Config
from market_hours import can_place_orders
from engine import apply_fill, decide, equity, halt, new_state, update_peaks
from upstox_api import Upstox, UpstoxError, resolve_instrument_keys

log = logging.getLogger("algo")


def load_state(cfg):
    if os.path.exists(cfg.state_file):
        with open(cfg.state_file) as f:
            return json.load(f)
    return new_state(cfg)


def save_state(cfg, state):
    tmp = cfg.state_file + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, cfg.state_file)


def reconcile(state, broker_qty, broker_cash, keys):
    """Trust the broker over our ledger when they disagree, in the safe direction.

    Returns [(symbol, ledger_qty, broker_qty)] where the broker holds MORE than the ledger knows
    about (e.g. an order filled but its confirmation was lost). The caller must not trade then,
    or it could buy the same shares twice."""
    extra = [(sym, state["positions"].get(sym, {"qty": 0})["qty"], broker_qty.get(key, 0))
             for sym, key in keys.items()
             if broker_qty.get(key, 0) > state["positions"].get(sym, {"qty": 0})["qty"]]
    for sym in list(state["positions"]):
        held = broker_qty.get(keys[sym], 0)
        if held < state["positions"][sym]["qty"]:
            log.warning("%s: ledger says %d but broker holds %d, using broker figure",
                        sym, state["positions"][sym]["qty"], held)
            if held == 0:
                del state["positions"][sym]
            else:
                state["positions"][sym]["qty"] = held
    if broker_cash < state["cash"]:
        log.warning("Broker cash ₹%.2f < ledger cash ₹%.2f, capping ledger", broker_cash, state["cash"])
        state["cash"] = broker_cash
    return extra


def adopt_holdings(state, detail, keys, cfg):
    """Add shares Upstox holds beyond the ledger (bot's own symbols only) at their average price."""
    added = []
    for sym, key in keys.items():
        held, avg = detail.get(key, (0, 0.0))
        known = state["positions"].get(sym, {"qty": 0})["qty"]
        if held > known and avg > 0:
            apply_fill(state, sym, "BUY", held - known, avg, cfg)
            added.append((sym, held - known, avg))
    return added


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.StreamHandler(sys.stdout),
                                  logging.FileHandler(os.path.join(os.path.dirname(__file__), "algo.log"))])
    cfg = Config()
    mode = "LIVE" if cfg.live else "PAPER"
    log.info("=== run %s, mode %s ===", datetime.now().isoformat(timespec="seconds"), mode)

    state = load_state(cfg)
    if "--resume" in sys.argv:
        state["halted"], state["halt_reason"] = False, ""
        save_state(cfg, state)
        if os.path.exists(cfg.halt_file):
            os.remove(cfg.halt_file)
        log.info("Halt cleared. Trading resumes on the next run.")
        return 0
    if "--adopt-holdings" in sys.argv:
        api = Upstox(cfg.access_token)
        added = adopt_holdings(state, api.holdings_detail(), resolve_instrument_keys(cfg.symbols, cfg.instrument_keys), cfg)
        for sym, qty, avg in added:
            log.info("Adopted %d %s @ ₹%.2f from Upstox into the bot's record", qty, sym, avg)
        if state["halted"] and state["halt_reason"].startswith("ledger mismatch"):
            state["halted"], state["halt_reason"] = False, ""
            if os.path.exists(cfg.halt_file):
                os.remove(cfg.halt_file)
        save_state(cfg, state)
        log.info("Record now: cash ₹%.2f | positions %s", state["cash"],
                 {s: p["qty"] for s, p in state["positions"].items()})
        return 0
    if state["halted"]:
        # Frozen: no normal trading, but the hard floor below is still enforced.
        log.error("Bot is HALTED: %s. Only the ₹%.0f hard floor is being watched. "
                  "Run `python3 bot.py --resume` once you've decided to continue.",
                  state["halt_reason"], cfg.hard_floor)
    api = Upstox(cfg.access_token)
    keys = resolve_instrument_keys(cfg.symbols, cfg.instrument_keys)

    if cfg.live:
        extra = reconcile(state, api.holdings(), api.available_cash(), keys)
        if extra:
            what = ", ".join(f"{sym} (record {known}, Upstox {held})" for sym, known, held in extra)
            reason = f"ledger mismatch: Upstox holds more than the bot's record: {what}"
            halt(state, reason)
            with open(cfg.halt_file, "w") as f:
                f.write(f"{datetime.now().isoformat()} {reason}\n")
            save_state(cfg, state)
            log.error("%s. Not trading, so nothing is bought twice. If those shares are the bot's, run "
                      "`.venv/bin/python bot.py --adopt-holdings` on the server.", reason)
            return 1

    closes = {s: api.daily_closes(k) for s, k in keys.items()}
    ltp = api.ltp(list(keys.values()))
    for s, k in keys.items():
        # History excludes today; the live price stands in for today's close.
        if k not in ltp:
            log.error("No live price for %s, skipping this run to be safe.", s)
            return 1
        closes[s] = closes[s] + [ltp[k]]
    prices = {s: c[-1] for s, c in closes.items()}
    update_peaks(state, prices)

    orders = decide(state, closes, cfg)
    if not orders:
        log.info("No action today.")
    for o in orders:
        if o["side"] == "HALT":
            halt(state, o["reason"])
            with open(cfg.halt_file, "w") as f:
                f.write(f"{datetime.now().isoformat()} {o['reason']}\n")
            log.error("KILL SWITCH: %s. Trading halted (holdings kept unless the hard floor "
                      "was hit). Run `python3 bot.py --resume` to continue.", o["reason"])
            continue
        sym, side, qty = o["symbol"], o["side"], o["qty"]
        if cfg.live and not can_place_orders():
            log.error("Past the 3:10 PM order cutoff; not placing %s %s. It will retry tomorrow.", side, sym)
            break
        px = prices[sym]
        limit = px * (1 + cfg.limit_slippage_pct) if side == "BUY" else px * (1 - cfg.limit_slippage_pct)
        log.info("%s %s %d %s @ ~₹%.2f (limit ₹%.2f) reason=%s", mode, side, qty, sym, px, limit, o["reason"])
        if cfg.live:
            try:
                filled, avg = api.execute(keys[sym], side, qty, limit)
            except UpstoxError as e:
                log.error("Order failed: %s", e)
                continue
            log.info("  filled %d @ ₹%.2f", filled, avg)
            apply_fill(state, sym, side, filled, avg or px, cfg)
        else:
            apply_fill(state, sym, side, qty, px, cfg)
        save_state(cfg, state)

    save_state(cfg, state)
    log.info("Cash ₹%.2f | positions %s | equity ₹%.2f (floor ₹%.2f)",
             state["cash"], {s: p["qty"] for s, p in state["positions"].items()},
             equity(state, prices), cfg.capital_floor)
    return 0


if __name__ == "__main__":
    sys.exit(main())
