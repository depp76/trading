"""ui/dialogs/backtest_result.py — BacktestResultDialog — one strategy.rebalance.run_rebalance_backtest() result.

Split out of the former single ui/dialogs.py (2026-09-17)."""
import logging
from datetime import datetime

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QPushButton, QLabel, QTableWidget, QTableWidgetItem, QHeaderView, QWidget, QTabWidget,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor

import matplotlib.dates as mdates
import mplcursors

from ui.colors import PROFIT, LOSS, ACTION_BUY, ACTION_SELL, WARN
from ui.theme import ACCENT, TEXT_MUTED
from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas

logger = logging.getLogger(__name__)



# ---------------------------------------------------------------------------
# Weekly rebalance backtest result dialog (rebalance.md section 6)
# ---------------------------------------------------------------------------
class BacktestResultDialog(QDialog):
    """Shows one strategy.rebalance.run_rebalance_backtest() result: an equity
    curve (strategy vs benchmark) plus summary stats. Purely a display of an
    already-computed result dict -- no computation happens in this class,
    so changes to the backtest engine (data_fetcher.py) never require
    touching this dialog unless the result dict's shape itself changes."""

    def __init__(self, result: dict, parent=None, ticker_name_map: dict = None):
        super().__init__(parent)
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowMaximizeButtonHint | Qt.WindowType.WindowMinimizeButtonHint)
        self.setWindowTitle("Weekly Rebalance Backtest \u2014 rebalance.md 3-1")
        self.resize(900, 700)
        self.showMaximized()

        layout = QVBoxLayout(self)

        s = result["summary"]
        cost_str = f" | Total Cost: {s.get('total_cost_amount', 0.0):,.0f} KRW ({s.get('total_cost_drag_pct', 0.0):.2f}%)" if s.get('total_cost_amount') else ""
        top_n_str = ", ".join(f"{m}={n}" for m, n in result.get("top_n_by_market", {}).items())
        header = QLabel(
            f"<b>{result['start_date']} \u2192 {result['end_date']}</b> "
            f"({result['lookback_years']}y, top_n=[{top_n_str}], band\u00d7{result['band_multiplier']}) \u2014 "
            f"Net Return: <b style='color:{PROFIT if s['total_return_pct'] >= 0 else LOSS}'>"
            f"{s['total_return_pct']:+.1f}%</b> vs benchmark {s['benchmark_return_pct']:+.1f}% | "
            f"CAGR {s['cagr_pct']:+.1f}% | Max Drawdown {s['max_drawdown_pct']:.1f}% | "
            f"Sharpe {s.get('sharpe', 0.0):.2f} | Annual Vol {s.get('annual_vol_pct', 0.0):.1f}% | "
            f"{s['n_rebalances']} rebalances, {s['n_trades']} trades, win rate {s['win_rate_pct']:.0f}%"
            f"{cost_str}"
        )
        header.setTextFormat(Qt.TextFormat.RichText)
        header.setWordWrap(True)
        layout.addWidget(header)

        if result.get("skipped_tickers"):
            skipped = QLabel(
                f"\u26a0\ufe0f Skipped {len(result['skipped_tickers'])} ticker(s) with insufficient history: "
                + ", ".join(result["skipped_tickers"][:15])
                + (" ..." if len(result["skipped_tickers"]) > 15 else "")
            )
            skipped.setStyleSheet(f"color:{WARN};")
            skipped.setWordWrap(True)
            layout.addWidget(skipped)

        tabs = QTabWidget()
        layout.addWidget(tabs, 1)

        chart_tab = QWidget()
        chart_layout = QVBoxLayout(chart_tab)
        chart_layout.setContentsMargins(0, 0, 0, 0)
        tabs.addTab(chart_tab, "Chart")

        fig = Figure(figsize=(9, 5.5), constrained_layout=True)
        ax = fig.add_subplot(111)

        equity = result["equity_curve"]
        bench = result["benchmark_curve"]

        if equity:
            eq_dates = [datetime.strptime(pt["date"], "%Y-%m-%d") for pt in equity]
            eq_x = mdates.date2num(eq_dates)
            eq_y = [pt["value"] for pt in equity]
            ax.plot(eq_x, eq_y, color=ACCENT, linewidth=2, label="Strategy")

        if bench:
            bn_dates = [datetime.strptime(pt["date"], "%Y-%m-%d") for pt in bench]
            bn_x = mdates.date2num(bn_dates)
            bn_y = [pt["value"] for pt in bench]
            ax.plot(bn_x, bn_y, color=TEXT_MUTED, linewidth=1.6, linestyle="--", label="Benchmark (KOSPI)")

        ax.xaxis.set_major_formatter(mdates.DateFormatter("%y.%m"))
        fig.autofmt_xdate(rotation=25)
        ax.legend(fontsize=9)
        ax.grid(True, linestyle=":", alpha=0.5)
        ax.set_ylabel("Portfolio Value (KRW)")
        ax.set_title("Weekly Rebalance Backtest \u2014 Strategy vs Benchmark", fontsize=12, fontweight="bold")

        if equity:
            sc_eq = ax.scatter(eq_x, eq_y, alpha=0)
            cursors_artists = [sc_eq]
            sc_bn = None
            if bench:
                sc_bn = ax.scatter(bn_x, bn_y, alpha=0)
                cursors_artists.append(sc_bn)

            cursor = mplcursors.cursor(cursors_artists, hover=2)

            @cursor.connect("add")
            def on_add(sel):
                idx = int(sel.index)
                if sc_bn is not None and sel.artist is sc_bn:
                    lbl = "Benchmark"
                    date_str = bench[idx]["date"] if 0 <= idx < len(bench) else ""
                    val = bench[idx]["value"] if 0 <= idx < len(bench) else 0.0
                else:
                    lbl = "Strategy"
                    date_str = equity[idx]["date"] if 0 <= idx < len(equity) else ""
                    val = equity[idx]["value"] if 0 <= idx < len(equity) else 0.0
                sel.annotation.set_text(f"{lbl}\n{date_str}: {val:,.0f} KRW")
                sel.annotation.get_bbox_patch().set(fc="white", alpha=0.9, edgecolor="gray")

        canvas = FigureCanvas(fig)
        chart_layout.addWidget(canvas, 1)

        trades_tab = QWidget()
        trades_layout = QVBoxLayout(trades_tab)
        trades_layout.setContentsMargins(0, 0, 0, 0)
        tabs.addTab(trades_tab, f"Trade History ({len(result.get('trades') or [])})")

        ticker_name_map = ticker_name_map or {}

        trades_tbl = QTableWidget()
        cols = ["Date", "Name", "Ticker", "Action", "Price", "Shares", "Gross Amount", "Fee", "Tax", "Net Amount"]
        trades_tbl.setColumnCount(len(cols))
        trades_tbl.setHorizontalHeaderLabels(cols)
        trades_tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        trades_tbl.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        trades_tbl.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        trades_tbl.verticalHeader().setVisible(False)
        trades_tbl.setSortingEnabled(False)

        def _trade_cell(text, align=Qt.AlignmentFlag.AlignCenter):
            it = QTableWidgetItem(text)
            it.setTextAlignment(align)
            return it

        trades = result.get("trades") or []
        trades_tbl.setUpdatesEnabled(False)
        try:
            trades_tbl.setRowCount(len(trades))
            for r, tr in enumerate(trades):
                ticker = tr.get("ticker", "")
                action = tr.get("action", "")
                action_it = _trade_cell(action.upper())
                action_it.setForeground(QColor(ACTION_BUY if action == "buy" else ACTION_SELL))
                right = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                trades_tbl.setItem(r, 0, _trade_cell(tr.get("date", "")))
                trades_tbl.setItem(r, 1, _trade_cell(ticker_name_map.get(ticker, "")))
                trades_tbl.setItem(r, 2, _trade_cell(ticker))
                trades_tbl.setItem(r, 3, action_it)
                trades_tbl.setItem(r, 4, _trade_cell(f"{tr.get('price', 0):,.0f}", right))
                trades_tbl.setItem(r, 5, _trade_cell(f"{tr.get('shares', 0):,.2f}", right))
                trades_tbl.setItem(r, 6, _trade_cell(f"{tr.get('gross_amount', 0):,.0f}", right))
                trades_tbl.setItem(r, 7, _trade_cell(f"{tr.get('fee', 0):,.0f}", right))
                trades_tbl.setItem(r, 8, _trade_cell(f"{tr.get('tax', 0):,.0f}", right))
                trades_tbl.setItem(r, 9, _trade_cell(f"{tr.get('net_amount', 0):,.0f}", right))
        finally:
            trades_tbl.setUpdatesEnabled(True)

        trades_layout.addWidget(trades_tbl, 1)

        disclaimer = QLabel(
            "\u26a0\ufe0f Backtest excludes trailing-PER factor (no bulk historical source). "
            "Includes 0.015% brokerage commission + 0.18% sell tax. Research tool, not investment advice \u2014 "
            "see rebalance.md section 6."
        )
        disclaimer.setStyleSheet(f"color:{TEXT_MUTED}; font-size:9pt;")
        disclaimer.setWordWrap(True)
        layout.addWidget(disclaimer)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn, alignment=Qt.AlignmentFlag.AlignRight)
