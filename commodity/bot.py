"""Commodity option bot. Runs once each weekday evening (scheduled at 18:30 IST).

  python3 -m commodity.bot             # PAPER unless COMMODITY_LIVE=1
  python3 -m commodity.bot --resume    # clear a halt after reviewing it
  python3 -m commodity.bot --decide-only   # just show tonight's data + news + AI call, then stop

Flow: price data + live news (Claude) -> decision -> hard rules -> wait for the entry level ->
buy 1 lot of the at-the-money option -> exchange stop-loss -> watch every 30s -> exit at
target / stop / 22:45. Every decision is appended to commodity_decisions.jsonl for review.

If the first decision doesn't trade, the bot watches the breakout levels Claude named
(watch_above / watch_below) until 21:30. If one breaks, Claude is asked once more with fresh
price data, and that answer goes through the same hard rules. At most one re-check per night.
"""
import json
import logging
import os
import sys
import time
from datetime import datetime

from commodity import engine, market
from commodity.ai import Analyst
from commodity.config import CommodityConfig
from market_hours import IST
from upstox_api import Upstox, UpstoxError

log = logging.getLogger("commodity")
CHARGES = 120.0  # rough round-trip brokerage + taxes for one option lot
STATUS_SECONDS = 300  # print a "still watching" line this often


def now():
    return datetime.now(IST)


def load_state(cfg):
    if os.path.exists(cfg.state_file):
        with open(cfg.state_file) as f:
            return json.load(f)
    return engine.new_state()


def save_state(cfg, state):
    tmp = cfg.state_file + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, cfg.state_file)


def halt(cfg, state, reason, notify):
    state["halted"], state["halt_reason"] = True, reason
    with open(cfg.halt_file, "w") as f:
        f.write(f"{now().isoformat()} {reason}\n")
    notify(f"Commodity bot HALTED: {reason}. Run `python3 -m commodity.bot --resume` after review.")


class Broker:
    """Thin wrapper so paper and live share one code path."""

    def __init__(self, up, live):
        self.up, self.live = up, live

    def price(self, key):
        return self.up.ltp([key]).get(key)

    def wait(self, order_id, wait_s=30):
        deadline, d = time.time() + wait_s, {}
        while time.time() < deadline:
            d = self.up.order_details(order_id)
            if d.get("status") in ("complete", "rejected", "cancelled"):
                return d
            time.sleep(2)
        try:
            self.up.cancel_order(order_id)
        except UpstoxError:
            pass
        time.sleep(2)
        return self.up.order_details(order_id)

    def buy(self, key, lots, limit):
        if not self.live:
            return limit / 1.01
        d = self.wait(self.up.place_custom_order(key, "BUY", lots, "LIMIT", limit, product="I", tag="commodity-bot"))
        return float(d.get("average_price") or 0) if d.get("status") == "complete" else 0.0

    def place_stop(self, key, lots, trigger):
        if not self.live:
            return "paper-stop"
        return self.up.place_custom_order(key, "SELL", lots, "SL", round(trigger * 0.97, 1), trigger,
                                          product="I", tag="commodity-bot")

    def stop_fill(self, stop_id):
        """Average price if the exchange stop-loss already executed, else None."""
        if not self.live or not stop_id:
            return None
        d = self.up.order_details(stop_id)
        return float(d.get("average_price") or 0) if d.get("status") == "complete" else None

    def sell_now(self, key, lots, stop_id, price):
        """Cancel the stop, then sell; retries lower. Returns fill price or 0 if it never filled."""
        if not self.live:
            return price
        if stop_id:
            try:
                self.up.cancel_order(stop_id)
            except UpstoxError:
                pass
        filled = self.stop_fill(stop_id)  # the stop may have fired while we were cancelling
        if filled:
            return filled
        for cut in (0.99, 0.97, 0.94):
            px = self.price(key) or price
            d = self.wait(self.up.place_custom_order(key, "SELL", lots, "LIMIT", round(px * cut, 1),
                                                     product="I", tag="commodity-bot"))
            if d.get("status") == "complete":
                return float(d.get("average_price") or px)
        return 0.0


def main(notify=print, analyst=None, up=None, sleep=time.sleep):
    cfg = CommodityConfig()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler(cfg.log_file)])
    state = load_state(cfg)
    if "--resume" in sys.argv:
        state["halted"], state["halt_reason"] = False, ""
        save_state(cfg, state)
        if os.path.exists(cfg.halt_file):
            os.remove(cfg.halt_file)
        log.info("Commodity halt cleared.")
        return 0

    mode = "LIVE" if cfg.live else "PAPER"
    polls = {"n": 0}

    def status(text):
        """Log a short heartbeat every STATUS_SECONDS so a quiet Terminal is clearly still working."""
        polls["n"] += 1
        if polls["n"] % max(1, STATUS_SECONDS // cfg.poll_seconds) == 0:
            log.info("%s %s", now().strftime("%H:%M"), text)

    t0 = now()
    today = t0.date().isoformat()
    log.info("=== commodity run %s, mode %s ===", t0.isoformat(timespec="seconds"), mode)
    if t0.weekday() > 4:
        return 0
    if cfg.live and cfg.max_premium <= 0 and "--decide-only" not in sys.argv:
        notify("Commodity bot not started: set COMMODITY_MAX_PREMIUM (via install_mac.py --commodity-budget).")
        return 1
    engine.new_day(state, today)
    ok, why = engine.can_trade(state, today, cfg)
    if not ok:
        log.info("No trade today: %s", why)
        save_state(cfg, state)
        return 0

    up = up or Upstox(os.environ.get("UPSTOX_ACCESS_TOKEN", ""))
    broker = Broker(up, cfg.live)
    trade_rows = market.load_rows(cfg.trade_exchange, cfg.underlying)
    data_rows = trade_rows if cfg.data_exchange == cfg.trade_exchange else market.load_rows(cfg.data_exchange, cfg.underlying)
    trade_fut, data_fut = market.nearest_future(trade_rows), market.nearest_future(data_rows)
    if not trade_fut or not data_fut:
        notify(f"Commodity bot: no {cfg.underlying} futures found. No trade.")
        return 1
    fut_key = trade_fut["instrument_key"]
    fut_price = broker.price(fut_key)
    if not fut_price:
        log.warning("No live price for %s (market closed or holiday?). No trade.", trade_fut["trading_symbol"])
        return 0

    analyst = analyst or Analyst(cfg)
    news, sources = analyst.news_brief(cfg.underlying)

    def decide(px, update=""):
        t = market.technicals(up, data_fut)
        kw = {"update": update} if update else {}
        d, _ = analyst.decide(cfg.underlying, t, trade_fut["trading_symbol"], px, news,
                              now().strftime("%d-%b-%Y %H:%M"), **kw)
        good, note = engine.validate(d, t["price"], t["atr14"], (t.get("today") or {}).get("avg"), cfg)
        with open(cfg.decisions_file, "a") as f:
            f.write(json.dumps({"time": now().isoformat(), "mode": mode, "recheck": bool(update), "tech": t,
                                "trade_future": px, "news": news, "sources": sources, "decision": d,
                                "passed_rules": good, "rule_note": note}) + "\n")
        text = (f"AI{' re-check' if update else ''}: {d['direction']} ({d.get('confidence')}%) "
                f"entry {d.get('entry_trigger')} stop {d.get('stop_level')} target {d.get('target_level')}")
        log.info("%s | rules: %s", text, note)
        log.info("Reasoning: %s", d.get("reasoning", ""))
        return d, good, note, text, px, t

    decision, ok, why, summary, fut_price, tech = decide(fut_price)
    if "--decide-only" in sys.argv:
        print(f"\n--- NEWS BRIEF ---\n{news}\n\n--- DECISION ---\n{json.dumps(decision, indent=1)}\n"
              f"Hard rules: {'PASS' if ok else 'BLOCK'} ({why})")
        return 0
    if not ok:
        above, below = engine.watch_levels(decision, fut_price, tech["atr14"])
        end = cfg.entry_window[1]
        if not (above or below):
            notify(f"Commodity: no trade tonight. {summary}. Rule: {why}")
            save_state(cfg, state)
            return 0
        levels = " / ".join(x for x in (f"above {above:g}" if above else "", f"below {below:g}" if below else "") if x)
        notify(f"Commodity: no trade yet ({why}). Watching {levels} until {end}.")
        while True:
            fut_price = broker.price(fut_key) or fut_price
            way = engine.broke(fut_price, above, below)
            if way:
                break
            if now().strftime("%H:%M") >= end:
                notify(f"Commodity: no breakout ({levels}) by {end}. No trade tonight.")
                save_state(cfg, state)
                return 0
            status(f"crude {fut_price:,.1f}, watching {levels} until {end}")
            sleep(cfg.poll_seconds)
        level = above if way == "above" else below
        log.info("Futures %.2f broke %s %g; asking the AI again.", fut_price, way, level)
        decision, ok, why, summary, fut_price, tech = decide(fut_price,
            f"At {now().strftime('%H:%M')} the futures broke {way} {level:g} (now {fut_price}). "
            f"Your earlier call: {decision['direction']} ({decision.get('confidence')}%): "
            f"{decision.get('reasoning', '')}\nIs this a real move worth trading now, or a fake-out?")
        if not ok:
            notify(f"Commodity: broke {way} {level:g}, but re-check says no trade. {summary}. Rule: {why}")
            save_state(cfg, state)
            return 0

    # Wait for the entry level, giving up if the stop side breaks first or the window closes.
    side = decision["direction"]
    end = cfg.entry_window[1]
    while True:
        fut_price = broker.price(fut_key) or fut_price
        if engine.triggered(side, fut_price, decision["entry_trigger"]):
            break
        if engine.exit_reason(side, fut_price, 1, decision, 0, 1e18, "00:00", cfg) == "stop":
            notify(f"Commodity: stop level {decision['stop_level']} broke before entry. No trade.")
            return 0
        if now().strftime("%H:%M") >= end:
            notify(f"Commodity: entry {decision['entry_trigger']} never triggered by {end}. No trade.")
            return 0
        status(f"crude {fut_price:,.1f}, waiting for {side} entry {decision['entry_trigger']} until {end}")
        sleep(cfg.poll_seconds)

    opt = market.pick_option(trade_rows, side, fut_price, now(), cfg.min_days_to_expiry)
    if not opt:
        notify("Commodity: no suitable option contract. No trade.")
        return 0
    prem = broker.price(opt["instrument_key"])
    units = market.units_per_lot(opt)
    if not prem:
        notify(f"Commodity: no live premium for {opt['trading_symbol']}. No trade.")
        return 0
    try:
        bid, ask = up.best_bid_ask(opt["instrument_key"])
    except UpstoxError as e:
        notify(f"Commodity: couldn't read the order book for {opt['trading_symbol']} ({e}). No trade.")
        return 0
    ok, why = engine.spread_ok(bid, ask, cfg)
    log.info("%s %s", opt["trading_symbol"], why)
    if not ok:
        notify(f"Commodity: {opt['trading_symbol']} skipped, {why}. No trade.")
        return 0
    limit = round(max(prem * 1.01, ask), 1)  # at least the current ask, so the order can fill
    cost = limit * units * cfg.lots
    cap = cfg.max_premium if cfg.max_premium > 0 else float("inf")
    if cost > cap:
        notify(f"Commodity: {opt['trading_symbol']} costs ₹{cost:,.0f} > cap ₹{cap:,.0f}. No trade.")
        return 0

    log.info(f"{mode} BUY {cfg.lots} lot {opt['trading_symbol']} @ ~₹{prem:.2f}, limit ₹{limit:.2f} "
             f"(₹{cost:,.0f}) | futures {fut_price:.2f}")
    try:
        fill = broker.buy(opt["instrument_key"], cfg.lots, limit)
    except UpstoxError as e:
        notify(f"Commodity: buy rejected: {e}")
        return 1
    if not fill:
        notify(f"Commodity: buy of {opt['trading_symbol']} did not fill. No position.")
        return 0
    state["last_trade_date"] = today
    save_state(cfg, state)
    prem_stop, prem_target = engine.premium_levels(fill, decision, cfg)
    try:
        stop_id = broker.place_stop(opt["instrument_key"], cfg.lots, prem_stop)
    except UpstoxError as e:
        log.error("Stop-loss rejected (%s); exiting immediately rather than hold unprotected.", e)
        stop_id = None
    log.info("filled @ ₹%.2f | option stop ₹%.2f target ₹%.2f", fill, prem_stop, prem_target)
    notify(f"Commodity {mode}: bought {opt['trading_symbol']} @ ₹{fill:.2f}. Stop ₹{prem_stop}, target ₹{prem_target}.")

    reason, exit_px = (None, None) if stop_id else ("no_stop", None)
    while reason is None:
        sleep(cfg.poll_seconds)
        hit = broker.stop_fill(stop_id)
        if hit:
            reason, exit_px = "stop (exchange)", hit
            break
        fp = broker.price(fut_key) or fut_price
        op = broker.price(opt["instrument_key"]) or fill
        reason = engine.exit_reason(side, fp, op, decision, prem_stop, prem_target, now().strftime("%H:%M"), cfg)
        exit_px = op
        status(f"crude {fp:,.1f}, option ₹{op:.2f} (bought ₹{fill:.2f}, stop ₹{prem_stop}, target ₹{prem_target}), "
               f"P&L ~₹{(op - fill) * units * cfg.lots:,.0f}")
    if not reason.startswith("stop (exchange)"):
        exit_px = broker.sell_now(opt["instrument_key"], cfg.lots, stop_id, exit_px or fill)
        if not exit_px:
            notify(f"URGENT: could not exit {opt['trading_symbol']}. Close it in the Upstox app NOW. "
                   "Intraday positions are auto-squared off before close.")
            halt(cfg, state, "exit failed; manual check needed", notify)
            save_state(cfg, state)
            return 1

    pnl = (exit_px - fill) * units * cfg.lots - CHARGES
    engine.record(state, today, pnl, {"contract": opt["trading_symbol"], "side": side, "fill": fill,
                                      "exit": exit_px, "reason": reason,
                                      "confidence": decision.get("confidence")}, cfg)
    if state["halted"]:
        halt(cfg, state, state["halt_reason"], notify)
    save_state(cfg, state)
    log.info(f"{mode} EXIT {opt['trading_symbol']} @ ₹{exit_px:.2f} ({reason}) P&L ₹{pnl:,.0f} "
             f"| total ₹{state['total_pnl']:,.0f}")
    notify(f"Commodity {mode}: exited @ ₹{exit_px:.2f} ({reason}). P&L ₹{pnl:,.0f}. Total ₹{state['total_pnl']:,.0f}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
