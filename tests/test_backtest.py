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


if __name__ == "__main__":
    unittest.main()
