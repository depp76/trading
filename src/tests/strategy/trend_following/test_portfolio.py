"""tests/strategy/trend_following/test_portfolio.py — v3 equal-sleeve portfolio backtest
(trend_following.md 3 "v3")."""
import unittest
from unittest.mock import patch

import numpy as np
import polars as pl

from strategy.trend_following import TrendFollowingConfig, run_backtest, run_portfolio_backtest, run_portfolio_backtest_for_tickers
from tests.strategy.trend_following.frames import make_frame as _frame




def _walk(n, seed, drift=0.001):
    rnd = np.random.default_rng(seed)
    closes = [100.0]
    for _ in range(n - 1):
        closes.append(max(1.0, closes[-1] * (1 + rnd.normal(drift, 0.02))))
    return closes


CFG = TrendFollowingConfig(entry_n=5, exit_n=3)


class TestPortfolioBacktest(unittest.TestCase):

    def test_portfolio_return_is_mean_of_sleeves(self):
        hist = {"A": _frame(_walk(120, 1)), "B": _frame(_walk(120, 2)), "C": _frame(_walk(120, 3))}
        res = run_portfolio_backtest(hist, CFG)
        sleeves = np.column_stack([run_backtest(df, CFG)["signals"]["strategy_return"].to_numpy() for df in hist.values()])
        np.testing.assert_allclose(res["daily"]["portfolio_return"].to_numpy(), sleeves.mean(axis=1), atol=1e-12)
        self.assertEqual(res["summary"]["n_instruments"], 3)
        self.assertEqual(len(res["equity_curve"]), 120)
        self.assertEqual(res["summary"]["n_trades"], sum(s["n_trades"] for s in res["per_instrument"].values()))

    def test_missing_days_contribute_zero(self):
        a = _frame(_walk(60, 4))
        b = _frame(_walk(60, 5), skip={10, 11, 12})          # B has no bars on 3 days
        res = run_portfolio_backtest({"A": a, "B": b}, CFG)
        self.assertEqual(res["daily"].height, 60)              # union of dates
        ra = run_backtest(a, CFG)["signals"]["strategy_return"].to_numpy()
        pr = res["daily"]["portfolio_return"].to_numpy()
        for i in (10, 11, 12):
            self.assertAlmostEqual(pr[i], ra[i] / 2.0)         # B counted as 0 that day
        self.assertEqual(res["sleeve_returns"].columns, ["Date", "A", "B"])

    def test_gross_exposure_bounded_by_max_weight(self):
        cfg = TrendFollowingConfig(entry_n=5, exit_n=3, max_weight=0.5)
        hist = {"A": _frame(_walk(150, 6)), "B": _frame(_walk(150, 7))}
        res = run_portfolio_backtest(hist, cfg)
        self.assertLessEqual(res["daily"]["gross_exposure"].max(), 0.5 + 1e-12)
        self.assertLessEqual(res["summary"]["avg_gross_exposure_pct"], 50.0 + 1e-9)
        self.assertLessEqual(res["daily"]["n_positions"].max(), 2)

    def test_diversification_lowers_vol_vs_average_sleeve(self):
        hist = {f"T{i}": _frame(_walk(400, 10 + i)) for i in range(6)}
        res = run_portfolio_backtest(hist, CFG)
        sleeve_vols = [s["annual_vol_pct"] for s in res["per_instrument"].values()]
        self.assertLess(res["summary"]["annual_vol_pct"], np.mean(sleeve_vols))

    def test_skips_short_or_empty_histories(self):
        hist = {"A": _frame(_walk(100, 8)), "B": _frame([1, 2, 3]), "C": pl.DataFrame()}
        res = run_portfolio_backtest(hist, CFG, min_days=30)
        self.assertEqual(res["summary"]["n_instruments"], 1)
        self.assertEqual(sorted(res["summary"]["skipped"]), ["B", "C"])

    def test_all_empty(self):
        res = run_portfolio_backtest({"A": pl.DataFrame()}, CFG)
        self.assertEqual(res["summary"]["n_instruments"], 0)
        self.assertFalse(res["summary"]["passes_risk_gate"])
        self.assertEqual(res["equity_curve"], [])

    @patch("strategy.trend_following.portfolio.get_historical_data")
    def test_for_tickers_fetches_each(self, mock_hist):
        mock_hist.side_effect = lambda t, s: _frame(_walk(80, hash(t) % 100))
        res = run_portfolio_backtest_for_tickers(["X", "Y"], "2025-01-01", CFG)
        self.assertEqual(mock_hist.call_count, 2)
        self.assertEqual(res["tickers"], ["X", "Y"])
        self.assertEqual(res["summary"]["n_instruments"], 2)


if __name__ == "__main__":
    unittest.main()
