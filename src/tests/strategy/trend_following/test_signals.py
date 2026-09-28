"""trend_following.md 2-1..2-4, 3: rolling helpers, weekly check days, the
regime state machine, and the feature arrays on the synthetic dataset."""
import unittest
from datetime import date

import numpy as np

from strategy.trend_following.config import StrategyParams
from strategy.trend_following.signals import (
    rolling, shift, atr, weekly_check_days, regime_state, compute_features, rank_desc_score,
)
from tests.strategy.trend_following.helpers import synthetic_dataset, business_days, TOP


class TestRollingHelpers(unittest.TestCase):
    def test_rolling_mean_and_shift(self):
        a = np.arange(1.0, 7.0)
        m = rolling(a, 3, "mean")
        self.assertTrue(np.isnan(m[:2]).all())
        self.assertAlmostEqual(m[2], 2.0)
        self.assertAlmostEqual(m[-1], 5.0)
        s = shift(a, 2)
        self.assertTrue(np.isnan(s[:2]).all())
        self.assertEqual(s[2], 1.0)
        self.assertTrue(np.array_equal(shift(a, 0), a))

    def test_rolling_works_on_2d(self):
        a = np.column_stack([np.arange(5.0), np.arange(5.0) * 2])
        m = rolling(a, 2, "max")
        self.assertEqual(m.shape, (5, 2))
        self.assertEqual(m[4, 1], 8.0)

    def test_atr_of_constant_range(self):
        close = np.full(30, 100.0)
        high = np.full(30, 102.0)
        low = np.full(30, 98.0)
        a = atr(high, low, close, 20)
        self.assertAlmostEqual(a[-1], 4.0)
        self.assertTrue(np.isnan(a[18]))


class TestWeeklyCheckDays(unittest.TestCase):
    def test_monday_is_picked_or_the_next_trading_day(self):
        days = business_days(date(2026, 9, 1), 14)          # Tue 2026-09-01 .. Fri 2026-09-18
        days = [d for d in days if d != date(2026, 9, 7)]   # drop Monday 09-07 (holiday)
        mask = weekly_check_days(days, 0)
        picked = [d for d, m in zip(days, mask) if m]
        self.assertIn(date(2026, 9, 1), picked)             # first week has no Monday -> Tuesday
        self.assertIn(date(2026, 9, 8), picked)             # Monday missing -> Tuesday
        self.assertIn(date(2026, 9, 14), picked)
        self.assertEqual(sum(mask), 3)

    def test_friday_check_falls_back_to_last_day_of_week(self):
        days = [date(2026, 9, 14), date(2026, 9, 15), date(2026, 9, 16), date(2026, 9, 17)]  # no Friday
        mask = weekly_check_days(days, 4)
        self.assertEqual([d for d, m in zip(days, mask) if m], [date(2026, 9, 17)])


class TestRegime(unittest.TestCase):
    def test_rising_index_is_risk_on_and_falling_is_off(self):
        n = 200
        days = business_days(date(2021, 1, 4), n)
        p = StrategyParams()
        up = 100.0 * np.cumprod(np.full(n, 1.001))
        on, check = regime_state(up, days, p)
        self.assertTrue(on[-50:].all())
        down = 100.0 * np.cumprod(np.full(n, 0.999))
        off, _ = regime_state(down, days, p)
        self.assertFalse(off[-50:].any())
        self.assertGreater(check.sum(), 30)

    def test_state_only_changes_on_check_days(self):
        n = 120
        days = business_days(date(2021, 1, 4), n)
        p = StrategyParams()
        close = 100.0 * np.cumprod(np.full(n, 1.001))
        crash_day = 92                     # a Wednesday: the crash keeps the old state until Monday's check
        close[crash_day:] = close[crash_day - 1] * 0.8
        on, check = regime_state(close, days, p)
        nxt = next(i for i in range(crash_day, n) if check[i])
        if nxt > crash_day:
            self.assertTrue(on[crash_day])
        self.assertFalse(on[nxt])


class TestFeatures(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ds = synthetic_dataset(with_flows=True)
        cls.feats = compute_features(cls.ds, StrategyParams())

    def test_up_stock_breaks_out_daily_and_flat_never(self):
        f, b = self.feats, self.ds.stocks
        j_up, j_flat = b.col("UP"), b.col("FLAT")
        t0 = self.ds.start_idx
        self.assertTrue(f.breakout[t0:, j_up].all())
        self.assertTrue(f.trend_ok[t0:, j_up].all())
        self.assertFalse(f.breakout[t0:, j_flat].any())
        self.assertFalse(f.trend_ok[t0:, j_flat].any())
        self.assertTrue(f.liquid[t0:, j_up].all())

    def test_volume_confirmation_only_on_spike_days(self):
        f, b = self.feats, self.ds.stocks
        j = b.col("UP")
        spikes = [t for t in range(60, b.T) if t % 7 == 0]
        self.assertTrue(f.vol_ok[spikes, j].all())
        self.assertFalse(f.vol_ok[[t for t in range(60, b.T) if t % 7 == 1], j].any())

    def test_flow_signals(self):
        f, b = self.feats, self.ds.stocks
        self.assertTrue(f.has_flows)
        self.assertGreater(f.flow_coverage, 0.99)
        j = b.col("SPIKE")
        self.assertTrue(f.flow_ok[100:TOP - 10, j].all())
        self.assertFalse(f.flow_ok[TOP + 25:, j].any())
        self.assertTrue(np.isfinite(f.flow_strength[100, j]))
        self.assertGreater(f.flow_strength[100, j], 0)
        j_d = b.col("DRIFT")
        self.assertTrue(f.flow_exit[300:, j_d].any())

    def test_no_flow_data_means_no_flow_signal(self):
        ds = synthetic_dataset(with_flows=False)
        f = compute_features(ds, StrategyParams())
        self.assertFalse(f.has_flows)
        self.assertFalse(f.flow_ok.any())
        self.assertTrue(np.isnan(f.flow_strength).all())

    def test_spike_exits_via_channel_after_the_top(self):
        f, b = self.feats, self.ds.stocks
        j = b.col("SPIKE")
        self.assertTrue(f.exit_channel[TOP + 1:TOP + 12, j].any())
        self.assertFalse(f.exit_channel[100:TOP - 1, j].any())

    def test_high52_proximity_and_rs(self):
        f, b = self.feats, self.ds.stocks
        j = b.col("UP")
        self.assertTrue(np.isfinite(f.high52_prox[-1, j]))
        self.assertGreater(f.high52_prox[-1, j], 0.99)
        self.assertGreater(f.rs[-1, j], 0)          # UP outruns the index
        self.assertLess(f.rs[-1, b.col("FLAT")], 0)


class TestRankScore(unittest.TestCase):
    def test_sum_of_ranks_with_nan_lowest(self):
        s = rank_desc_score(np.array([np.nan, 1.0, 5.0]), np.array([3.0, 2.0, 1.0]))
        # ranks: col1 -> [1,2,3], col2 -> [3,2,1] => [4,4,4]; ties allowed
        self.assertEqual(list(s), [4.0, 4.0, 4.0])
        s2 = rank_desc_score(np.array([2.0, 1.0]), np.array([2.0, 1.0]))
        self.assertEqual(list(s2), [4.0, 2.0])


if __name__ == "__main__":
    unittest.main()
