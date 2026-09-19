"""tests/test_frames.py — data.frames._to_polars: the pandas -> polars boundary
every history fetch passes through (index naming, tz stripping, de-dup, sort)."""
import unittest
from datetime import date

import pandas as pd
import polars as pl

from data.frames import _to_polars


class TestToPolars(unittest.TestCase):

    def test_none_and_empty_become_empty_frame(self):
        self.assertTrue(_to_polars(None).is_empty())
        self.assertTrue(_to_polars(pd.DataFrame()).is_empty())

    def test_polars_input_is_returned_as_is(self):
        df = pl.DataFrame({"Date": [date(2026, 1, 1)], "Close": [1.0]})
        self.assertIs(_to_polars(df), df)

    def test_datetime_index_becomes_date_column(self):
        idx = pd.to_datetime(["2026-01-02", "2026-01-01"])
        out = _to_polars(pd.DataFrame({"Close": [2.0, 1.0]}, index=idx))
        self.assertIn("Date", out.columns)
        self.assertEqual(out["Date"].dtype, pl.Date)
        # sorted ascending by Date
        self.assertEqual(out["Date"].to_list(), [date(2026, 1, 1), date(2026, 1, 2)])
        self.assertEqual(out["Close"].to_list(), [1.0, 2.0])

    def test_named_date_index_is_renamed(self):
        idx = pd.Index(pd.to_datetime(["2026-03-01"]), name="date")
        out = _to_polars(pd.DataFrame({"Close": [5.0]}, index=idx))
        self.assertEqual(out.columns, ["Date", "Close"])

    def test_timezone_is_stripped(self):
        idx = pd.to_datetime(["2026-01-01 09:30", "2026-01-02 09:30"]).tz_localize("America/New_York")
        out = _to_polars(pd.DataFrame({"Close": [1.0, 2.0]}, index=idx))
        self.assertEqual(out["Date"].dtype, pl.Date)
        self.assertEqual(out["Date"].to_list(), [date(2026, 1, 1), date(2026, 1, 2)])

    def test_duplicate_dates_keep_last_row(self):
        idx = pd.to_datetime(["2026-01-01", "2026-01-01", "2026-01-02"])
        out = _to_polars(pd.DataFrame({"Close": [1.0, 1.5, 2.0]}, index=idx))
        self.assertEqual(out["Close"].to_list(), [1.5, 2.0])

    def test_string_date_column_is_cast(self):
        pdf = pd.DataFrame({"Date": ["2026-02-02", "2026-02-01"], "Close": [2.0, 1.0]}).set_index("Date")
        out = _to_polars(pdf)
        self.assertEqual(out["Date"].dtype, pl.Date)
        self.assertEqual(out["Date"].to_list(), [date(2026, 2, 1), date(2026, 2, 2)])


if __name__ == "__main__":
    unittest.main()
