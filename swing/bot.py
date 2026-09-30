"""Swing bot runner. Run once per trading day at ~2:57 PM IST (orders stop at 3:10 PM).

  python3 -m swing.bot                # PAPER: real prices, simulated fills
  SWING_LIVE=1 python3 -m swing.bot   # LIVE: real orders with broker-held stop and target
  python3 -m swing.bot --resume       # clear a halt after you've reviewed it

In LIVE mode each entry is a single Upstox GTT order: buy now + 3:1 target + stop-loss, held by
Upstox, so exits fire during market hours even if this computer is off.
"""
import json
import logging
import os
import sys
import time
from datetime import date, datetime

from market_hours import can_place_orders
from swing.config import SwingConfig
from swing.engine import (check_exit, close_position, end_of_day, equity, floor_action,
                          new_state, open_position, pick_entries)
from swing.indicators import compute
from upstox_api import Upstox, UpstoxError, resolve_instrument_keys

log = logging.getLogger("swing")


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


def halt(cfg, state, reason):
    state["halted"], state["halt_reason"] = True, reason
    with open(cfg.halt_file, "w") as f:
        f.write(f"{datetime.now().isoformat()} {reason}\n")
    log.error("SWING HALTED: %s. Existing stops/targets stay active. "
              "Run `python3 -m swing.bot --resume` once you've decided to continue.", reason)


def wait_for_fill(api, gtt_id, wait_s=30):
    """After placing a bracket GTT, wait for its entry order. Returns (qty, avg_price)."""
    deadline = time.time() + wait_s
    while time.time() < deadline:
        time.sleep(3)
        try:
            rules = api.gtt_details(gtt_id).get("rules", [])
            entry = next((r for r in rules if r.get("strategy") == "ENTRY" and r.get("order_id")), None)
            if entry:
                d = api.order_details(entry["order_id"])
                if d.get("status") in ("complete", "rejected", "cancelled"):
                    return int(d.get("filled_quantity") or 0), float(d.get("average_price") or 0)
        except UpstoxError as e:
            log.warning("Checking GTT %s: %s", gtt_id, e)
    return 0, 0.0


def exit_fill(api, gtt_id):
    """If the broker-held target or stop already fired, return (price, reason)."""
    try:
        rules = api.gtt_details(gtt_id).get("rules", [])
    except UpstoxError:
        return None
    for r in rules:
        if r.get("strategy") in ("TARGET", "STOPLOSS") and r.get("order_id"):
            d = api.order_details(r["order_id"])
            if d.get("status") == "complete":
                return float(d["average_price"]), r["strategy"].lower().replace("stoploss", "stop")
    return None


def sell_now(api, cfg, key, pos, price):
    """Cancel the bracket and sell at a limit just under the market. Returns (qty, avg)."""
    if pos.get("gtt_id"):
        try:
            api.cancel_gtt(pos["gtt_id"])
        except UpstoxError as e:
            log.warning("Cancel GTT %s: %s", pos["gtt_id"], e)
    return api.execute(key, "SELL", pos["qty"], price * 0.995)


def main():
    cfg = SwingConfig()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler(cfg.log_file)])
    mode = "LIVE" if cfg.live else "PAPER"
    today_str = date.today().isoformat()
    state = load_state(cfg)

    if "--resume" in sys.argv:
        state["halted"], state["halt_reason"] = False, ""
        save_state(cfg, state)
        if os.path.exists(cfg.halt_file):
            os.remove(cfg.halt_file)
        log.info("Swing halt cleared.")
        return 0
    log.info("=== swing run %s, mode %s ===", datetime.now().isoformat(timespec="seconds"), mode)
    if state.get("last_run") == today_str and "--force" not in sys.argv:
        log.info("Already ran today; skipping so nothing is traded twice.")
        return 0

    api = Upstox(cfg.access_token)
    keys = resolve_instrument_keys(cfg.symbols, cfg.instrument_keys, strict=False)
    missing = [s for s in cfg.symbols if s not in keys]
    if missing:
        log.warning("Skipping symbols Upstox doesn't know (update swing/universe.py): %s", missing)
    hist = {}
    for s, k in keys.items():
        hist[s] = api.daily_candles(k, 420)
        time.sleep(0.1)
    live_bars = api.today_ohlc(list(keys.values()))
    for s, k in keys.items():
        if k in live_bars:
            hist[s].append(live_bars[k])
    idx = {s: len(hist[s]) - 1 for s, k in keys.items() if k in live_bars and len(hist[s]) > 210}
    prices = {s: hist[s][-1].close for s in hist if hist[s]}
    inds = {s: compute(hist[s]) for s in idx}

    # 1) Exits: target / stop (held by Upstox in live mode) and the 20-day time stop.
    held = api.holdings() if cfg.live else {}
    for s in list(state["positions"]):
        pos, key = state["positions"][s], keys.get(s)
        if pos["entry_date"] == today_str or key is None:
            continue
        if cfg.live:
            fired = exit_fill(api, pos["gtt_id"]) if pos.get("gtt_id") else None
            if fired:
                pnl = close_position(state, s, fired[0], fired[1], today_str, cfg)
                log.info("%s EXIT %s %d @ ₹%.2f (%s) P&L ₹%.0f", mode, s, pos["qty"], fired[0], fired[1], pnl)
            elif held.get(key, 0) <= 0:
                log.warning("%s no longer held but its GTT didn't report a fill; recording exit at ₹%.2f",
                            s, prices[s])
                close_position(state, s, prices[s], "closed_outside_bot", today_str, cfg)
            elif pos["bars"] + 1 >= cfg.max_hold_days and can_place_orders():
                qty, avg = sell_now(api, cfg, key, pos, prices[s])
                if qty:
                    pnl = close_position(state, s, avg, "time", today_str, cfg)
                    log.info("%s EXIT %s %d @ ₹%.2f (time) P&L ₹%.0f", mode, s, qty, avg, pnl)
        elif s in idx:
            ex = check_exit(pos, hist[s][-1], cfg)
            if ex:
                pnl = close_position(state, s, ex[0], ex[1], today_str, cfg)
                log.info("%s EXIT %s %d @ ₹%.2f (%s) P&L ₹%.0f", mode, s, pos["qty"], ex[0], ex[1], pnl)
        save_state(cfg, state)

    # 2) Floors.
    eq = equity(state, prices)
    fa = floor_action(state, eq, cfg)
    if fa == "hard":
        for s in list(state["positions"]):
            pos = state["positions"][s]
            if cfg.live:
                qty, avg = sell_now(api, cfg, keys[s], pos, prices[s])
                if not qty:
                    log.error("Hard-floor sell of %s did not fill; its stop-loss is no longer active!", s)
                    continue
                price = avg
            else:
                price = prices[s]
            close_position(state, s, price, "hard_floor", today_str, cfg)
            log.info("%s EXIT %s @ ₹%.2f (hard floor)", mode, s, price)
    if fa and not state["halted"]:
        halt(cfg, state, f"{fa} floor: equity ₹{eq:.0f}")

    # 3) Entries.
    if cfg.live and not state["halted"]:
        cash = api.available_cash()
        if cash < state["cash"]:
            log.warning("Broker cash ₹%.0f < swing ledger cash ₹%.0f, capping", cash, state["cash"])
            state["cash"] = cash
    if state["pause_left"] > 0:
        log.info("Cooling off after a losing streak: %d more day(s) without new entries.", state["pause_left"])
    for e in pick_entries(state, inds, idx, prices, eq, cfg):
        s = e["symbol"]
        if cfg.live and not can_place_orders():
            log.error("Past the 3:10 PM order cutoff; skipping new entries today.")
            break
        log.info("%s BUY %d %s @ ~₹%.2f  stop ₹%.2f  target ₹%.2f  (risk ₹%.0f)", mode, e["qty"], s,
                 e["price"], e["stop"], e["target"], e["qty"] * (e["price"] - e["stop"]))
        if cfg.live:
            try:
                gtt_id = api.place_bracket_gtt(keys[s], e["qty"], e["stop"], e["target"])
            except UpstoxError as err:
                log.error("Order failed for %s: %s", s, err)
                continue
            qty, avg = wait_for_fill(api, gtt_id)
            if not qty:
                log.error("%s entry did not fill; cancelling its GTT.", s)
                try:
                    api.cancel_gtt(gtt_id)
                except UpstoxError:
                    pass
                continue
            log.info("  filled %d @ ₹%.2f; stop and target are now held by Upstox", qty, avg)
            open_position(state, s, qty, avg, e["stop"], e["target"], today_str, cfg, gtt_id)
        else:
            open_position(state, s, e["qty"], e["price"], e["stop"], e["target"], today_str, cfg)
        save_state(cfg, state)

    end_of_day(state)
    state["last_run"] = today_str
    save_state(cfg, state)
    log.info("Cash ₹%.0f | open %s | equity ₹%.0f (soft floor ₹%.0f, hard ₹%.0f) | closed trades %d",
             state["cash"], {s: p["qty"] for s, p in state["positions"].items()}, equity(state, prices),
             cfg.soft_floor, cfg.hard_floor, len(state["trades"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
