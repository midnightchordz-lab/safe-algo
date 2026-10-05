"""Safety tests: run with `python -m unittest discover -s tests` from the repo root."""
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


class OrderConfirmationTests(unittest.TestCase):
    """5 Oct 2026: Upstox answered 'Order not found' right after accepting the orders, the bot
    logged a failure and its record missed shares it really bought. These pin the fix."""

    def setUp(self):
        from unittest import mock
        import upstox_api
        self.mock, self.mod = mock, upstox_api
        self.up = upstox_api.Upstox("token")
        self.up.place_order = lambda *a, **k: "OID1"
        self.not_found = upstox_api.UpstoxError(
            "GET https://api.upstox.com/v2/order/details -> HTTP 404: {'errorCode': 'UDAPI100010', "
            "'message': 'Order not found'}")

    def test_not_found_right_after_placing_is_retried(self):
        replies = iter([self.not_found, self.not_found,
                        {"status": "complete", "filled_quantity": 18, "average_price": 257.4}])

        def details(oid):
            r = next(replies)
            if isinstance(r, Exception):
                raise r
            return r
        self.up.order_details = details
        self.up.order_book = lambda: []
        with self.mock.patch("time.sleep"):
            self.assertEqual(self.up.execute("K", "BUY", 18, 258.6), (18, 257.4))

    def test_order_book_used_when_details_still_missing(self):
        def details(oid):
            raise self.not_found
        self.up.order_details = details
        self.up.order_book = lambda: [{"order_id": "OID1", "status": "complete", "filled_quantity": 39,
                                       "average_price": 121.7}]
        with self.mock.patch("time.sleep"):
            self.assertEqual(self.up.execute("K", "BUY", 39, 122.3), (39, 121.7))

    def test_other_errors_still_raise(self):
        def details(oid):
            raise self.mod.UpstoxError("HTTP 401 unauthorized")
        self.up.order_details = details
        with self.mock.patch("time.sleep"), self.assertRaises(self.mod.UpstoxError):
            self.up.execute("K", "BUY", 1, 10)

    def test_never_found_is_an_error_not_a_silent_zero(self):
        def details(oid):
            raise self.not_found
        self.up.order_details = details
        self.up.order_book = lambda: []
        self.up.cancel_order = lambda oid: None
        with self.mock.patch("time.sleep"), self.mock.patch("time.time", side_effect=[0, 0, 99, 99, 99]), \
                self.assertRaises(self.mod.UpstoxError):
            self.up.execute("K", "BUY", 1, 10)


class LedgerMismatchTests(unittest.TestCase):
    def setUp(self):
        import bot
        self.bot, self.cfg = bot, Config()
        self.keys = {"NIFTYBEES": "NSE_EQ|N", "GOLDBEES": "NSE_EQ|G"}

    def test_extra_broker_shares_are_reported_so_the_bot_does_not_buy_twice(self):
        state = new_state(self.cfg)                                  # record: no holdings
        extra = self.bot.reconcile(state, {"NSE_EQ|N": 18, "NSE_EQ|G": 39}, 50000.0, self.keys)
        self.assertEqual(extra, [("NIFTYBEES", 0, 18), ("GOLDBEES", 0, 39)])
        self.assertEqual(state["positions"], {})                     # nothing adopted silently

    def test_matching_record_reports_nothing(self):
        state = new_state(self.cfg)
        apply_fill(state, "NIFTYBEES", "BUY", 18, 257.4, self.cfg)
        self.assertEqual(self.bot.reconcile(state, {"NSE_EQ|N": 18}, 5000.0, self.keys), [])

    def test_adopt_holdings_records_the_real_fills(self):
        state = new_state(self.cfg)
        added = self.bot.adopt_holdings(state, {"NSE_EQ|N": (18, 257.4), "NSE_EQ|G": (39, 121.7),
                                                 "NSE_EQ|OTHER": (5, 100.0)}, self.keys, self.cfg)
        self.assertEqual([a[:2] for a in added], [("NIFTYBEES", 18), ("GOLDBEES", 39)])
        self.assertEqual({s: p["qty"] for s, p in state["positions"].items()}, {"NIFTYBEES": 18, "GOLDBEES": 39})
        spent = 18 * 257.4 + 39 * 121.7
        self.assertLess(state["cash"], self.cfg.budget - spent + 1)  # cash reduced by the cost (plus charges)
        self.assertGreater(state["cash"], self.cfg.budget - spent - 100)
        # With the real holdings on record, the next run doesn't buy again.
        self.assertEqual(decide(state, {"NIFTYBEES": [257.4] * 250, "GOLDBEES": [121.7] * 250}, self.cfg), [])
