"""
tests/test_assets_calc.py — 환율 캐시, safe_float, 입력 유효성 검사 테스트
"""
import os
import sys
import unittest
import datetime as _dt
from unittest.mock import patch

_PROJ_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJ_ROOT)

import data_fetcher


class TestUsdKrwCache(unittest.TestCase):

    def setUp(self):
        data_fetcher._USD_KRW_CACHE["rate"] = None
        data_fetcher._USD_KRW_CACHE["df"] = None

    @patch("data_fetcher.fdr")
    def test_cached_rate_is_reused(self, mock_fdr):
        data_fetcher._USD_KRW_CACHE["rate"] = 1350.0
        rate = data_fetcher.get_usd_krw_rate()
        self.assertAlmostEqual(rate, 1350.0)
        mock_fdr.DataReader.assert_not_called()

    @patch("data_fetcher.fdr")
    def test_fallback_on_fdr_error(self, mock_fdr):
        mock_fdr.DataReader.side_effect = Exception("Network error")
        rate = data_fetcher.get_usd_krw_rate()
        self.assertIsInstance(rate, float)
        self.assertGreater(rate, 0)

    def test_returns_positive_float_from_cache(self):
        data_fetcher._USD_KRW_CACHE["rate"] = 1380.5
        rate = data_fetcher.get_usd_krw_rate()
        self.assertGreater(rate, 0)


class TestSafeFloat(unittest.TestCase):

    def test_valid_number(self):
        self.assertAlmostEqual(data_fetcher.safe_float("1234.5"), 1234.5)

    def test_none_returns_default(self):
        self.assertAlmostEqual(data_fetcher.safe_float(None), 0.0)

    def test_empty_string_returns_default(self):
        self.assertAlmostEqual(data_fetcher.safe_float(""), 0.0)

    def test_custom_default(self):
        self.assertAlmostEqual(data_fetcher.safe_float(None, default=-1.0), -1.0)

    def test_already_float(self):
        self.assertAlmostEqual(data_fetcher.safe_float(3.14), 3.14)

    def test_non_numeric_string_returns_default(self):
        self.assertAlmostEqual(data_fetcher.safe_float("abc"), 0.0)


class TestValidateDateStr(unittest.TestCase):
    """_validate_date_str 로직을 직접 인라인으로 검증합니다."""

    def _validate_date_str(self, text):
        text = text.strip()
        if not text:
            return False
        try:
            _dt.datetime.strptime(text, "%Y-%m-%d")
            return True
        except ValueError:
            return False

    def test_valid_date(self):
        self.assertTrue(self._validate_date_str("2024-01-15"))

    def test_empty_string_invalid(self):
        self.assertFalse(self._validate_date_str(""))

    def test_whitespace_only_invalid(self):
        self.assertFalse(self._validate_date_str("   "))

    def test_wrong_format_slash(self):
        self.assertFalse(self._validate_date_str("2024/01/15"))

    def test_invalid_month(self):
        self.assertFalse(self._validate_date_str("2024-13-01"))

    def test_invalid_day(self):
        self.assertFalse(self._validate_date_str("2024-01-32"))

    def test_partial_date_invalid(self):
        self.assertFalse(self._validate_date_str("2024-01"))

    def test_leading_trailing_whitespace_stripped(self):
        self.assertTrue(self._validate_date_str("  2024-06-01  "))


class TestValidatePositiveNumber(unittest.TestCase):

    def _validate_positive_number(self, text):
        text = text.replace(",", "").replace("%", "").strip()
        if not text:
            return False
        try:
            return float(text) > 0
        except ValueError:
            return False

    def test_positive_integer(self):
        self.assertTrue(self._validate_positive_number("100"))

    def test_positive_float(self):
        self.assertTrue(self._validate_positive_number("3.14"))

    def test_zero_invalid(self):
        self.assertFalse(self._validate_positive_number("0"))

    def test_negative_invalid(self):
        self.assertFalse(self._validate_positive_number("-1"))

    def test_empty_invalid(self):
        self.assertFalse(self._validate_positive_number(""))

    def test_comma_separated_number(self):
        self.assertTrue(self._validate_positive_number("1,000"))

    def test_percent_stripped(self):
        self.assertTrue(self._validate_positive_number("50%"))

    def test_non_numeric_invalid(self):
        self.assertFalse(self._validate_positive_number("abc"))

    def test_very_small_positive(self):
        self.assertTrue(self._validate_positive_number("0.001"))


class TestBuySellDateCrossValidation(unittest.TestCase):

    def _buy_before_sell(self, buy, sell):
        try:
            buy_d = _dt.datetime.strptime(buy, "%Y-%m-%d").date()
            sell_d = _dt.datetime.strptime(sell, "%Y-%m-%d").date()
            return buy_d < sell_d
        except ValueError:
            return False

    def test_buy_before_sell_valid(self):
        self.assertTrue(self._buy_before_sell("2024-01-01", "2024-06-01"))

    def test_buy_same_as_sell_invalid(self):
        self.assertFalse(self._buy_before_sell("2024-01-01", "2024-01-01"))

    def test_buy_after_sell_invalid(self):
        self.assertFalse(self._buy_before_sell("2024-06-01", "2024-01-01"))

    def test_empty_sell_date(self):
        self.assertFalse(self._buy_before_sell("2024-01-01", ""))


if __name__ == "__main__":
    unittest.main()
