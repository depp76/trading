"""ui/dialogs/assets_graph.py — TotalAssetsGraphDialog — cumulative return graph for the Total Assets tab.

Split out of the former single ui/dialogs.py (2026-09-17)."""
import logging

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QPushButton,
)
from PyQt6.QtCore import Qt

import pandas as pd
import matplotlib.dates as mdates
import mplcursors
from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas

from ui.theme import ACCENT, TEXT, TEXT_MUTED, TEXT_FAINT

logger = logging.getLogger(__name__)



# ---------------------------------------------------------------------------
# TotalAssetsGraphDialog — Cumulative Returns graph
# ---------------------------------------------------------------------------
class TotalAssetsGraphDialog(QDialog):
    def __init__(self, dates, kospi_returns, asset_returns, usd_asset_returns, totals, usd_totals, parent=None):
        super().__init__(parent)
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowMaximizeButtonHint | Qt.WindowType.WindowMinimizeButtonHint)
        self.setWindowTitle("Total Assets Cumulative Returns")
        self.resize(900, 600)
        self.showMaximized()

        layout = QVBoxLayout(self)

        fig = Figure(figsize=(9, 6), constrained_layout=True)
        ax = fig.add_subplot(111)

        dates_dt = [pd.to_datetime(d) for d in dates]
        x_dates = mdates.date2num(dates_dt)

        # Series identity, not price direction: ink for the main series,
        # accent for its USD view, muted dashed for the benchmark (red/blue
        # stay reserved for gain/loss, docs/ui.md 1.1).
        line_kospi, = ax.plot(x_dates, kospi_returns, color=TEXT_MUTED, linewidth=1.6, linestyle="--", marker=".", markersize=4, label="KOSPI")
        line_asset, = ax.plot(x_dates, asset_returns, color=TEXT, linewidth=2, linestyle="-", marker=".", markersize=4, label="Total Assets")
        line_usd, = ax.plot(x_dates, usd_asset_returns, color=ACCENT, linewidth=2, linestyle="-", marker=".", markersize=4, label="Total Assets ($)")

        ax.axhline(0, color=TEXT_FAINT, linestyle='--', linewidth=1)

        ax.set_xticks(x_dates)
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%y.%m.%d"))
        fig.autofmt_xdate(rotation=45)
        ax.margins(x=0)

        ax.legend(fontsize=10)
        ax.grid(True, linestyle=":", alpha=0.5)
        ax.set_ylabel("Cumulative Return (%)", fontsize=10)
        ax.set_title("Total Assets vs KOSPI Cumulative Return", fontsize=12, fontweight="bold")

        # Annotate actual values on the markers
        for i in range(len(dates)):
            # Total Assets (KRW)
            amt_krw = totals[i]
            ret_krw = asset_returns[i]
            krw_str = f"{amt_krw:,.0f}"
            if amt_krw >= 1000000:
                krw_str = f"{amt_krw/1000000:.1f}M"
            elif amt_krw >= 1000:
                krw_str = f"{amt_krw/1000:.0f}K"

            ax.annotate(f"{ret_krw:+.1f}%\n({krw_str})", (x_dates[i], ret_krw),
                        textcoords="offset points", xytext=(0, 10), ha='center',
                        fontsize=8, color=TEXT, fontweight="bold")

            # Total Assets (USD)
            amt_usd = usd_totals[i]
            ret_usd = usd_asset_returns[i]
            ax.annotate(f"{ret_usd:+.1f}%\n(${amt_usd:,.0f})", (x_dates[i], ret_usd),
                        textcoords="offset points", xytext=(0, -25), ha='center',
                        fontsize=8, color=ACCENT, fontweight="bold")

        # Create invisible scatter points to force cursor to snap only to actual data points
        sc_kospi = ax.scatter(x_dates, kospi_returns, alpha=0)
        sc_asset = ax.scatter(x_dates, asset_returns, alpha=0)
        sc_usd = ax.scatter(x_dates, usd_asset_returns, alpha=0)

        cursor = mplcursors.cursor([sc_kospi, sc_asset, sc_usd], hover=2)
        @cursor.connect("add")
        def on_add(sel):
            idx = int(sel.index)
            if 0 <= idx < len(dates):
                date_str = dates[idx]
                val = sel.target[1]

                if sel.artist == sc_kospi:
                    lbl = "KOSPI"
                    val_str = f"{val:+.1f}%"
                elif sel.artist == sc_asset:
                    lbl = "Total Assets"
                    amt = totals[idx]
                    val_str = f"{val:+.1f}%\nValue: {amt:,.0f} KRW"
                elif sel.artist == sc_usd:
                    lbl = "Total Assets ($)"
                    amt = usd_totals[idx]
                    val_str = f"{val:+.1f}%\nValue: ${amt:,.0f}"
                else:
                    lbl = ""
                    val_str = f"{val:+.1f}%"

                sel.annotation.set_text(f"{lbl}\n{date_str}: {val_str}")
                sel.annotation.get_bbox_patch().set(fc="white", alpha=0.9, edgecolor="gray")

        canvas = FigureCanvas(fig)
        layout.addWidget(canvas)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn, alignment=Qt.AlignmentFlag.AlignRight)
