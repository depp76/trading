"""tests/strategy/trend_following/test_signals.py — donchian_signal() rules and the
no-lookahead guarantee (trend_following.md 3, 5)."""
import unittest
from datetime import date

import polars as pl

from strategy.trend_following import TrendFollowingConfig, donchian_signal
from tests.strategy.trend_following.frames import make_frame as _frame




class TestDonchianSignal(unittest.TestCase):

    def setUp(self):
        self.cfg = TrendFollowingConfig(entry_n=3, exit_n=2)

    def test_channel_excludes_current_day(self):
        # highs 10,11,12 then a big day: upper on day 3 must be max(High[0..2]) = 13, not 24
        df = _frame([10, 11, 12, 23], highs=[11, 12, 13, 24], lows=[9, 10, 11, 22])
        out = donchian_signal(df, self.cfg)
        self.assertAlmostEqual(out["upper"][3], 13.0)
        self.assertTrue(out["entry"][3])
        self.assertEqual(out["position"][3], 1)

    def test_warmup_never_signals(self):
        df = _frame([1, 50, 100, 150, 200])
        out = donchian_signal(df, self.cfg)
        # first entry_n rows have no complete prior window
        for i in range(self.cfg.entry_n):
            self.assertIsNone(out["upper"][i])
            self.assertFalse(out["entry"][i])
            self.assertEqual(out["position"][i], 0)

    def test_exit_on_breakdown_below_prior_lows(self):
        # rise (enter), then fall below the lowest low of the prior exit_n=2 days (exit)
        closes = [10, 10, 10, 20, 21, 22, 5, 5]
        df = _frame(closes)
        out = donchian_signal(df, self.cfg)
        pos = out["position"].to_list()
        self.assertEqual(pos[3], 1)              # breakout at 20 > max(High)=11
        self.assertEqual(pos[5], 1)              # still long while rising
        self.assertEqual(pos[6], 0)              # 5 < min(Low of days 4,5) -> exit
        self.assertTrue(out["exit"][6])

    def test_long_only_single_position(self):
        # repeated breakouts while already long must not change position (no pyramiding);
        # exits while flat must not create shorts
        closes = [10, 10, 10, 20, 30, 40, 2, 1, 0.5]
        out = donchian_signal(_frame(closes), self.cfg)
        pos = out["position"].to_list()
        self.assertEqual(pos[3:6], [1, 1, 1])
        self.assertTrue(all(p in (0, 1) for p in pos))
        self.assertEqual(pos[6:], [0, 0, 0])

    def test_no_lookahead(self):
        # Truncating the future must not change any earlier row (upper/lower/entry/exit/position).
        import random
        rnd = random.Random(7)
        closes = [100.0]
        for _ in range(119):
            closes.append(max(1.0, closes[-1] * (1 + rnd.uniform(-0.05, 0.05))))
        df = _frame(closes)
        full = donchian_signal(df, self.cfg)
        for k in (5, 17, 40, 77, 119):
            part = donchian_signal(df.head(k), self.cfg)
            for col in ("upper", "lower", "entry", "exit", "position"):
                self.assertEqual(part[col].to_list(), full[col].head(k).to_list(), f"{col} differs at k={k}")

    def test_missing_columns_raise(self):
        with self.assertRaises(ValueError):
            donchian_signal(pl.DataFrame({"Date": [date(2025, 1, 1)], "Close": [1.0]}), self.cfg)

    def test_empty_frame(self):
        df = _frame([]).clear()
        out = donchian_signal(df, self.cfg)
        self.assertEqual(out.height, 0)
        for col in ("upper", "lower", "entry", "exit", "position"):
            self.assertIn(col, out.columns)

    def test_default_config_values(self):
        cfg = TrendFollowingConfig()
        self.assertEqual((cfg.entry_n, cfg.exit_n), (20, 10))
        self.assertEqual((cfg.sharpe_min, cfg.mdd_max_pct), (1.5, 15.0))
        self.assertEqual(cfg.cost_per_side, 0.0)
        with self.assertRaises(ValueError):
            TrendFollowingConfig(entry_n=0)


if __name__ == "__main__":
    unittest.main()
