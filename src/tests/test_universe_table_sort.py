"""tests/test_universe_table_sort.py — regression coverage for the Phase 2
sort/refresh data-integrity fix (docs/ui.md 1.9, roadmap Phase 2).

Before the fix, StockTable.update_changed_rows() treated `changed_rows`
(indices into the `data` list) as view row numbers. With
setSortingEnabled(True), sorting the table by a column reorders the on-screen
rows without reordering `data`, so a lightweight refresh after a user sort
wrote a changed ticker's new price into whatever row happened to sit at that
index on screen -- a different stock entirely.

Column indices below are the docs/ui.md 2.2 redesign's 13-column layout
(col 0 = merged identity cell, col 1 = Price); ticker/name/status live in
col 0's Qt.ItemDataRole.UserRole payload rather than separate columns.
"""
import unittest

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt

app = QApplication.instance() or QApplication([])

from ui.widgets import StockTable, COL_IDENTITY, COL_PRICE  # noqa: E402


def _mk(ticker, name, price, market_cap=100_000_000_000):
    return {
        "name": name, "ticker": ticker, "market": "KOSPI",
        "market_cap": market_cap, "trailing_per": 10.0, "forward_per": 9.0,
        "price": price, "currency": "", "change_mode": "pct", "changes": {},
    }


def _tickers(table):
    return [table.item(r, COL_IDENTITY).data(Qt.ItemDataRole.UserRole)["ticker"] for r in range(table.rowCount())]


class TestUpdateChangedRowsAfterSort(unittest.TestCase):

    def test_incremental_update_targets_the_correct_ticker_after_sort(self):
        table = StockTable()
        data = [
            _mk("000001", "A", 100),
            _mk("000002", "B", 300),
            _mk("000003", "C", 200),
        ]
        table.load_data(data)

        # Sort ascending by Price, as a user clicking the header would:
        # 100 (000001) / 200 (000003) / 300 (000002).
        table._filter_header.setSortIndicator(COL_PRICE, Qt.SortOrder.AscendingOrder)
        self.assertEqual(_tickers(table), ["000001", "000003", "000002"])

        # data index 1 is ticker 000002, which now sits at view row 2, not
        # view row 1. Simulate the 60s lightweight refresh reporting a new
        # price for it.
        updated = [
            _mk("000001", "A", 100),
            _mk("000002", "B", 999),
            _mk("000003", "C", 200),
        ]
        table.update_changed_rows(updated, {1})

        # The row actually showing 000002 (view row 2) got the new price...
        self.assertEqual(table.item(2, COL_IDENTITY).data(Qt.ItemDataRole.UserRole)["ticker"], "000002")
        self.assertEqual(table.item(2, COL_PRICE).text(), "999")
        # ...and the row in between (000003, view row 1) was left untouched
        # rather than being overwritten with 000002's data.
        self.assertEqual(table.item(1, COL_IDENTITY).data(Qt.ItemDataRole.UserRole)["ticker"], "000003")
        self.assertEqual(table.item(1, COL_PRICE).text(), "200")

    def test_unknown_or_out_of_range_changed_index_is_skipped(self):
        table = StockTable()
        data = [_mk("000001", "A", 100)]
        table.load_data(data)
        # Should not raise for an out-of-range data index.
        table.update_changed_rows(data, {5})
        self.assertEqual(table.item(0, COL_PRICE).text(), "100")


class TestSortStateSurvivesReload(unittest.TestCase):

    def test_load_data_reapplies_the_users_sort_instead_of_resetting_it(self):
        table = StockTable()
        data = [
            _mk("000001", "A", 100),
            _mk("000002", "B", 300),
            _mk("000003", "C", 200),
        ]
        table.load_data(data)
        self.assertEqual(_tickers(table), ["000001", "000002", "000003"])  # insertion order before any sort

        table._filter_header.setSortIndicator(COL_PRICE, Qt.SortOrder.AscendingOrder)
        sorted_order = _tickers(table)
        self.assertEqual(sorted_order, ["000001", "000003", "000002"])

        # A full reload (e.g. after a manual refresh) must not silently drop
        # back to insertion order.
        table.load_data(data)
        self.assertEqual(_tickers(table), sorted_order)

    def test_first_load_still_defaults_to_insertion_order(self):
        table = StockTable()
        data = [_mk("000002", "B", 300), _mk("000001", "A", 100)]
        table.load_data(data)
        self.assertEqual(_tickers(table), ["000002", "000001"])


class TestNumericSortIsNumericNotLexicographic(unittest.TestCase):
    """Regression for a second, deeper bug found while rebuilding this
    table: QTableWidgetItem aliases Qt.ItemDataRole.EditRole onto the same
    storage as Qt.ItemDataRole.DisplayRole, so the old
    `setData(EditRole, num); setText(formatted)` pattern silently discarded
    the numeric sort key -- a comma-formatted Price/Cap column sorted as
    plain text ("1,849,000" before "999,000"). NumericItem fixes this by
    keeping its own sort key via __lt__."""

    def test_price_sorts_numerically_not_lexicographically(self):
        table = StockTable()
        data = [
            _mk("000001", "A", 999_000),
            _mk("000002", "B", 1_849_000),
            _mk("000003", "C", 50_000),
        ]
        table.load_data(data)
        table._filter_header.setSortIndicator(COL_PRICE, Qt.SortOrder.AscendingOrder)
        self.assertEqual(_tickers(table), ["000003", "000001", "000002"])


class TestIndexRowsFormatByChangeMode(unittest.TestCase):
    """Index / yield / commodity rows live in the same table as equities
    (user direction 2026-09-19); change_mode sets the price/change unit and
    the cap cell shows "-" where there is no market cap."""

    def _row(self, table, ticker):
        for r in range(table.rowCount()):
            if table.item(r, COL_IDENTITY).data(Qt.ItemDataRole.UserRole)["ticker"] == ticker:
                return r
        raise AssertionError(ticker)

    def test_price_and_change_units(self):
        from ui.widgets import COL_CHG, COL_CAP, COL_D3
        table = StockTable()
        table.load_data([
            {"ticker": "KS11", "name": "KOSPI", "market": "Index", "is_index": True, "price": 2500.126,
             "market_cap": float("inf"), "changes": {"1d": 1.23, "3d": -0.5}, "change_mode": "pct"},
            {"ticker": "KR3YT", "name": "KR 3Y", "market": "Index", "is_index": True, "is_bond": True, "price": 3.125,
             "currency": "%", "market_cap": float("inf"), "changes": {"1d": -2.4, "3d": 5.0}, "change_mode": "bp"},
            {"ticker": "^VIX", "name": "VIX", "market": "Index", "is_index": True, "price": 18.4,
             "market_cap": float("inf"), "changes": {"1d": 0.75}, "change_mode": "abs"},
            {"ticker": "CL=F", "name": "WTI", "market": "Index", "is_index": True, "price": 88.5, "usd_price": 88.5,
             "currency": "$", "market_cap": float("inf"), "changes": {"1d": -0.7}, "change_mode": "abs"},
            _mk("005930", "Samsung", 70000),
        ])
        cell = lambda t, c: table.item(self._row(table, t), c).text()  # noqa: E731
        self.assertEqual((cell("KS11", COL_PRICE), cell("KS11", COL_CHG), cell("KS11", COL_D3)), ("2,500.13", "+1.2%", "-0.5%"))
        self.assertEqual((cell("KR3YT", COL_PRICE), cell("KR3YT", COL_CHG), cell("KR3YT", COL_D3)), ("3.12%", "-2bp", "+5bp"))
        self.assertEqual((cell("^VIX", COL_PRICE), cell("^VIX", COL_CHG)), ("18.40", "+0.75"))
        self.assertEqual((cell("CL=F", COL_PRICE), cell("CL=F", COL_CHG)), ("$88.50", "-0.70"))
        self.assertEqual(cell("KS11", COL_CAP), "-")
        self.assertEqual(cell("005930", COL_CAP), "1,000")   # 100,000,000,000 KRW -> 1,000 eok


class TestFrozenColumnTracksIdentityWidth(unittest.TestCase):
    """The frozen Name overlay is a second QTableWidget whose own column
    used to stay at Qt's 100px default while its widget was resized to
    column 0's full width -- the overlay painted the identity cell at 100px
    and blank viewport for the rest, which showed up on screen as an empty
    gap between Name and Price."""

    def test_frozen_column_width_matches_column_0_after_stretch(self):
        table = StockTable()
        table.resize(1400, 600)
        table.show()
        table.load_data([_mk("000001", "A", 100), _mk("000002", "B", 200)])
        app.processEvents()
        self.assertGreater(table.columnWidth(COL_IDENTITY), 100)
        self.assertEqual(table._frozen.columnWidth(0), table.columnWidth(COL_IDENTITY))
        self.assertEqual(table._frozen.width(), table.columnWidth(COL_IDENTITY))

    def test_columns_fill_the_whole_viewport(self):
        # The old 0.99 viewport factor left a ~1% blank strip at the right
        # edge (14px on a 1398px viewport).
        table = StockTable()
        table.resize(1400, 600)
        table.show()
        table.load_data([_mk("000001", "A", 100)])
        app.processEvents()
        total = sum(table.columnWidth(c) for c in range(table.columnCount()) if not table.isColumnHidden(c))
        self.assertEqual(total, table.viewport().width())

    def test_frozen_column_follows_a_manual_header_resize(self):
        table = StockTable()
        table.resize(1400, 600)
        table.show()
        table.load_data([_mk("000001", "A", 100)])
        app.processEvents()
        table.setColumnWidth(COL_IDENTITY, 333)
        app.processEvents()
        self.assertEqual(table._frozen.columnWidth(0), 333)
        self.assertEqual(table._frozen.width(), 333)


if __name__ == "__main__":
    unittest.main()
