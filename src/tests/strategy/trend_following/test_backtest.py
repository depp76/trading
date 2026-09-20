"""tests/strategy/trend_following/test_backtest.py — run_backtest() position timing,
costs, trade extraction and metrics (trend_following.md 5)."""
import math
import unittest
from unittest.mock import patch

import numpy as np
import polars as pl

from strategy.trend_following import TrendFollowingConfig, run_backtest, run_backtest_for_ticker
from tests.strategy.trend_following.frames import make_frame as _frame




CFG = TrendFollowingConfig(entry_n=3, exit_n=2)


class TestRunBacktest(unittest.TestCase):

    def test_flat_market_no_trades(self):
        res = run_backtest(_frame([10] * 30), CFG)
        s = res["summary"]
        self.assertEqual(s["n_trades"], 0)
        self.assertAlmostEqual(s["total_return_pct"], 0.0)
        self.assertAlmostEqual(s["exposure_pct"], 0.0)
        self.assertFalse(s["passes_risk_gate"])
        self.assertEqual(len(res["equity_curve"]), 30)

    def test_position_earns_next_days_return(self):
        # breakout on day 3 (close 20); the strategy must miss day 3's jump and earn day 4's
        closes = [10, 10, 10, 20, 22, 22, 22, 22]
        res = run_backtest(_frame(closes), CFG)
        sig = res["signals"]
        sr = sig["strategy_return"].to_list()
        self.assertAlmostEqual(sr[3], 0.0)                # entry day itself not earned
        self.assertAlmostEqual(sr[4], 22 / 20 - 1)         # first full day in position
        pos_lag = np.r_[0, sig["position"].to_numpy()[:-1]]
        np.testing.assert_allclose(sr, pos_lag * sig["daily_return"].to_numpy())

    def test_trade_extraction_and_open_trade(self):
        closes = [10, 10, 10, 20, 22, 24, 5, 5, 5, 30, 33]   # one closed trade, one still open
        res = run_backtest(_frame(closes), CFG)
        trades = res["trades"]
        self.assertEqual(len(trades), 2)
        self.assertEqual(trades[0]["entry_date"], "2025-01-04")
        self.assertEqual(trades[0]["exit_date"], "2025-01-07")
        self.assertAlmostEqual(trades[0]["return_pct"], (5 / 20 - 1) * 100)   # enter@20 close, exit@5 close
        self.assertEqual(trades[0]["days_held"], 3)
        self.assertIsNone(trades[1]["exit_date"])
        self.assertEqual(res["summary"]["n_trades"], 2)
        self.assertEqual(res["summary"]["n_closed_trades"], 1)
        self.assertAlmostEqual(res["summary"]["win_rate_pct"], 0.0)

    def test_equity_matches_trade_compounding(self):
        closes = [10, 10, 10, 20, 25, 30, 2, 2]
        res = run_backtest(_frame(closes), CFG, initial_capital=100.0)
        final = res["equity_curve"][-1]["value"]
        self.assertAlmostEqual(final, 100.0 * (2 / 20))     # bought 20, sold 2
        self.assertAlmostEqual(res["summary"]["total_return_pct"], -90.0)
        self.assertGreater(res["summary"]["max_drawdown_pct"], 0.0)

    def test_costs_reduce_return(self):
        closes = [10, 10, 10, 20, 25, 30, 2, 2]
        free = run_backtest(_frame(closes), CFG)["summary"]["total_return_pct"]
        costly = run_backtest(_frame(closes), TrendFollowingConfig(entry_n=3, exit_n=2, fee_rate=0.001, slippage_rate=0.001))
        self.assertLess(costly["summary"]["total_return_pct"], free)
        # two fills (entry + exit), each charged cost_per_side
        fills = costly["signals"].filter(pl.col("strategy_return") < 0).height
        self.assertGreaterEqual(fills, 2)

    def test_metrics_sanity_on_uptrend(self):
        closes = [100 * (1.01 ** i) for i in range(120)]
        s = run_backtest(_frame(closes), CFG)["summary"]
        self.assertGreater(s["total_return_pct"], 0.0)
        self.assertGreater(s["cagr_pct"], 0.0)
        self.assertGreaterEqual(s["max_drawdown_pct"], 0.0)
        self.assertTrue(0.0 <= s["exposure_pct"] <= 100.0)
        self.assertTrue(math.isfinite(s["sharpe"]))
        self.assertTrue(s["passes_risk_gate"])          # steady uptrend: high Sharpe, ~0 drawdown

    def test_risk_gate_thresholds(self):
        closes = [100 * (1.01 ** i) for i in range(120)]
        strict = TrendFollowingConfig(entry_n=3, exit_n=2, sharpe_min=1e9)
        self.assertFalse(run_backtest(_frame(closes), strict)["summary"]["passes_risk_gate"])

    def test_too_short_history(self):
        res = run_backtest(_frame([10]), CFG)
        self.assertEqual(res["summary"]["n_days"], 0)
        self.assertEqual(res["trades"], [])

    def test_return_metrics_matches_pre_migration_formula(self):
        """return_metrics() delegates to strategy.metrics since Phase 2 of
        review_agy.md Section 4; pin it to the formula it used before (a
        verbatim copy), including the risk-free rate, so the recorded
        real-data results in trend_following.md 5 stay reproducible."""
        import datetime as _dt
        from strategy.trend_following import return_metrics

        rng = np.random.default_rng(7)
        cfg = TrendFollowingConfig(trading_days_per_year=252, risk_free_rate=0.03,
                                   sharpe_min=1.0, mdd_max_pct=20.0)
        for n in (1, 2, 5, 60, 300):
            ret = rng.normal(0.0005, 0.02, n)
            dates = [_dt.date(2024, 1, 1) + _dt.timedelta(days=int(i * 1.4)) for i in range(n)]
            got = return_metrics(dates, ret, cfg, initial_capital=100.0)

            equity = 100.0 * np.cumprod(1.0 + ret)
            final = float(equity[-1])
            years = max((dates[-1] - dates[0]).days, 1) / 365.25
            cagr = (final / 100.0) ** (1.0 / years) - 1.0 if final > 0 else -1.0
            tdpy = cfg.trading_days_per_year
            vol = float(np.std(ret, ddof=1)) * math.sqrt(tdpy) if n > 1 else 0.0
            excess = ret - cfg.risk_free_rate / tdpy
            sd = float(np.std(excess, ddof=1)) if n > 1 else 0.0
            sharpe = float(np.mean(excess)) / sd * math.sqrt(tdpy) if sd > 0 else 0.0
            peak = np.maximum.accumulate(equity)
            mdd = -float((equity / peak - 1.0).min()) * 100.0

            self.assertAlmostEqual(got["total_return_pct"], (final / 100.0 - 1.0) * 100.0, places=9)
            self.assertAlmostEqual(got["cagr_pct"], cagr * 100.0, places=9)
            self.assertAlmostEqual(got["annual_vol_pct"], vol * 100.0, places=9)
            self.assertAlmostEqual(got["sharpe"], sharpe, places=9)
            self.assertAlmostEqual(got["max_drawdown_pct"], mdd, places=9)
            self.assertEqual(got["passes_risk_gate"], bool(sharpe >= 1.0 and mdd <= 20.0))
            self.assertEqual((got["start_date"], got["end_date"], got["n_days"]),
                             (dates[0].strftime("%Y-%m-%d"), dates[-1].strftime("%Y-%m-%d"), n))

    @patch("strategy.trend_following.backtest.get_historical_data")
    def test_run_backtest_for_ticker(self, mock_hist):
        mock_hist.return_value = _frame([10, 10, 10, 20, 22, 22])
        res = run_backtest_for_ticker("005930", "2025-01-01", CFG)
        self.assertEqual(res["ticker"], "005930")
        self.assertEqual(res["error"], "")
        self.assertEqual(res["summary"]["n_trades"], 1)
        mock_hist.assert_called_once_with("005930", "2025-01-01")

    @patch("strategy.trend_following.backtest.get_historical_data")
    def test_run_backtest_for_ticker_no_data(self, mock_hist):
        mock_hist.return_value = pl.DataFrame()
        res = run_backtest_for_ticker("XXXX", "2025-01-01", CFG)
        self.assertEqual(res["error"], "No data")
        self.assertEqual(res["summary"]["n_trades"], 0)


if __name__ == "__main__":
    unittest.main()
