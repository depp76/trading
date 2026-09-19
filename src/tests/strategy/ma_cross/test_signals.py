"""tests/strategy/ma_cross/test_signals.py — MaCrossConfig validation and the
entry/exit conditions in strategy.ma_cross.signals (ma_cross.md 3)."""
import unittest
from datetime import date

import numpy as np
import polars as pl

from strategy.ma_cross import (
    MaCrossConfig, entry_signal, exit_condition, next_true_index,
    run_backtest_strategy, run_backtest_for_stock,
)


class TestConfig(unittest.TestCase):

    def test_defaults_match_the_former_literals(self):
        c = MaCrossConfig()
        self.assertEqual((c.fast_n, c.slow_n, c.entry_mult, c.take_profit_mult, c.overheat_mult), (20, 60, 1.10, 1.30, 1.30))
        self.assertEqual((c.fast_col, c.slow_col, c.windows), ("MA20", "MA60", (10, 20, 60)))

    def test_rejects_bad_values(self):
        for kw in ({"fast_n": 60, "slow_n": 20}, {"take_profit_mult": 1.0}, {"overheat_mult": 0.9},
                   {"entry_mult": 0}, {"days": 10}):
            with self.assertRaises(ValueError):
                MaCrossConfig(**kw)


class TestSignals(unittest.TestCase):

    def test_entry_is_the_crossing_day_only(self):
        slow = np.full(6, 100.0)
        fast = np.array([100.0, 105.0, 111.0, 115.0, 108.0, 112.0])   # crosses 110 on day 2 and day 5
        self.assertEqual(entry_signal(fast, slow).tolist(), [False, False, True, False, False, True])

    def test_entry_multiple_is_configurable(self):
        slow = np.full(3, 100.0)
        fast = np.array([99.0, 101.0, 102.0])
        self.assertEqual(entry_signal(fast, slow, MaCrossConfig(entry_mult=1.0)).tolist(), [False, True, False])
        self.assertEqual(entry_signal(fast, slow).tolist(), [False, False, False])

    def test_exit_condition_overheat_or_dead_cross(self):
        slow = np.full(4, 100.0)
        fast = np.array([120.0, 135.0, 99.0, 110.0])
        self.assertEqual(exit_condition(fast, slow).tolist(), [False, True, True, False])

    def test_next_true_index(self):
        self.assertEqual(next_true_index([False, False, True, False, True]).tolist(), [2, 2, 2, 4, 4])
        self.assertEqual(next_true_index([False, False]).tolist(), [2, 2])


class TestBacktestWithConfig(unittest.TestCase):

    def _frame(self):
        n = 20
        dates = [date(2023, 1, d + 1) for d in range(n)]
        ma60 = [100.0] * n
        ma20 = [100.0] * 5 + [111.0] * (n - 5)
        close = [100.0] * n
        opens = [100.0] * 6 + [131.0] + [100.0] * (n - 7)
        return pl.DataFrame({"Date": dates, "Open": opens, "High": close, "Low": close, "Close": close,
                             "Volume": [1000] * n, "MA20": ma20, "MA60": ma60})

    def test_default_config_reproduces_the_literal_behaviour(self):
        df = self._frame()
        self.assertEqual(run_backtest_strategy(df), run_backtest_strategy(df, config=MaCrossConfig()))

    def test_overheat_multiple_controls_the_exit(self):
        # Fast MA sits at 111 vs slow 100 after the cross: no exit under the
        # default 1.30 overheat level (0 trades), an exit under 1.05 (1 trade).
        df = self._frame()
        base = run_backtest_strategy(df, buy_sell_points=True)
        tight = run_backtest_strategy(df, buy_sell_points=True, config=MaCrossConfig(overheat_mult=1.05))
        self.assertEqual((base[0], tight[0]), (0, 1))

    def test_run_backtest_for_stock_returns_summary_and_frame(self):
        df = self._frame()
        res = run_backtest_for_stock("TEST", "KOSPI", df=df)
        self.assertEqual(res["error"], "")
        self.assertIs(res["df"], df)
        s = res["summary"]
        self.assertEqual(s["n_trades"], len(res["trades"]))
        self.assertEqual(set(s), {"n_trades", "win_count", "win_rate_pct", "cumulative_return_pct"})
        if s["n_trades"]:
            self.assertAlmostEqual(s["win_rate_pct"], s["win_count"] / s["n_trades"] * 100)


if __name__ == "__main__":
    unittest.main()
