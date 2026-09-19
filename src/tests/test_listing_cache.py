"""tests/test_listing_cache.py — data.listing._singleflight_cache: day-scoped
memoisation, concurrent de-duplication, LRU bound and cache_clear()."""
import threading
import time
import unittest
from datetime import datetime
from unittest.mock import patch

import data.listing as listing


def _cached(maxsize=16):
    calls = []

    @listing._singleflight_cache(maxsize=maxsize)
    def fn(key):
        calls.append(key)
        return f"result-{key}"

    return fn, calls


class TestSingleflightCache(unittest.TestCase):

    def test_second_call_hits_cache(self):
        fn, calls = _cached()
        self.assertEqual(fn("A"), "result-A")
        self.assertEqual(fn("A"), "result-A")
        self.assertEqual(calls, ["A"])

    def test_entries_expire_at_day_boundary(self):
        fn, calls = _cached()
        with patch("data.listing.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 9, 18, 23, 0)
            fn("A")
            fn("A")
            mock_dt.now.return_value = datetime(2026, 9, 19, 0, 1)
            fn("A")
        self.assertEqual(calls, ["A", "A"])

    def test_lru_bound_evicts_oldest(self):
        fn, calls = _cached(maxsize=2)
        fn("A"); fn("B")
        fn("A")            # A is now most recent
        fn("C")            # evicts B
        fn("A")            # still cached
        fn("B")            # recomputed
        self.assertEqual(calls, ["A", "B", "C", "B"])

    def test_cache_clear_forces_recompute(self):
        fn, calls = _cached()
        fn("A")
        fn.cache_clear()
        fn("A")
        self.assertEqual(calls, ["A", "A"])

    def test_concurrent_callers_share_one_computation(self):
        calls = []
        release = threading.Event()

        @listing._singleflight_cache(maxsize=4)
        def slow(key):
            calls.append(key)
            release.wait(2.0)
            return f"result-{key}"

        results = []

        def worker():
            results.append(slow("X"))

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        # Let every thread reach either the computation or the wait-on-lock.
        time.sleep(0.2)
        release.set()
        for t in threads:
            t.join(3.0)

        self.assertEqual(results, ["result-X"] * 5)
        self.assertEqual(calls, ["X"], "only one thread should have computed the key")

    def test_distinct_keys_do_not_block_each_other(self):
        started = {}
        release = threading.Event()

        @listing._singleflight_cache(maxsize=4)
        def slow(key):
            started[key] = True
            release.wait(2.0)
            return key

        threads = [threading.Thread(target=slow, args=(k,)) for k in ("A", "B")]
        for t in threads:
            t.start()
        time.sleep(0.2)
        self.assertEqual(set(started), {"A", "B"}, "second key must not wait on the first key's lock")
        release.set()
        for t in threads:
            t.join(3.0)


class TestGetStockListingFallback(unittest.TestCase):

    def setUp(self):
        listing.get_stock_listing.cache_clear()
        listing._get_listing_with_norm.cache_clear()

    @patch("data.listing.fdr")
    def test_fdr_failure_returns_empty_frame_with_expected_columns(self, mock_fdr):
        mock_fdr.StockListing.side_effect = Exception("offline")
        df = listing.get_stock_listing("ETF/KR")
        self.assertTrue(df.empty)
        self.assertEqual(list(df.columns), ["Symbol", "Code", "Name", "Market"])

    @patch("data.listing.fdr")
    def test_listing_with_norm_strips_spaces_and_uppercases(self, mock_fdr):
        import pandas as pd
        mock_fdr.StockListing.return_value = pd.DataFrame(
            {"Symbol": ["069500"], "Name": ["kodex 200_tr"], "Market": ["ETF"]}
        )
        df = listing._get_listing_with_norm("ETF/KR")
        self.assertEqual(df["NameNorm"].iloc[0], "KODEX200TR")
        # Cached: the underlying listing is fetched once even though two caches sit above it.
        listing._get_listing_with_norm("ETF/KR")
        self.assertEqual(mock_fdr.StockListing.call_count, 1)


if __name__ == "__main__":
    unittest.main()
