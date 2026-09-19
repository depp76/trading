"""tests/strategy/trend_following/test_validation.py — IS/OOS holdout and anchored
walk-forward validation (trend_following.md 5, 6)."""
import unittest
from datetime import date

import numpy as np
import polars as pl

from strategy.trend_following import (
    TrendFollowingConfig, holdout_validation, walk_forward_validation, yearly_folds, window_metrics,
    run_portfolio_backtest,
)
from tests.strategy.trend_following.frames import make_frame as _frame




def _walk(n, seed, drift=0.0008):
    rnd = np.random.default_rng(seed)
    closes = [100.0]
    for _ in range(n - 1):
        closes.append(max(1.0, closes[-1] * (1 + rnd.normal(drift, 0.02))))
    return closes


START = date(2020, 1, 1)
N = 365 * 4 + 1          # 2020-01-01 .. 2023-12-31 (calendar days, one bar per day)
HIST = {"A": _frame(_walk(N, 1), start=START), "B": _frame(_walk(N, 2), start=START), "C": _frame(_walk(N, 3), start=START)}
GRID = [{"entry_n": 10, "exit_n": 5}, {"entry_n": 30, "exit_n": 15}, {"entry_n": 60, "exit_n": 30}]


class TestWindowMetrics(unittest.TestCase):

    def test_window_slices_by_date(self):
        res = run_portfolio_backtest(HIST, TrendFollowingConfig(entry_n=10, exit_n=5))
        daily = res["daily"].select(["Date", "portfolio_return"])
        m = window_metrics(daily, date(2021, 1, 1), date(2022, 1, 1), TrendFollowingConfig())
        self.assertEqual(m["n_days"], 365)
        self.assertEqual(m["start_date"], "2021-01-01")
        self.assertEqual(m["end_date"], "2021-12-31")
        full = window_metrics(daily, date(2020, 1, 1), None, TrendFollowingConfig())
        self.assertEqual(full["n_days"], N)


class TestHoldout(unittest.TestCase):

    def test_picks_best_on_is_and_reports_oos(self):
        res = holdout_validation(HIST, split_date=date(2023, 1, 1), grid=GRID)
        self.assertEqual(len(res["grid"]), 3)
        best_is = max(r["is"]["sharpe"] for r in res["grid"])
        self.assertAlmostEqual(res["is"]["sharpe"], best_is)
        self.assertIn(res["best_params"], GRID)
        self.assertEqual(res["oos"]["start_date"], "2023-01-01")
        self.assertEqual(res["is"]["end_date"], "2022-12-31")
        self.assertIsNotNone(res["is_oos_rank_corr"])
        self.assertTrue(-1.0 <= res["is_oos_rank_corr"] <= 1.0)

    def test_mdd_cap_constrains_choice(self):
        res = holdout_validation(HIST, split_date=date(2023, 1, 1), grid=GRID, mdd_cap=1e9)
        unconstrained = holdout_validation(HIST, split_date=date(2023, 1, 1), grid=GRID)
        self.assertEqual(res["best_params"], unconstrained["best_params"])
        tight = holdout_validation(HIST, split_date=date(2023, 1, 1), grid=GRID, mdd_cap=0.0)
        # nothing qualifies with a 0% cap -> falls back to the unconstrained winner
        self.assertEqual(tight["best_params"], unconstrained["best_params"])

    def test_empty_histories(self):
        res = holdout_validation({"A": pl.DataFrame()}, split_date=date(2023, 1, 1), grid=GRID)
        self.assertIsNone(res["best_params"])
        self.assertEqual(res["grid"], [])


class TestWalkForward(unittest.TestCase):

    def test_folds_are_anchored_and_stitched(self):
        folds = yearly_folds(2022, 2023)
        res = walk_forward_validation(HIST, folds, grid=GRID)
        self.assertEqual(res["n_folds"], 2)
        f0, f1 = res["folds"]
        self.assertEqual(f0["is"]["start_date"], "2020-01-01")
        self.assertEqual(f0["is"]["end_date"], "2021-12-31")   # everything before the fold
        self.assertEqual(f1["is"]["end_date"], "2022-12-31")   # expanding window
        self.assertEqual(f0["oos"]["n_days"], 365)
        self.assertEqual(res["oos"]["n_days"], 730)
        self.assertEqual(res["oos_daily"].height, 730)
        self.assertEqual(res["oos"]["start_date"], "2022-01-01")
        self.assertEqual(res["oos"]["end_date"], "2023-12-31")
        for f in res["folds"]:
            self.assertIn(f["best_params"], GRID)
            self.assertEqual(len(f["grid_oos_sharpe"]), 3)

    def test_stitched_series_matches_fold_winners(self):
        res = walk_forward_validation(HIST, yearly_folds(2022, 2023), grid=GRID)
        labels = res["oos_daily"]["params"].unique().to_list()
        self.assertTrue(set(labels) <= {f["best_label"] for f in res["folds"]})

    def test_fold_without_enough_is_data_is_skipped(self):
        res = walk_forward_validation(HIST, yearly_folds(2020, 2021), grid=GRID, min_is_days=250)
        self.assertEqual(res["n_folds"], 1)                  # 2020 fold has no IS data
        self.assertEqual(res["folds"][0]["oos_start"], "2021-01-01")

    def test_yearly_folds(self):
        self.assertEqual(yearly_folds(2024, 2025), [(date(2024, 1, 1), date(2025, 1, 1)), (date(2025, 1, 1), date(2026, 1, 1))])


if __name__ == "__main__":
    unittest.main()
