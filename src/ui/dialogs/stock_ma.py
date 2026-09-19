"""ui/dialogs/stock_ma.py — StockMaDialog — per-stock price/MA chart with
volume, MA-divergence and RSI panels and an investor-trend (or futures-curve)
side table.

Split out of the former single ui/dialogs.py (2026-09-17); restructured into
build/plot/interaction methods on 2026-09-19; redesigned the same day per the
docs/ui.md "MA Chart Redesign" mockup (12 issues, phases A-D):

  A  colors: volume/RSI use the app-wide PROFIT/LOSS rule (they were the
     only US-style green-up/red-down in the app); the four MAs are one accent
     ramp (ui.colors.MA_RAMP, darkest = shortest window) instead of five hues;
     Close is neutral ink.
  B  no legend: each line carries a value label at its right edge
     (_place_end_labels, pushed apart to avoid overlap); no per-point markers.
  C  no twin axes: RSI is its own panel, the duplicate Div lines on the price
     axis are gone (the divergence panel is the one place), Equal Weight is
     rebased onto the price axis; divergence bands are three (overheated /
     neutral / depressed) around ui.colors.MA_DIV_NEUTRAL_PCT, the same
     constant the Universe table reads, with 100 centred.
  D  an exclusive view segment (Short / Long / Close only / Divergence [/
     Equal Weight]) replaces four look-alike independent toggles with a hidden
     "nothing pressed" default; period buttons (1M..3Y/All); a crosshair shared
     by every panel with a fixed readout row instead of hover tooltips.

Layout
------
::

    +-- splitter ------------------------------------------------------+
    | name  ticker · market  price  chg%   [1M 3M 6M 1Y 3Y All] as-of  |
    | VIEW [Short Long Close Div]  note    [Volume] [Divergence] [RSI] |
    | 2026-08-21  ■ Close 1,849,000  ■ MA5 ...  ■ Vol 4.2M  ■ Div(20) |
    |   FigureCanvas: price (+MA ramp)      | right panel (optional)   |
    |                 volume                |   investor / futures tbl |
    |                 divergence            |   "Company Information"  |
    |                 rsi                   |                          |
    |   horizontal scrollbar                |                          |
    +------------------------------------------------------------------+
    [Close]

Simple charts (bond yields, VIX, WTI) keep only the price panel and the
period buttons; bonds also get the Y-axis +/- buttons.
"""
import logging

import numpy as np
import pandas as pd
import polars as pl
import matplotlib.dates as mdates
from matplotlib.figure import Figure
from matplotlib.collections import PolyCollection
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QScrollBar, QButtonGroup,
    QTableWidget, QTableWidgetItem, QHeaderView, QSplitter, QWidget, QFrame,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont

from ui.common import create_font, FONT_TITLE, FONT_BODY, FONT_SMALL, FONT_CAPTION
from ui.colors import PROFIT, LOSS, MA_RAMP, MA_DIV_NEUTRAL_PCT
from ui.theme import ACCENT, TEXT, TEXT_MUTED, TEXT_FAINT, LINE, LINE_SOFT, GRP_BG, SURFACE

logger = logging.getLogger(__name__)

_STOCK_MARKETS = ("KOSPI", "KOSDAQ", "NASDAQ 100", "S&P500")
_SIMPLE_CHART_TICKERS = ("CL=F", "^VIX", "VKOSPI")
_SCROLL_MAX = 10000
_TICK_PT = FONT_SMALL          # issue #12: nothing on the chart below 9pt

# Segment buttons share one look: a checkable neutral button; the checked one
# takes the theme's QPushButton:checked accent.
_SEG_H = 26

_PERIODS = [("1M", 1), ("3M", 3), ("6M", 6), ("1Y", 12), ("3Y", 36), ("All", None)]
_DEFAULT_PERIOD = "1Y"

# view key -> (label, MA windows shown, note)
_VIEWS = {
    "short": ("Short", ("MA5", "MA10", "MA20"), "MA5 · MA10 · MA20"),
    "long":  ("Long", ("MA20", "MA50"), "MA20 · MA50"),
    "close": ("Close only", (), "Moving averages hidden"),
    "div":   ("Divergence", ("MA5", "MA10", "MA20"), "Divergence panel enlarged"),
    "ew":    ("Equal Weight", (), "KODEX 200 EW rebased to this price"),
}


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


def _compact(v: float) -> str:
    """1,849,000 -> '1.85M', 71,200 -> '71.2k' -- for the narrow end labels."""
    a = abs(v)
    if a >= 1e6:
        return f"{v / 1e6:.2f}M"
    if a >= 1e3:
        return f"{v / 1e3:.1f}k"
    return f"{v:,.2f}" if a < 100 else f"{v:,.0f}"


def _segment_button(text: str, group: QButtonGroup = None, checked: bool = False, width: int = None) -> QPushButton:
    btn = QPushButton(text)
    btn.setCheckable(True)
    btn.setChecked(checked)
    btn.setFixedHeight(_SEG_H)
    btn.setFont(create_font(FONT_SMALL, style_name="Semilight"))
    if width:
        btn.setFixedWidth(width)
    if group is not None:
        group.addButton(btn)
    return btn


class StockMaDialog(QDialog):
    """Price + moving averages for one instrument, with volume, divergence and
    RSI panels (docs/ui.md MA Chart Redesign)."""

    def __init__(self, ticker, name, market, df, investor_data=None, parent=None, change_mode='pct'):
        super().__init__(parent)
        self._ticker = ticker
        self._name = name
        self._market = market
        self._df = df
        self._investor_data = investor_data or []
        self._change_mode = change_mode

        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowMaximizeButtonHint | Qt.WindowType.WindowMinimizeButtonHint)
        self.setWindowTitle(f"{name} ({ticker}) — Moving Averages")
        self.resize(1200, 720)
        self.showMaximized()

        self._is_simple_chart = ticker in _SIMPLE_CHART_TICKERS or change_mode == 'bp'
        self._has_data = (
            df is not None and not df.is_empty() and "Close" in df.columns
            and (self._is_simple_chart or all(c in df.columns for c in MA_RAMP))
        )
        self._currency, self._unit_suffix, self._fmt_str = self._format_config()

        # Artists / state the interaction handlers refer to.
        self._lines = {}          # "Close" | "MA5" | ... | "EW" -> Line2D
        self._end_labels = {}     # same keys -> Annotation
        self._crosshairs = []     # one axvline per panel
        self._panel_axes = {}     # "price" | "vol" | "div" | "rsi" -> Axes
        self._panel_on = {"vol": False, "div": False, "rsi": False}
        self._view = "short"
        self._x = None
        self._pan_start = None
        self._cursor_idx = None
        self.scrollbar = None

        root = QVBoxLayout(self)
        root.setSpacing(6)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(splitter)

        chart_widget = QWidget()
        chart_v = QVBoxLayout(chart_widget)
        chart_v.setContentsMargins(0, 0, 0, 0)
        chart_v.setSpacing(4)
        splitter.addWidget(chart_widget)

        is_stock = market in _STOCK_MARKETS
        if self._investor_data or is_stock:
            splitter.addWidget(self._build_right_panel(is_stock))
            splitter.setStretchFactor(0, 10)
            splitter.setStretchFactor(1, 3)

        chart_v.addLayout(self._build_header_row())
        chart_v.addLayout(self._build_view_row())
        chart_v.addWidget(self._build_readout_row())

        self._fig = Figure(figsize=(9.5, 7.5))
        self._canvas = FigureCanvas(self._fig)
        if self._has_data:
            self._plot()
        else:
            ax = self._fig.add_subplot(111)
            ax.text(0.5, 0.5, "Failed to load detailed data.", ha="center", va="center",
                    transform=ax.transAxes, color=TEXT_MUTED, fontsize=FONT_BODY)
            ax.set_axis_off()

        if change_mode == 'bp':
            chart_v.addLayout(self._build_y_zoom_row())
        chart_v.addWidget(self._canvas, 1)
        if self.scrollbar is not None:
            chart_v.addWidget(self.scrollbar)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        root.addWidget(close_btn, alignment=Qt.AlignmentFlag.AlignRight)

    # ------------------------------------------------------------- top rows
    def _build_header_row(self) -> QHBoxLayout:
        """Name · ticker/market · last price · change% | period segment | as-of."""
        row = QHBoxLayout()
        row.setSpacing(10)

        name_lbl = QLabel(self._name)
        name_lbl.setFont(create_font(FONT_TITLE - 2, QFont.Weight.Bold))
        row.addWidget(name_lbl)

        meta_lbl = QLabel(f"{self._ticker} · {self._market}")
        meta_lbl.setFont(create_font(FONT_SMALL, style_name="Semilight"))
        meta_lbl.setStyleSheet(f"color:{TEXT_FAINT};")
        row.addWidget(meta_lbl)

        last, chg = self._last_close_and_change()
        self._price_lbl = QLabel(self._fmt_price(last) if last is not None else "—")
        self._price_lbl.setFont(create_font(FONT_TITLE - 2, QFont.Weight.Bold))
        row.addWidget(self._price_lbl)
        self._chg_lbl = QLabel(self._fmt_change(chg) if chg is not None else "")
        self._chg_lbl.setFont(create_font(FONT_BODY, style_name="Semilight"))
        if chg is not None:
            self._chg_lbl.setStyleSheet(f"color:{PROFIT if chg > 0 else LOSS if chg < 0 else TEXT_MUTED};")
        row.addWidget(self._chg_lbl)
        row.addStretch()

        self._period_group = QButtonGroup(self)
        self._period_group.setExclusive(True)
        self._period_buttons = {}
        for label, months in _PERIODS:
            btn = _segment_button(label, self._period_group, checked=(label == _DEFAULT_PERIOD), width=44)
            btn.clicked.connect(lambda _c, m=months, lbl=label: self._on_period_clicked(lbl, m))
            row.addWidget(btn)
            self._period_buttons[label] = btn

        as_of = ""
        if self._has_data:
            as_of = f"as of {pd.to_datetime(self._df.get_column('Date')[-1]).strftime('%Y-%m-%d')}"
        as_of_lbl = QLabel(as_of)
        as_of_lbl.setFont(create_font(FONT_CAPTION, style_name="Semilight"))
        as_of_lbl.setStyleSheet(f"color:{TEXT_FAINT};")
        row.addSpacing(6)
        row.addWidget(as_of_lbl)
        return row

    def _build_view_row(self) -> QHBoxLayout:
        """VIEW segment (exclusive) + note | panel toggles (independent)."""
        row = QHBoxLayout()
        row.setSpacing(6)
        cap = QLabel("VIEW")
        cap.setFont(create_font(FONT_CAPTION, QFont.Weight.Bold))
        cap.setStyleSheet(f"color:{TEXT_FAINT};")
        row.addWidget(cap)

        self._view_group = QButtonGroup(self)
        self._view_group.setExclusive(True)
        self._view_buttons = {}
        keys = [] if self._is_simple_chart else ["short", "long", "close", "div"]
        if self._has_data and "EqualWeight" in self._df.columns and not self._is_simple_chart:
            keys.append("ew")
        for key in keys:
            btn = _segment_button(_VIEWS[key][0], self._view_group, checked=(key == "short"))
            btn.clicked.connect(lambda _c, k=key: self._set_view(k))
            row.addWidget(btn)
            self._view_buttons[key] = btn

        self._view_note = QLabel(_VIEWS["short"][2] if keys else "")
        self._view_note.setFont(create_font(FONT_SMALL, style_name="Semilight"))
        self._view_note.setStyleSheet(f"color:{TEXT_MUTED};")
        row.addSpacing(4)
        row.addWidget(self._view_note)
        row.addStretch()

        self._panel_buttons = {}
        for key, label in (("vol", "Volume"), ("div", "Divergence"), ("rsi", "RSI")):
            btn = _segment_button(label)
            btn.clicked.connect(lambda checked, k=key: self._set_panel(k, checked))
            row.addWidget(btn)
            self._panel_buttons[key] = btn
        return row

    def _build_readout_row(self) -> QFrame:
        """Fixed crosshair readout (issue #9): the date plus every visible
        series' value at the cursor, or the latest bar when the cursor is
        outside the chart."""
        frame = QFrame()
        frame.setStyleSheet(f"QFrame {{ background:{GRP_BG}; border:1px solid {LINE_SOFT}; border-radius:6px; }}")
        row = QHBoxLayout(frame)
        row.setContentsMargins(10, 4, 10, 4)
        self._readout_lbl = QLabel("")
        self._readout_lbl.setFont(create_font(FONT_SMALL, style_name="Semilight"))
        self._readout_lbl.setTextFormat(Qt.TextFormat.RichText)
        row.addWidget(self._readout_lbl, 1)
        hint = QLabel("Crosshair spans every panel")
        hint.setFont(create_font(FONT_CAPTION, style_name="Semilight"))
        hint.setStyleSheet(f"color:{TEXT_FAINT};")
        row.addWidget(hint)
        return frame

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
            fin_btn.setFont(create_font(FONT_BODY, QFont.Weight.Bold))
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
        lbl.setFont(create_font(FONT_BODY, QFont.Weight.Bold))
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        return lbl

    @staticmethod
    def _new_side_table(headers: list) -> QTableWidget:
        table = QTableWidget()
        table.setFont(create_font(FONT_SMALL, style_name="Semilight"))
        table.horizontalHeader().setFont(create_font(FONT_SMALL, QFont.Weight.Bold))
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
                color = PROFIT if val > 0 else (LOSS if val < 0 else None)
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
        zoom_in_btn.setFixedSize(70, _SEG_H)
        zoom_out_btn.setFixedSize(70, _SEG_H)
        row.addStretch()
        row.addWidget(zoom_out_btn)
        row.addWidget(zoom_in_btn)
        row.addSpacing(20)
        zoom_in_btn.clicked.connect(self._zoom_y_in)
        zoom_out_btn.clicked.connect(self._zoom_y_out)
        return row

    # ------------------------------------------------------------ formatting
    def _format_config(self) -> tuple:
        """(currency prefix, unit suffix, number format) for prices."""
        if self._change_mode == 'bp':                 # bond yield in %
            return "", "%", "{:.2f}"
        if self._change_mode == 'abs' and self._market == 'Index':   # VIX or WTI
            return ("$" if self._ticker == 'CL=F' else ""), "", "{:,.2f}"
        if self._market == "Index":
            return "", "", "{:,.0f}"
        if self._market in ("KOSPI", "KOSDAQ"):
            return "", "", "{:,.0f}"
        return "$", "", "{:,.2f}"

    def _fmt_price(self, v: float) -> str:
        return f"{self._currency}{self._fmt_str.format(v)}{self._unit_suffix}"

    def _fmt_change(self, chg: float) -> str:
        if self._change_mode == 'bp':
            return f"{chg * 100:+.0f}bp"
        return f"{chg:+.2f}%"

    def _last_close_and_change(self):
        """(last close, change vs the previous close) -- % for stocks, the raw
        yield difference for bonds (formatted as bp by _fmt_change)."""
        if not self._has_data:
            return None, None
        closes = self._df.get_column("Close").drop_nulls()
        if len(closes) == 0:
            return None, None
        last = float(closes[-1])
        if len(closes) < 2:
            return last, None
        prev = float(closes[-2])
        if self._change_mode == 'bp':
            return last, last - prev
        return last, ((last - prev) / prev * 100.0) if prev else None

    def _col(self, name: str):
        return self._df.get_column(name).to_numpy() if name in self._df.columns else None

    # -------------------------------------------------------------- plotting
    def _plot(self) -> None:
        df = self._df
        dates = df.get_column("Date").to_numpy()
        self._x = mdates.date2num(dates)
        self._dates = dates

        has_vol = "Volume" in df.columns and float(df.get_column("Volume").max() or 0) > 0
        has_div = not self._is_simple_chart and ("MA20_Div" in df.columns or "MA50_Div" in df.columns)
        has_rsi = "RSI14" in df.columns

        gs = self._fig.add_gridspec(4, 1)
        ax_price = self._fig.add_subplot(gs[0])
        self._panel_axes["price"] = ax_price
        if has_vol:
            self._panel_axes["vol"] = self._fig.add_subplot(gs[1], sharex=ax_price)
        if has_div:
            self._panel_axes["div"] = self._fig.add_subplot(gs[2], sharex=ax_price)
        if has_rsi and not self._is_simple_chart:
            self._panel_axes["rsi"] = self._fig.add_subplot(gs[3], sharex=ax_price)

        self._plot_price_panel()
        if "vol" in self._panel_axes:
            self._plot_volume_panel()
        if "div" in self._panel_axes:
            self._plot_divergence_panel()
        if "rsi" in self._panel_axes:
            self._plot_rsi_panel()

        for key in ("vol", "div", "rsi"):
            available = key in self._panel_axes
            btn = self._panel_buttons[key]
            btn.setVisible(available)
            default_on = available and key != "rsi"
            btn.setChecked(default_on)
            self._panel_on[key] = default_on

        for ax in self._panel_axes.values():
            ax.tick_params(axis="both", labelsize=_TICK_PT, colors=TEXT_MUTED, length=3)
            ax.grid(True, linestyle=":", color=LINE, alpha=0.9)
            for spine in ("top", "right"):
                ax.spines[spine].set_visible(False)
            for spine in ("left", "bottom"):
                ax.spines[spine].set_color(LINE)
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%y.%m"))
            ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=4, maxticks=9))
            self._crosshairs.append(ax.axvline(self._x[-1], color=ACCENT, linewidth=1, linestyle=(0, (3, 3)),
                                               visible=False, zorder=6))

        self._install_pan_zoom()
        self._install_crosshair()
        self._set_view("short" if not self._is_simple_chart else "close", initial=True)
        self._apply_period(_DEFAULT_PERIOD)
        self._update_readout(None)

    def _plot_price_panel(self) -> None:
        """Close (ink) + MA ramp; Equal Weight rebased onto the same axis."""
        ax = self._panel_axes["price"]
        x = self._x
        self._lines["Close"], = ax.plot(x, self._col("Close"), color=TEXT, linewidth=1.7, label="Close", zorder=5)
        if not self._is_simple_chart:
            for key, color in MA_RAMP.items():
                arr = self._col(key)
                if arr is not None:
                    self._lines[key], = ax.plot(x, arr, color=color, linewidth=1.3, label=key, zorder=4)
        ew = self._col("EqualWeight")
        if ew is not None and not self._is_simple_chart:
            close = self._col("Close")
            both = ~(np.isnan(ew) | np.isnan(close))
            if both.any():
                first = int(np.argmax(both))
                rebased = ew * (close[first] / ew[first])
                self._lines["EW"], = ax.plot(x, rebased, color=TEXT_MUTED, linewidth=1.2, linestyle="--",
                                             label="EW", zorder=3, visible=False)
        ax.set_ylabel(f"Price{(' (' + self._unit_suffix + ')') if self._unit_suffix else ''}",
                      fontsize=_TICK_PT, color=TEXT_MUTED)
        ax.yaxis.set_major_formatter(lambda v, _pos: self._fmt_str.format(v))
        ax.tick_params(labelbottom=False)

    def _plot_volume_panel(self) -> None:
        """Up/down volume bars in PROFIT/LOSS (issue #1)."""
        ax = self._panel_axes["vol"]
        df = self._df
        up = df.filter(pl.col("Close") >= pl.col("Open"))
        down = df.filter(pl.col("Close") < pl.col("Open"))
        width = 0.62
        for part, color, alpha in ((up, PROFIT, 0.55), (down, LOSS, 0.5)):
            if part.height:
                xs = mdates.date2num(part.get_column("Date").to_numpy())
                ax.add_collection(PolyCollection(
                    _make_bars(xs, [0] * part.height, part.get_column("Volume").to_numpy(), width),
                    facecolors=color, alpha=alpha, linewidths=0))
        ax.set_ylabel("Volume", fontsize=_TICK_PT, color=TEXT_MUTED)
        ax.yaxis.set_major_formatter(lambda v, _pos: _compact(v) if v else "0")
        ax.tick_params(labelbottom=False)

    def _plot_divergence_panel(self) -> None:
        """Div(20)/Div(50) = price / MA x 100 over three bands centred on 100
        (issue #6): above 100+MA_DIV_NEUTRAL_PCT overheated (PROFIT tint),
        below 100-MA_DIV_NEUTRAL_PCT depressed (LOSS tint), neutral between."""
        ax = self._panel_axes["div"]
        n = MA_DIV_NEUTRAL_PCT
        self._div_bands = [
            ax.axhspan(100 + n, 10_000, facecolor=PROFIT, alpha=0.09, linewidth=0),
            ax.axhspan(100 - n, 100 + n, facecolor=TEXT_FAINT, alpha=0.07, linewidth=0),
            ax.axhspan(-10_000, 100 - n, facecolor=LOSS, alpha=0.08, linewidth=0),
        ]
        ax.axhline(100, color=TEXT_FAINT, linewidth=1, linestyle=(0, (4, 3)))
        if "MA50_Div" in self._df.columns:
            self._lines["Div(50)"], = ax.plot(self._x, self._col("MA50_Div"), color=MA_RAMP["MA20"], linewidth=1.4)
        if "MA20_Div" in self._df.columns:
            self._lines["Div(20)"], = ax.plot(self._x, self._col("MA20_Div"), color=MA_RAMP["MA5"], linewidth=1.4)
        ax.set_ylabel("Div %", fontsize=_TICK_PT, color=TEXT_MUTED)
        ax.tick_params(labelbottom=False)

    def _plot_rsi_panel(self) -> None:
        """RSI14 in its own panel (issue #4); overbought = PROFIT, oversold = LOSS."""
        ax = self._panel_axes["rsi"]
        rsi = self._col("RSI14")
        self._lines["RSI"], = ax.plot(self._x, rsi, color=ACCENT, linewidth=1.3)
        ax.axhline(70, color=PROFIT, linestyle=":", alpha=0.6, linewidth=1)
        ax.axhline(30, color=LOSS, linestyle=":", alpha=0.6, linewidth=1)
        ax.fill_between(self._x, 70, 100, where=(rsi >= 70), facecolor=PROFIT, alpha=0.1, linewidth=0)
        ax.fill_between(self._x, 0, 30, where=(rsi <= 30), facecolor=LOSS, alpha=0.1, linewidth=0)
        ax.set_ylim(0, 100)
        ax.set_yticks([30, 50, 70])
        ax.set_ylabel("RSI 14", fontsize=_TICK_PT, color=TEXT_MUTED)
        ax.tick_params(labelbottom=False)

    # --------------------------------------------------------- layout / view
    def _visible_panels(self) -> list:
        order = [("price", 3 if self._view != "div" else 2)]
        if self._panel_on["vol"] and "vol" in self._panel_axes:
            order.append(("vol", 1))
        if self._panel_on["div"] and "div" in self._panel_axes:
            order.append(("div", 2 if self._view == "div" else 1))
        if self._panel_on["rsi"] and "rsi" in self._panel_axes:
            order.append(("rsi", 1))
        return order

    def _apply_layout(self) -> None:
        """Re-grid the visible panels (issue #11: the ratio is no longer a
        fixed 3:1:1 -- panels can be hidden and the Divergence view enlarges
        that panel)."""
        panels = self._visible_panels()
        gs = self._fig.add_gridspec(len(panels), 1, height_ratios=[r for _, r in panels],
                                    hspace=0.08, left=0.075, right=0.90, top=0.97, bottom=0.07)
        shown = set()
        for i, (key, _r) in enumerate(panels):
            ax = self._panel_axes[key]
            ax.set_subplotspec(gs[i])
            ax.set_visible(True)
            ax.tick_params(labelbottom=(i == len(panels) - 1))
            shown.add(key)
        for key, ax in self._panel_axes.items():
            if key not in shown:
                ax.set_visible(False)

    def _set_panel(self, key: str, on: bool) -> None:
        self._panel_on[key] = bool(on)
        self._apply_layout()
        self._update_readout(self._cursor_idx)
        self._canvas.draw_idle()

    def _set_view(self, key: str, initial: bool = False) -> None:
        self._view = key
        shown = set(_VIEWS[key][1])
        for name, line in self._lines.items():
            if name in MA_RAMP:
                line.set_visible(name in shown)
            elif name == "EW":
                line.set_visible(key == "ew")
        if key in self._view_buttons and not self._view_buttons[key].isChecked():
            self._view_buttons[key].setChecked(True)
        if self._view_buttons:
            self._view_note.setText(_VIEWS[key][2])
        self._apply_layout()
        if not initial:
            self._autofit_y()
            self._place_end_labels()
            self._update_readout(self._cursor_idx)
            self._canvas.draw_idle()

    # ------------------------------------------------------------ end labels
    def _price_series_visible(self) -> list:
        """[(key, Line2D)] of the price-panel lines currently shown, Close first."""
        out = []
        for key in ("Close", *MA_RAMP.keys(), "EW"):
            line = self._lines.get(key)
            if line is not None and line.get_visible():
                out.append((key, line))
        return out

    def _place_end_labels(self) -> None:
        """A value label at each visible price-panel line's right edge (issue
        #3, replacing the legend), pushed apart vertically so they never
        overlap (same rule as the mockup: keep anchor order, min pitch)."""
        ax = self._panel_axes["price"]
        for ann in self._end_labels.values():
            ann.remove()
        self._end_labels.clear()

        x_right = ax.get_xlim()[1]
        idx = int(np.searchsorted(self._x, x_right, side="right") - 1)
        idx = max(0, min(len(self._x) - 1, idx))
        entries = []
        for key, line in self._price_series_visible():
            y = line.get_ydata()[idx]
            if y is None or not np.isfinite(y):
                continue
            if key == "Close":
                text, bg, fg = self._fmt_str.format(y), TEXT, SURFACE
            elif key == "EW":
                text, bg, fg = f"EW {_compact(y)}", TEXT_MUTED, SURFACE
            else:
                bg = MA_RAMP[key]
                text, fg = f"{key} {_compact(y)}", (SURFACE if key in ("MA5", "MA10") else TEXT)
            entries.append((key, float(y), text, bg, fg))
        if not entries:
            return

        # Collision push in display pixels, then back to an offset in points.
        dpi = self._fig.dpi
        pitch_px = 1.6 * _TICK_PT * dpi / 72
        anchored = sorted(((ax.transData.transform((x_right, y))[1], key, y, text, bg, fg)
                           for key, y, text, bg, fg in entries), key=lambda t: t[0])
        placed = []
        for disp_y, key, y, text, bg, fg in anchored:
            if placed and disp_y - placed[-1][0] < pitch_px:
                disp_y = placed[-1][0] + pitch_px
            placed.append([disp_y, key, y, text, bg, fg])
        top_px = ax.transAxes.transform((0, 1))[1]
        overflow = placed[-1][0] - top_px + pitch_px / 2
        if overflow > 0:
            for p in placed:
                p[0] -= overflow

        for disp_y, key, y, text, bg, fg in placed:
            anchor_px = ax.transData.transform((x_right, y))[1]
            dy_pt = (disp_y - anchor_px) * 72 / dpi
            self._end_labels[key] = ax.annotate(
                text, xy=(x_right, y), xytext=(5, dy_pt), textcoords="offset points",
                fontsize=_TICK_PT, color=fg, ha="left", va="center", annotation_clip=False, zorder=7,
                bbox=dict(boxstyle="round,pad=0.25", facecolor=bg, edgecolor="none"),
            )

    # ----------------------------------------------------------- crosshair
    def _install_crosshair(self) -> None:
        canvas = self._canvas
        canvas.mpl_connect("motion_notify_event", self._on_motion)
        canvas.mpl_connect("axes_leave_event", self._on_leave)
        canvas.mpl_connect("figure_leave_event", self._on_leave)
        canvas.mpl_connect("resize_event", lambda _e: self._place_end_labels())

    def _index_at(self, xdata: float) -> int:
        i = int(np.searchsorted(self._x, xdata))
        if i <= 0:
            return 0
        if i >= len(self._x):
            return len(self._x) - 1
        return i if abs(self._x[i] - xdata) < abs(self._x[i - 1] - xdata) else i - 1

    def _set_cursor(self, idx) -> None:
        """Move the shared crosshair to bar `idx` (None hides it) and refresh
        the readout row."""
        self._cursor_idx = idx
        for line in self._crosshairs:
            if idx is None:
                line.set_visible(False)
            else:
                line.set_xdata([self._x[idx], self._x[idx]])
                line.set_visible(True)
        self._update_readout(idx)
        self._canvas.draw_idle()

    def _on_leave(self, _event) -> None:
        if self._cursor_idx is not None:
            self._set_cursor(None)

    def _update_readout(self, idx) -> None:
        """Readout chips for bar `idx` (latest bar when None)."""
        if self._x is None:
            self._readout_lbl.setText("")
            return
        i = len(self._x) - 1 if idx is None else idx
        date_str = pd.to_datetime(self._dates[i]).strftime("%Y-%m-%d")

        def chip(color, key, value):
            return (f'<span style="color:{color};">&#9644;</span>&nbsp;'
                    f'<span style="color:{TEXT_MUTED};">{key}</span>&nbsp;'
                    f'<b style="color:{TEXT};">{value}</b>')

        def val(col):
            arr = self._col(col)
            if arr is None or i >= len(arr):
                return None
            v = arr[i]
            return None if v is None or (isinstance(v, float) and np.isnan(v)) else float(v)

        parts = [f'<span style="color:{TEXT_MUTED};">{date_str}</span>']
        close = val("Close")
        if close is not None:
            parts.append(chip(TEXT, "Close", self._fmt_price(close)))
        for key, line in self._price_series_visible():
            if key in ("Close", "EW"):
                continue
            v = val(key)
            if v is not None:
                parts.append(chip(MA_RAMP[key], key, self._fmt_str.format(v)))
        if "EW" in self._lines and self._lines["EW"].get_visible():
            v = self._lines["EW"].get_ydata()[i]
            if v is not None and np.isfinite(v):
                parts.append(chip(TEXT_MUTED, "EW", self._fmt_str.format(float(v))))
        if self._panel_on.get("vol"):
            v = val("Volume")
            if v is not None:
                parts.append(chip(PROFIT, "Vol", _compact(v)))
        if self._panel_on.get("div"):
            for col, key, color in (("MA20_Div", "Div(20)", MA_RAMP["MA5"]), ("MA50_Div", "Div(50)", MA_RAMP["MA20"])):
                v = val(col)
                if v is not None:
                    parts.append(chip(color, key, f"{v:.1f}"))
        if self._panel_on.get("rsi"):
            v = val("RSI14")
            if v is not None:
                parts.append(chip(ACCENT, "RSI", f"{v:.0f}"))
        self._readout_lbl.setText("&nbsp;&nbsp;&nbsp;".join(parts))

    # ----------------------------------------------------------- pan / zoom
    def _install_pan_zoom(self) -> None:
        """Mouse-wheel zoom, left-drag pan and a horizontal scrollbar, all
        clamped to the data range (no right padding so future dates never appear)."""
        abs_min, abs_max = float(self._x[0]), float(self._x[-1])
        self._x_lo = abs_min - (abs_max - abs_min) * 0.02
        self._x_hi = abs_max

        self.scrollbar = QScrollBar(Qt.Orientation.Horizontal)
        self.scrollbar.valueChanged.connect(self._on_scrollbar_change)

        canvas = self._canvas
        canvas.mpl_connect('scroll_event', self._on_scroll)
        canvas.mpl_connect('button_press_event', self._on_press)
        canvas.mpl_connect('button_release_event', self._on_release)

    def _chart_axes(self) -> list:
        return [ax for ax in self._panel_axes.values() if ax.get_visible()]

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

    def _set_xlim(self, xmin: float, xmax: float) -> None:
        self._panel_axes["price"].set_xlim(*self._clamp_xlim(xmin, xmax))
        self._autofit_y()
        self._place_end_labels()
        self._update_scrollbar()
        self._canvas.draw_idle()

    def _visible_mask(self):
        xmin, xmax = self._panel_axes["price"].get_xlim()
        return (self._x >= xmin) & (self._x <= xmax)

    def _autofit_y(self) -> None:
        """Fit each panel's Y range to the bars in view (with the period
        buttons a 1M window on a 5Y Y range would be a flat line)."""
        mask = self._visible_mask()
        if not mask.any():
            return

        def span(arrays, pad):
            vals = np.concatenate([np.asarray(a, dtype=float)[mask] for a in arrays if a is not None])
            vals = vals[np.isfinite(vals)]
            if len(vals) == 0:
                return None
            lo, hi = float(vals.min()), float(vals.max())
            margin = (hi - lo) * pad or abs(hi) * 0.01 or 1.0
            return lo - margin, hi + margin

        price_arrays = [line.get_ydata() for _k, line in self._price_series_visible()]
        rng = span(price_arrays, 0.04)
        if rng:
            self._panel_axes["price"].set_ylim(*rng)

        if "vol" in self._panel_axes:
            vol = self._col("Volume")
            if vol is not None:
                v = np.asarray(vol, dtype=float)[mask]
                v = v[np.isfinite(v)]
                if len(v) and v.max() > 0:
                    self._panel_axes["vol"].set_ylim(0, float(v.max()) * 1.1)

        if "div" in self._panel_axes:
            rng = span([self._col("MA20_Div"), self._col("MA50_Div")], 0.0)
            if rng:
                # Symmetric about 100 so the baseline sits mid-panel (issue #6).
                r = max(abs(rng[1] - 100), abs(100 - rng[0])) + MA_DIV_NEUTRAL_PCT
                self._panel_axes["div"].set_ylim(100 - r, 100 + r)

    def _update_scrollbar(self) -> None:
        x_min, x_max = self._panel_axes["price"].get_xlim()
        denom = (self._x_hi - self._x_lo) - (x_max - x_min)
        val = 0 if denom <= 0 else int((x_min - self._x_lo) / denom * _SCROLL_MAX)
        self.scrollbar.blockSignals(True)
        self.scrollbar.setRange(0, _SCROLL_MAX)
        self.scrollbar.setValue(max(0, min(_SCROLL_MAX, val)))
        self.scrollbar.blockSignals(False)

    def _on_scrollbar_change(self, val: int) -> None:
        x_min, x_max = self._panel_axes["price"].get_xlim()
        w = x_max - x_min
        new_xmin = self._x_lo + (val / _SCROLL_MAX) * ((self._x_hi - self._x_lo) - w)
        self._set_xlim(new_xmin, new_xmin + w)

    def _on_period_clicked(self, label: str, months) -> None:
        self._apply_period(label, months)

    def _apply_period(self, label: str, months=None) -> None:
        if label in self._period_buttons and not self._period_buttons[label].isChecked():
            self._period_buttons[label].setChecked(True)
        if months is None:
            months = dict(_PERIODS).get(label)
        if months is None:
            self._set_xlim(self._x_lo, self._x_hi)
            return
        end = pd.to_datetime(self._dates[-1])
        start = mdates.date2num(end - pd.DateOffset(months=months))
        self._set_xlim(max(start, self._x_lo), self._x_hi)

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
        x_min, x_max = self._panel_axes["price"].get_xlim()
        x_focus = event.xdata
        self._set_xlim(x_focus - (x_focus - x_min) * factor, x_focus + (x_max - x_focus) * factor)

    def _on_press(self, event) -> None:
        if event.button != 1 or event.inaxes not in self._chart_axes():
            return
        self._pan_start = (event.x, self._panel_axes["price"].get_xlim())

    def _on_motion(self, event) -> None:
        if self._pan_start is not None:
            xpress, xlim = self._pan_start
            bbox = self._panel_axes["price"].get_window_extent()
            if bbox.width == 0:
                return
            dx_data = (event.x - xpress) * ((xlim[1] - xlim[0]) / bbox.width)
            self._set_xlim(xlim[0] - dx_data, xlim[1] - dx_data)
            return
        if event.inaxes not in self._chart_axes() or event.xdata is None:
            if self._cursor_idx is not None:
                self._set_cursor(None)
            return
        idx = self._index_at(event.xdata)
        if idx != self._cursor_idx:
            self._set_cursor(idx)

    def _on_release(self, _event) -> None:
        self._pan_start = None

    def _zoom_y_in(self, *_):
        self._zoom_y(0.8)

    def _zoom_y_out(self, *_):
        self._zoom_y(1.2)

    def _zoom_y(self, factor: float) -> None:
        """Scale the price panel's Y range around the midpoint of the visible closes."""
        ax = self._panel_axes["price"]
        ymin, ymax = ax.get_ylim()
        try:
            close = np.asarray(self._col("Close"), dtype=float)[self._visible_mask()]
            close = close[np.isfinite(close)]
            ymid = (close.min() + close.max()) / 2 if len(close) else (ymin + ymax) / 2
        except Exception:
            ymid = (ymin + ymax) / 2
        ydiff = (ymax - ymin) / 2
        ax.set_ylim(ymid - ydiff * factor, ymid + ydiff * factor)
        self._place_end_labels()
        self._canvas.draw_idle()
