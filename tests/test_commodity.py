"""Commodity bot tests (run from the repo root): rules, AI failure handling, full evenings with fakes."""
import json
import os
import sys
import tempfile
import types
import unittest
from datetime import datetime, timedelta
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from commodity import engine  # noqa: E402
from commodity.config import CommodityConfig  # noqa: E402
from market_hours import IST  # noqa: E402

DOWN = {"direction": "DOWN", "confidence": 70, "entry_trigger": 8750, "stop_level": 8960,
        "target_level": 8400, "event_risk": False, "reasoning": "test", "key_risks": []}


class RuleTests(unittest.TestCase):
    def setUp(self):
        self.cfg = CommodityConfig()

    def test_good_plan_passes(self):
        self.assertEqual(engine.validate(DOWN, 8800, 380, 8820, self.cfg), (True, "ok"))

    def test_low_confidence_rejected(self):
        self.assertFalse(engine.validate(dict(DOWN, confidence=60), 8800, 380, 8820, self.cfg)[0])

    def test_min_confidence_cannot_go_below_60(self):
        with mock.patch.dict(os.environ, {"COMMODITY_MIN_CONFIDENCE": "20"}):
            self.assertEqual(CommodityConfig().min_confidence, 60)

    def test_event_risk_rejected(self):
        self.assertFalse(engine.validate(dict(DOWN, event_risk=True), 8800, 380, 8820, self.cfg)[0])

    def test_poor_reward_risk_rejected(self):
        self.assertFalse(engine.validate(dict(DOWN, target_level=8600), 8800, 380, 8820, self.cfg)[0])

    def test_inconsistent_levels_rejected(self):
        self.assertFalse(engine.validate(dict(DOWN, stop_level=8500), 8800, 380, 8820, self.cfg)[0])

    def test_price_data_must_agree(self):
        ok, why = engine.validate(DOWN, 8800, 380, 8700, self.cfg)  # price above today's avg
        self.assertFalse(ok)
        self.assertIn("disagrees", why)

    def test_no_trade_passthrough(self):
        self.assertFalse(engine.validate({"direction": "NO_TRADE"}, 8800, 380, 8820, self.cfg)[0])

    def test_spread_rule(self):
        self.assertTrue(engine.spread_ok(99, 101, self.cfg)[0])
        self.assertFalse(engine.spread_ok(95, 105, self.cfg)[0])
        self.assertFalse(engine.spread_ok(0, 101, self.cfg)[0])

    def test_premium_stop_capped_at_40pct(self):
        stop, target = engine.premium_levels(100, dict(DOWN, stop_level=9500), self.cfg)
        self.assertEqual(stop, 60.0)

    def test_one_trade_per_day_and_loss_limit_and_streak(self):
        s = engine.new_state()
        s["last_trade_date"] = "2026-10-05"
        self.assertFalse(engine.can_trade(s, "2026-10-05", self.cfg)[0])
        s = engine.new_state()
        for _ in range(3):
            engine.record(s, "d", -1000, {}, self.cfg)
        self.assertEqual(s["pause_left"], self.cfg.pause_days)
        s = engine.new_state()
        engine.record(s, "d", -self.cfg.max_total_loss, {}, self.cfg)
        self.assertTrue(s["halted"])


class FakeResp:
    def __init__(self, text, stop="end_turn"):
        self.content = [types.SimpleNamespace(type="text", text=text)]
        self.stop_reason = stop


class AITests(unittest.TestCase):
    def analyst(self, resp):
        from commodity.ai import Analyst
        client = types.SimpleNamespace(messages=types.SimpleNamespace(create=lambda **k: resp))
        return Analyst(CommodityConfig(), client=client)

    def test_valid_json(self):
        d, _ = self.analyst(FakeResp(json.dumps(DOWN))).decide("CRUDEOIL", {}, "F", 8800, "", "now")
        self.assertEqual(d["direction"], "DOWN")

    def test_refusal_is_no_trade(self):
        d, _ = self.analyst(FakeResp("", "refusal")).decide("CRUDEOIL", {}, "F", 8800, "", "now")
        self.assertEqual(d["direction"], "NO_TRADE")

    def test_garbage_is_no_trade(self):
        d, _ = self.analyst(FakeResp("I think it goes down")).decide("CRUDEOIL", {}, "F", 8800, "", "now")
        self.assertEqual(d["direction"], "NO_TRADE")


class Clock:
    def __init__(self, start):
        self.t = start

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += timedelta(seconds=s)


class BotTests(unittest.TestCase):
    """A full evening: entry triggers, price falls to target, exit."""

    def run_evening(self, live, path, decision=DOWN, cap="40000", gap=0.01):
        import commodity.bot as bot
        tmp = tempfile.mkdtemp()
        env = {"COMMODITY_LIVE": "1" if live else "0", "COMMODITY_MAX_PREMIUM": cap}
        exp = int(datetime(2026, 10, 19, tzinfo=IST).timestamp() * 1000)
        rows = [{"instrument_type": "FUT", "trading_symbol": "CRUDEOIL FUT 19 OCT 26", "instrument_key": "F",
                 "expiry": exp}] + [
            {"instrument_type": t, "trading_symbol": f"CRUDEOIL {k} {t} 19 OCT 26", "instrument_key": f"{t}{k}",
             "expiry": exp, "strike_price": k, "lot_size": 100} for k in range(8500, 9100, 50) for t in ("CE", "PE")]
        clock = Clock(datetime(2026, 10, 5, 18, 30, tzinfo=IST))
        it = iter(path)
        state = {"fut": 8800.0}
        orders = []

        class Up:
            def ltp(self, keys):
                k = keys[0]
                if k == "F":
                    state["fut"] = next(it, state["fut"])
                    return {k: state["fut"]}
                return {k: max(1.0, 280 + 0.5 * (8800 - state["fut"]))}

            def place_custom_order(self, key, side, qty, otype, price, trig=0.0, product="I", tag=""):
                orders.append((side, qty, otype, round(price, 1), trig))
                return f"o{len(orders)}"

            def order_details(self, oid):
                n = int(oid[1:])
                kind = orders[n - 1][2]
                return {"status": "trigger pending"} if kind == "SL" else \
                    {"status": "complete", "average_price": orders[n - 1][3], "filled_quantity": 100}

            def cancel_order(self, oid):
                orders.append(("CANCEL", oid, "", 0, 0))

            def best_bid_ask(self, key):
                mid = self.ltp([key])[key]
                return mid * (1 - gap / 2), mid * (1 + gap / 2)

        analyst = types.SimpleNamespace(news_brief=lambda c: ("news", []),
                                        decide=lambda *a: (decision, ""))
        tech = {"price": 8800, "atr14": 380, "today": {"avg": 8820}}
        notes = []
        with mock.patch.dict(os.environ, env), \
                mock.patch("commodity.config.HERE", tmp), \
                mock.patch.object(bot.market, "load_rows", lambda ex, name: rows), \
                mock.patch.object(bot.market, "technicals", lambda up, fut: tech), \
                mock.patch.object(bot, "now", clock.now), \
                mock.patch.object(bot.time, "sleep", lambda s: None), \
                mock.patch.object(sys, "argv", ["bot"]):
            cfg = CommodityConfig(state_file=os.path.join(tmp, "s.json"), decisions_file=os.path.join(tmp, "d.jsonl"),
                                  log_file=os.path.join(tmp, "l.log"), halt_file=os.path.join(tmp, "H"))
            with mock.patch.object(bot, "CommodityConfig", lambda: cfg):
                rc = bot.main(notify=notes.append, analyst=analyst, up=Up(), sleep=clock.sleep)
            st = {}
            if os.path.exists(cfg.state_file):
                with open(cfg.state_file) as f:
                    st = json.load(f)
        return rc, orders, notes, st

    def test_paper_evening_hits_target(self):
        rc, orders, notes, st = self.run_evening(False, [8800, 8780, 8745, 8700, 8600, 8500, 8390])
        self.assertEqual(rc, 0)
        self.assertEqual(orders, [])  # paper: no real orders
        self.assertEqual(st["trades"][0]["reason"], "target")
        self.assertGreater(st["trades"][0]["pnl"], 0)

    def test_live_evening_orders_in_safe_order(self):
        rc, orders, notes, st = self.run_evening(True, [8800, 8745, 8700, 8390])
        kinds = [(o[0], o[2]) for o in orders]
        self.assertEqual(kinds[0], ("BUY", "LIMIT"))
        self.assertEqual(kinds[1], ("SELL", "SL"))          # stop placed right after the fill
        self.assertEqual(kinds[2][0], "CANCEL")              # stop cancelled before exiting
        self.assertEqual(kinds[3], ("SELL", "LIMIT"))
        self.assertTrue(all(o[1] == 1 for o in orders if o[0] in ("BUY", "SELL")))  # always 1 lot

    def test_stop_hit(self):
        rc, orders, notes, st = self.run_evening(False, [8800, 8745, 8800, 8970])
        self.assertEqual(st["trades"][0]["reason"], "stop")
        self.assertLess(st["trades"][0]["pnl"], 0)

    def test_entry_never_triggers(self):
        rc, orders, notes, st = self.run_evening(True, [8800] * 2000)
        self.assertEqual(orders, [])
        self.assertTrue(any("never triggered" in n for n in notes))

    def test_over_cap_no_trade(self):
        rc, orders, notes, st = self.run_evening(True, [8800, 8745], cap="5000")
        self.assertEqual(orders, [])
        self.assertTrue(any("> cap" in n for n in notes))

    def test_wide_spread_no_trade(self):
        rc, orders, notes, st = self.run_evening(True, [8800, 8745], gap=0.08)
        self.assertEqual(orders, [])
        self.assertTrue(any("bid/ask gap" in n for n in notes))

    def test_no_quotes_no_trade(self):
        rc, orders, notes, st = self.run_evening(True, [8800, 8745], gap=2.0)  # bid would be <= 0
        self.assertEqual(orders, [])

    def test_live_requires_cap(self):
        rc, orders, notes, st = self.run_evening(True, [8800], cap="0")
        self.assertEqual(rc, 1)
        self.assertEqual(orders, [])


if __name__ == "__main__":
    unittest.main()
