"""ui/dialogs/stock_ma.py — StockMaDialog — per-stock MA/RSI/divergence chart with
an investor-trend (or futures-curve) side table.

Split out of the former single ui/dialogs.py (2026-09-17); restructured from a
single ~900-line __init__ into build/plot/interaction methods on 2026-09-19 with
identical observable behaviour (see the characterisation notes in the commit).

Layout
------
::

    +-- splitter ------------------------------------------------------+
    | graph_widget                          | right panel (optional)   |
    |   [Y-Axis +/-]        (bp mode only)  |   investor / futures tbl |
    |   [EW][Div][Long Term][Closing Price] |   "Company Information"  |
    |   FigureCanvas: ax1 price + MAs       |                          |
    |                 ax2 volume + RSI      |                          |
    |                 ax3 MA divergence     |                          |
    |   horizontal scrollbar                |                          |
    +------------------------------------------------------------------+
    [Close]

Toggle buttons are mutually exclusive: checking one unchecks the others and
restores the plain MA view when unchecked.
"""
import logging

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QScrollBar,
    QTableWidget, QTableWidgetItem, QHeaderView, QSplitter, QWidget,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont

import numpy as np
import pandas as pd
import polars as pl
import matplotlib.dates as mdates
import mplcursors
from matplotlib.figure import Figure
from matplotlib.collections import PolyCollection
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas

from ui.common import create_font

logger = logging.getLogger(__name__)

_TOGGLE_BTN_STYLE = (
    "QPushButton { background:#2c3e50; color:#ecf0f1; border:1px solid #7f8c8d;"
    " border-radius:4px; padding:0 12px; font-weight:bold; }"
    "QPushButton:checked { background:#8e44ad; color:#fff; border:1px solid #9b59b6; }"
    "QPushButton:hover:!checked { background:#34495e; }"
)
_STOCK_MARKETS = ("KOSPI", "KOSDAQ", "NASDAQ 100", "S&P500")
_SIMPLE_CHART_TICKERS = ("CL=F", "^VIX", "VKOSPI")
_REQUIRED_MA_COLS = ("MA5", "MA10", "MA20", "MA50")
_COL_UP, _COL_DOWN = "#2ecc71", "#e74c3c"   # US-style candle/volume colours
_SCROLL_MAX = 10000


def _make_bars(x, y_bottom, y_top, w):
    """Vectorised (N, 4, 2) rectangle polygons for volume bars; NaN rows dropped."""
    x = np.asarray(x, dtype=float)
    y_bottom = np.asarray(y_bottom, dtype=float)
    y_top = np.asarray(y_top, dtype=float)
    valid = ~(np.isnan(y_bottom) | np.isnan(y_top))
    x, y_bottom, y_top = x[valid], y_bottom[valid], y_top[valid]
    if len(x) == 0:
        return []
    w2 = w / 2
    polys = np.empty((len(x), 4, 2))
    polys[:, 0] = np.stack([x - w2, y_bottom], axis=1)
    polys[:, 1] = np.stack([x - w2, y_top], axis=1)
    polys[:, 2] = np.stack([x + w2, y_top], axis=1)
    polys[:, 3] = np.stack([x + w2, y_bottom], axis=1)
    return polys


def _uncheck_silently(btn: QPushButton) -> None:
    btn.blockSignals(True)
    btn.setChecked(False)
    btn.blockSignals(False)


def _set_visible(*artists, visible: bool) -> None:
    for a in artists:
        if a is not None:
            a.set_visible(visible)


# ---------------------------------------------------------------------------
# StockMaDialog — Stock MA chart dialog (Close + MA20 + MA50)
# ---------------------------------------------------------------------------
class StockMaDialog(QDialog):
    """Dialog showing Close + 20-Day MA + 50-Day MA for a single stock."""

    def __init__(self, ticker, name, market, df, investor_data=None, parent=None, change_mode='pct'):
        super().__init__(parent)
        self._ticker = ticker
        self._name = name
        self._market = market
        self._df = df
        self._investor_data = investor_data or []
        self._change_mode = change_mode

        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowMaximizeButtonHint | Qt.WindowType.WindowMinimizeButtonHint)
        self.setWindowTitle(f"{name} ({ticker}) - 20 & 50-Day Moving Average")
        self.resize(1150, 600)
        self.showMaximized()

        layout = QVBoxLayout(self)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(splitter)

        graph_widget = QWidget()
        graph_v_layout = QVBoxLayout(graph_widget)
        graph_v_layout.setContentsMargins(0, 0, 0, 0)
        splitter.addWidget(graph_widget)

        self._build_toggle_buttons()

        is_stock = market in _STOCK_MARKETS
        if self._investor_data or is_stock:
            splitter.addWidget(self._build_right_panel(is_stock))
            splitter.setStretchFactor(0, 10)
            splitter.setStretchFactor(1, 3)

        # --- chart -----------------------------------------------------------
        self._fig = Figure(figsize=(9.5, 7.5), constrained_layout=True)
        self._is_simple_chart = ticker in _SIMPLE_CHART_TICKERS or change_mode == 'bp'
        if self._is_simple_chart:
            self._ax1, self._ax2 = self._fig.subplots(2, 1, sharex=True, gridspec_kw={'height_ratios': [3, 1]})
            self._ax3 = None
        else:
            self._ax1, self._ax2, self._ax3 = self._fig.subplots(
                3, 1, sharex=True, gridspec_kw={'height_ratios': [3, 1, 1]})

        # Artists the toggle buttons and hover handlers refer to; all optional.
        self._l_close = self._l_ma5 = self._l_ma10 = self._l_ma20 = self._l_ma50 = None
        self._ax_div_main = self._l_div20_main = self._l_div50_main = None
        self._ax_ew = self._l_ew = None
        self._ax_rsi = None
        self._cursors: list = []
        self._dates_arr = None
        self._pan_start = None
        self.scrollbar = None

        has_data = (
            df is not None and not df.is_empty()
            and all(c in df.columns for c in _REQUIRED_MA_COLS)
        )
        if has_data:
            self._plot()
        else:
            self._ax1.text(0.5, 0.5, "Failed to load detailed data.", ha="center", va="center",
                           transform=self._ax1.transAxes, color="gray", fontsize=12)

        if change_mode == 'bp':
            graph_v_layout.addLayout(self._build_y_zoom_row())

        graph_v_layout.addLayout(self._build_toggle_row())
        graph_v_layout.addWidget(FigureCanvas(self._fig))
        if self.scrollbar is not None:
            graph_v_layout.addWidget(self.scrollbar)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn, alignment=Qt.AlignmentFlag.AlignRight)

    # ------------------------------------------------------------------ widgets
    @staticmethod
    def _make_toggle_button(text: str) -> QPushButton:
        btn = QPushButton(text)
        btn.setCheckable(True)
        btn.setChecked(False)
        btn.setFixedHeight(28)
        btn.setStyleSheet(_TOGGLE_BTN_STYLE)
        return btn

    def _build_toggle_buttons(self) -> None:
        """The four mutually exclusive view toggles (inserted above the canvas later)."""
        self._ma_btn = self._make_toggle_button("Long Term")
        self._close_btn = self._make_toggle_button("Closing Price")
        self._div_btn = self._make_toggle_button("Div")
        self._ew_btn = self._make_toggle_button("Equal Weight")
        self._ew_btn.setVisible(False)   # shown only when the frame has an EqualWeight column

    def _build_toggle_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(0, 2, 6, 0)
        row.addStretch()
        row.addWidget(self._ew_btn)
        row.addWidget(self._div_btn)
        row.addWidget(self._ma_btn)
        row.addWidget(self._close_btn)
        return row

    def _build_right_panel(self, is_stock: bool) -> QWidget:
        """Investor-trend table (KR stocks), futures curve (WTI) and the
        'Company Information' link button."""
        panel = QWidget()
        v = QVBoxLayout(panel)
        v.setContentsMargins(0, 0, 0, 0)

        inv = self._investor_data
        is_wti_futures = bool(inv) and self._ticker == "CL=F" and 'Contract' in inv[0]
        if is_wti_futures:
            v.addWidget(self._section_label("Futures Price (Last 8 Months)"))
            v.addWidget(self._build_futures_table(inv))
        elif inv:
            v.addWidget(self._section_label(f"Supply & Demand Trend (Last {len(inv)} Days)"))
            v.addWidget(self._build_investor_table(inv))

        if is_stock:
            fin_btn = QPushButton("Company Information")
            fin_btn.setFont(create_font(10, QFont.Weight.Bold))
            fin_btn.setMinimumHeight(40)
            fin_btn.clicked.connect(self._open_company_page)
            v.addSpacing(10)
            v.addWidget(fin_btn)

        if not inv:
            v.addStretch()
        return panel

    @staticmethod
    def _section_label(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setFont(create_font(10, QFont.Weight.Bold))
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        return lbl

    @staticmethod
    def _new_side_table(headers: list) -> QTableWidget:
        table = QTableWidget()
        table.setFont(create_font(9))
        table.horizontalHeader().setFont(create_font(9, QFont.Weight.Bold))
        table.setColumnCount(len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        return table

    @staticmethod
    def _cell(text: str, align=Qt.AlignmentFlag.AlignCenter, color: str = None) -> QTableWidgetItem:
        it = QTableWidgetItem(text)
        it.setTextAlignment(align)
        if color:
            it.setForeground(QColor(color))
        return it

    def _build_futures_table(self, rows: list) -> QTableWidget:
        right = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        table = self._new_side_table(["Name", "Code", "Return"])
        table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            table.setItem(i, 0, self._cell(row['Contract']))
            table.setItem(i, 1, self._cell(row['Symbol']))
            table.setItem(i, 2, self._cell(f"${row.get('Close', 0):,.2f}", right))
        table.setMinimumWidth(260)
        for c in range(3):
            table.horizontalHeader().setSectionResizeMode(c, QHeaderView.ResizeMode.Stretch)
        return table

    def _build_investor_table(self, rows: list) -> QTableWidget:
        right = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        has_details = any(r.get('InvestmentTrust', 0) != 0 or r.get('PrivateEquity', 0) != 0 for r in rows)
        headers = ["Date", "Close", "Foreigner", "Institution", "Retail"]
        keys = ["Foreigner", "Institution", "Retail"]
        if has_details:
            headers.extend(["Inv.Trust", "PrivateEq."])
            keys.extend(["InvestmentTrust", "PrivateEquity"])

        table = self._new_side_table(headers)
        table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            table.setItem(i, 0, self._cell(row['Date']))
            table.setItem(i, 1, self._cell(f"{row.get('Close', 0):,}", right))
            for j, key in enumerate(keys, start=2):
                val = row.get(key, 0)
                color = "#d32f2f" if val > 0 else ("#1976d2" if val < 0 else None)   # slightly muted
                table.setItem(i, j, self._cell(f"{val:,}", right, color))
        table.setMinimumWidth(360 + (140 if has_details else 0))
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        for col in range(1, len(headers)):
            table.horizontalHeader().setSectionResizeMode(col, QHeaderView.ResizeMode.Stretch)
        return table

    def _open_company_page(self, *_):
        import webbrowser
        if self._market in ("KOSPI", "KOSDAQ"):
            url = f"https://finance.naver.com/item/main.naver?code={str(self._ticker).zfill(6)}"
        else:
            url = f"https://www.google.com/finance?q={str(self._ticker).replace('.', '-')}"
        webbrowser.open(url)

    def _build_y_zoom_row(self) -> QHBoxLayout:
        """'Y-Axis +/-' buttons (bond yields only; the X range is handled by scroll-zoom)."""
        row = QHBoxLayout()
        zoom_in_btn = QPushButton("Y-Axis +")
        zoom_out_btn = QPushButton("Y-Axis -")
        zoom_in_btn.setFixedSize(60, 25)
        zoom_out_btn.setFixedSize(60, 25)
        row.addStretch()
        row.addWidget(zoom_out_btn)
        row.addWidget(zoom_in_btn)
        row.addSpacing(20)
        zoom_in_btn.clicked.connect(self._zoom_y_in)
        zoom_out_btn.clicked.connect(self._zoom_y_out)
        return row

    # ----------------------------------------------------------------- plotting
    def _format_config(self) -> tuple:
        """(currency prefix, unit suffix, number format) for legends and tooltips."""
        if self._change_mode == 'bp':                 # bond yield in %
            return "", "%", "{:.2f}"
        if self._change_mode == 'abs' and self._market == 'Index':   # VIX or WTI
            return ("$" if self._ticker == 'CL=F' else ""), "", "{:,.2f}"
        if self._market == "Index":
            return "", "", "{:,.0f}"
        if self._market in ("KOSPI", "KOSDAQ"):
            return "KRW", "", "{:,.0f}"
        return "$", "", "{:,.2f}"

    def _last_valid(self, col: str) -> float:
        try:
            s = self._df.get_column(col).drop_nulls()
            return float(s[-1]) if len(s) > 0 else 0.0
        except Exception:
            return 0.0

    def _col(self, name: str):
        return self._df.get_column(name).to_numpy() if name in self._df.columns else None

    def _plot(self) -> None:
        df = self._df
        self._currency, self._unit_suffix, self._fmt_str = self._format_config()
        self._dates_arr = df.get_column("Date").to_numpy()

        self._plot_price_panel()
        self._plot_volume_panel()
        self._format_date_axes()
        self._plot_divergence_panel()
        self._install_hover()
        self._install_pan_zoom()

        self._ma_btn.toggled.connect(self._toggle_ma_only)
        self._div_btn.toggled.connect(self._toggle_div)
        self._close_btn.toggled.connect(self._toggle_close_only)
        self._ew_btn.toggled.connect(self._toggle_ew_only)

        # Default: start in Short-Term mode
        self._ma_btn.setChecked(False)
        self._toggle_ma_only(False)

    def _plot_price_panel(self) -> None:
        """ax1: Close + MA lines (or Close only for simple charts), hidden
        Div(20)/Div(50) twin axis and the optional Equal-Weight twin axis."""
        df, ax1, dates = self._df, self._ax1, self._dates_arr
        cur, suf, fmt = self._currency, self._unit_suffix, self._fmt_str

        def label(title, col):
            return f"{title} ({cur}{fmt.format(self._last_valid(col))}{suf})"

        if not self._is_simple_chart:
            def plot_line(col, color, lbl):
                line, = ax1.plot(dates, self._col(col), color=color, linewidth=1.5, linestyle="-",
                                 marker=".", markersize=3, label=lbl)
                return line

            self._l_close = plot_line("Close", "#2980b9", label("Price", "Close"))
            self._l_ma5 = plot_line("MA5", "#7f6000", label("5-Day MA", "MA5"))
            self._l_ma10 = plot_line("MA10", "#1abc9c", label("10-Day MA", "MA10"))
            self._l_ma20 = plot_line("MA20", "#f39c12", label("20-Day MA", "MA20"))
            self._l_ma50 = plot_line("MA50", "#9b59b6", label("50-Day MA", "MA50"))

            if "MA20_Div" in df.columns and "MA50_Div" in df.columns:
                ax_div = ax1.twinx()
                ax_div.set_ylabel("Divergence (%)", color="#34495e")
                ax_div.tick_params(axis='y', labelcolor="#34495e")
                self._l_div20_main, = ax_div.plot(dates, self._col("MA20_Div"), color="#e67e22", linewidth=1.5,
                                                  linestyle="--", label="Div(20)", visible=False)
                self._l_div50_main, = ax_div.plot(dates, self._col("MA50_Div"), color="#e74c3c", linewidth=1.5,
                                                  linestyle="--", label="Div(50)", visible=False)
                ax_div.set_visible(False)
                self._ax_div_main = ax_div
        else:
            self._l_close, = ax1.plot(dates, self._col("Close"), color="#2980b9", linewidth=2.0, linestyle="-",
                                      label=label("Price", "Close"))

        if "EqualWeight" in df.columns:
            self._ew_btn.setVisible(True)
            ax_ew = ax1.twinx()
            self._l_ew, = ax_ew.plot(dates, self._col("EqualWeight"), color="#34495e", linewidth=1.5,
                                     linestyle="--", marker=".", markersize=3, label="Equal Weight (252650)")
            ax_ew.set_ylabel("KODEX 200 EW", color="#34495e")
            ax_ew.tick_params(axis='y', labelcolor="#34495e")
            ax_ew.set_visible(False)
            self._l_ew.set_visible(False)
            self._ax_ew = ax_ew

            lines_1, labels_1 = ax1.get_legend_handles_labels()
            lines_ew, labels_ew = ax_ew.get_legend_handles_labels()
            ax1.legend(lines_1 + lines_ew, labels_1 + labels_ew, loc="upper left", fontsize=9)
        else:
            ax1.legend(loc="upper left", fontsize=9)
        ax1.grid(True, linestyle=":", alpha=0.5)
        ax1.set_title(self._name, fontsize=12, fontweight="bold")

    def _plot_volume_panel(self) -> None:
        """ax2: up/down volume bars plus an RSI14 overlay on a twin axis."""
        df, ax2, dates = self._df, self._ax2, self._dates_arr
        if "Volume" in df.columns:
            up = df.filter(pl.col("Close") >= pl.col("Open"))
            down = df.filter(pl.col("Close") < pl.col("Open"))
            width = 0.6
            up_x = mdates.date2num(up.get_column("Date").to_numpy())
            down_x = mdates.date2num(down.get_column("Date").to_numpy())
            ax2.add_collection(PolyCollection(
                _make_bars(up_x, [0] * len(up), up.get_column("Volume").to_numpy(), width),
                facecolors=_COL_UP, alpha=0.7))
            ax2.add_collection(PolyCollection(
                _make_bars(down_x, [0] * len(down), down.get_column("Volume").to_numpy(), width),
                facecolors=_COL_DOWN, alpha=0.7))
            max_vol = df.get_column("Volume").max()
            if max_vol and max_vol > 0:
                ax2.set_ylim(0, max_vol * 1.1)
        ax2.set_ylabel("Volume")
        ax2.grid(True, linestyle=":", alpha=0.4)

        if "RSI14" in df.columns:
            ax_rsi = ax2.twinx()
            rsi = self._col("RSI14")
            ax_rsi.plot(dates, rsi, color="#8e44ad", linewidth=1.5, alpha=0.8)
            ax_rsi.axhline(70, color="#e74c3c", linestyle=":", alpha=0.5)
            ax_rsi.axhline(30, color="#2ecc71", linestyle=":", alpha=0.5)
            ax_rsi.fill_between(dates, 70, 100, where=(rsi >= 70), facecolor='#e74c3c', alpha=0.1)
            ax_rsi.fill_between(dates, 0, 30, where=(rsi <= 30), facecolor='#2ecc71', alpha=0.1)
            ax_rsi.set_ylim(-10, 110)
            ax_rsi.set_yticks([30, 50, 70])
            ax_rsi.set_ylabel("RSI 14", color="#8e44ad")
            ax_rsi.tick_params(axis='y', labelcolor="#8e44ad")
            self._ax_rsi = ax_rsi

    def _format_date_axes(self) -> None:
        for ax in (self._ax2, self._ax3):
            if ax is not None:
                ax.xaxis.set_major_formatter(mdates.DateFormatter("%y.%m"))
                ax.xaxis.set_major_locator(mdates.MonthLocator(interval=1))
        self._fig.autofmt_xdate(rotation=25)

    def _plot_divergence_panel(self) -> None:
        """ax3: Div(50) and Div(20) (price / MA in %) over colour-coded tiers.
        Each line gets an invisible marker twin that mplcursors hovers on."""
        df, ax3, dates = self._df, self._ax3, self._dates_arr
        self._l_div50_hover = self._l_div20_hover = None
        if self._is_simple_chart or not ("MA50_Div" in df.columns or "MA20_Div" in df.columns):
            return

        all_divs = [self._col(c) for c in ("MA50_Div", "MA20_Div") if c in df.columns]
        combined_max = float(max(a.max() for a in all_divs))
        combined_min = float(min(a.min() for a in all_divs))

        # Colour-coded background bands (MA50 tiers as baseline) + 100% reference
        ax3.axhspan(130, max(combined_max * 1.05, 135), facecolor='#b41e1e', alpha=0.12)
        ax3.axhspan(110, 130, facecolor='#e74c3c', alpha=0.10)
        ax3.axhspan(107, 110, facecolor='#e08080', alpha=0.08)
        ax3.axhspan(98, 102, facecolor='#888888', alpha=0.06)
        ax3.axhspan(90, 98, facecolor='#5082d2', alpha=0.08)
        ax3.axhspan(min(combined_min * 0.98, 85), 90, facecolor='#1e1eb4', alpha=0.12)
        ax3.axhline(100, color='#555555', linewidth=1.0, linestyle='--', alpha=0.7)

        def plot_div(col, color, lbl):
            arr = self._col(col)
            ax3.plot(dates, arr, color=color, linewidth=1.6, marker='.', markersize=2, label=lbl, zorder=3)
            hover, = ax3.plot(dates, arr, color='none', marker='o', markersize=5, alpha=0, zorder=4)
            return hover

        if "MA50_Div" in df.columns:
            self._l_div50_hover = plot_div("MA50_Div", '#2980b9', 'Div(50)')
        if "MA20_Div" in df.columns:
            self._l_div20_hover = plot_div("MA20_Div", '#e67e22', 'Div(20)')

        ax3.legend(loc='upper left', fontsize=7, framealpha=0.7)
        ax3.yaxis.set_label_position('right')
        ax3.yaxis.tick_right()
        ax3.set_ylabel("Div(%)", fontsize=8)
        ax3.grid(True, linestyle=':', alpha=0.4)
        ax3.tick_params(axis='y', labelsize=7)

    # ------------------------------------------------------------------- hover
    def _install_hover(self) -> None:
        """mplcursors tooltips: OHLCV / single-series box on ax1, and a
        Price-MA-Div box on each ax3 divergence line."""
        close = self._col("Close")
        if self._l_div50_hover is not None:
            c = mplcursors.cursor([self._l_div50_hover], hover=2)
            c.connect("add", self._make_div_hover_handler(
                close, self._col("MA50_Div"), self._col("MA50"), "MA50", "Div(50)", "#2980b9"))
            self._cursors.append(c)
        if self._l_div20_hover is not None:
            c = mplcursors.cursor([self._l_div20_hover], hover=2)
            c.connect("add", self._make_div_hover_handler(
                close, self._col("MA20_Div"), self._col("MA20"), "MA20", "Div(20)", "#e67e22"))
            self._cursors.append(c)

        artists = [a for a in (self._l_close, self._l_ma5, self._l_ma10, self._l_ma20, self._l_ma50,
                               self._l_div20_main, self._l_div50_main, self._l_ew) if a is not None]
        self._main_cursor = mplcursors.cursor(artists, hover=2)
        self._main_cursor.connect("add", self._on_main_hover)
        self._cursors.insert(0, self._main_cursor)

        canvas = self._fig.canvas
        canvas.mpl_connect('axes_leave_event', self._on_leave_axes)
        canvas.mpl_connect('figure_leave_event', self._on_leave_axes)

    def _make_div_hover_handler(self, close, div_arr, ma_arr, ma_label, div_label, edge_color):
        dates, fmt, change_mode = self._dates_arr, self._fmt_str, self._change_mode

        def _handler(sel):
            try:
                idx = int(sel.index)
                if not (0 <= idx < len(dates)):
                    sel.annotation.set_visible(False)
                    return
                date_str = pd.to_datetime(dates[idx]).strftime("%Y-%m-%d")
                close_v = float(close[idx])
                div_v = float(div_arr[idx])
                ma_v = float(ma_arr[idx]) if ma_arr is not None else None
                if change_mode == 'bp':
                    price_str = f"{close_v:.2f}"
                    ma_str = f"{ma_v:.2f}" if ma_v is not None else "-"
                else:
                    price_str = fmt.format(close_v)
                    ma_str = fmt.format(ma_v) if ma_v is not None else "-"
                div_str = f"{div_v:.0f}%"

                w = max(len(price_str), len(ma_str), len(div_str), 10)
                box_w = w + 10
                txt = (f"{date_str:^{box_w}}\n"
                       f"  {'Price':<8}{price_str:>{w}}\n"
                       f"  {ma_label:<8}{ma_str:>{w}}\n"
                       f"  {div_label:<8}{div_str:>{w}}")
                sel.annotation.set_text(txt)
                sel.annotation.set_fontfamily("monospace")
                sel.annotation.set_fontsize(8.5)
                sel.annotation.get_bbox_patch().set(fc="white", alpha=0.93, edgecolor=edge_color,
                                                    boxstyle="round,pad=0.5")
                sel.annotation.arrow_patch.set(arrowstyle="->", color=edge_color)
            except Exception:
                logger.debug("MA divergence hover tooltip render failed", exc_info=True)
                sel.annotation.set_visible(False)
        return _handler

    def _on_main_hover(self, sel) -> None:
        try:
            idx = int(sel.index)
            if not (0 <= idx < self._df.height):
                return
            cur, suf, fmt = self._currency, self._unit_suffix, self._fmt_str
            if self._change_mode == 'bp':
                cur = ""
            date_str = pd.to_datetime(self._dates_arr[idx]).strftime("%Y-%m-%d")

            series = (
                (self._l_ma5, "MA5", "5-Day MA", False),
                (self._l_ma10, "MA10", "10-Day MA", False),
                (self._l_ma20, "MA20", "20-Day MA", False),
                (self._l_ma50, "MA50", "50-Day MA", False),
                (self._l_div20_main, "MA20_Div", "Div(20)", True),
                (self._l_div50_main, "MA50_Div", "Div(50)", True),
                (self._l_ew, "EqualWeight", "Equal Weight (252650)", False),
            )
            txt = None
            for artist, col, title, is_div in series:
                if artist is None or sel.artist != artist:
                    continue
                arr = self._col(col)
                if col == "EqualWeight" and (arr is None or arr[idx] != arr[idx]):
                    break             # NaN -> fall back to the OHLCV box
                v = float(arr[idx]) if arr is not None else 0.0
                if is_div:
                    txt = f"[{date_str}]\n{title}: {v:.1f}%"
                else:
                    txt = f"[{date_str}]\n{title}: {cur}{fmt.format(v)}{suf}"
                break

            if txt is None:
                def val(col):
                    arr = self._col(col)
                    return float(arr[idx]) if arr is not None else 0
                vol = self._col("Volume")
                v_str = f"{int(vol[idx]):,}" if (vol is not None and vol[idx] == vol[idx]) else "-"
                txt = (f"[{date_str}]\n"
                       f"Open:  {cur}{fmt.format(val('Open'))}{suf}\n"
                       f"High:  {cur}{fmt.format(val('High'))}{suf}\n"
                       f"Low:   {cur}{fmt.format(val('Low'))}{suf}\n"
                       f"Close: {cur}{fmt.format(val('Close'))}{suf}\n"
                       f"Vol:   {v_str}")

            sel.annotation.set_text(txt)
            sel.annotation.get_bbox_patch().set(fc="white", alpha=0.9, edgecolor="gray")
        except Exception:
            logger.debug("Main chart hover tooltip render failed", exc_info=True)
            sel.annotation.set_text("Data load error")

    def _on_leave_axes(self, _event) -> None:
        """Hide tooltips when the mouse leaves the axes/figure."""
        for c in self._cursors:
            if hasattr(c, 'selections'):
                for sel in list(c.selections):
                    c.remove_selection(sel)
        self._fig.canvas.draw_idle()

    # ---------------------------------------------------------------- pan/zoom
    def _install_pan_zoom(self) -> None:
        """Mouse-wheel zoom, left-drag pan and a horizontal scrollbar, all
        clamped to the data range (no right padding so future dates never appear)."""
        dates = self._dates_arr
        abs_min = mdates.date2num(dates[0])
        abs_max = mdates.date2num(dates[-1])
        self._x_lo = abs_min - (abs_max - abs_min) * 0.05
        self._x_hi = abs_max

        self.scrollbar = QScrollBar(Qt.Orientation.Horizontal)
        self.scrollbar.valueChanged.connect(self._on_scrollbar_change)

        canvas = self._fig.canvas
        canvas.mpl_connect('scroll_event', self._on_scroll)
        canvas.mpl_connect('button_press_event', self._on_press)
        canvas.mpl_connect('motion_notify_event', self._on_motion)
        canvas.mpl_connect('button_release_event', self._on_release)

        # Show only the last 1 year initially; right bound clamped to last data date
        end_date = pd.to_datetime(dates[-1])
        start_date = end_date - pd.DateOffset(years=1)
        self._ax1.set_xlim(mdates.date2num(start_date), abs_max)
        self._update_scrollbar()

    def _chart_axes(self) -> list:
        return [ax for ax in (self._ax1, self._ax2, self._ax3, self._ax_rsi) if ax is not None]

    def _clamp_xlim(self, new_xmin: float, new_xmax: float) -> tuple:
        w = new_xmax - new_xmin
        if w > (self._x_hi - self._x_lo):
            return self._x_lo, self._x_hi
        if new_xmin < self._x_lo:
            new_xmin = self._x_lo
            new_xmax = new_xmin + w
        if new_xmax > self._x_hi:
            new_xmax = self._x_hi
            new_xmin = new_xmax - w
        return new_xmin, new_xmax

    def _update_scrollbar(self) -> None:
        x_min, x_max = self._ax1.get_xlim()
        denom = (self._x_hi - self._x_lo) - (x_max - x_min)
        if denom <= 0:
            val = 0
        else:
            val = int((x_min - self._x_lo) / denom * _SCROLL_MAX)
            val = max(0, min(_SCROLL_MAX, val))
        self.scrollbar.blockSignals(True)
        self.scrollbar.setRange(0, _SCROLL_MAX)
        self.scrollbar.setValue(val)
        self.scrollbar.blockSignals(False)

    def _on_scrollbar_change(self, val: int) -> None:
        x_min, x_max = self._ax1.get_xlim()
        w = x_max - x_min
        new_xmin = self._x_lo + (val / _SCROLL_MAX) * ((self._x_hi - self._x_lo) - w)
        self._ax1.set_xlim(new_xmin, new_xmin + w)
        self._fig.canvas.draw_idle()

    def _on_scroll(self, event) -> None:
        if event.inaxes not in self._chart_axes() or event.xdata is None:
            return
        scale = 1.15
        if event.button == 'up':
            factor = 1 / scale        # zoom in
        elif event.button == 'down':
            factor = scale            # zoom out
        else:
            return
        x_min, x_max = self._ax1.get_xlim()
        x_focus = event.xdata
        new_xmin = x_focus - (x_focus - x_min) * factor
        new_xmax = x_focus + (x_max - x_focus) * factor
        self._ax1.set_xlim(*self._clamp_xlim(new_xmin, new_xmax))
        self._update_scrollbar()
        self._fig.canvas.draw_idle()

    def _on_press(self, event) -> None:
        if event.button != 1 or event.inaxes not in self._chart_axes():
            return
        self._pan_start = (event.x, self._ax1.get_xlim())
        self._main_cursor.enabled = False

    def _on_motion(self, event) -> None:
        if self._pan_start is None:
            return
        xpress, xlim = self._pan_start
        bbox = self._ax1.get_window_extent()
        if bbox.width == 0:
            return
        dx_data = (event.x - xpress) * ((xlim[1] - xlim[0]) / bbox.width)
        self._ax1.set_xlim(*self._clamp_xlim(xlim[0] - dx_data, xlim[1] - dx_data))
        self._update_scrollbar()
        self._fig.canvas.draw_idle()

    def _on_release(self, _event) -> None:
        self._pan_start = None
        self._main_cursor.enabled = True
        self._fig.canvas.draw_idle()

    def _zoom_y_in(self, *_):
        self._zoom_y(0.8)

    def _zoom_y_out(self, *_):
        self._zoom_y(1.2)

    def _zoom_y(self, factor: float) -> None:
        """Scale the ax1 Y range around the midpoint of the visible closes."""
        ax1 = self._ax1
        xmin, xmax = ax1.get_xlim()
        ymin, ymax = ax1.get_ylim()
        try:
            x_data = mdates.date2num(self._dates_arr)
            mask = (x_data >= xmin) & (x_data <= xmax)
            y_visible = [float(y) for m, y in zip(mask, self._df.get_column("Close").to_list())
                         if m and y is not None]
            y_visible = [y for y in y_visible if not np.isnan(y)]
            ymid = (min(y_visible) + max(y_visible)) / 2 if y_visible else (ymin + ymax) / 2
        except Exception:
            ymid = (ymin + ymax) / 2
        ydiff = (ymax - ymin) / 2
        ax1.set_ylim(ymid - ydiff * factor, ymid + ydiff * factor)
        self._fig.canvas.draw_idle()

    # ----------------------------------------------------------------- toggles
    def _set_div_visible(self, visible: bool) -> None:
        if self._ax_div_main is not None:
            _set_visible(self._ax_div_main, self._l_div20_main, self._l_div50_main, visible=visible)

    def _set_ew_visible(self, visible: bool) -> None:
        if self._ax_ew is not None:
            _set_visible(self._ax_ew, self._l_ew, visible=visible)

    def _hide_all_mas(self) -> None:
        _set_visible(self._l_ma5, self._l_ma10, self._l_ma20, self._l_ma50, visible=False)

    def _release_div_button(self) -> None:
        if self._div_btn.isChecked():
            _uncheck_silently(self._div_btn)
            self._set_div_visible(False)

    def _release_ew_button(self) -> None:
        if self._ew_btn.isChecked():
            _uncheck_silently(self._ew_btn)
            self._set_ew_visible(False)

    def _release_close_button(self) -> None:
        if self._close_btn.isChecked():
            _uncheck_silently(self._close_btn)

    def _release_ma_button(self) -> None:
        if self._ma_btn.isChecked():
            _uncheck_silently(self._ma_btn)

    def _toggle_ma_only(self, checked: bool) -> None:
        """Short Term (MA5/MA10) when unchecked, Long Term (MA20/MA50) when checked.
        Also the 'plain' view the other toggles fall back to."""
        self._release_div_button()
        self._release_close_button()
        self._release_ew_button()
        _set_visible(self._l_ma5, self._l_ma10, visible=not checked)
        _set_visible(self._l_ma20, self._l_ma50, visible=checked)
        self._fig.canvas.draw_idle()

    def _toggle_div(self, checked: bool) -> None:
        if checked:
            self._release_close_button()
            self._release_ew_button()
            self._hide_all_mas()
            self._set_div_visible(True)
        else:
            self._set_div_visible(False)
            self._toggle_ma_only(self._ma_btn.isChecked())
        self._fig.canvas.draw_idle()

    def _toggle_close_only(self, checked: bool) -> None:
        if checked:
            self._release_ma_button()
            self._release_div_button()
            self._release_ew_button()
            self._hide_all_mas()
        else:
            self._toggle_ma_only(self._ma_btn.isChecked())
        self._fig.canvas.draw_idle()

    def _toggle_ew_only(self, checked: bool) -> None:
        if checked:
            self._release_ma_button()
            self._release_div_button()
            self._release_close_button()
            self._hide_all_mas()
            self._set_ew_visible(True)
        else:
            self._set_ew_visible(False)
            self._toggle_ma_only(self._ma_btn.isChecked())
        self._fig.canvas.draw_idle()

    # --------------------------------------------------------------- lifecycle
    def closeEvent(self, event):
        """Release the matplotlib figure from memory when the dialog is closed."""
        import matplotlib.pyplot as plt
        try:
            plt.close("all")
        except Exception:
            logger.debug("plt.close('all') failed on dialog close", exc_info=True)
        super().closeEvent(event)
