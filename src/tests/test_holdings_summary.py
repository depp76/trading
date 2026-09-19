"""tests/test_holdings_summary.py — the Holdings Summary dialog sorts by
value (NumericItem) and colors P/L with the PROFIT/LOSS tokens."""
import unittest

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

app = QApplication.instance() or QApplication([])

from ui.dialogs.holdings_summary import build_holdings_summary  # noqa: E402
from ui.colors import QC_PROFIT, QC_LOSS  # noqa: E402


def _closed(company, buy, sell):
    return {"company": company, "buy_amount": buy, "sell_amount": sell, "pl": sell - buy,
            "days_held": 10, "buy_date": "2026-01-01"}


class TestHoldingsSummary(unittest.TestCase):

    def test_none_when_empty(self):
        self.assertIsNone(build_holdings_summary(None, [], []))

    def test_default_order_is_pl_desc_and_numeric_sort_works(self):
        dlg = build_holdings_summary(None, [
            _closed("Small", 1_000, 1_200),          # +200
            _closed("Big", 1_000_000, 1_849_000),    # +849,000
            _closed("Loss", 500_000, 300_000),       # -200,000
        ], [])
        tbl = dlg.table
        names = [tbl.item(r, 0).text() for r in range(tbl.rowCount())]
        self.assertEqual(names, ["Big", "Small", "Loss"])
        self.assertTrue(tbl.isSortingEnabled())

        # Sorting "Total Buy" ascending must be numeric, not "1,000,000" < "500,000" text order.
        tbl.sortItems(1, Qt.SortOrder.AscendingOrder)
        self.assertEqual([tbl.item(r, 0).text() for r in range(tbl.rowCount())], ["Small", "Loss", "Big"])

        colors = {tbl.item(r, 0).text(): tbl.item(r, 3).foreground().color().name() for r in range(tbl.rowCount())}
        self.assertEqual(colors["Big"], QC_PROFIT.name())
        self.assertEqual(colors["Loss"], QC_LOSS.name())


if __name__ == "__main__":
    unittest.main()
