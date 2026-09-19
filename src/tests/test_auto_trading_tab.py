"""tests/test_auto_trading_tab.py — AutoTradingTab table population (batched repaint)."""
import unittest
from unittest.mock import MagicMock

from PyQt6.QtWidgets import QApplication, QTableWidget

from ui.auto_trading_tab import AutoTradingTab

_qapp = QApplication.instance() or QApplication([])


class TestAutoTradingTabRepaintOptimization(unittest.TestCase):
    """Test AutoTradingTab batch table population repaint optimization."""

    def test_fill_candidate_table_set_updates_enabled(self):
        tbl = QTableWidget(0, 5)
        # Mock setUpdatesEnabled to track calls
        tbl.setUpdatesEnabled = MagicMock()
        rows = [
            {"rank": 1, "ticker": "005930", "name": "Samsung", "market": "KOSPI", "score": 88.5},
        ]
        AutoTradingTab._fill_candidate_table(tbl, rows)

        # Ensure called with False first, then True
        tbl.setUpdatesEnabled.assert_any_call(False)
        tbl.setUpdatesEnabled.assert_any_call(True)
        self.assertEqual(tbl.setUpdatesEnabled.call_count, 2)
        self.assertEqual(tbl.rowCount(), 1)


if __name__ == "__main__":
    unittest.main()
