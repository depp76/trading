"""tests/test_kis_realtime.py — KRX trading-hours guard and the KIS realtime price
path (WebSocket bypass outside market hours, REST fallback worker)."""
import unittest
from unittest.mock import patch
from datetime import datetime

from data.collectors.kis import is_krx_market_open, fetch_kis_realtime_prices, _kis_rest_price_fallback, _KST


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

    @patch("data.collectors.kis.is_krx_market_open", return_value=True)
    @patch("data.collectors.kis._get_kis_approval_key")
    @patch("data.collectors.kis._kis_rest_price_fallback")
    def test_few_tickers_skip_the_websocket_during_market_hours(self, mock_fallback, mock_key, _open):
        mock_fallback.return_value = {"005930": 70000.0, "000660": 200000.0}
        result = fetch_kis_realtime_prices(["005930", "000660"])
        mock_key.assert_not_called()          # no WebSocket handshake attempted
        mock_fallback.assert_called_once_with(["005930", "000660"], {})
        self.assertEqual(result, {"005930": 70000.0, "000660": 200000.0})

    @patch("data.collectors.kis.fetch_kis_stock_info")
    def test_rest_fallback_worker(self, mock_info):
        mock_info.side_effect = lambda code: {"price": 75000} if code == "005930" else None
        res = _kis_rest_price_fallback(["005930", "000660"], {})
        self.assertEqual(res, {"005930": 75000.0})


if __name__ == "__main__":
    unittest.main()
