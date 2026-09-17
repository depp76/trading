"""tests/test_indicators.py — fetch_historical_changes modes and the small
data.cache helpers (is_kr_code, start_date), roadmap 6-3b.
"""
import os
import sys
import unittest
from datetime import date, datetime, timedelta

_PROJ_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJ_ROOT)

import polars as pl

from data.indicators import fetch_historical_changes
from data.cache import is_kr_code, start_date
from data.collectors.naver import _parse_marcap_krw


def _history(n=130, start_close=100.0, step=1.0):
    """n business-day rows ending today, Close rising by step per row."""
    today = date.today()
    dates = []
    d = today
    while len(dates) < n:
        if d.weekday() < 5:
            dates.append(d)
        d -= timedelta(days=1)
    dates.reverse()
    closes = [start_close + i * step for i in range(n)]
    return pl.DataFrame({"Date": dates, "Close": closes,
                         "Open": closes, "High": closes, "Low": closes,
                         "Volume": [1000.0] * n})


class TestFetchHistoricalChanges(unittest.TestCase):

    def setUp(self):
        self.df = _history()
        self.closes = self.df.get_column("Close").to_list()
        self.n = len(self.closes)

    def test_pct_mode(self):
        cp = self.closes[-1] + 10.0
        ch = fetch_historical_changes("T", cp, self.df, mode="pct")
        old_5d = self.closes[self.n - 1 - 5]
        self.assertAlmostEqual(ch["5d"], (cp - old_5d) / old_5d * 100)
        self.assertAlmostEqual(ch["52w_high"], max(self.closes))
        self.assertAlmostEqual(ch["52w_high_diff"], (cp - max(self.closes)) / max(self.closes) * 100)
        self.assertAlmostEqual(ch["ma20_div"], cp / (sum(self.closes[-20:]) / 20) * 100)
        self.assertAlmostEqual(ch["ma50_div"], cp / (sum(self.closes[-50:]) / 50) * 100)
        self.assertNotEqual(ch["ma20_roc_1w"], 0.0)

    def test_bp_mode_is_absolute_times_100(self):
        cp = self.closes[-1] + 0.25
        ch = fetch_historical_changes("KR3YT", cp, self.df, mode="bp")
        old_5d = self.closes[self.n - 1 - 5]
        self.assertAlmostEqual(ch["5d"], (cp - old_5d) * 100)
        self.assertAlmostEqual(ch["52w_high_diff"], (cp - max(self.closes)) * 100)
        self.assertAlmostEqual(ch["52w_low_diff"], (cp - min(self.closes)) * 100)
        # MA divergence is a pct-mode-only concept
        self.assertEqual(ch["ma20_div"], 0.0)
        self.assertEqual(ch["ma50_div"], 0.0)
        self.assertEqual(ch["ma20_roc_1w"], 0.0)

    def test_abs_mode_reports_reference_levels(self):
        cp = self.closes[-1] + 3.0
        ch = fetch_historical_changes("VIX", cp, self.df, mode="abs")
        # abs mode: period columns carry the past level itself (VIX/WTI display)
        self.assertAlmostEqual(ch["5d"], self.closes[self.n - 1 - 5])
        self.assertAlmostEqual(ch["52w_high_diff"], cp - max(self.closes))
        self.assertAlmostEqual(ch["52w_low_diff"], cp - min(self.closes))
        self.assertEqual(ch["ma20_div"], 0.0)

    def test_non_positive_price_returns_zeros_without_fetch(self):
        ch = fetch_historical_changes("T", 0.0, self.df)
        self.assertTrue(all(v == 0.0 for v in ch.values()))

    def test_short_history_skips_unavailable_windows(self):
        df = _history(n=8)
        ch = fetch_historical_changes("T", 200.0, df, mode="pct")
        self.assertNotEqual(ch["5d"], 0.0)
        self.assertEqual(ch["10d"], 0.0)
        self.assertEqual(ch["ma20_div"], 0.0)

    def test_empty_history(self):
        ch = fetch_historical_changes("T", 100.0, pl.DataFrame())
        self.assertEqual(ch["5d"], 0.0)
        self.assertEqual(ch["52w_high"], 0.0)


class TestCacheHelpers(unittest.TestCase):

    def test_is_kr_code(self):
        self.assertTrue(is_kr_code("005930"))
        self.assertTrue(is_kr_code("00088K"))   # letter suffix (preferred/ETN)
        self.assertFalse(is_kr_code("AAPL"))
        self.assertFalse(is_kr_code("BRK.B"))
        self.assertFalse(is_kr_code("^KS11"))
        self.assertFalse(is_kr_code("ABCDEF"))  # 6 chars but no digit
        self.assertFalse(is_kr_code(""))
        self.assertFalse(is_kr_code(None))

    def test_start_date_is_410_days_back(self):
        s = start_date()
        parsed = datetime.strptime(s, "%Y-%m-%d").date()
        self.assertEqual((date.today() - parsed).days, 410)

    def test_parse_marcap_krw(self):
        self.assertEqual(_parse_marcap_krw("12조3,456"), (12 * 10000 + 3456) * 100_000_000)
        self.assertEqual(_parse_marcap_krw("3,456"), 3456 * 100_000_000)
        self.assertEqual(_parse_marcap_krw("5조"), 5 * 10000 * 100_000_000)
        self.assertEqual(_parse_marcap_krw(""), 0)
        self.assertEqual(_parse_marcap_krw("-"), 0)


if __name__ == "__main__":
    unittest.main()
