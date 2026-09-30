"""Safety tests: run with `python -m unittest discover -s tests` from trading_algo/."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest import run, synthetic  # noqa: E402
from config import Config  # noqa: E402
from engine import apply_fill, decide, new_state  # noqa: E402


def rising(n=200, start=100.0, step=0.3):
    return [start + i * step for i in range(n)]


class SafetyTests(unittest.TestCase):
    def setUp(self):
        self.cfg = Config()
        self.cfg.live = False

    def test_never_spends_more_than_budget(self):
        state = new_state(self.cfg)
        orders = decide(state, {s: rising() for s in self.cfg.symbols}, self.cfg)
        spent = sum(o["qty"] * rising()[-1] for o in orders if o["side"] == "BUY")
        self.assertGreater(spent, 0)
        self.assertLessEqual(spent, self.cfg.budget * (1 - self.cfg.cash_buffer_pct))

    def test_only_buy_and_sell_of_held_qty(self):
        state = new_state(self.cfg)
        apply_fill(state, "NIFTYBEES", "BUY", 10, 100.0, self.cfg)
        falling = [200 - i * 0.5 for i in range(200)]
        orders = decide(state, {"NIFTYBEES": falling, "GOLDBEES": falling}, self.cfg)
        sells = [o for o in orders if o["side"] == "SELL"]
        self.assertEqual(sells[0]["qty"], 10)
        self.assertFalse(any(o["side"] == "BUY" for o in orders))

    def test_ledger_never_goes_short(self):
        state = new_state(self.cfg)
        apply_fill(state, "NIFTYBEES", "BUY", 5, 100.0, self.cfg)
        apply_fill(state, "NIFTYBEES", "SELL", 50, 100.0, self.cfg)
        self.assertNotIn("NIFTYBEES", state["positions"])
        self.assertGreater(state["cash"], 0)

    def test_floor_triggers_liquidation_and_halt(self):
        state = new_state(self.cfg)
        apply_fill(state, "NIFTYBEES", "BUY", 40, 200.0, self.cfg)  # ₹8000 in
        crashed = rising()[:-1] + [100.0]                          # halves overnight
        orders = decide(state, {"NIFTYBEES": crashed, "GOLDBEES": rising()}, self.cfg)
        self.assertEqual(orders[0], {"symbol": "NIFTYBEES", "side": "SELL", "qty": 40,
                                     "reason": "capital_floor"})
        self.assertEqual(orders[-1]["side"], "HALT")

    def test_halted_state_does_nothing(self):
        state = new_state(self.cfg)
        state["halted"] = True
        self.assertEqual(decide(state, {s: rising() for s in self.cfg.symbols}, self.cfg), [])

    def test_equity_never_reaches_zero_in_stress_tests(self):
        for kind in ("bull", "sideways", "bear", "crash"):
            for seed in range(10):
                series = {s: synthetic(kind, seed=seed * 7 + i) for i, s in enumerate(self.cfg.symbols)}
                r = run(series, self.cfg)
                self.assertGreater(r["min"], self.cfg.budget * 0.5, (kind, seed))
                if kind != "crash":
                    self.assertFalse(r["halted"], (kind, seed))


if __name__ == "__main__":
    unittest.main()
