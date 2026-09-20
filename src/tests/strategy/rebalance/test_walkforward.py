"""
tests/strategy/rebalance/test_walkforward.py — strategy/rebalance/walkforward.py transaction-cost verification (rebalance.md 11-3)
"""
import math
import unittest
from datetime import date

import polars as pl


class TestRebalanceTransactionCosts(unittest.TestCase):

    def _make_dummy_series(self, ticker: str, dates: list, prices: list, scores_high: bool):
        # Create minimal Polars DataFrame matching _SNAPSHOT_COLUMNS
        div_val = 110.0 if scores_high else 80.0
        return pl.DataFrame({
            "Date": dates,
            "Close": prices,
            "MA20_Div": [div_val] * len(dates),
            "MA50_Div": [div_val] * len(dates),
            "high52w_diff": [0.0 if scores_high else -30.0] * len(dates),
            "ret_20d": [10.0 if scores_high else -10.0] * len(dates),
            "ret_60d": [20.0 if scores_high else -20.0] * len(dates),
            "ma20_roc_1w": [5.0 if scores_high else -5.0] * len(dates),
        })

    def test_fee_and_tax_deducted_in_simulation(self):
        from strategy.rebalance import _run_walkforward_simulation, _summarize_backtest

        dates = [date(2025, 1, 3), date(2025, 1, 10), date(2025, 1, 17)]
        # Ticker A starts strong (scores high), then weakens
        series_a = self._make_dummy_series("A", dates, [100.0, 110.0, 90.0], scores_high=True)
        # Ticker B starts weak, then strengthens
        series_b = self._make_dummy_series("B", dates, [50.0, 50.0, 60.0], scores_high=False)

        series_map = {"A": series_a, "B": series_b}

        # Run simulation with 0.015% buy fee, 0.015% sell fee, 0.18% sell tax
        sim = _run_walkforward_simulation(
            series_map,
            dates,
            top_n_by_market={"KOSPI": 1},
            band_multiplier=1.0,
            initial_capital=10_000_000.0,
            market_by_ticker={"A": "KOSPI", "B": "KOSPI"},
            buy_fee_rate=0.00015,
            sell_fee_rate=0.00015,
            sell_tax_rate=0.0018,
        )

        self.assertIn("trades", sim)
        self.assertGreater(len(sim["trades"]), 0)

        # Check that trade entries record fee and tax fields
        for tr in sim["trades"]:
            self.assertIn("fee", tr)
            self.assertIn("tax", tr)
            self.assertGreaterEqual(tr["fee"], 0.0)
            if tr["action"] == "sell":
                self.assertGreater(tr["tax"], 0.0)

        # Check total friction cost
        self.assertGreater(sim["total_cost_paid"], 0.0)

        # Check summary cost output
        summary = _summarize_backtest(
            sim["equity_curve"],
            [],
            10_000_000.0,
            sim["closed_trade_returns"],
            len(dates),
            len(sim["trades"]),
            total_cost_paid=sim["total_cost_paid"],
        )
        self.assertEqual(summary["total_cost_amount"], sim["total_cost_paid"])
        self.assertGreater(summary["total_cost_drag_pct"], 0.0)


class TestSharpeAndVol(unittest.TestCase):
    """strategy/rebalance/backtest.py's Sharpe/annualized-vol addition (review.md 4-2)."""

    def _curve(self, values):
        return [{"date": f"2025-01-{i+1:02d}", "value": v} for i, v in enumerate(values)]

    def test_empty_or_single_point_curve_is_zero(self):
        from strategy.rebalance.backtest import _sharpe_and_vol
        self.assertEqual(_sharpe_and_vol([]), (0.0, 0.0))
        self.assertEqual(_sharpe_and_vol(self._curve([100.0])), (0.0, 0.0))

    def test_flat_curve_has_zero_vol_and_zero_sharpe(self):
        from strategy.rebalance.backtest import _sharpe_and_vol
        vol, sharpe = _sharpe_and_vol(self._curve([100.0] * 5))
        self.assertEqual(vol, 0.0)
        self.assertEqual(sharpe, 0.0)  # zero-variance guard, not a division by zero

    def test_matches_hand_computed_annualization(self):
        import math
        import numpy as np
        from strategy.rebalance.backtest import _sharpe_and_vol, _REBALANCE_PERIODS_PER_YEAR

        values = [100.0, 105.0, 102.0, 108.0, 110.0]
        vol, sharpe = _sharpe_and_vol(self._curve(values))

        returns = np.diff(values) / np.array(values[:-1])
        std_ret = float(np.std(returns, ddof=1))
        expected_vol = std_ret * math.sqrt(_REBALANCE_PERIODS_PER_YEAR) * 100
        expected_sharpe = float(np.mean(returns)) / std_ret * math.sqrt(_REBALANCE_PERIODS_PER_YEAR)

        self.assertAlmostEqual(vol, expected_vol, places=8)
        self.assertAlmostEqual(sharpe, expected_sharpe, places=8)
        self.assertGreater(vol, 0.0)

    def test_summarize_backtest_includes_sharpe_and_vol(self):
        from strategy.rebalance.backtest import _summarize_backtest

        curve = self._curve([100.0, 105.0, 102.0, 108.0, 110.0])
        summary = _summarize_backtest(curve, [], 100.0, [], 5, 5)
        self.assertIn("sharpe", summary)
        self.assertIn("annual_vol_pct", summary)
        self.assertTrue(math.isfinite(summary["sharpe"]))
        self.assertGreater(summary["annual_vol_pct"], 0.0)

    def test_summarize_backtest_empty_curve_reports_zero_sharpe_and_vol(self):
        from strategy.rebalance.backtest import _summarize_backtest

        summary = _summarize_backtest([], [], 100.0, [], 0, 0)
        self.assertEqual(summary["sharpe"], 0.0)
        self.assertEqual(summary["annual_vol_pct"], 0.0)

    def test_summarize_backtest_matches_pre_migration_formula(self):
        """_summarize_backtest() delegates to strategy.metrics since Phase 2 of
        review_agy.md Section 4. Pin total return / CAGR / max drawdown to the
        formula it used before (a verbatim copy), including the conventions
        that differ from the shared calculator's own: total return and CAGR
        measured against initial_capital rather than the curve's first point
        (which is already net of the first rebalance's fees), drawdown as a
        NEGATIVE percentage (ui/dialogs/backtest_result.py shows it as-is),
        and CAGR 0.0 rather than -100% for a curve that ends at zero."""
        from datetime import date, datetime, timedelta
        from strategy.rebalance.backtest import _summarize_backtest

        initial = 100_000_000.0
        # First point below initial_capital, like a real curve after fees.
        values = [99_900_000.0, 104_000_000.0, 98_500_000.0, 110_200_000.0, 107_000_000.0, 121_000_000.0]
        curve = [{"date": (date(2025, 1, 3) + timedelta(weeks=i)).strftime("%Y-%m-%d"), "value": v}
                 for i, v in enumerate(values)]
        s = _summarize_backtest(curve, [], initial, [1.0, -2.0, 3.0], 6, 12, total_cost_paid=50_000.0)

        final = values[-1]
        n_days = (datetime.strptime(curve[-1]["date"], "%Y-%m-%d")
                  - datetime.strptime(curve[0]["date"], "%Y-%m-%d")).days
        expected_cagr = ((final / initial) ** (1 / (n_days / 365.25)) - 1) * 100
        peak, max_dd = values[0], 0.0
        for v in values:
            peak = max(peak, v)
            max_dd = min(max_dd, (v - peak) / peak * 100)

        self.assertAlmostEqual(s["total_return_pct"], (final / initial - 1) * 100, places=9)
        self.assertAlmostEqual(s["cagr_pct"], expected_cagr, places=9)
        self.assertAlmostEqual(s["max_drawdown_pct"], max_dd, places=9)
        self.assertLess(s["max_drawdown_pct"], 0.0)   # negative-sign convention preserved
        # The first point is below initial_capital but is the running peak's
        # starting point, so it must not register as a drawdown from capital.
        self.assertAlmostEqual(max_dd, (98_500_000.0 - 104_000_000.0) / 104_000_000.0 * 100, places=9)

        wiped = curve[:-1] + [{"date": curve[-1]["date"], "value": 0.0}]
        self.assertEqual(_summarize_backtest(wiped, [], initial, [], 6, 12)["cagr_pct"], 0.0)


if __name__ == "__main__":
    unittest.main()
