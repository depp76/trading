"""trend_following.md 6-3 benchmarks on the shared engine and the 6-4 metrics."""
import unittest
from datetime import date

import numpy as np

from strategy.trend_following.benchmarks import run_benchmarks, run_bm3, run_bm4
from strategy.trend_following.config import StrategyParams
from strategy.trend_following.costs import CostModel
from strategy.trend_following.metrics import (
    cagr, max_drawdown, sharpe, nav_returns, yearly_returns, monthly_returns, capture_ratios, beta_alpha,
    information_ratio, period_returns, summarize_run,
)
from strategy.trend_following.signals import compute_features
from tests.strategy.trend_following.helpers import synthetic_dataset, business_days


class TestBenchmarks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ds = synthetic_dataset()
        cls.p = StrategyParams()
        cls.feats = compute_features(cls.ds, cls.p)
        cls.bms = run_benchmarks(cls.ds, cls.feats, cls.p, CostModel())

    def test_all_four_present(self):
        self.assertEqual(set(self.bms), {"BM1", "BM2", "BM3", "BM4"})

    def test_bm1_is_fully_invested_from_day_one_and_pays_no_tax(self):
        bm1 = self.bms["BM1"]
        self.assertGreater(bm1.exposure[0], 0.95)
        self.assertGreater(bm1.exposure[-1], 0.95)
        self.assertEqual(bm1.total_costs.tax, 0.0)
        self.assertGreater(bm1.nav[-1], 1.0)

    def test_bm3_sits_in_cash_until_ma200_exists_then_holds(self):
        bm3 = self.bms["BM3"]
        t0 = self.ds.start_idx
        # ~250 warm-up sessions before start in the synthetic calendar -> MA200 exists on day one,
        # and the ETF rises every day, so BM3 should be invested almost throughout.
        self.assertGreater(np.mean(bm3.exposure > 0.9), 0.95)
        self.assertGreaterEqual(t0, 200)

    def test_bm3_goes_to_cash_below_the_average(self):
        ds = synthetic_dataset()
        # Flip the ETF into a persistent decline after the start: BM3 must exit and stay out.
        book = ds.bm
        t0 = ds.start_idx
        T = book.T
        decay = np.cumprod(np.full(T - t0, 0.99))
        for arr in (book.open, book.high, book.low, book.close):
            arr[t0:, 0] = arr[t0 - 1, 0] * decay
        res = run_bm3(ds, StrategyParams(), CostModel())
        self.assertLess(res.exposure[-1], 0.01)
        self.assertTrue(any(t.reason == "below_ma" for t in res.trades))

    def test_bm2_equal_weight_over_liquid_names(self):
        bm2 = self.bms["BM2"]
        # all four synthetic names are liquid; ~equal weights on the first session
        self.assertEqual(bm2.n_positions[0], 4)
        self.assertGreater(bm2.exposure[0], 0.9)

    def test_bm4_compounds_the_rate(self):
        bm4 = run_bm4(self.ds, self.p)
        years = (self.ds.eval_dates[-1] - self.ds.eval_dates[0]).days / 365.0
        self.assertAlmostEqual(bm4.nav[-1], 1.02 ** years, delta=0.005)
        self.assertEqual(bm4.trades, [])


class TestMetrics(unittest.TestCase):
    def test_cagr_and_mdd(self):
        dates = [date(2021, 1, 1), date(2021, 7, 1), date(2022, 1, 1)]
        self.assertAlmostEqual(cagr(np.array([1.0, 1.5, 2.0]), dates), 1.0, places=6)
        self.assertAlmostEqual(max_drawdown(np.array([1.0, 0.5, 0.75, 1.2])), -0.5)
        self.assertEqual(max_drawdown(np.array([1.0, 1.1, 1.2])), 0.0)

    def test_sharpe_positive_for_positive_drift(self):
        rng = np.random.default_rng(0)
        rets = np.concatenate([[0.0], 0.001 + 0.01 * rng.standard_normal(500)])
        nav = np.cumprod(1 + rets)
        self.assertGreater(sharpe(nav_returns(nav), np.zeros_like(nav)), 0.5)

    def test_yearly_and_monthly_splits(self):
        dates = business_days(date(2021, 12, 28), 10)     # spans 2021 -> 2022
        nav = np.linspace(1.0, 2.0, 10)
        y = yearly_returns(nav, dates)
        self.assertEqual(set(y), {2021, 2022})
        last_2021 = max(i for i, d in enumerate(dates) if d.year == 2021)
        self.assertAlmostEqual(y[2021], nav[last_2021] / nav[0] - 1)
        self.assertAlmostEqual(y[2022], nav[-1] / nav[last_2021] - 1)
        m = monthly_returns(nav, dates)
        self.assertEqual(set(m), {(2021, 12), (2022, 1)})

    def test_capture_beta_ir(self):
        bm = {(2021, 1): 0.02, (2021, 2): -0.01, (2021, 3): 0.03}
        st = {k: 2 * v for k, v in bm.items()}
        up, down = capture_ratios(st, bm)
        self.assertAlmostEqual(up, 2.0)
        self.assertAlmostEqual(down, 2.0)
        rng = np.random.default_rng(1)
        r = np.concatenate([[0.0], 0.01 * rng.standard_normal(300)])
        beta, alpha = beta_alpha(r, r, np.zeros_like(r))
        self.assertAlmostEqual(beta, 1.0)
        self.assertAlmostEqual(alpha, 0.0)
        self.assertEqual(information_ratio(r, r), 0.0)

    def test_period_returns(self):
        dates = business_days(date(2021, 1, 4), 780)
        nav = np.linspace(1.0, 3.0, 780)
        pr = period_returns(nav, dates)
        self.assertIsNotNone(pr["2021-22"])
        self.assertIsNotNone(pr["2023-24"])
        self.assertIsNone(pr["2025-26"])

    def test_summarize_run_has_relative_block(self):
        ds = synthetic_dataset()
        p = StrategyParams()
        feats = compute_features(ds, p)
        bms = run_benchmarks(ds, feats, p, CostModel())
        s = summarize_run(bms["BM3"], bms["BM1"], bms["BM3"])
        for key in ("cagr", "vol", "sharpe", "mdd", "calmar", "turnover", "cost_pct_per_year", "avg_cash_weight",
                    "n_trades", "win_rate", "bm1_excess_cagr", "bm1_ir", "bm1_beta", "bm1_up_capture",
                    "bm1_period_win", "bm3_beats_risk_adjusted"):
            self.assertIn(key, s)
        self.assertFalse(s["bm3_beats_risk_adjusted"])   # a run never beats itself strictly


if __name__ == "__main__":
    unittest.main()
