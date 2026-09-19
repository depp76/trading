"""ui/dialogs/index_ma.py — IndexMaDialog — 2x2 chart of Close/MA20/MA50 for the major indices.

Split out of the former single ui/dialogs.py (2026-09-17)."""
import logging

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QPushButton,
)
from PyQt6.QtCore import Qt

import matplotlib.dates as mdates
import mplcursors
from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas

logger = logging.getLogger(__name__)

from data_fetcher import INDEX_TICKERS
from ui.colors import MA_RAMP
from ui.theme import TEXT, TEXT_MUTED


# ---------------------------------------------------------------------------
# IndexMaDialog — Index MA20 chart dialog (2x2 subplots)
# ---------------------------------------------------------------------------
class IndexMaDialog(QDialog):
    """2x2 subplot dialog showing Close, 20-day MA, and 50-day MA for each major index."""

    def __init__(self, results, parent=None):
        """
        results: dict {label: (DataFrame | None, error_str)}
        """
        super().__init__(parent)
        self.setWindowTitle("Major Index - 20 & 50-Day Moving Average")
        self.resize(1100, 620)

        layout = QVBoxLayout(self)

        fig = Figure(figsize=(15, 6), constrained_layout=True)
        labels = [lbl for lbl in INDEX_TICKERS.keys() if lbl not in ("Dow Jones", "US10YT", "JP10YT", "KR3YT", "VIX", "VKOSPI", "WTI")]   # KOSPI, KOSDAQ, S&P500, NASDAQ, NASDAQ 100

        cols = 3 if len(labels) > 4 else 2
        for idx, label in enumerate(labels):
            ax = fig.add_subplot(2, cols, idx + 1)
            df, err = results.get(label, (None, "No data"))

            if df is not None and not df.is_empty() and "MA10" in df.columns and "MA20" in df.columns and "MA50" in df.columns:
                dates = df.get_column("Date").to_numpy()
                # Same vocabulary as StockMaDialog: ink close, one accent ramp
                # for the MAs (shorter = darker), no per-point markers.
                line_close, = ax.plot(dates, df.get_column("Close").to_numpy(), color=TEXT,
                        linewidth=1.5, label="Close")
                line_ma10, = ax.plot(dates, df.get_column("MA10").to_numpy(), color=MA_RAMP["MA10"],
                        linewidth=1.3, label="10-Day MA")
                line_ma20, = ax.plot(dates, df.get_column("MA20").to_numpy(), color=MA_RAMP["MA20"],
                        linewidth=1.3, label="20-Day MA")
                line_ma50, = ax.plot(dates, df.get_column("MA50").to_numpy(), color=MA_RAMP["MA50"],
                        linewidth=1.3, label="50-Day MA")
                ax.xaxis.set_major_formatter(mdates.DateFormatter("%y.%m"))
                ax.xaxis.set_major_locator(mdates.MonthLocator(interval=1))
                fig.autofmt_xdate(rotation=25)
                ax.legend(fontsize=8)
                ax.grid(True, linestyle=":", alpha=0.5)

                cursor = mplcursors.cursor([line_close, line_ma10, line_ma20, line_ma50], hover=2)
                @cursor.connect("add")
                def on_add(sel):
                    date_str = mdates.num2date(sel.target[0]).strftime("%Y-%m-%d")
                    val = sel.target[1]
                    lbl = sel.artist.get_label()
                    sel.annotation.set_text(f"{lbl}\n{date_str}: {val:,.0f}")
                    sel.annotation.get_bbox_patch().set(fc="white", alpha=0.9, edgecolor="gray")
            else:
                ax.text(0.5, 0.5, f"Failed to load\n{err or ''}",
                        ha="center", va="center", transform=ax.transAxes,
                        color=TEXT_MUTED, fontsize=10)

            ax.set_title(label, fontsize=11, fontweight="bold")

        canvas = FigureCanvas(fig)
        layout.addWidget(canvas)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn, alignment=Qt.AlignmentFlag.AlignRight)
