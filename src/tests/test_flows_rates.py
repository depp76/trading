"""data/flows.py cache + fetch decisions and data/rates.py sources, with the
network collectors mocked."""
import json
import os
import tempfile
import unittest
from datetime import date
from unittest.mock import patch

import data.flows as flows
import data.rates as rates


def _naver_rows(n, start=date(2026, 9, 1)):
    from datetime import timedelta
    rows = []
    d = start + timedelta(days=n * 2)
    while len(rows) < n:
        if d.weekday() < 5:
            rows.append({"Date": d.strftime("%Y.%m.%d"), "Close": 1000 + len(rows), "Foreigner": 10, "Institution": -4,
                         "Retail": -6, "InvestmentTrust": 0, "PrivateEquity": 0})
        d -= timedelta(days=1)
    return rows


class TestInvestorFlows(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patch_dir = patch.object(flows, "FLOWS_CACHE_DIR", self.tmp.name)
        self.patch_dir.start()

    def tearDown(self):
        self.patch_dir.stop()
        self.tmp.cleanup()

    def test_fetches_once_then_serves_from_cache(self):
        today = date(2026, 9, 28)   # Monday
        with patch.object(flows, "_fetch_investor_trend_naver", return_value=_naver_rows(30)) as fetch:
            df = flows.get_investor_flows("5930", "2026-08-01", today=today)
            self.assertEqual(fetch.call_count, 1)
            self.assertEqual(fetch.call_args.args[0], "005930")
            self.assertEqual(df.columns, ["Date", "Close", "Foreigner", "Institution", "Retail"])
            self.assertGreater(df.height, 0)
            self.assertEqual(df.get_column("Foreigner")[0], 10.0)
            self.assertTrue(os.path.exists(os.path.join(self.tmp.name, "005930.json")))
            # rows already cover the range up to the last completed session -> no second fetch
            flows.get_investor_flows("005930", "2026-08-01", today=today)
            self.assertEqual(fetch.call_count, 1)

    def test_refetches_when_history_is_missing(self):
        today = date(2026, 9, 28)
        with patch.object(flows, "_fetch_investor_trend_naver", return_value=_naver_rows(30)) as fetch:
            flows.get_investor_flows("000660", "2026-08-01", today=today)
            flows.get_investor_flows("000660", "2025-01-01", today=today)   # earlier start -> fetch again
            self.assertEqual(fetch.call_count, 2)
            self.assertGreater(fetch.call_args.kwargs["days"], 400)

    def test_fetch_failure_returns_empty_frame(self):
        with patch.object(flows, "_fetch_investor_trend_naver", side_effect=RuntimeError("down")):
            df = flows.get_investor_flows("000001", "2026-08-01", today=date(2026, 9, 28))
            self.assertEqual(df.height, 0)
            self.assertIn("Date", df.columns)

    def test_last_expected_session_skips_weekends(self):
        self.assertEqual(flows._last_expected_session(date(2026, 9, 28)), date(2026, 9, 25))   # Mon -> Fri
        self.assertEqual(flows._last_expected_session(date(2026, 9, 30)), date(2026, 9, 29))   # Wed -> Tue


class TestRates(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.csv_path = os.path.join(self.tmp.name, "cd91.csv")
        self.cache_path = os.path.join(self.tmp.name, "cd91.json")
        self.p1 = patch.object(rates, "CD91_CSV_FILE", self.csv_path)
        self.p2 = patch.object(rates, "CD91_CACHE_FILE", self.cache_path)
        self.p1.start()
        self.p2.start()

    def tearDown(self):
        self.p1.stop()
        self.p2.stop()
        self.tmp.cleanup()

    def test_csv_source(self):
        with open(self.csv_path, "w", encoding="utf-8") as f:
            f.write("Date,Rate\n2024-01-02,3.83\n2024.01.03,3.84%\nbad,row\n")
        with patch.dict(os.environ, {"ECOS_API_KEY": ""}):
            df = rates.get_cd91_series("2024-01-01", "2024-01-31")
        self.assertEqual(df.height, 2)
        self.assertAlmostEqual(df.get_column("Rate")[1], 3.84)
        self.assertEqual(df.get_column("Date")[0], date(2024, 1, 2))

    def test_empty_without_sources(self):
        with patch.dict(os.environ, {"ECOS_API_KEY": ""}):
            df = rates.get_cd91_series("2024-01-01", "2024-01-31")
        self.assertEqual(df.height, 0)

    def test_ecos_parsing_and_cache(self):
        payload = {"StatisticSearch": {"row": [{"TIME": "20240102", "DATA_VALUE": "3.83"},
                                               {"TIME": "20240103", "DATA_VALUE": "3.85"}]}}

        class _Res:
            def raise_for_status(self):
                pass

            def json(self):
                return payload

        with patch.dict(os.environ, {"ECOS_API_KEY": "k"}), patch.object(rates.requests, "get", return_value=_Res()) as get:
            df = rates.get_cd91_series("2024-01-01", "2024-01-31")
            self.assertEqual(df.height, 2)
            self.assertEqual(get.call_count, 1)
            with open(self.cache_path, encoding="utf-8") as f:
                self.assertEqual(json.load(f)["2024-01-03"], 3.85)


if __name__ == "__main__":
    unittest.main()
