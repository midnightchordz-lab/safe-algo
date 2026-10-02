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


NO_TRADE_PLANS = {"direction": "NO_TRADE", "confidence": 52, "entry_trigger": 0, "stop_level": 0,
                  "target_level": 0, "event_risk": False, "reasoning": "signals conflict", "key_risks": [],
                  "up_trigger": 8858, "up_stop": 8750, "up_target": 9030, "up_confidence": 66,
                  "down_trigger": 8770, "down_stop": 8880, "down_target": 8600, "down_confidence": 70}


class RuleTests(unittest.TestCase):
    def setUp(self):
        self.cfg = CommodityConfig()

    def test_good_plan_passes(self):
        self.assertEqual(engine.validate(DOWN, 8800, 380, 8820, self.cfg), (True, "ok"))

    def test_low_confidence_rejected(self):
        self.assertFalse(engine.validate(dict(DOWN, confidence=49), 8800, 380, 8820, self.cfg)[0])

    def test_edge_rule(self):
        # 54% to win 160 vs lose 95: edge 86.4 - 43.7 = 42.7 pts >= 0.25 x 95.
        put = dict(DOWN, confidence=54, entry_trigger=8760, stop_level=8855, target_level=8600)
        self.assertEqual(engine.validate(put, 8800, 380, 8820, self.cfg), (True, "ok"))
        # A stricter edge setting blocks the same plan, and reports why.
        with mock.patch.dict(os.environ, {"COMMODITY_MIN_EDGE": "0.5"}):
            ok, why = engine.validate(put, 8800, 380, 8820, CommodityConfig())
        self.assertFalse(ok)
        self.assertIn("edge 43 pts < 0.5", why)

    def test_thresholds_cannot_be_loosened(self):
        with mock.patch.dict(os.environ, {"COMMODITY_MIN_CONFIDENCE": "20", "COMMODITY_MIN_EDGE": "0"}):
            cfg = CommodityConfig()
        self.assertEqual((cfg.min_confidence, cfg.min_edge), (50, 0.25))

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

    def test_plans_from_decision(self):
        ps = engine.plans(NO_TRADE_PLANS, 8800)
        self.assertEqual([(p["direction"], p["entry_trigger"], p["confidence"]) for p in ps],
                         [("UP", 8858, 66), ("DOWN", 8770, 70)])
        self.assertEqual(engine.describe(ps[1]), "PUT below 8770 (stop 8880, target 8600, 70%)")
        self.assertEqual(len(engine.plans(NO_TRADE_PLANS, 8860)), 1)               # call trigger already crossed
        self.assertEqual(engine.plans(dict(NO_TRADE_PLANS, up_trigger=0, down_stop=0), 8800), [])  # incomplete

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

    def run_evening(self, live, path, decision=DOWN, cap="40000", gap=0.01, tmp=None):
        import commodity.bot as bot
        tmp = tmp or tempfile.mkdtemp()
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

        self.calls = []

        def decide(*a):
            self.calls.append(a)
            return decision, ""

        analyst = types.SimpleNamespace(news_brief=lambda c: ("news", []), decide=decide)
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

    def test_failed_ai_call_arms_nothing(self):
        failed = {"direction": "NO_TRADE", "confidence": 0, "entry_trigger": 0, "stop_level": 0,
                  "target_level": 0, "event_risk": True, "reasoning": "AI call failed", "key_risks": []}
        rc, orders, notes, st = self.run_evening(True, [8800] * 50, decision=failed)
        self.assertEqual(orders, [])
        self.assertTrue(any("No conditional plan passed" in n for n in notes))

    def test_plan_failing_rules_not_armed(self):
        weak = dict(NO_TRADE_PLANS, up_confidence=45, down_target=8700)  # low confidence / poor reward
        rc, orders, notes, st = self.run_evening(True, [8800, 8700, 8700, 8700], decision=weak)
        self.assertEqual(orders, [])
        self.assertTrue(any("No conditional plan passed" in n for n in notes))

    def test_armed_no_breakout(self):
        with self.assertLogs("commodity", "INFO") as logs:
            rc, orders, notes, st = self.run_evening(True, [8800] * 2000, decision=NO_TRADE_PLANS)
        self.assertEqual((orders, len(self.calls)), ([], 1))
        self.assertTrue(any("Armed until 21:30: CALL above 8858 (stop 8750, target 9030, 66%) / "
                            "PUT below 8770 (stop 8880, target 8600, 70%)" in n for n in notes))
        self.assertTrue(any("no plan triggered by 21:30" in n for n in notes))
        beats = [l for l in logs.output if "crude 8,800.0, armed:" in l]
        self.assertEqual(len(beats), 36)  # 18:30-21:30: one line every 5 minutes, not every 30 seconds

    def test_single_poke_does_not_trigger(self):
        rc, orders, notes, st = self.run_evening(True, [8800, 8765] + [8800, 8765] * 500, decision=NO_TRADE_PLANS)
        self.assertEqual(orders, [])

    def test_held_break_executes_plan_without_asking_again(self):
        rc, orders, notes, st = self.run_evening(True, [8800, 8790, 8765, 8760, 8750, 8700, 8590],
                                                 decision=NO_TRADE_PLANS)
        self.assertEqual(len(self.calls), 1)                           # one AI decision all night
        self.assertTrue(any("executing pre-committed plan PUT below 8770" in n for n in notes))
        self.assertEqual((orders[0][0], orders[0][2]), ("BUY", "LIMIT"))
        self.assertEqual((orders[1][0], orders[1][2]), ("SELL", "SL"))
        self.assertTrue(all(o[1] == 1 for o in orders if o[0] in ("BUY", "SELL")))
        self.assertEqual(st["trades"][0]["reason"], "target")

    def test_triggered_plan_still_needs_price_rule(self):
        # The call triggers, but price is below today's average (8820 in the fake data): blocked.
        rc, orders, notes, st = self.run_evening(True, [8800, 8860, 8865] + [8865] * 50,
                                                 decision=dict(NO_TRADE_PLANS, down_trigger=0))
        self.assertEqual(orders, [])
        self.assertTrue(any("rule blocks it now" in n and "disagrees" in n for n in notes))

    def test_restart_reuses_saved_plan(self):
        tmp = tempfile.mkdtemp()
        self.run_evening(True, [8800] * 2000, decision=NO_TRADE_PLANS, tmp=tmp)
        self.assertEqual(len(self.calls), 1)
        rc, orders, notes, st = self.run_evening(True, [8800] * 2000, decision=DOWN, tmp=tmp)
        self.assertEqual(len(self.calls), 0)                   # no new AI call on the restart
        self.assertTrue(any("Armed until 21:30: CALL above 8858" in n for n in notes))  # same levels as before
        self.assertEqual(st["plan"]["decision"]["down_trigger"], 8770)

    def test_live_requires_cap(self):
        rc, orders, notes, st = self.run_evening(True, [8800], cap="0")
        self.assertEqual(rc, 1)
        self.assertEqual(orders, [])


class ScoreTests(unittest.TestCase):
    """Replaying plans against 15-minute bars."""

    def setUp(self):
        from commodity import score
        self.score, self.cfg = score, CommodityConfig()
        self.put = {"direction": "DOWN", "confidence": 54, "entry_trigger": 8760, "stop_level": 8855,
                    "target_level": 8600}

    def bars(self, *hlc):
        t = datetime(2026, 10, 1, 18, 30)
        out = []
        for i, (h, lo) in enumerate(hlc):
            out.append(((t + timedelta(minutes=15 * i)).strftime("%H:%M"), (h + lo) / 2, h, lo, (h + lo) / 2))
        return out

    def test_target_after_trigger(self):
        r = self.score.simulate(self.put, self.bars((8800, 8770), (8780, 8750), (8700, 8590)), "18:30", self.cfg)
        self.assertEqual((r["result"], r["points"]), ("target", 160))

    def test_stop_after_trigger(self):
        r = self.score.simulate(self.put, self.bars((8780, 8750), (8860, 8800)), "18:30", self.cfg)
        self.assertEqual((r["result"], r["points"]), ("stop", -95))

    def test_both_in_one_bar_counts_as_stop(self):
        r = self.score.simulate(self.put, self.bars((8780, 8750), (8870, 8590)), "18:30", self.cfg)
        self.assertEqual(r["result"], "stop")

    def test_bars_before_decision_ignored_and_no_trigger(self):
        r = self.score.simulate(self.put, self.bars((8780, 8700), (8800, 8770), (8800, 8770)), "18:50", self.cfg)
        self.assertEqual(r["result"], "no trigger")

    def test_time_exit(self):
        bars = self.bars((8780, 8750), *[(8770, 8740)] * 20)   # runs past 22:45 without stop/target
        r = self.score.simulate(self.put, bars, "18:30", self.cfg)
        self.assertEqual(r["result"], "time")

    def test_summary_numbers(self):
        rs = [{"result": "target", "points": 160}, {"result": "stop", "points": -95},
              {"result": "no trigger", "points": 0}]
        s = self.score.summarise(rs)
        self.assertEqual((s["plans"], s["triggered"], s["wins"], s["losses"]), (3, 2, 1, 1))
        self.assertEqual((s["avg_win"], s["avg_loss"], s["total"]), (7880, -4870, 3010))
        self.assertEqual(s["profit_factor"], 1.62)

    def test_main_dedupes_restarts_and_skips_tonight(self):
        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, "d.jsonl")
        entry = {"time": "2026-10-01T20:09:10+05:30", "trade_key": "F", "trade_future": 8851,
                 "tech": {"atr14": 380}, "passed_rules": False, "decision": NO_TRADE_PLANS}
        with open(path, "w") as f:
            for e in (entry, dict(entry, time="2026-10-01T20:20:00+05:30"),
                      dict(entry, time=datetime.now(IST).date().isoformat() + "T19:00:00+05:30")):
                f.write(json.dumps(e) + "\n")
        bars = [[f"2026-10-01T{h}:00+05:30", 8800, 8865, 8790, 8860, 0, 0] for h in ("20", "21")]
        up = types.SimpleNamespace(_req=lambda *a, **k: {"candles": bars})
        out = []
        with mock.patch.object(self.score, "CommodityConfig", lambda: CommodityConfig(decisions_file=path)):
            self.score.main(up=up, out=out.append)
        plan_lines = [l for l in out if l.startswith("2026-10-01")]
        self.assertEqual(len(plan_lines), 2)       # call + put once each, despite the restart
        self.assertTrue(any("CALL above 8858" in l and "[armed]" in l for l in plan_lines))
        self.assertTrue(any(l.startswith("ARMED only:") for l in out))


if __name__ == "__main__":
    unittest.main()
