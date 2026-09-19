"""tests/test_history_table.py — ui.history_table cell factories and
fill_table_rows() column layout for the Trading History grid."""
import unittest

from PyQt6.QtWidgets import QApplication, QTableWidget, QLabel
from PyQt6.QtCore import Qt

app = QApplication.instance() or QApplication([])

from ui.history_table import si, ni, pi, wi, dash, loading_item, fill_table_rows  # noqa: E402

N_COLS = 21
DASH = "—"  # docs/ui.md 3.3: a thin, muted em dash instead of a bold "-"


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

    def test_numeric_cells_sort_by_value_not_display_text(self):
        # QTableWidgetItem aliases EditRole onto DisplayRole, so the old
        # setData(EditRole)+setText() pair sorted comma-formatted numbers as
        # strings ("1,849,000" < "999,000"). ni/pi/wi are NumericItems now.
        self.assertLess(ni(999_000), ni(1_849_000))
        self.assertLess(pi(-2.0), pi(10.0))
        self.assertLess(wi(9.5), wi(12.0))
        self.assertFalse(ni(1_849_000) < ni(999_000))

    def test_pct_item_colour_by_sign(self):
        self.assertEqual(pi(3.14).text(), "+3.1%")
        self.assertEqual(pi(-2.0).text(), "-2.0%")
        self.assertNotEqual(pi(1.0).foreground().color().name(), pi(-1.0).foreground().color().name())
        self.assertEqual(wi(12.34).text(), "12.3%")

    def test_plain_items(self):
        self.assertEqual(si("abc").text(), "abc")
        self.assertEqual(dash().text(), DASH)
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
        self.assertEqual(t[7:14], [DASH] * 7)            # sell section empty
        self.assertEqual(t[15:18], ["Total"] * 3)      # price not yet loaded
        self.assertEqual(t[18:21], [DASH] * 3)

    def test_open_row_identity_cell_carries_state_for_the_delegate(self):
        # Company (col 0)'s marker+badge is painted by TradeStateDelegate
        # from Qt.ItemDataRole.UserRole (docs/ui.md 1.7/issue #5) rather
        # than a bg_open background color.
        fill_table_rows(self.tbl, [("open", _rec())])
        data = self.tbl.item(0, 0).data(Qt.ItemDataRole.UserRole)
        self.assertEqual(data, {"state": "Open"})

    def test_closed_row_identity_cell_state_is_closed(self):
        rec = _rec(sell_date="2026-02-09", sell_price=120.0, sell_qty=10.0, sell_amount=1200.0)
        fill_table_rows(self.tbl, [("closed", rec)])
        data = self.tbl.item(0, 0).data(Qt.ItemDataRole.UserRole)
        self.assertEqual(data, {"state": "Closed"})

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
        self.assertEqual(t[16], DASH)   # closed rows never show a current P/L amount...

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
        self.assertEqual(t[14:21], [DASH] * 7)

    def test_hide_stale_closed_false_shows_position_and_past_columns(self):
        # docs/ui.md 3.5: the 30-day rule is now a caller-controlled toggle
        # rather than always-on.
        rec = _rec(sell_date="2026-01-20", sell_price=1.0, curr_days=45, curr_price=99.0, wk1=2.0)
        fill_table_rows(self.tbl, [("closed", rec)], hide_stale_closed=False)
        t = self._texts(0)
        self.assertEqual(t[14], "45")
        self.assertEqual(t[15], "99")

    def test_monthly_summary_row_spans_as_a_group_header(self):
        # docs/ui.md 3.4: a month summary is a group-header row now (a single
        # QLabel spanning every column), not per-column data cells subject
        # to the same sort/filter/double-click-edit path as a real trade.
        rec = {"company": "Monthly Summary [2026-01]", "buy_date": "2026-01", "buy_amount": 1500.0,
               "pl": -50.0, "trade_count": 3, "win_rate_pct": 0.0}
        out = fill_table_rows(self.tbl, [("monthly", rec)])
        self.assertEqual(out, [("monthly", rec)])
        self.assertEqual(self.tbl.columnSpan(0, 0), N_COLS)
        widget = self.tbl.cellWidget(0, 0)
        self.assertIsInstance(widget, QLabel)
        text = widget.text()
        self.assertIn("2026-01", text)
        self.assertIn("-50", text)

    def test_row_reused_from_monthly_to_trade_clears_span_and_widget(self):
        # A row index that held a group-header on one render and a normal
        # trade on the next (row count reused, see fill_table_rows) must not
        # keep the stale span/cellWidget from the earlier render.
        monthly = {"company": "Monthly Summary [2026-01]", "buy_date": "2026-01", "buy_amount": 0.0,
                   "pl": 0.0, "trade_count": 0, "win_rate_pct": None}
        fill_table_rows(self.tbl, [("monthly", monthly)])
        fill_table_rows(self.tbl, [("open", _rec())])
        self.assertEqual(self.tbl.columnSpan(0, 0), 1)
        self.assertIsNone(self.tbl.cellWidget(0, 0))
        self.assertEqual(self.tbl.item(0, 0).text(), "X")

    def test_row_count_follows_input(self):
        fill_table_rows(self.tbl, [("open", _rec()), ("open", _rec())])
        self.assertEqual(self.tbl.rowCount(), 2)
        fill_table_rows(self.tbl, [("open", _rec())])
        self.assertEqual(self.tbl.rowCount(), 1)


if __name__ == "__main__":
    unittest.main()
