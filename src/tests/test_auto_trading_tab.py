"""tests/test_auto_trading_tab.py — AutoTradingTab table population (batched repaint)."""
import unittest
from unittest.mock import MagicMock

from PyQt6.QtWidgets import QApplication, QTableWidget
from PyQt6.QtCore import Qt

from ui.auto_trading_tab import AutoTradingTab, _SIGNAL_COLUMNS, COL_STOCK, COL_ACTION, COL_HELD

_qapp = QApplication.instance() or QApplication([])


def _row(**kw):
    base = {
        "ticker": "005930", "name": "Samsung", "market": "KOSPI",
        "rank": 1, "action": "Buy", "color": "#2e7d5b",
        "score": 1.5, "score_pct": 0.8, "raw": {}, "factors": {}, "held": False,
    }
    base.update(kw)
    return base


class TestAutoTradingTabRepaintOptimization(unittest.TestCase):
    """Merged ranking table (docs/ui.md Strategy Redesign issue #5) batch
    table population repaint optimization -- _fill_candidate_table's
    replacement."""

    def _tab(self):
        tab = AutoTradingTab.__new__(AutoTradingTab)  # skip __init__'s full UI build
        tab._signal_table = QTableWidget(0, len(_SIGNAL_COLUMNS))
        return tab

    def test_fill_signal_table_set_updates_enabled(self):
        tab = self._tab()
        tab._signal_table.setUpdatesEnabled = MagicMock()
        tab._fill_signal_table([_row()])

        tab._signal_table.setUpdatesEnabled.assert_any_call(False)
        tab._signal_table.setUpdatesEnabled.assert_any_call(True)
        self.assertEqual(tab._signal_table.setUpdatesEnabled.call_count, 2)
        self.assertEqual(tab._signal_table.rowCount(), 1)

    def test_fill_signal_table_sets_row_data(self):
        tab = self._tab()
        tab._fill_signal_table([_row(held=True)])

        stock_data = tab._signal_table.item(0, COL_STOCK).data(Qt.ItemDataRole.UserRole)
        self.assertEqual(stock_data["name"], "Samsung")
        self.assertEqual(stock_data["meta"], "005930 · KOSPI")

        action_data = tab._signal_table.item(0, COL_ACTION).data(Qt.ItemDataRole.UserRole)
        self.assertEqual(action_data["action"], "Buy")

        self.assertEqual(tab._signal_table.item(0, COL_HELD).text(), "●")


if __name__ == "__main__":
    unittest.main()
