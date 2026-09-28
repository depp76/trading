"""tests/strategy/trend_following/test_data_adjusted.py — trend_following.md 3-1 data
checks against the live Naver feed. Needs the network, so it only runs when
RUN_NETWORK_TESTS=1 is set (the default suite is offline)."""
from datetime import date
import os
import unittest

from strategy.trend_following import abnormal_return_rows

NETWORK = os.environ.get("RUN_NETWORK_TESTS") == "1"


@unittest.skipUnless(NETWORK, "set RUN_NETWORK_TESTS=1 to run the live data checks")
class TestSamsungSplitAdjusted(unittest.TestCase):

    def test_2018_split_is_adjusted_and_ohlcv_is_complete(self):
        from data.history import get_historical_data
        df = get_historical_data("005930", "2018-04-01")
        self.assertFalse(df.is_empty())
        for col in ("Date", "Open", "High", "Low", "Close", "Volume"):
            self.assertIn(col, df.columns)
        window = df.filter((df["Date"] >= date(2018, 4, 20))
                           & (df["Date"] <= date(2018, 5, 20)))
        # a 50:1 split left unadjusted shows up as a ~-98% day; adjusted data stays inside the band
        self.assertEqual(abnormal_return_rows({"005930": window}, threshold=0.35), [])
        may4 = df.filter(df["Date"] == date(2018, 5, 4))
        self.assertEqual(may4.height, 1)
        self.assertLess(float(may4["Close"][0]), 100_000.0)          # post-split price level

    def test_kospi_index_history_available(self):
        from data.history import get_historical_data
        idx = get_historical_data("^KS11", "2016-01-01")
        self.assertGreater(idx.height, 2000)
        self.assertEqual(abnormal_return_rows({"^KS11": idx}, threshold=0.15), [])
