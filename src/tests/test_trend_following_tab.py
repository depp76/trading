"""tests/test_trend_following_tab.py — TrendFollowingTab input parsing and result
rendering, exercised headlessly (no network: a canned run_backtest() result)."""
import unittest
from datetime import date, timedelta
from unittest.mock import patch

import polars as pl
from PyQt6.QtWidgets import QApplication

app = QApplication.instance() or QApplication([])

from ui.trend_following_tab import TrendFollowingTab, _METRICS  # noqa: E402
from ui.dialogs.trend_following_chart import TrendFollowingChartDialog  # noqa: E402
from strategy.trend_following import run_backtest, TrendFollowingConfig  # noqa: E402


class _FakeUniverse:
    all_data = [
        {"ticker": "005930", "name": "Samsung"},
        {"ticker": "^KS11", "name": "KOSPI", "is_index": True},
        {"ticker": "", "name": "no ticker"},
    ]


def _result():
    closes = [10, 10, 10, 20, 22, 24, 5, 5, 5, 30, 33]
    closes = [float(c) for c in closes]
    dates = [date(2025, 1, 1) + timedelta(days=i) for i in range(len(closes))]
    df = pl.DataFrame({"Date": dates, "Open": closes, "High": [c + 1 for c in closes],
                       "Low": [c - 1 for c in closes], "Close": closes, "Volume": [1.0] * len(closes)})
    return run_backtest(df, TrendFollowingConfig(entry_n=3, exit_n=2))


class TestTrendFollowingTab(unittest.TestCase):

    def setUp(self):
        self.tab = TrendFollowingTab(_FakeUniverse())

    def test_defaults_match_config(self):
        cfg = self.tab._config_from_inputs()
        self.assertEqual((cfg.entry_n, cfg.exit_n), (20, 10))
        self.assertEqual(cfg.cost_per_side, 0.0)

    def test_inputs_parsed_and_validated(self):
        self.tab._ticker_edit.setText("  aapl ")
        self.tab._start_edit.setText("2024-1-5")
        self.tab._entry_spin.setValue(55)
        self.tab._exit_spin.setValue(20)
        self.tab._fee_spin.setValue(0.1)      # percent
        ticker, start, cfg = self.tab._read_inputs()
        self.assertEqual(ticker, "AAPL")
        self.assertEqual(start, "2024-01-05")
        self.assertEqual((cfg.entry_n, cfg.exit_n), (55, 20))
        self.assertAlmostEqual(cfg.fee_rate, 0.001)

    def test_invalid_inputs_rejected(self):
        self.tab._ticker_edit.setText("")
        self.assertIsNone(self.tab._read_inputs())
        self.tab._ticker_edit.setText("AAPL")
        self.tab._start_edit.setText("not a date")
        self.assertIsNone(self.tab._read_inputs())

    def test_universe_combo_lists_tickers(self):
        self.tab._refresh_universe_combo()
        combo = self.tab._universe_combo
        self.assertEqual(combo.count(), 3)          # placeholder + 2 tickers (blank one skipped)
        combo.setCurrentIndex(1)
        self.assertEqual(self.tab._ticker_edit.text(), "005930")

    def test_render_fills_summary_and_trades(self):
        res = _result()
        self.tab._render(res)
        self.assertEqual(self.tab._summary_tbl.item(0, len(_METRICS)).text(), "FAIL")
        self.assertEqual(self.tab._summary_tbl.item(0, 5).text(), str(res["summary"]["n_trades"]))
        self.assertEqual(self.tab._trades_tbl.rowCount(), len(res["trades"]))
        self.assertEqual(self.tab._trades_tbl.item(1, 2).text(), "open")

    def test_finished_handler_paths(self):
        self.tab._last_ticker = "X"
        self.tab._on_backtest_finished(_result(), "")
        self.assertTrue(self.tab._chart_btn.isEnabled())
        self.assertIn("Donchian", self.tab._status_lbl.text())

    def test_chart_dialog_builds(self):
        dlg = TrendFollowingChartDialog(_result(), "TEST")
        self.assertIn("TEST", dlg.windowTitle())

    def test_portfolio_ticker_parsing_and_universe_fill(self):
        self.tab._portfolio_edit.setText(" aapl, 005930;msft,, aapl ")
        self.assertEqual(self.tab._portfolio_tickers(), ["AAPL", "005930", "MSFT"])
        self.tab._universe_tab = type("U", (), {"all_data": [
            {"ticker": "A", "market_cap": 10}, {"ticker": "B", "market_cap": 30},
            {"ticker": "^KS11", "market_cap": 0, "is_index": True}, {"ticker": "C", "market_cap": 20},
        ]})()
        self.tab._topn_spin.setValue(2)
        self.tab._on_use_universe()
        self.assertEqual(self.tab._portfolio_edit.text(), "B, C")

    def test_portfolio_dialogs_build_from_engine_results(self):
        from datetime import date as _d, timedelta as _td
        from strategy.trend_following import run_portfolio_backtest, walk_forward_validation, holdout_validation, yearly_folds
        from ui.dialogs import TrendFollowingPortfolioDialog, TrendFollowingValidationDialog
        import numpy as np

        def walk(n, seed):
            r = np.random.default_rng(seed)
            c = [100.0]
            for _ in range(n - 1):
                c.append(max(1.0, c[-1] * (1 + r.normal(0.0008, 0.02))))
            return c
        n = 365 * 3 + 1
        hist = {}
        for i, t in enumerate(("A", "B", "C")):
            closes = walk(n, i)
            dates = [_d(2021, 1, 1) + _td(days=k) for k in range(n)]
            hist[t] = pl.DataFrame({"Date": dates, "Open": closes, "High": [c + 1 for c in closes],
                                    "Low": [c - 1 for c in closes], "Close": closes, "Volume": [1.0] * n})
        cfg = TrendFollowingConfig(entry_n=10, exit_n=5)
        pres = run_portfolio_backtest(hist, cfg)
        pres["tickers"] = list(hist)
        dlg = TrendFollowingPortfolioDialog(pres)
        self.assertIn("3 instruments", dlg.windowTitle())
        grid = [{"entry_n": 10, "exit_n": 5}, {"entry_n": 30, "exit_n": 15}]
        vres = {"walkforward": walk_forward_validation(hist, yearly_folds(2023, 2023), grid=grid, base=cfg),
                "holdout": holdout_validation(hist, split_date=_d(2023, 1, 1), grid=grid, base=cfg),
                "n_instruments": 3, "tickers": list(hist)}
        self.assertEqual(vres["walkforward"]["n_folds"], 1)
        vdlg = TrendFollowingValidationDialog(vres)
        self.assertIn("3 instruments", vdlg.windowTitle())
        # tab handler routes a validation result to the status label without blocking
        with patch.object(TrendFollowingValidationDialog, "exec", return_value=0):
            self.tab._on_portfolio_finished(vres, "")
        self.assertIn("Walk-forward OOS", self.tab._status_lbl.text())
        with patch.object(TrendFollowingPortfolioDialog, "exec", return_value=0):
            self.tab._on_portfolio_finished(pres, "")
        self.assertIn("Portfolio (3 instruments)", self.tab._status_lbl.text())

    def test_portfolio_thread_attributes(self):
        from threads.fetch_threads import TrendFollowingPortfolioThread
        th = TrendFollowingPortfolioThread(["A", "B"], "2020-01-01", TrendFollowingConfig(), mode="validate",
                                           oos_first_year=2022, oos_last_year=2026)
        self.assertTrue(callable(th.start))
        self.assertEqual((th.start_date, th.mode, th.oos_first_year), ("2020-01-01", "validate", 2022))

    def test_backtest_thread_keeps_qthread_start(self):
        # a `self.start = ...` attribute would shadow QThread.start() and break _on_run_clicked
        from threads.fetch_threads import TrendFollowingBacktestThread
        th = TrendFollowingBacktestThread("005930", "2024-01-01", TrendFollowingConfig())
        self.assertTrue(callable(th.start))
        self.assertEqual(th.start_date, "2024-01-01")


if __name__ == "__main__":
    unittest.main()
