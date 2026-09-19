"""ui/universe_tab.py — UniverseTab (Phase 5 split)

Split out from: main.py MainWindow (2026-08-29 feat/3-1-modularize, Phase 5)
Contains:
  UniverseTab — "Trading Universe" tab: watchlist table, ticker add/search
  controls, and all market-data-refresh orchestration that used to
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
import logging
from collections import Counter
from datetime import datetime

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QLineEdit,
    QPushButton, QMessageBox, QFrame,
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont

from paths import UNIVERSE_CACHE_FILE, CUSTOM_SETTINGS_FILE
from threads.fetch_threads import (
    SingleStockFetchThread,
    AllDataFetchThread,
    UniverseLightweightFetchThread,
    StockMaThread,
    GeminiStockReportThread,
)
from ui.widgets import StockTable, TOGGLE_GROUPS
from ui.dialogs import StockMaDialog
from ui.dialogs.stock_report import show_stock_report_result
from ui.colors import PROFIT, LOSS, FLAT
from ui.theme import SURFACE, LINE, TEXT_MUTED

logger = logging.getLogger(__name__)


from ui.common import (
    create_font,
    _MARKET_ORDER,
    atomic_save_json,
    safe_load_json,
    ThreadOwnerMixin,
)

# docs/ui.md 2.4: index/bond/commodity rows move out of the main table into
# small "Market rail" cards -- their prices aren't comparable to equities
# (points, bp, $ vs won) and previously forced Market Cap/tPER/fPER to show
# "-" for every one of these rows.
def _is_rail_row(item: dict) -> bool:
    return bool(item.get('is_index') or item.get('is_bond') or item.get('change_mode') in ('bp', 'abs'))


class UniverseTab(ThreadOwnerMixin, QWidget):
    """Trading Universe tab: watchlist table + ticker add/search controls."""

    status_text_changed = pyqtSignal(str)  # -> MainWindow's shared status_label
    sync_time_changed = pyqtSignal(str)    # -> MainWindow's shared update_time_label
    status_message = pyqtSignal(str)       # -> MainWindow's native status bar (roadmap 2-3)
    refresh_started = pyqtSignal()         # -> MainWindow also reloads Trading History tab
    auto_lightweight_tick = pyqtSignal()   # -> MainWindow also triggers Trading History realtime update

    def __init__(self, parent=None):
        super().__init__(parent)
        self.all_data = []
        self.market_status = {}
        self._market_filter = "ALL"
        self._open_dialogs: list = []
        self._build_ui()
        self.load_custom_settings()
        self._apply_column_group_and_density_settings()

        # Try to load cached universe data to make startup instant, but always refresh to latest afterwards.
        cached = safe_load_json(UNIVERSE_CACHE_FILE, default=None)
        if cached:
            self.all_data = cached
            self._reload_table_and_rail()
            self.filter_table()
            self.update_total_status(prefix="Loaded cached universe. Refreshing data...")

    def _build_ui(self):
        universe_layout = QVBoxLayout(self)

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

        # Market filter (docs/ui.md 2.1): ALL/KOSPI/KOSDAQ toolbar buttons,
        # replacing the old Excel-style dropdown on a Market table column
        # (Market isn't a column anymore -- see the identity cell's meta text).
        add_layout.addSpacing(8)
        self._market_buttons = {}
        for m in ("ALL", "KOSPI", "KOSDAQ"):
            btn = QPushButton(m)
            btn.setFont(create_font(9, style_name="Semilight"))
            btn.setCheckable(True)
            btn.setFixedWidth(64)
            btn.setChecked(m == "ALL")
            btn.clicked.connect(lambda checked, mk=m: self._on_market_filter_changed(mk))
            add_layout.addWidget(btn)
            self._market_buttons[m] = btn

        add_layout.addSpacing(8)
        self.tg_filter_btn = QPushButton("Target List")
        self.tg_filter_btn.setFont(create_font(10, style_name="Semilight"))
        self.tg_filter_btn.setCheckable(True)  # checked state: ui/theme.py's QPushButton:checked rule
        self.tg_filter_btn.setFixedWidth(100)
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
        self.table.ai_report_requested.connect(self._on_ai_report_requested)
        self.table.ma_chart_requested.connect(self._on_ma_chart_requested)
        self.table.delete_requested.connect(self.delete_stock)
        self.table.toggle_requested.connect(self.toggle_stock)

        # Column-group toggle + density toggle (docs/ui.md 1.4, 2.5)
        toolbar2 = QHBoxLayout()

        lbl_cols = QLabel("Columns:")
        lbl_cols.setFont(create_font(10, style_name="Semilight"))
        toolbar2.addWidget(lbl_cols)

        self._group_buttons = {}
        for group_key, group_label in TOGGLE_GROUPS:
            btn = QPushButton(group_label)
            btn.setFont(create_font(9, style_name="Semilight"))
            btn.setCheckable(True)  # checked state: ui/theme.py's QPushButton:checked rule
            btn.setFixedWidth(80)
            btn.clicked.connect(lambda checked, g=group_key: self._on_column_group_toggled(g, checked))
            toolbar2.addWidget(btn)
            self._group_buttons[group_key] = btn

        toolbar2.addSpacing(16)

        lbl_density = QLabel("Density:")
        lbl_density.setFont(create_font(10, style_name="Semilight"))
        toolbar2.addWidget(lbl_density)

        self.density_combo = QComboBox()
        self.density_combo.setFont(create_font(9, style_name="Semilight"))
        self.density_combo.addItems(["Compact", "Normal", "Spacious"])
        self.density_combo.setFixedWidth(100)
        self.density_combo.currentTextChanged.connect(self._on_density_changed)
        toolbar2.addWidget(self.density_combo)

        toolbar2.addStretch()
        universe_layout.addLayout(toolbar2)

        # Market rail (docs/ui.md 2.4): index/bond/commodity cards, separated
        # out of the main table entirely. Populated by _refresh_market_rail().
        self._rail_layout = QHBoxLayout()
        self._rail_layout.setSpacing(8)
        universe_layout.addLayout(self._rail_layout)

        universe_layout.addWidget(self.table)

    def _on_market_filter_changed(self, market):
        self._market_filter = market
        for m, btn in self._market_buttons.items():
            btn.setChecked(m == market)
        self.filter_table()

    def _on_column_group_toggled(self, group, checked):
        self.table.set_column_group_visible(group, checked)
        groups = self.custom_settings.setdefault("column_groups", {})
        groups[group] = checked
        self.save_custom_settings()

    def _on_density_changed(self, label):
        level = label.lower()
        self.table.set_density(level)
        self.custom_settings["density"] = level
        self.save_custom_settings()

    def _apply_column_group_and_density_settings(self):
        groups = self.custom_settings.setdefault(
            "column_groups", {"price": True, "value": True, "momentum": True}
        )
        for group_key, _label in TOGGLE_GROUPS:
            enabled = groups.get(group_key, True)
            self._group_buttons[group_key].setChecked(enabled)
            self.table.set_column_group_visible(group_key, enabled)

        density = self.custom_settings.setdefault("density", "compact")
        idx = self.density_combo.findText(density.capitalize())
        if idx >= 0:
            self.density_combo.setCurrentIndex(idx)
        self.table.set_density(density)

    def load_custom_settings(self):
        self.custom_settings = {"added": [], "deleted": [], "highlights": {}}
        data = safe_load_json(CUSTOM_SETTINGS_FILE, default={})
        if data:
            self.custom_settings.update(data)
            # Migration: old array "highlighted" -> dict "highlights"
            if isinstance(self.custom_settings.get("highlighted"), list):
                hl_dict = self.custom_settings.setdefault("highlights", {})
                for t in self.custom_settings["highlighted"]:
                    hl_dict[t] = "Tg"
                del self.custom_settings["highlighted"]
                self.save_custom_settings()

    def save_custom_settings(self):
        try:
            atomic_save_json(CUSTOM_SETTINGS_FILE, self.custom_settings)
        except Exception:
            logger.warning("Failed to save custom_settings.json", exc_info=True)

    # ---Table / Market rail split (docs/ui.md 2.4) ---
    def _reload_table_and_rail(self):
        """Rebuild both the main table and the Market rail from self.all_data.
        Call after anything that replaces all_data wholesale (cache load,
        add, delete, full refresh) -- the incremental lightweight-refresh
        path has its own leaner version in _on_universe_lightweight_loaded."""
        main_rows = [x for x in self.all_data if not _is_rail_row(x)]
        rail_rows = [x for x in self.all_data if _is_rail_row(x)]
        self.table.load_data(main_rows, self.custom_settings.get("highlights", {}))
        self._refresh_market_rail(rail_rows)

    def _refresh_market_rail(self, rail_rows):
        while self._rail_layout.count():
            child = self._rail_layout.takeAt(0)
            w = child.widget()
            if w:
                w.deleteLater()
        for it in rail_rows:
            self._rail_layout.addWidget(self._make_rail_card(it))
        self._rail_layout.addStretch()

    @staticmethod
    def _make_rail_card(item: dict) -> QFrame:
        mode = item.get('change_mode', 'pct')
        currency = item.get('currency', '')
        price = float(item.get('price', 0.0) or 0.0)
        chg = float(item.get('changes', {}).get('1d', 0.0) or 0.0)

        if item.get('is_bond') or mode == 'bp':
            value_text = f"{price:.2f}%"
            chg_text = f"{chg:+.0f}bp"
            tone = PROFIT if chg > 0 else LOSS if chg < 0 else FLAT
        elif mode == 'abs':
            # data/indicators.py's abs-mode period-change branch stores the
            # old price itself rather than a delta (a pre-existing quirk in
            # fetch_historical_changes), so there's no reliable day-over-day
            # figure to show here yet -- leave it blank rather than show a
            # misleading number.
            value_text = f"${price:,.2f}" if currency == '$' else f"{price:,.2f}"
            chg_text = "-"
            tone = FLAT
        else:
            value_text = f"${price:,.2f}" if currency == '$' else f"{price:,.0f}"
            chg_text = f"{chg:+.1f}%"
            tone = PROFIT if chg > 0 else LOSS if chg < 0 else FLAT

        card = QFrame()
        card.setObjectName("RailCard")
        card.setStyleSheet(
            f"QFrame#RailCard {{ background:{SURFACE}; border-left:2px solid {tone}; "
            f"border-top:1px solid {LINE}; border-bottom:1px solid {LINE}; border-right:1px solid {LINE}; "
            "border-radius:0 6px 6px 0; }}"
        )
        layout = QVBoxLayout(card)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(2)

        lbl = QLabel(item.get('name', item.get('ticker', '')))
        lbl.setFont(create_font(9, style_name="Semilight"))
        lbl.setStyleSheet(f"color: {TEXT_MUTED};")
        layout.addWidget(lbl)

        val_lbl = QLabel(value_text)
        val_lbl.setFont(create_font(11, QFont.Weight.Bold))
        layout.addWidget(val_lbl)

        chg_lbl = QLabel(chg_text)
        chg_lbl.setFont(create_font(9, style_name="Semilight"))
        chg_lbl.setStyleSheet(f"color: {tone};")
        layout.addWidget(chg_lbl)

        return card

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
        self._reload_table_and_rail()
        self.filter_table()
        self.update_total_status(prefix=f"Deleted '{ticker}'.")

    def toggle_stock(self, ticker):
        """Cycles a ticker's highlight state -/On/Tg (docs/ui.md 1.7: shown
        as a Watch/Target badge in the identity cell now, toggled from the
        table's context menu instead of a persistent per-row button)."""
        highlights = self.custom_settings.setdefault("highlights", {})
        current = highlights.get(ticker, "-")

        if current == "-":
            highlights[ticker] = "On"
        elif current == "On":
            highlights[ticker] = "Tg"
        else:
            highlights.pop(ticker, None)

        self.save_custom_settings()
        self.table.update_row_status(ticker, highlights.get(ticker, "-"))

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
            self.save_custom_settings()

        self.add_ticker_btn.setEnabled(False)
        self.status_text_changed.emit(f"Fetching '{ticker}' from {market}...")

        thread = self._track_thread(SingleStockFetchThread(market, ticker), '_single_fetch_thread')
        thread.finished.connect(self.on_single_stock_loaded)
        thread.start()

    def on_single_stock_loaded(self, result, error, ticker=""):
        """SingleStockFetchThread.finished for the Add button."""
        self._handle_single_stock_loaded(result, error, is_startup=False, ticker_hint=ticker)

    def _on_startup_stock_loaded(self, result, error, ticker=""):
        """SingleStockFetchThread.finished for user-added tickers re-fetched after a
        full refresh (on_finished_all); a failure removes the ticker from settings."""
        self._handle_single_stock_loaded(result, error, is_startup=True, ticker_hint=ticker)

    def _handle_single_stock_loaded(self, result, error, is_startup, ticker_hint):
        if not is_startup:
            self.add_ticker_btn.setEnabled(True)
        if error or result is None:
            if not is_startup:
                self.status_text_changed.emit(f"Error: {error}" if error else "Stock not found.")
            else:
                label = ticker_hint or (result.get('ticker', '') if result else '?')
                logger.warning("[Startup] Added ticker '%s' failed to load: %s", label, error or "No data returned")
                if label:
                    self.load_custom_settings()
                    added_list = self.custom_settings.get("added", [])
                    new_added = [x for x in added_list if x.get("ticker") != label]
                    if len(new_added) != len(added_list):
                        self.custom_settings["added"] = new_added
                        self.save_custom_settings()
                        logger.info("[Startup] Cleaned up invalid ticker '%s' from custom settings", label)
            return

        self.load_custom_settings()
        ticker = result.get('ticker', '')
        if ticker in self.custom_settings.get("deleted", []):
            return
        if any(x.get('ticker') == ticker for x in self.all_data):
            if not is_startup:
                self.status_text_changed.emit(f"'{ticker}' is already in the table.")
            return

        target_market = result.get('market', '') or self.market_combo.currentText()
        added_list = self.custom_settings.setdefault("added", [])
        if not any(x.get("ticker") == ticker for x in added_list):
            added_list.append({"market": target_market, "ticker": ticker})
            self.save_custom_settings()

        self.all_data.append(result)
        self.all_data.sort(key=lambda x: (
            0 if x.get('is_index') else 1,
            x.get('index_order', 99) if x.get('is_index') else _MARKET_ORDER.get(x.get('market', ''), 99),
            -float(x.get('market_cap', 0) or 0)
        ))
        self._reload_table_and_rail()
        self.filter_table()  # restore filter state

        if not is_startup:
            self.ticker_input.clear()
            msg = f"Added '{result.get('name', ticker)}' ({ticker})."
            self.update_total_status(prefix=msg)

    def filter_table(self, text=None):
        if text is None:
            text = self.search_input.text()
        tg_only = getattr(self, 'tg_filter_btn', None) is not None and self.tg_filter_btn.isChecked()
        self.table.apply_col_filters(text, tg_only=tg_only, market=self._market_filter)

    # ---Per-stock MA (20 + 60) ---
    def _on_ma_chart_requested(self, ticker: str):
        """StockTable.ma_chart_requested (double-click/Enter/context menu on
        a row) -- replaces the old per-row 📈 button, which carried its own
        name/market/change_mode via the callback closure; those are looked
        up from all_data instead now."""
        item = next((d for d in self.all_data if d.get('ticker') == ticker), None)
        if item is None:
            return
        self.show_stock_ma(ticker, item.get('name', ticker), item.get('market', ''), item.get('change_mode', 'pct'))

    def show_stock_ma(self, ticker, name, market, change_mode='pct'):
        self.status_text_changed.emit(f"Loading MA20 & MA50 for {name} ({ticker})...")
        thread = self._track_thread(StockMaThread(ticker, name, market, change_mode))
        thread.finished.connect(self.on_stock_ma_loaded)
        thread.start()

    def on_stock_ma_loaded(self, ticker, name, df, error, investor_data, market, change_mode):
        self.status_text_changed.emit(f"MA chart loaded for {name} ({ticker}).")
        if error and df is None:
            QMessageBox.warning(self, "Error", f"Failed to load data for {ticker}:\n{error}")
            return
        if not market:
            market = next((d.get('market', '') for d in self.all_data if d.get('ticker') == ticker), "")
        dlg = StockMaDialog(ticker, name, market, df, investor_data=investor_data, parent=None, change_mode=change_mode)
        dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
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

    # ---AI Stock Report (roadmap 2-1, review.md 2-1) ---
    def _on_ai_report_requested(self, ticker: str):
        item = next((d for d in self.all_data if d.get('ticker') == ticker), None)
        if item is None:
            QMessageBox.warning(self, "AI Stock Report", f"No data found for {ticker}.")
            return
        name = item.get('name', ticker)
        self.status_text_changed.emit(f"🤖 Generating AI report for {name} ({ticker})...")
        thread = self._track_thread(GeminiStockReportThread(item))
        thread.finished.connect(self._on_stock_report_ready)
        thread.start()

    def _on_stock_report_ready(self, ticker: str, name: str, result_text: str, error: str):
        if error:
            self.status_text_changed.emit(f"AI report failed for {name} ({ticker}).")
            QMessageBox.warning(self, "AI Stock Report", f"Failed to generate report:\n{error}")
            return
        self.status_text_changed.emit(f"AI report ready for {name} ({ticker}).")
        show_stock_report_result(self, ticker, name, result_text)

    def refresh_data(self):
        self.refresh_btn.setEnabled(False)
        self.all_data = []
        self.market_status = {m: "Waiting" for m in ("Indices", "KOSPI", "KOSDAQ")} #, "NASDAQ 100", "S&P500")}
        self.update_status_display()

        self._track_thread(AllDataFetchThread(), 'fetch_thread')
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

            self._track_thread(UniverseLightweightFetchThread(self.all_data), '_lw_fetch_thread')
            self._lw_fetch_thread.finished_all.connect(self._on_universe_lightweight_loaded)
            self._lw_fetch_thread.status_message.connect(self.status_message)
            self._lw_fetch_thread.start()

            self.auto_lightweight_tick.emit()
        else:
            # If empty, do a full refresh
            self.refresh_data()

    def _on_universe_lightweight_loaded(self, updated_data):
        # UniverseLightweightFetchThread preserves all_data's order/length
        # and only reassigns item["changes"] (to a freshly-fetched dict) for
        # tickers whose price actually moved -- every unchanged item keeps
        # the exact same "changes" dict object it had before the thread ran.
        # That lets us tell which rows actually need a UI refresh by object
        # identity, without a value-by-value diff.
        #
        # changed_rows must index into main_rows (what the table actually
        # holds, docs/ui.md 2.4), not all_data -- so old/new are filtered
        # the same way before comparing, not compared first and filtered
        # after.
        old_main = [x for x in self.all_data if not _is_rail_row(x)]
        new_main = [x for x in updated_data if not _is_rail_row(x)]
        changed_rows = {
            i for i, (old_item, new_item) in enumerate(zip(old_main, new_main))
            if old_item.get("changes") is not new_item.get("changes")
        }

        self.all_data = updated_data
        highlights = self.custom_settings.get("highlights", {})

        # Save current scroll position
        v_scroll = self.table.verticalScrollBar().value()

        # Incremental update: only the rows whose price/changes actually
        # changed get their cells rebuilt (identity/cap/PER never change on
        # this path, so there's no need to touch them at all, unlike a full
        # load_data()).
        self.table.update_changed_rows(new_main, changed_rows, highlights)
        self._refresh_market_rail([x for x in updated_data if _is_rail_row(x)])
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
                    x.get('index_order', 99) if x.get('is_index') else _MARKET_ORDER.get(x.get('market', ''), 99),
                    -float(x.get('market_cap', 0) or 0)
                )
            )
            self._reload_table_and_rail()
            self.filter_table(self.search_input.text())
            self.update_total_status()
            self.update_last_sync_time()

            # Cache the newly fetched data
            try:
                atomic_save_json(UNIVERSE_CACHE_FILE, self.all_data)
            except Exception as e:
                logger.warning("Error caching universe data: %s", e, exc_info=True)

            existing_tickers = {x.get("ticker") for x in self.all_data}
            missing_added = [x for x in added if x["ticker"] not in existing_tickers and x["ticker"] not in deleted]

            for item in missing_added:
                thread = self._track_thread(SingleStockFetchThread(item["market"], item["ticker"]))
                thread.finished.connect(self._on_startup_stock_loaded)
                thread.start()

        except Exception as e:
            logger.error("Data sort/load error in on_finished_all", exc_info=True)
            self.status_text_changed.emit(f"Data sort/load error: {e}")
        finally:
            self.refresh_btn.setEnabled(True)
