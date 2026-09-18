"""ui/history_tab.py — TradingHistoryTab (Phase 4 split)

Split out from: main.py (2026-08-29 feat/3-1-modularize, Phase 4)
Contains:
  TradingHistoryTab
"""
import csv
import logging
import datetime as _dt

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QTableWidget, QTableWidgetItem, QLineEdit, QPushButton,
    QLabel, QHeaderView, QComboBox, QMessageBox, QDialog, QFrame,
    QInputDialog, QFileDialog,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QSettings, QTimer
from PyQt6.QtGui import QColor, QFont, QPainter, QPen

import trade_db
from data_fetcher import is_kr_code

from threads.fetch_threads import (
    PositionPriceFetchThread,
    GeminiDiagnosisThread,
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


from ui.common import create_font, _fmt_num_edit, FONT_FAMILY_CSS, retire_thread
from ui.history_calc import compute_pl_fields, build_monthly_rows
from ui.history_table import fill_table_rows
from ui.dialogs.holdings_summary import show_holdings_summary
from ui.dialogs.ai_diagnosis import show_ai_diagnosis_result


class TradingHistoryTab(QWidget):
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

    # Section spans: (label, start_col, span)  - kept for reference only
    _SECTIONS = [
        ("Trading",  0, 3),
        ("Buy",      3, 4),
        ("Sell",     7, 7),
        ("Position", 14, 4),
        ("Past",     18, 3),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._closed_data  = []
        self._open_data    = []
        self._current_path = ""
        self._price_thread: QThread | None = None
        self._ai_diagnosis_thread = None
        self._ai_diagnosis_loading_dlg = None
        self._deposit_thread = None
        self._ticker_resolve_threads: list = []
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

        # --------------------------------
        # TOP PANEL: Styled Dashboard Cards (1. Position Summary, 2. Metrics, 3. Control Center)
        # --------------------------------
        top_panel = QHBoxLayout()
        top_panel.setSpacing(5)
        top_panel.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)

        def _create_card(title_text):
            card = QFrame()
            card.setObjectName("DashboardCard")
            card.setStyleSheet("""
                QFrame#DashboardCard {
                    background-color: #ffffff;
                    border: 1px solid #dcdcdc;
                    border-radius: 8px;
                }
            """)
            vbox = QVBoxLayout(card)
            vbox.setContentsMargins(12, 10, 12, 10)
            vbox.setSpacing(5)
            vbox.setAlignment(Qt.AlignmentFlag.AlignTop)
            if title_text:
                lbl = QLabel(title_text)
                lbl.setStyleSheet("font-size: 10pt; font-weight: bold; color: #0078d4;")
                vbox.addWidget(lbl)
            return card, vbox

        # ---Card 1: Position Summary ---
        pos_card, pos_layout = _create_card("")
        pos_card.setFixedWidth(420)
        pos_card.setFixedHeight(125)
        pos_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)  # Center vertically since there is no title label

        pos_table = QTableWidget(3, 3)
        self._pos_summary_table = pos_table
        pos_table.setHorizontalHeaderLabels(["Position", "P/L", "Total"])
        pos_table.setVerticalHeaderLabels(["KR", "US", "Total"])
        # Table font setting: Malgun Gothic Semilight
        pos_table.setFont(create_font(9, QFont.Weight.Bold))
        pos_table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        pos_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        pos_table.setAlternatingRowColors(True)
        # Column widths: 0 and 1 stretch, 2 is interactive with fixed width to prevent clipping
        pos_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        pos_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        pos_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        pos_table.setColumnWidth(2, 90)
        pos_table.verticalHeader().setDefaultSectionSize(26)
        pos_table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        pos_table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        pos_table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        pos_table.setFixedWidth(400)
        pos_table.setFixedHeight(110)
        pos_table.setStyleSheet("""
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
        """)
        pos_layout.addWidget(pos_table)
        top_panel.addWidget(pos_card)

        # ---Card 2: Account Metrics + Controls (unified, 3 rows) ---
        metrics_card, metrics_layout = _create_card("")
        metrics_card.setFixedHeight(126)
        metrics_layout.setContentsMargins(12, 6, 12, 2)
        metrics_layout.setSpacing(2)
        metrics_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        INPUT_STYLE = (
            "QLineEdit { background:#fff; color:#111; border:1px solid #ccc; "
            "border-radius:4px; padding:3px 6px; font-size:12px; font-weight:bold; }"
        )
        BTN_H = 28
        FLD_W = 110   # field (label + widget) width per column

        def make_lbl(text):
            l = QLabel(text)
            l.setStyleSheet("font-size:12px; font-weight:bold; color:#444;")
            l.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            return l

        def make_ro_edit(align=Qt.AlignmentFlag.AlignRight):
            e = QLineEdit("-")
            e.setAlignment(align)
            e.setFixedWidth(110)
            e.setFixedHeight(BTN_H)
            e.setStyleSheet(INPUT_STYLE)
            e.setReadOnly(True)
            return e

        def make_rw_edit(placeholder=""):
            e = QLineEdit()
            e.setPlaceholderText(placeholder)
            e.setAlignment(Qt.AlignmentFlag.AlignRight)
            e.setFixedWidth(110)
            e.setFixedHeight(BTN_H)
            e.setStyleSheet(INPUT_STYLE)
            return e

        def lbl_field_pair(grid, row, col, lbl_text, widget):
            """Insert label at col (left-aligned in cell), widget at col+1."""
            lbl = make_lbl(lbl_text)
            lbl.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            grid.addWidget(lbl, row, col,
                           Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            grid.addWidget(widget, row, col + 1, Qt.AlignmentFlag.AlignVCenter)

        grid = QGridLayout()
        grid.setHorizontalSpacing(20)
        grid.setVerticalSpacing(5)
        grid.setContentsMargins(0, 0, 0, 0)
        # Label cols(even) stretch=0 - sized to text content only
        # Field cols(odd)  stretch=1 - share remaining space equally
        # - label-ield gap = horizontalSpacing uniformly across all pairs
        # Columns: 0=lbl, 1=val, 2=lbl, 3=val, 4=lbl, 5=val, 6=lbl, 7=val, 8=lbl, 9=val
        for c in range(10):
            grid.setColumnStretch(c, 0 if c % 2 == 0 else 1)

        # ---Row 0: Total Asset | P/L | P/L(%) | Withdrawal | Principal ---
        self._total_asset_edit = make_ro_edit()
        lbl_field_pair(grid, 0, 0, "Total Asset:", self._total_asset_edit)

        self._total_pl_edit = make_ro_edit()
        lbl_field_pair(grid, 0, 2, "Total P/L:", self._total_pl_edit)

        self._total_pl_pct_edit = make_ro_edit()
        lbl_field_pair(grid, 0, 4, "Total P/L(%):", self._total_pl_pct_edit)

        self._withdrawal_edit = make_rw_edit("e.g. 5,000,000")
        self._withdrawal_edit.textEdited.connect(self._on_deposit_changed)
        self._withdrawal_edit.textEdited.connect(lambda t: _fmt_num_edit(self._withdrawal_edit, t))
        lbl_field_pair(grid, 0, 6, "Withdrawal:", self._withdrawal_edit)

        self._principal_edit = make_rw_edit("e.g. 50,000,000")
        self._principal_edit.textEdited.connect(self._on_deposit_changed)
        self._principal_edit.textEdited.connect(lambda t: _fmt_num_edit(self._principal_edit, t))
        lbl_field_pair(grid, 0, 8, "Principal:", self._principal_edit)

        # ---Row 1: Total Invest | Deposit | Deposit(%) ---
        self._total_invest_edit = make_ro_edit()
        lbl_field_pair(grid, 1, 0, "Total Invest:", self._total_invest_edit)

        self._deposit_edit = make_rw_edit("e.g. 10,000,000")
        self._deposit_edit.textEdited.connect(self._on_deposit_changed)
        self._deposit_edit.textEdited.connect(lambda t: _fmt_num_edit(self._deposit_edit, t))
        lbl_field_pair(grid, 1, 2, "Deposit:", self._deposit_edit)

        self._deposit_pct_edit = make_ro_edit()
        lbl_field_pair(grid, 1, 4, "Deposit(%):", self._deposit_pct_edit)

        # ---Row 2: Summary | Sort by Date | Current Holdings | Search Company ---
        btn_style_blue = "QPushButton { background:#0078d4; color:white; border-radius:4px; padding:2px; font-weight:bold; font-size:9pt; } QPushButton:hover { background:#005a9e; }"
        btn_style_green = "QPushButton { background:#107c10; color:white; border-radius:4px; padding:2px; font-weight:bold; font-size:9pt; } QPushButton:hover { background:#0b5e0b; }"
        btn_style_orange = "QPushButton { background:#d35400; color:white; border-radius:4px; padding:2px; font-weight:bold; font-size:9pt; } QPushButton:hover { background:#e67e22; }"
        btn_style_purple = "QPushButton { background:#6c3483; color:white; border-radius:4px; padding:2px; font-weight:bold; font-size:9pt; } QPushButton:hover { background:#9b59b6; }"
        btn_style_navy = "QPushButton { background:#1a5276; color:white; border-radius:4px; padding:2px; font-weight:bold; font-size:9pt; } QPushButton:checked { background:#2874a6; border:2px solid #85c1e9; } QPushButton:hover:!checked { background:#21618c; }"

        summary_btn = QPushButton("Summary")
        summary_btn.setFixedHeight(BTN_H)
        summary_btn.setFixedWidth(FLD_W)
        summary_btn.setStyleSheet(btn_style_purple)
        summary_btn.clicked.connect(self._show_holdings_summary)
        # summary_btn - controls_row (below)

        self._sort_by_date = False
        self._sort_date_btn = QPushButton("Sort by Date")
        self._sort_date_btn.setFixedHeight(BTN_H)
        self._sort_date_btn.setFixedWidth(120)
        self._sort_date_btn.setCheckable(True)
        self._sort_date_btn.setStyleSheet(btn_style_navy)
        def _on_sort_date_toggled(checked, btn=self._sort_date_btn):
            self._sort_by_date = checked
            btn.setText("🔄 Sort by Position" if checked else "📅 Sort by Date")
            self._apply_filter()
        self._sort_date_btn.toggled.connect(_on_sort_date_toggled)
        # sort_date_btn - controls_row (below)

        self._open_stocks_combo = QComboBox()
        self._open_stocks_combo.addItem("Current Holdings...")
        self._open_stocks_combo.setFixedHeight(BTN_H)
        self._open_stocks_combo.setFixedWidth(250)
        self._open_stocks_combo.setStyleSheet(
            "QComboBox { background:#fff; color:#111; border:1px solid #ccc; border-radius:4px; padding:3px 6px; font-size:9pt; font-weight:bold; }"
            "QComboBox::drop-down { border-left:1px solid #ccc; }"
        )
        self._open_stocks_combo.currentTextChanged.connect(self._on_open_stock_combo_changed)
        # open_stocks_combo - controls_row (below)

        # Search Company (QLineEdit +  btn spanning remaining columns)
        self._search_stock_pl_edit = QLineEdit()
        self._search_stock_pl_edit.setPlaceholderText("Search Company")
        self._search_stock_pl_edit.setFixedHeight(BTN_H)
        self._search_stock_pl_edit.setFixedWidth(250)
        self._search_stock_pl_edit.setStyleSheet(INPUT_STYLE)
        self._search_stock_pl_edit.returnPressed.connect(self._on_search_stock_pl)
        # search_stock_pl_edit - controls_row (below)

        search_pl_btn = QPushButton("🔍")
        search_pl_btn.setFixedHeight(BTN_H)
        search_pl_btn.setFixedWidth(36)
        search_pl_btn.setStyleSheet(
            "QPushButton { background:#6c757d; color:white; border-radius:4px; padding:2px; font-weight:bold; font-size:9pt; }"
            "QPushButton:hover { background:#5a6268; }"
        )
        search_pl_btn.clicked.connect(self._on_search_stock_pl)
        # search_pl_btn - controls_row (below)

        fetch_dep_btn = self._fetch_dep_btn = QPushButton("🔄 Fetch")
        fetch_dep_btn.setFixedHeight(BTN_H)
        fetch_dep_btn.setFixedWidth(FLD_W)
        fetch_dep_btn.setStyleSheet(btn_style_blue)
        fetch_dep_btn.clicked.connect(self._fetch_account_deposit)

        reload_btn = QPushButton("🔄 Reload")
        reload_btn.setFixedHeight(BTN_H)
        reload_btn.setFixedWidth(FLD_W)
        reload_btn.setStyleSheet(btn_style_green)
        reload_btn.clicked.connect(self._reload_current)

        add_btn = QPushButton("➕ Add Trade")
        add_btn.setFixedHeight(BTN_H)
        add_btn.setFixedWidth(FLD_W)
        add_btn.setStyleSheet(btn_style_orange)
        add_btn.clicked.connect(self._show_add_trade_dialog)
       
        # Status label in row 1, rightmost
        self._deposit_status_lbl = QLabel("")
        self._deposit_status_lbl.setStyleSheet("font-size:9pt; color:#107c10; font-weight:bold;")
        self._deposit_status_lbl.setFixedHeight(BTN_H)

        # Spanned horizontal layout for buttons in row 1, col 6-9
        row1_buttons_layout = QHBoxLayout()
        row1_buttons_layout.setSpacing(5)
        row1_buttons_layout.setContentsMargins(0, 0, 0, 0)
        row1_buttons_layout.addWidget(fetch_dep_btn)
        row1_buttons_layout.addWidget(reload_btn)
        row1_buttons_layout.addWidget(add_btn)
        row1_buttons_layout.addWidget(self._deposit_status_lbl)
        row1_buttons_layout.addStretch()
        grid.addLayout(row1_buttons_layout, 1, 6, 1, 4, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        # Spaced lower row for buttons/combobox/search (separate layout so columns don't affect each other)
        lower_layout = QHBoxLayout()
        lower_layout.setSpacing(10)
        lower_layout.setContentsMargins(0, 0, 0, 0)
        lower_layout.addWidget(summary_btn)
        lower_layout.addWidget(self._sort_date_btn)
        lower_layout.addWidget(self._open_stocks_combo)
        lower_layout.addWidget(self._search_stock_pl_edit)
        lower_layout.addWidget(search_pl_btn)

        ai_diag_btn = QPushButton("🤖 AI Diagnosis")
        ai_diag_btn.setFixedHeight(BTN_H)
        ai_diag_btn.setFixedWidth(FLD_W)
        ai_diag_btn.setToolTip("Analyse portfolio risk, performance, and investment ideas using Gemini AI")
        ai_diag_btn.setStyleSheet(
            "QPushButton { background:#0a3d62; color:white; border-radius:4px; padding:2px; font-weight:bold; font-size:9pt; }"
            "QPushButton:hover { background:#1e5799; }"
        )
        ai_diag_btn.clicked.connect(self._show_ai_diagnosis)
        lower_layout.addWidget(ai_diag_btn)

        export_btn = QPushButton("📥 Export")
        export_btn.setFixedHeight(BTN_H)
        export_btn.setFixedWidth(FLD_W)
        export_btn.setToolTip("Export the currently displayed rows to Excel or CSV")
        export_btn.setStyleSheet(
            "QPushButton { background:#6c757d; color:white; border-radius:4px; padding:2px; font-weight:bold; font-size:9pt; }"
            "QPushButton:hover { background:#5a6268; }"
        )
        export_btn.clicked.connect(self._on_export_clicked)
        lower_layout.addWidget(export_btn)

        lower_layout.addStretch()

        metrics_layout.addLayout(grid)
        metrics_layout.addSpacing(6)
        metrics_layout.addLayout(lower_layout)

        self._path_label = QLabel("")
        self._path_label.setStyleSheet("color:#777; font-size:9px; border:none;")
        self._path_label.setFixedHeight(12)
        metrics_layout.addWidget(self._path_label)

        top_panel.addWidget(metrics_card)
        top_panel.addStretch()

        root.addLayout(top_panel, 0)  # stretch=0: top panel does not grow)


        # ---Main Data Table ---
        sections = [
            ("Trading",  0,  3, "#444444"),
            ("Buy",       3,  4, "#1a6b3c"),
            ("Sell",      7,  7, "#c0392b"),
            ("Position", 14,  4, "#0078d4"),
            ("Past",     18,  3, "#6d28d9"),
        ]

        class _SectionTable(QTableWidget):
            """QTableWidget that draws section separator lines after cell painting."""
            def __init__(self_, secs):
                super().__init__()
                self_._secs     = secs
                self_._last_col = max(s + sp - 1 for _, s, sp, _ in secs)

            def viewportEvent(self_, event):
                result = super().viewportEvent(event)
                if int(event.type()) == 12:  # QPaintEvent
                    self_._draw_section_lines()
                return result

            def _draw_section_lines(self_):
                vp = self_.viewport()
                painter = QPainter(vp)
                if not painter.isActive():
                    return
                painter.save()
                h = vp.height()
                pen = QPen()
                pen.setWidth(1)
                pen.setCosmetic(True)
                for _, start, span, color in self_._secs:
                    end_col = start + span - 1
                    pen.setColor(QColor(color))
                    painter.setPen(pen)
                    xl = self_.columnViewportPosition(start)
                    painter.drawLine(xl, 0, xl, h - 1)
                    if end_col == self_._last_col:
                        xr = self_.columnViewportPosition(end_col) + self_.columnWidth(end_col) - 1
                        painter.drawLine(xr, 0, xr, h - 1)
                painter.restore()

        tbl = self._table = _SectionTable(sections)
        tbl.setColumnCount(len(self._COLS))
        tbl.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        tbl.setAlternatingRowColors(False)
        tbl.setSortingEnabled(False)
        tbl.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        # Table font: Pretendard, Noto Sans KR, Segoe UI, Malgun Gothic 9pt (set appropriate size to prevent text cutoff)
        tbl_font = create_font(9, style_name="Semilight")
        tbl.setFont(tbl_font)
        # Fixed row height: 22px (secure margin against font)
        tbl.verticalHeader().setDefaultSectionSize(22)
        tbl.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        tbl.verticalHeader().setVisible(False)


        grouped_hdr = GroupedHeaderView(sections, self._COLS, tbl)
        grouped_hdr.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        grouped_hdr.setMinimumSectionSize(40)
        tbl.setHorizontalHeader(grouped_hdr)
        tbl.setStyleSheet(
            "QTableWidget { gridline-color: #d0d0d0; " + FONT_FAMILY_CSS + " font-size: 9pt; }"
            "QTableWidget::item { padding: 1px 3px; }"
        )

        tbl.cellDoubleClicked.connect(self._on_cell_double_clicked)
        root.addWidget(tbl, 1)  # stretch=1: trading table fills remaining space

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

    def _display_ai_diagnosis_result(self, result_text: str):
        show_ai_diagnosis_result(self, result_text)

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
            
        self._rt_price_thread = RealtimePriceThread(list(kr_tickers), list(us_tickers))
        self._rt_price_thread.prices_fetched.connect(self._on_realtime_prices_fetched)
        self._rt_price_thread.status_message.connect(self.status_message.emit)
        self._rt_price_thread.start()

    def collect_threads_to_stop(self):
        """Return every QThread this tab may have started, for MainWindow.closeEvent
        (mirrors UniverseTab.collect_threads_to_stop())."""
        threads = []
        pt = getattr(self, '_price_thread', None)
        if pt is not None:
            threads.append(pt)
        rt = getattr(self, '_rt_price_thread', None)
        if rt is not None:
            threads.append(rt)
        dt = getattr(self, '_deposit_thread', None)
        if dt is not None:
            threads.append(dt)
        threads.extend(getattr(self, '_ticker_resolve_threads', []))
        for t in getattr(self, '_zombie_threads', []):
            threads.append(t)
        return threads

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

        # Retire previous thread safely without garbage collecting while running
        retire_thread(self, '_price_thread')

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

        thread = PositionPriceFetchThread(
            names, tickers, markets, buy_prices, qtys, buy_amts, is_open, skip_fetch,
        )
        thread.prices_ready.connect(self._on_prices_ready)
        thread.status_message.connect(self.status_message.emit)
        self._price_thread = thread
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
        self._deposit_thread = AccountDepositThread()
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
        deposit    = self._get_deposit()
        withdrawal = self._get_withdrawal()
        principal  = self._get_principal()

        # ---Dynamic recalculation of curr_days ---
        # sell info exists: today - sell_date (days passed since sell)
        # sell info doesn't exist: today - buy_date (days held)
        today = _dt.date.today()
        _date_cache: dict[str, _dt.date] = {}
        def _parse_date(s: str) -> _dt.date | None:
            if not s:
                return None
            if s not in _date_cache:
                try:
                    _date_cache[s] = _dt.datetime.strptime(s[:10], "%Y-%m-%d").date()
                except Exception:
                    _date_cache[s] = None
            return _date_cache[s]

        for r in self._closed_data + self._open_data:
            sell_dt = _parse_date(r.get("sell_date", ""))
            if sell_dt:
                r["curr_days"] = (today - sell_dt).days
            else:
                buy_dt = _parse_date(r.get("buy_date", ""))
                if buy_dt:
                    r["curr_days"] = (today - buy_dt).days

        # ---recalculate per-row P/L: P/L = Current Price * Quantity - Buy Amount ---
        kr_cost = 0.0
        kr_eval = 0.0
        us_cost = 0.0
        us_eval = 0.0

        cost_total = 0.0
        eval_total = 0.0
        
        for r in self._open_data:
            buy_amt   = r.get("buy_amount", 0.0)
            qty       = r.get("qty", 0.0)
            price     = r.get("curr_price", 0.0)
            market    = r.get("market", "")
            
            is_us = market in ("US", "NASDAQ", "NYSE", "AMEX", "NASDAQ 100", "S&P500")

            if price > 0 and qty > 0:
                eval_val         = price * qty          # Evaluation Amount = Current Price * Quantity
                r["curr_pl"]     = eval_val - buy_amt   # P/L = Evaluation Amount - Buy Amount
                r["curr_pl_pct"] = (r["curr_pl"] / buy_amt * 100) if buy_amt else 0.0
            else:
                eval_val         = buy_amt
                r["curr_pl"]     = 0.0
                r["curr_pl_pct"] = 0.0
                
            cost_total += buy_amt
            eval_total += eval_val

            if is_us:
                us_cost += buy_amt
                us_eval += eval_val
            else:
                kr_cost += buy_amt
                kr_eval += eval_val

        # Calculate Position
        kr_pl = kr_eval - kr_cost
        kr_pl_pct = (kr_pl / kr_cost * 100) if kr_cost else 0.0

        us_pl = us_eval - us_cost
        us_pl_pct = (us_pl / us_cost * 100) if us_cost else 0.0

        pos_pl     = eval_total - cost_total
        pos_pl_pct = (pos_pl / cost_total * 100) if cost_total else 0.0

        # Total = Sum of Evaluation Amount + Deposit + Withdrawal
        total        = eval_total + deposit + withdrawal
        total_pl     = total - principal
        total_pl_pct = (total_pl / principal * 100) if principal > 0 else 0.0
        self.total_asset_updated.emit(total)

        # position_w: weight based on total invest
        total_invest = total - withdrawal
        for r in self._open_data:
            qty   = r.get("qty", 0.0)
            price = r.get("curr_price", 0.0)
            ev    = (price * qty) if price > 0 and qty > 0 else r.get("buy_amount", 0.0)
            r["position_w"]  = (ev / total_invest * 100) if total_invest > 0 else 0.0
            r["curr_pct_pl"] = r["curr_pl_pct"] * (r["position_w"] / 100.0) if r["position_w"] else 0.0

        for r in self._closed_data:
            r["position_w"] = 0.0
            r["curr_pct_pl"] = 0.0

        deposit_base = total - withdrawal
        deposit_pct = (deposit / deposit_base * 100) if deposit_base > 0 else 0.0
        if hasattr(self, "_deposit_pct_edit"):
            self._deposit_pct_edit.setText(f"{deposit_pct:.1f}%")

        # ---Update Position Summary Table ---
        if hasattr(self, "_pos_summary_table"):
            tbl = self._pos_summary_table
            
            def _pos_color(v): return "#e74c3c" if v < 0 else "#1a6b3c"
            def _krw(v):       return f"{v:,.0f}"
            def _kpct(v):      return f"{v:+.1f}%"

            def set_item(r, c, text, color=None):
                it = QTableWidgetItem(text)
                it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                if color:
                    it.setForeground(QColor(color))
                tbl.setItem(r, c, it)

            # KR Row
            set_item(0, 0, _krw(kr_cost))
            set_item(0, 1, f"{kr_pl:+,.0f}", _pos_color(kr_pl))
            set_item(0, 2, _kpct(kr_pl_pct), _pos_color(kr_pl_pct))

            # US Row
            set_item(1, 0, _krw(us_cost))
            set_item(1, 1, f"{us_pl:+,.0f}", _pos_color(us_pl))
            set_item(1, 2, _kpct(us_pl_pct), _pos_color(us_pl_pct))

            # Total Row
            set_item(2, 0, _krw(cost_total))
            set_item(2, 1, f"{pos_pl:+,.0f}", _pos_color(pos_pl))
            set_item(2, 2, _kpct(pos_pl_pct), _pos_color(pos_pl_pct))

        # Update Total Asset / P/L inline labels
        INPUT_STYLE_BASE = (
            "QLineEdit { border:1px solid #ccc; "
            "border-radius:4px; padding:3px 6px; font-size:12px; font-weight:bold; }"
        )
        if hasattr(self, "_total_invest_edit"):
            self._total_invest_edit.setText(f"{total - withdrawal:,.0f}")
        if hasattr(self, "_total_asset_edit"):
            self._total_asset_edit.setText(f"{total:,.0f}")
        if hasattr(self, "_total_pl_edit"):
            self._total_pl_edit.setText(f"{total_pl:+,.0f}")
            self._total_pl_edit.setToolTip(f"Total Asset ({total:,.0f}) - Principal ({principal:,.0f})")
            self._total_pl_edit.setStyleSheet(
                INPUT_STYLE_BASE + " QLineEdit { background:#fff; color:#111; }"
            )
        if hasattr(self, "_total_pl_pct_edit"):
            self._total_pl_pct_edit.setText(f"{total_pl_pct:+.1f}%")
            self._total_pl_pct_edit.setStyleSheet(
                INPUT_STYLE_BASE + " QLineEdit { background:#fff; color:#111; }"
            )

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
        if not hasattr(self, '_open_stocks_combo'):
            return
            
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

    def _show_ai_diagnosis(self):
        """Show an AI-powered portfolio diagnosis dialog using Gemini API."""
        if hasattr(self, "_ai_diagnosis_thread") and self._ai_diagnosis_thread is not None and self._ai_diagnosis_thread.isRunning():
            return

        # Show non-blocking loading dialog while the API is called in background thread
        loading_dlg = QDialog(self)
        loading_dlg.setWindowTitle("🤖 AI Portfolio Diagnosis")
        loading_dlg.setModal(True)
        loading_dlg.resize(400, 110)
        loading_layout = QVBoxLayout(loading_dlg)
        loading_layout.setContentsMargins(16, 14, 16, 14)
        loading_layout.setSpacing(12)

        loading_lbl = QLabel("⏳ Analysing your portfolio with Gemini AI…\nPlease wait.")
        loading_lbl.setFont(create_font(10, style_name="Semilight"))
        loading_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        loading_layout.addWidget(loading_lbl)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setFixedWidth(80)
        cancel_btn.setStyleSheet(
            "QPushButton { background:#888; color:white; border-radius:4px; padding:3px 8px; }"
            "QPushButton:hover { background:#666; }"
        )
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        btn_layout.addWidget(cancel_btn)
        loading_layout.addLayout(btn_layout)

        # Stashed on self (not captured in a closure) so the finished-signal handler and the
        # Cancel button can be bound methods — connecting QThread.finished to a plain closure
        # defeats Qt's automatic cross-thread queuing (it can only detect thread affinity via
        # a QObject receiver), so the slot would otherwise run on the worker thread.
        self._ai_diagnosis_loading_dlg = loading_dlg
        self._ai_diagnosis_thread = GeminiDiagnosisThread(self._open_data, self._closed_data)
        self._ai_diagnosis_thread.finished.connect(self._on_ai_diagnosis_finished)
        cancel_btn.clicked.connect(self._cancel_ai_diagnosis)
        self._ai_diagnosis_thread.start()
        loading_dlg.exec()

    def _on_ai_diagnosis_finished(self, result_text: str, err: str):
        loading_dlg = self._ai_diagnosis_loading_dlg
        if loading_dlg is None or not loading_dlg.isVisible():
            return
        loading_dlg.close()
        if err:
            final_text = f"⚠️ AI analysis error:\n{err}"
        else:
            final_text = result_text
        self._display_ai_diagnosis_result(final_text)

    def _cancel_ai_diagnosis(self):
        """Cancel button handler: actually stops the background Gemini call instead of just
        hiding the loading dialog, since GeminiDiagnosisThread has no cooperative cancellation
        (the Gemini HTTP call is blocking) — terminate() mirrors the same forced-stop escape
        hatch MainWindow.closeEvent already uses for stuck threads."""
        thread = getattr(self, "_ai_diagnosis_thread", None)
        if thread is not None and thread.isRunning():
            thread.terminate()
            thread.wait(500)
        loading_dlg = getattr(self, "_ai_diagnosis_loading_dlg", None)
        if loading_dlg is not None:
            loading_dlg.reject()

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

    # ---Export (review.md 2-2) ---
    def _export_headers(self) -> list[str]:
        """Column headers for export, derived from _SECTIONS/_COLS so they can't
        drift out of sync with the table -- repeated sub-labels (Date/Price/Q'ty/
        Amount/Days/P&L/P&L%) get their section name prefixed so the exported
        file's headers are unambiguous even though the on-screen grouped header
        only shows the section name once."""
        headers = []
        for label, start, span in self._SECTIONS:
            for i in range(start, start + span):
                col_name = self._COLS[i]
                headers.append(col_name if label == "Trading" else f"{label} {col_name}")
        return headers

    def _on_export_clicked(self):
        if self._table.rowCount() == 0:
            QMessageBox.information(self, "Export", "No data to export.")
            return

        default_name = f"trading_history_{_dt.date.today().strftime('%Y%m%d')}.xlsx"
        path, selected_filter = QFileDialog.getSaveFileName(
            self, "Export Trading History", default_name,
            "Excel Workbook (*.xlsx);;CSV File (*.csv)",
        )
        if not path:
            return

        want_csv = "csv" in selected_filter.lower() or path.lower().endswith(".csv")
        if want_csv and not path.lower().endswith(".csv"):
            path += ".csv"
        elif not want_csv and not path.lower().endswith(".xlsx"):
            path += ".xlsx"

        headers = self._export_headers()
        rows = []
        for r in range(self._table.rowCount()):
            rows.append([
                (self._table.item(r, c).text() if self._table.item(r, c) else "")
                for c in range(self._table.columnCount())
            ])

        try:
            if want_csv:
                with open(path, "w", newline="", encoding="utf-8-sig") as f:
                    writer = csv.writer(f)
                    writer.writerow(headers)
                    writer.writerows(rows)
            else:
                from openpyxl import Workbook
                wb = Workbook()
                ws = wb.active
                ws.title = "Trading History"
                ws.append(headers)
                for row in rows:
                    ws.append(row)
                wb.save(path)
        except Exception as e:
            logger.warning("Trading history export failed: %s", e, exc_info=True)
            QMessageBox.warning(self, "Export Error", f"Failed to export:\n{e}")
            return

        self.status_message.emit(f"Exported {len(rows)} row(s) to {path}")

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
                self._ticker_resolve_threads = [
                    t for t in self._ticker_resolve_threads if t.isRunning()
                ]
                thread = SingleStockFetchThread(rec.get("market", ""), new_ticker)
                thread.finished.connect(
                    lambda result, error, r=rec: self._on_ticker_name_resolved(r, result, error)
                )
                self._ticker_resolve_threads.append(thread)
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

    def _on_ticker_name_resolved(self, rec: dict, result, error: str):
        """SingleStockFetchThread callback for the ticker-cell edit above."""
        if not result or not result.get("name") or rec.get("company") == result["name"]:
            return
        rec["company"] = result["name"]
        rec["is_overridden"] = True
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
