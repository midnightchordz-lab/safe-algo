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
    """Rules shared by both strategies, exercised with the trend strategy."""

    def setUp(self):
        self.cfg = Config()
        self.cfg.live = False
        self.cfg.strategy = "trend"

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
        self.cfg.floor_action = "liquidate"
        state = new_state(self.cfg)
        apply_fill(state, "NIFTYBEES", "BUY", 40, 200.0, self.cfg)  # ₹8000 in
        crashed = rising()[:-1] + [150.0]                          # value ~7,970: under floor
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


class RebalanceTests(unittest.TestCase):
    def setUp(self):
        self.cfg = Config()
        self.cfg.strategy = "rebalance"
        self.flat = [100.0] * 150

    def test_initial_buy_is_equal_weight_within_budget(self):
        state = new_state(self.cfg)
        orders = decide(state, {s: self.flat for s in self.cfg.symbols}, self.cfg)
        self.assertEqual([o["side"] for o in orders], ["BUY", "BUY"])
        self.assertEqual(orders[0]["qty"], orders[1]["qty"])
        spent = sum(o["qty"] * 100 for o in orders)
        self.assertLessEqual(spent, self.cfg.budget * (1 - self.cfg.cash_buffer_pct))

    def _hold_equal(self, price_a=100.0, price_b=100.0):
        state = new_state(self.cfg)
        closes = {"NIFTYBEES": self.flat, "GOLDBEES": self.flat}
        for o in decide(state, closes, self.cfg):
            apply_fill(state, o["symbol"], o["side"], o["qty"], 100.0, self.cfg)
        return state, {"NIFTYBEES": self.flat + [price_a], "GOLDBEES": self.flat + [price_b]}

    def test_no_trades_inside_band(self):
        state, closes = self._hold_equal(110.0, 95.0)
        self.assertEqual(decide(state, closes, self.cfg), [])

    def test_rebalances_when_drifted(self):
        state, closes = self._hold_equal(160.0, 90.0)
        orders = decide(state, closes, self.cfg)
        self.assertEqual((orders[0]["symbol"], orders[0]["side"]), ("NIFTYBEES", "SELL"))
        self.assertEqual((orders[1]["symbol"], orders[1]["side"]), ("GOLDBEES", "BUY"))

    def test_floor_freezes_without_selling(self):
        state, closes = self._hold_equal(75.0, 75.0)  # value ~7,600: below 8,500, above 7,000
        orders = decide(state, closes, self.cfg)
        self.assertEqual([o["side"] for o in orders], ["HALT"])

    def test_hard_floor_sells_even_when_halted(self):
        state, closes = self._hold_equal(60.0, 60.0)  # value ~6,200: below the hard floor
        state["halted"] = True
        orders = decide(state, closes, self.cfg)
        self.assertEqual(sorted(o["side"] for o in orders), ["SELL", "SELL"])

    def test_losses_capped_near_hard_floor_in_stress_tests(self):
        for kind in ("bull", "sideways", "bear"):
            for seed in range(10):
                series = {s: synthetic(kind, seed=seed * 7 + i) for i, s in enumerate(self.cfg.symbols)}
                r = run(series, self.cfg)
                self.assertGreater(r["min"], self.cfg.hard_floor * 0.93, (kind, seed))


if __name__ == "__main__":
    unittest.main()
