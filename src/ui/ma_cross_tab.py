"""ui/ma_cross_tab.py — MaCrossTab: the "MA Cross" sub-tab of the Strategy tab
(ma_cross.md 3, 4).

Runs strategy.ma_cross.run_backtest_for_stock() for one ticker in
MaCrossBacktestThread and shows the result: a KPI strip (trades / wins / win
rate / compounded return), a Close + fast/slow MA chart with the entry and
exit fills marked, and the trade list. Research only -- nothing here places
orders (ma_cross.md 1).

Reads UniverseTab.all_data on demand to offer a ticker picker, the same
direct-reference pattern as ui/trend_following_tab.py.
"""
import logging
from datetime import date

import matplotlib.dates as mdates
from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit, QComboBox,
    QSpinBox, QDoubleSpinBox, QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox,
    QFrame, QSplitter,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QColor

from data_fetcher import is_kr_code
from strategy.ma_cross import MaCrossConfig
from threads.fetch_threads import MaCrossBacktestThread
from ui.common import create_font, ThreadOwnerMixin, FONT_TITLE, FONT_BODY, FONT_SMALL, FONT_KPI
from ui.colors import PROFIT, LOSS, FLAT, ACTION_BUY, ACTION_SELL, MA_RAMP
from ui.theme import TEXT, TEXT_MUTED, LINE
from ui.widgets import NumericItem

logger = logging.getLogger(__name__)

_TRADE_COLS = ["#", "Buy date", "Buy price", "Sell date", "Sell price", "Return %", "Days", "Annualized %"]


def _fmt_date(v) -> str:
    try:
        return str(v)[:10]
    except Exception:
        return ""


class MaCrossTab(ThreadOwnerMixin, QWidget):

    def __init__(self, universe_tab=None, parent=None):
        super().__init__(parent)
        self._universe_tab = universe_tab
        self._backtest_thread = None
        self._last_result = None
        self._last_ticker = ""
        self._build_ui()

    # ── UI ───────────────────────────────────────────────────────────────────
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(8)

        title = QLabel("MA Cross — Fast/Slow Moving Average Golden Cross Backtest")
        title.setFont(create_font(FONT_TITLE, QFont.Weight.Bold))
        root.addWidget(title)

        subtitle = QLabel(
            "Buy at the close when the fast MA crosses above slow MA x entry multiple; sell at the next open "
            "on take-profit, overheat (fast >= slow x overheat multiple) or the dead cross; one position at a "
            "time, long only (ma_cross.md 3)."
        )
        subtitle.setFont(create_font(FONT_SMALL, style_name="Semilight"))
        subtitle.setObjectName("muted")
        subtitle.setWordWrap(True)
        root.addWidget(subtitle)

        # Row 1: ticker / universe picker / target year
        row1 = QHBoxLayout()
        row1.addWidget(self._lbl("Ticker:"))
        self._ticker_edit = QLineEdit()
        self._ticker_edit.setFont(create_font(FONT_BODY, style_name="Semilight"))
        self._ticker_edit.setPlaceholderText("e.g. 005930, AAPL")
        self._ticker_edit.setFixedWidth(150)
        self._ticker_edit.returnPressed.connect(self._on_run_clicked)
        row1.addWidget(self._ticker_edit)

        row1.addWidget(self._lbl("From Universe:"))
        self._universe_combo = QComboBox()
        self._universe_combo.setFont(create_font(FONT_BODY, style_name="Semilight"))
        self._universe_combo.setMinimumWidth(260)
        self._universe_combo.currentIndexChanged.connect(self._on_universe_pick)
        row1.addWidget(self._universe_combo)

        row1.addWidget(self._lbl("Year:"))
        self._year_combo = QComboBox()
        self._year_combo.setFont(create_font(FONT_BODY, style_name="Semilight"))
        self._year_combo.addItem(f"Trailing {MaCrossConfig().days // 365}y", userData=None)
        for y in range(date.today().year, date.today().year - 6, -1):
            self._year_combo.addItem(str(y), userData=y)
        self._year_combo.setToolTip("Only entries signalled in that calendar year (history starts the prior September for MA warm-up)")
        row1.addWidget(self._year_combo)
        row1.addStretch()
        root.addLayout(row1)

        # Row 2: parameters + run
        cfg = MaCrossConfig()
        row2 = QHBoxLayout()
        row2.addWidget(self._lbl("Fast MA:"))
        self._fast_spin = self._int_spin(cfg.fast_n, 2, 200)
        row2.addWidget(self._fast_spin)
        row2.addWidget(self._lbl("Slow MA:"))
        self._slow_spin = self._int_spin(cfg.slow_n, 3, 400)
        row2.addWidget(self._slow_spin)
        row2.addWidget(self._lbl("Entry x:"))
        self._entry_spin = self._mult_spin(cfg.entry_mult, 0.5)
        row2.addWidget(self._entry_spin)
        row2.addWidget(self._lbl("Take profit x:"))
        self._tp_spin = self._mult_spin(cfg.take_profit_mult, 1.01)
        row2.addWidget(self._tp_spin)
        row2.addWidget(self._lbl("Overheat x:"))
        self._overheat_spin = self._mult_spin(cfg.overheat_mult, 1.01)
        row2.addWidget(self._overheat_spin)

        self._run_btn = QPushButton("▶ Run Backtest")
        self._run_btn.setFont(create_font(FONT_BODY, QFont.Weight.Bold))
        self._run_btn.setFixedHeight(32)
        self._run_btn.setObjectName("primary")   # the tab's one accented action (docs/ui.md 1.6)
        self._run_btn.clicked.connect(self._on_run_clicked)
        row2.addWidget(self._run_btn)

        self._status_lbl = QLabel("")
        self._status_lbl.setFont(create_font(FONT_SMALL, style_name="Semilight"))
        self._status_lbl.setObjectName("muted")
        row2.addWidget(self._status_lbl)
        row2.addStretch()
        root.addLayout(row2)

        root.addWidget(self._build_kpi_strip())

        splitter = QSplitter(Qt.Orientation.Vertical)
        self._fig = Figure(figsize=(9, 4.5))
        self._canvas = FigureCanvas(self._fig)
        splitter.addWidget(self._canvas)
        splitter.addWidget(self._build_trades_table())
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        root.addWidget(splitter, 1)

        note = QLabel(
            "Research/backtesting signal generator, not investment advice. Fees, taxes and slippage are not "
            "modelled and open positions at the end of the data are not counted (ma_cross.md 6)."
        )
        note.setFont(create_font(FONT_SMALL, style_name="Semilight"))
        note.setObjectName("muted")
        note.setWordWrap(True)
        root.addWidget(note)

    def _build_kpi_strip(self) -> QFrame:
        card = QFrame()
        card.setObjectName("DashboardCard")
        layout = QHBoxLayout(card)
        layout.setContentsMargins(16, 8, 16, 8)
        layout.setSpacing(0)
        self._kpi = {}
        for key, label, sub in (
            ("n_trades", "Trades", "closed round trips"),
            ("win_count", "Wins", "return > 0"),
            ("win_rate_pct", "Win rate", "wins / trades"),
            ("cumulative_return_pct", "Cumulative return", "compounded across trades"),
        ):
            box = QVBoxLayout()
            box.setSpacing(2)
            lbl = QLabel(label.upper())
            lbl.setObjectName("kpiLabel")
            box.addWidget(lbl)
            value = QLabel("—")
            value.setObjectName("kpiValue")
            value.setFont(create_font(FONT_KPI, style_name="Semilight"))
            box.addWidget(value)
            sub_lbl = QLabel(sub)
            sub_lbl.setObjectName("kpiSub")
            box.addWidget(sub_lbl)
            layout.addLayout(box, 1)
            self._kpi[key] = value
        return card

    def _build_trades_table(self) -> QTableWidget:
        tbl = self._trades_tbl = QTableWidget(0, len(_TRADE_COLS))
        tbl.setHorizontalHeaderLabels(_TRADE_COLS)
        tbl.setFont(create_font(FONT_SMALL, style_name="Semilight"))
        tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        tbl.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        tbl.setAlternatingRowColors(True)
        tbl.verticalHeader().setVisible(False)
        tbl.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        tbl.setSortingEnabled(True)
        return tbl

    @staticmethod
    def _lbl(text):
        lbl = QLabel(text)
        lbl.setFont(create_font(FONT_BODY, style_name="Semilight"))
        return lbl

    @staticmethod
    def _int_spin(value, lo, hi):
        sp = QSpinBox()
        sp.setRange(lo, hi)
        sp.setValue(value)
        return sp

    @staticmethod
    def _mult_spin(value, lo):
        sp = QDoubleSpinBox()
        sp.setRange(lo, 5.0)
        sp.setDecimals(2)
        sp.setSingleStep(0.05)
        sp.setValue(value)
        return sp

    # ── universe picker ──────────────────────────────────────────────────────
    def showEvent(self, event):
        super().showEvent(event)
        self._refresh_universe_combo()

    def _refresh_universe_combo(self):
        data = getattr(self._universe_tab, "all_data", None) or []
        items = [(it.get("ticker", ""), it.get("name", ""), it.get("market", ""))
                 for it in data if it.get("ticker") and not it.get("is_index")]
        current = self._universe_combo.currentData()
        self._universe_combo.blockSignals(True)
        try:
            self._universe_combo.clear()
            self._universe_combo.addItem("(pick from Trading Universe)", userData=None)
            for ticker, name, market in items:
                self._universe_combo.addItem(f"{name} ({ticker})", userData=(ticker, market))
            if current:
                idx = self._universe_combo.findData(current)
                if idx >= 0:
                    self._universe_combo.setCurrentIndex(idx)
        finally:
            self._universe_combo.blockSignals(False)

    def _on_universe_pick(self, _index):
        picked = self._universe_combo.currentData()
        if picked:
            self._ticker_edit.setText(picked[0])

    # ── inputs ───────────────────────────────────────────────────────────────
    def _read_inputs(self):
        """(ticker, market, config, target_year) or None after a warning."""
        ticker = self._ticker_edit.text().strip().upper()
        if not ticker:
            QMessageBox.warning(self, "Input Error", "Enter a ticker or pick one from the Trading Universe.")
            return None
        picked = self._universe_combo.currentData()
        if picked and picked[0] == ticker:
            market = picked[1]
        else:
            market = "KOSPI" if is_kr_code(ticker) else ""
        try:
            config = MaCrossConfig(
                fast_n=int(self._fast_spin.value()),
                slow_n=int(self._slow_spin.value()),
                entry_mult=float(self._entry_spin.value()),
                take_profit_mult=float(self._tp_spin.value()),
                overheat_mult=float(self._overheat_spin.value()),
            )
        except ValueError as e:
            QMessageBox.warning(self, "Input Error", str(e))
            return None
        return ticker, market, config, self._year_combo.currentData()

    # ── run ──────────────────────────────────────────────────────────────────
    def _on_run_clicked(self):
        if self._backtest_thread is not None and self._backtest_thread.isRunning():
            return
        inputs = self._read_inputs()
        if inputs is None:
            return
        ticker, market, config, target_year = inputs
        self._last_ticker = ticker
        self._run_btn.setEnabled(False)
        self._status_lbl.setText(f"Fetching {ticker} history...")
        self._track_thread(MaCrossBacktestThread(ticker, market, config, target_year), '_backtest_thread')
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
            return
        self._last_result = result
        s = result["summary"]
        self._status_lbl.setText(
            f"{self._last_ticker}: {s['n_trades']} trades, {s['win_rate_pct']:.0f}% win rate, "
            f"{s['cumulative_return_pct']:+.1f}% compounded"
        )
        self._render(result)

    # ── render ───────────────────────────────────────────────────────────────
    def _render(self, result: dict):
        s = result["summary"]
        self._kpi["n_trades"].setText(str(s["n_trades"]))
        self._kpi["win_count"].setText(str(s["win_count"]))
        self._kpi["win_rate_pct"].setText(f"{s['win_rate_pct']:.0f}%")
        cum = s["cumulative_return_pct"]
        self._kpi["cumulative_return_pct"].setText(f"{cum:+.1f}%")
        self._kpi["cumulative_return_pct"].setStyleSheet(f"color:{PROFIT if cum > 0 else LOSS if cum < 0 else FLAT};")
        self._fill_trades(result.get("trades") or [])
        self._draw_chart(result)

    def _fill_trades(self, trades: list):
        tbl = self._trades_tbl
        right = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        tbl.setSortingEnabled(False)
        tbl.setUpdatesEnabled(False)
        try:
            tbl.setRowCount(len(trades))
            for r, t in enumerate(trades):
                ret = float(t["return_pct"])
                tone = QColor(PROFIT if ret > 0 else LOSS if ret < 0 else FLAT)
                cells = [
                    NumericItem(str(r + 1), r + 1),
                    QTableWidgetItem(_fmt_date(t["buy_date"])),
                    NumericItem(f"{t['buy_price']:,.0f}", float(t["buy_price"])),
                    QTableWidgetItem(_fmt_date(t["sell_date"])),
                    NumericItem(f"{t['sell_price']:,.0f}", float(t["sell_price"])),
                    NumericItem(f"{ret:+.2f}%", ret),
                    NumericItem(str(t["days_held"]), int(t["days_held"])),
                    NumericItem(f"{t['ann_return']:+.1f}%", float(t["ann_return"])),
                ]
                for c, it in enumerate(cells):
                    it.setTextAlignment(right if c in (0, 2, 4, 5, 6, 7) else Qt.AlignmentFlag.AlignCenter)
                    if c in (5, 7):
                        it.setForeground(tone)
                    tbl.setItem(r, c, it)
        finally:
            tbl.setUpdatesEnabled(True)
            tbl.setSortingEnabled(True)

    def _draw_chart(self, result: dict):
        fig = self._fig
        fig.clear()
        ax = fig.add_subplot(111)
        df = result.get("df")
        cfg = self._read_config_for_chart()
        if df is None or df.is_empty():
            ax.text(0.5, 0.5, "No data", ha="center", va="center", transform=ax.transAxes, color=TEXT_MUTED)
            self._canvas.draw_idle()
            return
        x = mdates.date2num(df.get_column("Date").to_numpy())
        ax.plot(x, df.get_column("Close").to_numpy(), color=TEXT, linewidth=1.4, label="Close")
        for col, color in ((cfg.fast_col, MA_RAMP["MA20"]), (cfg.slow_col, MA_RAMP["MA50"])):
            if col in df.columns:
                ax.plot(x, df.get_column(col).to_numpy(), color=color, linewidth=1.2, label=col)
        trades = result.get("trades") or []
        if trades:
            bx = mdates.date2num([t["buy_date"] for t in trades])
            sx = mdates.date2num([t["sell_date"] for t in trades])
            ax.scatter(bx, [t["buy_price"] for t in trades], marker="^", color=ACTION_BUY, s=55, zorder=5, label="Buy")
            ax.scatter(sx, [t["sell_price"] for t in trades], marker="v", color=ACTION_SELL, s=55, zorder=5, label="Sell")
        ax.grid(True, linestyle=":", color=LINE)
        ax.tick_params(labelsize=FONT_SMALL, colors=TEXT_MUTED)
        for spine in ("top", "right"):
            ax.spines[spine].set_visible(False)
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%y.%m"))
        ax.legend(fontsize=FONT_SMALL, loc="upper left", frameon=False)
        ax.set_title(f"{result.get('ticker', '')} — {cfg.fast_col}/{cfg.slow_col} cross", fontsize=FONT_BODY, fontweight="bold")
        fig.tight_layout()
        self._canvas.draw_idle()

    def _read_config_for_chart(self) -> MaCrossConfig:
        try:
            return MaCrossConfig(fast_n=int(self._fast_spin.value()), slow_n=int(self._slow_spin.value()))
        except ValueError:
            return MaCrossConfig()
