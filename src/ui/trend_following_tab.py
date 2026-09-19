"""ui/trend_following_tab.py — TrendFollowingTab: run the Donchian channel breakout
backtest (strategy/trend_following, spec trend_following.md) on one ticker from the UI.

Signal generation / research only — nothing here places orders. Reads
UniverseTab.all_data on demand to offer the watchlist tickers in a combo (same
direct-reference pattern as ui/auto_trading_tab.py); the backtest itself runs in
threads.fetch_threads.TrendFollowingBacktestThread so the window never blocks on
the history fetch.
"""
import logging
from datetime import date, timedelta

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox,
    QPushButton, QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QColor

from strategy.trend_following import TrendFollowingConfig
from threads.fetch_threads import TrendFollowingBacktestThread, TrendFollowingPortfolioThread
from ui.common import create_font, _validate_date_str, _normalize_date_str, _set_field_error, ThreadOwnerMixin
from ui.dialogs.trend_following_chart import TrendFollowingChartDialog
from ui.dialogs.trend_following_portfolio import TrendFollowingPortfolioDialog, TrendFollowingValidationDialog

logger = logging.getLogger(__name__)

_METRICS = [
    ("total_return_pct", "Total return", "{:+.1f}%"),
    ("cagr_pct", "CAGR", "{:+.1f}%"),
    ("annual_vol_pct", "Annual vol", "{:.1f}%"),
    ("sharpe", "Sharpe", "{:.2f}"),
    ("max_drawdown_pct", "Max drawdown", "{:.1f}%"),
    ("n_trades", "Trades", "{}"),
    ("win_rate_pct", "Win rate", "{:.0f}%"),
    ("avg_trade_return_pct", "Avg trade", "{:+.2f}%"),
    ("exposure_pct", "Exposure", "{:.0f}%"),
]


class TrendFollowingTab(ThreadOwnerMixin, QWidget):

    def __init__(self, universe_tab=None, parent=None):
        super().__init__(parent)
        self._universe_tab = universe_tab
        self._backtest_thread = None
        self._portfolio_thread = None
        self._last_result = None
        self._last_ticker = ""
        self._build_ui()

    # ── UI ───────────────────────────────────────────────────────────────────
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(8)

        title = QLabel("Trend Following — Donchian Channel Breakout Backtest")
        title.setFont(create_font(16, QFont.Weight.Bold))
        root.addWidget(title)

        subtitle = QLabel(
            "Buy when the close breaks above the prior entry_n-day high, sell when it breaks below the "
            "prior exit_n-day low; long only, single position, applied from the next day (trend_following.md 3)."
        )
        subtitle.setFont(create_font(9, style_name="Semilight"))
        subtitle.setStyleSheet("color:#7f8c8d;")
        subtitle.setWordWrap(True)
        root.addWidget(subtitle)

        # Row 1: ticker / universe picker / start date
        row1 = QHBoxLayout()
        row1.addWidget(self._lbl("Ticker:"))
        self._ticker_edit = QLineEdit()
        self._ticker_edit.setFont(create_font(10, style_name="Semilight"))
        self._ticker_edit.setPlaceholderText("e.g. 005930, AAPL, ^GSPC")
        self._ticker_edit.setFixedWidth(150)
        self._ticker_edit.returnPressed.connect(self._on_run_clicked)
        row1.addWidget(self._ticker_edit)

        row1.addWidget(self._lbl("From Universe:"))
        self._universe_combo = QComboBox()
        self._universe_combo.setFont(create_font(10, style_name="Semilight"))
        self._universe_combo.setMinimumWidth(260)
        self._universe_combo.currentIndexChanged.connect(self._on_universe_pick)
        row1.addWidget(self._universe_combo)

        row1.addWidget(self._lbl("Start:"))
        self._start_edit = QLineEdit((date.today() - timedelta(days=5 * 365)).strftime("%Y-%m-%d"))
        self._start_edit.setFont(create_font(10, style_name="Semilight"))
        self._start_edit.setFixedWidth(110)
        row1.addWidget(self._start_edit)
        row1.addStretch()
        root.addLayout(row1)

        # Row 2: parameters + run
        row2 = QHBoxLayout()
        row2.addWidget(self._lbl("entry_n:"))
        self._entry_spin = QSpinBox()
        self._entry_spin.setRange(1, 500)
        self._entry_spin.setValue(TrendFollowingConfig().entry_n)
        row2.addWidget(self._entry_spin)

        row2.addWidget(self._lbl("exit_n:"))
        self._exit_spin = QSpinBox()
        self._exit_spin.setRange(1, 500)
        self._exit_spin.setValue(TrendFollowingConfig().exit_n)
        row2.addWidget(self._exit_spin)

        row2.addWidget(self._lbl("Fee/side:"))
        self._fee_spin = self._pct_spin()
        row2.addWidget(self._fee_spin)

        row2.addWidget(self._lbl("Slippage/side:"))
        self._slip_spin = self._pct_spin()
        row2.addWidget(self._slip_spin)

        self._run_btn = QPushButton("▶ Run Backtest")
        self._run_btn.setFont(create_font(10, QFont.Weight.Bold))
        self._run_btn.setFixedHeight(32)
        self._run_btn.setStyleSheet(
            "QPushButton { background:#8e44ad; color:white; border-radius:4px; padding:4px 14px; font-weight:bold; }"
            "QPushButton:hover { background:#732d91; }"
            "QPushButton:disabled { background:#bbb; }"
        )
        self._run_btn.clicked.connect(self._on_run_clicked)
        row2.addWidget(self._run_btn)

        self._chart_btn = QPushButton("\U0001f4c8 Chart")
        self._chart_btn.setFont(create_font(10, QFont.Weight.Bold))
        self._chart_btn.setFixedHeight(32)
        self._chart_btn.setEnabled(False)
        self._chart_btn.clicked.connect(self._on_chart_clicked)
        row2.addWidget(self._chart_btn)

        self._status_lbl = QLabel("")
        self._status_lbl.setFont(create_font(9, style_name="Semilight"))
        self._status_lbl.setStyleSheet("color:#7f8c8d;")
        row2.addWidget(self._status_lbl)
        row2.addStretch()
        root.addLayout(row2)

        # Row 3: v2 overlays (trend_following.md 3 "v2"); 0 = off, so defaults reproduce v1
        row3 = QHBoxLayout()
        row3.addWidget(self._lbl("v2 \u2014 Regime MA:"))
        self._regime_spin = QSpinBox()
        self._regime_spin.setRange(0, 500)
        self._regime_spin.setSpecialValueText("off")
        self._regime_spin.setValue(0)
        self._regime_spin.setToolTip("Enter only while Close is above this simple moving average (0 = off)")
        row3.addWidget(self._regime_spin)

        row3.addWidget(self._lbl("Stop ATR\u00d7:"))
        self._stop_spin = QDoubleSpinBox()
        self._stop_spin.setRange(0.0, 10.0)
        self._stop_spin.setDecimals(1)
        self._stop_spin.setSingleStep(0.5)
        self._stop_spin.setSpecialValueText("off")
        self._stop_spin.setValue(0.0)
        self._stop_spin.setToolTip("Exit when Close falls this many ATRs below the anchor (0 = off)")
        row3.addWidget(self._stop_spin)

        self._stop_mode_combo = QComboBox()
        self._stop_mode_combo.addItems(["trailing", "fixed"])
        self._stop_mode_combo.setToolTip("trailing: anchor = highest close since entry; fixed: anchor = entry close")
        row3.addWidget(self._stop_mode_combo)

        row3.addWidget(self._lbl("Vol target:"))
        self._vol_spin = QDoubleSpinBox()
        self._vol_spin.setRange(0.0, 100.0)
        self._vol_spin.setDecimals(1)
        self._vol_spin.setSingleStep(1.0)
        self._vol_spin.setSuffix(" %")
        self._vol_spin.setSpecialValueText("off")
        self._vol_spin.setValue(0.0)
        self._vol_spin.setToolTip("Size each trade to this annualised volatility (0 = off, full weight)")
        row3.addWidget(self._vol_spin)

        row3.addWidget(self._lbl("Max weight:"))
        self._maxw_spin = QDoubleSpinBox()
        self._maxw_spin.setRange(0.1, 3.0)
        self._maxw_spin.setDecimals(1)
        self._maxw_spin.setSingleStep(0.1)
        self._maxw_spin.setValue(1.0)
        self._maxw_spin.setToolTip("Cap on the position weight (1.0 = no leverage)")
        row3.addWidget(self._maxw_spin)
        row3.addStretch()
        root.addLayout(row3)

        # Row 4: v3 portfolio + IS/OOS validation (trend_following.md 3 "v3", 5)
        row4 = QHBoxLayout()
        row4.addWidget(self._lbl("v3 \u2014 Portfolio tickers:"))
        self._portfolio_edit = QLineEdit()
        self._portfolio_edit.setFont(create_font(10, style_name="Semilight"))
        self._portfolio_edit.setPlaceholderText("comma-separated, e.g. 005930, 000660, AAPL")
        self._portfolio_edit.setMinimumWidth(320)
        row4.addWidget(self._portfolio_edit, 1)

        row4.addWidget(self._lbl("Top N:"))
        self._topn_spin = QSpinBox()
        self._topn_spin.setRange(2, 300)
        self._topn_spin.setValue(20)
        self._topn_spin.setToolTip("How many Trading Universe stocks (by market cap) to load with the button")
        row4.addWidget(self._topn_spin)

        self._use_universe_btn = QPushButton("Use Universe")
        self._use_universe_btn.setToolTip("Fill the ticker list with the top-N Trading Universe stocks by market cap")
        self._use_universe_btn.clicked.connect(self._on_use_universe)
        row4.addWidget(self._use_universe_btn)

        self._portfolio_btn = QPushButton("\u25b6 Run Portfolio")
        self._portfolio_btn.setFont(create_font(10, QFont.Weight.Bold))
        self._portfolio_btn.setFixedHeight(32)
        self._portfolio_btn.setStyleSheet(
            "QPushButton { background:#1a5276; color:white; border-radius:4px; padding:4px 14px; font-weight:bold; }"
            "QPushButton:hover { background:#21618c; }"
            "QPushButton:disabled { background:#bbb; }"
        )
        self._portfolio_btn.setToolTip("Equal-sleeve portfolio backtest with the parameters above")
        self._portfolio_btn.clicked.connect(lambda: self._on_portfolio_clicked("portfolio"))
        row4.addWidget(self._portfolio_btn)

        row4.addWidget(self._lbl("OOS years:"))
        self._oos_years_spin = QSpinBox()
        self._oos_years_spin.setRange(1, 15)
        self._oos_years_spin.setValue(5)
        self._oos_years_spin.setToolTip("Number of most recent calendar years used as yearly out-of-sample folds; "
                                        "the first of them is also the holdout split")
        row4.addWidget(self._oos_years_spin)

        self._validate_btn = QPushButton("\u2696 Validate (IS/OOS)")
        self._validate_btn.setFont(create_font(10, QFont.Weight.Bold))
        self._validate_btn.setFixedHeight(32)
        self._validate_btn.setStyleSheet(
            "QPushButton { background:#6c3483; color:white; border-radius:4px; padding:4px 14px; font-weight:bold; }"
            "QPushButton:hover { background:#9b59b6; }"
            "QPushButton:disabled { background:#bbb; }"
        )
        self._validate_btn.setToolTip("Holdout + anchored yearly walk-forward over the 12-config default grid "
                                      "(entry/exit x vol target x ATR stop); the parameters above are the base config. "
                                      "Use a Start date well before the OOS years.")
        self._validate_btn.clicked.connect(lambda: self._on_portfolio_clicked("validate"))
        row4.addWidget(self._validate_btn)
        root.addLayout(row4)

        # Summary metrics (one row, one column per metric)
        self._summary_tbl = QTableWidget(1, len(_METRICS) + 1)
        self._summary_tbl.setHorizontalHeaderLabels([label for _, label, _ in _METRICS] + ["Risk gate"])
        self._summary_tbl.setFont(create_font(10, QFont.Weight.Bold))
        self._summary_tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._summary_tbl.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self._summary_tbl.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._summary_tbl.verticalHeader().setVisible(False)
        self._summary_tbl.setFixedHeight(64)
        root.addWidget(self._summary_tbl)

        # Trades
        trades_lbl = QLabel("Trades")
        trades_lbl.setFont(create_font(11, QFont.Weight.Bold))
        root.addWidget(trades_lbl)
        self._trades_tbl = QTableWidget(0, 8)
        self._trades_tbl.setHorizontalHeaderLabels(
            ["#", "Entry", "Exit", "Reason", "Days", "Weight", "Price %", "Return %"])
        self._trades_tbl.setFont(create_font(9, style_name="Semilight"))
        self._trades_tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._trades_tbl.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._trades_tbl.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._trades_tbl.verticalHeader().setVisible(False)
        root.addWidget(self._trades_tbl, 1)

        disclaimer = QLabel(
            "⚠️ Research/backtesting tool, not investment advice. Single-period in-sample result; "
            "see trend_following.md section 5 for the preliminary real-data sweep and its caveats."
        )
        disclaimer.setFont(create_font(8, style_name="Semilight"))
        disclaimer.setStyleSheet("color:#888;")
        disclaimer.setWordWrap(True)
        root.addWidget(disclaimer)

    @staticmethod
    def _lbl(text):
        lbl = QLabel(text)
        lbl.setFont(create_font(10, style_name="Semilight"))
        return lbl

    @staticmethod
    def _pct_spin():
        sp = QDoubleSpinBox()
        sp.setRange(0.0, 5.0)
        sp.setDecimals(3)
        sp.setSingleStep(0.01)
        sp.setSuffix(" %")
        sp.setValue(0.0)
        return sp

    # ── universe picker ──────────────────────────────────────────────────────
    def showEvent(self, event):
        super().showEvent(event)
        self._refresh_universe_combo()

    def _refresh_universe_combo(self):
        data = getattr(self._universe_tab, "all_data", None) or []
        items = [(it.get("ticker", ""), it.get("name", "")) for it in data if it.get("ticker")]
        current = self._universe_combo.currentData()
        self._universe_combo.blockSignals(True)
        try:
            self._universe_combo.clear()
            self._universe_combo.addItem("(pick from Trading Universe)", userData="")
            for ticker, name in items:
                self._universe_combo.addItem(f"{ticker}  {name}", userData=ticker)
            if current:
                idx = self._universe_combo.findData(current)
                if idx >= 0:
                    self._universe_combo.setCurrentIndex(idx)
        finally:
            self._universe_combo.blockSignals(False)

    def _on_universe_pick(self, _index):
        ticker = self._universe_combo.currentData()
        if ticker:
            self._ticker_edit.setText(str(ticker))

    def _universe_top_tickers(self, n: int) -> list:
        data = getattr(self._universe_tab, "all_data", None) or []
        stocks = [it for it in data if it.get("ticker") and not it.get("is_index")]
        stocks.sort(key=lambda it: -float(it.get("market_cap", 0) or 0))
        return [it["ticker"] for it in stocks[:n]]

    def _on_use_universe(self):
        tickers = self._universe_top_tickers(int(self._topn_spin.value()))
        if not tickers:
            QMessageBox.information(self, "No Data", "Trading Universe has no stocks yet \u2014 refresh it first.")
            return
        self._portfolio_edit.setText(", ".join(tickers))

    def _portfolio_tickers(self) -> list:
        seen, out = set(), []
        for raw in self._portfolio_edit.text().replace(";", ",").split(","):
            t = raw.strip().upper()
            if t and t not in seen:
                seen.add(t)
                out.append(t)
        return out

    # ── inputs ───────────────────────────────────────────────────────────────
    def _config_from_inputs(self) -> TrendFollowingConfig:
        return TrendFollowingConfig(
            entry_n=int(self._entry_spin.value()),
            exit_n=int(self._exit_spin.value()),
            fee_rate=float(self._fee_spin.value()) / 100.0,
            slippage_rate=float(self._slip_spin.value()) / 100.0,
            regime_ma_n=int(self._regime_spin.value()),
            stop_atr_mult=float(self._stop_spin.value()),
            stop_mode=self._stop_mode_combo.currentText(),
            vol_target_pct=float(self._vol_spin.value()),
            max_weight=float(self._maxw_spin.value()),
        )

    def _read_inputs(self):
        """Returns (ticker, start, config) or None after flagging the bad field."""
        ticker = self._ticker_edit.text().strip().upper()
        _set_field_error(self._ticker_edit, "" if ticker else "Ticker is required")
        start_ok = _validate_date_str(self._start_edit.text())
        _set_field_error(self._start_edit, "" if start_ok else "Start date must be YYYY-MM-DD")
        if not ticker or not start_ok:
            return None
        return ticker, _normalize_date_str(self._start_edit.text()), self._config_from_inputs()

    # ── run ──────────────────────────────────────────────────────────────────
    def _on_run_clicked(self):
        if self._backtest_thread is not None and self._backtest_thread.isRunning():
            return
        inputs = self._read_inputs()
        if inputs is None:
            return
        ticker, start, config = inputs
        self._last_ticker = ticker
        self._run_btn.setEnabled(False)
        self._chart_btn.setEnabled(False)
        self._status_lbl.setText(f"Fetching {ticker} history from {start}...")
        self._track_thread(TrendFollowingBacktestThread(ticker, start, config), '_backtest_thread')
        self._backtest_thread.finished.connect(self._on_backtest_finished)
        self._backtest_thread.start()

    def _on_backtest_finished(self, result, error: str):
        self._run_btn.setEnabled(True)
        if error or result is None:
            self._status_lbl.setText("Backtest failed — see app.log")
            QMessageBox.warning(self, "Backtest Error", f"Backtest failed:\n{error or 'unknown error'}")
            return
        if result.get("error"):
            self._status_lbl.setText(f"{self._last_ticker}: {result['error']}")
            QMessageBox.information(self, "No Data", f"No history for '{self._last_ticker}'.")
            return
        self._last_result = result
        self._render(result)
        self._chart_btn.setEnabled(True)
        s = result["summary"]
        v2 = s.get("v2") or {}
        overlays = []
        if v2.get("regime_ma_n"):
            overlays.append(f"regime MA{v2['regime_ma_n']}")
        if v2.get("stop_atr_mult"):
            overlays.append(f"{v2['stop_mode']} stop {v2['stop_atr_mult']:g}×ATR{v2['atr_n']}")
        if v2.get("vol_target_pct"):
            overlays.append(f"vol target {v2['vol_target_pct']:g}% (max {v2['max_weight']:g})")
        exits = (f" | exits: {s.get('n_channel_exits', 0)} channel / {s.get('n_stop_exits', 0)} stop"
                 if v2.get("stop_atr_mult") else "")
        self._status_lbl.setText(
            f"{self._last_ticker}: {s['start_date']} → {s['end_date']} ({s['n_days']} days), "
            f"Donchian {s['entry_n']}/{s['exit_n']}" + (" + " + ", ".join(overlays) if overlays else " (v1)") + exits
        )

    # ── v3 portfolio / validation ────────────────────────────────────────────
    def _on_portfolio_clicked(self, mode: str):
        if self._portfolio_thread is not None and self._portfolio_thread.isRunning():
            return
        tickers = self._portfolio_tickers()
        _set_field_error(self._portfolio_edit, "" if len(tickers) >= 2 else "Enter at least two tickers")
        start_ok = _validate_date_str(self._start_edit.text())
        _set_field_error(self._start_edit, "" if start_ok else "Start date must be YYYY-MM-DD")
        if len(tickers) < 2 or not start_ok:
            return
        start = _normalize_date_str(self._start_edit.text())
        config = self._config_from_inputs()
        oos_last = date.today().year
        oos_first = oos_last - int(self._oos_years_spin.value()) + 1
        self._portfolio_btn.setEnabled(False)
        self._validate_btn.setEnabled(False)
        self._status_lbl.setText(f"{'Validating' if mode == 'validate' else 'Portfolio backtest'}: {len(tickers)} tickers from {start}...")
        self._track_thread(TrendFollowingPortfolioThread(tickers, start, config, mode=mode,
                                                         oos_first_year=oos_first, oos_last_year=oos_last),
                           '_portfolio_thread')
        self._portfolio_thread.progress.connect(self._status_lbl.setText)
        self._portfolio_thread.finished.connect(self._on_portfolio_finished)
        self._portfolio_thread.start()

    def _on_portfolio_finished(self, result, error: str):
        self._portfolio_btn.setEnabled(True)
        self._validate_btn.setEnabled(True)
        if error or result is None:
            self._status_lbl.setText("Portfolio run failed \u2014 see app.log")
            QMessageBox.warning(self, "Portfolio Error", f"Run failed:\n{error or 'unknown error'}")
            return
        if "walkforward" in result:
            wf = result["walkforward"]
            if not wf.get("n_folds"):
                self._status_lbl.setText("Validation produced no folds \u2014 use an earlier Start date")
                QMessageBox.information(self, "Validation", "No out-of-sample fold had enough in-sample history. "
                                                            "Set an earlier Start date or fewer OOS years.")
                return
            o = wf["oos"]
            self._status_lbl.setText(
                f"Walk-forward OOS ({wf['n_folds']} folds): Sharpe {o['sharpe']:.2f}, MDD {o['max_drawdown_pct']:.1f}%, "
                f"CAGR {o['cagr_pct']:+.1f}% \u2014 gate {'PASS' if o['passes_risk_gate'] else 'FAIL'}"
            )
            TrendFollowingValidationDialog(result, parent=self).exec()
            return
        s = result["summary"]
        if not s.get("n_instruments"):
            self._status_lbl.setText("Portfolio: no usable history")
            QMessageBox.information(self, "No Data", "None of the tickers returned enough history.")
            return
        self._status_lbl.setText(
            f"Portfolio ({s['n_instruments']} instruments): Sharpe {s['sharpe']:.2f}, MDD {s['max_drawdown_pct']:.1f}%, "
            f"CAGR {s['cagr_pct']:+.1f}%, exposure {s['avg_gross_exposure_pct']:.0f}% \u2014 gate {'PASS' if s['passes_risk_gate'] else 'FAIL'}"
        )
        TrendFollowingPortfolioDialog(result, parent=self).exec()

    def _on_chart_clicked(self):
        if not self._last_result:
            return
        dlg = TrendFollowingChartDialog(self._last_result, self._last_ticker, parent=self)
        dlg.exec()

    # ── render ───────────────────────────────────────────────────────────────
    def _render(self, result: dict):
        s = result["summary"]
        tbl = self._summary_tbl
        tbl.setUpdatesEnabled(False)
        try:
            for c, (key, _label, fmt) in enumerate(_METRICS):
                val = s.get(key, 0)
                it = QTableWidgetItem(fmt.format(val))
                it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if key in ("total_return_pct", "cagr_pct", "avg_trade_return_pct"):
                    it.setForeground(QColor("#c0392b" if val > 0 else "#2980b9" if val < 0 else "#555"))
                tbl.setItem(0, c, it)
            gate = bool(s.get("passes_risk_gate"))
            gate_it = QTableWidgetItem("PASS" if gate else "FAIL")
            gate_it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            gate_it.setForeground(QColor("#107c10" if gate else "#c0392b"))
            tbl.setItem(0, len(_METRICS), gate_it)
        finally:
            tbl.setUpdatesEnabled(True)

        trades = result.get("trades") or []
        tt = self._trades_tbl
        tt.setUpdatesEnabled(False)
        try:
            tt.setRowCount(len(trades))
            right = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            for r, t in enumerate(trades):
                ret = t.get("return_pct", 0.0)
                px = t.get("price_return_pct", 0.0)
                reason = t.get("exit_reason") or ("open" if not t.get("exit_date") else "")
                cells = [
                    (str(r + 1), Qt.AlignmentFlag.AlignCenter),
                    (t.get("entry_date", ""), Qt.AlignmentFlag.AlignCenter),
                    (t.get("exit_date") or "open", Qt.AlignmentFlag.AlignCenter),
                    (reason, Qt.AlignmentFlag.AlignCenter),
                    (str(t.get("days_held", 0)), right),
                    (f"{t.get('weight', 1.0):.2f}", right),
                    (f"{px:+.2f}%", right),
                    (f"{ret:+.2f}%", right),
                ]
                for c, (text, align) in enumerate(cells):
                    it = QTableWidgetItem(text)
                    it.setTextAlignment(align)
                    if c in (6, 7):
                        v = px if c == 6 else ret
                        it.setForeground(QColor("#c0392b" if v > 0 else "#2980b9" if v < 0 else "#555"))
                    if c == 3 and reason == "stop":
                        it.setForeground(QColor("#d35400"))
                    tt.setItem(r, c, it)
        finally:
            tt.setUpdatesEnabled(True)
