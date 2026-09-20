"""tests/test_assets_tab.py — TradingRecordTab's sort-safe record lookups.

review_agy.md #1: _on_cell_double_clicked/_delete_selected used to index
self._records (always ascending by date) with the QTableWidget's visual row
index, which no longer matches once the table's default descending sort
indicator (set in _build_ui) re-sorts the rows. Both now look up the record
by the date bound to column 0's UserRole instead.
"""
import unittest
from unittest.mock import patch

from PyQt6.QtWidgets import QApplication, QMessageBox

app = QApplication.instance() or QApplication([])


def _make_tab():
    from ui.assets_tab import TradingRecordTab
    with patch("ui.assets_tab.TradingRecordTab._start_metrics_preload"), \
         patch("ui.assets_tab.TradingRecordTab._schedule_daily_sync"), \
         patch("ui.assets_tab.trade_db.load_asset_records", return_value=[]):
        tab = TradingRecordTab()
    tab._metrics_ready = False  # skip network rate/kospi lookups
    return tab


class TestSortSafeRecordLookup(unittest.TestCase):

    def setUp(self):
        self.tab = _make_tab()
        self.tab._records = [
            {"date": "2026-01-02", "total": 1000.0, "manual": True},
            {"date": "2026-01-09", "total": 2000.0, "manual": True},
            {"date": "2026-01-16", "total": 3000.0, "manual": True},
        ]
        self.tab._refresh_table()
        # Default sort indicator is descending by date (column 0), so
        # visual row 0 is 2026-01-16 even though self._records[0] is
        # 2026-01-02 -- the exact mismatch this fix targets.
        self.assertEqual(self.tab._records[0]["date"], "2026-01-02")
        self.assertIn("2026-01-16", self.tab._table.item(0, 0).text())

    def test_double_click_edits_the_visually_selected_date_not_records0(self):
        with patch("ui.assets_tab.QInputDialog.getText", return_value=("3,500", True)):
            self.tab._on_cell_double_clicked(0, 4)  # visually the 2026-01-16 row
        edited = next(r for r in self.tab._records if r["date"] == "2026-01-16")
        self.assertEqual(edited["total"], 3500.0)
        self.assertTrue(edited["manual"])
        # The oldest record (self._records[0] before the fix) must be untouched.
        untouched = next(r for r in self.tab._records if r["date"] == "2026-01-02")
        self.assertEqual(untouched["total"], 1000.0)

    def test_delete_selected_removes_the_visually_selected_date_not_records0(self):
        self.tab._table.selectRow(0)  # visually the 2026-01-16 row
        with patch("ui.assets_tab.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
            self.tab._delete_selected()
        remaining_dates = {r["date"] for r in self.tab._records}
        self.assertEqual(remaining_dates, {"2026-01-02", "2026-01-09"})


class TestMultiYearDateCombo(unittest.TestCase):
    """review_agy.md #3: the Friday-date combo used to only generate the
    current year's Fridays, so a record from a prior year couldn't be
    selected/edited at all."""

    def test_combo_extends_back_to_the_earliest_record_year(self):
        from ui.assets_tab import TradingRecordTab
        with patch("ui.assets_tab.TradingRecordTab._start_metrics_preload"), \
             patch("ui.assets_tab.TradingRecordTab._schedule_daily_sync"), \
             patch("ui.assets_tab.trade_db.load_asset_records",
                   return_value=[{"date": "2024-03-08", "total": 1000.0, "manual": True}]):
            tab = TradingRecordTab()

        self.assertEqual(tab._date_combo_start_year, 2024)
        dates = {tab._date_combo.itemData(i) for i in range(tab._date_combo.count())}
        self.assertIn("2024-03-08", dates)

    def test_selected_date_reads_currentData_not_a_regenerated_zip(self):
        tab = _make_tab()
        idx = tab._date_combo.count() - 1
        tab._date_combo.setCurrentIndex(idx)
        self.assertEqual(tab._selected_date(), tab._date_combo.itemData(idx))


if __name__ == "__main__":
    unittest.main()
