"""ui/widgets.py — Reusable UI widget classes (Phase 3-1 분리)

분리 출처: main.py (2026-08-29 feat/3-1-modularize)
포함 클래스:
  FilterPopup, FilterableHeader, StockTable, GroupedHeaderView
"""
from PyQt6.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QLineEdit, QCheckBox, QScrollArea,
    QWidget, QPushButton, QTableWidget, QTableWidgetItem, QHeaderView,
    QStyleOptionHeader,
)
from PyQt6.QtCore import Qt, pyqtSignal, QPoint, QRect
from PyQt6.QtGui import QColor, QFont, QPainter, QPen, QPolygon

from ui import theme

# Shared helpers imported from main package (available at runtime when main.py loads)
# We use a lazy import pattern to avoid circular imports.
def _get_create_font():
    import main as _m
    return _m.create_font

def _get_hist_keys():
    import main as _m
    return _m._HIST_KEYS


# ---------------------------------------------------------------------------
# FilterPopup  — Excel-style checkbox popup
# ---------------------------------------------------------------------------
class FilterPopup(QFrame):
    """Checkbox popup for Excel-style column filtering."""
    filter_changed = pyqtSignal(int, object)  # col, frozenset | None

    def __init__(self, col, unique_vals, current_filter, parent=None):
        super().__init__(parent, Qt.WindowType.Popup)
        self.col = col
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet(
            f"QFrame {{ background: {theme.c('panel_bg')}; color: {theme.c('text')}; "
            f"border: 1px solid {theme.c('border')}; border-radius: 4px; }}"
        )

        create_font = _get_create_font()

        layout = QVBoxLayout(self)
        layout.setSpacing(3)
        layout.setContentsMargins(8, 8, 8, 8)

        # Search inside popup
        self._search = QLineEdit()
        self._search.setFont(create_font(9, style_name="Semilight"))
        self._search.setPlaceholderText("Search...")
        self._search.textChanged.connect(self._on_search)
        layout.addWidget(self._search)

        # Select All
        self._all_cb = QCheckBox("(Select All)")
        self._all_cb.setFont(create_font(9, style_name="Semilight"))
        self._all_cb.setTristate(True)
        layout.addWidget(self._all_cb)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        layout.addWidget(sep)

        # Scroll area for values
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFixedHeight(min(240, 28 * len(unique_vals) + 8))
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inner = QWidget()
        inner_layout = QVBoxLayout(inner)
        inner_layout.setSpacing(2)
        inner_layout.setContentsMargins(2, 2, 2, 2)

        self._checkboxes = {}
        for val in sorted(unique_vals, key=lambda v: (v != "Index", v)):
            cb = QCheckBox(str(val))
            cb.setFont(create_font(9, style_name="Semilight"))
            cb.setChecked(current_filter is None or val in current_filter)
            inner_layout.addWidget(cb)
            self._checkboxes[val] = cb
        scroll.setWidget(inner)
        layout.addWidget(scroll)

        # Buttons
        btn_layout = QHBoxLayout()
        ok_btn = QPushButton("OK")
        ok_btn.setFont(create_font(9, QFont.Weight.Bold))
        ok_btn.setFixedHeight(26)
        ok_btn.clicked.connect(self._apply)
        clear_btn = QPushButton("Reset")
        clear_btn.setFont(create_font(9, QFont.Weight.Bold))
        clear_btn.setFixedHeight(26)
        clear_btn.clicked.connect(self._clear)
        btn_layout.addWidget(clear_btn)
        btn_layout.addWidget(ok_btn)
        layout.addLayout(btn_layout)

        self._updating = False
        self._sync_all()
        self._all_cb.stateChanged.connect(self._on_all_changed)
        for cb in self._checkboxes.values():
            cb.stateChanged.connect(self._on_value_changed)

    def _on_search(self, text):
        text = text.lower()
        for val, cb in self._checkboxes.items():
            cb.setVisible(not text or text in str(val).lower())

    def _sync_all(self):
        self._updating = True
        visible = [cb for cb in self._checkboxes.values() if cb.isVisible()]
        checked_count = sum(1 for cb in visible if cb.isChecked())
        if checked_count == len(visible):
            self._all_cb.setCheckState(Qt.CheckState.Checked)
        elif checked_count == 0:
            self._all_cb.setCheckState(Qt.CheckState.Unchecked)
        else:
            self._all_cb.setCheckState(Qt.CheckState.PartiallyChecked)
        self._updating = False

    def _on_all_changed(self, state):
        if self._updating:
            return
        checked = (state == Qt.CheckState.Checked.value)
        self._updating = True
        for cb in self._checkboxes.values():
            if cb.isVisible():
                cb.setChecked(checked)
        self._updating = False

    def _on_value_changed(self, _):
        if not self._updating:
            self._sync_all()

    def _clear(self):
        self.filter_changed.emit(self.col, None)
        self.close()

    def _apply(self):
        checked = frozenset(val for val, cb in self._checkboxes.items() if cb.isChecked())
        all_vals = frozenset(self._checkboxes.keys())
        self.filter_changed.emit(self.col, None if checked == all_vals else checked)
        self.close()


# ---------------------------------------------------------------------------
# FilterableHeader  — draws ▼ icon per filter column
# ---------------------------------------------------------------------------
class FilterableHeader(QHeaderView):
    filter_requested = pyqtSignal(int, QPoint)
    FILTER_COLS = {2}  # Market column index

    def __init__(self, orientation, parent=None):
        super().__init__(orientation, parent)
        self.setSectionsClickable(True)
        self._active_filter_cols = set()

    def set_active_filter_cols(self, cols):
        self._active_filter_cols = set(cols)
        self.viewport().update()

    def paintSection(self, painter, rect, logicalIndex):
        super().paintSection(painter, rect, logicalIndex)
        if logicalIndex in self.FILTER_COLS:
            is_active = logicalIndex in self._active_filter_cols
            icon_w, icon_h = 10, 6
            ix = rect.right() - icon_w - 4
            iy = rect.center().y() - icon_h // 2 + 1
            painter.save()
            color = QColor("#0078d4") if is_active else QColor("#888888")
            painter.setPen(color)
            painter.setBrush(color)
            pts = QPolygon([
                QPoint(ix, iy),
                QPoint(ix + icon_w, iy),
                QPoint(ix + icon_w // 2, iy + icon_h),
            ])
            painter.drawPolygon(pts)
            painter.restore()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.pos()
            logical = self.logicalIndexAt(pos)
            if logical in self.FILTER_COLS:
                # Any click on a filter column - open popup (no sorting)
                gp = self.viewport().mapToGlobal(pos)
                self.filter_requested.emit(logical, gp)
                return
        super().mousePressEvent(event)


# ---------------------------------------------------------------------------
# StockTable  — main universe table with filters and action buttons
# ---------------------------------------------------------------------------
class StockTable(QTableWidget):
    col_filter_changed = pyqtSignal()  # emitted when any column filter changes

    # Fixed column widths for Pf, MA, and Del columns
    W_PF  = 30
    W_MA  = 30
    W_DEL = 30

    # Pre-allocated color constants - avoids re-creating QColor objects on every load_data() call
    _C_RED            = QColor("red")
    _C_BLUE           = QColor("blue")
    _C_BG_SEVERE      = QColor(255,  90,  90)   # <= -30%
    _C_BG_MEDIUM      = QColor(255, 160, 160)   # -30~-15%
    _C_BG_LIGHT       = QColor(255, 210, 210)   # -15~  0%
    _C_BG_BLUE_LIGHT  = QColor(190, 220, 255)   #   0~15%
    _C_BG_BLUE_MEDIUM = QColor(120, 175, 255)   #  15~30%
    _C_BG_BLUE_SEVERE = QColor( 90, 155, 255)   # >= 30%
    _C_FG_BLACK       = QColor(  0,   0,   0)

    def __init__(self):
        super().__init__()
        create_font = _get_create_font()
        self.setColumnCount(22)
        self.setHorizontalHeaderLabels(
            ["Name", "Pf", "Market", "Ticker", "Market Cap", "tPER", "fPER", "Price", "Div(20)", "Div(50)",
             "52W High", "High Diff", "52W Low", "Low Diff",
             "3D", "5D", "10D", "20D", "60D", "120D", "MA", "Del"]
        )
        # Table font: Malgun Gothic Semilight 9pt (set appropriate size to prevent text cutoff)
        self.setFont(create_font(9, style_name="Semilight"))
        self.setStyleSheet(
            f"QTableWidget {{ gridline-color: {theme.c('gridline')}; font-family: 'Malgun Gothic Semilight', '맑은 고딕 Semilight', 'Malgun Gothic'; font-size: 9pt; }}"
            "QTableWidget::item { padding: 1px 3px; }"
        )
        # Use filterable header
        self._filter_header = FilterableHeader(Qt.Orientation.Horizontal, self)
        self._filter_header.setFont(create_font(9, QFont.Weight.Bold))
        self._filter_header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self._filter_header.filter_requested.connect(self._show_filter_popup)
        self.setHorizontalHeader(self._filter_header)
        self._col_filters = {}  # col_index -> frozenset | None
        self._numeric_conditions: list[dict] = []  # [{col, op, val}, ...] for AI filters
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(22)
        self.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        self.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAlternatingRowColors(True)
        self.setSortingEnabled(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # Fixed-width columns: Pf(1), MA-chart(20), Del(21)
        self.setColumnWidth(1, self.W_PF)
        self.setColumnWidth(20, self.W_MA)
        self.setColumnWidth(21, self.W_DEL)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Guard against re-entrant calls: setColumnWidth() inside _stretch_columns
        # triggers another resizeEvent, which would create an infinite feedback loop
        # causing the viewport to oscillate left/right during scrolling.
        if getattr(self, '_resizing', False):
            return
        self._resizing = True
        try:
            self._stretch_columns()
        finally:
            self._resizing = False

    def showEvent(self, event):
        super().showEvent(event)
        self._stretch_columns()

    def _stretch_columns(self):
        if self.rowCount() == 0:
            return

        vp_w = int(self.viewport().width() * 0.99)
        if vp_w <= 0:
            return

        # ---Fixed button columns ---
        W_PF  = self.W_PF   # col 1
        W_MA  = self.W_MA   # col 20 (MA chart button)
        W_DEL = self.W_DEL   # col 21

        # Budget shared among 19 flexible columns (0, 2-19)
        flex_w = vp_w - W_PF - W_MA - W_DEL

        # ---Column weights (larger - wider) ---
        weights = [
            3.0,              # 0  Name
            1.0,              # 2  Market
            1.0,              # 3  Ticker
            1.2,              # 4  Market Cap
            0.8,              # 5  tPER
            0.8,              # 6  fPER
            1.0,              # 7  Price
            0.87,             # 8  Div(20)
            0.87,             # 9  Div(50)
            1.1,              # 10 52W High
            0.87,             # 11 High Diff
            1.1,              # 12 52W Low
            0.87,             # 13 Low Diff
            0.87, 0.87, 0.87, 0.87, 0.87, 0.87,  # 14-19  3D-20D
        ]  # 19 weights total

        MIN_W = 38   # absolute floor for any flexible column
        total_weight = sum(weights)
        unit  = flex_w / total_weight
        widths = [max(MIN_W, int(w * unit)) for w in weights]

        # Rounding residual - absorb into Name column (index 0) using exact arithmetic
        # to prevent per-frame pixel drift that causes the viewport to oscillate.
        used = sum(widths) + W_PF + W_MA + W_DEL
        residual = vp_w - used
        widths[0] = max(MIN_W, widths[0] + residual)

        flex_cols = [0, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19]

        # Build the complete target width map
        new_widths = {}
        for col, w in zip(flex_cols, widths):
            new_widths[col] = w
        new_widths[1]  = W_PF
        new_widths[20] = W_MA
        new_widths[21] = W_DEL

        # Skip redundant setColumnWidth calls when nothing has changed.
        # Each setColumnWidth() fires an internal geometry update that can
        # re-trigger resizeEvent, so avoiding no-op calls breaks the loop.
        if getattr(self, '_last_col_widths', None) == new_widths:
            return
        self._last_col_widths = new_widths

        for col, w in new_widths.items():
            self.setColumnWidth(col, w)

    def _make_item(self, text, align=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter):
        item = QTableWidgetItem(text)
        item.setTextAlignment(align)
        return item

    def load_data(self, data, highlights=None):
        if highlights is None:
            highlights = {}

        _HIST_KEYS = _get_hist_keys()

        self.setUpdatesEnabled(False)  # UI Batch Repaint Optimization
        self.setSortingEnabled(False)
        self.clearContents()
        if self.rowCount() != len(data):
            self.setRowCount(len(data))

        center = Qt.AlignmentFlag.AlignCenter
        right = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter

        # Alias class-level color constants for readability inside this loop
        color_red            = self._C_RED
        color_blue           = self._C_BLUE
        color_bg_severe      = self._C_BG_SEVERE
        color_bg_medium      = self._C_BG_MEDIUM
        color_bg_light       = self._C_BG_LIGHT
        color_bg_blue_light  = self._C_BG_BLUE_LIGHT
        color_bg_blue_medium = self._C_BG_BLUE_MEDIUM
        color_bg_blue_severe = self._C_BG_BLUE_SEVERE
        color_fg_black       = self._C_FG_BLACK

        for row, item in enumerate(data):
            currency = item.get('currency', '')
            if item.get('is_bond'):
                price_text = f"{item.get('price', 0):.2f}%"
            elif item.get('is_index'):
                price_text = f"${int(item.get('price', 0)):,}" if currency == '$' else f"{int(item.get('price', 0)):,}"
            elif currency == '$' and 'usd_price' in item:
                price_text = f"${item.get('usd_price', 0):,.2f}"
            else:
                price_text = f"{int(item.get('price', 0)):,}"

            marcap_raw = 0
            if item.get('is_index'):
                marcap_eok = "-"
            else:
                try:
                    # market_cap is in KRW. 100,000,000 = 100M KRW
                    marcap_raw = int(item.get('market_cap', 0)) // 100_000_000
                    marcap_eok = f"{marcap_raw:,}"
                except Exception:
                    marcap_eok = "0"

            name_item = self._make_item(item['name'])
            h_state = highlights.get(item.get('ticker'))
            if h_state == "On":
                name_item.setBackground(QColor("yellow"))
            elif h_state == "Tg":
                name_item.setBackground(QColor(135, 206, 235))
            self.setItem(row, 0, name_item)

            # col 1: Pf/Tg button placeholder (actual widget populated by add_action_buttons)
            self.setItem(row, 1, QTableWidgetItem(""))

            self.setItem(row, 2, self._make_item(item['market'], center))
            self.setItem(row, 3, self._make_item(item['ticker'], center))

            marcap_item = QTableWidgetItem()
            marcap_item.setData(Qt.ItemDataRole.EditRole, marcap_raw)
            marcap_item.setText(marcap_eok)
            marcap_item.setTextAlignment(center if marcap_eok == "-" else right)
            self.setItem(row, 4, marcap_item)

            # tPER (col 5)
            tper_val = item.get('trailing_per')
            if tper_val is not None and not item.get('is_index'):
                tper_item = QTableWidgetItem()
                tper_item.setData(Qt.ItemDataRole.EditRole, float(tper_val))
                tper_item.setText(f"{tper_val:.1f}")
                tper_item.setTextAlignment(right)
            else:
                tper_item = self._make_item("-", center)
                tper_item.setForeground(QColor("#aaaaaa"))
            self.setItem(row, 5, tper_item)

            # fPER (col 6)
            fper_val = item.get('forward_per')
            if fper_val is not None and not item.get('is_index'):
                fper_item = QTableWidgetItem()
                fper_item.setData(Qt.ItemDataRole.EditRole, float(fper_val))
                fper_item.setText(f"{fper_val:.1f}")
                fper_item.setTextAlignment(right)
            else:
                fper_item = self._make_item("-", center)
                fper_item.setForeground(QColor("#aaaaaa"))
            self.setItem(row, 6, fper_item)

            changes = item.get('changes', {})
            high_52w = changes.get("52w_high", 0.0)
            high_diff = changes.get("52w_high_diff", 0.0)
            low_52w = changes.get("52w_low", 0.0)
            low_diff = changes.get("52w_low_diff", 0.0)
            ma20_div = changes.get("ma20_div", 0.0)
            ma50_div = changes.get("ma50_div", 0.0)

            is_idx = item.get('is_index', False)
            is_bond = item.get('is_bond', False)
            chg_mode = item.get('change_mode', 'pct')  # 'pct', 'bp', 'abs'
            is_wti = (currency == '$' and chg_mode == 'abs')
            fmt = "${:,.2f}" if currency == '$' else "{:,.2f}" if is_idx else "{:,.0f}"
            if is_bond:
                fmt = "{:,.2f}%"

            # col 7: Price
            price_raw = float(item.get('usd_price', 0) if currency == '$' and 'usd_price' in item else item.get('price', 0))
            price_item = QTableWidgetItem()
            price_item.setData(Qt.ItemDataRole.EditRole, price_raw)
            price_item.setText(price_text)
            price_item.setTextAlignment(right)
            self.setItem(row, 7, price_item)

            # col 8: Div(20) (MA20 divergence ratio, base = 100%)
            if chg_mode == 'pct' and ma20_div != 0.0:
                div20_item = QTableWidgetItem()
                div20_item.setData(Qt.ItemDataRole.EditRole, round(ma20_div, 4))
                div20_item.setText(f"{ma20_div:.1f}%")
                div20_item.setTextAlignment(right)
                if ma20_div >= 120:
                    div20_item.setBackground(QColor(180,  30,  30))
                    div20_item.setForeground(QColor(255, 255, 255))
                elif ma20_div >= 110:
                    div20_item.setForeground(QColor(200,   0,   0))
                elif ma20_div >= 105:
                    div20_item.setForeground(QColor(230,  80,  80))
                elif ma20_div >= 102:
                    div20_item.setForeground(QColor(210, 130, 130))
                elif ma20_div >= 98:
                    div20_item.setForeground(QColor(130, 130, 130))
                elif ma20_div >= 93:
                    div20_item.setForeground(QColor( 80, 130, 210))
                else:
                    div20_item.setBackground(QColor( 30,  30, 180))
                    div20_item.setForeground(QColor(255, 255, 255))
            else:
                div20_item = self._make_item("-", center)
                div20_item.setForeground(QColor("#aaaaaa"))
            self.setItem(row, 8, div20_item)

            # col 9: Div(50) (MA50 divergence ratio, base = 100%)
            if chg_mode == 'pct' and ma50_div != 0.0:
                div50_item = QTableWidgetItem()
                div50_item.setData(Qt.ItemDataRole.EditRole, round(ma50_div, 4))
                div50_item.setText(f"{ma50_div:.1f}%")
                div50_item.setTextAlignment(right)
                if ma50_div >= 130:
                    div50_item.setBackground(QColor(180,  30,  30))
                    div50_item.setForeground(QColor(255, 255, 255))
                elif ma50_div >= 110:
                    div50_item.setForeground(QColor(200,   0,   0))
                elif ma50_div >= 107:
                    div50_item.setForeground(QColor(230,  80,  80))
                elif ma50_div >= 103:
                    div50_item.setForeground(QColor(210, 130, 130))
                elif ma50_div >= 98:
                    div50_item.setForeground(QColor(130, 130, 130))
                elif ma50_div >= 90:
                    div50_item.setForeground(QColor( 80, 130, 210))
                else:
                    div50_item.setBackground(QColor( 30,  30, 180))
                    div50_item.setForeground(QColor(255, 255, 255))
            else:
                div50_item = self._make_item("-", center)
                div50_item.setForeground(QColor("#aaaaaa"))
            self.setItem(row, 9, div50_item)

            h_str = "-" if high_52w == 0 else fmt.format(high_52w)
            self.setItem(row, 10, self._make_item(h_str, right))

            hd_str = "-"
            if high_52w:
                if chg_mode == 'bp':
                    hd_str = f"{high_diff:+.0f} bp"
                elif chg_mode == 'abs':
                    hd_str = f"${high_diff:+.2f}" if is_wti else f"{high_diff:+.2f}"
                else:
                    hd_str = f"{high_diff:+.1f}%"
            hd_item = QTableWidgetItem()
            hd_item.setData(Qt.ItemDataRole.EditRole, float(high_diff) if high_52w else float('-inf'))
            hd_item.setText(hd_str)
            hd_item.setTextAlignment(right)
            if chg_mode == 'pct':
                if high_diff > 0: hd_item.setForeground(color_red)
                elif high_diff < 0: hd_item.setForeground(color_blue)
            self.setItem(row, 11, hd_item)

            l_str = "-" if low_52w == 0 else fmt.format(low_52w)
            self.setItem(row, 12, self._make_item(l_str, right))

            ld_str = "-"
            if low_52w:
                if chg_mode == 'bp':
                    ld_str = f"{low_diff:+.0f} bp"
                elif chg_mode == 'abs':
                    ld_str = f"${low_diff:+.2f}" if is_wti else f"{low_diff:+.2f}"
                else:
                    ld_str = f"{low_diff:+.1f}%"
            ld_item = QTableWidgetItem()
            ld_item.setData(Qt.ItemDataRole.EditRole, float(low_diff) if low_52w else float('-inf'))
            ld_item.setText(ld_str)
            ld_item.setTextAlignment(right)
            if chg_mode == 'pct':
                if low_diff > 0: ld_item.setForeground(color_red)
                elif low_diff < 0: ld_item.setForeground(color_blue)
            self.setItem(row, 13, ld_item)

            for i, key in enumerate(_HIST_KEYS):
                raw = changes.get(key, 0.0)
                try:
                    val = float(raw)
                except (TypeError, ValueError):
                    val = 0.0
                change_item = QTableWidgetItem()
                change_item.setData(Qt.ItemDataRole.EditRole, val)
                if chg_mode == 'bp':
                    change_item.setText(f"{val:+.0f} bp")
                elif chg_mode == 'abs':
                    change_item.setText(f"${val:,.2f}" if is_wti else f"{val:.2f}")
                else:
                    change_item.setText(f"{val:+.1f}%")
                change_item.setTextAlignment(right)

                if chg_mode == 'pct':
                    if val <= -30:
                        change_item.setBackground(color_bg_severe)
                        change_item.setForeground(color_fg_black)
                    elif val <= -15:
                        change_item.setBackground(color_bg_medium)
                        change_item.setForeground(color_fg_black)
                    elif val < 0:
                        change_item.setBackground(color_bg_light)
                        change_item.setForeground(color_fg_black)
                    elif val >= 30:
                        change_item.setBackground(color_bg_blue_severe)
                        change_item.setForeground(color_fg_black)
                    elif val >= 15:
                        change_item.setBackground(color_bg_blue_medium)
                        change_item.setForeground(color_fg_black)
                    elif val > 0:
                        change_item.setBackground(color_bg_blue_light)
                        change_item.setForeground(color_fg_black)
                self.setItem(row, 14 + i, change_item)

        # MA20 buttons are added externally after load_data via add_ma20_button
        # Clear sort indicator BEFORE enabling sorting so Qt does not auto-resort
        # the rows and overwrites the insertion order (Index-KOSPI-KOSDAQ-NASDAQ-S&P500, by market cap).
        self._filter_header.setSortIndicator(-1, Qt.SortOrder.AscendingOrder)
        self.setSortingEnabled(True)
        self._stretch_columns()
        self.setUpdatesEnabled(True)

    def add_action_buttons(self, row, h_state, tg_callback, ma_callback, del_callback):
        """Insert 'Pf/Tg', 'MA' for MA chart, and 'Del' for Delete."""
        # Determine effective display state
        if h_state in ("On", "Tg"):
            display_state = h_state
        else:
            display_state = "-"

        btn_tg = QPushButton(display_state if display_state in ("On", "Tg") else "-")
        btn_tg.setFixedHeight(24)
        btn_tg.setFixedWidth(self.W_PF)
        if h_state == "On":
            btn_style = "background-color: yellow; color: black;"
        elif h_state == "Tg":
            btn_style = "background-color: #87CEEB; color: black;"
        else:
            btn_style = "background-color: #ecf0f1; color: black;"

        btn_tg.setStyleSheet(
            f"QPushButton {{ padding: 2px; {btn_style} font-size: 11px; font-weight: bold; }}"
        )
        btn_tg.clicked.connect(tg_callback)
        self.setCellWidget(row, 1, btn_tg)

        # MA chart Button (col 20)
        btn_ma = QPushButton("📈")
        btn_ma.setFixedHeight(24)
        btn_ma.setFixedWidth(self.W_MA)
        btn_ma.setStyleSheet(
            "QPushButton { padding: 2px; background-color: #7d3c98; font-size: 11px; }"
            "QPushButton:hover { background-color: #6c3483; }"
        )
        btn_ma.clicked.connect(ma_callback)
        self.setCellWidget(row, 20, btn_ma)

        # Delete Button (col 21)
        btn_del = QPushButton("❌")
        btn_del.setFixedHeight(24)
        btn_del.setFixedWidth(self.W_DEL)
        btn_del.setStyleSheet(
            "QPushButton { padding: 2px; background-color: #e74c3c; font-size: 11px; }"
            "QPushButton:hover { background-color: #c0392b; }"
        )
        btn_del.clicked.connect(del_callback)
        self.setCellWidget(row, 21, btn_del)

    # ---Excel-style column filters ---
    def _get_unique_col_vals(self, col):
        vals = set()
        for row in range(self.rowCount()):
            item = self.item(row, col)
            if item:
                vals.add(item.text())
        return vals

    def _show_filter_popup(self, col, global_pos):
        unique_vals = self._get_unique_col_vals(col)
        current_filter = self._col_filters.get(col)
        popup = FilterPopup(col, unique_vals, current_filter, None)  # None = independent popup window
        popup.filter_changed.connect(self._on_col_filter_changed)
        popup.move(global_pos)
        popup.show()
        popup.adjustSize()
        self._active_popup = popup  # keep reference to prevent GC

    def _on_col_filter_changed(self, col, values):
        if values is None:
            self._col_filters.pop(col, None)
        else:
            self._col_filters[col] = values
        self._filter_header.set_active_filter_cols(set(self._col_filters.keys()))
        self.col_filter_changed.emit()

    def apply_col_filters(self, text_filter="", tg_only=False):
        """Apply both column filters and text search. Called by MainWindow.filter_table."""
        text_lower = text_filter.lower()

        hdr = self._filter_header
        sort_col = hdr.sortIndicatorSection()
        sort_order = hdr.sortIndicatorOrder()
        self.setUpdatesEnabled(False)  # suppress repaints while toggling row visibility
        self.setSortingEnabled(False)

        _ops = {
            "<":  lambda a, b: a <  b,
            "<=": lambda a, b: a <= b,
            "==": lambda a, b: a == b,
            ">=": lambda a, b: a >= b,
            ">":  lambda a, b: a >  b,
            "contains": lambda a, b: b.lower() in str(a).lower(),
        }

        for row in range(self.rowCount()):
            hidden = False
            # Column filters (excel-style set membership)
            for col, vals in self._col_filters.items():
                item = self.item(row, col)
                cell_val = item.text() if item else ""
                if cell_val not in vals:
                    hidden = True
                    break

            # AI numeric/string conditions
            if not hidden and self._numeric_conditions:
                for cond in self._numeric_conditions:
                    col = cond.get("col")
                    op  = cond.get("op", "==")
                    val = cond.get("val")
                    if col is None or val is None:
                        continue
                    item = self.item(row, col)
                    if item is None:
                        hidden = True
                        break
                    raw = item.data(Qt.ItemDataRole.EditRole)
                    # Fall back to text if EditRole has no numeric data
                    if raw is None:
                        raw = item.text()
                    cmp_fn = _ops.get(op)
                    if cmp_fn is None:
                        continue
                    try:
                        if isinstance(val, str):
                            match = cmp_fn(str(raw), val)
                        else:
                            match = cmp_fn(float(raw), float(val))
                        if not match:
                            hidden = True
                            break
                    except (TypeError, ValueError):
                        hidden = True
                        break

            if not hidden and tg_only:
                btn = self.cellWidget(row, 1)
                if btn and isinstance(btn, QPushButton):
                    if btn.text() != "Tg":
                        hidden = True

            # Text search (Name or Ticker)
            if not hidden and text_lower:
                name_item = self.item(row, 0)
                ticker_item = self.item(row, 3)
                name = name_item.text().lower() if name_item else ""
                ticker = ticker_item.text().lower() if ticker_item else ""
                if text_lower not in name and text_lower not in ticker:
                    hidden = True
            self.setRowHidden(row, hidden)

        # Re-enable sorting and restore the exact same sort indicator so Qt
        # does not apply a different sort order.
        self.setSortingEnabled(True)
        hdr.setSortIndicator(sort_col, sort_order)
        self.setUpdatesEnabled(True)

    def clear_ai_filter(self):
        """Remove any AI-applied numeric conditions."""
        self._numeric_conditions = []

    def set_ai_conditions(self, conditions: list[dict]):
        """Apply a list of numeric/string conditions from the AI natural-language filter."""
        self._numeric_conditions = conditions


# ---------------------------------------------------------------------------
# GroupedHeaderView  — two-row grouped column header
# ---------------------------------------------------------------------------
class GroupedHeaderView(QHeaderView):
    """
    A QHeaderView that renders two rows:
      Top row - section group labels spanning multiple columns
                   (e.g. "Buy", "Sell", "Position", "Past")
      Bottom row - individual column sub-labels

    sections: list of (group_label, start_col, col_span, color_hex)
    sub_labels: list of per-column label strings (len == columnCount)
    """

    def __init__(self, sections, sub_labels, parent=None, group_h=22, sub_h=22):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self._GROUP_H = group_h
        self._SUB_H = sub_h
        self._sections   = sections    # [(label, start, span, color), ...]
        self._sub_labels = sub_labels  # [str, ...]
        self.setDefaultSectionSize(72)
        self.setSectionsClickable(False)

    # ---total header height ---
    def sizeHint(self):
        sh = super().sizeHint()
        sh.setHeight(self._GROUP_H + self._SUB_H)
        return sh

    def sectionSizeHint(self, _):
        return self._GROUP_H + self._SUB_H

    # ---painting ---
    def paintSection(self, painter, rect, logical_index):
        """Paint the bottom sub-label row only (group row drawn in paintEvent)."""
        if not rect.isValid():
            return
        painter.save()

        # Bottom sub-label cell
        sub_rect = rect.adjusted(0, self._GROUP_H, 0, 0)
        opt = self._style_option(logical_index)

        # Find this column's group colour and section boundary flags
        col_color = "#444444"
        is_left_edge = False
        is_right_edge = False
        for _, start, span, color in self._sections:
            if start <= logical_index < start + span:
                col_color = color
                is_left_edge  = (logical_index == start)
                is_right_edge = (logical_index == start + span - 1)
                break

        # Draw frame
        self.style().drawControl(
            self.style().ControlElement.CE_Header, opt, painter, self
        )

        # Draw sub-label text with group colour
        painter.setPen(QColor(col_color))
        f = painter.font()
        f.setPointSize(8)
        f.setBold(True)
        painter.setFont(f)
        label = self._sub_labels[logical_index] if logical_index < len(self._sub_labels) else ""
        painter.drawText(sub_rect, Qt.AlignmentFlag.AlignCenter, label)

        painter.restore()

    def paintEvent(self, event):
        """First paint all sections (bottom row), then overlay the top group row."""
        super().paintEvent(event)

        painter = QPainter(self.viewport())
        painter.save()

        total_h = self._GROUP_H + self._SUB_H

        for label, start, span, color in self._sections:
            if start >= self.count():
                continue
            # X-coordinates: from left edge of start col to right edge of (start+span-1)
            x_left  = self.sectionViewportPosition(start)
            end_col  = min(start + span - 1, self.count() - 1)
            x_right  = self.sectionViewportPosition(end_col) + self.sectionSize(end_col)
            gx = x_left
            gw = x_right - x_left
            if gw <= 0:
                continue

            group_rect = QRect(gx, 0, gw, self._GROUP_H)

            # Background
            bg = QColor(color)
            bg.setAlpha(30)
            painter.fillRect(group_rect, bg)

            # Thin top/bottom border for the group row
            pen_thin = QPen(QColor(color))
            pen_thin.setWidth(1)
            pen_thin.setCosmetic(True)
            painter.setPen(pen_thin)
            painter.drawLine(gx, 0, gx + gw - 1, 0)                                  # top
            painter.drawLine(gx, self._GROUP_H - 1, gx + gw - 1, self._GROUP_H - 1) # bottom

            # Label only (vertical borders are drawn per-column in paintSection)
            pen2 = QPen(QColor(color))
            painter.setPen(pen2)
            f = painter.font()
            f.setPointSize(9)
            f.setBold(True)
            painter.setFont(f)
            painter.drawText(group_rect, Qt.AlignmentFlag.AlignCenter, label)

        # ---Section boundary vertical lines drawn LAST (on top of everything) ---
        last_section_end = max(s + sp - 1 for _, s, sp, _ in self._sections)
        pen_sec = QPen()
        pen_sec.setWidth(1)
        pen_sec.setCosmetic(True)
        for _, start, span, color in self._sections:
            if start >= self.count():
                continue
            end_col = min(start + span - 1, self.count() - 1)
            pen_sec.setColor(QColor(color))
            painter.setPen(pen_sec)
            # Left border of this section
            xl = self.sectionViewportPosition(start)
            painter.drawLine(xl, 0, xl, total_h - 1)
            # Right border only for the very last section
            if end_col == last_section_end:
                xr = self.sectionViewportPosition(end_col) + self.sectionSize(end_col) - 1
                painter.drawLine(xr, 0, xr, total_h - 1)

        painter.restore()

    def _style_option(self, logical_index):
        opt = QStyleOptionHeader()
        self.initStyleOption(opt)
        opt.section = logical_index
        opt.rect    = QRect(
            self.sectionViewportPosition(logical_index),
            self._GROUP_H,
            self.sectionSize(logical_index),
            self._SUB_H,
        )
        opt.text = ""   # we draw text ourselves
        opt.position = QStyleOptionHeader.SectionPosition.Middle
        return opt
