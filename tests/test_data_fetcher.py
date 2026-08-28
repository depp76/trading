"""
tests/test_data_fetcher.py — LRU 캐시, yf_quote_batch (mock 기반) 테스트
"""
import os
import sys
import unittest
from unittest.mock import patch, MagicMock
from collections import OrderedDict
from datetime import date as _date

_PROJ_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJ_ROOT)

import data_fetcher


class TestHistCache(unittest.TestCase):

    def setUp(self):
        data_fetcher._HIST_CACHE.clear()
        data_fetcher._HIST_CACHE_HITS = 0
        data_fetcher._HIST_CACHE_MISSES = 0

    def _make_polars_df(self, n=5):
        import polars as pl
        from datetime import date
        dates = [date(2024, 1, d) for d in range(1, n+1)]
        closes = [100.0 + d for d in range(n)]
        return pl.DataFrame({"Date": dates, "Close": closes,
                             "Open": closes, "High": closes,
                             "Low": closes, "Volume": [1000]*n})

    @patch("data_fetcher._fetch_historical_uncached")
    def test_cache_miss_on_first_call(self, mock_fetch):
        mock_fetch.return_value = self._make_polars_df()
        data_fetcher.get_historical_data("005930", "2024-01-01")
        self.assertEqual(data_fetcher._HIST_CACHE_MISSES, 1)
        self.assertEqual(data_fetcher._HIST_CACHE_HITS, 0)

    @patch("data_fetcher._fetch_historical_uncached")
    def test_cache_hit_on_second_call(self, mock_fetch):
        df = self._make_polars_df()
        mock_fetch.return_value = df
        with patch("data_fetcher._hist_df_is_stale", return_value=False):
            data_fetcher.get_historical_data("005930", "2024-01-01")
            data_fetcher.get_historical_data("005930", "2024-01-01")
        self.assertEqual(data_fetcher._HIST_CACHE_HITS, 1)
        mock_fetch.assert_called_once()

    @patch("data_fetcher._fetch_historical_uncached")
    def test_stale_cache_triggers_refetch(self, mock_fetch):
        df = self._make_polars_df()
        mock_fetch.return_value = df
        with patch("data_fetcher._hist_df_is_stale", return_value=True):
            data_fetcher.get_historical_data("005930", "2024-01-01")
            data_fetcher.get_historical_data("005930", "2024-01-01")
        self.assertEqual(mock_fetch.call_count, 2)

    @patch("data_fetcher._fetch_historical_uncached")
    def test_empty_df_not_cached(self, mock_fetch):
        import polars as pl
        mock_fetch.return_value = pl.DataFrame()
        data_fetcher.get_historical_data("INVALID", "2024-01-01")
        self.assertNotIn(("INVALID", "2024-01-01"), data_fetcher._HIST_CACHE)

    @patch("data_fetcher._fetch_historical_uncached")
    def test_lru_eviction_at_max(self, mock_fetch):
        df = self._make_polars_df()
        mock_fetch.return_value = df
        orig_max = data_fetcher._HIST_CACHE_MAX
        data_fetcher._HIST_CACHE_MAX = 3
        try:
            with patch("data_fetcher._hist_df_is_stale", return_value=False):
                for i in range(4):
                    data_fetcher.get_historical_data(f"TICKER{i}", "2024-01-01")
            self.assertLessEqual(len(data_fetcher._HIST_CACHE), 3)
        finally:
            data_fetcher._HIST_CACHE_MAX = orig_max


class TestLogHistCacheStats(unittest.TestCase):

    def setUp(self):
        data_fetcher._HIST_CACHE_HITS = 0
        data_fetcher._HIST_CACHE_MISSES = 0

    def test_logs_at_interval(self):
        interval = data_fetcher._HIST_CACHE_LOG_INTERVAL
        with patch.object(data_fetcher.logger, "info") as mock_log:
            data_fetcher._HIST_CACHE_HITS = interval - 1
            data_fetcher._log_hist_cache_stats()
            mock_log.assert_not_called()
            data_fetcher._HIST_CACHE_HITS = interval
            data_fetcher._log_hist_cache_stats()
            mock_log.assert_called_once()

    def test_no_log_when_total_zero(self):
        with patch.object(data_fetcher.logger, "info") as mock_log:
            data_fetcher._log_hist_cache_stats()
            mock_log.assert_not_called()


class TestYfQuoteBatch(unittest.TestCase):

    def _make_response(self, symbols):
        items = [{"symbol": s, "regularMarketPrice": 100.0} for s in symbols]
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"quoteResponse": {"result": items}}
        resp.raise_for_status = MagicMock()
        return resp

    @patch("data_fetcher._get_yf_crumb", return_value="test_crumb")
    @patch.object(data_fetcher._YF_SESSION, "get")
    def test_single_chunk(self, mock_get, mock_crumb):
        symbols = ["AAPL", "MSFT", "GOOGL"]
        mock_get.return_value = self._make_response(symbols)
        result = data_fetcher.yf_quote_batch(symbols)
        self.assertEqual(set(result.keys()), set(symbols))
        mock_get.assert_called_once()

    @patch("data_fetcher._get_yf_crumb", return_value="test_crumb")
    @patch.object(data_fetcher._YF_SESSION, "get")
    def test_empty_symbols(self, mock_get, mock_crumb):
        result = data_fetcher.yf_quote_batch([])
        self.assertEqual(result, {})
        mock_get.assert_not_called()

    @patch("data_fetcher._get_yf_crumb", return_value="crumb")
    @patch.object(data_fetcher._YF_SESSION, "get")
    def test_returns_symbol_keyed_dict(self, mock_get, mock_crumb):
        mock_get.return_value = self._make_response(["TSLA"])
        result = data_fetcher.yf_quote_batch(["TSLA"])
        self.assertIn("TSLA", result)

    @patch("data_fetcher._get_yf_crumb", return_value="crumb")
    @patch.object(data_fetcher._YF_SESSION, "get")
    def test_network_error_returns_empty(self, mock_get, mock_crumb):
        mock_get.side_effect = Exception("Connection error")
        result = data_fetcher.yf_quote_batch(["AAPL"])
        self.assertEqual(result, {})


if __name__ == "__main__":
    unittest.main()
