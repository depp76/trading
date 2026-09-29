"""ui/strategy_tab.py + ui/trend_following_tab.py built headlessly: request
building from the form, the worker wiring, result population, error path."""
import unittest
from datetime import date
from unittest.mock import patch

from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QPixmap

app = QApplication.instance() or QApplication([])

from strategy.trend_following.research import ResearchRequest, run_research  # noqa: E402
from tests.strategy.trend_following.helpers import synthetic_dataset  # noqa: E402
from ui.strategy_tab import StrategyTab  # noqa: E402
from ui.trend_following_tab import TrendFollowingTab  # noqa: E402


class _FakeThread:
    instances = []

    def __init__(self, request):
        self.request = request
        self.started = False
        self.cancelled = False
        self.progress = _Sig()
        self.finished = _Sig()
        _FakeThread.instances.append(self)

    def start(self):
        self.started = True

    def cancel(self):
        self.cancelled = True

    def isRunning(self):
        return False

    def isFinished(self):
        return not self.started

    def blockSignals(self, _b):
        pass


class _Sig:
    def __init__(self):
        self.slots = []

    def connect(self, fn):
        self.slots.append(fn)

    def emit(self, *a):
        for s in self.slots:
            s(*a)


class TestTrendFollowingTab(unittest.TestCase):
    def test_request_reflects_the_form(self):
        tab = TrendFollowingTab()
        tab.universe_spin.setValue(120)
        tab.liquidity_spin.setValue(50)
        tab.positions_spin.setValue(8)
        tab.weekday_combo.setCurrentIndex(2)
        tab.cost_combo.setCurrentIndex(1)
        tab.variant_cbs["A3"].setChecked(False)
        tab.flows_cb.setChecked(True)
        tab.event_cb.setChecked(False)
        req = tab.build_request()
        self.assertEqual(req.params.universe_size, 120)
        self.assertEqual(req.params.min_avg_trading_value, 50e8)
        self.assertEqual(req.params.max_positions, 8)
        self.assertEqual(req.params.check_weekday, 2)
        self.assertEqual(req.cost_multipliers, (1.0,))
        self.assertNotIn("A3", req.variant_ids)
        self.assertIn("B", req.variant_ids)
        self.assertTrue(req.include_flows)
        self.assertFalse(req.run_event_study)
        self.assertEqual(req.start, date(2021, 1, 1))

    def test_run_starts_a_tracked_worker_and_disables_the_button(self):
        tab = TrendFollowingTab()
        _FakeThread.instances.clear()
        with patch("ui.trend_following_tab.TrendFollowingResearchThread", _FakeThread):
            tab._on_run_clicked()
        self.assertEqual(len(_FakeThread.instances), 1)
        worker = _FakeThread.instances[0]
        self.assertTrue(worker.started)
        self.assertFalse(tab.run_btn.isEnabled())
        self.assertIs(tab._research_thread, worker)
        worker.progress.emit("Loading...")
        self.assertEqual(tab.status_lbl.text(), "Loading...")
        worker.finished.emit(None, "boom")
        self.assertTrue(tab.run_btn.isEnabled())
        self.assertIn("boom", tab.status_lbl.text())
        self.assertFalse(tab.save_btn.isEnabled())

    def test_stop_cancels_the_worker_and_reports_cancelled(self):
        from threads.strategy_threads import CANCELLED_MESSAGE
        tab = TrendFollowingTab()
        self.assertFalse(tab.stop_btn.isEnabled())
        _FakeThread.instances.clear()
        with patch("ui.trend_following_tab.TrendFollowingResearchThread", _FakeThread):
            tab._on_run_clicked()
        worker = _FakeThread.instances[0]
        self.assertTrue(tab.stop_btn.isEnabled())
        with patch.object(worker, "isRunning", return_value=True):
            tab._on_stop_clicked()
        self.assertTrue(worker.cancelled)
        self.assertFalse(tab.stop_btn.isEnabled())
        self.assertIn("Stopping", tab.status_lbl.text())
        worker.finished.emit(None, CANCELLED_MESSAGE)
        self.assertEqual(tab.status_lbl.text(), CANCELLED_MESSAGE)
        self.assertTrue(tab.run_btn.isEnabled())
        self.assertFalse(tab.save_btn.isEnabled())

    def test_populate_from_a_real_result(self):
        ds = synthetic_dataset(with_flows=True)
        req = ResearchRequest(start=date(2021, 1, 4), variant_ids=("A0", "A5"), cost_multipliers=(1.0,),
                              include_flows=True)
        result = run_research(ds, req)
        tab = TrendFollowingTab()
        tab._on_research_finished(result, "")
        self.assertEqual(tab.table.rowCount(), len(result.runs))
        self.assertEqual(tab.table.item(0, 0).text(), "A0")      # strategies first at 1x
        self.assertEqual(tab.table.item(0, 1).text(), "1x")
        self.assertEqual(tab.event_table.rowCount(), len(result.event_study))
        self.assertTrue(tab.save_btn.isEnabled())
        self.assertIn("Done", tab.status_lbl.text())
        self.assertGreater(len(tab.ax.lines), 0)
        tab.resize(1400, 900)
        tab.render(QPixmap(1400, 900))

    def test_save_writes_markdown(self):
        import os
        import tempfile
        ds = synthetic_dataset()
        result = run_research(ds, ResearchRequest(start=date(2021, 1, 4), variant_ids=("A0",),
                                                  cost_multipliers=(1.0,), run_event_study=False))
        tab = TrendFollowingTab()
        tab._on_research_finished(result, "")
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "r.md")
            with patch("ui.trend_following_tab.QFileDialog.getSaveFileName", return_value=(path, "")):
                tab._on_save_clicked()
            with open(path, encoding="utf-8") as f:
                self.assertIn("# Trend Following backtest report", f.read())

    def test_rejects_bad_window(self):
        tab = TrendFollowingTab()
        tab.start_edit.setDate(tab.end_edit.date())
        with patch("ui.trend_following_tab.QMessageBox.warning") as warn, \
             patch("ui.trend_following_tab.TrendFollowingResearchThread", _FakeThread):
            _FakeThread.instances.clear()
            tab._on_run_clicked()
        warn.assert_called_once()
        self.assertEqual(_FakeThread.instances, [])


class TestStrategyTab(unittest.TestCase):
    def test_has_the_trend_following_sub_tab_and_merges_threads(self):
        tab = StrategyTab()
        self.assertEqual(tab.sub_tabs.count(), 1)
        self.assertEqual(tab.sub_tabs.tabText(0), "Trend Following")
        self.assertEqual(tab.collect_threads_to_stop(), [])
        _FakeThread.instances.clear()
        with patch("ui.trend_following_tab.TrendFollowingResearchThread", _FakeThread):
            tab.trend_following_tab._on_run_clicked()
        self.assertEqual(len(tab.collect_threads_to_stop()), 1)


if __name__ == "__main__":
    unittest.main()
