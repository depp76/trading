"""ui/dialogs.py — Dialog classes (Phase 3-1 분리)

분리 출처: main.py (2026-08-29 feat/3-1-modularize)
포함 클래스:
  IndexMaDialog, StockMaDialog,
  BuyEditDialog, SellEditDialog, TradeEntryDialog, StockTradeHistoryDialog,
  TotalAssetsGraphDialog
"""
import logging
import datetime as _dt
from datetime import datetime

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QGridLayout,
    QPushButton, QLineEdit, QLabel, QComboBox, QScrollBar,
    QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox,
    QApplication, QSplitter, QWidget, QStyledItemDelegate,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont, QPen

import numpy as np
import pandas as pd
import matplotlib.dates as mdates
import mplcursors
from matplotlib.figure import Figure
from matplotlib.collections import PolyCollection
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas

from ui import theme

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lazy helpers — avoid circular imports with main
# ---------------------------------------------------------------------------
def _get_create_font():
    import main as _m
    return _m.create_font


def _get_index_tickers():
    import main as _m
    return _m.INDEX_TICKERS


def _get_validators():
    import main as _m
    return (
        _m._fmt_num_edit,
        _m._validate_date_str,
        _m._validate_positive_number,
        _m._mk_field_validator,
    )


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
        create_font = _get_create_font()
        INDEX_TICKERS = _get_index_tickers()
        self.setWindowTitle("Major Index - 20 & 50-Day Moving Average")
        self.resize(1100, 620)

        layout = QVBoxLayout(self)

        fig = Figure(figsize=(15, 6), constrained_layout=True)
        labels = [lbl for lbl in INDEX_TICKERS.keys() if lbl not in ("Dow Jones", "US10YT", "JP10YT", "KR3YT", "VIX", "VKOSPI", "WTI")]   # KOSPI, KOSDAQ, S&P500, NASDAQ, NASDAQ 100

        cols = 3 if len(labels) > 4 else 2
        for idx, label in enumerate(labels):
            ax = fig.add_subplot(2, cols, idx + 1)
            color = INDEX_TICKERS[label][1]
            df, err = results.get(label, (None, "No data"))

            if df is not None and not df.is_empty() and "MA10" in df.columns and "MA20" in df.columns and "MA50" in df.columns:
                dates = df.get_column("Date").to_numpy()
                line_close, = ax.plot(dates, df.get_column("Close").to_numpy(), color=color,
                        linewidth=1.2, marker=".", markersize=3, label="Close")
                line_ma10, = ax.plot(dates, df.get_column("MA10").to_numpy(), color="#1abc9c",
                        linewidth=1.6, linestyle="-", marker=".", markersize=3, label="10-Day MA")
                line_ma20, = ax.plot(dates, df.get_column("MA20").to_numpy(), color="#e74c3c",
                        linewidth=1.6, linestyle="-", marker=".", markersize=3, label="20-Day MA")
                line_ma50, = ax.plot(dates, df.get_column("MA50").to_numpy(), color="#e67e22",
                        linewidth=1.6, linestyle="-", marker=".", markersize=3, label="50-Day MA")
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
                    sel.annotation.set_color(theme.c("text"))
                    sel.annotation.get_bbox_patch().set(fc=theme.c("panel_bg"), alpha=0.9, edgecolor="gray")
            else:
                ax.text(0.5, 0.5, f"Failed to load\n{err or ''}",
                        ha="center", va="center", transform=ax.transAxes,
                        color="gray", fontsize=10)

            ax.set_title(label, fontsize=11, fontweight="bold")

        canvas = FigureCanvas(fig)
        layout.addWidget(canvas)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn, alignment=Qt.AlignmentFlag.AlignRight)


# ---------------------------------------------------------------------------
# StockMaDialog — Stock MA chart dialog (Close + MA20 + MA50)
# ---------------------------------------------------------------------------
class StockMaDialog(QDialog):
    """Dialog showing Close + 20-Day MA + 50-Day MA for a single stock."""

    def __init__(self, ticker, name, market, df, investor_data=None, parent=None, change_mode='pct'):
        super().__init__(parent)
        create_font = _get_create_font()
        import polars as pl
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

        # ---MA-only toggle button (created here; inserted into layout just above canvas later) ---
        self._ma_only = False
        self._ma_btn = QPushButton("Long Term")
        self._ma_btn.setCheckable(True)
        self._ma_btn.setChecked(False)
        self._ma_btn.setFixedHeight(28)
        self._ma_btn.setStyleSheet(
            "QPushButton { background:#2c3e50; color:#ecf0f1; border:1px solid #7f8c8d;"
            " border-radius:4px; padding:0 12px; font-weight:bold; }"
            "QPushButton:checked { background:#8e44ad; color:#fff; border:1px solid #9b59b6; }"
            "QPushButton:hover:!checked { background:#34495e; }"
        )

        self._close_btn = QPushButton("Closing Price")
        self._close_btn.setCheckable(True)
        self._close_btn.setChecked(False)
        self._close_btn.setFixedHeight(28)
        self._close_btn.setStyleSheet(self._ma_btn.styleSheet())

        self._div_btn = QPushButton("Div")
        self._div_btn.setCheckable(True)
        self._div_btn.setChecked(False)
        self._div_btn.setFixedHeight(28)
        self._div_btn.setStyleSheet(self._ma_btn.styleSheet())

        self._ew_btn = QPushButton("Equal Weight")
        self._ew_btn.setCheckable(True)
        self._ew_btn.setChecked(False)
        self._ew_btn.setFixedHeight(28)
        self._ew_btn.setStyleSheet(self._ma_btn.styleSheet())
        self._ew_btn.setVisible(False)

        is_stock = market in ("KOSPI", "KOSDAQ", "NASDAQ 100", "S&P500")
        has_right_panel = (investor_data and len(investor_data) > 0) or is_stock

        if has_right_panel:
            table_widget = QWidget()
            table_v_layout = QVBoxLayout(table_widget)
            table_v_layout.setContentsMargins(0, 0, 0, 0)
            splitter.addWidget(table_widget)

            splitter.setStretchFactor(0, 10)
            splitter.setStretchFactor(1, 3)

            is_wti_futures = False
            if investor_data and len(investor_data) > 0:
                is_wti_futures = (ticker == "CL=F" and 'Contract' in investor_data[0])

            if is_wti_futures:
                lbl = QLabel("Futures Price (Last 8 Months)")
                lbl.setFont(create_font(10, QFont.Weight.Bold))
                lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                table_v_layout.addWidget(lbl)

                table = QTableWidget()
                table.setFont(create_font(9))
                table.horizontalHeader().setFont(create_font(9, QFont.Weight.Bold))
                table.setColumnCount(3)
                table.setHorizontalHeaderLabels(["Name", "Code", "Return"])
                table.verticalHeader().setVisible(False)
                table.setRowCount(len(investor_data))
                for i, row_data in enumerate(investor_data):
                    dt_item = QTableWidgetItem(row_data['Contract'])
                    dt_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    table.setItem(i, 0, dt_item)

                    sym_item = QTableWidgetItem(row_data['Symbol'])
                    sym_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    table.setItem(i, 1, sym_item)

                    close_val = row_data.get('Close', 0)
                    close_item = QTableWidgetItem(f"${close_val:,.2f}")
                    close_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                    table.setItem(i, 2, close_item)

                table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
                table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
                table.setMinimumWidth(260)
                table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
                table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
                table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
                table_v_layout.addWidget(table)
            elif investor_data and len(investor_data) > 0:
                lbl = QLabel(f"Supply & Demand Trend (Last {len(investor_data)} Days)")
                lbl.setFont(create_font(10, QFont.Weight.Bold))
                lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                table_v_layout.addWidget(lbl)

                table = QTableWidget()
                table.setFont(create_font(9))
                table.horizontalHeader().setFont(create_font(9, QFont.Weight.Bold))

                has_details = any(row.get('InvestmentTrust', 0) != 0 or row.get('PrivateEquity', 0) != 0 for row in investor_data)
                headers = ["Date", "Close", "Foreigner", "Institution", "Retail"]
                keys = ["Foreigner", "Institution", "Retail"]
                if has_details:
                    headers.extend(["Inv.Trust", "PrivateEq."])
                    keys.extend(["InvestmentTrust", "PrivateEquity"])

                table.setColumnCount(len(headers))
                table.setHorizontalHeaderLabels(headers)
                table.verticalHeader().setVisible(False)
                table.setRowCount(len(investor_data))
                for i, row_data in enumerate(investor_data):
                    dt_item = QTableWidgetItem(row_data['Date'])
                    dt_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    table.setItem(i, 0, dt_item)

                    close_val = row_data.get('Close', 0)
                    close_item = QTableWidgetItem(f"{close_val:,}")
                    close_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                    table.setItem(i, 1, close_item)

                    for j, key in enumerate(keys, start=2):
                        val = row_data.get(key, 0)
                        item = QTableWidgetItem(f"{val:,}")
                        item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                        # use slightly muted colors
                        if val > 0:
                            item.setForeground(QColor("#d32f2f"))
                        elif val < 0:
                            item.setForeground(QColor("#1976d2"))
                        table.setItem(i, j, item)

                table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
                table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
                table.setMinimumWidth(360 + (140 if has_details else 0))
                table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
                for col in range(1, len(headers)):
                    table.horizontalHeader().setSectionResizeMode(col, QHeaderView.ResizeMode.Stretch)
                table_v_layout.addWidget(table)

            if is_stock:
                fin_btn = QPushButton("Company Information")
                fin_btn.setFont(create_font(10, QFont.Weight.Bold))
                fin_btn.setMinimumHeight(40)

                def open_fin_data(checked=False, t=ticker, m=market):
                    import webbrowser
                    if m in ("KOSPI", "KOSDAQ"):
                        t_str = str(t).zfill(6)
                        url = f"https://finance.naver.com/item/main.naver?code={t_str}"
                    else:
                        t_str = str(t).replace(".", "-")
                        url = f"https://www.google.com/finance?q={t_str}"
                    webbrowser.open(url)

                fin_btn.clicked.connect(open_fin_data)

                table_v_layout.addSpacing(10)
                table_v_layout.addWidget(fin_btn)

            if not (investor_data and len(investor_data) > 0):
                table_v_layout.addStretch()

        fig = Figure(figsize=(9.5, 7.5), constrained_layout=True)
        is_simple_chart = (ticker in ('CL=F', '^VIX', 'VKOSPI') or change_mode == 'bp')
        if is_simple_chart:
            axs = fig.subplots(2, 1, sharex=True, gridspec_kw={'height_ratios': [3, 1]})
            ax1, ax2 = axs[0], axs[1]
            ax3 = None
        else:
            axs = fig.subplots(3, 1, sharex=True, gridspec_kw={'height_ratios': [3, 1, 1]})
            ax1, ax2, ax3 = axs[0], axs[1], axs[2]

        if df is not None and not df.is_empty() and "MA5" in df.columns and "MA10" in df.columns and "MA20" in df.columns and "MA50" in df.columns:
            # --- Unit / format config per change_mode ---
            if change_mode == 'bp':
                # Bond: yield in %, differences in bp
                currency = ""
                unit_suffix = "%"
                fmt_str = "{:.2f}"
                def diff_fmt(v1, v2): return f"{(v1-v2)*100:+.1f} bp" if v2 else "-"
            elif change_mode == 'abs' and market == 'Index':
                # VIX or WTI
                is_wti = ticker == 'CL=F'
                currency = "$" if is_wti else ""
                unit_suffix = "" if is_wti else ""
                fmt_str = "{:,.2f}"
                def diff_fmt(v1, v2): return f"${v1-v2:+.2f}" if (is_wti and v2) else (f"{v1-v2:+.2f}" if v2 else "-")
            else:
                if market == "Index":
                    currency = ""
                    fmt_str = "{:,.0f}"
                else:
                    currency = "KRW" if market in ("KOSPI", "KOSDAQ") else "$"
                    fmt_str = "{:,.0f}" if market in ("KOSPI", "KOSDAQ") else "{:,.2f}"
                unit_suffix = ""
                def diff_fmt(v1, v2): return f"{(v1-v2)/v2*100:+.1f}%" if v2 else "-"
            def _get_last_valid(col_name):
                try:
                    s = df.get_column(col_name).drop_nulls()
                    return float(s[-1]) if len(s) > 0 else 0.0
                except Exception:
                    return 0.0

            current_price = _get_last_valid("Close")
            ma5 = _get_last_valid("MA5")
            ma10 = _get_last_valid("MA10")
            ma20 = _get_last_valid("MA20")
            ma50 = _get_last_valid("MA50")
            diff10_str = diff_fmt(current_price, ma10)
            diff20_str = diff_fmt(current_price, ma20)
            diff50_str = diff_fmt(current_price, ma50)

            # US style colors
            col_up = '#2ecc71'    # green
            col_down = '#e74c3c'  # red

            up = df.filter(pl.col("Close") >= pl.col("Open"))
            down = df.filter(pl.col("Close") < pl.col("Open"))

            def make_bars(x, y_bottom, y_top, w):
                """Vectorized polygon generation for candlestick bodies."""
                x = np.asarray(x, dtype=float)
                y_bottom = np.asarray(y_bottom, dtype=float)
                y_top = np.asarray(y_top, dtype=float)
                # Filter NaN rows
                valid = ~(np.isnan(y_bottom) | np.isnan(y_top))
                x, y_bottom, y_top = x[valid], y_bottom[valid], y_top[valid]
                if len(x) == 0:
                    return []
                w2 = w / 2
                # Build (N, 4, 2) array of rectangles directly
                polys = np.empty((len(x), 4, 2))
                polys[:, 0] = np.stack([x - w2, y_bottom], axis=1)
                polys[:, 1] = np.stack([x - w2, y_top],    axis=1)
                polys[:, 2] = np.stack([x + w2, y_top],    axis=1)
                polys[:, 3] = np.stack([x + w2, y_bottom], axis=1)
                return polys

            width = 0.6
            up_x = mdates.date2num(up.get_column("Date").to_numpy())
            down_x = mdates.date2num(down.get_column("Date").to_numpy())
            dates_arr = df.get_column("Date").to_numpy()

            fmt_str_val = fmt_str  # alias for closure

            if not is_simple_chart:
                self._candle_artists = []

                def _plot_valid(col, color, label):
                    _arr = df.get_column(col).to_numpy()
                    l, = ax1.plot(dates_arr, _arr, color=color, linewidth=1.5, linestyle="-", marker=".", markersize=3, label=label)
                    return l

                l_close = _plot_valid("Close", "#2980b9", f"Price ({currency}{fmt_str_val.format(current_price)}{unit_suffix})")
                l_ma5 = _plot_valid("MA5", "#7f6000", f"5-Day MA ({currency}{fmt_str_val.format(ma5)}{unit_suffix})")
                l_ma10 = _plot_valid("MA10", "#1abc9c", f"10-Day MA ({currency}{fmt_str_val.format(ma10)}{unit_suffix})")
                l_ma20 = _plot_valid("MA20", "#f39c12", f"20-Day MA ({currency}{fmt_str_val.format(ma20)}{unit_suffix})")
                l_ma50 = _plot_valid("MA50", "#9b59b6", f"50-Day MA ({currency}{fmt_str_val.format(ma50)}{unit_suffix})")

                ax_div_main = None
                l_div20_main = None
                l_div50_main = None
                if "MA20_Div" in df.columns and "MA50_Div" in df.columns:
                    ax_div_main = ax1.twinx()
                    ax_div_main.set_ylabel("Divergence (%)", color="#34495e")
                    ax_div_main.tick_params(axis='y', labelcolor="#34495e")
                    l_div20_main, = ax_div_main.plot(dates_arr, df.get_column("MA20_Div").to_numpy(), color="#e67e22", linewidth=1.5, linestyle="--", label="Div(20)", visible=False)
                    l_div50_main, = ax_div_main.plot(dates_arr, df.get_column("MA50_Div").to_numpy(), color="#e74c3c", linewidth=1.5, linestyle="--", label="Div(50)", visible=False)
                    ax_div_main.set_visible(False)
            else:
                self._candle_artists = []
                _c_arr = df.get_column("Close").to_numpy()
                l_close, = ax1.plot(dates_arr, _c_arr, color="#2980b9", linewidth=2.0, linestyle="-", label=f"Price ({currency}{fmt_str_val.format(current_price)}{unit_suffix})")
                l_ma5 = l_ma10 = l_ma20 = l_ma50 = None
                ax_div_main = l_div20_main = l_div50_main = None

            l_ew = None
            ax_ew = None
            if "EqualWeight" in df.columns:
                self._ew_btn.setVisible(True)
                ax_ew = ax1.twinx()
                ew_arr = df.get_column("EqualWeight").to_numpy()
                l_ew, = ax_ew.plot(dates_arr, ew_arr, color="#34495e", linewidth=1.5, linestyle="--", marker=".", markersize=3, label="Equal Weight (252650)")
                ax_ew.set_ylabel("KODEX 200 EW", color="#34495e")
                ax_ew.tick_params(axis='y', labelcolor="#34495e")
                ax_ew.set_visible(False)
                l_ew.set_visible(False)

            # --- Backtesting removed as per user request ---

            # Top subplot styling
            if l_ew is not None:
                lines_1, labels_1 = ax1.get_legend_handles_labels()
                lines_ew, labels_ew = ax_ew.get_legend_handles_labels()
                ax1.legend(lines_1 + lines_ew, labels_1 + labels_ew, loc="upper left", fontsize=9)
            else:
                ax1.legend(loc="upper left", fontsize=9)
            ax1.grid(True, linestyle=":", alpha=0.5)
            ax1.set_title(name, fontsize=12, fontweight="bold")

            # Volume bar (Optimized)
            self._volume_artists = [ax2]  # will toggle ax2 visibility
            if "Volume" in df.columns:
                vp_up = PolyCollection(make_bars(up_x, [0]*len(up), up.get_column("Volume").to_numpy(), width), facecolors=col_up, alpha=0.7)
                vp_down = PolyCollection(make_bars(down_x, [0]*len(down), down.get_column("Volume").to_numpy(), width), facecolors=col_down, alpha=0.7)
                ax2.add_collection(vp_up)
                ax2.add_collection(vp_down)
                max_vol = df.get_column("Volume").max()
                if max_vol and max_vol > 0:
                    ax2.set_ylim(0, max_vol * 1.1)

            ax2.set_ylabel("Volume")
            ax2.grid(True, linestyle=":", alpha=0.4)

            # RSI Chart (Overlay on ax2)
            ax_rsi = None
            if "RSI14" in df.columns:
                ax_rsi = ax2.twinx()
                rsi_arr = df.get_column("RSI14").to_numpy()
                ax_rsi.plot(dates_arr, rsi_arr, color="#8e44ad", linewidth=1.5, alpha=0.8)
                ax_rsi.axhline(70, color="#e74c3c", linestyle=":", alpha=0.5)
                ax_rsi.axhline(30, color="#2ecc71", linestyle=":", alpha=0.5)
                ax_rsi.fill_between(dates_arr, 70, 100, where=(rsi_arr >= 70), facecolor='#e74c3c', alpha=0.1)
                ax_rsi.fill_between(dates_arr, 0, 30, where=(rsi_arr <= 30), facecolor='#2ecc71', alpha=0.1)
                ax_rsi.set_ylim(-10, 110)
                ax_rsi.set_yticks([30, 50, 70])
                ax_rsi.set_ylabel("RSI 14", color="#8e44ad")
                ax_rsi.tick_params(axis='y', labelcolor="#8e44ad")

            # Date Formatting on the bottom-most axis (ax3)
            ax2.xaxis.set_major_formatter(mdates.DateFormatter("%y.%m"))
            ax2.xaxis.set_major_locator(mdates.MonthLocator(interval=1))
            if ax3 is not None:
                ax3.xaxis.set_major_formatter(mdates.DateFormatter("%y.%m"))
                ax3.xaxis.set_major_locator(mdates.MonthLocator(interval=1))
            fig.autofmt_xdate(rotation=25)

            # ── MA Divergence subplot (ax3) — Div(50) + Div(20) combined ───
            ax3_div    = None
            _arr_div   = None
            _arr_ma50  = None
            _arr_div20 = None
            l_div3     = None
            l_div4     = None
            _has_div = ("MA50_Div" in df.columns or "MA20_Div" in df.columns) and not is_simple_chart
            if _has_div:
                # Determine combined range for background bands
                _all_divs = []
                if "MA50_Div" in df.columns:
                    _all_divs.append(df.get_column("MA50_Div").to_numpy())
                if "MA20_Div" in df.columns:
                    _all_divs.append(df.get_column("MA20_Div").to_numpy())
                _combined_max = float(max(a.max() for a in _all_divs))
                _combined_min = float(min(a.min() for a in _all_divs))

                # Color-coded background bands (MA50 tiers as baseline)
                ax3.axhspan(130, max(_combined_max * 1.05, 135), facecolor='#b41e1e', alpha=0.12)
                ax3.axhspan(110, 130, facecolor='#e74c3c', alpha=0.10)
                ax3.axhspan(107, 110, facecolor='#e08080', alpha=0.08)
                ax3.axhspan( 98, 102, facecolor='#888888', alpha=0.06)
                ax3.axhspan( 90,  98, facecolor='#5082d2', alpha=0.08)
                ax3.axhspan(min(_combined_min * 0.98, 85), 90, facecolor='#1e1eb4', alpha=0.12)
                # Reference line at 100%
                ax3.axhline(100, color='#555555', linewidth=1.0, linestyle='--', alpha=0.7)

                # — MA50_Div line (blue) —
                if "MA50_Div" in df.columns:
                    div_arr   = df.get_column("MA50_Div").to_numpy()
                    _arr_div  = div_arr
                    _arr_ma50 = df.get_column("MA50").to_numpy() if "MA50" in df.columns else None
                    ax3.plot(dates_arr, div_arr, color='#2980b9', linewidth=1.6,
                             marker='.', markersize=2, label='Div(50)', zorder=3)
                    l_div3, = ax3.plot(dates_arr, div_arr, color='none', marker='o',
                                       markersize=5, alpha=0, zorder=4)

                # — MA20_Div line (orange) —
                if "MA20_Div" in df.columns:
                    div20_arr  = df.get_column("MA20_Div").to_numpy()
                    _arr_div20 = div20_arr
                    ax3.plot(dates_arr, div20_arr, color='#e67e22', linewidth=1.6,
                             marker='.', markersize=2, label='Div(20)', zorder=3)
                    l_div4, = ax3.plot(dates_arr, div20_arr, color='none', marker='o',
                                       markersize=5, alpha=0, zorder=4)

                ax3.legend(loc='upper left', fontsize=7, framealpha=0.7)
                ax3.yaxis.set_label_position('right')
                ax3.yaxis.tick_right()
                ax3.set_ylabel("Div(%)", fontsize=8)
                ax3.grid(True, linestyle=':', alpha=0.4)
                ax3.tick_params(axis='y', labelsize=7)
                ax3_div = ax3

            # Pre-cache arrays for O(1) hover access (avoids Polars row() per event)
            _arr_open  = df.get_column("Open").to_numpy()
            _arr_high  = df.get_column("High").to_numpy()
            _arr_low   = df.get_column("Low").to_numpy()
            _arr_close = df.get_column("Close").to_numpy()
            _arr_vol   = df.get_column("Volume").to_numpy() if "Volume" in df.columns else None
            _arr_ma5   = df.get_column("MA5").to_numpy()  if "MA5"  in df.columns else None
            _arr_ma10  = df.get_column("MA10").to_numpy() if "MA10" in df.columns else None
            _arr_ma20  = df.get_column("MA20").to_numpy() if "MA20" in df.columns else None

            _arr_ew    = df.get_column("EqualWeight").to_numpy() if "EqualWeight" in df.columns else None

            cursor3 = None
            cursor4 = None

            # ── mplcursors for ax3 divergence chart ──
            if l_div3 is not None:
                cursor3 = mplcursors.cursor([l_div3], hover=2)
                @cursor3.connect("add")
                def on_add_div(sel,
                               _dates=dates_arr,
                               _close=_arr_close,
                               _div=_arr_div,
                               _ma50=_arr_ma50,
                               _fmt=fmt_str,
                               _cur=currency,
                               _sfx=unit_suffix):
                    try:
                        idx = int(sel.index)
                        n   = len(_dates)
                        if not (0 <= idx < n):
                            sel.annotation.set_visible(False)
                            return
                        date_str = pd.to_datetime(_dates[idx]).strftime("%Y-%m-%d")
                        close_v  = float(_close[idx])
                        div_v    = float(_div[idx])
                        ma50_v   = float(_ma50[idx]) if _ma50 is not None else None

                        if change_mode == 'bp':
                            price_str = f"{close_v:.2f}"
                            ma50_str  = f"{ma50_v:.2f}" if ma50_v is not None else "-"
                        else:
                            price_str = f"{_fmt.format(close_v)}"
                            ma50_str  = f"{_fmt.format(ma50_v)}" if ma50_v is not None else "-"

                        div_str = f"{div_v:.0f}%"

                        W      = max(len(price_str), len(ma50_str), len(div_str), 10)
                        BOX_W  = W + 10
                        txt = (f"{date_str:^{BOX_W}}\n"
                               f"  Price   {price_str:>{W}}\n"
                               f"  MA50    {ma50_str:>{W}}\n"
                               f"  Div(50) {div_str:>{W}}")
                        sel.annotation.set_text(txt)
                        sel.annotation.set_fontfamily("monospace")
                        sel.annotation.set_fontsize(8.5)
                        sel.annotation.set_color(theme.c("text"))
                        sel.annotation.get_bbox_patch().set(
                            fc=theme.c("panel_bg"), alpha=0.93, edgecolor="#2980b9",
                            boxstyle="round,pad=0.5"
                        )
                        sel.annotation.arrow_patch.set(arrowstyle="->", color="#2980b9")
                    except Exception:
                        sel.annotation.set_visible(False)

            # ── mplcursors for ax3 Div(20) line ──
            if l_div4 is not None:
                cursor4 = mplcursors.cursor([l_div4], hover=2)
                @cursor4.connect("add")
                def on_add_div20(sel,
                                 _dates=dates_arr,
                                 _close=_arr_close,
                                 _div20=_arr_div20,
                                 _ma20=_arr_ma20,
                                 _fmt=fmt_str,
                                 _cur=currency,
                                 _sfx=unit_suffix):
                    try:
                        idx = int(sel.index)
                        n   = len(_dates)
                        if not (0 <= idx < n):
                            sel.annotation.set_visible(False)
                            return
                        date_str = pd.to_datetime(_dates[idx]).strftime("%Y-%m-%d")
                        close_v  = float(_close[idx])
                        div_v    = float(_div20[idx])
                        ma20_v   = float(_ma20[idx]) if _ma20 is not None else None

                        if change_mode == 'bp':
                            price_str = f"{close_v:.2f}"
                            ma20_str  = f"{ma20_v:.2f}" if ma20_v is not None else "-"
                        else:
                            price_str = f"{_fmt.format(close_v)}"
                            ma20_str  = f"{_fmt.format(ma20_v)}" if ma20_v is not None else "-"

                        div_str = f"{div_v:.0f}%"

                        W      = max(len(price_str), len(ma20_str), len(div_str), 10)
                        BOX_W  = W + 10
                        txt = (f"{date_str:^{BOX_W}}\n"
                               f"  Price   {price_str:>{W}}\n"
                               f"  MA20    {ma20_str:>{W}}\n"
                               f"  Div(20) {div_str:>{W}}")
                        sel.annotation.set_text(txt)
                        sel.annotation.set_fontfamily("monospace")
                        sel.annotation.set_fontsize(8.5)
                        sel.annotation.set_color(theme.c("text"))
                        sel.annotation.get_bbox_patch().set(
                            fc=theme.c("panel_bg"), alpha=0.93, edgecolor="#e67e22",
                            boxstyle="round,pad=0.5"
                        )
                        sel.annotation.arrow_patch.set(arrowstyle="->", color="#e67e22")
                    except Exception:
                        sel.annotation.set_visible(False)

            cursor_artists = [art for art in [l_close, l_ma5, l_ma10, l_ma20, l_ma50, l_div20_main, l_div50_main] if art is not None]
            if l_ew is not None:
                cursor_artists.append(l_ew)
            cursor = mplcursors.cursor(cursor_artists, hover=2)
            @cursor.connect("add")
            def on_add(sel):
                try:
                    idx = int(sel.index)
                    if 0 <= idx < df.height:
                        date_str = pd.to_datetime(dates_arr[idx]).strftime("%Y-%m-%d")
                        o = float(_arr_open[idx])  if _arr_open  is not None else 0
                        h = float(_arr_high[idx])  if _arr_high  is not None else 0
                        l = float(_arr_low[idx])   if _arr_low   is not None else 0
                        c = float(_arr_close[idx]) if _arr_close is not None else 0
                        v = int(_arr_vol[idx]) if (_arr_vol is not None and _arr_vol[idx] == _arr_vol[idx]) else None
                        v_str = f"{v:,}" if v is not None else "-"

                        is_div = False
                        if l_ma5 is not None and sel.artist == l_ma5:
                            v = float(_arr_ma5[idx]) if _arr_ma5 is not None else 0
                            title = "5-Day MA"
                        elif l_ma10 is not None and sel.artist == l_ma10:
                            v = float(_arr_ma10[idx]) if _arr_ma10 is not None else 0
                            title = "10-Day MA"
                        elif l_ma20 is not None and sel.artist == l_ma20:
                            v = float(_arr_ma20[idx]) if _arr_ma20 is not None else 0
                            title = "20-Day MA"
                        elif l_ma50 is not None and sel.artist == l_ma50:
                            v = float(_arr_ma50[idx]) if _arr_ma50 is not None else 0
                            title = "50-Day MA"
                        elif l_div20_main is not None and sel.artist == l_div20_main:
                            v = float(df.get_column("MA20_Div").to_numpy()[idx])
                            title = "Div(20)"
                            is_div = True
                        elif l_div50_main is not None and sel.artist == l_div50_main:
                            v = float(df.get_column("MA50_Div").to_numpy()[idx])
                            title = "Div(50)"
                            is_div = True
                        elif l_ew is not None and sel.artist == l_ew:
                            v = float(_arr_ew[idx]) if _arr_ew is not None and _arr_ew[idx] == _arr_ew[idx] else None
                            title = "Equal Weight (252650)"
                        else:
                            v = None
                            title = ""

                        if change_mode == 'bp':
                            if v is not None:
                                if is_div:
                                    txt = f"[{date_str}]\n{title}: {v:.1f}%"
                                else:
                                    txt = f"[{date_str}]\n{title}: {fmt_str.format(v)}{unit_suffix}"
                            else:
                                txt = (f"[{date_str}]\n"
                                       f"Open:  {fmt_str.format(o)}{unit_suffix}\n"
                                       f"High:  {fmt_str.format(h)}{unit_suffix}\n"
                                       f"Low:   {fmt_str.format(l)}{unit_suffix}\n"
                                       f"Close: {fmt_str.format(c)}{unit_suffix}\n"
                                       f"Vol:   {v_str}")
                        else:
                            if v is not None:
                                if is_div:
                                    txt = f"[{date_str}]\n{title}: {v:.1f}%"
                                else:
                                    txt = f"[{date_str}]\n{title}: {currency}{fmt_str.format(v)}{unit_suffix}"
                            else:
                                txt = (f"[{date_str}]\n"
                                       f"Open:  {currency}{fmt_str.format(o)}{unit_suffix}\n"
                                       f"High:  {currency}{fmt_str.format(h)}{unit_suffix}\n"
                                       f"Low:   {currency}{fmt_str.format(l)}{unit_suffix}\n"
                                       f"Close: {currency}{fmt_str.format(c)}{unit_suffix}\n"
                                       f"Vol:   {v_str}")

                        sel.annotation.set_text(txt)
                        sel.annotation.set_color(theme.c("text"))
                        sel.annotation.get_bbox_patch().set(fc=theme.c("panel_bg"), alpha=0.9, edgecolor="gray")
                except Exception as e:
                    sel.annotation.set_text("Data load error")

            # Hide tooltips when mouse leaves the axes/figure
            def on_leave_axes(event):
                for c in [cursor, cursor3, cursor4]:
                    if c is not None and hasattr(c, 'selections'):
                        for sel in list(c.selections):
                            c.remove_selection(sel)
                fig.canvas.draw_idle()

            fig.canvas.mpl_connect('axes_leave_event', on_leave_axes)
            fig.canvas.mpl_connect('figure_leave_event', on_leave_axes)

            # Define global bounds for pan/zoom mapping
            abs_min = mdates.date2num(dates_arr[0])
            abs_max = mdates.date2num(dates_arr[-1])
            pad = (abs_max - abs_min) * 0.05
            abs_min_pad = abs_min - pad
            # Do NOT add any right padding -- this prevents future dates from appearing on x-axis
            abs_max_pad = abs_max

            self.scrollbar = QScrollBar(Qt.Orientation.Horizontal)

            def update_scrollbar():
                x_min, x_max = ax1.get_xlim()
                w = x_max - x_min
                denom = (abs_max_pad - abs_min_pad) - w
                if denom <= 0:
                    val = 0
                else:
                    fraction = (x_min - abs_min_pad) / denom
                    val = int(fraction * 10000)
                    val = max(0, min(10000, val))

                self.scrollbar.blockSignals(True)
                self.scrollbar.setRange(0, 10000)
                self.scrollbar.setValue(val)
                self.scrollbar.blockSignals(False)

            def on_scrollbar_change(val):
                x_min, x_max = ax1.get_xlim()
                w = x_max - x_min
                fraction = val / 10000.0
                new_xmin = abs_min_pad + fraction * ((abs_max_pad - abs_min_pad) - w)
                ax1.set_xlim(new_xmin, new_xmin + w)
                fig.canvas.draw_idle()

            self.scrollbar.valueChanged.connect(on_scrollbar_change)

            # --- Pan & Zoom Interactivity ---
            ax1._pan_start = None

            def on_scroll(event):
                valid_axes = [ax for ax in [ax1, ax2, ax3] if ax is not None] + ([ax_rsi] if ax_rsi else [])
                if event.inaxes not in valid_axes or event.xdata is None: return
                x_min, x_max = ax1.get_xlim()

                scale = 1.15
                if event.button == 'up':     # Zoom In
                    factor = 1 / scale
                elif event.button == 'down': # Zoom Out
                    factor = scale
                else: return

                x_focus = event.xdata

                new_left = (x_focus - x_min) * factor
                new_right = (x_max - x_focus) * factor

                new_xmin = x_focus - new_left
                new_xmax = x_focus + new_right

                w = new_xmax - new_xmin

                # Validate maximum width
                if w > (abs_max_pad - abs_min_pad):
                    new_xmin = abs_min_pad
                    new_xmax = abs_max_pad
                else:
                    # Enforce bounds
                    if new_xmin < abs_min_pad:
                        new_xmin = abs_min_pad
                        new_xmax = new_xmin + w
                    if new_xmax > abs_max_pad:
                        new_xmax = abs_max_pad
                        new_xmin = new_xmax - w

                ax1.set_xlim(new_xmin, new_xmax)

                update_scrollbar()
                fig.canvas.draw_idle()

            def on_press(event):
                valid_axes = [ax for ax in [ax1, ax2, ax3] if ax is not None] + ([ax_rsi] if ax_rsi else [])
                if event.button != 1 or event.inaxes not in valid_axes: return
                ax1._pan_start = event.x, ax1.get_xlim()
                cursor.enabled = False

            def on_motion(event):
                if ax1._pan_start is None: return
                xpress, xlim = ax1._pan_start
                dx = event.x - xpress

                bbox = ax1.get_window_extent()
                if bbox.width == 0: return

                dx_data = dx * ((xlim[1] - xlim[0]) / bbox.width)
                new_xmin = xlim[0] - dx_data
                new_xmax = xlim[1] - dx_data

                w = new_xmax - new_xmin
                if new_xmin < abs_min_pad:
                    new_xmin = abs_min_pad
                    new_xmax = new_xmin + w
                if new_xmax > abs_max_pad:
                    new_xmax = abs_max_pad
                    new_xmin = new_xmax - w

                ax1.set_xlim(new_xmin, new_xmax)
                update_scrollbar()
                fig.canvas.draw_idle()

            def on_release(event):
                ax1._pan_start = None
                cursor.enabled = True
                fig.canvas.draw_idle()

            fig.canvas.mpl_connect('scroll_event', on_scroll)
            fig.canvas.mpl_connect('button_press_event', on_press)
            fig.canvas.mpl_connect('motion_notify_event', on_motion)
            fig.canvas.mpl_connect('button_release_event', on_release)

            # Show only the last 1 year initially; right bound clamped to last data date
            end_date = pd.to_datetime(dates_arr[-1])
            start_date = end_date - pd.DateOffset(years=1)
            ax1.set_xlim(mdates.date2num(start_date), abs_max)

            # Let matplotlib autoscale the Y axis for bonds just like other assets
            # so that historical yield ranges are fully visible.

            update_scrollbar()

            # ---MA-only toggle logic ---
            def _toggle_ma_only(checked):
                """Toggle between Short Term (5, 10) and Long Term (20, 50) MAs."""
                if self._div_btn.isChecked():
                    self._div_btn.blockSignals(True)
                    self._div_btn.setChecked(False)
                    self._div_btn.blockSignals(False)
                    if ax_div_main is not None:
                        ax_div_main.set_visible(False)
                        l_div20_main.set_visible(False)
                        l_div50_main.set_visible(False)

                if hasattr(self, '_close_btn') and self._close_btn.isChecked():
                    self._close_btn.blockSignals(True)
                    self._close_btn.setChecked(False)
                    self._close_btn.blockSignals(False)

                if hasattr(self, '_ew_btn') and self._ew_btn.isChecked():
                    self._ew_btn.blockSignals(True)
                    self._ew_btn.setChecked(False)
                    self._ew_btn.blockSignals(False)
                    if ax_ew is not None:
                        ax_ew.set_visible(False)
                        l_ew.set_visible(False)

                if l_ma5 is not None:
                    l_ma5.set_visible(not checked)
                if l_ma10 is not None:
                    l_ma10.set_visible(not checked)

                if l_ma20 is not None:
                    l_ma20.set_visible(checked)
                if l_ma50 is not None:
                    l_ma50.set_visible(checked)

                fig.canvas.draw_idle()

            self._ma_btn.toggled.connect(_toggle_ma_only)

            def _toggle_div(checked):
                if checked:
                    if hasattr(self, '_close_btn') and self._close_btn.isChecked():
                        self._close_btn.blockSignals(True)
                        self._close_btn.setChecked(False)
                        self._close_btn.blockSignals(False)

                    if hasattr(self, '_ew_btn') and self._ew_btn.isChecked():
                        self._ew_btn.blockSignals(True)
                        self._ew_btn.setChecked(False)
                        self._ew_btn.blockSignals(False)
                        if ax_ew is not None:
                            ax_ew.set_visible(False)
                            l_ew.set_visible(False)

                    if l_ma5 is not None: l_ma5.set_visible(False)
                    if l_ma10 is not None: l_ma10.set_visible(False)
                    if l_ma20 is not None: l_ma20.set_visible(False)
                    if l_ma50 is not None: l_ma50.set_visible(False)

                    if ax_div_main is not None:
                        ax_div_main.set_visible(True)
                        l_div20_main.set_visible(True)
                        l_div50_main.set_visible(True)
                else:
                    if ax_div_main is not None:
                        ax_div_main.set_visible(False)
                        l_div20_main.set_visible(False)
                        l_div50_main.set_visible(False)
                    _toggle_ma_only(self._ma_btn.isChecked())

                fig.canvas.draw_idle()

            self._div_btn.toggled.connect(_toggle_div)

            def _toggle_close_only(checked):
                if checked:
                    if self._ma_btn.isChecked():
                        self._ma_btn.blockSignals(True)
                        self._ma_btn.setChecked(False)
                        self._ma_btn.blockSignals(False)
                    if self._div_btn.isChecked():
                        self._div_btn.blockSignals(True)
                        self._div_btn.setChecked(False)
                        self._div_btn.blockSignals(False)
                        if ax_div_main is not None:
                            ax_div_main.set_visible(False)
                            l_div20_main.set_visible(False)
                            l_div50_main.set_visible(False)
                    if hasattr(self, '_ew_btn') and self._ew_btn.isChecked():
                        self._ew_btn.blockSignals(True)
                        self._ew_btn.setChecked(False)
                        self._ew_btn.blockSignals(False)
                        if ax_ew is not None:
                            ax_ew.set_visible(False)
                            l_ew.set_visible(False)

                    if l_ma5 is not None: l_ma5.set_visible(False)
                    if l_ma10 is not None: l_ma10.set_visible(False)
                    if l_ma20 is not None: l_ma20.set_visible(False)
                    if l_ma50 is not None: l_ma50.set_visible(False)
                else:
                    _toggle_ma_only(self._ma_btn.isChecked())
                fig.canvas.draw_idle()

            self._close_btn.toggled.connect(_toggle_close_only)

            def _toggle_ew_only(checked):
                if checked:
                    if self._ma_btn.isChecked():
                        self._ma_btn.blockSignals(True)
                        self._ma_btn.setChecked(False)
                        self._ma_btn.blockSignals(False)
                    if self._div_btn.isChecked():
                        self._div_btn.blockSignals(True)
                        self._div_btn.setChecked(False)
                        self._div_btn.blockSignals(False)
                        if ax_div_main is not None:
                            ax_div_main.set_visible(False)
                            l_div20_main.set_visible(False)
                            l_div50_main.set_visible(False)
                    if hasattr(self, '_close_btn') and self._close_btn.isChecked():
                        self._close_btn.blockSignals(True)
                        self._close_btn.setChecked(False)
                        self._close_btn.blockSignals(False)

                    if l_ma5 is not None: l_ma5.set_visible(False)
                    if l_ma10 is not None: l_ma10.set_visible(False)
                    if l_ma20 is not None: l_ma20.set_visible(False)
                    if l_ma50 is not None: l_ma50.set_visible(False)

                    if ax_ew is not None:
                        ax_ew.set_visible(True)
                        l_ew.set_visible(True)
                else:
                    if ax_ew is not None:
                        ax_ew.set_visible(False)
                        l_ew.set_visible(False)
                    _toggle_ma_only(self._ma_btn.isChecked())
                fig.canvas.draw_idle()

            if hasattr(self, '_ew_btn'):
                self._ew_btn.toggled.connect(_toggle_ew_only)

            # ---Default: Start in Short-Term mode ---
            self._ma_btn.setChecked(False)
            _toggle_ma_only(False)
            # ---
        else:
            ax1.text(0.5, 0.5, "Failed to load detailed data.", ha="center", va="center", transform=ax1.transAxes, color="gray", fontsize=12)

        if change_mode == 'bp':
            scale_layout = QHBoxLayout()
            zoom_in_btn = QPushButton("Y-Axis +")
            zoom_out_btn = QPushButton("Y-Axis -")
            zoom_in_btn.setFixedSize(60, 25)
            zoom_out_btn.setFixedSize(60, 25)
            scale_layout.addStretch()
            scale_layout.addWidget(zoom_out_btn)
            scale_layout.addWidget(zoom_in_btn)
            scale_layout.addSpacing(20)

            def _zoom_y(factor):
                xmin, xmax = ax1.get_xlim()
                ymin, ymax = ax1.get_ylim()
                try:
                    x_data = mdates.date2num(dates_arr)
                    mask = (x_data >= xmin) & (x_data <= xmax)
                    y_data = df.get_column("Close").to_list()
                    y_visible = [float(y) for m, y in zip(mask, y_data) if m and y is not None]
                    y_visible = [y for y in y_visible if not np.isnan(y)]
                    if len(y_visible) > 0:
                        ymid = (min(y_visible) + max(y_visible)) / 2
                    else:
                        ymid = (ymin + ymax) / 2
                except Exception:
                    ymid = (ymin + ymax) / 2

                ydiff = (ymax - ymin) / 2
                ax1.set_ylim(ymid - ydiff * factor, ymid + ydiff * factor)
                fig.canvas.draw_idle()

            zoom_in_btn.clicked.connect(lambda _, f=0.8: _zoom_y(f))
            zoom_out_btn.clicked.connect(lambda _, f=1.2: _zoom_y(f))

            graph_v_layout.addLayout(scale_layout)

        # ---MA button row inserted just above the canvas ---
        _ma_btn_row = QHBoxLayout()
        _ma_btn_row.setContentsMargins(0, 2, 6, 0)
        _ma_btn_row.addStretch()
        if hasattr(self, '_ew_btn'):
            _ma_btn_row.addWidget(self._ew_btn)
        _ma_btn_row.addWidget(self._div_btn)
        _ma_btn_row.addWidget(self._ma_btn)
        _ma_btn_row.addWidget(self._close_btn)
        graph_v_layout.addLayout(_ma_btn_row)

        canvas = FigureCanvas(fig)
        graph_v_layout.addWidget(canvas)

        if hasattr(self, 'scrollbar'):
            graph_v_layout.addWidget(self.scrollbar)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn, alignment=Qt.AlignmentFlag.AlignRight)

    def closeEvent(self, event):
        """Release the matplotlib figure from memory when the dialog is closed."""
        import matplotlib.pyplot as plt
        try:
            plt.close("all")
        except Exception:
            logger.debug("plt.close('all') failed on dialog close", exc_info=True)
        super().closeEvent(event)


# ---------------------------------------------------------------------------
# BuyEditDialog — Edit buy details in TradingHistoryTab
# ---------------------------------------------------------------------------
class BuyEditDialog(QDialog):
    """Dialog for editing buy details in TradingHistoryTab."""
    def __init__(self, current_data, parent=None):
        super().__init__(parent)
        _fmt_num_edit, _validate_date_str, _validate_positive_number, _mk_field_validator = _get_validators()
        self.setWindowTitle("Edit Buy Information")
        self.setMinimumWidth(300)
        self.result_data = None

        layout = QFormLayout(self)

        self.buy_date_edit = QLineEdit(current_data.get("buy_date", ""))
        self.buy_date_edit.setPlaceholderText("YYYY-MM-DD")

        b_price = current_data.get("buy_price", 0.0)
        self.buy_price_edit = QLineEdit(f"{b_price:,.2f}" if b_price else "")

        b_qty = current_data.get("qty", 0.0)
        self.buy_qty_edit = QLineEdit(f"{b_qty:,.0f}" if b_qty else "")

        b_amt = current_data.get("buy_amount", 0.0)
        self.buy_amount_edit = QLineEdit(f"{b_amt:,.2f}" if b_amt else "")
        self.buy_amount_edit.setPlaceholderText("Auto-calculated if empty")

        layout.addRow("Buy Date:", self.buy_date_edit)
        layout.addRow("Buy Price:", self.buy_price_edit)
        layout.addRow("Quantity:", self.buy_qty_edit)
        layout.addRow("Buy Amount:", self.buy_amount_edit)

        btn_box = QHBoxLayout()
        save_btn = QPushButton("Save")
        save_btn.clicked.connect(self.on_save)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)

        save_btn.setStyleSheet("background-color: #1a6b3c; color: white; padding: 5px;")
        btn_box.addWidget(save_btn)
        btn_box.addWidget(cancel_btn)

        # ---1,000-separator auto-format ---
        self.buy_price_edit.textEdited.connect(
            lambda t: _fmt_num_edit(self.buy_price_edit, t, decimal=True))
        self.buy_qty_edit.textEdited.connect(
            lambda t: _fmt_num_edit(self.buy_qty_edit, t))
        self.buy_amount_edit.textEdited.connect(
            lambda t: _fmt_num_edit(self.buy_amount_edit, t, decimal=True))

        # ---Real-time validation: red border + tooltip on bad input (roadmap 2-5) ---
        self._val_date = _mk_field_validator(
            self.buy_date_edit, _validate_date_str, "날짜 형식이 올바르지 않습니다 (YYYY-MM-DD)")
        self._val_price = _mk_field_validator(
            self.buy_price_edit, _validate_positive_number, "0보다 큰 숫자를 입력하세요")
        self._val_qty = _mk_field_validator(
            self.buy_qty_edit, _validate_positive_number, "0보다 큰 숫자를 입력하세요")
        self.buy_date_edit.textChanged.connect(self._val_date)
        self.buy_price_edit.textChanged.connect(self._val_price)
        self.buy_qty_edit.textChanged.connect(self._val_qty)
        # Validate pre-filled values immediately so existing bad data is flagged on open.
        self._val_date(); self._val_price(); self._val_qty()

        layout.addRow(btn_box)

    def on_save(self):
        date_ok = self._val_date()
        price_ok = self._val_price()
        qty_ok = self._val_qty()
        if not (date_ok and price_ok and qty_ok):
            QMessageBox.warning(self, "Input Error", "입력값을 확인하세요 (빨간 테두리로 표시된 항목).")
            return

        def to_f(val):
            try: return float(val.replace(',', '').replace('%', '').strip())
            except Exception: return 0.0

        b_price = to_f(self.buy_price_edit.text())
        b_qty = to_f(self.buy_qty_edit.text())
        b_amt = to_f(self.buy_amount_edit.text())

        self.result_data = {
            "buy_date": self.buy_date_edit.text().strip(),
            "buy_price": b_price,
            "qty": b_qty,
            "buy_amount": b_amt if b_amt > 0 else b_price * b_qty,
        }
        self.accept()


# ---------------------------------------------------------------------------
# SellEditDialog — Edit sell details in TradingHistoryTab
# ---------------------------------------------------------------------------
class SellEditDialog(QDialog):
    """Dialog for editing sell details in TradingHistoryTab."""
    def __init__(self, current_data, parent=None):
        super().__init__(parent)
        _fmt_num_edit, _validate_date_str, _validate_positive_number, _mk_field_validator = _get_validators()
        self.setWindowTitle("Edit Sell Information")
        self.setMinimumWidth(300)
        self.result_data = None
        self._buy_date_str = current_data.get("buy_date", "")

        layout = QFormLayout(self)

        self.sell_date_edit = QLineEdit(current_data.get("sell_date", ""))
        self.sell_date_edit.setPlaceholderText("YYYY-MM-DD (Leave empty if Open)")

        s_price = current_data.get("sell_price", 0.0)
        self.sell_price_edit = QLineEdit(f"{s_price:,.0f}" if s_price else "")

        s_qty = current_data.get("sell_qty", 0.0)
        self.sell_qty_edit = QLineEdit(f"{s_qty:,.0f}" if s_qty else "")

        s_amt = current_data.get("sell_amount", 0.0)
        self.sell_amount_edit = QLineEdit(f"{s_amt:,.0f}" if s_amt else "")
        self.sell_amount_edit.setPlaceholderText("Auto-calculated if empty")

        layout.addRow("Sell Date:", self.sell_date_edit)
        layout.addRow("Sell Price:", self.sell_price_edit)
        layout.addRow("Sell Quantity:", self.sell_qty_edit)
        layout.addRow("Sell Amount:", self.sell_amount_edit)

        btn_box = QHBoxLayout()
        save_btn = QPushButton("Save")
        save_btn.clicked.connect(self.on_save)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)

        save_btn.setStyleSheet("background-color: #d35400; color: white; padding: 5px;")
        btn_box.addWidget(save_btn)
        btn_box.addWidget(cancel_btn)

        # ---1,000-separator auto-format ---
        self.sell_price_edit.textEdited.connect(
            lambda t: _fmt_num_edit(self.sell_price_edit, t))
        self.sell_qty_edit.textEdited.connect(
            lambda t: _fmt_num_edit(self.sell_qty_edit, t))
        self.sell_amount_edit.textEdited.connect(
            lambda t: _fmt_num_edit(self.sell_amount_edit, t))

        # ---Real-time validation (roadmap 2-5) ---
        # sell_date may be left empty (position stays open); price/qty are only
        # required once a sell_date is actually entered.
        def _validate_sell_date(text: str) -> bool:
            text = text.strip()
            return True if not text else _validate_date_str(text)

        def _validate_sell_amount_field(text: str) -> bool:
            text = text.replace(",", "").replace("%", "").strip()
            required = bool(self.sell_date_edit.text().strip())
            if not text:
                return not required
            try:
                return float(text) > 0
            except ValueError:
                return False

        self._val_sell_date = _mk_field_validator(
            self.sell_date_edit, _validate_sell_date, "날짜 형식이 올바르지 않습니다 (YYYY-MM-DD)")
        self._val_sell_price = _mk_field_validator(
            self.sell_price_edit, _validate_sell_amount_field, "매도일 입력 시 0보다 큰 숫자가 필요합니다")
        self._val_sell_qty = _mk_field_validator(
            self.sell_qty_edit, _validate_sell_amount_field, "매도일 입력 시 0보다 큰 숫자가 필요합니다")
        self.sell_date_edit.textChanged.connect(self._val_sell_date)
        self.sell_date_edit.textChanged.connect(self._val_sell_price)
        self.sell_date_edit.textChanged.connect(self._val_sell_qty)
        self.sell_price_edit.textChanged.connect(self._val_sell_price)
        self.sell_qty_edit.textChanged.connect(self._val_sell_qty)
        self._val_sell_date(); self._val_sell_price(); self._val_sell_qty()

        layout.addRow(btn_box)

    def on_save(self):
        date_ok = self._val_sell_date()
        price_ok = self._val_sell_price()
        qty_ok = self._val_sell_qty()
        if not (date_ok and price_ok and qty_ok):
            QMessageBox.warning(self, "Input Error", "입력값을 확인하세요 (빨간 테두리로 표시된 항목).")
            return

        sell_date_str = self.sell_date_edit.text().strip()
        if sell_date_str and self._buy_date_str:
            try:
                buy_d = _dt.datetime.strptime(self._buy_date_str, "%Y-%m-%d").date()
                sell_d = _dt.datetime.strptime(sell_date_str, "%Y-%m-%d").date()
                if buy_d > sell_d:
                    QMessageBox.warning(
                        self, "Input Error",
                        f"매수일({self._buy_date_str})이 매도일({sell_date_str})보다 늦을 수 없습니다.",
                    )
                    return
            except ValueError:
                pass  # buy_date on the record predates this validation; skip the cross-check

        def to_f(val):
            try: return float(val.replace(',', '').replace('%', '').strip())
            except Exception: return 0.0

        s_price = to_f(self.sell_price_edit.text())
        s_qty = to_f(self.sell_qty_edit.text())
        s_amt = to_f(self.sell_amount_edit.text())

        self.result_data = {
            "sell_date": sell_date_str,
            "sell_price": s_price,
            "sell_qty": s_qty,
            "sell_amount": s_amt,
        }
        self.accept()


# ---------------------------------------------------------------------------
# TradeEntryDialog — Enter a new trade into TradingHistoryTab
# ---------------------------------------------------------------------------
class TradeEntryDialog(QDialog):
    """Dialog for entering a new trade into TradingHistoryTab."""
    def __init__(self, parent=None):
        super().__init__(parent)
        _fmt_num_edit, _validate_date_str, _validate_positive_number, _mk_field_validator = _get_validators()
        self.setWindowTitle("Add New Trade")
        self.setMinimumWidth(300)
        self.result_data = None

        layout = QFormLayout(self)

        self.market_combo = QComboBox()
        self.market_combo.addItems(["KOSPI", "KOSDAQ", "NASDAQ", "NASDAQ 100", "NYSE", "AMEX"])

        self.ticker_edit = QLineEdit()
        self.ticker_edit.setPlaceholderText("Enter Ticker (e.g. QQQM)")

        self.buy_date_edit = QLineEdit(datetime.now().strftime("%Y-%m-%d"))
        self.buy_date_edit.setPlaceholderText("YYYY-MM-DD")
        self.buy_price_edit = QLineEdit()
        self.qty_edit = QLineEdit()
        self.buy_amount_edit = QLineEdit()
        self.buy_amount_edit.setPlaceholderText("Auto-calculated if empty")

        layout.addRow("Market:", self.market_combo)
        layout.addRow("Ticker:", self.ticker_edit)
        layout.addRow("Buy Date:", self.buy_date_edit)
        layout.addRow("Buy Price:", self.buy_price_edit)
        layout.addRow("Buy Quantity:", self.qty_edit)
        layout.addRow("Buy Amount:", self.buy_amount_edit)

        # ---1,000-separator auto-format ---
        self.buy_price_edit.textEdited.connect(
            lambda t: _fmt_num_edit(self.buy_price_edit, t, decimal=True))
        self.qty_edit.textEdited.connect(
            lambda t: _fmt_num_edit(self.qty_edit, t))
        self.buy_amount_edit.textEdited.connect(
            lambda t: _fmt_num_edit(self.buy_amount_edit, t, decimal=True))

        # ---Real-time validation (roadmap 2-5) ---
        self._val_date = _mk_field_validator(
            self.buy_date_edit, _validate_date_str, "날짜 형식이 올바르지 않습니다 (YYYY-MM-DD)")
        self._val_price = _mk_field_validator(
            self.buy_price_edit, _validate_positive_number, "0보다 큰 숫자를 입력하세요")
        self._val_qty = _mk_field_validator(
            self.qty_edit, _validate_positive_number, "0보다 큰 숫자를 입력하세요")
        self.buy_date_edit.textChanged.connect(self._val_date)
        self.buy_price_edit.textChanged.connect(self._val_price)
        self.qty_edit.textChanged.connect(self._val_qty)
        self._val_date(); self._val_price(); self._val_qty()

        btn_box = QHBoxLayout()
        save_btn = QPushButton("Save")
        save_btn.clicked.connect(self.on_save)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)

        save_btn.setStyleSheet(f"background-color: {theme.c('accent')}; color: white; padding: 5px;")
        btn_box.addWidget(save_btn)
        btn_box.addWidget(cancel_btn)

        layout.addRow(btn_box)

    def on_save(self):
        market = self.market_combo.currentText()
        ticker = self.ticker_edit.text().strip().upper()
        if not ticker:
            QMessageBox.warning(self, "Error", "Ticker is required.")
            return

        date_ok = self._val_date()
        price_ok = self._val_price()
        qty_ok = self._val_qty()
        if not (date_ok and price_ok and qty_ok):
            QMessageBox.warning(self, "Input Error", "입력값을 확인하세요 (빨간 테두리로 표시된 항목).")
            return

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            from data_fetcher import fetch_single_stock
            res, err = fetch_single_stock(market, ticker)

            is_valid = False
            company = ticker

            if res is not None:
                is_valid = True
                company = res.get("name", ticker)

            if not is_valid or company == ticker or company.upper() == ticker.upper():
                try:
                    from yahooquery import Ticker as YQTicker
                    yf_sym = ticker
                    if market == "KOSPI": yf_sym = f"{ticker}.KS"
                    elif market == "KOSDAQ": yf_sym = f"{ticker}.KQ"
                    elif "." in ticker: yf_sym = ticker.replace(".", "-")

                    qt = YQTicker(yf_sym).quote_type
                    if qt and isinstance(qt, dict) and yf_sym in qt and isinstance(qt[yf_sym], dict):
                        fetched = qt[yf_sym].get('longName') or qt[yf_sym].get('shortName')
                        if fetched:
                            company = fetched
                            is_valid = True
                except Exception:
                    logger.debug("yahooquery company-name lookup failed for ticker=%s", ticker, exc_info=True)

            if (not is_valid or company == ticker or company.upper() == ticker.upper()) and market in ("KOSPI", "KOSDAQ"):
                try:
                    from data_fetcher import _fetch_naver_info
                    n_nv, _ = _fetch_naver_info(ticker)
                    if n_nv:
                        company = n_nv
                        is_valid = True
                except Exception:
                    logger.debug("Naver company-name lookup failed for ticker=%s", ticker, exc_info=True)

            if not is_valid:
                QApplication.restoreOverrideCursor()
                QMessageBox.warning(self, "Input Error", f"Ticker is not valid (Could not find stock information):\n{ticker}\n\nDetails: {err or ''}")
                return

        except Exception as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(self, "Error", f"Error checking ticker:\n{e}")
            return
        finally:
            QApplication.restoreOverrideCursor()

        def to_f(val):
            try: return float(val.replace(',', '').replace('%', '').strip())
            except Exception: return 0.0

        b_price = to_f(self.buy_price_edit.text())
        qty = to_f(self.qty_edit.text())
        b_amt = to_f(self.buy_amount_edit.text())

        self.result_data = {
            "market": market,
            "ticker": ticker,
            "company": company,
            "buy_date": self.buy_date_edit.text().strip(),
            "buy_price": b_price,
            "qty": qty,
            "buy_amount": b_amt,
            "sell_date": "",
            "sell_price": 0.0,
            "sell_qty": 0.0,
            "sell_amount": 0.0,
        }
        self.accept()


# ---------------------------------------------------------------------------
# StockTradeHistoryDialog — Detailed trade history for a single stock
# ---------------------------------------------------------------------------
class StockTradeHistoryDialog(QDialog):
    def __init__(self, company, matches, total_pl, total_buy, total_sell, parent=None, is_open_position=False):
        super().__init__(parent)
        title = f"[{company}] Current Holdings" if is_open_position else f"[{company}] Detailed Trading History"
        self.setWindowTitle(title)
        self.resize(1000, 600)

        layout = QVBoxLayout(self)

        # Table
        tbl = QTableWidget()
        if is_open_position:
            cols = ["Buy Date", "Buy Price", "Buy Q'ty", "Buy Amt", "Status", "Current Price", "Current Q'ty", "Current Amt", "P/L", "P/L(%)"]
        else:
            cols = ["Buy Date", "Buy Price", "Buy Q'ty", "Buy Amt", "Days", "Sell Date", "Sell Price", "Sell Q'ty", "Sell Amt", "P/L", "P/L(%)"]
        tbl.setColumnCount(len(cols))
        tbl.setHorizontalHeaderLabels(cols)
        # open: data rows + total row + weight row; closed: data rows + total row
        tbl.setRowCount(len(matches) + (2 if is_open_position else 1))
        tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        tbl.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)

        # Custom vertical header: data rows show row numbers; footer rows appear blank.
        _n_data = len(matches)
        _footer_rows = ({_n_data, _n_data + 1} if is_open_position else {_n_data})

        class _FooterBlankVHeader(QHeaderView):
            def paintSection(self, painter, rect, logical_index):
                if logical_index in _footer_rows:
                    painter.save()
                    painter.fillRect(rect, QColor(theme.c("table_bg")))
                    painter.restore()
                else:
                    super().paintSection(painter, rect, logical_index)

        _vh = _FooterBlankVHeader(Qt.Orientation.Vertical, tbl)
        _vh.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        _vh.setDefaultSectionSize(tbl.verticalHeader().defaultSectionSize())
        tbl.setVerticalHeader(_vh)

        def _si(text):
            it = QTableWidgetItem(str(text))
            it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            return it

        def _ni(val, fmt="{:,.0f}"):
            it = QTableWidgetItem(fmt.format(val))
            it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            return it

        for r, rec in enumerate(matches):
            tbl.setItem(r, 0, _si(rec.get("buy_date", "")))
            tbl.setItem(r, 1, _ni(rec.get("buy_price", 0)))
            tbl.setItem(r, 2, _ni(rec.get("qty", 0)))
            tbl.setItem(r, 3, _ni(rec.get("buy_amount", 0)))

            if is_open_position:
                # Open: col4=Status, col5=CurrPrice, col6=CurrQty, col7=CurrAmt, col8=P/L, col9=P/L%
                tbl.setItem(r, 4, _si("Open"))
                curr_price = rec.get("curr_price", 0)
                qty = rec.get("qty", 0)
                tbl.setItem(r, 5, _ni(curr_price))
                tbl.setItem(r, 6, _ni(qty))
                tbl.setItem(r, 7, _ni(curr_price * qty))
                pl_col, pct_col = 8, 9
                pl_val = rec.get("curr_pl", 0.0)
                pl_pct_val = rec.get("curr_pl_pct", 0.0)
            else:
                # Closed: col4=Days, col5=SellDate, col6=SellPrice, col7=SellQty, col8=SellAmt, col9=P/L, col10=P/L%
                d = int(rec.get("days_held", 0) or 0)
                days_it = QTableWidgetItem(str(d) if d > 0 else "-")
                days_it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                days_it.setForeground(QColor("#888888"))
                tbl.setItem(r, 4, days_it)
                tbl.setItem(r, 5, _si(rec.get("sell_date", "")))
                tbl.setItem(r, 6, _ni(rec.get("sell_price", 0)))
                tbl.setItem(r, 7, _ni(rec.get("sell_qty", 0)))
                tbl.setItem(r, 8, _ni(rec.get("sell_amount", 0)))
                pl_col, pct_col = 9, 10
                pl_val = rec.get("pl", 0.0)
                pl_pct_val = rec.get("pl_pct", 0.0)

            pl_it = _ni(pl_val)
            if pl_val > 0: pl_it.setForeground(QColor("#c0392b"))
            elif pl_val < 0: pl_it.setForeground(QColor("#2980b9"))
            tbl.setItem(r, pl_col, pl_it)

            pl_pct_it = _ni(pl_pct_val, "{:+.1f}%")
            if pl_pct_val > 0: pl_pct_it.setForeground(QColor("#c0392b"))
            elif pl_pct_val < 0: pl_pct_it.setForeground(QColor("#2980b9"))
            tbl.setItem(r, pct_col, pl_pct_it)

        # ---Total row (always present for both open and closed) ---
        total_row = len(matches)
        bold_font = tbl.item(0, 0).font() if tbl.rowCount() > 0 else tbl.font()
        bold_font.setBold(True)

        def _bold_label(text, align=Qt.AlignmentFlag.AlignCenter):
            it = QTableWidgetItem(text)
            it.setTextAlignment(align)
            it.setFont(bold_font)
            return it

        def _bold_ni(val, fmt="{:,.0f}"):
            it = _ni(val, fmt)
            it.setFont(bold_font)
            return it

        if is_open_position:
            # Span cols 0-2 for "Total" label
            tbl.setSpan(total_row, 0, 1, 3)
            tbl.setItem(total_row, 0, _bold_label("Total"))
            # Buy Amt (col 3)
            tbl.setItem(total_row, 3, _bold_ni(total_buy))
            # Status col blank
            tbl.setItem(total_row, 4, QTableWidgetItem(""))
            # Current Price / Current Q'ty blank
            tbl.setItem(total_row, 5, QTableWidgetItem(""))
            tbl.setItem(total_row, 6, QTableWidgetItem(""))
            # Current Amt (col 7)
            tbl.setItem(total_row, 7, _bold_ni(total_sell))
            # P/L (col 8)
            pl_tot_it = _bold_ni(total_pl)
            if total_pl > 0: pl_tot_it.setForeground(QColor("#c0392b"))
            elif total_pl < 0: pl_tot_it.setForeground(QColor("#2980b9"))
            tbl.setItem(total_row, 8, pl_tot_it)
            # P/L % (col 9)
            if total_buy > 0:
                total_pct = (total_pl / total_buy) * 100
                pct_tot_it = _bold_ni(total_pct, "{:+.1f}%")
                if total_pct > 0: pct_tot_it.setForeground(QColor("#c0392b"))
                elif total_pct < 0: pct_tot_it.setForeground(QColor("#2980b9"))
            else:
                pct_tot_it = QTableWidgetItem("")
                pct_tot_it.setFont(bold_font)
            tbl.setItem(total_row, 9, pct_tot_it)
            tbl.setVerticalHeaderItem(total_row, QTableWidgetItem(""))  # hide row number
        else:
            # Closed: 11 cols - span 0-3 for "Total", col4(Days) blank, col5-8 sell section, col9=P/L, col10=P/L%
            tbl.setSpan(total_row, 0, 1, 4)
            tbl.setItem(total_row, 0, _bold_label("Total"))
            tbl.setItem(total_row, 4, QTableWidgetItem(""))  # Days blank
            tbl.setSpan(total_row, 5, 1, 3)
            tbl.setItem(total_row, 5, QTableWidgetItem(""))
            tbl.setItem(total_row, 8, _bold_ni(total_sell))
            total_pl_it = _bold_ni(total_pl)
            if total_pl > 0: total_pl_it.setForeground(QColor("#c0392b"))
            elif total_pl < 0: total_pl_it.setForeground(QColor("#2980b9"))
            tbl.setItem(total_row, 9, total_pl_it)
            if total_buy > 0:
                total_pct = (total_pl / total_buy) * 100
                pct_it = _bold_ni(total_pct, "{:+.1f}%")
                if total_pct > 0: pct_it.setForeground(QColor("#c0392b"))
                elif total_pct < 0: pct_it.setForeground(QColor("#2980b9"))
            else:
                pct_it = QTableWidgetItem("")
                pct_it.setFont(bold_font)
            tbl.setItem(total_row, 10, pct_it)
            tbl.setVerticalHeaderItem(total_row, QTableWidgetItem(""))  # hide row number

        tbl.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)

        if not is_open_position:
            # Closed position: draw outline border around Total row via delegate.
            _closed_total_row = total_row
            _closed_n_cols = tbl.columnCount()
            _closed_grid_clr = QColor("#d0d0d0")

            class _ClosedDelegate(QStyledItemDelegate):
                def paint(self, painter, option, index):
                    super().paint(painter, option, index)
                    r = option.rect
                    row = index.row()
                    col = index.column()
                    if row != _closed_total_row:
                        # Normal grid lines for data rows
                        painter.save()
                        painter.setPen(QPen(_closed_grid_clr, 1))
                        painter.drawLine(r.left(), r.bottom(), r.right(), r.bottom())
                        if col < _closed_n_cols - 1:
                            painter.drawLine(r.right(), r.top(), r.right(), r.bottom())
                        painter.restore()
                    else:
                        # Outline border around the entire Total row
                        painter.save()
                        painter.setPen(QPen(_closed_grid_clr, 1))
                        # painter.drawLine(r.left(), r.top(), r.right(), r.top())        # top
                        painter.drawLine(r.left(), r.bottom(), r.right(), r.bottom())  # bottom
                        if col == 0:
                            painter.drawLine(r.left() + 1, r.top(), r.left() + 1, r.bottom())  # left
                        if col == _closed_n_cols - 1:
                            painter.drawLine(r.right(), r.top(), r.right(), r.bottom()) # right
                        painter.restore()

            tbl.setShowGrid(False)
            tbl._closed_delegate = _ClosedDelegate(tbl)
            tbl.setItemDelegate(tbl._closed_delegate)

        if is_open_position:
            # Weight row: weight % in col 7 (Current Amt), all other cols blank
            weight_row = total_row + 1
            total_w = sum(rec.get("position_w", 0.0) for rec in matches)
            for c in range(10):
                tbl.setItem(weight_row, c, QTableWidgetItem(""))
            w_it = _bold_ni(total_w, "{:.1f}%")
            w_it.setForeground(QColor("#555555"))
            tbl.setItem(weight_row, 7, w_it)
            tbl.setVerticalHeaderItem(weight_row, QTableWidgetItem(""))  # hide row number

            # Remove borders from footer rows (Total + Weight):
            # hide the built-in grid and re-draw only for data rows via delegate.
            _footer_rows = {total_row, weight_row}
            _grid_clr = QColor("#d0d0d0")
            _n_cols = tbl.columnCount()

            class _PartialGridDelegate(QStyledItemDelegate):
                def paint(self, painter, option, index):
                    super().paint(painter, option, index)
                    r = option.rect
                    row = index.row()
                    col = index.column()
                    if row not in _footer_rows:
                        # Draw normal grid lines for data rows
                        painter.save()
                        painter.setPen(QPen(_grid_clr, 1))
                        painter.drawLine(r.left(), r.bottom(), r.right(), r.bottom())
                        if col < _n_cols - 1:
                            painter.drawLine(r.right(), r.top(), r.right(), r.bottom())
                        painter.restore()
                    elif row == total_row:
                        # Draw outline border around the entire Total row
                        painter.save()
                        painter.setPen(QPen(_grid_clr, 1))
                        # painter.drawLine(r.left(), r.top(), r.right(), r.top())       # top
                        painter.drawLine(r.left(), r.bottom(), r.right(), r.bottom()) # bottom
                        if col == 0:
                            painter.drawLine(r.left() + 1, r.top(), r.left() + 1, r.bottom()) # left edge
                        if col == _n_cols - 1:
                            painter.drawLine(r.right(), r.top(), r.right(), r.bottom()) # right edge
                        painter.restore()

            tbl.setShowGrid(False)
            tbl._partial_grid_delegate = _PartialGridDelegate(tbl)
            tbl.setItemDelegate(tbl._partial_grid_delegate)

        layout.addWidget(tbl)

        close_btn = QPushButton("Close")
        close_btn.setFixedHeight(30)
        close_btn.setFixedWidth(100)
        close_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {theme.c('accent')};
                color: white;
                border-radius: 4px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: {theme.c('accent_hover')};
            }}
        """)
        close_btn.clicked.connect(self.accept)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        btn_layout.addWidget(close_btn)
        layout.addLayout(btn_layout)

        # ---Determine scroll bar visibility based on content height ---
        # Compare total content height against the available table viewport height.
        hdr_h = tbl.horizontalHeader().height()
        row_h = tbl.verticalHeader().defaultSectionSize()
        n_rows = tbl.rowCount()
        content_h = hdr_h + row_h * n_rows + 4  # +4 for border
        CLOSE_BTN_H = 44  # close button row + margins
        PADDING = 20      # dialog layout margins
        available_tbl_h = 600 - CLOSE_BTN_H - PADDING
        if content_h <= available_tbl_h:
            tbl.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        else:
            tbl.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setFixedSize(1000, 600)


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

        line_kospi, = ax.plot(x_dates, kospi_returns, color="#8e44ad", linewidth=2, linestyle="-", marker=".", markersize=4, label="KOSPI")
        line_asset, = ax.plot(x_dates, asset_returns, color="#c0392b", linewidth=2, linestyle="-", marker=".", markersize=4, label="Total Assets")
        line_usd, = ax.plot(x_dates, usd_asset_returns, color="#2980b9", linewidth=2, linestyle="-", marker=".", markersize=4, label="Total Assets ($)")

        ax.axhline(0, color='gray', linestyle='--', linewidth=1)

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
                        fontsize=8, color="#c0392b", fontweight="bold")

            # Total Assets (USD)
            amt_usd = usd_totals[i]
            ret_usd = usd_asset_returns[i]
            ax.annotate(f"{ret_usd:+.1f}%\n(${amt_usd:,.0f})", (x_dates[i], ret_usd),
                        textcoords="offset points", xytext=(0, -25), ha='center',
                        fontsize=8, color="#2980b9", fontweight="bold")

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
                sel.annotation.set_color(theme.c("text"))
                sel.annotation.get_bbox_patch().set(fc=theme.c("panel_bg"), alpha=0.9, edgecolor="gray")

        canvas = FigureCanvas(fig)
        layout.addWidget(canvas)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn, alignment=Qt.AlignmentFlag.AlignRight)
