"""tests/strategy/trend_following/test_universe.py — yearly candidate selection
(trend_following.md 2-1): no dependence on data after the selection date, the
trading-value and listing-age floors, and the UniverseTab.all_data filter."""
from datetime import date
import unittest

import polars as pl

from strategy.trend_following import KrTrendConfig, average_trading_value, kr_universe_candidates, yearly_members
from tests.strategy.trend_following.v1_frames import bdays, ohlcv

CFG = KrTrendConfig(universe_top_m=2, trading_value_n=5, min_trading_value=1_000_000, min_history_days=8)


def _frame(n_days, close, volume, start=date(2024, 12, 2)):
    d = bdays(start, n_days)
    return ohlcv(d, [close] * n_days, volumes=[volume] * n_days)


class TestYearlyMembers(unittest.TestCase):

    def test_ranks_by_average_trading_value(self):
        hist = {
            "A": _frame(20, 1000, 5000),     # 5.0M / day
            "B": _frame(20, 2000, 5000),     # 10.0M / day
            "C": _frame(20, 1000, 2000),     # 2.0M / day
        }
        self.assertEqual(yearly_members(hist, 2025, CFG), ["B", "A"])

    def test_future_data_does_not_change_selection(self):
        base = {"A": _frame(20, 1000, 5000), "B": _frame(20, 2000, 5000), "C": _frame(20, 1000, 2000)}
        before = yearly_members(base, 2025, CFG)
        # C explodes in 2025: irrelevant to the selection made before Jan 1, 2025
        future = ohlcv(bdays(date(2025, 1, 2), 30), [10_000] * 30, volumes=[9_000_000] * 30)
        with_future = dict(base)
        with_future["C"] = pl.concat([base["C"], future])
        self.assertEqual(yearly_members(with_future, 2025, CFG), before)

    def test_liquidity_floor(self):
        hist = {"A": _frame(20, 100, 5000), "B": _frame(20, 2000, 5000)}      # A: 0.5M < 1M floor
        self.assertEqual(yearly_members(hist, 2025, CFG), ["B"])

    def test_listing_age_floor(self):
        young = _frame(6, 5000, 5000, start=date(2024, 12, 20))               # only 6 rows before Jan 1
        hist = {"A": young, "B": _frame(20, 2000, 5000)}
        self.assertEqual(yearly_members(hist, 2025, CFG), ["B"])

    def test_missing_volume_is_excluded(self):
        hist = {"A": _frame(20, 1000, 5000).drop("Volume"), "B": _frame(20, 2000, 5000)}
        self.assertEqual(yearly_members(hist, 2025, CFG), ["B"])

    def test_fixed_mode_keeps_everyone(self):
        cfg = KrTrendConfig(universe_mode="fixed")
        hist = {"A": _frame(3, 1, 1), "B": _frame(3, 1, 1), "C": pl.DataFrame()}
        self.assertEqual(yearly_members(hist, 2025, cfg), ["A", "B"])

    def test_average_trading_value_uses_rows_before_as_of_only(self):
        df = ohlcv(bdays(date(2024, 12, 23), 8), [100, 100, 100, 100, 100, 999, 999, 999],
                   volumes=[10, 10, 10, 10, 10, 10, 10, 10])
        value, count = average_trading_value(df, date(2024, 12, 30), 5)
        self.assertEqual(count, 5)
        self.assertAlmostEqual(value, 1000.0)
        self.assertEqual(average_trading_value(df, date(2024, 12, 24), 5), (None, 1))


class TestKrUniverseCandidates(unittest.TestCase):

    def test_filters_indices_bonds_and_non_kr(self):
        data = [
            {"ticker": "^KS11", "is_index": True},
            {"ticker": "KR3YT", "is_bond": True},
            {"ticker": "CL=F", "is_index": True},
            {"ticker": "005930", "market": "KOSPI"},
            {"ticker": "AAPL", "market": "US"},
            {"ticker": "035720", "market": "KOSDAQ"},
            {"ticker": "005930", "market": "KOSPI"},     # duplicate
            {"ticker": "", "market": "KOSPI"},
        ]
        self.assertEqual(kr_universe_candidates(data), ["005930", "035720"])

    def test_empty_input(self):
        self.assertEqual(kr_universe_candidates(None), [])
        self.assertEqual(kr_universe_candidates([]), [])
