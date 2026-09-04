"""
tests/rebalance/test_walkforward.py — data/rebalance/walkforward.py 거래비용 검증 (trading.md 11-3)
"""
import os
import sys
import unittest
from datetime import date

_PROJ_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _PROJ_ROOT)

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
        from data_fetcher import _run_walkforward_simulation, _summarize_backtest
        from datetime import date

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


if __name__ == "__main__":
    unittest.main()
