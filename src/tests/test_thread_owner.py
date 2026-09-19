"""tests/test_thread_owner.py — ui.common.ThreadOwnerMixin bookkeeping and the
bound-method slots that replaced the lambda connections on QThread.finished."""
import unittest
from unittest.mock import patch

from PyQt6.QtCore import QThread, QObject
from PyQt6.QtWidgets import QApplication, QMessageBox

app = QApplication.instance() or QApplication([])

from ui.common import ThreadOwnerMixin  # noqa: E402


class _Worker(QThread):
    """Never started unless a test says so; run() returns immediately."""
    def run(self):
        pass


class _Owner(ThreadOwnerMixin, QObject):
    pass


class TestThreadOwnerMixin(unittest.TestCase):

    def test_tracked_threads_are_collected(self):
        o = _Owner()
        t1, t2 = _Worker(), _Worker()
        self.assertIs(o._track_thread(t1), t1)
        o._track_thread(t2)
        self.assertEqual(o.collect_threads_to_stop(), [t1, t2])

    def test_finished_threads_are_pruned(self):
        o = _Owner()
        done = _Worker()
        done.start()
        self.assertTrue(done.wait(2000))
        o._track_thread(done)
        pending = o._track_thread(_Worker())
        self.assertEqual(o.collect_threads_to_stop(), [pending])

    def test_attr_replaces_and_retires_previous(self):
        o = _Owner()
        first = o._track_thread(_Worker(), "_price_thread")
        self.assertIs(o._price_thread, first)
        second = o._track_thread(_Worker(), "_price_thread")
        self.assertIs(o._price_thread, second)
        # `first` never ran, so retire_thread just drops the attribute; both objects
        # are still tracked (not finished) and reported once each.
        self.assertEqual(o.collect_threads_to_stop(), [first, second])

    def test_running_previous_thread_becomes_zombie_and_is_still_collected(self):
        o = _Owner()
        with patch.object(_Worker, "isRunning", return_value=True):
            first = o._track_thread(_Worker(), "_t")
            o._track_thread(_Worker(), "_t")
            self.assertIn(first, o._zombie_threads)
            self.assertTrue(first.signalsBlocked())
            collected = o.collect_threads_to_stop()
        self.assertEqual(len(collected), 2)
        self.assertEqual(len(set(map(id, collected))), 2)   # zombie not double-counted

    def test_empty_owner(self):
        self.assertEqual(_Owner().collect_threads_to_stop(), [])


class TestSignalSlotArity(unittest.TestCase):
    """The worker signals now echo request context so slots are bound methods."""

    def test_single_stock_signal_carries_ticker(self):
        from threads.fetch_threads import SingleStockFetchThread
        got = []
        t = SingleStockFetchThread("KOSPI", "005930")
        t.finished.connect(lambda r, e, tk: got.append((r, e, tk)))
        with patch("threads.fetch_threads.fetch_single_stock", return_value=({"ticker": "005930"}, None)):
            t.run()
        self.assertEqual(got, [({"ticker": "005930"}, "", "005930")])

    def test_stock_ma_signal_carries_market_and_change_mode(self):
        from threads.fetch_threads import StockMaThread
        got = []
        t = StockMaThread("AAPL", "Apple", "NASDAQ 100", change_mode="pct")
        t.finished.connect(lambda *a: got.append(a))
        with patch("threads.fetch_threads.fetch_stock_ma_multi", return_value=(None, "boom")):
            t.run()
        self.assertEqual(len(got), 1)
        ticker, name, df, err, inv, market, cm = got[0]
        self.assertEqual((ticker, name, df, err, inv, market, cm),
                         ("AAPL", "Apple", None, "boom", [], "NASDAQ 100", "pct"))

    def test_strategy_summary_counts_trend_following_outcomes_in_parallel(self):
        import polars as pl
        from threads.fetch_threads import StrategySummaryThread

        def fake_tf(ticker, _start):
            if ticker == "ERR":
                return {"error": "no data"}
            pos = [0, 1] if ticker == "IN" else [1, 0]
            return {"error": None, "signals": pl.DataFrame({"position": pos})}

        fake_rebalance = {"buy_candidates": [1, 2], "sell_candidates": [3]}
        got = []
        t = StrategySummaryThread([], set(), {"KOSPI": 10}, 1.5, ["IN", "FLAT", "ERR"], "2025-01-01")
        t.finished.connect(lambda r, e: got.append((r, e)))
        with patch("strategy.trend_following.run_backtest_for_ticker", side_effect=fake_tf), \
             patch("strategy.rebalance.compute_weekly_rebalance_signals", return_value=fake_rebalance):
            t.run()
        (result, err), = got
        self.assertEqual(err, "")
        self.assertEqual(
            {k: result[k] for k in ("buy_count", "sell_count", "tf_in_position", "tf_flat", "tf_errors", "tf_total")},
            {"buy_count": 2, "sell_count": 1, "tf_in_position": 1, "tf_flat": 1, "tf_errors": 1, "tf_total": 3},
        )

    def test_auto_backup_covers_every_hand_entered_state_file(self):
        # trading_record.json (Total Assets weekly snapshots) is typed in by
        # hand and cannot be rebuilt from any API, so it must be in the list.
        from threads.fetch_threads import _AUTO_BACKUP_FILES
        from paths import DB_FILE, CUSTOM_SETTINGS_FILE, TRADING_RECORD_FILE
        self.assertEqual(set(_AUTO_BACKUP_FILES), {DB_FILE, CUSTOM_SETTINGS_FILE, TRADING_RECORD_FILE})

    def test_history_tab_save_overrides_upserts_only_the_given_records(self):
        from ui.history_tab import TradingHistoryTab
        tab = TradingHistoryTab()
        edited = {"ticker": "005930", "company": "A", "buy_date": "2026-01-01", "orig_key": "k1"}
        untouched = {"ticker": "000660", "company": "B", "buy_date": "2026-02-01", "orig_key": "k2",
                     "sell_date": "2026-03-01"}
        tab._open_data = [edited]
        tab._closed_data = [untouched]
        with patch("ui.history_tab.trade_db.upsert_trades") as upsert:
            tab._save_overrides([edited])
            upsert.assert_called_once_with([edited])
            tab._save_overrides([])
            upsert.assert_called_once()
        tab._settings_save_timer.stop()

    def test_history_tab_deletes_selected_trades(self):
        from ui.history_tab import TradingHistoryTab
        tab = TradingHistoryTab()
        open_rec = {"ticker": "005930", "company": "A", "buy_date": "2026-01-01", "orig_key": "k1",
                    "buy_price": 1.0, "qty": 1.0, "buy_amount": 1.0}
        closed_rec = {"ticker": "000660", "company": "B", "buy_date": "2026-02-01", "orig_key": "k2",
                      "sell_date": "2026-03-01", "buy_price": 1.0, "qty": 1.0, "buy_amount": 1.0}
        keep_rec = {"ticker": "035420", "company": "C", "buy_date": "2026-04-01", "orig_key": "k3",
                    "buy_price": 1.0, "qty": 1.0, "buy_amount": 1.0}
        tab._open_data = [open_rec, keep_rec]
        tab._closed_data = [closed_rec]
        tab._row_data = [("closed", closed_rec), ("open", open_rec), ("open", keep_rec)]
        with patch.object(tab, "_selected_trade_records", return_value=[("closed", closed_rec), ("open", open_rec)]), \
             patch("ui.history_tab.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes), \
             patch("ui.history_tab.trade_db.delete_trade", return_value=True) as delete, \
             patch.object(tab, "_refresh_summary"), patch.object(tab, "_apply_filter"):
            tab._delete_selected_trades()
        self.assertEqual([c.args[0] for c in delete.call_args_list], ["k2", "k1"])
        self.assertEqual(tab._open_data, [keep_rec])
        self.assertEqual(tab._closed_data, [])
        # "No" leaves everything alone.
        with patch.object(tab, "_selected_trade_records", return_value=[("open", keep_rec)]), \
             patch("ui.history_tab.QMessageBox.question", return_value=QMessageBox.StandardButton.No), \
             patch("ui.history_tab.trade_db.delete_trade") as delete:
            tab._delete_selected_trades()
        delete.assert_not_called()
        self.assertEqual(tab._open_data, [keep_rec])
        tab._settings_save_timer.stop()

    def test_history_tab_renames_every_record_with_that_ticker(self):
        from ui.history_tab import TradingHistoryTab
        tab = TradingHistoryTab()
        a = {"ticker": "005930", "company": "old", "buy_date": "2026-01-01"}
        b = {"ticker": "005930", "company": "old", "buy_date": "2026-02-01"}
        c = {"ticker": "000660", "company": "other", "buy_date": "2026-03-01"}
        tab._open_data = [a, c]
        tab._closed_data = [b]
        with patch.object(tab, "_save_overrides") as save, \
             patch.object(tab, "_refresh_summary"), patch.object(tab, "_apply_filter"):
            tab._on_ticker_name_resolved({"name": "Samsung"}, "", "005930")
            self.assertEqual((a["company"], b["company"], c["company"]), ("Samsung", "Samsung", "other"))
            self.assertTrue(a["is_overridden"] and b["is_overridden"])
            save.assert_called_once()
            # no-op when nothing changes / no name
            tab._on_ticker_name_resolved({"name": "Samsung"}, "", "005930")
            tab._on_ticker_name_resolved(None, "err", "005930")
            save.assert_called_once()
        tab._settings_save_timer.stop()


if __name__ == "__main__":
    unittest.main()
