"""tests/test_history_bonds.py — KR3YT history is served from the cached Naver
series instead of re-scraping (data.history routing)."""
import unittest
from unittest.mock import patch

import pandas as pd
import polars as pl


class TestKr3ytScrapingOptimization(unittest.TestCase):
    """Test KR3YT historical data fetch optimization."""

    @patch("data.history._get_kr3y_df")
    def test_kr3yt_historical_uses_cached_df(self, mock_get_kr3y):
        dates = pd.date_range("2024-01-01", "2024-01-05")
        mock_df = pd.DataFrame({"Close": [3.5, 3.52, 3.48, 3.51, 3.49]}, index=dates)
        mock_get_kr3y.return_value = mock_df

        from data.history import _fetch_historical_uncached
        df = _fetch_historical_uncached("KR3YT", "2024-01-03")
        mock_get_kr3y.assert_called_once()
        self.assertIsInstance(df, pl.DataFrame)
        self.assertFalse(df.is_empty())
        # Filtered to >= 2024-01-03
        self.assertEqual(len(df), 3)


if __name__ == "__main__":
    unittest.main()
