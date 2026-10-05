"""Swing bot guardrail tests: `python3 -m unittest discover -s tests` from the repo root."""
import os
import sys
import unittest
from dataclasses import replace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from swing.config import SwingConfig  # noqa: E402
from swing.engine import (check_exit, close_position, floor_action, new_state,  # noqa: E402
                          open_position, pick_entries, size)
from swing.indicators import compute  # noqa: E402
from upstox_api import Candle  # noqa: E402


def bar(o, h, l, c):
    return Candle("2026-01-01", o, h, l, c, 0)


def uptrend(n=260, start=100.0, step=0.25, wiggle=1.5):
    """Steady uptrend closing at a fresh 20-day high on the last bar (a breakout)."""
    out = []
    for i in range(n):
        c = start + i * step + (wiggle if i % 2 else -wiggle)
        out.append(Candle(f"d{i:04d}", c - 0.5, c + 1.5, c - 1.5, c, 0))
    last = out[-1]
    out[-1] = last._replace(close=last.close + 5, high=last.close + 6)
    return out


class ExitTests(unittest.TestCase):
    def setUp(self):
        self.cfg = SwingConfig()
        self.pos = {"stop": 95.0, "target": 115.0, "bars": 0}

    def test_stop(self):
        self.assertEqual(check_exit(self.pos, bar(100, 101, 94, 96), self.cfg), (95.0, "stop"))

    def test_gap_down_fills_at_open_not_stop(self):
        self.assertEqual(check_exit(self.pos, bar(90, 92, 88, 91), self.cfg), (90, "stop_gap"))

    def test_target(self):
        self.assertEqual(check_exit(self.pos, bar(110, 116, 109, 114), self.cfg), (115.0, "target"))

    def test_both_touched_assumes_stop_first(self):
        self.assertEqual(check_exit(self.pos, bar(100, 120, 90, 110), self.cfg)[1], "stop")

    def test_time_stop(self):
        self.pos["bars"] = self.cfg.max_hold_days - 1
        self.assertEqual(check_exit(self.pos, bar(100, 101, 99, 100.5), self.cfg), (100.5, "time"))

    def test_hold_inside_range(self):
        self.assertIsNone(check_exit(self.pos, bar(100, 105, 97, 103), self.cfg))


class SizingTests(unittest.TestCase):
    def setUp(self):
        self.cfg = SwingConfig()

    def test_risk_capped_at_one_percent(self):
        qty = size(1000, 960, 25_000, 25_000, 0, self.cfg)
        self.assertGreater(qty, 0)
        self.assertLessEqual(qty * 40, 250)

    def test_position_capped_at_35_percent(self):
        qty = size(100, 99, 25_000, 25_000, 0, self.cfg)  # tiny stop would allow a huge position
        self.assertLessEqual(qty * 100, 25_000 * 0.35)

    def test_total_open_risk_capped(self):
        self.assertEqual(size(1000, 960, 25_000, 25_000, 700, self.cfg), 0)

    def test_skips_tiny_orders(self):
        self.assertEqual(size(5000, 4000, 25_000, 25_000, 0, self.cfg), 0)

    def test_never_spends_more_than_cash(self):
        self.assertEqual(size(1000, 960, 2_000, 25_000, 0, self.cfg), 0)


class GuardrailTests(unittest.TestCase):
    def setUp(self):
        self.cfg = replace(SwingConfig(), entry="breakout")  # test data is breakout-shaped

    def test_three_to_one_levels(self):
        ind = compute(uptrend())
        from swing.strategy import breakout
        stop, target, _ = breakout(ind, len(ind["close"]) - 1, self.cfg)
        price = ind["close"][-1]
        self.assertAlmostEqual(target - price, 3 * (price - stop), places=6)

    def test_losing_streak_pauses_entries(self):
        state = new_state(self.cfg)
        for i in range(self.cfg.losing_streak_pause):
            open_position(state, f"S{i}", 10, 100, 96, 112, "d", self.cfg)
            close_position(state, f"S{i}", 96, "stop", "d", self.cfg)
        self.assertEqual(state["pause_left"], self.cfg.pause_days)
        candles = uptrend()
        inds = {"X": compute(candles)}
        self.assertEqual(pick_entries(state, inds, {"X": len(candles) - 1}, {"X": candles[-1].close},
                                      24_000, self.cfg), [])

    def test_floors(self):
        self.assertIsNone(floor_action({}, 22_000, self.cfg))
        self.assertEqual(floor_action({}, 21_000, self.cfg), "soft")
        self.assertEqual(floor_action({}, 17_000, self.cfg), "hard")

    def test_entry_limits(self):
        cfg = replace(self.cfg, max_new_per_day=2)
        candles = uptrend()
        syms = ["A", "B", "C", "D"]
        inds = {s: compute(candles) for s in syms}
        idx = {s: len(candles) - 1 for s in syms}
        prices = {s: candles[-1].close for s in syms}
        entries = pick_entries(new_state(cfg), inds, idx, prices, 25_000, cfg)
        self.assertEqual(len(entries), 2)
        self.assertLessEqual(sum(e["qty"] * (e["price"] - e["stop"]) for e in entries), 25_000 * 0.03)

    def test_halted_never_enters(self):
        state = new_state(self.cfg)
        state["halted"] = True
        candles = uptrend()
        self.assertEqual(pick_entries(state, {"X": compute(candles)}, {"X": len(candles) - 1},
                                      {"X": candles[-1].close}, 25_000, self.cfg), [])


if __name__ == "__main__":
    unittest.main()


class ScreenTests(unittest.TestCase):
    """The daily screen line and the replay tool (logging only, no trading effect)."""

    def bars(self, closes):
        from upstox_api import Candle
        return [Candle(f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}", c, c * 1.01, c * 0.99, c, 0)
                for i, c in enumerate(closes)]

    def test_funnel_counts_and_signal_match_the_rule(self):
        from swing.config import SwingConfig
        from swing.indicators import compute
        from swing.strategy import diagnose, screen_summary, signal
        cfg = SwingConfig()
        up = [100 + i * 0.5 for i in range(260)]
        dip = up[:-2] + [up[-3] - 6, up[-3] + 1]          # dips below the 20-day, then closes back above
        flat = [100.0] * 260                              # not in an uptrend
        inds = {"A": compute(self.bars(dip)), "B": compute(self.bars(flat)), "C": compute(self.bars(up))}
        idx = {s: 259 for s in inds}
        self.assertEqual(diagnose(inds["B"], 259, cfg), "not in uptrend")
        self.assertEqual(diagnose(inds["C"], 259, cfg), "no dip")
        stage_a = diagnose(inds["A"], 259, cfg)
        self.assertEqual(stage_a == "SIGNAL", signal(inds["A"], 259, cfg) is not None)
        line, watch = screen_summary(inds, idx, cfg)
        self.assertIn("3 stocks | 2 in uptrend", line)

    def test_replay_lists_each_day(self):
        from swing.config import SwingConfig
        from swing.replay import replay
        up = [100 + i * 0.5 for i in range(260)]
        rows = replay({"A": self.bars(up), "NIFTYBEES": self.bars(up)}, SwingConfig(), 5)
        self.assertEqual(len(rows), 5)
        self.assertTrue(all("signal(s)" in line for _, line, _, _ in rows))
