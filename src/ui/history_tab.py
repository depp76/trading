"""ui/history_tab.py — TradingHistoryTab (Phase 4 split)

Split out from: main.py (2026-08-29 feat/3-1-modularize, Phase 4)
Contains:
  TradingHistoryTab
"""
import logging
import datetime as _dt

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QTableWidget, QTableWidgetItem, QLineEdit, QPushButton,
    QLabel, QHeaderView, QComboBox, QMessageBox, QDialog, QFrame,
    QInputDialog,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QSettings, QTimer
from PyQt6.QtGui import QColor, QFont

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
    _ACTION_INSIGHT_COLOR, _ACTION_INSIGHT_HOVER_COLOR,
)
from ui.history_calc import compute_pl_fields, build_monthly_rows, summarize_positions
from ui.history_table import fill_table_rows, SectionTable, SECTIONS
from ui.dialogs.holdings_summary import show_holdings_summary


# ── Dashboard card styling / widget factories for TradingHistoryTab._build_ui ──
_BTN_H = 28
_FLD_W = 110   # field (label + widget) width per column

_INPUT_STYLE = (
    "QLineEdit { background:#fff; color:#111; border:1px solid #ccc; "
    "border-radius:4px; padding:3px 6px; font-size:12px; font-weight:bold; }"
)
_COMBO_STYLE = (
    "QComboBox { background:#fff; color:#111; border:1px solid #ccc; border-radius:4px; padding:3px 6px; font-size:9pt; font-weight:bold; }"
    "QComboBox::drop-down { border-left:1px solid #ccc; }"
)
_CARD_STYLE = """
                QFrame#DashboardCard {
                    background-color: #ffffff;
                    border: 1px solid #dcdcdc;
                    border-radius: 8px;
                }
            """
_POS_TABLE_STYLE = """
            QTableWidget {
                border: 1px solid #c8c8c8;
                border-radius: 6px;
                background-color: #ffffff;
                gridline-color: #e4e4e4;
                """ + FONT_FAMILY_CSS + """
                font-size: 12px;
                font-weight: bold;
                color: #1a1a2e;
            }
            QHeaderView::section {
                background-color: #f0f2f5;
                border: none;
                border-right: 1px solid #d0d0d0;
                border-bottom: 1px solid #d0d0d0;
                """ + FONT_FAMILY_CSS + """
                font-weight: bold;
                font-size: 12px;
                color: #444;
                padding: 2px 4px;
            }
        """


def _btn_style(bg: str, hover: str) -> str:
    return (f"QPushButton {{ background:{bg}; color:white; border-radius:4px; padding:2px; font-weight:bold; font-size:9pt; }}"
            f" QPushButton:hover {{ background:{hover}; }}")


_BTN_BLUE = _btn_style("#0078d4", "#005a9e")
_BTN_ORANGE = _btn_style("#d35400", "#e67e22")
_BTN_GREY = _btn_style("#6c757d", "#5a6268")
_BTN_INSIGHT = _btn_style(_ACTION_INSIGHT_COLOR, _ACTION_INSIGHT_HOVER_COLOR)
# Checkable toggle (roadmap 7-4c: was navy = _ACTION_PORTFOLIO_COLOR, reassigned to grey
# since "Sort by Date" is a secondary-utility toggle, not a Strategy-tab backtest action).
_BTN_GREY_CHECKABLE = ("QPushButton { background:#6c757d; color:white; border-radius:4px; padding:2px; font-weight:bold; font-size:9pt; }"
                       " QPushButton:checked { background:#495057; border:2px solid #adb5bd; }"
                       " QPushButton:hover:!checked { background:#5a6268; }")


def _create_card(title_text: str) -> tuple:
    """White rounded dashboard card; returns (frame, its QVBoxLayout)."""
    card = QFrame()
    card.setObjectName("DashboardCard")
    card.setStyleSheet(_CARD_STYLE)
    vbox = QVBoxLayout(card)
    vbox.setContentsMargins(12, 10, 12, 10)
    vbox.setSpacing(5)
    vbox.setAlignment(Qt.AlignmentFlag.AlignTop)
    if title_text:
        lbl = QLabel(title_text)
        lbl.setStyleSheet("font-size: 10pt; font-weight: bold; color: #0078d4;")
        vbox.addWidget(lbl)
    return card, vbox


def _styled_button(text: str, style: str, on_click=None, *, width: int = _FLD_W,
                   height: int = _BTN_H, tooltip: str = "") -> QPushButton:
    btn = QPushButton(text)
    btn.setFixedHeight(height)
    btn.setFixedWidth(width)
    btn.setStyleSheet(style)
    if tooltip:
        btn.setToolTip(tooltip)
    if on_click is not None:
        btn.clicked.connect(on_click)
    return btn


def _make_lbl(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet("font-size:12px; font-weight:bold; color:#444;")
    lbl.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    return lbl


def _make_ro_edit(align=Qt.AlignmentFlag.AlignRight) -> QLineEdit:
    e = QLineEdit("-")
    e.setAlignment(align)
    e.setFixedWidth(110)
    e.setFixedHeight(_BTN_H)
    e.setStyleSheet(_INPUT_STYLE)
    e.setReadOnly(True)
    return e


def _make_rw_edit(placeholder: str = "") -> QLineEdit:
    e = QLineEdit()
    e.setPlaceholderText(placeholder)
    e.setAlignment(Qt.AlignmentFlag.AlignRight)
    e.setFixedWidth(110)
    e.setFixedHeight(_BTN_H)
    e.setStyleSheet(_INPUT_STYLE)
    return e


def _lbl_field_pair(grid: QGridLayout, row: int, col: int, lbl_text: str, widget) -> None:
    """Label at `col` (left-aligned), widget at `col + 1`."""
    grid.addWidget(_make_lbl(lbl_text), row, col, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    grid.addWidget(widget, row, col + 1, Qt.AlignmentFlag.AlignVCenter)


class TradingHistoryTab(ThreadOwnerMixin, QWidget):
    """Trading History tab - load from Excel and display closed/open positions."""
    total_asset_updated = pyqtSignal(float)
    status_message = pyqtSignal(str)  # forwards background-thread progress text to MainWindow's status bar

    # Column indices in the unified table (matches Excel header layout exactly)
    # Sections: Trading | Buy(5) | Sell(7) | Position(4) | Past(3)
    _COLS = [
        "Company",    # 0
        "Market",     # 1
        "Ticker",     # 2
        "Date",       # 3  - Buy
        "Price",      # 4  - 
        "Q'ty",       # 5  - 
        "Amount",     # 6  - 
        "Date",       # 7  - Sell
        "Days",       # 8  - 
        "Price",      # 9  - 
        "Q'ty",       # 10 - 
        "Amount",     # 11 - 
        "P/L",        # 12 - 
        "P/L(%)",     # 13 - 
        "Days",       # 14 - Position (open holdings)
        "Price",      # 15 - 
        "P/L",        # 16 - 
        "P/L(%)",     # 17 - 
        "5D",         # 18 - Past (trading days)
        "10D",        # 19 - 
        "20D",        # 20 - 
    ]

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

        # Top panel: dashboard cards (position summary | account metrics + controls)
        top_panel = QHBoxLayout()
        top_panel.setSpacing(5)
        top_panel.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        top_panel.addWidget(self._build_position_card())
        top_panel.addWidget(self._build_metrics_card())
        top_panel.addStretch()
        root.addLayout(top_panel, 0)          # stretch=0: top panel does not grow

        root.addWidget(self._build_history_table(), 1)   # stretch=1: fills the rest

    def _build_position_card(self) -> QFrame:
        """Card 1: the 3x3 KR / US / Total position summary table."""
        card, layout = _create_card("")
        card.setFixedWidth(420)
        card.setFixedHeight(125)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)  # no title label: centre vertically

        tbl = self._pos_summary_table = QTableWidget(3, 3)
        tbl.setHorizontalHeaderLabels(["Position", "P/L", "Total"])
        tbl.setVerticalHeaderLabels(["KR", "US", "Total"])
        tbl.setFont(create_font(9, QFont.Weight.Bold))
        tbl.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        tbl.setAlternatingRowColors(True)
        # Columns 0/1 stretch; column 2 stays interactive at a fixed width so it never clips
        tbl.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        tbl.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        tbl.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        tbl.setColumnWidth(2, 90)
        tbl.verticalHeader().setDefaultSectionSize(26)
        tbl.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        tbl.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        tbl.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        tbl.setFixedWidth(400)
        tbl.setFixedHeight(110)
        tbl.setStyleSheet(_POS_TABLE_STYLE)
        layout.addWidget(tbl)
        return card

    def _build_metrics_card(self) -> QFrame:
        """Card 2: account metric fields (2 grid rows) + the action/control row."""
        card, layout = _create_card("")
        card.setFixedHeight(126)
        layout.setContentsMargins(12, 6, 12, 2)
        layout.setSpacing(2)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        layout.addLayout(self._build_metrics_grid())
        layout.addSpacing(6)
        layout.addLayout(self._build_controls_row())

        self._path_label = QLabel("")
        self._path_label.setStyleSheet("color:#777; font-size:9px; border:none;")
        self._path_label.setFixedHeight(12)
        layout.addWidget(self._path_label)
        return card

    def _build_metrics_grid(self) -> QGridLayout:
        """Row 0: Total Asset | P/L | P/L(%) | Withdrawal | Principal
        Row 1: Total Invest | Deposit | Deposit(%) | [Fetch] [Reload] [Add Trade] status"""
        grid = QGridLayout()
        grid.setHorizontalSpacing(20)
        grid.setVerticalSpacing(5)
        grid.setContentsMargins(0, 0, 0, 0)
        # Label columns (even) size to their text; field columns (odd) share the rest.
        for c in range(10):
            grid.setColumnStretch(c, 0 if c % 2 == 0 else 1)

        self._total_asset_edit = _make_ro_edit()
        _lbl_field_pair(grid, 0, 0, "Total Asset:", self._total_asset_edit)
        self._total_pl_edit = _make_ro_edit()
        _lbl_field_pair(grid, 0, 2, "Total P/L:", self._total_pl_edit)
        self._total_pl_pct_edit = _make_ro_edit()
        _lbl_field_pair(grid, 0, 4, "Total P/L(%):", self._total_pl_pct_edit)
        self._withdrawal_edit = self._make_money_input("e.g. 5,000,000")
        _lbl_field_pair(grid, 0, 6, "Withdrawal:", self._withdrawal_edit)
        self._principal_edit = self._make_money_input("e.g. 50,000,000")
        _lbl_field_pair(grid, 0, 8, "Principal:", self._principal_edit)

        self._total_invest_edit = _make_ro_edit()
        _lbl_field_pair(grid, 1, 0, "Total Invest:", self._total_invest_edit)
        self._deposit_edit = self._make_money_input("e.g. 10,000,000")
        _lbl_field_pair(grid, 1, 2, "Deposit:", self._deposit_edit)
        self._deposit_pct_edit = _make_ro_edit()
        _lbl_field_pair(grid, 1, 4, "Deposit(%):", self._deposit_pct_edit)

        # Row 1, cols 6-9: data buttons + KIS deposit status
        self._fetch_dep_btn = _styled_button("🔄 Fetch", _BTN_BLUE, self._fetch_account_deposit)
        reload_btn = _styled_button("🔄 Reload", _BTN_BLUE, self._reload_current)
        add_btn = _styled_button("➕ Add Trade", _BTN_ORANGE, self._show_add_trade_dialog)
        self._deposit_status_lbl = QLabel("")
        self._deposit_status_lbl.setStyleSheet("font-size:9pt; color:#107c10; font-weight:bold;")
        self._deposit_status_lbl.setFixedHeight(_BTN_H)

        buttons = QHBoxLayout()
        buttons.setSpacing(5)
        buttons.setContentsMargins(0, 0, 0, 0)
        for w in (self._fetch_dep_btn, reload_btn, add_btn, self._deposit_status_lbl):
            buttons.addWidget(w)
        buttons.addStretch()
        grid.addLayout(buttons, 1, 6, 1, 4, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        return grid

    def _make_money_input(self, placeholder: str) -> QLineEdit:
        """Editable KRW field: re-formats with thousands separators as you type
        and schedules a debounced QSettings save."""
        edit = _make_rw_edit(placeholder)
        edit.textEdited.connect(self._on_deposit_changed)
        edit.textEdited.connect(lambda t, e=edit: _fmt_num_edit(e, t))
        return edit

    def _build_controls_row(self) -> QHBoxLayout:
        """Summary | Sort by Date | Current Holdings combo | Search"""
        row = QHBoxLayout()
        row.setSpacing(10)
        row.setContentsMargins(0, 0, 0, 0)

        row.addWidget(_styled_button("Summary", _BTN_INSIGHT, self._show_holdings_summary))

        self._sort_by_date = False
        self._sort_date_btn = _styled_button("Sort by Date", _BTN_GREY_CHECKABLE, width=120)
        self._sort_date_btn.setCheckable(True)
        self._sort_date_btn.toggled.connect(self._on_sort_date_toggled)
        row.addWidget(self._sort_date_btn)

        self._open_stocks_combo = QComboBox()
        self._open_stocks_combo.addItem("Current Holdings...")
        self._open_stocks_combo.setFixedHeight(_BTN_H)
        self._open_stocks_combo.setFixedWidth(250)
        self._open_stocks_combo.setStyleSheet(_COMBO_STYLE)
        self._open_stocks_combo.currentTextChanged.connect(self._on_open_stock_combo_changed)
        row.addWidget(self._open_stocks_combo)

        self._search_stock_pl_edit = QLineEdit()
        self._search_stock_pl_edit.setPlaceholderText("Search Company")
        self._search_stock_pl_edit.setFixedHeight(_BTN_H)
        self._search_stock_pl_edit.setFixedWidth(250)
        self._search_stock_pl_edit.setStyleSheet(_INPUT_STYLE)
        self._search_stock_pl_edit.returnPressed.connect(self._on_search_stock_pl)
        row.addWidget(self._search_stock_pl_edit)
        row.addWidget(_styled_button("🔍", _BTN_GREY, self._on_search_stock_pl, width=36))
        row.addStretch()
        return row

    def _on_sort_date_toggled(self, checked: bool):
        self._sort_by_date = checked
        self._sort_date_btn.setText("🔄 Sort by Position" if checked else "📅 Sort by Date")
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
        tbl.cellDoubleClicked.connect(self._on_cell_double_clicked)
        return tbl

    def _fit_columns(self):
        """Set column widths to fill the viewport without horizontal scrolling."""
        tbl = self._table
        viewport_w = tbl.viewport().width()
        if viewport_w <= 0:
            return

        # ---Per-column minimum widths (col 0 = Company handled separately) ---
        # Order: col 1..20
        #         Market Ticker |Date  Price  Qty  Amt|Date  Days  Price  Qty  Amt    P/L   P/L%|Days  Price  P/L   P/L%|5D   10D  20D
        mins = [
            62,   64,            # 1 Market, 2 Ticker
            84,   78,   55,  85,  # 3-6  Buy: Date Price Qty Amount
            84,   40,   78,  55,  85,  85,  70,  # 7-13 Sell: Date Days Price Qty Amount P/L P/L(%)
            40,   78,   85,  70,  # 14-17 Position: Days Price P/L P/L(%)
            70,   70,   70,  # 18-20 Trend: 5D 10D 20D
        ]
        if len(mins) != 20:
            logger.warning("[_fit_columns] mins length mismatch: %d, expected 20", len(mins))
            return

        fixed_total = sum(mins)
        MIN_NAME_W  = 140
        avail_for_name = viewport_w - fixed_total
        name_w = max(MIN_NAME_W, avail_for_name)

        # If everything doesn't fit, allow horizontal scroll instead of squeezing
        # (Allow horizontal scroll -> prevent text cutoff)
        tbl.setColumnWidth(0, name_w)
        for i, w in enumerate(mins, start=1):
            tbl.setColumnWidth(i, w)


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

    def _save_overrides(self):
        """Persist all currently edited/overridden records back to the DB."""
        try:
            to_save = [
                rec for rec in self._closed_data + self._open_data
                if rec.get("is_overridden") or rec.get("is_custom") or
                   rec.get("sell_date") or rec.get("sell_price")
            ]
            if to_save:
                trade_db.upsert_trades(to_save)
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
        self._deposit_status_lbl.setStyleSheet("font-size:10pt; color:#0078d4; font-weight:bold;")
        self._deposit_status_lbl.setText("⏳ Fetching deposit...")
        self.status_message.emit("Fetching account deposit from KIS...")
        self._track_thread(AccountDepositThread(), '_deposit_thread')
        self._deposit_thread.finished.connect(self._on_account_deposit_fetched)
        self._deposit_thread.start()

    def _on_account_deposit_fetched(self, val: float, err: str):
        self._fetch_dep_btn.setEnabled(True)
        if err:
            self._deposit_status_lbl.setStyleSheet("font-size:10pt; color:#d32f2f; font-weight:bold;")
            self._deposit_status_lbl.setText("❌ Failed to fetch")
            QTimer.singleShot(5000, lambda: self._deposit_status_lbl.setText(""))
            QMessageBox.critical(self, "Error", f"Failed to fetch data:\n{err}")
            return
        self._deposit_edit.setText(f"{int(val):,}")
        self._on_deposit_changed()
        # Inline status display (instead of QMessageBox) - immediate edit possible
        self._deposit_status_lbl.setStyleSheet("font-size:10pt; color:#107c10; font-weight:bold;")
        self._deposit_status_lbl.setText(f"💰 {int(val):,} KRW (Est.)")
        QTimer.singleShot(4000, lambda: self._deposit_status_lbl.setText(""))
        self._deposit_edit.selectAll()
        self._deposit_edit.setFocus()

    # ---Summary ---
    def _refresh_summary(self):
        """Recompute the position aggregates (ui.history_calc.summarize_positions)
        and render them into the dashboard cards."""
        deposit    = self._get_deposit()
        withdrawal = self._get_withdrawal()
        principal  = self._get_principal()

        agg = summarize_positions(
            self._open_data, self._closed_data,
            deposit=deposit, withdrawal=withdrawal, principal=principal,
        )
        total, total_pl = agg["total"], agg["total_pl"]
        self.total_asset_updated.emit(total)

        self._deposit_pct_edit.setText(f"{agg['deposit_pct']:.1f}%")

        # ---Update Position Summary Table ---
        tbl = self._pos_summary_table

        def _pos_color(v): return "#e74c3c" if v < 0 else "#1a6b3c"

        def set_item(r, c, text, color=None):
            it = QTableWidgetItem(text)
            it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            if color:
                it.setForeground(QColor(color))
            tbl.setItem(r, c, it)

        for row, prefix in ((0, "kr"), (1, "us")):
            cost, pl, pct = agg[f"{prefix}_cost"], agg[f"{prefix}_pl"], agg[f"{prefix}_pl_pct"]
            set_item(row, 0, f"{cost:,.0f}")
            set_item(row, 1, f"{pl:+,.0f}", _pos_color(pl))
            set_item(row, 2, f"{pct:+.1f}%", _pos_color(pct))
        set_item(2, 0, f"{agg['cost_total']:,.0f}")
        set_item(2, 1, f"{agg['pos_pl']:+,.0f}", _pos_color(agg["pos_pl"]))
        set_item(2, 2, f"{agg['pos_pl_pct']:+.1f}%", _pos_color(agg["pos_pl_pct"]))

        # Update Total Asset / P/L inline labels
        self._total_invest_edit.setText(f"{agg['total_invest']:,.0f}")
        self._total_asset_edit.setText(f"{total:,.0f}")
        self._total_pl_edit.setText(f"{total_pl:+,.0f}")
        self._total_pl_edit.setToolTip(f"Total Asset ({total:,.0f}) - Principal ({principal:,.0f})")
        self._total_pl_edit.setStyleSheet(_INPUT_STYLE)
        self._total_pl_pct_edit.setText(f"{agg['total_pl_pct']:+.1f}%")
        self._total_pl_pct_edit.setStyleSheet(_INPUT_STYLE)

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
        closed_rows = [("closed", r) for r in self._closed_data]
        open_rows   = [("open", r) for r in self._open_data]

        sort_by_date = getattr(self, "_sort_by_date", False)

        if sort_by_date:
            # All rows sorted by buy_date ascending (closed + open together),
            # with a summary row appended after each calendar month.
            all_rows = closed_rows + open_rows
            all_rows.sort(key=lambda x: x[1]["buy_date"])
            rows = self._build_monthly_rows(all_rows)
        else:
            # Default: closed (oldest first) then open (oldest first)
            closed_rows.sort(key=lambda x: x[1]["buy_date"])
            open_rows.sort(key=lambda x: x[1]["buy_date"])
            rows = closed_rows + open_rows
        self._fill_table(rows)

    # ---Table item helpers ---
    # ---Unified table fill ---
    def _fill_table(self, rows: list):
        tbl = self._table
        tbl.setSortingEnabled(False)
        tbl.setUpdatesEnabled(False)
        try:
            self._row_data = fill_table_rows(tbl, rows)
        finally:
            tbl.setUpdatesEnabled(True)
        self._update_open_stocks_combo()
        self._fit_columns()
        tbl.scrollToBottom()

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
        
        if col == 0:
            curr_val = rec.get("company", "")
            new_str, ok = QInputDialog.getText(self, "Edit", "Company Name:", text=str(curr_val))
            if ok and new_str.strip():
                rec["company"] = new_str.strip()
                rec["is_overridden"] = True
                self._save_overrides()
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
                self._save_overrides()

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
                self._save_overrides()
            return

        if col in {7, 9, 10, 11}:
            dlg = SellEditDialog(rec, self)
            if dlg.exec() == QDialog.DialogCode.Accepted and dlg.result_data:
                res = dlg.result_data
                rec["sell_date"]   = res["sell_date"]
                rec["sell_price"]  = res["sell_price"]
                rec["sell_qty"]    = res["sell_qty"]
                rec["sell_amount"] = res["sell_amount"] if res["sell_amount"] > 0 else res["sell_price"] * res["sell_qty"]
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
                self._save_overrides()
            return

    def _on_ticker_name_resolved(self, result, error: str, ticker: str):
        """SingleStockFetchThread.finished for the ticker-cell edit above: apply the
        resolved company name to every record now carrying that ticker."""
        name = (result or {}).get("name")
        if not name:
            return
        changed = False
        for rec in self._open_data + self._closed_data:
            if rec.get("ticker") == ticker and rec.get("company") != name:
                rec["company"] = name
                rec["is_overridden"] = True
                changed = True
        if not changed:
            return
        self._save_overrides()
        self._refresh_summary()
        self._apply_filter()

    def _show_add_trade_dialog(self):
        """Open TradeEntryDialog to add manual trade record."""
        dlg = TradeEntryDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.result_data:
            res = dlg.result_data
            buy_price = res["buy_price"]
            qty = res["qty"]
            buy_amount = res["buy_amount"] or (buy_price * qty)
            
            sell_price = res["sell_price"]
            sell_qty = res["sell_qty"]
            sell_amount = res["sell_amount"] or (sell_price * sell_qty)
            
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
