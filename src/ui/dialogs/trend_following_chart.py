"""ui/dialogs/trend_following_chart.py — TrendFollowingChartDialog: price with the
Donchian channel and entry/exit markers, plus strategy equity vs buy-and-hold, for one
strategy.trend_following.run_backtest() result (trend_following.md 4, 5).

Display only: the result dict is computed elsewhere; nothing here touches the engine.
"""
import logging
from datetime import datetime

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QPushButton, QLabel, QTableWidget, QTableWidgetItem, QHeaderView,
    QWidget, QTabWidget,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor

import matplotlib.dates as mdates
from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas

logger = logging.getLogger(__name__)


class TrendFollowingChartDialog(QDialog):

    def __init__(self, result: dict, ticker: str = "", parent=None):
        super().__init__(parent)
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowMaximizeButtonHint | Qt.WindowType.WindowMinimizeButtonHint)
        self.setWindowTitle(f"Trend Following Backtest — {ticker or 'chart'} (trend_following.md 3)")
        self.resize(1000, 760)

        layout = QVBoxLayout(self)
        s = result["summary"]
        gate = s.get("passes_risk_gate", False)
        header = QLabel(
            f"<b>{ticker}</b> {s.get('start_date') or ''} → {s.get('end_date') or ''} | "
            f"Donchian {s.get('entry_n')}/{s.get('exit_n')}, cost/side {s.get('cost_per_side', 0) * 100:.2f}% — "
            f"Return <b style='color:{'#c0392b' if s['total_return_pct'] >= 0 else '#2980b9'}'>{s['total_return_pct']:+.1f}%</b> | "
            f"CAGR {s['cagr_pct']:+.1f}% | Sharpe {s['sharpe']:.2f} | MDD {s['max_drawdown_pct']:.1f}% | "
            f"{s['n_trades']} trades, win rate {s['win_rate_pct']:.0f}%, exposure {s['exposure_pct']:.0f}% | "
            f"risk gate <b style='color:{'#107c10' if gate else '#c0392b'}'>{'PASS' if gate else 'FAIL'}</b>"
        )
        header.setTextFormat(Qt.TextFormat.RichText)
        header.setWordWrap(True)
        layout.addWidget(header)

        tabs = QTabWidget()
        layout.addWidget(tabs, 1)

        chart_tab = QWidget()
        chart_layout = QVBoxLayout(chart_tab)
        chart_layout.setContentsMargins(0, 0, 0, 0)
        tabs.addTab(chart_tab, "Chart")
        chart_layout.addWidget(FigureCanvas(self._build_figure(result, ticker)), 1)

        trades = result.get("trades") or []
        trades_tab = QWidget()
        trades_layout = QVBoxLayout(trades_tab)
        trades_layout.setContentsMargins(0, 0, 0, 0)
        tabs.addTab(trades_tab, f"Trades ({len(trades)})")
        trades_layout.addWidget(self._build_trades_table(trades), 1)

        note = QLabel(
            "⚠️ Signals are confirmed on the close and applied from the next day; "
            "long only, single position. Research tool, not investment advice — see trend_following.md section 5."
        )
        note.setStyleSheet("color:#888; font-size:9pt;")
        note.setWordWrap(True)
        layout.addWidget(note)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn, alignment=Qt.AlignmentFlag.AlignRight)

    # ── chart ────────────────────────────────────────────────────────────────
    @staticmethod
    def _build_figure(result: dict, ticker: str) -> Figure:
        fig = Figure(figsize=(10, 7), constrained_layout=True)
        ax_px, ax_eq = fig.subplots(2, 1, sharex=True, gridspec_kw={"height_ratios": [3, 2]})

        sig = result.get("signals")
        if sig is None or sig.height == 0:
            ax_px.set_title("No data")
            return fig

        dates = [datetime.combine(d, datetime.min.time()) if not isinstance(d, datetime) else d
                 for d in sig.get_column("Date").to_list()]
        x = mdates.date2num(dates)
        close = sig.get_column("Close").to_list()
        upper = sig.get_column("upper").to_list()
        lower = sig.get_column("lower").to_list()

        ax_px.plot(x, close, color="#2c3e50", linewidth=1.2, label="Close")
        ax_px.plot(x, upper, color="#27ae60", linewidth=1, linestyle="--", label=f"Upper ({result['summary'].get('entry_n')}d high)")
        ax_px.plot(x, lower, color="#c0392b", linewidth=1, linestyle="--", label=f"Lower ({result['summary'].get('exit_n')}d low)")

        v2 = result["summary"].get("v2") or {}
        if "regime_ma" in sig.columns and sig.get_column("regime_ma").null_count() < sig.height:
            ax_px.plot(x, sig.get_column("regime_ma").to_list(), color="#2980b9", linewidth=1,
                       label=f"Regime MA{v2.get('regime_ma_n', '')}")
        if "stop" in sig.columns and sig.get_column("stop").null_count() < sig.height:
            ax_px.plot(x, sig.get_column("stop").to_list(), color="#d35400", linewidth=1.2,
                       linestyle=":", label="ATR stop")

        pos = sig.get_column("position").to_list()
        ax_px.fill_between(x, min(close), max(close), where=[p == 1 for p in pos],
                           color="#27ae60", alpha=0.06, linewidth=0, label="In position")

        close_by_date = {d.strftime("%Y-%m-%d"): c for d, c in zip(dates, close)}
        entries = [(t["entry_date"], close_by_date.get(t["entry_date"])) for t in result.get("trades") or []]
        exits = [(t["exit_date"], close_by_date.get(t["exit_date"])) for t in result.get("trades") or [] if t["exit_date"]]
        if entries:
            ex = mdates.date2num([datetime.strptime(d, "%Y-%m-%d") for d, _ in entries])
            ax_px.scatter(ex, [c for _, c in entries], marker="^", color="#27ae60", s=60, zorder=5, label="Entry")
        if exits:
            xx = mdates.date2num([datetime.strptime(d, "%Y-%m-%d") for d, _ in exits])
            ax_px.scatter(xx, [c for _, c in exits], marker="v", color="#c0392b", s=60, zorder=5, label="Exit")

        ax_px.set_title(f"{ticker} — Donchian channel breakout", fontsize=12, fontweight="bold")
        ax_px.grid(True, linestyle=":", alpha=0.5)
        ax_px.legend(fontsize=8, loc="upper left")

        equity = sig.get_column("equity").to_list()
        base = equity[0] if equity and equity[0] else 1.0
        strat = [e / base for e in equity]
        bh = [c / close[0] for c in close] if close and close[0] else [1.0] * len(close)
        ax_eq.plot(x, strat, color="#c0392b", linewidth=1.8, label="Strategy")
        ax_eq.plot(x, bh, color="#8e44ad", linewidth=1.2, linestyle="--", label="Buy & hold")
        ax_eq.axhline(1.0, color="#999", linewidth=0.8)
        ax_eq.set_ylabel("Growth of 1")
        ax_eq.grid(True, linestyle=":", alpha=0.5)
        ax_eq.legend(fontsize=8, loc="upper left")
        ax_eq.xaxis.set_major_formatter(mdates.DateFormatter("%y.%m"))
        fig.autofmt_xdate(rotation=25)
        return fig

    # ── trades table ─────────────────────────────────────────────────────────
    @staticmethod
    def _build_trades_table(trades: list) -> QTableWidget:
        tbl = QTableWidget()
        cols = ["#", "Entry", "Exit", "Reason", "Days", "Weight", "Price %", "Return %"]
        tbl.setColumnCount(len(cols))
        tbl.setHorizontalHeaderLabels(cols)
        tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        tbl.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        tbl.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        tbl.verticalHeader().setVisible(False)
        right = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter

        def cell(text, align=Qt.AlignmentFlag.AlignCenter):
            it = QTableWidgetItem(text)
            it.setTextAlignment(align)
            return it

        tbl.setUpdatesEnabled(False)
        try:
            tbl.setRowCount(len(trades))
            for r, t in enumerate(trades):
                ret = t.get("return_pct", 0.0)
                px = t.get("price_return_pct", 0.0)
                reason = t.get("exit_reason") or ("open" if not t.get("exit_date") else "")
                ret_it = cell(f"{ret:+.2f}%", right)
                ret_it.setForeground(QColor("#c0392b" if ret > 0 else "#2980b9" if ret < 0 else "#555"))
                px_it = cell(f"{px:+.2f}%", right)
                px_it.setForeground(QColor("#c0392b" if px > 0 else "#2980b9" if px < 0 else "#555"))
                reason_it = cell(reason)
                if reason == "stop":
                    reason_it.setForeground(QColor("#d35400"))
                tbl.setItem(r, 0, cell(str(r + 1)))
                tbl.setItem(r, 1, cell(t.get("entry_date", "")))
                tbl.setItem(r, 2, cell(t.get("exit_date") or "open"))
                tbl.setItem(r, 3, reason_it)
                tbl.setItem(r, 4, cell(str(t.get("days_held", 0)), right))
                tbl.setItem(r, 5, cell(f"{t.get('weight', 1.0):.2f}", right))
                tbl.setItem(r, 6, px_it)
                tbl.setItem(r, 7, ret_it)
        finally:
            tbl.setUpdatesEnabled(True)
        return tbl
