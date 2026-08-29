"""ui/universe_tab.py — UniverseTab (Phase 5 split)

Split out from: main.py MainWindow (2026-08-29 feat/3-1-modularize, Phase 5)
Contains:
  UniverseTab — "Trading Universe" tab: watchlist table, ticker add/search/AI
  filter controls, and all market-data-refresh orchestration that used to
  live directly on MainWindow.

Cross-tab communication (mirrors the TradingHistoryTab pattern from Phase 4):
  status_text_changed / sync_time_changed — MainWindow forwards these to the
    shared footer labels (status_label / update_time_label) it owns, so the
    footer's on-screen position (below the tab widget, visible regardless of
    which tab is active) is unchanged from before this split.
  status_message — short-lived progress text, forwarded to MainWindow's
    native status bar via _on_thread_status_message (same as
    TradingHistoryTab.status_message).
  refresh_started — emitted whenever refresh_data() runs, so MainWindow can
    also reload the Trading History tab (mirrors the original unconditional
    call to trading_history_tab._reload_current() inside refresh_data()).
  auto_lightweight_tick — emitted when the 60s auto-timer takes the
    lightweight-update path, so MainWindow can also trigger the Trading
    History tab's realtime price update (mirrors the original call to
    trading_history_tab._start_realtime_price_update()).
"""
import json
import logging
import os
import traceback
from collections import Counter
from datetime import datetime

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QLineEdit,
    QPushButton, QMessageBox, QDialog,
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont

import gemini_helper
from threads.fetch_threads import (
    SingleStockFetchThread,
    AllDataFetchThread,
    UniverseLightweightFetchThread,
    StockMaThread,
)
from ui.widgets import StockTable
from ui.dialogs import StockMaDialog

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lazy helpers -- avoid circular imports with main (main.py imports this
# module at load time, so this module cannot import main at load time in
# return). These forward to main's implementations so the class body below
# can call create_font(...) / _MARKET_ORDER unchanged from their original
# form in main.py.
# ---------------------------------------------------------------------------
def _get_create_font():
    import main as _m
    return _m.create_font


def create_font(*args, **kwargs):
    return _get_create_font()(*args, **kwargs)


def _get_market_order():
    import main as _m
    return _m._MARKET_ORDER


class UniverseTab(QWidget):
    """Trading Universe tab: watchlist table + ticker add/search/AI filter controls."""

    status_text_changed = pyqtSignal(str)  # -> MainWindow's shared status_label
    sync_time_changed = pyqtSignal(str)    # -> MainWindow's shared update_time_label
    status_message = pyqtSignal(str)       # -> MainWindow's native status bar (roadmap 2-3)
    refresh_started = pyqtSignal()         # -> MainWindow also reloads Trading History tab
    auto_lightweight_tick = pyqtSignal()   # -> MainWindow also triggers Trading History realtime update

    def __init__(self, parent=None):
        super().__init__(parent)
        self.all_data = []
        self.market_status = {}
        self._build_ui()
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

    def _build_ui(self):
        universe_layout = QVBoxLayout(self)

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
            self.status_text_changed.emit(f"'{ticker}' is already in the table.")
            return

        if ticker in self.custom_settings.get("deleted", []):
            self.custom_settings["deleted"].remove(ticker)

        added_list = self.custom_settings.setdefault("added", [])
        if not any(x["ticker"] == ticker for x in added_list):
            added_list.append({"market": market, "ticker": ticker})

        self.save_custom_settings()

        self.add_ticker_btn.setEnabled(False)
        self.status_text_changed.emit(f"Fetching '{ticker}' from {market}...")

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
                self.status_text_changed.emit(f"Error: {error}" if error else "Stock not found.")
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
                self.status_text_changed.emit(f"'{ticker}' is already in the table.")
            return

        self.all_data.append(result)
        self.all_data.sort(key=lambda x: (
            0 if x.get('is_index') else 1,
            x.get('index_order', 99) if x.get('is_index') else _get_market_order().get(x.get('market', ''), 99),
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
        from PyQt6.QtWidgets import QApplication, QTextEdit

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
            self.status_text_changed.emit(f"🤖 AI filter applied: {explanation}")
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
        self.status_text_changed.emit(f"Loading MA20 & MA50 for {name} ({ticker})...")
        # Purge completed threads to prevent unbounded list growth
        self._stock_ma_threads = [t for t in getattr(self, '_stock_ma_threads', []) if t.isRunning()]
        thread = StockMaThread(ticker, name, market, change_mode)
        thread.finished.connect(lambda t, n, df, e, inv, cm=change_mode: self.on_stock_ma_loaded(t, n, df, e, inv, cm))
        self._stock_ma_threads.append(thread)
        thread.start()

    def on_stock_ma_loaded(self, ticker, name, df, error, investor_data=None, change_mode='pct'):
        self.status_text_changed.emit(f"MA chart loaded for {name} ({ticker}).")
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

        self.refresh_started.emit()

    def auto_update_tick(self):
        """Called every 60s by MainWindow's global auto timer (roadmap: Auto Update checkbox)."""
        if not self.refresh_btn.isEnabled():
            return

        if self.all_data:
            # If all_data is already populated, do a lightweight in-place update for Universe
            if getattr(self, '_lw_fetch_thread', None) is not None and self._lw_fetch_thread.isRunning():
                return

            self._lw_fetch_thread = UniverseLightweightFetchThread(self.all_data)
            self._lw_fetch_thread.finished_all.connect(self._on_universe_lightweight_loaded)
            self._lw_fetch_thread.status_message.connect(self.status_message)
            self._lw_fetch_thread.start()

            self.auto_lightweight_tick.emit()
        else:
            # If empty, do a full refresh
            self.refresh_data()

    def _on_universe_lightweight_loaded(self, updated_data):
        # UniverseLightweightFetchThread only reassigns item["changes"] (to a
        # freshly-fetched dict) for tickers whose price actually moved -- every
        # unchanged item keeps the exact same "changes" dict object it had
        # before the thread ran. That lets us tell which rows actually need a
        # UI refresh by object identity, without a value-by-value diff.
        old_data = self.all_data
        changed_rows = {
            i for i, (old_item, new_item) in enumerate(zip(old_data, updated_data))
            if old_item.get("changes") is not new_item.get("changes")
        }

        self.all_data = updated_data
        highlights = self.custom_settings.get("highlights", {})

        # Save current scroll position
        v_scroll = self.table.verticalScrollBar().value()

        # Incremental update: only the rows whose price/changes actually
        # changed get their cells rebuilt (name/market/ticker/marcap/PER and
        # the Tg/MA/Del action-button widgets never change on this path, so
        # there's no need to touch them at all, unlike a full load_data()).
        self.table.update_changed_rows(self.all_data, changed_rows, highlights)
        if changed_rows:
            self.filter_table(self.search_input.text())

        # Restore scroll position to prevent jumping
        self.table.verticalScrollBar().setValue(v_scroll)

        self.update_total_status("Auto-updated prices.")
        self.update_last_sync_time()

    def on_market_progress(self, market, current, total):
        self.market_status[market] = f"Loading ({current}/{total})"
        self.update_status_display()
        self.status_message.emit(f"Fetching {market} quotes... ({current}/{total})")

    def update_status_display(self):
        parts = " | ".join(f"{m}: {s}" for m, s in self.market_status.items())
        self.status_text_changed.emit(f"Status: {parts}")

    def update_last_sync_time(self):
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.sync_time_changed.emit(f"Updated: {now_str}")

    def update_total_status(self, prefix="Data Loaded."):
        market_counts = Counter(item.get("market", "Unknown") for item in self.all_data)
        order = ["Index", "KOSPI", "KOSDAQ"] #, "NASDAQ 100", "S&P500"]
        summary_parts = [
            f"{m} {market_counts[m]}" for m in order if m in market_counts
        ] + [
            f"{m} {c}" for m, c in sorted(market_counts.items()) if m not in order
        ]
        summary = ", ".join(summary_parts)
        self.status_text_changed.emit(f"{prefix} Total {len(self.all_data)} stocks ({summary})")

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
                    x.get('index_order', 99) if x.get('is_index') else _get_market_order().get(x.get('market', ''), 99),
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
            self.status_text_changed.emit(f"Data sort/load error: {e}")
        finally:
            self.refresh_btn.setEnabled(True)

    def collect_threads_to_stop(self):
        """Return every QThread this tab may have started, for MainWindow.closeEvent."""
        threads = []
        ft = getattr(self, 'fetch_thread', None)
        if ft is not None:
            threads.append(ft)
        for t in getattr(self, '_startup_threads', []):
            threads.append(t)
        for t in getattr(self, '_stock_ma_threads', []):
            threads.append(t)
        sft = getattr(self, '_single_fetch_thread', None)
        if sft is not None:
            threads.append(sft)
        return threads
