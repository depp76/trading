"""tests/test_universe_tab.py — UniverseTab behaviour that does not need the
network: the startup re-fetch of user-added tickers renders once per batch."""
import unittest
from unittest.mock import patch

from PyQt6.QtWidgets import QApplication

app = QApplication.instance() or QApplication([])


def _no_disk(_path, default=None):
    return default


def _stock(ticker, cap):
    return {"ticker": ticker, "name": f"S{ticker}", "market": "KOSPI", "market_cap": cap, "price": 1.0, "changes": {}}


class TestStartupAddedTickersRenderOnce(unittest.TestCase):

    def _tab(self):
        from ui.universe_tab import UniverseTab
        with patch("ui.universe_tab.safe_load_json", side_effect=_no_disk):
            tab = UniverseTab()
        tab.custom_settings = {"added": [], "deleted": []}
        return tab

    def setUp(self):
        # The tab persists all_data to UNIVERSE_CACHE_FILE after a batch render
        # and after a user add; never let a test touch the real cache file.
        p = patch("ui.universe_tab.atomic_save_json")
        self.save_json = p.start()
        self.addCleanup(p.stop)

    def test_startup_arrivals_are_batched_into_one_render(self):
        tab = self._tab()
        with patch.object(tab, "load_custom_settings"), patch.object(tab, "save_custom_settings"), \
             patch.object(tab, "_reload_table") as reload, patch.object(tab, "filter_table") as filt:
            for t, cap in (("100001", 5), ("100002", 50), ("100003", 20)):
                tab._handle_single_stock_loaded(_stock(t, cap), "", is_startup=True, ticker_hint=t)
            # Nothing rendered yet; the single-shot timer is armed instead.
            self.assertEqual(reload.call_count, 0)
            self.assertTrue(tab._startup_render_timer.isActive())
            self.assertEqual(self.save_json.call_count, 0)

            tab._startup_render_timer.stop()
            tab._render_startup_batch()
            self.assertEqual(reload.call_count, 1)
            self.assertEqual(filt.call_count, 1)
        # Sorted by market cap descending, like on_finished_all.
        self.assertEqual([d["ticker"] for d in tab.all_data], ["100002", "100003", "100001"])
        # The batch render persists the added tickers into the startup cache (review_agy.md 2.4).
        self.assertEqual(self.save_json.call_count, 1)
        self.assertEqual([d["ticker"] for d in self.save_json.call_args.args[1]], ["100002", "100003", "100001"])

    def test_user_add_still_renders_immediately(self):
        tab = self._tab()
        with patch.object(tab, "load_custom_settings"), patch.object(tab, "save_custom_settings"), \
             patch.object(tab, "_reload_table") as reload, patch.object(tab, "filter_table"), \
             patch.object(tab, "update_total_status"):
            tab._handle_single_stock_loaded(_stock("100009", 1), "", is_startup=False, ticker_hint="100009")
            self.assertEqual(reload.call_count, 1)
            self.assertFalse(tab._startup_render_timer.isActive())
        self.assertEqual(self.save_json.call_count, 1)
        self.assertEqual([d["ticker"] for d in self.save_json.call_args.args[1]], ["100009"])


class TestStatusFilterButton(unittest.TestCase):
    """The toolbar's status button cycles blank -> Port -> Target -> blank
    (user direction 2026-09-29) at a fixed size and drives the table filter."""

    def setUp(self):
        p = patch("ui.universe_tab.atomic_save_json")
        p.start()
        self.addCleanup(p.stop)
        from ui.universe_tab import UniverseTab
        with patch("ui.universe_tab.safe_load_json", side_effect=_no_disk):
            self.tab = UniverseTab()

    def test_cycles_label_checked_state_and_filter(self):
        tab = self.tab
        btn = tab.tg_filter_btn
        width0 = btn.width()
        self.assertEqual(btn.text(), "")
        self.assertFalse(btn.isChecked())
        seen = []
        with patch.object(tab.table, "apply_col_filters", side_effect=lambda *a, **k: seen.append(k["status"])):
            for expected_label, expected_status, expected_checked in (
                    ("Port", "On", True), ("Target", "Tg", True), ("", "", False), ("Port", "On", True)):
                btn.click()
                self.assertEqual(btn.text(), expected_label)
                self.assertEqual(btn.isChecked(), expected_checked)
                self.assertEqual(tab._status_filter, expected_status)
                self.assertEqual(btn.width(), width0)
        self.assertEqual(seen, ["On", "Tg", "", "On"])
        self.assertEqual(btn.minimumWidth(), btn.maximumWidth())     # fixed width: label changes never resize it

    def test_filter_hides_rows_of_other_statuses(self):
        from ui.widgets import StockTable
        table = StockTable()
        rows = {"A": "-", "B": "On", "C": "Tg"}
        table.setRowCount(0)
        with patch.object(table, "_row_identity", side_effect=lambda r: {"ticker": list(rows)[r],
                                                                          "status": list(rows.values())[r],
                                                                          "name": "", "market": "KOSPI"}):
            table.setRowCount(3)
            for status, visible in (("", {"A", "B", "C"}), ("On", {"B"}), ("Tg", {"C"})):
                table.apply_col_filters("", status=status, market="ALL")
                shown = {t for i, t in enumerate(rows) if not table.isRowHidden(i)}
                self.assertEqual(shown, visible, status)


class TestRowStatusButton(unittest.TestCase):
    """The identity cell's fixed-size blank/Port/Target button cycles the
    row's status on click (user direction 2026-09-29); other clicks in the
    cell do not."""

    def _table(self):
        from ui.widgets import StockTable
        table = StockTable()
        table.resize(900, 300)
        table.show()
        rows = [{"name": n, "ticker": t, "market": "KOSPI", "market_cap": 1e11, "trailing_per": 10.0,
                 "forward_per": 9.0, "price": 100.0, "currency": "", "change_mode": "pct", "changes": {}}
                for t, n in (("000001", "A"), ("000002", "B"))]
        table.load_data(rows, highlights={"000002": "On"})
        return table

    def test_click_on_the_button_emits_toggle_for_that_row_only(self):
        from PyQt6.QtCore import QPoint, QRect, Qt
        from PyQt6.QtTest import QTest
        from ui.delegates import IdentityDelegate
        from ui.widgets import COL_IDENTITY
        table = self._table()
        seen = []
        table.toggle_requested.connect(seen.append)
        col_w = table.columnWidth(COL_IDENTITY)
        for row in (0, 1):
            cell = QRect(0, table.rowViewportPosition(row), col_w, table.rowHeight(row))
            btn = IdentityDelegate.status_button_rect(cell)
            QTest.mouseClick(table.viewport(), Qt.MouseButton.LeftButton, pos=btn.center())
            # the name area of the same cell is an ordinary click
            QTest.mouseClick(table.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(cell.x() + 20, cell.center().y()))
        self.assertEqual(seen, ["000001", "000002"])
        table.close()

    def test_button_rect_is_the_same_size_in_every_state(self):
        from PyQt6.QtCore import QRect
        from ui.delegates import IdentityDelegate, STATUS_BUTTON
        self.assertEqual(set(STATUS_BUTTON), {"-", "On", "Tg"})
        for w in (150, 260, 400):
            r = IdentityDelegate.status_button_rect(QRect(0, 0, w, 28))
            self.assertEqual((r.width(), r.height()), (IdentityDelegate.STATUS_BTN_W, IdentityDelegate.STATUS_BTN_H))
            self.assertEqual(r.right(), w - 1 - IdentityDelegate.STATUS_BTN_MARGIN)


if __name__ == "__main__":
    unittest.main()


class _FakeScoreThread:
    instances = []

    def __init__(self, items, params=None, n_top=10):
        self.items = list(items)
        self.n_top = n_top
        self.started = False
        self.progress = _FakeSignal()
        self.finished = _FakeSignal()
        _FakeScoreThread.instances.append(self)

    def start(self):
        self.started = True

    def isRunning(self):
        return False

    def isFinished(self):
        return not self.started

    def blockSignals(self, _b):
        pass


class _FakeSignal:
    def __init__(self):
        self.slots = []

    def connect(self, fn):
        self.slots.append(fn)

    def emit(self, *a):
        for s in self.slots:
            s(*a)


class TestTrendScoreButton(unittest.TestCase):
    """The Trend Score button scores the KR rows on a tracked worker and
    opens the recommendation dialog when the worker finishes."""

    def setUp(self):
        p = patch("ui.universe_tab.atomic_save_json")
        p.start()
        self.addCleanup(p.stop)
        _FakeScoreThread.instances.clear()

    def _tab(self):
        from ui.universe_tab import UniverseTab
        with patch("ui.universe_tab.safe_load_json", side_effect=_no_disk):
            tab = UniverseTab()
        tab.all_data = [
            _stock("005930", 500), {**_stock("035720", 50), "market": "KOSDAQ"},
            {**_stock("AAPL", 900), "market": "NASDAQ 100"},
            {**_stock("KS11", 0), "market": "Index", "is_index": True},
        ]
        return tab

    def test_scores_only_kr_equities_on_a_tracked_thread(self):
        tab = self._tab()
        with patch("ui.universe_tab.TrendScoreThread", _FakeScoreThread):
            tab._on_trend_score_clicked()
        self.assertEqual(len(_FakeScoreThread.instances), 1)
        worker = _FakeScoreThread.instances[0]
        self.assertTrue(worker.started)
        self.assertEqual([d["ticker"] for d in worker.items], ["005930", "035720"])
        self.assertEqual(worker.n_top, 10)
        self.assertFalse(tab.trend_score_btn.isEnabled())
        self.assertIn(worker, tab.collect_threads_to_stop())
        self.assertIs(tab._trend_score_thread, worker)

    def test_empty_universe_warns_instead_of_starting(self):
        tab = self._tab()
        tab.all_data = [{**_stock("AAPL", 900), "market": "NASDAQ 100"}]
        with patch("ui.universe_tab.TrendScoreThread", _FakeScoreThread), \
             patch("ui.universe_tab.QMessageBox.information") as info:
            tab._on_trend_score_clicked()
        info.assert_called_once()
        self.assertEqual(_FakeScoreThread.instances, [])
        self.assertTrue(tab.trend_score_btn.isEnabled())

    def test_finished_opens_the_dialog_or_reports_the_error(self):
        from strategy.trend_following.config import StrategyParams
        from strategy.trend_following.scoring import compute_scores, weekly_recommendation
        from tests.strategy.trend_following.test_scoring import score_book
        tab = self._tab()
        statuses = []
        tab.status_text_changed.connect(statuses.append)
        with patch("ui.universe_tab.TrendScoreThread", _FakeScoreThread):
            tab._on_trend_score_clicked()
        worker = _FakeScoreThread.instances[0]
        worker.progress.emit("Daily history 25/40")
        self.assertIn("Trend Score: Daily history 25/40", statuses[-1])

        with patch("ui.universe_tab.QMessageBox.warning") as warn:
            worker.finished.emit(None, "boom")
        warn.assert_called_once()
        self.assertTrue(tab.trend_score_btn.isEnabled())
        self.assertIsNone(getattr(tab, "_trend_score_dialog", None))

        book, _ = score_book()
        p = StrategyParams()
        rec = weekly_recommendation(compute_scores(book, p), book, p)
        with patch("ui.universe_tab.QMessageBox.warning") as warn:
            tab._on_trend_score_finished(rec, "")
        warn.assert_not_called()
        dlg = tab._trend_score_dialog
        self.assertEqual(dlg.top_table.rowCount(), len(rec.top))
        self.assertIn("2 top / 3 bottom", statuses[-1])
        # Double-clicking a row routes to the tab's MA-chart launcher.
        with patch.object(tab, "_show_stock_ma") as show:
            dlg.ticker_activated.emit("005930")
        show.assert_called_once()
        self.assertEqual(show.call_args[0][0], "005930")
        dlg.close()
