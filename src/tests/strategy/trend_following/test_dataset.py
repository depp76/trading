"""dataset.build_dataset alignment, warm-up / start index, flows and rate handling."""
import unittest
from datetime import date

import numpy as np
import polars as pl

from strategy.trend_following.dataset import build_dataset, align_frame, PriceBook
from tests.strategy.trend_following.helpers import business_days, make_frame, synthetic_dataset


class TestAlignment(unittest.TestCase):
    def test_align_frame_fills_missing_dates_with_nan(self):
        days = business_days(date(2024, 1, 2), 5)
        df = pl.DataFrame({"Date": [days[0], days[2]], "Close": [1.0, 3.0]})
        out = align_frame(df, days, ["Close", "Volume"])
        self.assertEqual(out["Close"][0], 1.0)
        self.assertTrue(np.isnan(out["Close"][1]))
        self.assertEqual(out["Close"][2], 3.0)
        self.assertTrue(np.isnan(out["Volume"]).all())

    def test_pricebook_from_frames(self):
        days = business_days(date(2024, 1, 2), 30)
        frames = {"A": make_frame(days, np.linspace(100, 130, 30)), "B": make_frame(days[5:], np.linspace(50, 60, 25))}
        book = PriceBook.from_frames(frames, days, {"A": "KOSPI", "B": "KOSDAQ"})
        self.assertEqual((book.T, book.N), (30, 2))
        self.assertTrue(np.isnan(book.close[:5, book.col("B")]).all())
        self.assertEqual(book.market_of("B"), "KOSDAQ")
        self.assertEqual(book.market_of("A"), "KOSPI")


class TestBuildDataset(unittest.TestCase):
    def test_start_index_and_rf_period(self):
        ds = synthetic_dataset()
        self.assertEqual(ds.dates[ds.start_idx], date(2021, 1, 4))
        self.assertGreater(ds.start_idx, 200)
        self.assertEqual(ds.rf_period[0], 0.0)
        # a Monday row earns three calendar days of interest
        mondays = [i for i, d in enumerate(ds.dates) if d.weekday() == 0 and i > 0]
        i = mondays[0]
        self.assertAlmostEqual(ds.rf_period[i], 0.02 * (ds.dates[i] - ds.dates[i - 1]).days / 365)
        self.assertEqual(ds.info["n_tickers"], 4)
        self.assertTrue(ds.info["bm_is_total_return"])

    def test_flows_become_krw_amounts(self):
        ds = synthetic_dataset(with_flows=True)
        j = ds.stocks.col("UP")
        self.assertAlmostEqual(ds.flow_fi[10, j], 500.0 * ds.stocks.close[10, j])
        self.assertAlmostEqual(ds.flow_retail[10, j], -500.0 * ds.stocks.close[10, j])
        self.assertTrue(ds.info["has_flows"])

    def test_constant_rate_fallback_and_no_bm(self):
        days = business_days(date(2020, 6, 1), 300)
        frames = {"A": make_frame(days, np.linspace(1000, 2000, 300))}
        ds = build_dataset(days, np.linspace(100, 110, 300), frames, date(2021, 1, 4), rf_fallback=0.05)
        self.assertIsNone(ds.bm)
        self.assertTrue(np.allclose(ds.rf_annual, 0.05))
        self.assertIn("constant", ds.rf_source)

    def test_start_after_calendar_raises(self):
        days = business_days(date(2020, 6, 1), 30)
        frames = {"A": make_frame(days, np.linspace(1000, 2000, 30))}
        with self.assertRaises(ValueError):
            build_dataset(days, np.ones(30), frames, date(2025, 1, 1))


if __name__ == "__main__":
    unittest.main()
