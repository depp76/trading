"""
tests/test_backtest.py — run_backtest_strategy 결과 일치 검증
"""
import os
import sys
import unittest
from datetime import date

_PROJ_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJ_ROOT)

import polars as pl
from data_fetcher import run_backtest_strategy


def _make_ohlcv(n, close_val=100.0):
    dates = [date(2023, 1, d + 1) for d in range(n)]
    closes = [close_val] * n
    return {"Date": dates, "Open": closes, "High": closes,
            "Low": closes, "Close": closes, "Volume": [1000] * n}


class TestBacktestEdgeCases(unittest.TestCase):

    def test_none_df_returns_zeros(self):
        self.assertEqual(run_backtest_strategy(None), (0, 0, 0.0))

    def test_empty_df_returns_zeros(self):
        self.assertEqual(run_backtest_strategy(pl.DataFrame()), (0, 0, 0.0))

    def test_missing_ma_columns_returns_zeros(self):
        df = pl.DataFrame(_make_ohlcv(30))
        self.assertEqual(run_backtest_strategy(df), (0, 0, 0.0))

    def test_returns_tuple_length_3(self):
        n = 5
        df = pl.DataFrame({**_make_ohlcv(n), "MA20": [100.0]*n, "MA60": [100.0]*n})
        self.assertEqual(len(run_backtest_strategy(df)), 3)

    def test_buy_sell_points_true_returns_length_8(self):
        n = 5
        df = pl.DataFrame({**_make_ohlcv(n), "MA20": [100.0]*n, "MA60": [100.0]*n})
        self.assertEqual(len(run_backtest_strategy(df, buy_sell_points=True)), 8)


class TestBacktestSignal(unittest.TestCase):

    def _make_golden_cross_df(self):
        n = 20
        dates = [date(2023, 1, d + 1) for d in range(n)]
        ma60 = [100.0] * n
        ma20 = [100.0] * 5 + [111.0] * (n - 5)
        close = [100.0] * n
        opens = [100.0] * 6 + [131.0] + [100.0] * (n - 7)
        return pl.DataFrame({"Date": dates, "Open": opens, "High": close,
                             "Low": close, "Close": close, "Volume": [1000]*n,
                             "MA20": ma20, "MA60": ma60})

    def test_no_signal_when_flat(self):
        n = 30
        df = pl.DataFrame({**_make_ohlcv(n), "MA20": [100.0]*n, "MA60": [100.0]*n})
        total, wins, cum_ret = run_backtest_strategy(df)
        self.assertEqual(total, 0)
        self.assertAlmostEqual(cum_ret, 0.0)

    def test_cumulative_return_type_is_float(self):
        n = 10
        df = pl.DataFrame({**_make_ohlcv(n), "MA20": [100.0]*n, "MA60": [100.0]*n})
        _, _, cum_ret = run_backtest_strategy(df)
        self.assertIsInstance(cum_ret, float)

    def test_win_count_le_total_trades(self):
        df = self._make_golden_cross_df()
        total, wins, _ = run_backtest_strategy(df)
        self.assertLessEqual(wins, total)
        self.assertGreaterEqual(wins, 0)

    def test_buy_sell_list_lengths_match(self):
        df = self._make_golden_cross_df()
        res = run_backtest_strategy(df, buy_sell_points=True)
        total = res[0]
        self.assertEqual(len(res[3]), total)  # buy_dates
        self.assertEqual(len(res[5]), total)  # sell_dates

    def test_target_year_2099_no_trades(self):
        n = 10
        df = pl.DataFrame({**_make_ohlcv(n), "MA20": [100.0]*n, "MA60": [100.0]*n})
        total, _, _ = run_backtest_strategy(df, target_year=2099)
        self.assertEqual(total, 0)

    def test_cumulative_return_equals_sum(self):
        df = self._make_golden_cross_df()
        res = run_backtest_strategy(df, buy_sell_points=True)
        total, _, cum_ret, _, b_prices, _, s_prices, _ = res
        if total > 0:
            manual_sum = sum((s - b) / b * 100 for b, s in zip(b_prices, s_prices))
            self.assertAlmostEqual(cum_ret, manual_sum, places=6)


class TestRebalanceFactorExtraction(unittest.TestCase):

    def test_negative_and_zero_per_filtered_out(self):
        from data_fetcher import _extract_live_candidates

        sample_universe = [
            {
                "ticker": "005930",
                "name": "Samsung",
                "market": "KOSPI",
                "is_index": False,
                "trailing_per": -15.5,  # negative PER (loss-making)
                "changes": {
                    "ma20_div": 105.0,
                    "ma50_div": 102.0,
                    "52w_high_diff": -5.0,
                    "20d": 3.5,
                    "60d": 8.0,
                },
            },
            {
                "ticker": "000660",
                "name": "SK Hynix",
                "market": "KOSPI",
                "is_index": False,
                "trailing_per": 12.0,  # positive PER
                "changes": {
                    "ma20_div": 108.0,
                    "ma50_div": 104.0,
                    "52w_high_diff": -2.0,
                    "20d": 5.0,
                    "60d": 12.0,
                },
            },
            {
                "ticker": "035420",
                "name": "NAVER",
                "market": "KOSPI",
                "is_index": False,
                "trailing_per": 0.0,  # zero PER
                "changes": {
                    "ma20_div": 98.0,
                    "ma50_div": 95.0,
                    "52w_high_diff": -15.0,
                    "20d": -2.0,
                    "60d": -5.0,
                },
            },
        ]

        candidates = _extract_live_candidates(sample_universe)
        self.assertEqual(len(candidates), 3)

        cand_map = {c["ticker"]: c for c in candidates}
        # Negative PER -> None
        self.assertIsNone(cand_map["005930"]["raw"]["value_per"])
        # Positive PER -> 12.0
        self.assertEqual(cand_map["000660"]["raw"]["value_per"], 12.0)
        # Zero PER -> None
        self.assertIsNone(cand_map["035420"]["raw"]["value_per"])


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
            top_n=1,
            band_multiplier=1.0,
            initial_capital=10_000_000.0,
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


