"""tests/test_stock_ma_dialog.py — StockMaDialog after the docs/ui.md "MA Chart
Redesign" (2026-09-19): PROFIT/LOSS volume, accent MA ramp, right-edge value
labels instead of a legend, exclusive view segment, period buttons, panel
toggles and the shared crosshair + readout. Synthetic data only."""
import unittest
from datetime import date, timedelta

import numpy as np
import polars as pl
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QPixmap

app = QApplication.instance() or QApplication([])

from data.indicators import _compute_indicators  # noqa: E402
from ui.dialogs.stock_ma import StockMaDialog  # noqa: E402
from ui.colors import PROFIT, LOSS, MA_RAMP  # noqa: E402


def _df(n=400, ew=False, volume=True):
    rng = np.random.default_rng(1)
    close = 100000 * np.cumprod(1 + rng.normal(0.0005, 0.02, n))
    dates = [date(2025, 1, 1) + timedelta(days=i) for i in range(n)]
    df = pl.DataFrame({
        "Date": dates, "Open": close * (1 + rng.normal(0, 0.005, n)), "High": close * 1.01, "Low": close * 0.99,
        "Close": close, "Volume": (rng.random(n) * 5e6) if volume else np.zeros(n),
    }).with_columns(pl.col("Date").cast(pl.Datetime))
    df = _compute_indicators(df, windows=(5, 10, 20, 60))
    if ew:
        df = df.with_columns((pl.col("Close") * 0.5 + 1000).alias("EqualWeight"))
    return df


class _Ev:
    """The few MouseEvent attributes the handlers read."""
    def __init__(self, ax, xdata, x=0, button=None):
        self.inaxes, self.xdata, self.x, self.button = ax, xdata, x, button


def _rgb(hex_color):
    return tuple(round(int(hex_color[i:i + 2], 16) / 255, 2) for i in (1, 3, 5))


class TestStockChart(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.dlg = StockMaDialog("005930", "Samsung", "KOSPI", _df(ew=True),
                                investor_data=[{"Date": "2026-09-19", "Close": 1000, "Foreigner": 5,
                                                "Institution": -3, "Retail": 0}])

    def test_panels_and_default_view(self):
        d = self.dlg
        self.assertEqual(list(d._panel_axes), ["price", "vol", "div", "rsi"])
        self.assertEqual(d._panel_on, {"vol": True, "div": True, "rsi": False})
        self.assertEqual({k: d._lines[k].get_visible() for k in MA_RAMP},
                         {"MA5": True, "MA10": True, "MA20": True, "MA50": False})
        self.assertIsNone(d._panel_axes["price"].get_legend())      # issue #3: no legend
        for line in d._lines.values():
            self.assertEqual(line.get_marker(), "None")             # issue #8: no markers

    def test_volume_bars_use_profit_loss(self):
        cols = [tuple(c.get_facecolor()[0][:3].round(2)) for c in self.dlg._panel_axes["vol"].collections]
        self.assertEqual(cols, [_rgb(PROFIT), _rgb(LOSS)])

    def test_ma_lines_are_the_accent_ramp(self):
        for key, color in MA_RAMP.items():
            self.assertEqual(self.dlg._lines[key].get_color(), color)

    def test_end_labels_follow_the_visible_lines(self):
        d = self.dlg
        d._set_view("short")
        self.assertEqual(set(d._end_labels), {"Close", "MA5", "MA10", "MA20"})
        d._set_view("long")
        self.assertEqual(set(d._end_labels), {"Close", "MA20", "MA50"})
        d._set_view("close")
        self.assertEqual(set(d._end_labels), {"Close"})
        d._set_view("ew")
        self.assertTrue(d._lines["EW"].get_visible())
        self.assertEqual(set(d._end_labels), {"Close", "EW"})
        d._set_view("short")

    def test_period_buttons_set_the_window(self):
        d, ax = self.dlg, self.dlg._panel_axes["price"]
        d._apply_period("1M")
        self.assertAlmostEqual(ax.get_xlim()[1] - ax.get_xlim()[0], 31, delta=1)
        self.assertTrue(d._period_buttons["1M"].isChecked())
        d._apply_period("All")
        self.assertGreater(ax.get_xlim()[1] - ax.get_xlim()[0], 399)
        d._apply_period("1Y")

    def test_panel_toggles_and_divergence_view_regrid(self):
        d = self.dlg
        d._set_view("div")
        self.assertEqual(d._visible_panels(), [("price", 2), ("vol", 1), ("div", 2)])
        d._set_panel("rsi", True)
        self.assertEqual([k for k, _ in d._visible_panels()], ["price", "vol", "div", "rsi"])
        self.assertTrue(d._panel_axes["rsi"].get_visible())
        d._set_panel("vol", False)
        self.assertFalse(d._panel_axes["vol"].get_visible())
        d._set_panel("vol", True)
        d._set_panel("rsi", False)
        d._set_view("short")

    def test_crosshair_and_readout(self):
        d, ax = self.dlg, self.dlg._panel_axes["price"]
        d._on_motion(_Ev(ax, d._x[100]))
        self.assertEqual(d._cursor_idx, 100)
        self.assertTrue(all(c.get_visible() for c in d._crosshairs))
        self.assertIn("2025-04-11", d._readout_lbl.text())
        self.assertIn("Close", d._readout_lbl.text())
        self.assertIn("Div(20)", d._readout_lbl.text())
        d._on_leave(None)
        self.assertIsNone(d._cursor_idx)
        self.assertFalse(any(c.get_visible() for c in d._crosshairs))
        # Readout falls back to the latest bar.
        self.assertIn("2026-02-04", d._readout_lbl.text())

    def test_header_and_render(self):
        d = self.dlg
        self.assertEqual(d._price_lbl.text(), "59,260")
        self.assertTrue(d._chg_lbl.text().endswith("%"))
        pm = QPixmap(1200, 720)
        d.render(pm)


class TestInvestorTableFitsItsNumbers(unittest.TestCase):

    def test_columns_are_wide_enough_for_share_counts(self):
        from PyQt6.QtGui import QFontMetrics
        from PyQt6.QtWidgets import QTableWidget
        rows = [{"Date": "2026.09.19", "Close": 1849000, "Foreigner": -12345678, "Institution": 9876543, "Retail": 2468135}]
        d = StockMaDialog("005930", "Samsung", "KOSPI", _df(), investor_data=rows)
        table = d.findChild(QTableWidget)
        fm = QFontMetrics(table.font())
        # 8px padding a side (theme) + 3px style text margin a side, plus slack.
        needed = [fm.horizontalAdvance(table.item(0, c).text()) + 28 for c in range(table.columnCount())]
        for c, need in enumerate(needed):
            self.assertGreaterEqual(table.columnWidth(c), need, table.item(0, c).text())
        # The splitter cannot shrink the table below what every column needs.
        # (Column widths themselves can exceed this before the dialog is
        # shown: the last section stretches into the widget's default size.)
        self.assertGreaterEqual(table.minimumWidth(), sum(needed))


class TestSimpleAndEmptyCharts(unittest.TestCase):

    def test_bond_yield_is_a_single_panel_without_views(self):
        d = StockMaDialog("KR3YT", "KR 3Y", "Index", _df(volume=False), change_mode="bp")
        self.assertEqual(list(d._panel_axes), ["price"])
        self.assertEqual(d._view_buttons, {})
        self.assertEqual(list(d._lines), ["Close"])
        self.assertTrue(d._chg_lbl.text().endswith("bp"))
        d._zoom_y_in()
        d.render(QPixmap(800, 500))

    def test_missing_data_renders_a_notice(self):
        d = StockMaDialog("X", "X", "KOSPI", None)
        self.assertFalse(d._has_data)
        self.assertIsNone(d.scrollbar)
        d.render(QPixmap(800, 500))


if __name__ == "__main__":
    unittest.main()
