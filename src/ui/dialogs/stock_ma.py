"""ui/dialogs/stock_ma.py — StockMaDialog — per-stock MA/RSI/divergence chart with investor-trend and financials tabs.

Split out of the former single ui/dialogs.py (2026-09-17)."""
import logging

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QScrollBar,
    QTableWidget, QTableWidgetItem, QHeaderView, QSplitter, QWidget,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont

import numpy as np
import pandas as pd
import matplotlib.dates as mdates
import mplcursors
from matplotlib.figure import Figure
from matplotlib.collections import PolyCollection
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas

logger = logging.getLogger(__name__)

from ui.common import (
    create_font,
)


# ---------------------------------------------------------------------------
# StockMaDialog — Stock MA chart dialog (Close + MA20 + MA50)
# ---------------------------------------------------------------------------
class StockMaDialog(QDialog):
    """Dialog showing Close + 20-Day MA + 50-Day MA for a single stock."""

    def __init__(self, ticker, name, market, df, investor_data=None, parent=None, change_mode='pct'):
        super().__init__(parent)
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

            def _make_ma_div_hover_handler(dates, close, div_arr, ma_arr, fmt_str, ma_label, div_label, edge_color):
                """Builds an mplcursors 'add' handler for an MA-divergence hover
                line (Div(50)/MA50 or Div(20)/MA20 -- only the arrays/labels/color differ)."""
                def _handler(sel, _dates=dates, _close=close, _div=div_arr, _ma=ma_arr, _fmt=fmt_str):
                    try:
                        idx = int(sel.index)
                        n   = len(_dates)
                        if not (0 <= idx < n):
                            sel.annotation.set_visible(False)
                            return
                        date_str = pd.to_datetime(_dates[idx]).strftime("%Y-%m-%d")
                        close_v  = float(_close[idx])
                        div_v    = float(_div[idx])
                        ma_v     = float(_ma[idx]) if _ma is not None else None

                        if change_mode == 'bp':
                            price_str = f"{close_v:.2f}"
                            ma_str    = f"{ma_v:.2f}" if ma_v is not None else "-"
                        else:
                            price_str = f"{_fmt.format(close_v)}"
                            ma_str    = f"{_fmt.format(ma_v)}" if ma_v is not None else "-"

                        div_str = f"{div_v:.0f}%"

                        W      = max(len(price_str), len(ma_str), len(div_str), 10)
                        BOX_W  = W + 10
                        txt = (f"{date_str:^{BOX_W}}\n"
                               f"  {'Price':<8}{price_str:>{W}}\n"
                               f"  {ma_label:<8}{ma_str:>{W}}\n"
                               f"  {div_label:<8}{div_str:>{W}}")
                        sel.annotation.set_text(txt)
                        sel.annotation.set_fontfamily("monospace")
                        sel.annotation.set_fontsize(8.5)
                        sel.annotation.get_bbox_patch().set(
                            fc="white", alpha=0.93, edgecolor=edge_color,
                            boxstyle="round,pad=0.5"
                        )
                        sel.annotation.arrow_patch.set(arrowstyle="->", color=edge_color)
                    except Exception:
                        logger.debug("MA divergence hover tooltip render failed", exc_info=True)
                        sel.annotation.set_visible(False)
                return _handler

            # ── mplcursors for ax3 Div(50) line ──
            if l_div3 is not None:
                cursor3 = mplcursors.cursor([l_div3], hover=2)
                cursor3.connect("add", _make_ma_div_hover_handler(
                    dates_arr, _arr_close, _arr_div, _arr_ma50, fmt_str, "MA50", "Div(50)", "#2980b9"))

            # ── mplcursors for ax3 Div(20) line ──
            if l_div4 is not None:
                cursor4 = mplcursors.cursor([l_div4], hover=2)
                cursor4.connect("add", _make_ma_div_hover_handler(
                    dates_arr, _arr_close, _arr_div20, _arr_ma20, fmt_str, "MA20", "Div(20)", "#e67e22"))

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
                        sel.annotation.get_bbox_patch().set(fc="white", alpha=0.9, edgecolor="gray")
                except Exception:
                    logger.debug("Main chart hover tooltip render failed", exc_info=True)
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
