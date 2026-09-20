"""tests/strategy/test_metrics.py — strategy.metrics (review_agy.md
Section 4, Phase 1)."""
import datetime as _dt
import math
import unittest

from strategy.base import Trade
from strategy.metrics import calculate_equity_metrics, calculate_returns_metrics, calculate_trade_metrics


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

    def test_single_period_with_dates_counts_one_calendar_day(self):
        # trend_following's max(days, 1): a lone observation is annualised
        # over one day, not over 1/periods_per_year of a year.
        d = _dt.date(2026, 3, 2)
        with_dates = calculate_returns_metrics([0.02], dates=[d], periods_per_year=252)
        self.assertAlmostEqual(with_dates["cagr_pct"], (1.02 ** 365.25 - 1.0) * 100.0, places=6)
        without_dates = calculate_returns_metrics([0.02], periods_per_year=252)
        self.assertAlmostEqual(without_dates["cagr_pct"], (1.02 ** 252 - 1.0) * 100.0, places=6)

    def test_risk_free_rate_lowers_sharpe(self):
        returns = [0.01, 0.02, -0.005, 0.015, 0.0]
        self.assertLess(calculate_returns_metrics(returns, risk_free_rate=0.05)["sharpe"],
                        calculate_returns_metrics(returns, risk_free_rate=0.0)["sharpe"])


class TestCalculateEquityMetrics(unittest.TestCase):

    def test_empty_curve(self):
        m = calculate_equity_metrics([])
        self.assertEqual(m, {
            "total_return_pct": 0.0, "cagr_pct": 0.0, "annual_vol_pct": 0.0,
            "sharpe": 0.0, "max_drawdown_pct": 0.0,
        })

    def test_measures_against_initial_capital_when_given(self):
        # First point is below the capital put in (fees), as real curves are.
        values = [99.0, 110.0, 120.0]
        m = calculate_equity_metrics(values, initial_capital=100.0, periods_per_year=52)
        self.assertAlmostEqual(m["total_return_pct"], 20.0, places=9)
        # Without initial_capital the first point is the base.
        m2 = calculate_equity_metrics(values, periods_per_year=52)
        self.assertAlmostEqual(m2["total_return_pct"], (120.0 / 99.0 - 1.0) * 100.0, places=9)

    def test_drawdown_is_peak_relative_from_first_point_and_positive(self):
        # 99 -> 110 -> 88 -> 95: peak 110, trough 88 => 20% drawdown; the
        # first point sitting below initial_capital is not a drawdown.
        m = calculate_equity_metrics([99.0, 110.0, 88.0, 95.0], initial_capital=100.0)
        self.assertAlmostEqual(m["max_drawdown_pct"], 20.0, places=9)

    def test_sharpe_and_vol_come_from_period_returns_regardless_of_base(self):
        values = [100.0, 105.0, 102.0, 108.0, 110.0]
        a = calculate_equity_metrics(values, periods_per_year=52)
        b = calculate_equity_metrics(values, initial_capital=1.0, periods_per_year=52)
        self.assertEqual((a["sharpe"], a["annual_vol_pct"]), (b["sharpe"], b["annual_vol_pct"]))
        self.assertGreater(a["annual_vol_pct"], 0.0)

    def test_fewer_than_two_period_returns_give_zero_sharpe_and_vol(self):
        for values in ([100.0], [100.0, 105.0]):
            m = calculate_equity_metrics(values)
            self.assertEqual((m["annual_vol_pct"], m["sharpe"]), (0.0, 0.0))

    def test_non_positive_base_reports_zero_return_and_cagr(self):
        m = calculate_equity_metrics([100.0, 120.0], initial_capital=0.0)
        self.assertEqual((m["total_return_pct"], m["cagr_pct"]), (0.0, 0.0))

    def test_equity_and_returns_views_of_the_same_path_agree(self):
        # An equity curve and its own period returns must describe the same
        # path: identical Sharpe/vol/drawdown, and identical total return
        # when the curve is measured against its first point. (CAGR is left
        # out only because the returns view spans one fewer date.)
        values = [100.0, 103.0, 99.0, 104.5, 108.0, 106.0]
        dates = [_dt.date(2025, 1, 3) + _dt.timedelta(weeks=i) for i in range(len(values))]
        returns = [values[i] / values[i - 1] - 1.0 for i in range(1, len(values))]
        eq = calculate_equity_metrics(values, dates=dates, periods_per_year=52)
        rt = calculate_returns_metrics(returns, dates=dates[1:], periods_per_year=52)
        for key in ("sharpe", "annual_vol_pct", "max_drawdown_pct", "total_return_pct"):
            self.assertAlmostEqual(eq[key], rt[key], places=9, msg=key)


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
