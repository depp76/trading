"""
tests/test_trade_db.py — SQLite CRUD, 인덱스, 배치 upsert 테스트
실제 portfolio.db를 건드리지 않도록 임시 DB를 사용합니다.
"""
import os
import sys
import sqlite3
import tempfile
import unittest

_PROJ_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJ_ROOT)

import trade_db


class TempDBMixin:
    """각 테스트가 격리된 임시 DB를 사용합니다.

    _CUSTOM_JSON / _OVERRIDES_JSON 경로도 존재하지 않는 경로로 교체해
    _migrate_legacy_json이 실제 프로젝트 JSON을 읽지 못하도록 막습니다.
    """

    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        # DB 경로 격리
        self._orig_db        = trade_db._DB_FILE
        self._orig_custom    = trade_db._CUSTOM_JSON
        self._orig_overrides = trade_db._OVERRIDES_JSON
        trade_db._DB_FILE       = self._tmp.name
        trade_db._CUSTOM_JSON   = self._tmp.name + ".custom.json"     # 존재하지 않음
        trade_db._OVERRIDES_JSON = self._tmp.name + ".overrides.json" # 존재하지 않음
        trade_db.init_db()

    def tearDown(self):
        trade_db._DB_FILE        = self._orig_db
        trade_db._CUSTOM_JSON    = self._orig_custom
        trade_db._OVERRIDES_JSON = self._orig_overrides
        os.unlink(self._tmp.name)


def _make_record(**kwargs) -> dict:
    base = {
        "company":     "테스트종목",
        "market":      "KOSPI",
        "ticker":      "005930",
        "buy_date":    "2024-01-10",
        "buy_price":   70000.0,
        "qty":         10.0,
        "buy_amount":  700000.0,
        "sell_date":   "",
        "sell_price":  0.0,
        "sell_qty":    0.0,
        "sell_amount": 0.0,
    }
    base.update(kwargs)
    return base


class TestInitDb(TempDBMixin, unittest.TestCase):

    def test_table_exists(self):
        conn = sqlite3.connect(trade_db._DB_FILE)
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()]
        conn.close()
        self.assertIn("trades", tables)

    def test_index_buy_date_exists(self):
        conn = sqlite3.connect(trade_db._DB_FILE)
        indices = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ).fetchall()]
        conn.close()
        self.assertIn("idx_trades_buy_date", indices)

    def test_index_sell_date_exists(self):
        conn = sqlite3.connect(trade_db._DB_FILE)
        indices = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ).fetchall()]
        conn.close()
        self.assertIn("idx_trades_sell_date", indices)

    def test_init_db_idempotent(self):
        trade_db.init_db()
        self.test_table_exists()


class TestUpsertAndGet(TempDBMixin, unittest.TestCase):

    def test_insert_returns_key(self):
        key = trade_db.upsert_trade(_make_record())
        self.assertIsInstance(key, str)
        self.assertTrue(len(key) > 0)

    def test_insert_and_retrieve(self):
        rec = _make_record(company="삼성전자", buy_date="2024-02-01", qty=5.0)
        key = trade_db.upsert_trade(rec)
        fetched = trade_db.get_trade(key)
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched["company"], "삼성전자")
        self.assertEqual(fetched["buy_date"], "2024-02-01")
        self.assertAlmostEqual(fetched["qty"], 5.0)

    def test_update_on_conflict(self):
        key = trade_db.upsert_trade(_make_record())
        trade_db.upsert_trade(_make_record(orig_key=key, buy_price=80000.0))
        self.assertAlmostEqual(trade_db.get_trade(key)["buy_price"], 80000.0)

    def test_get_nonexistent_returns_none(self):
        self.assertIsNone(trade_db.get_trade("없는키_9999"))

    def test_orig_key_auto_generated(self):
        key = trade_db.upsert_trade(_make_record(company="현대차", buy_date="2024-03-01", qty=3.0))
        self.assertIn("현대차", key)
        self.assertIn("2024-03-01", key)

    def test_row_to_dict_has_runtime_fields(self):
        key = trade_db.upsert_trade(_make_record())
        fetched = trade_db.get_trade(key)
        for field in ("pl", "pl_pct", "days_held", "curr_price", "curr_pl"):
            self.assertIn(field, fetched)


class TestBatchUpsert(TempDBMixin, unittest.TestCase):

    def _make_batch(self, n):
        return [_make_record(company=f"종목{i}", buy_date=f"2024-01-{i+1:02d}", qty=float(i+1)) for i in range(n)]

    def test_batch_insert_returns_keys(self):
        self.assertEqual(len(trade_db.upsert_trades(self._make_batch(5))), 5)

    def test_batch_all_rows_saved(self):
        trade_db.upsert_trades(self._make_batch(10))
        self.assertEqual(len(trade_db.load_all_trades()), 10)

    def test_batch_empty_list(self):
        self.assertEqual(trade_db.upsert_trades([]), [])

    def test_batch_idempotent(self):
        records = self._make_batch(3)
        trade_db.upsert_trades(records)
        trade_db.upsert_trades(records)
        self.assertEqual(len(trade_db.load_all_trades()), 3)

    def test_batch_update_existing(self):
        keys = trade_db.upsert_trades(self._make_batch(3))
        trade_db.upsert_trades([_make_record(orig_key=keys[0], buy_price=99999.0)])
        self.assertAlmostEqual(trade_db.get_trade(keys[0])["buy_price"], 99999.0)


class TestQueries(TempDBMixin, unittest.TestCase):

    def setUp(self):
        super().setUp()
        trade_db.upsert_trade(_make_record(company="오픈A", buy_date="2024-01-01", qty=1.0))
        trade_db.upsert_trade(_make_record(company="오픈B", buy_date="2024-01-02", qty=2.0))
        trade_db.upsert_trade(_make_record(company="청산C", buy_date="2023-06-01", qty=3.0,
            sell_date="2024-01-15", sell_price=75000.0, sell_qty=3.0, sell_amount=225000.0))

    def test_load_all_trades_count(self):
        self.assertEqual(len(trade_db.load_all_trades()), 3)

    def test_load_all_trades_sorted(self):
        trades = trade_db.load_all_trades()
        dates = [t["buy_date"] for t in trades]
        self.assertEqual(dates, sorted(dates))

    def test_get_open_trades(self):
        open_trades = trade_db.get_open_trades()
        self.assertEqual(len(open_trades), 2)
        self.assertIn("오픈A", {t["company"] for t in open_trades})

    def test_get_closed_trades(self):
        closed = trade_db.get_closed_trades()
        self.assertEqual(len(closed), 1)
        self.assertEqual(closed[0]["sell_date"], "2024-01-15")


class TestDeleteTrade(TempDBMixin, unittest.TestCase):

    def test_delete_existing(self):
        key = trade_db.upsert_trade(_make_record())
        self.assertTrue(trade_db.delete_trade(key))
        self.assertIsNone(trade_db.get_trade(key))

    def test_delete_nonexistent(self):
        self.assertFalse(trade_db.delete_trade("없는키_xyz"))

    def test_delete_does_not_affect_others(self):
        k1 = trade_db.upsert_trade(_make_record(company="A", buy_date="2024-01-01", qty=1.0))
        k2 = trade_db.upsert_trade(_make_record(company="B", buy_date="2024-01-02", qty=2.0))
        trade_db.delete_trade(k1)
        self.assertIsNotNone(trade_db.get_trade(k2))


class TestDbPath(TempDBMixin, unittest.TestCase):

    def test_returns_string(self):
        self.assertIsInstance(trade_db.db_path(), str)

    def test_file_exists(self):
        self.assertTrue(os.path.exists(trade_db.db_path()))


if __name__ == "__main__":
    unittest.main()
