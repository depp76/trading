"""tests/strategy/test_metrics.py — strategy.metrics (review_agy.md
Section 4, Phase 1)."""
import datetime as _dt
import math
import unittest

from strategy.base import Trade
from strategy.metrics import calculate_returns_metrics, calculate_trade_metrics


class TestCalculateReturnsMetrics(unittest.TestCase):

    def test_empty_returns(self):
        self.assertEqual(calculate_returns_metrics([]), {
            "total_return_pct": 0.0, "cagr_pct": 0.0, "annual_vol_pct": 0.0,
            "sharpe": 0.0, "max_drawdown_pct": 0.0,
        })

    def test_all_zero_returns(self):
        m = calculate_returns_metrics([0.0, 0.0, 0.0])
        self.assertEqual(m, {
            "total_return_pct": 0.0, "cagr_pct": 0.0, "annual_vol_pct": 0.0,
            "sharpe": 0.0, "max_drawdown_pct": 0.0,
        })

    def test_total_return_and_max_drawdown(self):
        # equity: 1.10 -> 0.88 -> 0.924 (a 20% drawdown from the peak, then
        # a partial recovery that doesn't erase it).
        m = calculate_returns_metrics([0.10, -0.20, 0.05])
        self.assertAlmostEqual(m["total_return_pct"], -7.6, places=6)
        self.assertAlmostEqual(m["max_drawdown_pct"], 20.0, places=6)
        self.assertTrue(math.isfinite(m["sharpe"]))
        self.assertTrue(math.isfinite(m["cagr_pct"]))

    def test_single_period_has_zero_vol_and_sharpe(self):
        m = calculate_returns_metrics([0.05])
        self.assertEqual(m["annual_vol_pct"], 0.0)
        self.assertEqual(m["sharpe"], 0.0)
        self.assertAlmostEqual(m["total_return_pct"], 5.0, places=6)

    def test_dates_based_cagr_differs_from_period_count_fallback(self):
        # Two periods of +5% each, 365 days apart: with dates, that's
        # correctly annualized as ~1 year of compounding (cagr close to the
        # total return). Without dates, periods_per_year=252 treats it as
        # ~2 trading days out of a year, extrapolating wildly.
        returns = [0.05, 0.05]
        dates = [_dt.date(2025, 1, 1), _dt.date(2026, 1, 1)]

        with_dates = calculate_returns_metrics(returns, dates=dates, periods_per_year=252)
        without_dates = calculate_returns_metrics(returns, periods_per_year=252)

        self.assertAlmostEqual(with_dates["cagr_pct"], with_dates["total_return_pct"], delta=1.0)
        self.assertGreater(without_dates["cagr_pct"], 1e6)

    def test_sharpe_is_zero_for_a_flat_return_series_with_no_volatility(self):
        m = calculate_returns_metrics([0.01, 0.01, 0.01, 0.01])
        self.assertEqual(m["annual_vol_pct"], 0.0)
        self.assertEqual(m["sharpe"], 0.0)


class TestCalculateTradeMetrics(unittest.TestCase):

    def _trade(self, net_return_pct):
        return Trade(ticker="X", entry_date="2026-01-01", exit_date="2026-01-02",
                     entry_price=100.0, exit_price=100.0, qty=1.0, weight=1.0,
                     price_return_pct=net_return_pct, net_return_pct=net_return_pct)

    def test_no_trades(self):
        self.assertEqual(calculate_trade_metrics([]), {
            "n_trades": 0, "win_rate_pct": 0.0, "profit_factor": 0.0, "avg_trade_pct": 0.0,
        })

    def test_mixed_wins_and_losses(self):
        trades = [self._trade(10.0), self._trade(-5.0), self._trade(20.0)]
        m = calculate_trade_metrics(trades)
        self.assertEqual(m["n_trades"], 3)
        self.assertAlmostEqual(m["win_rate_pct"], 200.0 / 3.0, places=6)
        self.assertAlmostEqual(m["profit_factor"], 6.0, places=6)   # 30 gross win / 5 gross loss
        self.assertAlmostEqual(m["avg_trade_pct"], 25.0 / 3.0, places=6)

    def test_no_losses_caps_profit_factor_instead_of_returning_infinity(self):
        trades = [self._trade(10.0), self._trade(5.0)]
        m = calculate_trade_metrics(trades)
        self.assertTrue(math.isfinite(m["profit_factor"]))
        self.assertGreater(m["profit_factor"], 0.0)

    def test_all_zero_returns_give_zero_profit_factor(self):
        trades = [self._trade(0.0), self._trade(0.0)]
        m = calculate_trade_metrics(trades)
        self.assertEqual(m["profit_factor"], 0.0)


if __name__ == "__main__":
    unittest.main()
