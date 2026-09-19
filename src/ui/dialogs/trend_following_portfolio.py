"""ui/dialogs/trend_following_portfolio.py — result windows for the v3 multi-instrument
trend-following portfolio (TrendFollowingPortfolioDialog) and its IS/OOS validation
(TrendFollowingValidationDialog). See trend_following.md 3 "v3", 5.

Display only: both take result dicts produced by strategy.trend_following.
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

from ui.common import _STATUS_SUCCESS_COLOR, _STATUS_FAIL_COLOR
from ui.colors import PROFIT, LOSS, FLAT, WARN
from ui.theme import ACCENT, TEXT_MUTED, TEXT_FAINT
from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas

logger = logging.getLogger(__name__)

_RIGHT = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter


def _cell(text, align=Qt.AlignmentFlag.AlignCenter, color=None):
    it = QTableWidgetItem(text)
    it.setTextAlignment(align)
    if color:
        it.setForeground(QColor(color))
    return it


def _signed_color(v):
    return PROFIT if v > 0 else LOSS if v < 0 else FLAT


def _metrics_html(label, m):
    gate = m.get("passes_risk_gate", False)
    return (f"<b>{label}</b> {m.get('start_date') or ''} → {m.get('end_date') or ''} ({m.get('n_days', 0)}d): "
            f"CAGR <b style='color:{_signed_color(m['cagr_pct'])}'>{m['cagr_pct']:+.1f}%</b> | "
            f"vol {m['annual_vol_pct']:.1f}% | Sharpe <b>{m['sharpe']:.2f}</b> | MDD <b>{m['max_drawdown_pct']:.1f}%</b> | "
            f"risk gate <b style='color:{_STATUS_SUCCESS_COLOR if gate else _STATUS_FAIL_COLOR}'>{'PASS' if gate else 'FAIL'}</b>")


def _table(cols, rows, resize=QHeaderView.ResizeMode.ResizeToContents):
    tbl = QTableWidget()
    tbl.setColumnCount(len(cols))
    tbl.setHorizontalHeaderLabels(cols)
    tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    tbl.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    tbl.horizontalHeader().setSectionResizeMode(resize)
    tbl.verticalHeader().setVisible(False)
    tbl.setUpdatesEnabled(False)
    try:
        tbl.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, item in enumerate(row):
                tbl.setItem(r, c, item)
    finally:
        tbl.setUpdatesEnabled(True)
    return tbl


def _equity_figure(dates, values, title, exposure=None, extra=None):
    """Equity curve (growth of 1) with an optional gross-exposure strip below."""
    x = mdates.date2num([datetime.strptime(d, "%Y-%m-%d") if isinstance(d, str) else datetime.combine(d, datetime.min.time())
                         for d in dates])
    if exposure is not None:
        fig = Figure(figsize=(10, 6.5), constrained_layout=True)
        ax, ax2 = fig.subplots(2, 1, sharex=True, gridspec_kw={"height_ratios": [3, 1]})
    else:
        fig = Figure(figsize=(10, 5), constrained_layout=True)
        ax = fig.add_subplot(111)
        ax2 = None
    base = values[0] if values and values[0] else 1.0
    ax.plot(x, [v / base for v in values], color=ACCENT, linewidth=1.8, label="Portfolio")
    if extra:
        for lbl, series, style in extra:
            ax.plot(x, series, color=TEXT_MUTED, linewidth=1.2, linestyle=style, label=lbl)
    ax.axhline(1.0, color=TEXT_FAINT, linewidth=0.8)
    ax.set_ylabel("Growth of 1")
    ax.set_title(title, fontsize=12, fontweight="bold")
    ax.grid(True, linestyle=":", alpha=0.5)
    ax.legend(fontsize=8, loc="upper left")
    if ax2 is not None:
        ax2.fill_between(x, 0, exposure, color=ACCENT, alpha=0.3, linewidth=0)
        ax2.set_ylabel("Gross exposure")
        ax2.set_ylim(0, max(1.0, max(exposure) if exposure else 1.0))
        ax2.grid(True, linestyle=":", alpha=0.5)
    (ax2 or ax).xaxis.set_major_formatter(mdates.DateFormatter("%y.%m"))
    fig.autofmt_xdate(rotation=25)
    return fig


class TrendFollowingPortfolioDialog(QDialog):

    def __init__(self, result: dict, parent=None):
        super().__init__(parent)
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowMaximizeButtonHint | Qt.WindowType.WindowMinimizeButtonHint)
        s = result["summary"]
        self.setWindowTitle(f"Trend Following Portfolio — {s.get('n_instruments', 0)} instruments (trend_following.md 3 v3)")
        self.resize(1000, 760)
        layout = QVBoxLayout(self)

        v2 = s.get("v2") or {}
        overlays = [f"regime MA{v2['regime_ma_n']}" if v2.get("regime_ma_n") else "",
                    f"stop {v2['stop_atr_mult']:g}×ATR" if v2.get("stop_atr_mult") else "",
                    f"vol target {v2['vol_target_pct']:g}%" if v2.get("vol_target_pct") else ""]
        overlays = ", ".join(o for o in overlays if o) or "v1"
        header = QLabel(
            _metrics_html("Portfolio", s)
            + f"<br>Donchian {s.get('entry_n')}/{s.get('exit_n')} + {overlays}, cost/side {s.get('cost_per_side', 0) * 100:.2f}% | "
              f"equal sleeves, avg gross exposure {s.get('avg_gross_exposure_pct', 0):.0f}%, "
              f"avg {s.get('avg_n_positions', 0):.1f} positions, {s.get('n_trades', 0)} trades"
            + (f" | skipped: {', '.join(s['skipped'])}" if s.get("skipped") else "")
        )
        header.setTextFormat(Qt.TextFormat.RichText)
        header.setWordWrap(True)
        layout.addWidget(header)

        tabs = QTabWidget()
        layout.addWidget(tabs, 1)

        daily = result.get("daily")
        if daily is not None and daily.height:
            chart = QWidget()
            cl = QVBoxLayout(chart)
            cl.setContentsMargins(0, 0, 0, 0)
            fig = _equity_figure(daily.get_column("Date").to_list(), daily.get_column("equity").to_list(),
                                 "Portfolio equity (equal sleeves)", exposure=daily.get_column("gross_exposure").to_list())
            cl.addWidget(FigureCanvas(fig), 1)
            tabs.addTab(chart, "Chart")

        inst = s.get("instruments") or []
        rows = []
        for i in sorted(inst, key=lambda r: -r["sharpe"]):
            rows.append([
                _cell(i["ticker"]),
                _cell(f"{i['cagr_pct']:+.1f}%", _RIGHT, _signed_color(i["cagr_pct"])),
                _cell(f"{i['sharpe']:.2f}", _RIGHT),
                _cell(f"{i['max_drawdown_pct']:.1f}%", _RIGHT),
                _cell(str(i["n_trades"]), _RIGHT),
                _cell(f"{i['exposure_pct']:.0f}%", _RIGHT),
            ])
        tabs.addTab(_table(["Ticker", "CAGR", "Sharpe", "MDD", "Trades", "Exposure"], rows),
                    f"Instruments ({len(inst)})")

        note = QLabel("⚠️ Constant-mix equal sleeves; inter-sleeve rebalancing cost not modelled. "
                      "Single in-sample period — use Validate (IS/OOS) for out-of-sample numbers. Research tool, not investment advice.")
        note.setStyleSheet(f"color:{TEXT_MUTED}; font-size:9pt;")
        note.setWordWrap(True)
        layout.addWidget(note)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn, alignment=Qt.AlignmentFlag.AlignRight)


class TrendFollowingValidationDialog(QDialog):

    def __init__(self, result: dict, parent=None):
        """result: {"walkforward": walk_forward_validation(), "holdout": holdout_validation(),
        "n_instruments": int, "tickers": [...]}"""
        super().__init__(parent)
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowMaximizeButtonHint | Qt.WindowType.WindowMinimizeButtonHint)
        wf = result.get("walkforward") or {}
        ho = result.get("holdout") or {}
        self.setWindowTitle(f"Trend Following IS/OOS Validation — {result.get('n_instruments', 0)} instruments (trend_following.md 5)")
        self.resize(1050, 780)
        layout = QVBoxLayout(self)

        parts = []
        if wf.get("n_folds"):
            parts.append(_metrics_html(f"Walk-forward OOS ({wf['n_folds']} yearly folds, stitched)", wf["oos"]))
        if ho.get("best_params"):
            corr = ho.get("is_oos_rank_corr")
            corr_str = "n/a" if corr is None else f"{corr:.2f}"
            parts.append(f"Holdout split {ho['split_date']}: best on IS = <b>{ho['best_label']}</b> "
                         f"(IS Sharpe {ho['is']['sharpe']:.2f} → OOS Sharpe {ho['oos']['sharpe']:.2f}, "
                         f"OOS MDD {ho['oos']['max_drawdown_pct']:.1f}%); IS→OOS Sharpe rank corr {corr_str}")
        header = QLabel("<br>".join(parts) or "No validation result.")
        header.setTextFormat(Qt.TextFormat.RichText)
        header.setWordWrap(True)
        layout.addWidget(header)

        tabs = QTabWidget()
        layout.addWidget(tabs, 1)

        folds = wf.get("folds") or []
        if folds:
            rows = []
            for f in folds:
                o = f["oos"]
                rows.append([
                    _cell(f["oos_start"][:4]),
                    _cell(f["best_label"], Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                    _cell(f"{f['is']['sharpe']:.2f}", _RIGHT),
                    _cell(f"{o['sharpe']:.2f}", _RIGHT, _STATUS_SUCCESS_COLOR if o["sharpe"] >= 1.5 else None),
                    _cell(f"{o['cagr_pct']:+.1f}%", _RIGHT, _signed_color(o["cagr_pct"])),
                    _cell(f"{o['max_drawdown_pct']:.1f}%", _RIGHT, WARN if o["max_drawdown_pct"] > 15 else None),
                ])
            tabs.addTab(_table(["OOS year", "Chosen on IS", "IS Sharpe", "OOS Sharpe", "OOS CAGR", "OOS MDD"], rows),
                        "Walk-forward folds")
            od = wf.get("oos_daily")
            if od is not None and od.height:
                chart = QWidget()
                cl = QVBoxLayout(chart)
                cl.setContentsMargins(0, 0, 0, 0)
                import numpy as np
                eq = list(np.cumprod(1.0 + od.get_column("portfolio_return").to_numpy()))
                cl.addWidget(FigureCanvas(_equity_figure(od.get_column("Date").to_list(), eq, "Stitched out-of-sample equity")), 1)
                tabs.addTab(chart, "OOS equity")

        grid = ho.get("grid") or []
        if grid:
            rows = []
            for r in sorted(grid, key=lambda r: -r["is"]["sharpe"]):
                rows.append([
                    _cell(r["label"], Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                    _cell(f"{r['is']['sharpe']:.2f}", _RIGHT),
                    _cell(f"{r['is']['max_drawdown_pct']:.1f}%", _RIGHT),
                    _cell(f"{r['oos']['sharpe']:.2f}", _RIGHT, _STATUS_SUCCESS_COLOR if r["oos"]["sharpe"] >= 1.5 else None),
                    _cell(f"{r['oos']['max_drawdown_pct']:.1f}%", _RIGHT),
                    _cell(f"{r['oos']['cagr_pct']:+.1f}%", _RIGHT, _signed_color(r["oos"]["cagr_pct"])),
                ])
            tabs.addTab(_table(["Params", "IS Sharpe", "IS MDD", "OOS Sharpe", "OOS MDD", "OOS CAGR"], rows),
                        f"Holdout grid ({len(grid)})")

        note = QLabel("⚠️ Signals use the full history (backward-looking only); only the evaluation window is cut. "
                      "Quote the stitched walk-forward OOS numbers. Research tool, not investment advice — trend_following.md section 5.")
        note.setStyleSheet(f"color:{TEXT_MUTED}; font-size:9pt;")
        note.setWordWrap(True)
        layout.addWidget(note)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn, alignment=Qt.AlignmentFlag.AlignRight)
