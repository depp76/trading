"""tests/test_phase2.py — Unit tests for Phase 2 UI responsiveness and data performance optimizations."""
import os
import sys
import unittest
from unittest.mock import patch, MagicMock
from datetime import datetime

_PROJ_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJ_ROOT)

import polars as pl
import pandas as pd
from PyQt6.QtWidgets import QApplication, QTableWidget

from data.collectors.kis import is_krx_market_open, fetch_kis_realtime_prices, _kis_rest_price_fallback, _KST
from threads.fetch_threads import GeminiFilterThread, GeminiDiagnosisThread
from ui.auto_trading_tab import AutoTradingTab


# Ensure QApplication exists for Qt widget/thread tests
_qapp = QApplication.instance() or QApplication([])


class TestKrxMarketHours(unittest.TestCase):
    """Test KRX trading hours detection."""

    def test_weekend_saturday_is_closed(self):
        # 2026-09-12 is Saturday
        mock_dt = datetime(2026, 9, 12, 11, 0, 0, tzinfo=_KST)
        with patch("data.collectors.kis.datetime") as mock_datetime:
            mock_datetime.now.return_value = mock_dt
            self.assertFalse(is_krx_market_open())

    def test_weekend_sunday_is_closed(self):
        # 2026-09-13 is Sunday
        mock_dt = datetime(2026, 9, 13, 11, 0, 0, tzinfo=_KST)
        with patch("data.collectors.kis.datetime") as mock_datetime:
            mock_datetime.now.return_value = mock_dt
            self.assertFalse(is_krx_market_open())

    def test_weekday_before_market_open_is_closed(self):
        # Weekday (Friday) at 08:59:59 KST
        mock_dt = datetime(2026, 9, 11, 8, 59, 59, tzinfo=_KST)
        with patch("data.collectors.kis.datetime") as mock_datetime:
            mock_datetime.now.return_value = mock_dt
            self.assertFalse(is_krx_market_open())

    def test_weekday_during_trading_hours_is_open(self):
        # Weekday (Friday) at 10:30:00 KST
        mock_dt = datetime(2026, 9, 11, 10, 30, 0, tzinfo=_KST)
        with patch("data.collectors.kis.datetime") as mock_datetime:
            mock_datetime.now.return_value = mock_dt
            self.assertTrue(is_krx_market_open())

    def test_weekday_at_market_close_is_open(self):
        # Weekday (Friday) at 15:30:00 KST
        mock_dt = datetime(2026, 9, 11, 15, 30, 0, tzinfo=_KST)
        with patch("data.collectors.kis.datetime") as mock_datetime:
            mock_datetime.now.return_value = mock_dt
            self.assertTrue(is_krx_market_open())

    def test_weekday_after_market_close_is_closed(self):
        # Weekday (Friday) at 15:30:01 KST
        mock_dt = datetime(2026, 9, 11, 15, 30, 1, tzinfo=_KST)
        with patch("data.collectors.kis.datetime") as mock_datetime:
            mock_datetime.now.return_value = mock_dt
            self.assertFalse(is_krx_market_open())


class TestKisRealtimePriceFallback(unittest.TestCase):
    """Test KIS realtime price fetching and market-hours guard."""

    def test_empty_tickers_returns_empty_dict(self):
        result = fetch_kis_realtime_prices([])
        self.assertEqual(result, {})

    @patch("data.collectors.kis.is_krx_market_open", return_value=False)
    @patch("data.collectors.kis._kis_rest_price_fallback")
    def test_closed_market_bypasses_websocket(self, mock_fallback, mock_open):
        mock_fallback.return_value = {"005930": 70000.0}
        result = fetch_kis_realtime_prices(["005930"])
        mock_open.assert_called_once()
        mock_fallback.assert_called_once_with(["005930"], {})
        self.assertEqual(result, {"005930": 70000.0})

    @patch("data.collectors.kis.fetch_kis_stock_info")
    def test_rest_fallback_worker(self, mock_info):
        mock_info.side_effect = lambda code: {"price": 75000} if code == "005930" else None
        res = _kis_rest_price_fallback(["005930", "000660"], {})
        self.assertEqual(res, {"005930": 75000.0})


class TestKr3ytScrapingOptimization(unittest.TestCase):
    """Test KR3YT historical data fetch optimization."""

    @patch("data.market._get_kr3y_df")
    def test_kr3yt_historical_uses_cached_df(self, mock_get_kr3y):
        dates = pd.date_range("2024-01-01", "2024-01-05")
        mock_df = pd.DataFrame({"Close": [3.5, 3.52, 3.48, 3.51, 3.49]}, index=dates)
        mock_get_kr3y.return_value = mock_df

        from data.market import _fetch_historical_uncached
        df = _fetch_historical_uncached("KR3YT", "2024-01-03")
        mock_get_kr3y.assert_called_once()
        self.assertIsInstance(df, pl.DataFrame)
        self.assertFalse(df.is_empty())
        # Filtered to >= 2024-01-03
        self.assertEqual(len(df), 3)


class TestGeminiBackgroundThreads(unittest.TestCase):
    """Test asynchronous Gemini QThread execution."""

    @patch("gemini_helper.nl_to_filter")
    def test_gemini_filter_thread_success(self, mock_nl_to_filter):
        mock_nl_to_filter.return_value = {
            "text_filter": "Samsung",
            "conditions": [{"col": 7, "op": ">=", "val": 70000}],
            "explanation": "Price >= 70000",
        }
        thread = GeminiFilterThread("삼성전자 7만원 이상")
        results = []

        def _on_finished(res, err):
            results.append((res, err))

        thread.finished.connect(_on_finished)
        thread.run()

        self.assertEqual(len(results), 1)
        res, err = results[0]
        self.assertIsNotNone(res)
        self.assertEqual(err, "")
        self.assertEqual(res["text_filter"], "Samsung")

    @patch("gemini_helper.nl_to_filter", return_value=None)
    def test_gemini_filter_thread_none_result(self, mock_nl_to_filter):
        thread = GeminiFilterThread("invalid query")
        results = []

        def _on_finished(res, err):
            results.append((res, err))

        thread.finished.connect(_on_finished)
        thread.run()

        self.assertEqual(len(results), 1)
        res, err = results[0]
        self.assertIsNone(res)
        self.assertIn("failed", err)

    @patch("gemini_helper.portfolio_diagnosis")
    def test_gemini_diagnosis_thread_success(self, mock_diag):
        mock_diag.return_value = "Portfolio analysis report"
        thread = GeminiDiagnosisThread([], [])
        results = []

        def _on_finished(text, err):
            results.append((text, err))

        thread.finished.connect(_on_finished)
        thread.run()

        self.assertEqual(len(results), 1)
        text, err = results[0]
        self.assertEqual(text, "Portfolio analysis report")
        self.assertEqual(err, "")


class TestAutoTradingTabRepaintOptimization(unittest.TestCase):
    """Test AutoTradingTab batch table population repaint optimization."""

    def test_fill_candidate_table_set_updates_enabled(self):
        tbl = QTableWidget(0, 5)
        # Mock setUpdatesEnabled to track calls
        tbl.setUpdatesEnabled = MagicMock()
        rows = [
            {"rank": 1, "ticker": "005930", "name": "Samsung", "market": "KOSPI", "score": 88.5},
        ]
        AutoTradingTab._fill_candidate_table(tbl, rows)

        # Ensure called with False first, then True
        tbl.setUpdatesEnabled.assert_any_call(False)
        tbl.setUpdatesEnabled.assert_any_call(True)
        self.assertEqual(tbl.setUpdatesEnabled.call_count, 2)
        self.assertEqual(tbl.rowCount(), 1)


if __name__ == "__main__":
    unittest.main()
