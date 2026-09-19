"""tests/test_thread_owner.py — ui.common.ThreadOwnerMixin bookkeeping and the
bound-method slots that replaced the lambda connections on QThread.finished."""
import unittest
from unittest.mock import patch

from PyQt6.QtCore import QThread, QObject
from PyQt6.QtWidgets import QApplication

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
