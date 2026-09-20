"""tests/test_thread_owner.py — ui.common.ThreadOwnerMixin bookkeeping and the
bound-method slots that replaced the lambda connections on QThread.finished."""
import unittest
from unittest.mock import patch, MagicMock

from PyQt6.QtCore import QThread, QObject
from PyQt6.QtWidgets import QApplication, QMessageBox, QDialog

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
        # Everything typed in by hand lives in portfolio.db (trades + asset
        # snapshots) and custom_settings.json; both must be in the list.
        from threads.fetch_threads import _AUTO_BACKUP_FILES
        from paths import DB_FILE, CUSTOM_SETTINGS_FILE
        self.assertEqual(set(_AUTO_BACKUP_FILES), {DB_FILE, CUSTOM_SETTINGS_FILE})

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

    def test_history_tab_kpi_strip_matches_its_own_labels(self):
        """Total Asset/Cumulative Asset/Cost Basis used to be mislabeled --
        Total Asset silently included withdrawals despite its "Valuation +
        cash" subtext, and Total Invest showed NAV under a "Cost basis"
        subtext (review_agy.md #1). Pin the corrected values and confirm
        total_asset_updated emits live NAV, not the cumulative figure."""
        from ui.history_tab import TradingHistoryTab
        tab = TradingHistoryTab()
        rec = {"company": "A", "market": "KOSPI", "buy_date": "2026-01-01", "orig_key": "k1",
               "buy_price": 100.0, "qty": 10.0, "buy_amount": 1000.0, "curr_price": 120.0,
               "sell_date": "", "sell_price": 0.0, "sell_qty": 0.0, "sell_amount": 0.0}
        tab._open_data = [rec]
        tab._closed_data = []
        tab._principal_edit.setText("3,000")
        tab._deposit_edit.setText("500")
        tab._withdrawal_edit.setText("100")

        emitted = []
        tab.total_asset_updated.connect(emitted.append)
        tab._refresh_summary()

        # eval=1200, cost=1000, deposit=500, withdrawal=100, principal=3000
        self.assertEqual(tab._kpi_labels["total_asset"][0].text(), "1,700")      # nav = eval + deposit
        self.assertEqual(tab._kpi_labels["cumulative_asset"][0].text(), "1,800")  # nav + withdrawal
        self.assertEqual(tab._kpi_labels["cost_basis"][0].text(), "1,000")        # cost_total, not NAV
        self.assertEqual(tab._kpi_labels["total_pl"][0].text(), "-1,200")         # cumulative - principal
        self.assertEqual(emitted, [1700.0])
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

    def test_history_tab_partial_sell_splits_remainder_into_open_position(self):
        from ui.history_tab import TradingHistoryTab
        tab = TradingHistoryTab()
        rec = {"ticker": "005930", "company": "A", "buy_date": "2026-01-01", "orig_key": "k1",
               "buy_price": 1000.0, "qty": 100.0, "buy_amount": 100000.0,
               "sell_date": "", "sell_price": 0.0, "sell_qty": 0.0, "sell_amount": 0.0}
        tab._open_data = [rec]
        tab._closed_data = []
        tab._row_data = [("open", rec)]

        dlg = MagicMock()
        dlg.exec.return_value = QDialog.DialogCode.Accepted
        dlg.result_data = {"sell_date": "2026-02-01", "sell_price": 1200.0,
                            "sell_qty": 40.0, "sell_amount": 48000.0}

        with patch("ui.history_tab.SellEditDialog", return_value=dlg), \
             patch("ui.history_tab.trade_db.upsert_trade", return_value="k1_2") as upsert, \
             patch("ui.history_tab.trade_db.upsert_trades"), \
             patch.object(tab, "_refresh_summary"), patch.object(tab, "_apply_filter"):
            tab._on_cell_double_clicked(0, 10)

        # The original record becomes the closed sub-lot sized to what was sold.
        self.assertEqual(rec["qty"], 40.0)
        self.assertEqual(rec["buy_amount"], 40000.0)
        self.assertIn(rec, tab._closed_data)
        self.assertNotIn(rec, tab._open_data)

        # The untouched 60 shares survive as their own open position instead
        # of vanishing from holdings (review_agy.md #5).
        self.assertEqual(len(tab._open_data), 1)
        remainder = tab._open_data[0]
        self.assertEqual(remainder["qty"], 60.0)
        self.assertEqual(remainder["buy_amount"], 60000.0)
        self.assertEqual(remainder["sell_date"], "")
        self.assertEqual(remainder["orig_key"], "k1_2")
        upsert.assert_called_once()
        tab._settings_save_timer.stop()

    def test_history_tab_sequential_partial_sells_keep_splitting_correctly(self):
        """review_agy.md (4th pass) #6: a second partial sell on the
        remainder created by the first one must split again correctly, not
        just the first split in isolation. 100 -> sell 30 (remainder 70) ->
        sell 20 of the 70 (remainder 50)."""
        from ui.history_tab import TradingHistoryTab
        tab = TradingHistoryTab()
        rec = {"ticker": "005930", "company": "A", "buy_date": "2026-01-01", "orig_key": "k1",
               "buy_price": 1000.0, "qty": 100.0, "buy_amount": 100000.0,
               "sell_date": "", "sell_price": 0.0, "sell_qty": 0.0, "sell_amount": 0.0}
        tab._open_data = [rec]
        tab._closed_data = []

        # --- First partial sell: 30 of 100 ---
        tab._row_data = [("open", rec)]
        dlg1 = MagicMock()
        dlg1.exec.return_value = QDialog.DialogCode.Accepted
        dlg1.result_data = {"sell_date": "2026-02-01", "sell_price": 1200.0,
                             "sell_qty": 30.0, "sell_amount": 36000.0}
        with patch("ui.history_tab.SellEditDialog", return_value=dlg1), \
             patch("ui.history_tab.trade_db.upsert_trade", return_value="k1_2"), \
             patch("ui.history_tab.trade_db.upsert_trades"), \
             patch.object(tab, "_refresh_summary"), patch.object(tab, "_apply_filter"):
            tab._on_cell_double_clicked(0, 10)

        self.assertEqual(rec["qty"], 30.0)
        self.assertEqual(len(tab._open_data), 1)
        remainder1 = tab._open_data[0]
        self.assertEqual(remainder1["qty"], 70.0)
        self.assertEqual(remainder1["buy_amount"], 70000.0)
        self.assertEqual(remainder1["orig_key"], "k1_2")

        # --- Second partial sell: 20 of the remaining 70 ---
        tab._row_data = [("open", remainder1)]
        dlg2 = MagicMock()
        dlg2.exec.return_value = QDialog.DialogCode.Accepted
        dlg2.result_data = {"sell_date": "2026-03-01", "sell_price": 1300.0,
                             "sell_qty": 20.0, "sell_amount": 26000.0}
        with patch("ui.history_tab.SellEditDialog", return_value=dlg2), \
             patch("ui.history_tab.trade_db.upsert_trade", return_value="k1_3"), \
             patch("ui.history_tab.trade_db.upsert_trades"), \
             patch.object(tab, "_refresh_summary"), patch.object(tab, "_apply_filter"):
            tab._on_cell_double_clicked(0, 10)

        # remainder1 (30 of the original 70) becomes the closed sub-lot.
        self.assertEqual(remainder1["qty"], 20.0)
        self.assertEqual(remainder1["buy_amount"], 20000.0)
        self.assertIn(remainder1, tab._closed_data)
        self.assertNotIn(remainder1, tab._open_data)

        # The 50 still-untouched shares survive as a third open record.
        self.assertEqual(len(tab._open_data), 1)
        remainder2 = tab._open_data[0]
        self.assertEqual(remainder2["qty"], 50.0)
        self.assertEqual(remainder2["buy_amount"], 50000.0)
        self.assertEqual(remainder2["orig_key"], "k1_3")

        # First closed sub-lot (from the first sale) is untouched by the second.
        self.assertEqual(rec["qty"], 30.0)
        self.assertEqual(rec["buy_amount"], 30000.0)
        tab._settings_save_timer.stop()

    def test_history_tab_delete_uses_orig_key_not_value_equality(self):
        """Two open positions with identical field values (same ticker/date/
        qty/price) used to make list.remove(rec) ambiguous -- deleting one
        could remove the other instead (review_agy.md #2)."""
        from ui.history_tab import TradingHistoryTab
        tab = TradingHistoryTab()
        rec_a = {"ticker": "005930", "company": "A", "buy_date": "2026-01-01", "orig_key": "k1",
                 "buy_price": 1.0, "qty": 1.0, "buy_amount": 1.0}
        rec_b = {"ticker": "005930", "company": "A", "buy_date": "2026-01-01", "orig_key": "k2",
                 "buy_price": 1.0, "qty": 1.0, "buy_amount": 1.0}
        tab._open_data = [rec_a, rec_b]
        tab._closed_data = []
        tab._row_data = [("open", rec_a), ("open", rec_b)]
        with patch.object(tab, "_selected_trade_records", return_value=[("open", rec_a)]), \
             patch("ui.history_tab.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes), \
             patch("ui.history_tab.trade_db.delete_trade", return_value=True) as delete, \
             patch.object(tab, "_refresh_summary"), patch.object(tab, "_apply_filter"):
            tab._delete_selected_trades()
        delete.assert_called_once_with("k1")
        self.assertEqual(tab._open_data, [rec_b])
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
