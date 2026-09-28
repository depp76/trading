"""tests/test_trend_following_tab.py — TrendFollowingTab input parsing and result
rendering, exercised headlessly (no network: canned engine results)."""
import unittest
from datetime import date, timedelta
from unittest.mock import patch

import numpy as np
import polars as pl
from PyQt6.QtCore import QDate
from PyQt6.QtWidgets import QApplication, QDateEdit

app = QApplication.instance() or QApplication([])

from ui.trend_following_tab import TrendFollowingTab, _METRICS, _INSTRUMENT_COLS  # noqa: E402
from ui.dialogs.trend_following_chart import TrendFollowingChartDialog  # noqa: E402
from strategy.trend_following import run_backtest, run_portfolio_backtest, TrendFollowingConfig  # noqa: E402


class _FakeUniverse:
    all_data = [
        {"ticker": "005930", "name": "Samsung", "market_cap": 500},
        {"ticker": "000660", "name": "Hynix", "market_cap": 200},
        {"ticker": "035420", "name": "Naver", "market_cap": 50},
        {"ticker": "^KS11", "name": "KOSPI", "is_index": True},
        {"ticker": "", "name": "no ticker"},
    ]


def _single_result():
    closes = [10, 10, 10, 20, 22, 24, 5, 5, 5, 30, 33]
    closes = [float(c) for c in closes]
    dates = [date(2025, 1, 1) + timedelta(days=i) for i in range(len(closes))]
    df = pl.DataFrame({"Date": dates, "Open": closes, "High": [c + 1 for c in closes],
                       "Low": [c - 1 for c in closes], "Close": closes, "Volume": [1.0] * len(closes)})
    return run_backtest(df, TrendFollowingConfig(entry_n=3, exit_n=2))


def _histories(tickers=("A", "B", "C"), n=365 * 3 + 1):
    def walk(n, seed):
        r = np.random.default_rng(seed)
        c = [100.0]
        for _ in range(n - 1):
            c.append(max(1.0, c[-1] * (1 + r.normal(0.0008, 0.02))))
        return c
    hist = {}
    for i, t in enumerate(tickers):
        closes = walk(n, i)
        dates = [date(2021, 1, 1) + timedelta(days=k) for k in range(n)]
        hist[t] = pl.DataFrame({"Date": dates, "Open": closes, "High": [c + 1 for c in closes],
                                "Low": [c - 1 for c in closes], "Close": closes, "Volume": [1.0] * n})
    return hist


def _portfolio_result():
    hist = _histories()
    res = run_portfolio_backtest(hist, TrendFollowingConfig(entry_n=10, exit_n=5))
    res["tickers"] = list(hist)
    return res


class TestTrendFollowingTab(unittest.TestCase):

    def setUp(self):
        self.tab = TrendFollowingTab(_FakeUniverse())

    def test_defaults_match_config(self):
        cfg = self.tab._config_from_inputs()
        self.assertEqual((cfg.entry_n, cfg.exit_n), (20, 10))
        self.assertEqual(cfg.cost_per_side, 0.0)

    def test_single_ticker_controls_removed(self):
        # user direction 2026-09-28: no Ticker box / From Universe combo / Chart button / subtitle
        for name in ("_ticker_edit", "_universe_combo", "_chart_btn", "_portfolio_btn", "_trades_tbl"):
            self.assertFalse(hasattr(self.tab, name), name)

    def test_start_is_calendar_picker_left_of_run_button(self):
        self.assertIsInstance(self.tab._start_edit, QDateEdit)
        self.assertTrue(self.tab._start_edit.calendarPopup())
        self.assertEqual(self.tab._start_edit.displayFormat(), "yyyy-MM-dd")
        self.assertEqual(self.tab._start_edit.date(), QDate(date.today() - timedelta(days=5 * 365)))
        self.assertEqual(self.tab._start_edit.maximumDate(), QDate.currentDate())
        # same row, Start immediately before Run Backtest
        root = self.tab.layout()
        rows = [root.itemAt(i).layout() for i in range(root.count()) if root.itemAt(i).layout() is not None]
        row = next(r for r in rows if r.indexOf(self.tab._run_btn) >= 0)
        widgets = [row.itemAt(i).widget() for i in range(row.count()) if row.itemAt(i).widget() is not None]
        i_start = widgets.index(self.tab._start_edit)
        self.assertIs(widgets[i_start + 1], self.tab._run_btn)

    def test_no_v2_v3_prefixes_or_subtitle(self):
        from PyQt6.QtWidgets import QLabel, QPushButton
        texts = [w.text() for w in self.tab.findChildren(QLabel)] + [b.text() for b in self.tab.findChildren(QPushButton)]
        for t in texts:
            self.assertNotIn("v2", t, t)
            self.assertNotIn("v3", t, t)
            self.assertFalse(t.startswith("Buy "), t)
        self.assertIn("Regime MA:", texts)
        self.assertIn("Portfolio tickers:", texts)

    def test_inputs_parsed(self):
        self.tab._portfolio_edit.setText(" aapl, 005930;msft,, aapl ")
        self.tab._start_edit.setDate(QDate(2024, 1, 5))
        self.tab._entry_spin.setValue(55)
        self.tab._exit_spin.setValue(20)
        self.tab._fee_spin.setValue(0.1)      # percent
        tickers, start, cfg = self.tab._read_inputs()
        self.assertEqual(tickers, ["AAPL", "005930", "MSFT"])
        self.assertEqual(start, "2024-01-05")
        self.assertEqual((cfg.entry_n, cfg.exit_n), (55, 20))
        self.assertAlmostEqual(cfg.fee_rate, 0.001)

    def test_empty_list_filled_from_universe_top_n(self):
        self.tab._topn_spin.setValue(2)
        tickers, _start, _cfg = self.tab._read_inputs()
        self.assertEqual(tickers, ["005930", "000660"])            # by market cap, index skipped
        self.assertEqual(self.tab._portfolio_edit.text(), "005930, 000660")

    def test_fewer_than_two_tickers_rejected(self):
        self.tab._universe_tab = type("U", (), {"all_data": []})()
        self.tab._portfolio_edit.setText("AAPL")
        self.assertIsNone(self.tab._read_inputs())
        self.tab._portfolio_edit.setText("")
        self.assertIsNone(self.tab._read_inputs())
        self.assertIn("at least two", self.tab._status_lbl.text())

    def test_use_universe_button_fills_list(self):
        self.tab._topn_spin.setValue(3)
        self.tab._on_use_universe()
        self.assertEqual(self.tab._portfolio_edit.text(), "005930, 000660, 035420")

    def test_render_fills_summary_and_instruments(self):
        res = _portfolio_result()
        self.tab._render(res)
        s = res["summary"]
        self.assertEqual(self.tab._summary_tbl.item(0, len(_METRICS)).text(), "PASS" if s["passes_risk_gate"] else "FAIL")
        col = [k for k, _, _ in _METRICS].index("n_instruments")
        self.assertEqual(self.tab._summary_tbl.item(0, col).text(), "3")
        self.assertEqual(self.tab._instruments_tbl.rowCount(), 3)
        self.assertEqual(self.tab._instruments_tbl.item(0, 0).text(), "A")
        tcol = [k for k, _, _ in _INSTRUMENT_COLS].index("n_trades")
        self.assertEqual(self.tab._instruments_tbl.item(0, tcol).text(), str(s["instruments"][0]["n_trades"]))

    def test_finished_handler_renders_then_opens_dialog(self):
        from ui.dialogs import TrendFollowingPortfolioDialog, TrendFollowingValidationDialog
        from strategy.trend_following import walk_forward_validation, holdout_validation, yearly_folds
        pres = _portfolio_result()
        self.tab._run_btn.setEnabled(False)
        with patch.object(TrendFollowingPortfolioDialog, "exec", return_value=0) as ex:
            self.tab._on_portfolio_finished(pres, "")
        ex.assert_called_once()
        self.assertTrue(self.tab._run_btn.isEnabled())
        self.assertIn("Portfolio (3 instruments)", self.tab._status_lbl.text())
        self.assertEqual(self.tab._instruments_tbl.rowCount(), 3)

        hist = _histories()
        cfg = TrendFollowingConfig(entry_n=10, exit_n=5)
        grid = [{"entry_n": 10, "exit_n": 5}, {"entry_n": 30, "exit_n": 15}]
        vres = {"walkforward": walk_forward_validation(hist, yearly_folds(2023, 2023), grid=grid, base=cfg),
                "holdout": holdout_validation(hist, split_date=date(2023, 1, 1), grid=grid, base=cfg),
                "n_instruments": 3, "tickers": list(hist)}
        self.assertEqual(vres["walkforward"]["n_folds"], 1)
        with patch.object(TrendFollowingValidationDialog, "exec", return_value=0):
            self.tab._on_portfolio_finished(vres, "")
        self.assertIn("Walk-forward OOS", self.tab._status_lbl.text())

    def test_finished_handler_error_path(self):
        from PyQt6.QtWidgets import QMessageBox
        with patch.object(QMessageBox, "warning", return_value=None):
            self.tab._on_portfolio_finished(None, "boom")
        self.assertIn("failed", self.tab._status_lbl.text())
        self.assertTrue(self.tab._run_btn.isEnabled())
        self.assertTrue(self.tab._validate_btn.isEnabled())

    def test_chart_dialog_still_builds(self):
        # the single-ticker chart dialog module is kept (no UI caller since 2026-09-28)
        dlg = TrendFollowingChartDialog(_single_result(), "TEST")
        self.assertIn("TEST", dlg.windowTitle())

    def test_portfolio_dialogs_build_from_engine_results(self):
        from ui.dialogs import TrendFollowingPortfolioDialog
        pres = _portfolio_result()
        dlg = TrendFollowingPortfolioDialog(pres)
        self.assertIn("3 instruments", dlg.windowTitle())

    def test_portfolio_thread_attributes(self):
        from threads.fetch_threads import TrendFollowingPortfolioThread
        th = TrendFollowingPortfolioThread(["A", "B"], "2020-01-01", TrendFollowingConfig(), mode="validate",
                                           oos_first_year=2022, oos_last_year=2026)
        self.assertTrue(callable(th.start))
        self.assertEqual((th.start_date, th.mode, th.oos_first_year), ("2020-01-01", "validate", 2022))

    def test_backtest_thread_keeps_qthread_start(self):
        # a `self.start = ...` attribute would shadow QThread.start()
        from threads.fetch_threads import TrendFollowingBacktestThread
        th = TrendFollowingBacktestThread("005930", "2024-01-01", TrendFollowingConfig())
        self.assertTrue(callable(th.start))
        self.assertEqual(th.start_date, "2024-01-01")


if __name__ == "__main__":
    unittest.main()
