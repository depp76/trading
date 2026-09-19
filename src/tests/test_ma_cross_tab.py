"""tests/test_ma_cross_tab.py — MaCrossTab input handling and result rendering,
headless with a canned run_backtest_for_stock() result (no network)."""
import unittest
from datetime import date, timedelta
from unittest.mock import patch, MagicMock

import numpy as np
import polars as pl
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QPixmap

app = QApplication.instance() or QApplication([])

from data.indicators import _compute_indicators  # noqa: E402
from strategy.ma_cross import run_backtest_for_stock, MaCrossConfig  # noqa: E402
from ui.ma_cross_tab import MaCrossTab  # noqa: E402


def _result():
    n = 400
    rng = np.random.default_rng(5)
    close = 100.0 * np.cumprod(1 + rng.normal(0.001, 0.02, n))
    dates = [date(2025, 1, 1) + timedelta(days=i) for i in range(n)]
    df = pl.DataFrame({"Date": dates, "Open": close, "High": close, "Low": close, "Close": close,
                       "Volume": np.ones(n)}).with_columns(pl.col("Date").cast(pl.Datetime))
    df = _compute_indicators(df, windows=(10, 20, 60))
    return run_backtest_for_stock("TEST", "KOSPI", df=df, config=MaCrossConfig(entry_mult=1.0))


class FakeUniverse:
    all_data = [{"ticker": "005930", "name": "Samsung", "market": "KOSPI"},
                {"ticker": "KS11", "name": "KOSPI", "market": "Index", "is_index": True}]


class TestMaCrossTab(unittest.TestCase):

    def test_inputs_build_a_config_and_resolve_the_market(self):
        tab = MaCrossTab(FakeUniverse())
        tab._refresh_universe_combo()
        self.assertEqual(tab._universe_combo.count(), 2)          # placeholder + the one stock (index skipped)
        tab._universe_combo.setCurrentIndex(1)
        self.assertEqual(tab._ticker_edit.text(), "005930")
        ticker, market, cfg, year = tab._read_inputs()
        self.assertEqual((ticker, market, year), ("005930", "KOSPI", None))
        self.assertEqual((cfg.fast_n, cfg.slow_n), (20, 60))

        tab._ticker_edit.setText("aapl")
        _t, market, _c, _y = tab._read_inputs()
        self.assertEqual(market, "")
        tab._fast_spin.setValue(80)                                # fast >= slow -> config error
        with patch("ui.ma_cross_tab.QMessageBox.warning") as warn:
            self.assertIsNone(tab._read_inputs())
        warn.assert_called_once()

    def test_run_starts_the_thread_and_result_renders(self):
        tab = MaCrossTab(FakeUniverse())
        tab._ticker_edit.setText("005930")
        with patch("ui.ma_cross_tab.MaCrossBacktestThread") as thread_cls:
            thread_cls.return_value = MagicMock()
            tab._on_run_clicked()
            thread_cls.assert_called_once()
            self.assertFalse(tab._run_btn.isEnabled())
        tab._backtest_thread = None

        res = _result()
        tab._on_backtest_finished(res, "")
        self.assertTrue(tab._run_btn.isEnabled())
        self.assertEqual(tab._kpi["n_trades"].text(), str(res["summary"]["n_trades"]))
        self.assertEqual(tab._trades_tbl.rowCount(), len(res["trades"]))
        self.assertEqual(len(tab._fig.axes), 1)
        tab.resize(1200, 800)
        tab.render(QPixmap(1200, 800))

    def test_failures_keep_the_tab_usable(self):
        tab = MaCrossTab(FakeUniverse())
        with patch("ui.ma_cross_tab.QMessageBox.warning") as warn:
            tab._on_backtest_finished(None, "boom")
        warn.assert_called_once()
        self.assertTrue(tab._run_btn.isEnabled())
        tab._on_backtest_finished({"ticker": "X", "trades": [], "error": "No data", "summary": {}, "df": None}, "")
        self.assertIn("No data", tab._status_lbl.text())


if __name__ == "__main__":
    unittest.main()
