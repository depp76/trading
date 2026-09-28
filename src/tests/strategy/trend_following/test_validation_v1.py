"""tests/strategy/trend_following/test_validation_v1.py — the trend_following.md 4
runners on synthetic data: every variant produces a row, the grid marks its base point,
the walk-forward stitches one OOS year per fold, and the 3-1 abnormal-return check."""
from datetime import date
import unittest

import polars as pl

from strategy.trend_following import (
    ASSUMPTION_VARIANTS, KrTrendConfig, abnormal_return_rows, compare_assumptions,
    parameter_sensitivity, walk_forward_years,
)
from tests.strategy.trend_following.v1_frames import bdays, ohlcv, random_walk

CFG = KrTrendConfig(entry_n=5, exit_n=3, atr_n=5, index_regime_ma_n=10, universe_mode="fixed", max_positions=3)


def _universe(n_days=800, n_tickers=6, seed=0):
    dates = bdays(date(2023, 1, 2), n_days)
    hist = {f"{k:06d}": random_walk(dates, seed + k) for k in range(n_tickers)}
    idx = random_walk(dates, 4242, drift=0.0003, vol=0.01, p0=2500.0)
    return hist, idx


class TestRunners(unittest.TestCase):

    def test_compare_assumptions_one_row_per_variant(self):
        hist, idx = _universe(n_days=300)
        rows = compare_assumptions(hist, idx, CFG, variants=ASSUMPTION_VARIANTS[:4])
        self.assertEqual([r["name"] for r in rows], [v[0] for v in ASSUMPTION_VARIANTS[:4]])
        self.assertEqual(rows[0]["params"], {})
        for r in rows:
            for key in ("median_return_pct", "min_return_pct", "sharpe", "max_year_mdd_pct", "n_trades", "passes_risk_gate"):
                self.assertIn(key, r)
        # no-cost variant cannot do worse on net P/L than the same trades with costs... unless the
        # fill path differs; both are deterministic, so at least they run and differ in params
        self.assertEqual(rows[2]["params"]["slippage_rate"], 0.0)

    def test_parameter_sensitivity_marks_base_point(self):
        hist, idx = _universe(n_days=300)
        rows = parameter_sensitivity(hist, idx, CFG, channels=((5, 3), (8, 4)), max_positions=(3,), risks=(0.0075, 0.01))
        self.assertEqual(len(rows), 4)
        self.assertEqual(sum(1 for r in rows if r["is_base"]), 1)
        base = next(r for r in rows if r["is_base"])
        self.assertEqual(base["params"], {"entry_n": 5, "exit_n": 3, "max_positions": 3, "risk_per_trade": 0.0075})

    def test_walk_forward_one_fold_per_oos_year(self):
        hist, idx = _universe(n_days=800)                     # 2023-01 .. 2026-01: 2023-2025 complete
        wf = walk_forward_years(hist, idx, CFG, grid=[{"entry_n": 5, "exit_n": 3}, {"entry_n": 8, "exit_n": 4}],
                                first_oos_year=2024)
        self.assertEqual([f["year"] for f in wf["folds"]], [2024, 2025])
        for f in wf["folds"]:
            self.assertIn(f["chosen"], ({"entry_n": 5, "exit_n": 3}, {"entry_n": 8, "exit_n": 4}))
            self.assertIsNotNone(f["oos_return_pct"])
        self.assertIsNotNone(wf["oos"])
        self.assertGreater(wf["oos"]["n_days"], 400)
        self.assertEqual(set(wf["runs"]), {"entry_n=5, exit_n=3", "entry_n=8, exit_n=4"})
        self.assertLessEqual(wf["n_base_chosen"], 2)

    def test_abnormal_return_rows(self):
        dates = bdays(date(2025, 1, 6), 6)
        df = ohlcv(dates, [100.0, 101.0, 30.0, 31.0, 31.5, 60.0])          # -70% and +90% days
        rows = abnormal_return_rows({"X": df, "Y": pl.DataFrame()}, threshold=0.35)
        self.assertEqual([(r["ticker"], r["date"]) for r in rows], [("X", "2025-01-08"), ("X", "2025-01-13")])
        self.assertAlmostEqual(rows[0]["return_pct"], (30.0 / 101.0 - 1) * 100.0)
