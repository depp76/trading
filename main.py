import sys
import gc
import json
import traceback
import os
import shutil
import datetime as _dt
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dotenv import load_dotenv
from datetime import datetime
import pandas as pd
import polars as pl
import numpy as np
import trade_db
import gemini_helper
from matplotlib.collections import PolyCollection
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QTableWidget, QTableWidgetItem, QLineEdit, QPushButton,
    QLabel, QHeaderView, QTabWidget, QComboBox, QMessageBox,
    QDialog, QScrollArea, QFrame, QProgressBar, QFormLayout,
    QCheckBox, QScrollBar, QFileDialog, QSizePolicy, QSplitter,
    QStyleOptionHeader, QInputDialog, QStyledItemDelegate
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QPoint, QRect, QSettings, QTimer
from PyQt6.QtGui import QColor, QFont, QPolygon, QPainter, QPen, QShortcut, QKeySequence

def create_font(size: int = 10, weight: QFont.Weight = QFont.Weight.Normal, style_name: str = None) -> QFont:
    font = QFont()
    font.setFamilies(["Malgun Gothic Semilight", "맑은 고딕 Semilight", "Malgun Gothic"])
    font.setPointSize(size)
    if weight == QFont.Weight.Bold:
        font.setWeight(QFont.Weight.Bold)
    else:
        font.setWeight(QFont.Weight.Light)
        font.setStyleName(style_name or "Semilight")
    return font

load_dotenv()

import logging
logging.getLogger('matplotlib.font_manager').setLevel(logging.ERROR)

# ── Structured logging setup ──────────────────────────────────────────────────
# Both file and console handlers are set to WARNING so only genuine API
# failures (not normal fallback-chain silences) surface during runtime.
# To debug: change _file_handler.setLevel(logging.DEBUG) at the bottom.
_log_formatter = logging.Formatter(
    "%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
_file_handler = logging.FileHandler("app.log", encoding="utf-8")
_file_handler.setLevel(logging.WARNING)
_file_handler.setFormatter(_log_formatter)
_stream_handler = logging.StreamHandler()
_stream_handler.setLevel(logging.WARNING)
_stream_handler.setFormatter(_log_formatter)
logging.basicConfig(level=logging.DEBUG, handlers=[_file_handler, _stream_handler])
logger = logging.getLogger(__name__)  # 'main' — module-level logger for main.py
# ─────────────────────────────────────────────────────────────────────────────

import matplotlib
matplotlib.use("QtAgg")  # noqa: E402
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import mplcursors

plt.rcParams['font.family'] = ['Malgun Gothic Semilight', '맑은 고딕 Semilight', 'Malgun Gothic', 'sans-serif']
plt.rcParams['axes.unicode_minus'] = False

_HIST_KEYS = ["3d", "5d", "10d", "20d", "60d", "120d"]
_MARKET_ORDER = {"KOSPI": 0, "KOSDAQ": 1, "NASDAQ 100": 2, "S&P500": 3}

# ---
# Shared: 1,000-separator auto-formatter for QLineEdit
# ---
def _fmt_num_edit(edit: "QLineEdit", text: str, decimal: bool = False) -> None:
    """Re-format `text` with thousands commas and update `edit` in-place."
    Preserves cursor position. Supports optional decimal part."""
    raw = text.replace(',', '').strip()
    if not raw:
        return
    try:
        if decimal and '.' in raw:
            int_part, dec_part = raw.split('.', 1)
            int_part = int_part or '0'
            formatted = f"{int(int_part):,}.{dec_part}"
        else:
            formatted = f"{int(float(raw)):,}"
        if formatted != text:
            pos = edit.cursorPosition()
            delta = len(formatted) - len(text)
            edit.blockSignals(True)
            edit.setText(formatted)
            edit.setCursorPosition(max(0, pos + delta))
            edit.blockSignals(False)
    except (ValueError, OverflowError):
        pass


# ---
# Shared: trade-entry input validation (roadmap 2-5)
# ---
_FIELD_ERROR_STYLE = "border: 1px solid #e74c3c; background-color: #fdecea;"


def _set_field_error(edit: "QLineEdit", message: str = "") -> None:
    """Apply (message truthy) or clear (message falsy) red-border error styling
    + tooltip on a QLineEdit, per the roadmap's 'red border + tooltip' spec."""
    if message:
        edit.setStyleSheet(_FIELD_ERROR_STYLE)
        edit.setToolTip(message)
    else:
        edit.setStyleSheet("")
        edit.setToolTip("")


def _validate_date_str(text: str) -> bool:
    """True if text is a valid YYYY-MM-DD date."""
    text = text.strip()
    if not text:
        return False
    try:
        _dt.datetime.strptime(text, "%Y-%m-%d")
        return True
    except ValueError:
        return False


def _validate_positive_number(text: str) -> bool:
    """True if text parses (after stripping ',' and '%') to a strictly positive float."""
    text = text.replace(",", "").replace("%", "").strip()
    if not text:
        return False
    try:
        return float(text) > 0
    except ValueError:
        return False


def _mk_field_validator(edit: "QLineEdit", check_fn, error_msg: str):
    """Build a no-arg validator closure: re-reads `edit`'s current text, applies
    check_fn, sets/clears the red-border+tooltip error style, and returns the
    pass/fail bool. Connect the returned closure to `edit.textChanged` for
    real-time feedback, and call it again in on_save() to gate saving."""
    def _run(_ignored=None) -> bool:
        ok = check_fn(edit.text())
        _set_field_error(edit, "" if ok else error_msg)
        return ok
    return _run


# --- Phase 3-1: threads/ 패키지로 분리 ---
from threads.fetch_threads import (
    IndexMaThread,
    StockMaThread,
    SingleStockFetchThread,
    AllDataFetchThread,
    UniverseLightweightFetchThread,
    AutoBackupThread,
)

# --- Phase 3-1: ui/ 패키지로 분리 ---
from ui.widgets import (
    StockTable,
)
from ui.dialogs import (
    StockMaDialog,
)


# --- Phase 4: ui/history_tab.py, ui/assets_tab.py 로 분리 ---
from ui.history_tab import TradingHistoryTab
from ui.assets_tab import TradingRecordTab


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Portfolio Management")
        self.resize(1100, 850)

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)

        # Header
        header_layout = QHBoxLayout()
        title_label = QLabel("Portfolio Management")
        title_label.setFont(create_font(18, QFont.Weight.Bold))
        header_layout.addWidget(title_label)
        
        header_layout.addStretch()

        # Global Auto Timer
        self.global_auto_timer = QTimer(self)
        self.global_auto_timer.setInterval(60000)
        self.global_auto_timer.timeout.connect(self._on_global_auto_timer)

        # Add Auto Refresh Checkbox
        self.auto_refresh_cb = QCheckBox("Auto Update (1 min)")
        self.auto_refresh_cb.setFont(create_font(10, QFont.Weight.Bold))
        self.auto_refresh_cb.setStyleSheet("QCheckBox { color: #0078d4; margin-right: 15px; }")
        self.auto_refresh_cb.toggled.connect(self._toggle_global_auto_timer)
        header_layout.addWidget(self.auto_refresh_cb)
        
        # Check by default (this will trigger the toggled signal and start the timer)
        self.auto_refresh_cb.setChecked(True)

        main_layout.addLayout(header_layout)

        # Tab System
        self.tabs = QTabWidget()
        main_layout.addWidget(self.tabs)
        
        # 1. Trading Universe Tab
        self.universe_tab = QWidget()
        universe_layout = QVBoxLayout(self.universe_tab)
        
        tu_title = QLabel("Trading Universe")
        tu_title.setFont(create_font(16, QFont.Weight.Bold))
        universe_layout.addWidget(tu_title)
        
        # Add Ticker + Search panel
        add_layout = QHBoxLayout()
        
        lbl_market = QLabel("Market:")
        lbl_market.setFont(create_font(10, style_name="Semilight"))
        add_layout.addWidget(lbl_market)
        
        self.market_combo = QComboBox()
        self.market_combo.setFont(create_font(10, style_name="Semilight"))
        self.market_combo.addItems(["KOSPI", "KOSDAQ"]) #, "NASDAQ 100", "S&P500"])
        self.market_combo.setFixedWidth(100)
        add_layout.addWidget(self.market_combo)
        
        lbl_ticker = QLabel("Ticker:")
        lbl_ticker.setFont(create_font(10, style_name="Semilight"))
        add_layout.addWidget(lbl_ticker)
        
        self.ticker_input = QLineEdit()
        self.ticker_input.setFont(create_font(10, style_name="Semilight"))
        self.ticker_input.setPlaceholderText("e.g. AAPL or 005930")
        self.ticker_input.setFixedWidth(140)
        self.ticker_input.returnPressed.connect(self.add_ticker)
        add_layout.addWidget(self.ticker_input)
        
        self.add_ticker_btn = QPushButton("+ Add Ticker")
        self.add_ticker_btn.setFont(create_font(10, QFont.Weight.Bold))
        self.add_ticker_btn.setFixedWidth(120)
        self.add_ticker_btn.clicked.connect(self.add_ticker)
        add_layout.addWidget(self.add_ticker_btn)
        
        add_layout.addSpacing(16)
        
        lbl_search = QLabel("Search:")
        lbl_search.setFont(create_font(10, style_name="Semilight"))
        add_layout.addWidget(lbl_search)
        
        self.search_input = QLineEdit()
        self.search_input.setFont(create_font(10, style_name="Semilight"))
        self.search_input.setPlaceholderText("Search by name or ticker...")
        self.search_input.setFixedWidth(260)
        self.search_input.textChanged.connect(self.filter_table)
        add_layout.addWidget(self.search_input)

        self.ai_filter_btn = QPushButton("🤖 AI Filter")
        self.ai_filter_btn.setFont(create_font(10, style_name="Semilight"))
        self.ai_filter_btn.setFixedWidth(90)
        self.ai_filter_btn.setFixedHeight(28)
        self.ai_filter_btn.setToolTip("Filter stocks using natural language\nExample: KOSPI stocks with RSI below 30 and high volume")
        self.ai_filter_btn.setStyleSheet(
            "QPushButton { background:#0a3d62; color:white; border-radius:4px; padding:2px 6px; font-size:9pt; }"
            "QPushButton:hover { background:#1e5799; }"
        )
        self.ai_filter_btn.clicked.connect(self._show_ai_filter_dialog)
        add_layout.addWidget(self.ai_filter_btn)
        
        add_layout.addSpacing(16)
        
        self.tg_filter_btn = QPushButton("Target List")
        self.tg_filter_btn.setFont(create_font(10, style_name="Semilight"))
        self.tg_filter_btn.setCheckable(True)
        self.tg_filter_btn.setFixedWidth(100)
        self.tg_filter_btn.setStyleSheet(
            "QPushButton:checked { background-color: #87CEEB; font-weight: bold; color: black; }"
        )
        self.tg_filter_btn.clicked.connect(lambda: self.filter_table())
        add_layout.addWidget(self.tg_filter_btn)
        
        add_layout.addStretch()
        
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.setFont(create_font(10, style_name="Semilight"))
        self.refresh_btn.setFixedWidth(100)
        self.refresh_btn.clicked.connect(self.refresh_data)
        add_layout.addWidget(self.refresh_btn)
        
        universe_layout.addLayout(add_layout)
        
        self.table = StockTable()
        self.table.col_filter_changed.connect(self.filter_table)
        universe_layout.addWidget(self.table)

        self.tabs.addTab(self.universe_tab, "Trading Universe")
        
        # 3. Trading History Tab
        self.trading_history_tab = TradingHistoryTab()
        self.tabs.addTab(self.trading_history_tab, "Trading History")

        # 4. Total Assets Tab (Trading Record)
        self.trading_record_tab = TradingRecordTab()
        self.tabs.addTab(self.trading_record_tab, "Total Assets")

        self.trading_history_tab.total_asset_updated.connect(self.trading_record_tab.update_live_asset)
        self.trading_history_tab.status_message.connect(self._on_thread_status_message)

        # Native status bar: shows short-lived progress text from background fetch threads
        # (e.g. "KOSPI 시세 조회 중… (3/5)", "Yahoo Finance 응답 대기 중…").
        self.statusBar().showMessage("Ready")

        # Initialise the SQLite database (creates tables + migrates legacy JSON on first run).
        trade_db.init_db()

        # Load Trading History from the database (primary source).
        self.trading_history_tab.load_from_db()
        
        # Status Bar (Common)
        status_layout = QHBoxLayout()
        status_layout.setContentsMargins(0, 0, 0, 0)
        self.status_label = QLabel("Ready")
        status_layout.addWidget(self.status_label)
        status_layout.addStretch()
        
        self.update_time_label = QLabel("Update Time: -")
        self.update_time_label.setFont(create_font(9, style_name="Semilight"))
        self.update_time_label.setStyleSheet("color: #7f8c8d;")
        status_layout.addWidget(self.update_time_label)
        
        main_layout.addLayout(status_layout)

        self.all_data = []
        self.market_status = {}
        self.load_custom_settings()

        # Try to load cached universe data to make startup instant, but always refresh to latest afterwards.
        if os.path.exists("universe_cache.json"):
            try:
                with open("universe_cache.json", "r", encoding="utf-8") as f:
                    self.all_data = json.load(f)
                self.table.load_data(self.all_data, self.custom_settings.get("highlights", {}))
                self._populate_action_buttons()
                self.filter_table()
                self.update_total_status(prefix="Loaded cached universe. Refreshing data...")
            except Exception as e:
                print(f"Error loading universe cache: {e}")

        self.refresh_data()

        # ---Tab movement shortcuts (Ctrl+Tab / Ctrl+Shift+Tab) ---
        # QShortcut(WindowShortcut) works across the entire window regardless of focus position
        sc_next = QShortcut(QKeySequence("Ctrl+Tab"), self)
        sc_next.setContext(Qt.ShortcutContext.WindowShortcut)
        sc_next.activated.connect(self._tab_next)

        sc_prev = QShortcut(QKeySequence("Ctrl+Shift+Tab"), self)
        sc_prev.setContext(Qt.ShortcutContext.WindowShortcut)
        sc_prev.activated.connect(self._tab_prev)

        # Automatic backup of portfolio.db + custom_settings.json (roadmap 2-4).
        # Runs in the background so it never blocks startup.
        self._auto_backup_thread = AutoBackupThread()
        self._auto_backup_thread.backup_done.connect(self._on_thread_status_message)
        self._auto_backup_thread.start()

    def _tab_next(self):
        """Ctrl+Tab: Move to the Next Tab (Circular)."""
        count = self.tabs.count()
        if count > 1:
            self.tabs.setCurrentIndex((self.tabs.currentIndex() + 1) % count)

    def _tab_prev(self):
        """Ctrl+Shift+Tab: Move to the Previous Tab (Circular)."""
        count = self.tabs.count()
        if count > 1:
            self.tabs.setCurrentIndex((self.tabs.currentIndex() - 1) % count)

    def load_custom_settings(self):
        self.custom_settings = {"added": [], "deleted": [], "highlights": {}}
        try:
            if os.path.exists("custom_settings.json"):
                with open("custom_settings.json", "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.custom_settings.update(data)
                    # Migration: old array "highlighted" -> dict "highlights"
                    if isinstance(self.custom_settings.get("highlighted"), list):
                        hl_dict = self.custom_settings.setdefault("highlights", {})
                        for t in self.custom_settings["highlighted"]:
                            hl_dict[t] = "Tg"
                        del self.custom_settings["highlighted"]
                        self.save_custom_settings()
        except Exception:
            logger.warning("Failed to load custom_settings.json", exc_info=True)

    def save_custom_settings(self):
        try:
            with open("custom_settings.json", "w", encoding="utf-8") as f:
                json.dump(self.custom_settings, f)
        except Exception as e:
            print(f"Error saving settings: {e}")

    def delete_stock(self, ticker):
        reply = QMessageBox.question(
            self, 'Confirm Delete',
            f"Are you sure you want to delete '{ticker}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        # Use in-memory settings (no extra disk reload needed)
        if ticker not in self.custom_settings.setdefault("deleted", []):
            self.custom_settings["deleted"].append(ticker)
        self.custom_settings["added"] = [
            x for x in self.custom_settings.get("added", []) if x["ticker"] != ticker
        ]
        self.save_custom_settings()

        self.all_data = [x for x in self.all_data if x.get("ticker", "") != ticker]
        self.table.load_data(self.all_data, self.custom_settings.get("highlights", {}))
        self._populate_action_buttons()
        self.filter_table()
        self.update_total_status(prefix=f"Deleted '{ticker}'.")

    def toggle_stock(self, ticker):
        # Use in-memory settings without redundant disk reload
        highlights = self.custom_settings.setdefault("highlights", {})
        current = highlights.get(ticker, "-")

        if current == "-":
            highlights[ticker] = "On"
        elif current == "On":
            highlights[ticker] = "Tg"
        else:
            highlights.pop(ticker, None)

        self.save_custom_settings()

        new_state = highlights.get(ticker, "-")

        # O(1) row lookup via ticker-ow map built from all_data order
        ticker_to_row = {self.table.item(r, 3).text(): r
                         for r in range(self.table.rowCount())
                         if self.table.item(r, 3)}
        row = ticker_to_row.get(ticker)
        if row is None:
            return

        name_item = self.table.item(row, 0)
        if name_item:
            if new_state == "On":
                name_item.setBackground(QColor("yellow"))
            elif new_state == "Tg":
                name_item.setBackground(QColor(135, 206, 235))
            else:
                name_item.setData(Qt.ItemDataRole.BackgroundRole, None)

        item_data = next((x for x in self.all_data if x.get("ticker", "") == ticker), None)
        if item_data:
            name   = item_data.get('name', ticker)
            market = item_data.get('market', '')
            cm     = item_data.get('change_mode', 'pct')
            self.table.add_action_buttons(
                row, new_state,
                lambda checked=False, t=ticker: self.toggle_stock(t),
                lambda checked=False, t=ticker, n=name, m=market, c=cm: self.show_stock_ma(t, n, m, c),
                lambda checked=False, t=ticker: self.delete_stock(t),
            )

    def add_ticker(self):
        ticker = self.ticker_input.text().strip().upper()
        market = self.market_combo.currentText()
        if not ticker:
            return
        
        self.load_custom_settings()
        
        existing = {d['ticker'] for d in self.all_data}
        if ticker in existing:
            self.status_label.setText(f"'{ticker}' is already in the table.")
            return
            
        if ticker in self.custom_settings.get("deleted", []):
            self.custom_settings["deleted"].remove(ticker)
            
        added_list = self.custom_settings.setdefault("added", [])
        if not any(x["ticker"] == ticker for x in added_list):
            added_list.append({"market": market, "ticker": ticker})
            
        self.save_custom_settings()
        
        self.add_ticker_btn.setEnabled(False)
        self.status_label.setText(f"Fetching '{ticker}' from {market}...")
        
        if getattr(self, '_single_fetch_thread', None) is not None:
            try:
                if self._single_fetch_thread.isRunning():
                    try: self._single_fetch_thread.finished.disconnect()
                    except Exception: pass
                    if not hasattr(self, '_zombie_threads'): self._zombie_threads = []
                    self._zombie_threads = [t for t in self._zombie_threads if t.isRunning()]
                    self._zombie_threads.append(self._single_fetch_thread)
            except RuntimeError:
                pass
            self._single_fetch_thread = None

        self._single_fetch_thread = SingleStockFetchThread(market, ticker)
        self._single_fetch_thread.finished.connect(lambda r, e: self.on_single_stock_loaded(r, e, False))
        self._single_fetch_thread.start()

    def on_single_stock_loaded(self, result, error, is_startup=False, ticker_hint=""):
        if not is_startup:
            self.add_ticker_btn.setEnabled(True)
        if error or result is None:
            if not is_startup:
                self.status_label.setText(f"Error: {error}" if error else "Stock not found.")
            else:
                # Startup failures were previously silent - now visible in console for diagnosis
                label = ticker_hint or (result.get('ticker', '') if result else '?')
                print(f"[Startup] Added ticker '{label}' failed to load: {error or 'No data returned'}")
            return
            
        self.load_custom_settings()
        ticker = result.get('ticker', '')
        if ticker in self.custom_settings.get("deleted", []):
            return
        if any(x.get('ticker') == ticker for x in self.all_data):
            if not is_startup:
                self.status_label.setText(f"'{ticker}' is already in the table.")
            return
        
        self.all_data.append(result)
        self.all_data.sort(key=lambda x: (
            0 if x.get('is_index') else 1,
            x.get('index_order', 99) if x.get('is_index') else _MARKET_ORDER.get(x.get('market', ''), 99),
            -float(x.get('market_cap', 0) or 0)
        ))
        self.table.load_data(self.all_data, self.custom_settings.get("highlights", {}))
        self._populate_action_buttons()
        self.filter_table()  # restore filter state
        
        if not is_startup:
            self.ticker_input.clear()
            msg = f"Added '{result.get('name', ticker)}' ({ticker})."
            self.update_total_status(prefix=msg)

    def filter_table(self, text=None):
        if text is None:
            text = self.search_input.text()
        tg_only = getattr(self, 'tg_filter_btn', None) is not None and self.tg_filter_btn.isChecked()
        self.table.apply_col_filters(text, tg_only=tg_only)

    def _show_ai_filter_dialog(self):
        """Open a dialog to input a natural-language filter query, call Gemini, and apply conditions."""
        from PyQt6.QtWidgets import QTextEdit

        dlg = QDialog(self)
        dlg.setWindowTitle("🤖 AI Natural Language Filter")
        dlg.resize(500, 220)
        v = QVBoxLayout(dlg)
        v.setContentsMargins(16, 14, 16, 14)
        v.setSpacing(10)

        title_lbl = QLabel("Enter stock filter conditions in natural language")
        title_lbl.setFont(create_font(11, QFont.Weight.Bold))
        title_lbl.setStyleSheet("color:#0a3d62;")
        v.addWidget(title_lbl)

        example_lbl = QLabel(
            "Example: <i>KOSPI stocks with MA20 divergence below 95%</i><br>"
            "<i>Stocks with PER below 15 and market cap over 1 trillion KRW</i>"
        )
        example_lbl.setTextFormat(Qt.TextFormat.RichText)
        example_lbl.setFont(create_font(9, style_name="Semilight"))
        example_lbl.setStyleSheet("color:#555;")
        v.addWidget(example_lbl)

        query_edit = QLineEdit()
        query_edit.setFont(create_font(10, style_name="Semilight"))
        query_edit.setPlaceholderText("Enter filter conditions in Korean or English...")
        query_edit.setFixedHeight(32)
        v.addWidget(query_edit)

        # Status label
        status_lbl = QLabel("")
        status_lbl.setFont(create_font(9, style_name="Semilight"))
        status_lbl.setStyleSheet("color:#107c10;")
        v.addWidget(status_lbl)

        btn_row = QHBoxLayout()

        clear_btn = QPushButton("Reset Filter")
        clear_btn.setFixedWidth(100)
        clear_btn.setStyleSheet(
            "QPushButton { background:#888; color:white; border-radius:4px; padding:3px 8px; }"
            "QPushButton:hover { background:#666; }"
        )

        apply_btn = QPushButton("✅ Apply")
        apply_btn.setDefault(True)
        apply_btn.setFixedWidth(80)
        apply_btn.setStyleSheet(
            "QPushButton { background:#0078d4; color:white; border-radius:4px; padding:3px 8px; }"
            "QPushButton:hover { background:#005a9e; }"
        )

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setFixedWidth(60)
        cancel_btn.clicked.connect(dlg.reject)

        btn_row.addWidget(clear_btn)
        btn_row.addStretch()
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(apply_btn)
        v.addLayout(btn_row)

        def _do_clear():
            self.table.clear_ai_filter()
            self.filter_table()
            self.ai_filter_btn.setStyleSheet(
                "QPushButton { background:#0a3d62; color:white; border-radius:4px; padding:2px 6px; font-size:9pt; }"
                "QPushButton:hover { background:#1e5799; }"
            )
            dlg.accept()

        def _do_apply():
            nl_query = query_edit.text().strip()
            if not nl_query:
                return
            status_lbl.setText("⏳ AI is analysing conditions…")
            apply_btn.setEnabled(False)
            QApplication.processEvents()

            result = gemini_helper.nl_to_filter(nl_query)
            apply_btn.setEnabled(True)

            if result is None:
                status_lbl.setStyleSheet("color:#c0392b;")
                status_lbl.setText("⚠️ AI conversion failed. Please check your API key and network connection.")
                return

            conditions = result.get("conditions", [])
            text_filter = result.get("text_filter", "")
            explanation = result.get("explanation", "")

            self.table.set_ai_conditions(conditions)
            if text_filter:
                self.search_input.setText(text_filter)
            self.filter_table()

            # Highlight the AI filter button to indicate an active AI filter
            self.ai_filter_btn.setStyleSheet(
                "QPushButton { background:#107c10; color:white; border-radius:4px; padding:2px 6px; font-size:9pt; font-weight:bold; }"
                "QPushButton:hover { background:#0b5e0b; }"
            )

            # Show explanation in status bar
            self.status_label.setText(f"🤖 AI filter applied: {explanation}")
            dlg.accept()

        clear_btn.clicked.connect(_do_clear)
        apply_btn.clicked.connect(_do_apply)
        query_edit.returnPressed.connect(_do_apply)

        dlg.exec()

    # ---Per-stock MA (20 + 60) ---
    def _populate_action_buttons(self):
        """Add Tg, MA, and Delete buttons to every row of the table."""
        highlights = self.custom_settings.get("highlights", {})
        row_data = self.all_data
        for row in range(min(self.table.rowCount(), len(row_data))):
            item = row_data[row]
            ticker  = item.get('ticker', '')
            name    = item.get('name', ticker)
            market  = item.get('market', '')
            cm      = item.get('change_mode', 'pct')
            h_state = highlights.get(ticker, "-")
            self.table.add_action_buttons(
                row, h_state,
                lambda checked=False, t=ticker: self.toggle_stock(t),
                lambda checked=False, t=ticker, n=name, m=market, c=cm: self.show_stock_ma(t, n, m, c),
                lambda checked=False, t=ticker: self.delete_stock(t),
            )

    def show_stock_ma(self, ticker, name, market, change_mode='pct'):
        self.status_label.setText(f"Loading MA20 & MA50 for {name} ({ticker})...")
        # Purge completed threads to prevent unbounded list growth
        self._stock_ma_threads = [t for t in getattr(self, '_stock_ma_threads', []) if t.isRunning()]
        thread = StockMaThread(ticker, name, market, change_mode)
        thread.finished.connect(lambda t, n, df, e, inv, cm=change_mode: self.on_stock_ma_loaded(t, n, df, e, inv, cm))
        self._stock_ma_threads.append(thread)
        thread.start()

    def on_stock_ma_loaded(self, ticker, name, df, error, investor_data=None, change_mode='pct'):
        self.status_label.setText(f"MA chart loaded for {name} ({ticker}).")
        if error and df is None:
            QMessageBox.warning(self, "Error", f"Failed to load data for {ticker}:\n{error}")
            return
        market = next((d.get('market', '') for d in self.all_data if d.get('ticker') == ticker), "")
        dlg = StockMaDialog(ticker, name, market, df, investor_data=investor_data, parent=None, change_mode=change_mode)
        dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        if not hasattr(self, '_open_dialogs'):
            self._open_dialogs = []
        active_dialogs = []
        for d in self._open_dialogs:
            try:
                if d.isVisible():
                    active_dialogs.append(d)
            except RuntimeError:
                pass
        self._open_dialogs = active_dialogs
        self._open_dialogs.append(dlg)
        dlg.show()

    def refresh_data(self):
        self.refresh_btn.setEnabled(False)
        self.all_data = []
        self.market_status = {m: "Waiting" for m in ("Indices", "KOSPI", "KOSDAQ")} #, "NASDAQ 100", "S&P500")}
        self.update_status_display()

        if getattr(self, 'fetch_thread', None) is not None:
            try:
                if self.fetch_thread.isRunning():
                    try: self.fetch_thread.disconnect()
                    except Exception: pass
                    if not hasattr(self, '_zombie_threads'): self._zombie_threads = []
                    self._zombie_threads = [t for t in self._zombie_threads if t.isRunning()]
                    self._zombie_threads.append(self.fetch_thread)
            except RuntimeError:
                pass
            self.fetch_thread = None

        self.fetch_thread = AllDataFetchThread()
        self.fetch_thread.market_loaded.connect(self.on_market_loaded)
        self.fetch_thread.market_progress.connect(self.on_market_progress)
        self.fetch_thread.finished_all.connect(self.on_finished_all)
        self.fetch_thread.start()

        if hasattr(self, "trading_history_tab"):
            self.trading_history_tab._reload_current()

    def _toggle_global_auto_timer(self, checked):
        if checked:
            self.global_auto_timer.start()
            # Optionally trigger immediately when turned on if desired, 
            # but let's just wait 1 minute for the first tick to avoid spamming if user clicks back and forth.
        else:
            self.global_auto_timer.stop()

    def _on_global_auto_timer(self):
        # Skip if a full fetch is currently running
        if not hasattr(self, 'refresh_btn') or not self.refresh_btn.isEnabled():
            return
            
        if getattr(self, 'all_data', None):
            # If all_data is already populated, do a lightweight in-place update for Universe
            if getattr(self, '_lw_fetch_thread', None) is not None and self._lw_fetch_thread.isRunning():
                return
            
            self._lw_fetch_thread = UniverseLightweightFetchThread(self.all_data)
            self._lw_fetch_thread.finished_all.connect(self._on_universe_lightweight_loaded)
            self._lw_fetch_thread.status_message.connect(self._on_thread_status_message)
            self._lw_fetch_thread.start()
            
            # Also trigger lightweight update for Trading History
            if hasattr(self, "trading_history_tab"):
                self.trading_history_tab._start_realtime_price_update()
        else:
            # If empty, do a full refresh
            self.refresh_data()

    def _on_universe_lightweight_loaded(self, updated_data):
        self.all_data = updated_data
        highlights = self.custom_settings.get("highlights", {})
        
        # Save current scroll position
        v_scroll = self.table.verticalScrollBar().value()
        
        self.table.load_data(self.all_data, highlights)
        self._populate_action_buttons()
        self.filter_table(self.search_input.text())
        
        # Restore scroll position to prevent jumping
        self.table.verticalScrollBar().setValue(v_scroll)
        
        self.update_total_status("Auto-updated prices.")
        self.update_last_sync_time()

    def on_market_progress(self, market, current, total):
        self.market_status[market] = f"Loading ({current}/{total})"
        self.update_status_display()
        self._on_thread_status_message(f"{market} 시세 조회 중… ({current}/{total})")

    def _on_thread_status_message(self, msg: str):
        """Show a short-lived progress message from a background fetch thread
        in the native status bar (roadmap 2-3)."""
        self.statusBar().showMessage(msg, 5000)

    def update_status_display(self):
        parts = " | ".join(f"{m}: {s}" for m, s in self.market_status.items())
        self.status_label.setText(f"Status: {parts}")

    def update_last_sync_time(self):
        if hasattr(self, 'update_time_label'):
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.update_time_label.setText(f"Updated: {now_str}")

    def update_total_status(self, prefix="Data Loaded."):
        market_counts = Counter(item.get("market", "Unknown") for item in self.all_data)
        order = ["Index", "KOSPI", "KOSDAQ"] #, "NASDAQ 100", "S&P500"]
        summary_parts = [
            f"{m} {market_counts[m]}" for m in order if m in market_counts
        ] + [
            f"{m} {c}" for m, c in sorted(market_counts.items()) if m not in order
        ]
        summary = ", ".join(summary_parts)
        self.status_label.setText(f"{prefix} Total {len(self.all_data)} stocks ({summary})")

    def on_market_loaded(self, market, data):
        self.market_status[market] = f"Done({len(data)})"
        self.update_status_display()
        # Accumulate data incrementally so the table updates as each market finishes
        self.all_data.extend(data)

    def on_finished_all(self, all_data):
        try:
            self.load_custom_settings()
            deleted = set(self.custom_settings.get("deleted", []))
            added = self.custom_settings.get("added", [])
            
            filtered_data = [x for x in all_data if x.get("ticker", "") not in deleted]
            
            self.all_data = sorted(
                filtered_data,
                key=lambda x: (
                    0 if x.get('is_index') else 1,
                    x.get('index_order', 99) if x.get('is_index') else _MARKET_ORDER.get(x.get('market', ''), 99),
                    -float(x.get('market_cap', 0) or 0)
                )
            )
            self.table.load_data(self.all_data, self.custom_settings.get("highlights", {}))
            self._populate_action_buttons()
            self.filter_table(self.search_input.text())
            self.update_total_status()
            self.update_last_sync_time()

            # Cache the newly fetched data
            try:
                with open("universe_cache.json", "w", encoding="utf-8") as f:
                    json.dump(self.all_data, f, ensure_ascii=False)
            except Exception as e:
                print(f"Error caching universe data: {e}")

            existing_tickers = {x.get("ticker") for x in self.all_data}
            missing_added = [x for x in added if x["ticker"] not in existing_tickers and x["ticker"] not in deleted]
            
            self._startup_threads = getattr(self, "_startup_threads", [])
            for item in missing_added:
                _ticker = item["ticker"]
                _market = item["market"]
                thread = SingleStockFetchThread(_market, _ticker)
                thread.finished.connect(
                    lambda r, e, t=_ticker: self.on_single_stock_loaded(r, e, is_startup=True, ticker_hint=t)
                )
                self._startup_threads.append(thread)
                thread.start()

        except Exception as e:
            print(traceback.format_exc())
            self.status_label.setText(f"Data sort/load error: {e}")
        finally:
            self.refresh_btn.setEnabled(True)

    # ---Clean shutdown ---
    def closeEvent(self, event):
        """Stop all background QThread workers so the process exits cleanly."

        Without this, ThreadPoolExecutors inside QThread.run() keep non-daemon
        Python threads alive after the Qt event loop ends, which blocks the
        terminal from returning to the shell prompt.
        """
        # Collect every QThread this window may have started
        threads_to_stop = []

        ft = getattr(self, 'fetch_thread', None)
        if ft is not None:
            threads_to_stop.append(ft)

        bt = getattr(self, '_backtest_thread', None)
        if bt is not None:
            threads_to_stop.append(bt)

        for t in getattr(self, '_startup_threads', []):
            threads_to_stop.append(t)
        for t in getattr(self, '_stock_ma_threads', []):
            threads_to_stop.append(t)

        sft = getattr(self, '_single_fetch_thread', None)
        if sft is not None:
            threads_to_stop.append(sft)

        if hasattr(self, 'trading_history_tab'):
            p_thread = getattr(self.trading_history_tab, '_price_thread', None)
            if p_thread is not None:
                threads_to_stop.append(p_thread)
            
            rt_thread = getattr(self.trading_history_tab, '_rt_price_thread', None)
            if rt_thread is not None:
                threads_to_stop.append(rt_thread)
                
            for zt in getattr(self.trading_history_tab, '_zombie_threads', []):
                threads_to_stop.append(zt)

        for t in threads_to_stop:
            try:
                if t.isRunning():
                    t.quit()
                    if not t.wait(2000):   # wait up to 2 s
                        t.terminate()     # force-kill if still alive
                        t.wait(500)
            except Exception:
                logger.warning("Failed to cleanly stop background thread on close", exc_info=True)

        QApplication.instance().quit()
        event.accept()


if __name__ == "__main__":
    print("Starting Portfolio Management...")
    app = QApplication(sys.argv)
    app_font = create_font(10, style_name="Semilight")
    app.setFont(app_font)
    app.setStyleSheet("""
        QMainWindow { background-color: #f0f0f0; }
        QTableWidget {
            background-color: white;
            alternate-background-color: #f9f9f9;
            gridline-color: #d0d0d0;
            font-family: 'Malgun Gothic Semilight', '맑은 고딕 Semilight', 'Malgun Gothic';
        }
        QHeaderView::section {
            background-color: #e0e0e0;
            padding: 4px;
            border: 1px solid #d0d0d0;
            font-weight: bold;
            font-family: 'Malgun Gothic Semilight', '맑은 고딕 Semilight', 'Malgun Gothic';
        }
        QLineEdit {
            padding: 5px;
            border: 1px solid #c0c0c0;
            border-radius: 4px;
            font-family: 'Malgun Gothic Semilight', '맑은 고딕 Semilight', 'Malgun Gothic';
        }
        QComboBox {
            padding: 5px;
            border: 1px solid #c0c0c0;
            border-radius: 4px;
            font-family: 'Malgun Gothic Semilight', '맑은 고딕 Semilight', 'Malgun Gothic';
        }
        QPushButton {
            padding: 8px 16px;
            background-color: #0078d4;
            color: white;
            border: none;
            border-radius: 4px;
            font-weight: bold;
            font-family: 'Malgun Gothic Semilight', '맑은 고딕 Semilight', 'Malgun Gothic';
        }
        QPushButton:hover { background-color: #005a9e; }
        QPushButton:disabled { background-color: #cccccc; }
        QPushButton:checked { background-color: #005a9e; border: 2px solid #003f7f; }
        
        QTabWidget::pane {
            border: 1px solid #d0d0d0;
            background: white;
            border-radius: 4px;
        }
        QTabBar::tab {
            background: #e0e0e0;
            border: 1px solid #d0d0d0;
            padding: 10px 30px;
            font-weight: bold;
            font-family: 'Malgun Gothic Semilight', '맑은 고딕 Semilight', 'Malgun Gothic';
        }
        QTabBar::tab:selected {
            background: white;
            border-bottom: 2px solid #0078d4;
        }
    """)

    window = MainWindow()
    window.showMaximized()
    ret = app.exec()
    # Ensure all remaining non-daemon threads (ThreadPoolExecutor workers
    # started by data_fetcher) don't block process exit.
    import os
    os._exit(ret if ret else 0)

