"""ui/history_tab.py — TradingHistoryTab (Phase 4 split)

Split out from: main.py (2026-08-29 feat/3-1-modularize, Phase 4)
Contains:
  TradingHistoryTab
"""
import logging
import datetime as _dt

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QTableWidget, QLineEdit, QPushButton,
    QLabel, QHeaderView, QComboBox, QMessageBox, QDialog, QFrame,
    QInputDialog, QMenu,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QSettings, QTimer
from PyQt6.QtGui import QKeySequence, QShortcut

import trade_db
from data_fetcher import is_kr_code

from threads.fetch_threads import (
    PositionPriceFetchThread,
    AccountDepositThread,
    SingleStockFetchThread,
)
from threads.realtime import RealtimePriceThread
from ui.widgets import GroupedHeaderView
from ui.dialogs import (
    BuyEditDialog,
    SellEditDialog,
    TradeEntryDialog,
    StockTradeHistoryDialog,
)

logger = logging.getLogger(__name__)


from ui.common import (
    create_font, _fmt_num_edit, FONT_FAMILY_CSS, ThreadOwnerMixin,
    _STATUS_SUCCESS_COLOR, FONT_KPI, FONT_CAPTION,
)
from ui.colors import PROFIT, LOSS
from ui.theme import ACCENT_TEXT, TEXT_FAINT
from ui.history_calc import compute_pl_fields, build_monthly_rows, summarize_positions
from ui.history_table import fill_table_rows, SectionTable, SECTIONS, COLUMNS
from ui.delegates import TradeStateDelegate
from ui.dialogs.holdings_summary import show_holdings_summary


# ── Dashboard card styling / widget factories for TradingHistoryTab._build_ui ──
# docs/ui.md Phase 0 (section 6.2): inputs/combos/cards/the position-summary
# table used to carry their own hardcoded QSS here, fighting whatever the
# global stylesheet said. They now inherit ui/theme.py's QLineEdit/QComboBox/
# QFrame#DashboardCard/QTableWidget rules instead -- nothing left to set here.
_BTN_H = 28
_FLD_W = 110   # field (label + widget) width per column


def _styled_button(text: str, role: str = None, on_click=None, *, width: int = _FLD_W,
                   height: int = _BTN_H, tooltip: str = "") -> QPushButton:
    """`role` is an objectName ("primary" | "danger") for ui/theme.py's
    QPushButton#primary/#danger rules, or None for the default neutral
    outline button (docs/ui.md 1.6: one accented action per screen, not one
    hue per button/feature)."""
    btn = QPushButton(text)
    btn.setFixedHeight(height)
    btn.setFixedWidth(width)
    if role:
        btn.setObjectName(role)
    if tooltip:
        btn.setToolTip(tooltip)
    if on_click is not None:
        btn.clicked.connect(on_click)
    return btn


def _make_rw_edit(placeholder: str = "") -> QLineEdit:
    e = QLineEdit()
    e.setPlaceholderText(placeholder)
    e.setAlignment(Qt.AlignmentFlag.AlignRight)
    e.setFixedWidth(110)
    e.setFixedHeight(_BTN_H)
    return e


class TradingHistoryTab(ThreadOwnerMixin, QWidget):
    """Trading History tab - load from Excel and display closed/open positions."""
    total_asset_updated = pyqtSignal(float)
    status_message = pyqtSignal(str)  # forwards background-thread progress text to MainWindow's status bar

    # Column headers/widths/section grouping all come from history_table.COLUMNS
    # now (docs/ui.md issue #9: this list, SECTIONS and _fit_columns's own
    # `mins` list used to be three places kept in sync by hand -- the Excel
    # header mapping this list used to mirror belongs in the importer only,
    # per that same issue's fix). Column order: Trading(3) | Buy(4) | Sell(7)
    # | Position(4) | Past(3).
    _COLS = [c.label for c in COLUMNS]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._closed_data  = []
        self._open_data    = []
        self._current_path = ""
        self._price_thread: QThread | None = None
        self._deposit_thread = None
        self._row_data: list = []   # (kind, rec) per visible table row
        self._settings = QSettings("PortfolioManagement", "PortfolioManagement")
        self._settings_save_timer = QTimer(self)
        self._settings_save_timer.setSingleShot(True)
        self._settings_save_timer.setInterval(400)
        self._settings_save_timer.timeout.connect(self._flush_settings)
        self._build_ui()
        self._load_settings()

        # Real-time price refresh is driven solely by MainWindow's global 60s
        # auto-update timer (UniverseTab.auto_lightweight_tick ->
        # _start_realtime_price_update), so the "Auto Update" checkbox really
        # does stop KIS/Yahoo polling. This tab used to run its own unconditional
        # QTimer on top of that (roadmap 6-1b).
        self._rt_price_thread = None

    _SETTINGS_KEYS = ("principal", "deposit", "withdrawal")
    # QSettings scope the app shipped with before roadmap 6-2f; read once so the
    # user does not lose the saved principal/deposit/withdrawal on upgrade.
    _LEGACY_SETTINGS_SCOPE = ("MyCompany", "PortfolioManager")

    def _migrate_legacy_settings(self):
        if any(self._settings.value(f"trading_history/{k}", "") for k in self._SETTINGS_KEYS):
            return
        legacy = QSettings(*self._LEGACY_SETTINGS_SCOPE)
        migrated = False
        for k in self._SETTINGS_KEYS:
            v = legacy.value(f"trading_history/{k}", "")
            if v:
                self._settings.setValue(f"trading_history/{k}", v)
                migrated = True
        if migrated:
            logger.info("Migrated Trading History settings from legacy QSettings scope")

    def _load_settings(self):
        self._migrate_legacy_settings()
        principal   = self._settings.value("trading_history/principal", "")
        deposit     = self._settings.value("trading_history/deposit", "")
        withdrawal  = self._settings.value("trading_history/withdrawal", "")
        if principal:
            self._principal_edit.setText(str(principal))
            _fmt_num_edit(self._principal_edit, str(principal))
        if deposit:
            self._deposit_edit.setText(str(deposit))
            _fmt_num_edit(self._deposit_edit, str(deposit))
        if withdrawal:
            self._withdrawal_edit.setText(str(withdrawal))
            _fmt_num_edit(self._withdrawal_edit, str(withdrawal))

    # ---UI construction ---
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(6)
        root.setContentsMargins(10, 8, 10, 8)

        root.addWidget(self._build_kpi_strip(), 0)
        root.addLayout(self._build_controls_row())
        root.addWidget(self._build_history_table(), 1)   # stretch=1: fills the rest

    def _build_kpi_strip(self) -> QFrame:
        """Read-only KPI strip (docs/ui.md 3.6, issue #7): Total Asset/Total
        P/L/Total P/L(%)/Total Invest render as plain KPI text now instead
        of QLineEdit(readOnly) fields styled like inputs you could type
        into. Principal/Deposit/Withdrawal are the tab's only input-able
        values (issue #7's fix: "입력 가능한 항목만 실제 필드로 남긴다"), so
        each keeps a real editable field, just styled to sit in the strip."""
        card = QFrame()
        card.setObjectName("DashboardCard")
        layout = QHBoxLayout(card)
        layout.setContentsMargins(16, 8, 16, 8)
        layout.setSpacing(0)

        self._kpi_labels = {}  # key -> (value QLabel | None, sub QLabel)
        cell_widgets = []  # equalized below so every gap between fields is identical

        def cell(key, label, sub_text="", editable_widget=None):
            box = QVBoxLayout()
            box.setSpacing(2)
            lbl = QLabel(label.upper())
            lbl.setFont(create_font(FONT_CAPTION, style_name="Semilight"))
            lbl.setStyleSheet(f"color:{TEXT_FAINT}; letter-spacing:.05em;")
            box.addWidget(lbl)
            value_lbl = None
            if editable_widget is not None:
                editable_widget.setFont(create_font(FONT_KPI, style_name="Semilight"))
                editable_widget.setStyleSheet("border:none; padding:0px; background:transparent;")
                box.addWidget(editable_widget)
            else:
                value_lbl = QLabel("-")
                value_lbl.setFont(create_font(FONT_KPI, style_name="Semilight"))
                box.addWidget(value_lbl)
            sub_lbl = QLabel(sub_text)
            sub_lbl.setFont(create_font(FONT_CAPTION, style_name="Semilight"))
            sub_lbl.setStyleSheet(f"color:{TEXT_FAINT};")
            box.addWidget(sub_lbl)
            self._kpi_labels[key] = (value_lbl, sub_lbl)
            cell_widget = QWidget()
            cell_widget.setLayout(box)
            cell_widgets.append(cell_widget)
            layout.addWidget(cell_widget, 1)

        cell("total_asset", "Total Asset", "Valuation + cash")
        cell("total_pl", "Total P/L", "Realized + unrealized")
        cell("total_pl_pct", "Total P/L (%)", "vs Principal")
        self._principal_edit = self._make_money_input("e.g. 50,000,000")
        cell("principal", "Principal", "Deposits − withdrawals", self._principal_edit)
        cell("total_invest", "Total Invest", "Cost basis")
        self._deposit_edit = self._make_money_input("e.g. 10,000,000")
        cell("deposit", "Deposit", "", self._deposit_edit)
        self._withdrawal_edit = self._make_money_input("e.g. 5,000,000")
        cell("withdrawal", "Withdrawal", "Cumulative", self._withdrawal_edit)

        # Content widths vary a lot (e.g. "Realized + unrealized" vs. "Cost
        # basis"), which used to make the gap between fields look uneven even
        # though every cell shared the same stretch factor. Giving every cell
        # the same minimum width makes the columns -- and the gaps between
        # them -- genuinely equal.
        max_w = max(cw.sizeHint().width() for cw in cell_widgets)
        for cw in cell_widgets:
            cw.setMinimumWidth(max_w)
        return card

    def _make_money_input(self, placeholder: str) -> QLineEdit:
        """Editable KRW field: re-formats with thousands separators as you type
        and schedules a debounced QSettings save."""
        edit = _make_rw_edit(placeholder)
        edit.textEdited.connect(self._on_deposit_changed)
        edit.textEdited.connect(lambda t, e=edit: _fmt_num_edit(e, t))
        return edit

    def _build_controls_row(self) -> QHBoxLayout:
        """Single toolbar row: Fetch/Reload/Add Trade | Summary | Sort by
        Date | Current Holdings combo | Search | Period filter | ... |
        30-day-rule toggle. Used to be split across two dashboard cards plus
        a separate controls row (with a KR/US/Total position table and an
        All/Open/Closed status filter alongside them); both were removed and
        everything that is left was consolidated into this one row (user
        direction, 2026-09-20)."""
        row = QHBoxLayout()
        row.setSpacing(10)
        row.setContentsMargins(0, 0, 0, 0)

        # Add Trade is this row's one primary action (docs/ui.md 1.6);
        # Fetch/Reload are secondary utilities and stay neutral.
        self._fetch_dep_btn = _styled_button("🔄 Fetch", None, self._fetch_account_deposit)
        reload_btn = _styled_button("🔄 Reload", None, self._reload_current)
        add_btn = _styled_button("➕ Add Trade", "primary", self._show_add_trade_dialog)
        self._deposit_status_lbl = QLabel("")
        self._deposit_status_lbl.setStyleSheet(f"font-size:9pt; color:{_STATUS_SUCCESS_COLOR}; font-weight:bold;")
        self._deposit_status_lbl.setFixedHeight(_BTN_H)
        for w in (self._fetch_dep_btn, reload_btn, add_btn):
            row.addWidget(w)

        row.addWidget(_styled_button("Summary", None, self._show_holdings_summary))

        self._sort_by_date = False
        # Checked state comes from ui/theme.py's QPushButton:checked rule.
        self._sort_date_btn = _styled_button("Sort by Date", None, width=120)
        self._sort_date_btn.setCheckable(True)
        self._sort_date_btn.toggled.connect(self._on_sort_date_toggled)
        row.addWidget(self._sort_date_btn)

        self._open_stocks_combo = QComboBox()
        self._open_stocks_combo.addItem("Current Holdings...")
        self._open_stocks_combo.setFixedHeight(_BTN_H)
        self._open_stocks_combo.setFixedWidth(250)
        self._open_stocks_combo.currentTextChanged.connect(self._on_open_stock_combo_changed)
        row.addWidget(self._open_stocks_combo)

        self._search_stock_pl_edit = QLineEdit()
        self._search_stock_pl_edit.setPlaceholderText("Search Company")
        self._search_stock_pl_edit.setFixedHeight(_BTN_H)
        self._search_stock_pl_edit.setFixedWidth(250)
        self._search_stock_pl_edit.returnPressed.connect(self._on_search_stock_pl)
        row.addWidget(self._search_stock_pl_edit)
        row.addWidget(_styled_button("🔍", None, self._on_search_stock_pl, width=36))

        # Period filter (mockup "periods": 1M/3M/YTD/All), by buy date.
        self._period_filter = "All"
        self._period_buttons = {}
        for label in ("1M", "3M", "YTD", "All"):
            btn = _styled_button(label, None, width=44)
            btn.setCheckable(True)
            btn.setChecked(label == "All")
            btn.clicked.connect(lambda checked, p=label: self._on_period_filter_changed(p))
            row.addWidget(btn)
            self._period_buttons[label] = btn

        row.addStretch()

        # docs/ui.md 3.5: the 30-day rule used to be hardcoded with no UI
        # trace of it, so a user seeing a blank current-price cell had no
        # way to know why.
        self._hide_stale_closed = True
        self._stale_toggle_btn = _styled_button("Hide price 30d+ after close", None, width=190)
        self._stale_toggle_btn.setCheckable(True)
        self._stale_toggle_btn.setChecked(True)
        self._stale_toggle_btn.toggled.connect(self._on_stale_toggle_changed)
        row.addWidget(self._stale_toggle_btn)

        row.addWidget(self._deposit_status_lbl)

        self._path_label = QLabel("")
        self._path_label.setStyleSheet(f"color:{TEXT_FAINT}; font-size:{FONT_CAPTION}pt; border:none;")
        row.addWidget(self._path_label)

        return row

    def _on_sort_date_toggled(self, checked: bool):
        self._sort_by_date = checked
        self._sort_date_btn.setText("🔄 Sort by Position" if checked else "📅 Sort by Date")
        self._apply_filter()

    def _on_period_filter_changed(self, period: str):
        self._period_filter = period
        for p, btn in self._period_buttons.items():
            btn.setChecked(p == period)
        self._apply_filter()

    def _on_stale_toggle_changed(self, checked: bool):
        self._hide_stale_closed = checked
        self._apply_filter()

    def _build_history_table(self) -> QTableWidget:
        """The unified closed/open/monthly trade grid with the two-row grouped header."""
        tbl = self._table = SectionTable(SECTIONS)
        tbl.setColumnCount(len(self._COLS))
        tbl.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        tbl.setAlternatingRowColors(False)
        tbl.setSortingEnabled(False)
        tbl.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        tbl.setFont(create_font(9, style_name="Semilight"))
        tbl.verticalHeader().setDefaultSectionSize(22)   # fixed 22px rows: margin against the font
        tbl.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        tbl.verticalHeader().setVisible(False)

        grouped_hdr = GroupedHeaderView(SECTIONS, self._COLS, tbl)
        grouped_hdr.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        grouped_hdr.setMinimumSectionSize(40)
        tbl.setHorizontalHeader(grouped_hdr)
        tbl.setStyleSheet(
            "QTableWidget { gridline-color: #d0d0d0; " + FONT_FAMILY_CSS + " font-size: 9pt; }"
            "QTableWidget::item { padding: 1px 3px; }"
        )
        tbl.setItemDelegateForColumn(0, TradeStateDelegate(tbl))
        tbl.cellDoubleClicked.connect(self._on_cell_double_clicked)
        # Delete: right-click menu or the Delete key on the selected rows.
        # trade_db.delete_trade() existed for a long time with no UI path to
        # it, so a mistyped trade could only be removed by editing the DB.
        tbl.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        tbl.customContextMenuRequested.connect(self._on_table_context_menu)
        self._delete_shortcut = QShortcut(QKeySequence.StandardKey.Delete, tbl)
        self._delete_shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
        self._delete_shortcut.activated.connect(self._delete_selected_trades)
        return tbl

    def _selected_trade_records(self) -> list:
        """(kind, rec) for every selected row that is a real trade (month
        group-header rows are skipped)."""
        rows = sorted({idx.row() for idx in self._table.selectedIndexes()})
        return [self._row_data[r] for r in rows if r < len(self._row_data) and self._row_data[r][0] != "monthly"]

    def _on_table_context_menu(self, pos):
        if not self._selected_trade_records():
            return
        menu = QMenu(self._table)
        delete_action = menu.addAction("Delete Trade...")
        if menu.exec(self._table.viewport().mapToGlobal(pos)) is delete_action:
            self._delete_selected_trades()

    def _delete_selected_trades(self):
        targets = self._selected_trade_records()
        if not targets:
            return
        names = ", ".join(f"{rec.get('company', '')} ({rec.get('buy_date', '')})" for _, rec in targets[:3])
        if len(targets) > 3:
            names += f" and {len(targets) - 3} more"
        reply = QMessageBox.question(
            self, "Delete Trade",
            f"Delete {len(targets)} trade(s)?\n{names}\n\nThis removes them from portfolio.db.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        for kind, rec in targets:
            key = rec.get("orig_key")
            try:
                if key:
                    trade_db.delete_trade(key)
            except Exception as e:
                logger.error("Failed to delete trade %s: %s", key, e, exc_info=True)
                QMessageBox.warning(self, "Database Error", f"Failed to delete trade:\n{e}")
                break
            source = self._closed_data if kind == "closed" else self._open_data
            if rec in source:
                source.remove(rec)
        self._refresh_summary()
        self._apply_filter()

    def _fit_columns(self):
        """Set column widths to fill the viewport without horizontal scrolling
        -- min-widths come from history_table.COLUMNS (docs/ui.md issue #9)
        instead of a `mins` list hand-aligned to _COLS by comment. Only
        Company (col 0) flexes to fill the remaining space; every other
        column is a genuinely fixed-content width."""
        tbl = self._table
        viewport_w = tbl.viewport().width()
        if viewport_w <= 0:
            return

        fixed_total = sum(c.min_width for c in COLUMNS[1:])
        name_w = max(COLUMNS[0].min_width, viewport_w - fixed_total)

        # If everything doesn't fit, allow horizontal scroll instead of squeezing
        # (Allow horizontal scroll -> prevent text cutoff)
        tbl.setColumnWidth(0, name_w)
        for i, spec in enumerate(COLUMNS[1:], start=1):
            tbl.setColumnWidth(i, spec.min_width)


    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_columns()

    def showEvent(self, event):
        """Triggered when the tab becomes visible - ensures columns fill the viewport."""
        super().showEvent(event)
        QTimer.singleShot(0, self._fit_columns)

    # ---Load from JSON only (no Excel file required) ---
    def load_from_db(self):
        """Load all trade data from SQLite DB (portfolio.db)."
        This is the primary data source, replacing the old JSON files."""
        all_trades = trade_db.load_all_trades()
        self._closed_data = []
        self._open_data   = []
        for rec in all_trades:
            self._compute_pl_fields(rec)
            if rec.get("sell_date") or rec.get("sell_price"):
                self._closed_data.append(rec)
            else:
                self._open_data.append(rec)
        self._refresh_summary()
        self._apply_filter()
        self._start_price_fetch()

    def _reload_current(self):
        self.load_from_db()

    def _save_custom_trade(self, record) -> bool:
        """Persist a manually-added trade to the SQLite database."""
        try:
            saved_key = trade_db.upsert_trade(record)
            record["orig_key"] = saved_key
            return True
        except Exception as e:
            logger.error("Failed to save trade to DB: %s", e, exc_info=True)
            QMessageBox.critical(
                self, "Database Error",
                f"Failed to save trade to database:\n{e}\n\nThe record was not added."
            )
            return False


    # ---Delegates to the split-out modules (kept so callers/tests are unchanged) ---
    @staticmethod
    def _compute_pl_fields(rec: dict) -> None:
        """See ui.history_calc.compute_pl_fields."""
        compute_pl_fields(rec)

    @staticmethod
    def _build_monthly_rows(all_rows: list) -> list:
        """See ui.history_calc.build_monthly_rows."""
        return build_monthly_rows(all_rows)

    def _show_holdings_summary(self):
        show_holdings_summary(self, self._closed_data, self._open_data)

    def _save_overrides(self, records: list):
        """Persist just the given edited records back to the DB.

        Used to re-upsert every closed/overridden/custom trade on each edit
        (one cell change -> N rows rewritten, growing with the trade log);
        every call site knows exactly which record(s) it changed, so it
        passes those instead."""
        try:
            if records:
                trade_db.upsert_trades(records)
        except Exception as e:
            logger.error("Failed to save overrides to DB: %s", e, exc_info=True)
            QMessageBox.warning(
                self, "Database Warning",
                f"Failed to save modified trades to database:\n{e}"
            )


    # ---Real-time lightweight price fetch (1-min loop) ---
    def _start_realtime_price_update(self):
        if not self._open_data and not self._closed_data:
            return
        
        kr_tickers = set()
        us_tickers = set()
        
        # Open positions
        for r in self._open_data:
            ticker = r.get("ticker")
            if not ticker:
                continue
            market = r.get("market", "")
            if market in ("KOSPI", "KOSDAQ") or is_kr_code(ticker):
                kr_tickers.add(ticker)
            else:
                us_tickers.add(ticker)
                
        # Recently closed positions (Opportunity Cost tracking)
        for r in self._closed_data:
            if r.get("curr_days", 999) <= 30:
                ticker = r.get("ticker")
                if not ticker:
                    continue
                market = r.get("market", "")
                if market in ("KOSPI", "KOSDAQ") or is_kr_code(ticker):
                    kr_tickers.add(ticker)
                else:
                    us_tickers.add(ticker)
                
        if not kr_tickers and not us_tickers:
            return
            
        if self._rt_price_thread is not None and self._rt_price_thread.isRunning():
            return
            
        self._track_thread(RealtimePriceThread(list(kr_tickers), list(us_tickers)), '_rt_price_thread')
        self._rt_price_thread.prices_fetched.connect(self._on_realtime_prices_fetched)
        self._rt_price_thread.status_message.connect(self.status_message.emit)
        self._rt_price_thread.start()

    def _on_realtime_prices_fetched(self, prices_dict):
        if not prices_dict:
            return
            
        updated = False
        
        # Update open positions
        for r in self._open_data:
            ticker = r.get("ticker", "")
            if ticker in prices_dict:
                new_price = prices_dict[ticker]
                if r.get("curr_price", 0.0) != new_price:
                    r["curr_price"] = new_price
                    updated = True
                    
        # Update recently closed positions
        for r in self._closed_data:
            if r.get("curr_days", 999) <= 30:
                ticker = r.get("ticker", "")
                if ticker in prices_dict:
                    new_price = prices_dict[ticker]
                    if r.get("curr_price", 0.0) != new_price:
                        r["curr_price"] = new_price
                        # Update curr_pl_pct for historical closed items (opportunity cost %)
                        sell_price = r.get("sell_price", 0.0)
                        if sell_price > 0:
                            r["curr_pl_pct"] = (new_price - sell_price) / sell_price * 100
                        updated = True
                        
        if updated:
            self._refresh_summary()
            self._apply_filter()


    # ---Full price fetch for open positions ---
    def _start_price_fetch(self):
        """Launch a background thread to fetch current prices for open positions, and tickers for all."""
        if not self._open_data and not self._closed_data:
            return

        names      = []
        tickers    = []
        markets    = []
        buy_prices = []
        qtys       = []
        buy_amts   = []
        is_open    = []
        skip_fetch = []
        
        for r in self._closed_data:
            days_since = 0
            if r.get("sell_date"):
                try:
                    sd = _dt.datetime.strptime(r["sell_date"], "%Y-%m-%d").date()
                    days_since = (_dt.date.today() - sd).days
                except Exception:
                    logger.debug(
                        "days_since calculation failed for sell_date=%s", r.get("sell_date"), exc_info=True,
                    )

            names.append(r["company"])
            tickers.append(r.get("ticker", ""))
            markets.append(r.get("market", ""))
            
            skip_fetch.append(days_since > 30)
            
            buy_prices.append(0)
            qtys.append(0)
            buy_amts.append(0)
            is_open.append(False)
            
        for r in self._open_data:
            names.append(r["company"])
            tickers.append(r.get("ticker", ""))
            markets.append(r.get("market", ""))
            skip_fetch.append(False)
            buy_prices.append(r["buy_price"])
            qtys.append(r["qty"])
            buy_amts.append(r["buy_amount"])
            is_open.append(True)

        thread = self._track_thread(PositionPriceFetchThread(
            names, tickers, markets, buy_prices, qtys, buy_amts, is_open, skip_fetch,
        ), '_price_thread')
        thread.prices_ready.connect(self._on_prices_ready)
        thread.status_message.connect(self.status_message.emit)
        self._path_label.setText("⏳ Loading Current Prices...")
        thread.start()

    def _on_prices_ready(self, results: list):
        """
        results: list of dicts with keys:
          index, curr_price, curr_pl, curr_pl_pct, ticker, market
        """
        self._path_label.setText("")
        updated = False
        num_closed = len(self._closed_data)
        
        for res in results:
            idx = res["index"]
            curr_price = res.get("curr_price", 0.0)
            if idx < num_closed:
                # Closed data
                if res.get("ticker"):
                    self._closed_data[idx]["ticker"] = res.get("ticker")
                if res.get("market"):
                    self._closed_data[idx]["market"] = res.get("market")
                if res.get("name"):
                    self._closed_data[idx]["company"] = res.get("name")
                self._closed_data[idx]["curr_price"] = curr_price
                if curr_price > 0:
                    # col 17 "Position P/L(%)" = Change rate of current price vs sell price (reference for opportunity cost after selling)
                    sell_price = self._closed_data[idx].get("sell_price", 0.0)
                    if sell_price > 0:
                        self._closed_data[idx]["curr_pl_pct"] = (curr_price - sell_price) / sell_price * 100


                # Map past % changes for closed positions
                if "wk1" in res and res["wk1"] != 0.0:
                    self._closed_data[idx]["wk1"] = res["wk1"]
                if "wk2" in res and res["wk2"] != 0.0:
                    self._closed_data[idx]["wk2"] = res["wk2"]
                if "mth1" in res and res["mth1"] != 0.0:
                    self._closed_data[idx]["mth1"] = res["mth1"]

                updated = True
            else:
                # Open data: save curr_price only, P/L is calculated real-time in _refresh_summary
                open_idx = idx - num_closed
                if 0 <= open_idx < len(self._open_data):
                    if res.get("ticker"):
                        self._open_data[open_idx]["ticker"] = res.get("ticker")
                    if res.get("market"):
                        self._open_data[open_idx]["market"] = res.get("market")
                    if res.get("name"):
                        self._open_data[open_idx]["company"] = res.get("name")
                    self._open_data[open_idx]["curr_price"] = res.get("curr_price", 0.0)

                    if "wk1" in res and res["wk1"] != 0.0:
                        self._open_data[open_idx]["wk1"] = res["wk1"]
                    if "wk2" in res and res["wk2"] != 0.0:
                        self._open_data[open_idx]["wk2"] = res["wk2"]
                    if "mth1" in res and res["mth1"] != 0.0:
                        self._open_data[open_idx]["mth1"] = res["mth1"]

                    updated = True
                    
        if updated:
            self._refresh_summary()
            self._apply_filter()

    # ---Input handler ---
    def _flush_settings(self):
        """Flush principal/deposit/withdrawal to QSettings (called by debounce timer)."""
        self._settings.setValue("trading_history/principal",  self._principal_edit.text())
        self._settings.setValue("trading_history/deposit",    self._deposit_edit.text())
        self._settings.setValue("trading_history/withdrawal", self._withdrawal_edit.text())

    def _on_deposit_changed(self, *args, **kwargs):
        """Recompute summary whenever the user edits input fields."""
        try:
            # Debounce: write settings 400 ms after the last keystroke
            self._settings_save_timer.start()
            self._refresh_summary()
        except Exception as e:
            logger.error("Error in _on_deposit_changed: %s", e, exc_info=True)

    def _get_deposit(self) -> float:
        raw = self._deposit_edit.text().replace(',', '').strip()
        try:
            return float(raw) if raw else 0.0
        except ValueError:
            return 0.0

    def _get_withdrawal(self) -> float:
        raw = self._withdrawal_edit.text().replace(',', '').strip()
        try:
            return float(raw) if raw else 0.0
        except ValueError:
            return 0.0

    def _get_principal(self) -> float:
        raw = self._principal_edit.text().replace(',', '').strip()
        try:
            return float(raw) if raw else 0.0
        except ValueError:
            return 0.0

    def _fetch_account_deposit(self):
        """Fetch button: run the KIS balance inquiry off the UI thread (roadmap 6-1c)."""
        if self._deposit_thread is not None and self._deposit_thread.isRunning():
            return
        self._fetch_dep_btn.setEnabled(False)
        self._deposit_status_lbl.setStyleSheet(f"font-size:10pt; color:{ACCENT_TEXT}; font-weight:bold;")
        self._deposit_status_lbl.setText("⏳ Fetching deposit...")
        self.status_message.emit("Fetching account deposit from KIS...")
        self._track_thread(AccountDepositThread(), '_deposit_thread')
        self._deposit_thread.finished.connect(self._on_account_deposit_fetched)
        self._deposit_thread.start()

    def _on_account_deposit_fetched(self, val: float, err: str):
        self._fetch_dep_btn.setEnabled(True)
        if err:
            self._deposit_status_lbl.setStyleSheet(f"font-size:10pt; color:{PROFIT}; font-weight:bold;")
            self._deposit_status_lbl.setText("❌ Failed to fetch")
            QTimer.singleShot(5000, lambda: self._deposit_status_lbl.setText(""))
            QMessageBox.critical(self, "Error", f"Failed to fetch data:\n{err}")
            return
        self._deposit_edit.setText(f"{int(val):,}")
        self._on_deposit_changed()
        # Inline status display (instead of QMessageBox) - immediate edit possible
        self._deposit_status_lbl.setStyleSheet(f"font-size:10pt; color:{_STATUS_SUCCESS_COLOR}; font-weight:bold;")
        self._deposit_status_lbl.setText(f"💰 {int(val):,} KRW (Est.)")
        QTimer.singleShot(4000, lambda: self._deposit_status_lbl.setText(""))
        self._deposit_edit.selectAll()
        self._deposit_edit.setFocus()

    # ---Summary ---
    def _refresh_summary(self):
        """Recompute the position aggregates (ui.history_calc.summarize_positions)
        and render them into the KPI strip."""
        deposit    = self._get_deposit()
        withdrawal = self._get_withdrawal()
        principal  = self._get_principal()

        agg = summarize_positions(
            self._open_data, self._closed_data,
            deposit=deposit, withdrawal=withdrawal, principal=principal,
        )
        total, total_pl = agg["total"], agg["total_pl"]
        self.total_asset_updated.emit(total)

        def _pos_color(v): return LOSS if v < 0 else PROFIT

        # ---Update the KPI strip (docs/ui.md 3.6) ---
        def set_kpi(key, text, color=None, tooltip=None):
            val_lbl, sub_lbl = self._kpi_labels[key]
            if val_lbl is not None:
                val_lbl.setText(text)
                if color:
                    val_lbl.setStyleSheet(f"color:{color};")
                if tooltip:
                    val_lbl.setToolTip(tooltip)
            if key == "deposit":
                sub_lbl.setText(f"{agg['deposit_pct']:.1f}% of invested")

        set_kpi("total_asset", f"{total:,.0f}")
        set_kpi("total_pl", f"{total_pl:+,.0f}", _pos_color(total_pl),
                 tooltip=f"Total Asset ({total:,.0f}) - Principal ({principal:,.0f})")
        set_kpi("total_pl_pct", f"{agg['total_pl_pct']:+.1f}%", _pos_color(agg["total_pl_pct"]))
        set_kpi("total_invest", f"{agg['total_invest']:,.0f}")
        set_kpi("deposit", None)  # value is the live QLineEdit; only the sub-label updates

    def _on_search_stock_pl(self):
        query = self._search_stock_pl_edit.text().strip().lower()
        if not query:
            return
            
        total_pl = 0.0
        total_buy = 0.0
        total_sell = 0.0
        matches = []
        matched_company = ""
        
        for item in getattr(self, '_closed_data', []):
            comp = item.get("company", "")
            if query in comp.lower():
                matches.append(item)
                total_pl += float(item.get("pl", 0.0))
                total_buy += float(item.get("buy_amount", 0.0))
                total_sell += float(item.get("sell_amount", 0.0))
                if not matched_company:
                    matched_company = comp
                
        if not matches:
            QMessageBox.information(self, "Search Result", f"No completed trading history found for '{self._search_stock_pl_edit.text()}'.")
            return
            
        dlg = StockTradeHistoryDialog(matched_company, matches, total_pl, total_buy, total_sell, self)
        dlg.exec()

    def _update_open_stocks_combo(self):
        self._open_stocks_combo.blockSignals(True)
        self._open_stocks_combo.clear()
        self._open_stocks_combo.addItem("Current Holdings...")
        
        companies = []
        for item in self._open_data:
            comp = item.get("company", "")
            if comp and comp not in companies:
                companies.append(comp)
                
        companies.sort()
        for comp in companies:
            self._open_stocks_combo.addItem(comp)
            
        self._open_stocks_combo.setCurrentIndex(0)
        self._open_stocks_combo.blockSignals(False)

    def _on_open_stock_combo_changed(self, text):
        if not text or text == "Current Holdings...":
            return
            
        query = text.strip().lower()
        total_pl = 0.0
        total_buy = 0.0
        total_sell = 0.0
        matches = []
        matched_company = ""
        
        for item in self._open_data:
            comp = item.get("company", "")
            if query == comp.lower():
                rec = item.copy()
                curr_price = float(rec.get("curr_price", 0.0))
                qty = float(rec.get("qty", 0.0))
                buy_amt = float(rec.get("buy_amount", 0.0))
                eval_amt = curr_price * qty
                
                rec["sell_date"] = "Open"
                rec["sell_price"] = curr_price
                rec["sell_qty"] = qty
                rec["sell_amount"] = eval_amt
                
                pl = eval_amt - buy_amt
                pl_pct = (pl / buy_amt * 100) if buy_amt > 0 else 0.0
                
                rec["pl"] = pl
                rec["pl_pct"] = pl_pct
                
                matches.append(rec)
                total_pl += pl
                total_buy += buy_amt
                total_sell += eval_amt
                
                if not matched_company:
                    matched_company = comp

        if not matches:
            QMessageBox.information(self, "Search Result", f"No trading history found for '{text}'.")
            self._open_stocks_combo.blockSignals(True)
            self._open_stocks_combo.setCurrentIndex(0)
            self._open_stocks_combo.blockSignals(False)
            return
            
        dlg = StockTradeHistoryDialog(matched_company, matches, total_pl, total_buy, total_sell, self, is_open_position=True)
        dlg.exec()
        
        self._open_stocks_combo.blockSignals(True)
        self._open_stocks_combo.setCurrentIndex(0)
        self._open_stocks_combo.blockSignals(False)

    # ---Filter / refresh ---
    def _apply_filter(self, *_):
        state = getattr(self, "_state_filter", "All")
        period = getattr(self, "_period_filter", "All")

        # docs/ui.md 3.3: the status filter narrows which rows show, never
        # which columns exist (Buy/Sell/Position/Past structure is fixed).
        closed_src = self._closed_data if state in ("All", "Closed") else []
        open_src = self._open_data if state in ("All", "Open") else []

        cutoff = self._period_cutoff_date(period)
        if cutoff is not None:
            closed_src = [r for r in closed_src if r.get("buy_date", "") >= cutoff]
            open_src = [r for r in open_src if r.get("buy_date", "") >= cutoff]

        closed_rows = [("closed", r) for r in closed_src]
        open_rows   = [("open", r) for r in open_src]

        sort_by_date = getattr(self, "_sort_by_date", False)

        if sort_by_date:
            # All rows sorted by buy_date descending (closed + open together,
            # most recently bought first), with a summary row appended after
            # each calendar month's group (build_monthly_rows groups rows by
            # first-seen month, so the most recent month lands first here too).
            all_rows = closed_rows + open_rows
            all_rows.sort(key=lambda x: x[1]["buy_date"], reverse=True)
            rows = self._build_monthly_rows(all_rows)
        else:
            # Default: closed (most recently bought first) then open (most
            # recently bought first) -- user request: recently bought stocks
            # at the top instead of the bottom.
            closed_rows.sort(key=lambda x: x[1]["buy_date"], reverse=True)
            open_rows.sort(key=lambda x: x[1]["buy_date"], reverse=True)
            rows = closed_rows + open_rows
        self._fill_table(rows)

    @staticmethod
    def _period_cutoff_date(period: str):
        """1M/3M/YTD/All toolbar filter -- returns the earliest buy_date
        (YYYY-MM-DD string) to include, or None for no cutoff."""
        today = _dt.date.today()
        if period == "1M":
            return (today - _dt.timedelta(days=30)).strftime("%Y-%m-%d")
        if period == "3M":
            return (today - _dt.timedelta(days=90)).strftime("%Y-%m-%d")
        if period == "YTD":
            return _dt.date(today.year, 1, 1).strftime("%Y-%m-%d")
        return None

    # ---Table item helpers ---
    # ---Unified table fill ---
    def _fill_table(self, rows: list):
        tbl = self._table
        tbl.setSortingEnabled(False)
        tbl.setUpdatesEnabled(False)
        try:
            self._row_data = fill_table_rows(
                tbl, rows, hide_stale_closed=getattr(self, "_hide_stale_closed", True),
            )
        finally:
            tbl.setUpdatesEnabled(True)
        self._update_open_stocks_combo()
        self._fit_columns()
        tbl.scrollToTop()

    # ---Buy/Sell cell double-click edit ---
    # Editable columns: Buy(3=Date, 4=Price, 5=Qty, 6=Amount), Sell(8=Date, 10=Price, 11=Qty, 12=Amount)
    _EDITABLE_COLS = {
        3:  ("buy_date",    "Buy Date (YYYY-MM-DD)", "str"),
        4:  ("buy_price",   "Buy Price",               "float"),
        5:  ("qty",         "Buy Quantity",               "float"),
        6:  ("buy_amount",  "Buy Amount",               "float"),
        7:  ("sell_date",   "Sell Date (YYYY-MM-DD)", "str"),
        9:  ("sell_price",  "Sell Price",               "float"),
        10: ("sell_qty",    "Sell Quantity",               "float"),
        11: ("sell_amount", "Sell Amount",               "float"),
    }

    def _on_cell_double_clicked(self, row: int, col: int):
        """Edit a Buy/Sell field of a position via double-click."""
        if row >= len(self._row_data):
            return
        kind, rec = self._row_data[row]

        # docs/ui.md 3.4: a month group-header row is not a trade -- it used
        # to have no guard here at all, so double-clicking one opened
        # Buy/SellEditDialog on the fake aggregate record (silently
        # discarded on save since it's never in _open_data/_closed_data, but
        # confusing: nothing told the user their "edit" went nowhere).
        if kind == "monthly":
            return

        if col == 0:
            curr_val = rec.get("company", "")
            new_str, ok = QInputDialog.getText(self, "Edit", "Company Name:", text=str(curr_val))
            if ok and new_str.strip():
                rec["company"] = new_str.strip()
                rec["is_overridden"] = True
                self._save_overrides([rec])
                self._refresh_summary()
                self._apply_filter()
            return
            
        if col == 2:
            curr_val = rec.get("ticker", "")
            new_str, ok = QInputDialog.getText(self, "Edit", "Ticker:", text=str(curr_val))
            if ok and new_str.strip():
                new_ticker = new_str.strip()
                rec["ticker"] = new_ticker
                rec["is_overridden"] = True
                self._save_overrides([rec])

                self._start_price_fetch()
                self._refresh_summary()
                self._apply_filter()

                # Resolve the company name for the new ticker in the background
                # (used to be a synchronous fetch_single_stock call on the UI
                # thread, roadmap 6-1c). The record is updated again when it lands.
                thread = self._track_thread(SingleStockFetchThread(rec.get("market", ""), new_ticker))
                thread.finished.connect(self._on_ticker_name_resolved)
                thread.start()
            return

        if col in {3, 4, 5, 6}:
            dlg = BuyEditDialog(rec, self)
            if dlg.exec() == QDialog.DialogCode.Accepted and dlg.result_data:
                res = dlg.result_data
                rec["buy_date"]   = res["buy_date"]
                rec["buy_price"]  = res["buy_price"]
                rec["qty"]        = res["qty"]
                rec["buy_amount"] = res["buy_amount"]
                rec["is_overridden"] = True
                self._compute_pl_fields(rec)
                self._refresh_summary()
                self._apply_filter()
                self._save_overrides([rec])
            return

        if col in {7, 9, 10, 11}:
            dlg = SellEditDialog(rec, self)
            if dlg.exec() == QDialog.DialogCode.Accepted and dlg.result_data:
                res = dlg.result_data
                rec["sell_date"]   = res["sell_date"]
                rec["sell_price"]  = res["sell_price"]
                rec["sell_qty"]    = res["sell_qty"]
                rec["sell_amount"] = res["sell_amount"]  # dialog already fills price*qty when blank
                rec["is_overridden"] = True
                self._compute_pl_fields(rec)

                is_now_closed = bool(rec.get("sell_date") or rec.get("sell_price"))
                if kind == "open" and is_now_closed:
                    if rec in self._open_data:
                        self._open_data.remove(rec)
                    self._closed_data.append(rec)
                elif kind == "closed" and not is_now_closed:
                    if rec in self._closed_data:
                        self._closed_data.remove(rec)
                    self._open_data.append(rec)

                self._refresh_summary()
                self._apply_filter()
                self._save_overrides([rec])
            return

    def _on_ticker_name_resolved(self, result, error: str, ticker: str):
        """SingleStockFetchThread.finished for the ticker-cell edit above: apply the
        resolved company name to every record now carrying that ticker."""
        name = (result or {}).get("name")
        if not name:
            return
        changed = []
        for rec in self._open_data + self._closed_data:
            if rec.get("ticker") == ticker and rec.get("company") != name:
                rec["company"] = name
                rec["is_overridden"] = True
                changed.append(rec)
        if not changed:
            return
        self._save_overrides(changed)
        self._refresh_summary()
        self._apply_filter()

    def _show_add_trade_dialog(self):
        """Open TradeEntryDialog to add manual trade record."""
        dlg = TradeEntryDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.result_data:
            res = dlg.result_data
            buy_price = res["buy_price"]
            qty = res["qty"]
            buy_amount = res["buy_amount"]  # dialog already fills price*qty when blank
            
            sell_price = res["sell_price"]
            sell_qty = res["sell_qty"]
            sell_amount = res["sell_amount"]
            
            pl = sell_amount - buy_amount if (sell_amount > 0 and buy_amount > 0) else 0.0
            pl_pct = (pl / buy_amount * 100) if buy_amount > 0 else 0.0
            
            is_closed = bool(res["sell_date"] or res["sell_price"])
            
            try:
                bd = _dt.datetime.strptime(res["buy_date"], "%Y-%m-%d").date()
                if res["sell_date"]:
                    sd = _dt.datetime.strptime(res["sell_date"], "%Y-%m-%d").date()
                    days_held = (sd - bd).days
                    curr_days = 0
                else:
                    days_held = 0
                    curr_days = (_dt.date.today() - bd).days
            except Exception:
                days_held = 0
                curr_days = 0
                
            # No orig_key here on purpose: trade_db.upsert_trade() generates a
            # collision-free key inside the INSERT itself (roadmap 6-1e), and
            # _save_custom_trade() writes the returned key back into `record`.
            record = {
                "company":     res.get("company", ""),
                "market":      res.get("market", ""),
                "ticker":      res.get("ticker", ""),
                "buy_date":    res["buy_date"],
                "buy_price":   buy_price,
                "qty":         qty,
                "buy_amount":  buy_amount,
                "position_w":  0.0,
                "sell_date":   res["sell_date"],
                "days_held":   days_held,
                "sell_price":  sell_price,
                "sell_qty":    sell_qty,
                "sell_amount": sell_amount,
                "pl":          pl,
                "pl_pct":      pl_pct,
                "curr_days":   curr_days,
                "curr_price":  0.0,
                "curr_pl":     0.0,
                "curr_pl_pct": 0.0,
                "curr_pct_pl": 0.0,
                "wk1": 0.0, "wk2": 0.0, "mth1": 0.0,
                "is_custom":   True  # flag to indicate it's a manual entry if needed
            }
            
            if not self._save_custom_trade(record):
                return

            if is_closed:
                self._closed_data.append(record)
            else:
                self._open_data.append(record)
                
            self._refresh_summary()
            self._start_price_fetch()
            self._apply_filter()
