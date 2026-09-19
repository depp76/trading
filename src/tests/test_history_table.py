"""tests/test_history_table.py — ui.history_table cell factories and
fill_table_rows() column layout for the Trading History grid."""
import unittest

from PyQt6.QtWidgets import QApplication, QTableWidget
from PyQt6.QtCore import Qt

app = QApplication.instance() or QApplication([])

from ui.history_table import si, ni, pi, wi, dash, loading_item, fill_table_rows  # noqa: E402

N_COLS = 21


def _rec(**kw):
    base = {
        "company": "X", "market": "KOSPI", "ticker": "005930", "buy_date": "2026-01-10",
        "buy_price": 100.0, "qty": 10.0, "buy_amount": 1000.0,
        "sell_date": "", "sell_price": 0.0, "sell_qty": 0.0, "sell_amount": 0.0,
        "pl": 0.0, "pl_pct": 0.0, "days_held": 0, "curr_days": 0,
        "curr_price": 0.0, "curr_pl": 0.0, "curr_pl_pct": 0.0,
        "wk1": 0.0, "wk2": 0.0, "mth1": 0.0,
    }
    base.update(kw)
    return base


class TestCellFactories(unittest.TestCase):

    def test_numeric_item_formats_with_thousands_separator(self):
        self.assertEqual(ni(1234.5678).text(), "1,235")
        self.assertEqual(ni(0.1234, "{:.2f}").text(), "0.12")
        # Note: QTableWidgetItem treats DisplayRole and EditRole as one value, so
        # the float written via setData is replaced by the text. The history grid
        # has sorting disabled, so nothing relies on the numeric EditRole.
        self.assertEqual(ni(1234.5678).data(Qt.ItemDataRole.EditRole), "1,235")

    def test_pct_item_colour_by_sign(self):
        self.assertEqual(pi(3.14).text(), "+3.1%")
        self.assertEqual(pi(-2.0).text(), "-2.0%")
        self.assertNotEqual(pi(1.0).foreground().color().name(), pi(-1.0).foreground().color().name())
        self.assertEqual(wi(12.34).text(), "12.3%")

    def test_plain_items(self):
        self.assertEqual(si("abc").text(), "abc")
        self.assertEqual(dash().text(), "-")
        self.assertEqual(loading_item().text(), "Total")


class TestFillTableRows(unittest.TestCase):

    def setUp(self):
        self.tbl = QTableWidget(0, N_COLS)

    def _texts(self, row):
        return [self.tbl.item(row, c).text() if self.tbl.item(row, c) else None for c in range(N_COLS)]

    def test_open_row_without_price_shows_loading_cells(self):
        rows = [("open", _rec())]
        out = fill_table_rows(self.tbl, rows)
        self.assertEqual(out, rows)
        t = self._texts(0)
        self.assertEqual(t[0], "X")
        self.assertEqual(t[3], "2026-01-10")
        self.assertEqual(t[7:14], ["-"] * 7)            # sell section empty
        self.assertEqual(t[15:18], ["Total"] * 3)      # price not yet loaded
        self.assertEqual(t[18:21], ["-"] * 3)

    def test_open_row_with_price_shows_pl(self):
        rec = _rec(curr_price=120.0, curr_pl=200.0, curr_pl_pct=20.0, curr_days=5, wk1=1.5)
        fill_table_rows(self.tbl, [("open", rec)])
        t = self._texts(0)
        self.assertEqual(t[14], "5")
        self.assertEqual(t[15], "120")
        self.assertEqual(t[16], "200")
        self.assertEqual(t[17], "+20.0%")
        self.assertEqual(t[18], "+1.5%")

    def test_closed_row_fills_sell_section(self):
        rec = _rec(sell_date="2026-02-09", sell_price=120.0, sell_qty=10.0, sell_amount=1200.0,
                   pl=200.0, pl_pct=20.0, days_held=30, curr_days=3, curr_price=125.0)
        fill_table_rows(self.tbl, [("closed", rec)])
        t = self._texts(0)
        self.assertEqual(t[7], "2026-02-09")
        self.assertEqual(t[8], "30")
        self.assertEqual(t[12], "200")
        self.assertEqual(t[13], "+20.0%")
        self.assertEqual(t[16], "-")   # closed rows never show a current P/L amount...

    def test_closed_today_shows_opportunity_pl(self):
        rec = _rec(sell_date="2026-09-19", sell_price=100.0, sell_qty=10.0, sell_amount=1000.0,
                   curr_days=0, curr_price=110.0)
        fill_table_rows(self.tbl, [("closed", rec)])
        t = self._texts(0)
        self.assertEqual(t[16], "100")      # ...except when sold today: (110-100)*10
        self.assertEqual(t[17], "+10.0%")

    def test_stale_closed_row_hides_position_and_past_columns(self):
        rec = _rec(sell_date="2026-01-20", sell_price=1.0, curr_days=45, curr_price=99.0, wk1=2.0)
        fill_table_rows(self.tbl, [("closed", rec)])
        t = self._texts(0)
        self.assertEqual(t[14:21], ["-"] * 7)

    def test_monthly_summary_row_is_bold_with_total(self):
        rec = {"company": "Monthly Summary [2026-01]", "buy_date": "2026-01", "buy_amount": 1500.0,
               "pl": -50.0}
        fill_table_rows(self.tbl, [("monthly", rec)])
        t = self._texts(0)
        self.assertEqual(t[0], "Monthly Summary [2026-01]")
        self.assertEqual(t[6], "1,500")
        self.assertEqual(t[12], "-50")
        self.assertTrue(self.tbl.item(0, 0).font().bold())

    def test_row_count_follows_input(self):
        fill_table_rows(self.tbl, [("open", _rec()), ("open", _rec())])
        self.assertEqual(self.tbl.rowCount(), 2)
        fill_table_rows(self.tbl, [("open", _rec())])
        self.assertEqual(self.tbl.rowCount(), 1)


if __name__ == "__main__":
    unittest.main()
