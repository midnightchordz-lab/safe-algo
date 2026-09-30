"""Run once per trading day, around 3:00-3:15 PM IST (after enough of the day has traded).

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
    """Trust the broker over our ledger when they disagree, in the safe direction."""
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
    if state["halted"]:
        # Frozen: no normal trading, but the hard floor below is still enforced.
        log.error("Bot is HALTED: %s. Only the ₹%.0f hard floor is being watched. "
                  "Run `python3 bot.py --resume` once you've decided to continue.",
                  state["halt_reason"], cfg.hard_floor)
    api = Upstox(cfg.access_token)
    keys = resolve_instrument_keys(cfg.symbols, cfg.instrument_keys)

    if cfg.live:
        reconcile(state, api.holdings(), api.available_cash(), keys)

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
