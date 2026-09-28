"""ui/trend_following_tab.py — TrendFollowingTab: run the Donchian channel breakout
portfolio backtest (strategy/trend_following, spec trend_following.md) from the UI.

Signal generation / research only — nothing here places orders. Reads
UniverseTab.all_data on demand to fill the ticker list with the top-N watchlist
stocks (same direct-reference pattern as ui/auto_trading_tab.py); the backtest
itself runs in threads.fetch_threads.TrendFollowingPortfolioThread so the window
never blocks on the history fetches.

Layout (user direction, 2026-09-28): one parameter row ending in the Start date
picker and the Run Backtest button, then two collapsed rows (the v2 overlays and the
ticker list / IS-OOS validation), then the portfolio summary and the per-instrument
table. The single-ticker mode (Ticker box, From Universe combo, Chart button) was
removed the same day; Run Backtest now runs the equal-sleeve portfolio backtest on
the ticker list, filling it from the Trading Universe when it is empty.
"""
import logging
from datetime import date, timedelta

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox,
    QDateEdit, QPushButton, QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox,
)
from PyQt6.QtCore import Qt, QDate
from PyQt6.QtGui import QFont, QColor

from strategy.trend_following import TrendFollowingConfig
from ui.colors import PROFIT, LOSS, FLAT
from threads.fetch_threads import TrendFollowingPortfolioThread
from ui.common import (
    create_font, _set_field_error, ThreadOwnerMixin,
    _STATUS_SUCCESS_COLOR, _STATUS_FAIL_COLOR,
)
from ui.dialogs.trend_following_portfolio import TrendFollowingPortfolioDialog, TrendFollowingValidationDialog

logger = logging.getLogger(__name__)

# Portfolio-level summary columns (keys of run_portfolio_backtest()["summary"]).
_METRICS = [
    ("total_return_pct", "Total return", "{:+.1f}%"),
    ("cagr_pct", "CAGR", "{:+.1f}%"),
    ("annual_vol_pct", "Annual vol", "{:.1f}%"),
    ("sharpe", "Sharpe", "{:.2f}"),
    ("max_drawdown_pct", "Max drawdown", "{:.1f}%"),
    ("n_trades", "Trades", "{}"),
    ("avg_gross_exposure_pct", "Exposure", "{:.0f}%"),
    ("avg_n_positions", "Avg positions", "{:.1f}"),
    ("n_instruments", "Instruments", "{}"),
]

# Per-instrument rows (entries of summary["instruments"]).
_INSTRUMENT_COLS = [
    ("ticker", "Ticker", "{}"),
    ("cagr_pct", "CAGR", "{:+.1f}%"),
    ("sharpe", "Sharpe", "{:.2f}"),
    ("max_drawdown_pct", "Max drawdown", "{:.1f}%"),
    ("n_trades", "Trades", "{}"),
    ("exposure_pct", "Exposure", "{:.0f}%"),
]
_SIGNED_KEYS = {"total_return_pct", "cagr_pct"}


class TrendFollowingTab(ThreadOwnerMixin, QWidget):

    def __init__(self, universe_tab=None, parent=None):
        super().__init__(parent)
        self._universe_tab = universe_tab
        self._portfolio_thread = None
        self._last_result = None
        self._build_ui()

    # ── UI ───────────────────────────────────────────────────────────────────
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(8)

        title = QLabel("Trend Following — Donchian Channel Breakout Backtest")
        title.setFont(create_font(16, QFont.Weight.Bold))
        root.addWidget(title)

        # Row 1: parameters + start date + run
        row1 = QHBoxLayout()
        row1.addWidget(self._lbl("entry_n:"))
        self._entry_spin = QSpinBox()
        self._entry_spin.setRange(1, 500)
        self._entry_spin.setValue(TrendFollowingConfig().entry_n)
        row1.addWidget(self._entry_spin)

        row1.addWidget(self._lbl("exit_n:"))
        self._exit_spin = QSpinBox()
        self._exit_spin.setRange(1, 500)
        self._exit_spin.setValue(TrendFollowingConfig().exit_n)
        row1.addWidget(self._exit_spin)

        row1.addWidget(self._lbl("Fee/side:"))
        self._fee_spin = self._pct_spin()
        row1.addWidget(self._fee_spin)

        row1.addWidget(self._lbl("Slippage/side:"))
        self._slip_spin = self._pct_spin()
        row1.addWidget(self._slip_spin)

        row1.addWidget(self._lbl("Start:"))
        self._start_edit = QDateEdit()
        self._start_edit.setFont(create_font(10, style_name="Semilight"))
        self._start_edit.setCalendarPopup(True)          # click opens a calendar (user direction)
        self._start_edit.setDisplayFormat("yyyy-MM-dd")
        self._start_edit.setMaximumDate(QDate.currentDate())
        self._start_edit.setDate(QDate(date.today() - timedelta(days=5 * 365)))
        self._start_edit.setFixedWidth(130)
        row1.addWidget(self._start_edit)

        self._run_btn = QPushButton("▶ Run Backtest")
        self._run_btn.setFont(create_font(10, QFont.Weight.Bold))
        self._run_btn.setFixedHeight(32)
        # docs/ui.md 1.6: the tab's one accented action; every other button
        # here (Use Universe, Validate) is the neutral outline from ui/theme.py.
        self._run_btn.setObjectName("primary")
        self._run_btn.setToolTip("Equal-sleeve portfolio backtest on the ticker list below "
                                 "(filled from the Trading Universe when empty)")
        self._run_btn.clicked.connect(self._on_run_clicked)
        row1.addWidget(self._run_btn)

        self._status_lbl = QLabel("")
        self._status_lbl.setFont(create_font(9, style_name="Semilight"))
        self._status_lbl.setObjectName("muted")
        row1.addWidget(self._status_lbl)
        row1.addStretch()
        root.addLayout(row1)

        # Row 2: v2 overlays (trend_following.md 3 "v2"); 0 = off, so defaults reproduce v1
        row2 = QHBoxLayout()
        row2.addWidget(self._lbl("Regime MA:"))
        self._regime_spin = QSpinBox()
        self._regime_spin.setRange(0, 500)
        self._regime_spin.setSpecialValueText("off")
        self._regime_spin.setValue(0)
        self._regime_spin.setToolTip("Enter only while Close is above this simple moving average (0 = off)")
        row2.addWidget(self._regime_spin)

        row2.addWidget(self._lbl("Stop ATR×:"))
        self._stop_spin = QDoubleSpinBox()
        self._stop_spin.setRange(0.0, 10.0)
        self._stop_spin.setDecimals(1)
        self._stop_spin.setSingleStep(0.5)
        self._stop_spin.setSpecialValueText("off")
        self._stop_spin.setValue(0.0)
        self._stop_spin.setToolTip("Exit when Close falls this many ATRs below the anchor (0 = off)")
        row2.addWidget(self._stop_spin)

        self._stop_mode_combo = QComboBox()
        self._stop_mode_combo.addItems(["trailing", "fixed"])
        self._stop_mode_combo.setToolTip("trailing: anchor = highest close since entry; fixed: anchor = entry close")
        row2.addWidget(self._stop_mode_combo)

        row2.addWidget(self._lbl("Vol target:"))
        self._vol_spin = QDoubleSpinBox()
        self._vol_spin.setRange(0.0, 100.0)
        self._vol_spin.setDecimals(1)
        self._vol_spin.setSingleStep(1.0)
        self._vol_spin.setSuffix(" %")
        self._vol_spin.setSpecialValueText("off")
        self._vol_spin.setValue(0.0)
        self._vol_spin.setToolTip("Size each trade to this annualised volatility (0 = off, full weight)")
        row2.addWidget(self._vol_spin)

        row2.addWidget(self._lbl("Max weight:"))
        self._maxw_spin = QDoubleSpinBox()
        self._maxw_spin.setRange(0.1, 3.0)
        self._maxw_spin.setDecimals(1)
        self._maxw_spin.setSingleStep(0.1)
        self._maxw_spin.setValue(1.0)
        self._maxw_spin.setToolTip("Cap on the position weight (1.0 = no leverage)")
        row2.addWidget(self._maxw_spin)
        row2.addStretch()
        root.addLayout(self._make_collapsible("Regime MA / ATR stop / Vol target (optional overlays, off by default)", row2))

        # Row 3: ticker list + IS/OOS validation (trend_following.md 3 "v3", 5)
        row3 = QHBoxLayout()
        row3.addWidget(self._lbl("Portfolio tickers:"))
        self._portfolio_edit = QLineEdit()
        self._portfolio_edit.setFont(create_font(10, style_name="Semilight"))
        self._portfolio_edit.setPlaceholderText("comma-separated, e.g. 005930, 000660, AAPL (empty = top-N Trading Universe)")
        self._portfolio_edit.setMinimumWidth(320)
        row3.addWidget(self._portfolio_edit, 1)

        row3.addWidget(self._lbl("Top N:"))
        self._topn_spin = QSpinBox()
        self._topn_spin.setRange(2, 300)
        self._topn_spin.setValue(20)
        self._topn_spin.setToolTip("How many Trading Universe stocks (by market cap) to load with the button "
                                   "or when the list is empty")
        row3.addWidget(self._topn_spin)

        self._use_universe_btn = QPushButton("Use Universe")
        self._use_universe_btn.setToolTip("Fill the ticker list with the top-N Trading Universe stocks by market cap")
        self._use_universe_btn.clicked.connect(self._on_use_universe)
        row3.addWidget(self._use_universe_btn)

        row3.addWidget(self._lbl("OOS years:"))
        self._oos_years_spin = QSpinBox()
        self._oos_years_spin.setRange(1, 15)
        self._oos_years_spin.setValue(5)
        self._oos_years_spin.setToolTip("Number of most recent calendar years used as yearly out-of-sample folds; "
                                        "the first of them is also the holdout split")
        row3.addWidget(self._oos_years_spin)

        self._validate_btn = QPushButton("⚖ Validate (IS/OOS)")
        self._validate_btn.setFont(create_font(10, QFont.Weight.Bold))
        self._validate_btn.setFixedHeight(32)
        self._validate_btn.setToolTip("Holdout + anchored yearly walk-forward over the 12-config default grid "
                                      "(entry/exit x vol target x ATR stop); the parameters above are the base config. "
                                      "Use a Start date well before the OOS years.")
        self._validate_btn.clicked.connect(self._on_validate_clicked)
        row3.addWidget(self._validate_btn)
        root.addLayout(self._make_collapsible("Portfolio tickers / IS-OOS validation", row3))

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

        # Per-instrument breakdown
        inst_lbl = QLabel("Instruments")
        inst_lbl.setFont(create_font(11, QFont.Weight.Bold))
        root.addWidget(inst_lbl)
        self._instruments_tbl = QTableWidget(0, len(_INSTRUMENT_COLS))
        self._instruments_tbl.setHorizontalHeaderLabels([label for _, label, _ in _INSTRUMENT_COLS])
        self._instruments_tbl.setFont(create_font(9, style_name="Semilight"))
        self._instruments_tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._instruments_tbl.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._instruments_tbl.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._instruments_tbl.verticalHeader().setVisible(False)
        root.addWidget(self._instruments_tbl, 1)

        disclaimer = QLabel(
            "⚠️ Research/backtesting tool, not investment advice. Single-period in-sample result; "
            "see trend_following.md section 5 for the preliminary real-data sweep and its caveats."
        )
        disclaimer.setFont(create_font(8, style_name="Semilight"))
        disclaimer.setObjectName("muted")
        disclaimer.setWordWrap(True)
        root.addWidget(disclaimer)

    @staticmethod
    def _lbl(text):
        lbl = QLabel(text)
        lbl.setFont(create_font(10, style_name="Semilight"))
        return lbl

    @staticmethod
    def _make_collapsible(header_text: str, body_layout) -> QVBoxLayout:
        """Wrap an existing row layout behind a toggle button, collapsed by default
        (roadmap 7-3) — keeps the tab's default exposure to the parameter row while the
        overlay / ticker-list controls stay one click away instead of always taking up
        screen space."""
        container = QVBoxLayout()
        container.setContentsMargins(0, 0, 0, 0)
        container.setSpacing(2)

        toggle_btn = QPushButton(f"▶ {header_text}")
        toggle_btn.setCheckable(True)
        toggle_btn.setChecked(False)
        toggle_btn.setFont(create_font(9, style_name="Semilight"))
        toggle_btn.setStyleSheet(
            "QPushButton { text-align:left; background:transparent; border:none; color:#7f8c8d; padding:2px 0; }"
            "QPushButton:checked { color:#0078d4; font-weight:bold; }"
        )

        body = QWidget()
        body.setLayout(body_layout)
        body.setVisible(False)

        def _on_toggled(checked):
            body.setVisible(checked)
            arrow = "▼" if checked else "▶"
            toggle_btn.setText(f"{arrow} {header_text}")

        toggle_btn.toggled.connect(_on_toggled)

        container.addWidget(toggle_btn)
        container.addWidget(body)
        return container

    @staticmethod
    def _pct_spin():
        sp = QDoubleSpinBox()
        sp.setRange(0.0, 5.0)
        sp.setDecimals(3)
        sp.setSingleStep(0.01)
        sp.setSuffix(" %")
        sp.setValue(0.0)
        return sp

    # ── ticker list ──────────────────────────────────────────────────────────
    def _universe_top_tickers(self, n: int) -> list:
        data = getattr(self._universe_tab, "all_data", None) or []
        stocks = [it for it in data if it.get("ticker") and not it.get("is_index")]
        stocks.sort(key=lambda it: -float(it.get("market_cap", 0) or 0))
        return [it["ticker"] for it in stocks[:n]]

    def _on_use_universe(self):
        tickers = self._universe_top_tickers(int(self._topn_spin.value()))
        if not tickers:
            QMessageBox.information(self, "No Data", "Trading Universe has no stocks yet — refresh it first.")
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

    def _start_iso(self) -> str:
        """The Start picker's date as YYYY-MM-DD (QDateEdit cannot hold an invalid date,
        so there is nothing to validate)."""
        return self._start_edit.date().toString("yyyy-MM-dd")

    def _read_inputs(self):
        """Returns (tickers, start, config) or None after flagging the ticker list.
        An empty list is filled with the top-N Trading Universe stocks first."""
        tickers = self._portfolio_tickers()
        if not tickers:
            tickers = self._universe_top_tickers(int(self._topn_spin.value()))
            if tickers:
                self._portfolio_edit.setText(", ".join(tickers))
        _set_field_error(self._portfolio_edit, "" if len(tickers) >= 2 else "Enter at least two tickers")
        if len(tickers) < 2:
            self._status_lbl.setText("Enter at least two tickers (or refresh the Trading Universe)")
            return None
        return tickers, self._start_iso(), self._config_from_inputs()

    # ── run ──────────────────────────────────────────────────────────────────
    def _on_run_clicked(self):
        self._start_portfolio_run("portfolio")

    def _on_validate_clicked(self):
        self._start_portfolio_run("validate")

    def _start_portfolio_run(self, mode: str):
        if self._portfolio_thread is not None and self._portfolio_thread.isRunning():
            return
        inputs = self._read_inputs()
        if inputs is None:
            return
        tickers, start, config = inputs
        oos_last = date.today().year
        oos_first = oos_last - int(self._oos_years_spin.value()) + 1
        self._run_btn.setEnabled(False)
        self._validate_btn.setEnabled(False)
        self._status_lbl.setText(f"{'Validating' if mode == 'validate' else 'Portfolio backtest'}: {len(tickers)} tickers from {start}...")
        self._track_thread(TrendFollowingPortfolioThread(tickers, start, config, mode=mode,
                                                         oos_first_year=oos_first, oos_last_year=oos_last),
                           '_portfolio_thread')
        self._portfolio_thread.progress.connect(self._status_lbl.setText)
        self._portfolio_thread.finished.connect(self._on_portfolio_finished)
        self._portfolio_thread.start()

    def _on_portfolio_finished(self, result, error: str):
        self._run_btn.setEnabled(True)
        self._validate_btn.setEnabled(True)
        if error or result is None:
            self._status_lbl.setText("Portfolio run failed — see app.log")
            QMessageBox.warning(self, "Portfolio Error", f"Run failed:\n{error or 'unknown error'}")
            return
        if "walkforward" in result:
            wf = result["walkforward"]
            if not wf.get("n_folds"):
                self._status_lbl.setText("Validation produced no folds — use an earlier Start date")
                QMessageBox.information(self, "Validation", "No out-of-sample fold had enough in-sample history. "
                                                            "Set an earlier Start date or fewer OOS years.")
                return
            o = wf["oos"]
            self._status_lbl.setText(
                f"Walk-forward OOS ({wf['n_folds']} folds): Sharpe {o['sharpe']:.2f}, MDD {o['max_drawdown_pct']:.1f}%, "
                f"CAGR {o['cagr_pct']:+.1f}% — gate {'PASS' if o['passes_risk_gate'] else 'FAIL'}"
            )
            TrendFollowingValidationDialog(result, parent=self).exec()
            return
        s = result["summary"]
        if not s.get("n_instruments"):
            self._status_lbl.setText("Portfolio: no usable history")
            QMessageBox.information(self, "No Data", "None of the tickers returned enough history.")
            return
        self._last_result = result
        self._render(result)
        skipped = s.get("skipped") or []
        self._status_lbl.setText(
            f"Portfolio ({s['n_instruments']} instruments): Sharpe {s['sharpe']:.2f}, MDD {s['max_drawdown_pct']:.1f}%, "
            f"CAGR {s['cagr_pct']:+.1f}%, exposure {s['avg_gross_exposure_pct']:.0f}% — gate {'PASS' if s['passes_risk_gate'] else 'FAIL'}"
            + (f" | skipped {len(skipped)}: {', '.join(skipped[:5])}{'…' if len(skipped) > 5 else ''}" if skipped else "")
        )
        TrendFollowingPortfolioDialog(result, parent=self).exec()

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
                if key in _SIGNED_KEYS:
                    it.setForeground(QColor(PROFIT if val > 0 else LOSS if val < 0 else FLAT))
                tbl.setItem(0, c, it)
            gate = bool(s.get("passes_risk_gate"))
            gate_it = QTableWidgetItem("PASS" if gate else "FAIL")
            gate_it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            gate_it.setForeground(QColor(_STATUS_SUCCESS_COLOR if gate else _STATUS_FAIL_COLOR))
            tbl.setItem(0, len(_METRICS), gate_it)
        finally:
            tbl.setUpdatesEnabled(True)

        instruments = s.get("instruments") or []
        tt = self._instruments_tbl
        tt.setUpdatesEnabled(False)
        try:
            tt.setRowCount(len(instruments))
            right = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            for r, inst in enumerate(instruments):
                for c, (key, _label, fmt) in enumerate(_INSTRUMENT_COLS):
                    val = inst.get(key, 0)
                    it = QTableWidgetItem(fmt.format(val))
                    it.setTextAlignment(Qt.AlignmentFlag.AlignCenter if key == "ticker" else right)
                    if key in _SIGNED_KEYS:
                        it.setForeground(QColor(PROFIT if val > 0 else LOSS if val < 0 else FLAT))
                    tt.setItem(r, c, it)
        finally:
            tt.setUpdatesEnabled(True)
