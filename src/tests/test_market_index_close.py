"""tests/test_market_index_close.py — get_index_close_for_date widens its history
window for dates older than the 410-day watchlist lookback (review_agy.md 2.3),
so the Total Assets tab's KOSPI columns survive the first snapshot ageing out."""
import unittest
from datetime import date, timedelta
from unittest.mock import patch

import polars as pl

import data.market as market


def _frame(start: date, n: int) -> pl.DataFrame:
    days = [start + timedelta(days=i) for i in range(n)]
    return pl.DataFrame({"Date": days, "Close": [1000.0 + i for i in range(n)]})


class TestIndexLookbackStart(unittest.TestCase):
    def test_recent_target_keeps_the_default_window(self):
        recent = date.today() - timedelta(days=30)
        self.assertEqual(market._index_lookback_start(recent), market.start_date())

    def test_old_target_starts_the_year_before(self):
        old = date.today() - timedelta(days=800)
        self.assertEqual(market._index_lookback_start(old), f"{old.year - 1}-01-01")
        self.assertLess(market._index_lookback_start(old), old.isoformat())


class TestGetIndexCloseForDate(unittest.TestCase):
    def test_old_date_is_fetched_with_a_wide_window_and_found(self):
        target = date.today() - timedelta(days=800)
        seen = {}

        def fake_hist(ticker, start):
            seen["start"] = start
            return _frame(date.fromisoformat(start), 5000)

        with patch("data.market.get_historical_data", side_effect=fake_hist):
            close = market.get_index_close_for_date("KS11", target.isoformat())
        self.assertEqual(seen["start"], f"{target.year - 1}-01-01")
        self.assertGreater(close, 0.0)
        expected = 1000.0 + (target - date.fromisoformat(seen["start"])).days
        self.assertEqual(close, expected)

    def test_recent_date_uses_the_shared_default_window(self):
        target = date.today() - timedelta(days=10)
        with patch("data.market.get_historical_data", return_value=_frame(target - timedelta(days=20), 40)) as h:
            close = market.get_index_close_for_date("KS11", target.isoformat())
        self.assertEqual(h.call_args.args[1], market.start_date())
        self.assertEqual(close, 1020.0)

    def test_bad_date_returns_zero_without_fetching(self):
        with patch("data.market.get_historical_data") as h:
            self.assertEqual(market.get_index_close_for_date("KS11", "not-a-date"), 0.0)
        h.assert_not_called()


if __name__ == "__main__":
    unittest.main()
