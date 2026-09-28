"""tests/test_indicators.py — fetch_historical_changes modes and the small
data.cache helpers (is_kr_code, start_date), roadmap 6-3b.
"""
import unittest
from datetime import date, datetime, timedelta

import polars as pl

from unittest.mock import patch

from data.indicators import fetch_historical_changes, _current_session_index
from data.cache import is_kr_code, is_us_market, start_date
from data.collectors.naver import _parse_marcap_krw


def _history_ending(end, n=130, start_close=100.0, step=1.0):
    """n business-day rows ending on `end` (a date), Close rising by step per row."""
    dates = []
    d = end
    while len(dates) < n:
        if d.weekday() < 5:
            dates.append(d)
        d -= timedelta(days=1)
    dates.reverse()
    closes = [start_close + i * step for i in range(n)]
    return pl.DataFrame({"Date": dates, "Close": closes,
                         "Open": closes, "High": closes, "Low": closes,
                         "Volume": [1000.0] * n})


class TestCurrentSessionIndex(unittest.TestCase):
    """review_agy.md 2.3: which close plays 'the current session' for the
    N-session changes when the history may or may not have a bar for today."""

    MONDAY = datetime(2026, 9, 28, 10, 0)
    FRIDAY = date(2026, 9, 25)

    def test_last_bar_dated_today_is_current(self):
        self.assertEqual(_current_session_index(self.MONDAY.date(), 100.0, 101.0, 10, self.MONDAY), 9)

    def test_new_session_when_price_moved_since_last_bar(self):
        self.assertEqual(_current_session_index(self.FRIDAY, 100.0, 101.0, 10, self.MONDAY), 10)

    def test_no_trade_since_last_bar_keeps_it_current(self):
        # pre-open / holiday / US-hours instrument during the Korean day
        self.assertEqual(_current_session_index(self.FRIDAY, 100.0, 100.0, 10, self.MONDAY), 9)

    def test_float32_close_still_counts_as_equal(self):
        self.assertEqual(_current_session_index(self.FRIDAY, 512.3399963378906, 512.34, 10, self.MONDAY), 9)

    def test_weekend_keeps_last_bar_current(self):
        saturday = datetime(2026, 9, 26, 12, 0)
        self.assertEqual(_current_session_index(self.FRIDAY, 100.0, 101.0, 10, saturday), 9)


class TestFetchHistoricalChangesWithoutTodaysBar(unittest.TestCase):
    """fetch_historical_changes end to end when the history stops before today
    (review_agy.md 2.3). Before the fix 1d was measured against the bar *before*
    the last one, i.e. a two-session change."""

    MONDAY = datetime(2026, 9, 28, 10, 0)

    def setUp(self):
        self.df = _history_ending(date(2026, 9, 25))     # ends Friday
        self.closes = self.df.get_column("Close").to_list()
        self.n = len(self.closes)
        p = patch("data.indicators.datetime")
        mdt = p.start()
        self.addCleanup(p.stop)
        mdt.now.return_value = self.MONDAY

    def test_trading_session_compares_against_last_bar(self):
        cp = self.closes[-1] + 10.0
        ch = fetch_historical_changes("T", cp, self.df, mode="pct")
        last = self.closes[-1]
        self.assertAlmostEqual(ch["1d"], (cp - last) / last * 100)
        old_5d = self.closes[self.n - 5]
        self.assertAlmostEqual(ch["5d"], (cp - old_5d) / old_5d * 100)
        old_120d = self.closes[self.n - 120]
        self.assertAlmostEqual(ch["120d"], (cp - old_120d) / old_120d * 100)

    def test_pre_open_price_equal_to_last_close_reports_last_sessions_move(self):
        cp = self.closes[-1]
        ch = fetch_historical_changes("T", cp, self.df, mode="pct")
        prev = self.closes[-2]
        self.assertAlmostEqual(ch["1d"], (cp - prev) / prev * 100)
        old_5d = self.closes[self.n - 1 - 5]
        self.assertAlmostEqual(ch["5d"], (cp - old_5d) / old_5d * 100)

    def test_short_history_new_session_uses_last_bar_for_1d_only(self):
        df = _history_ending(date(2026, 9, 25), n=3)
        closes = df.get_column("Close").to_list()
        ch = fetch_historical_changes("T", closes[-1] + 1.0, df, mode="pct")
        self.assertNotEqual(ch["1d"], 0.0)
        self.assertNotEqual(ch["3d"], 0.0)      # closes[3 - 3] exists
        self.assertEqual(ch["5d"], 0.0)


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

    def test_abs_mode_reports_level_deltas(self):
        cp = self.closes[-1] + 3.0
        ch = fetch_historical_changes("VIX", cp, self.df, mode="abs")
        # abs mode: period columns are the change in the instrument's own unit
        self.assertAlmostEqual(ch["5d"], cp - self.closes[self.n - 1 - 5])
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

    def test_is_us_market(self):
        for m in ("US", "NASDAQ", "NYSE", "AMEX", "NASDAQ 100", "S&P500", "S&P 500"):
            self.assertTrue(is_us_market(m), m)
        for m in ("KOSPI", "KOSDAQ", "KRX", "ETF", "", None, "Index"):
            self.assertFalse(is_us_market(m), repr(m))
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
