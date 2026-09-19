"""ui/widgets.py — Reusable UI widget classes (Phase 3-1 split)

Split out from: main.py (2026-08-29 feat/3-1-modularize)
Contains:
  FilterPopup, FilterableHeader, StockTable, GroupedHeaderView
"""
from collections import namedtuple

from PyQt6.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QLineEdit, QCheckBox, QScrollArea,
    QWidget, QPushButton, QTableWidget, QTableWidgetItem, QHeaderView,
    QStyleOptionHeader, QMenu, QStyledItemDelegate, QStyle, QApplication,
    QAbstractItemView,
)
from PyQt6.QtCore import Qt, pyqtSignal, QPoint, QRect, QTimer, QSize
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen, QPolygon, QKeySequence

from ui.common import create_font, create_numeric_font, FONT_FAMILY_CSS
from ui.colors import fg_for, heatmap_bg, PROFIT, LOSS, FLAT
from ui.theme import ACCENT, ACCENT_TEXT, ACCENT_BG, TEXT_FAINT, LINE, LINE_SOFT


# ---------------------------------------------------------------------------
# StockTable column spec (docs/ui.md 2.2's merged-column redesign, sourced
# from the "Trading Universe Redesign" mockup's own `cols`/`headers`
# tables) -- the one place label, min-width, flex weight, toggle group and
# heatmap scale live for every column.
#
# This replaces the prior 22-column layout (separate Name/Market/Ticker/Pf/
# MA/Del columns, Div(20)+Div(50), 52W High/High Diff/Low/Low Diff as four
# columns, 3D/5D/10D/20D/60D/120D) with the mockup's 13: Name+Ticker merged
# into one identity cell (with an inline status badge, replacing the Pf
# button column), a single 52W range bar (replacing 4 columns), Div(20) and
# Div(50) merged into one "MA20 Div" (Div(50) dropped -- the mockup doesn't
# carry it), momentum trimmed to 3D/20D/60D/120D (5D/10D dropped), a new
# "Chg" day-over-day column (data/cache.py's _TD_PERIODS now has a "1d"
# entry for this), and a "Trend" mini chart. The MA-chart and Delete action
# buttons are gone too -- see StockTable's docstring for what replaced them.
#
# group is the docs/ui.md 2.5 column-group toggle bucket ("price"/"value"/
# "momentum"), or None for the identity column, which the group toggle never
# hides. There is no data for a fourth "supply/demand" group in this table,
# so no toggle was added for one rather than shipping an empty, permanently-
# disabled button.
# scale is the docs/ui.md 1.2 heatmap alpha scale, or None for non-heatmap
# columns. "trend" isn't a text cell (see TrendDelegate) so it has no scale.
# "chg" (day-over-day) uses a tighter scale than the 3D-120D columns since a
# single day's move is rarely more than a few percent for most stocks.
# ---------------------------------------------------------------------------
ColSpec = namedtuple("ColSpec", ["key", "label", "min_width", "weight", "group", "scale"])

COLUMNS = [
    ColSpec("identity",  "Name / Ticker", 150, 2.4,  None,       None),
    ColSpec("price",     "Price",          86, 0.95, "price",    None),
    ColSpec("chg",       "Chg",            62, 0.7,  "price",    5),
    ColSpec("cap",       "Cap",            62, 0.7,  "price",    None),
    ColSpec("range52w",  "52W Range",     112, 1.5,  "price",    None),
    ColSpec("tper",      "tPER",           54, 0.55, "value",    None),
    ColSpec("fper",      "fPER",           54, 0.55, "value",    None),
    ColSpec("ma20div",   "MA20 Div",       94, 1.05, "momentum", 20),
    ColSpec("d3",        "3D",             58, 0.62, "momentum", 10),
    ColSpec("d20",       "20D",            58, 0.62, "momentum", 10),
    ColSpec("d60",       "60D",            58, 0.62, "momentum", 10),
    ColSpec("d120",      "120D",           58, 0.62, "momentum", 10),
    ColSpec("trend",     "Trend",          80, 1.0,  "momentum", None),
]

# Column indices as constants -- COLUMNS' own order is the single source of
# truth, but spelling out "self.item(row, 1)" everywhere a specific column
# is meant makes future reordering a silent bug. Kept in sync with COLUMNS
# by the assertion below (fails loudly at import time if they drift).
COL_IDENTITY, COL_PRICE, COL_CHG, COL_CAP, COL_RANGE52W, COL_TPER, COL_FPER, \
    COL_MA20DIV, COL_D3, COL_D20, COL_D60, COL_D120, COL_TREND = range(len(COLUMNS))
assert [c.key for c in COLUMNS] == [
    "identity", "price", "chg", "cap", "range52w", "tper", "fper",
    "ma20div", "d3", "d20", "d60", "d120", "trend",
]

# (group key, toolbar button label) -- docs/ui.md 2.5, default all-on.
TOGGLE_GROUPS = [
    ("price", "Price"),
    ("value", "Value"),
    ("momentum", "Momentum"),
]

# Momentum columns paired with the changes{} dict key each one reads.
_MOMENTUM_COLS = [(COL_D3, "3d"), (COL_D20, "20d"), (COL_D60, "60d"), (COL_D120, "120d")]

# docs/ui.md 1.7: one badge/marker vocabulary for the highlight states
# custom_settings.json stores as "On"/"Tg" (kept as-is; only the two places
# that display it -- this badge and the toolbar's "Target List" filter --
# now agree on wording, rather than a "Pf" header + "On"/"Tg" button label +
# yellow/sky-blue background all naming the same thing differently).
_STATUS_BADGE = {
    "On": ("Watch", ACCENT_TEXT, ACCENT, ACCENT_BG),
    "Tg": ("Target", "#ffffff", ACCENT, ACCENT),
}
_STATUS_MARKER = {"On": ACCENT, "Tg": ACCENT}


class NumericItem(QTableWidgetItem):
    """QTableWidgetItem with a numeric sort key independent of its display
    text.

    Found while rebuilding this table's cells for the docs/ui.md redesign:
    QTableWidgetItem aliases Qt.ItemDataRole.EditRole and
    Qt.ItemDataRole.DisplayRole onto the same storage by default (a
    documented but easy-to-miss Qt behavior for QStandardItem-family
    classes). The pattern this file (and history_table.py's ni()/pi(),
    assets_tab.py's _pct_item() etc.) used everywhere --
    `setData(EditRole, 1849000.0)` then `setText("1,849,000")` -- means
    whichever call runs last simply overwrites the other; the formatted
    string silently becomes the sort key too, so a comma-formatted numeric
    column sorts as plain text ("1,849,000" sorts before "999,000"). This
    subclass keeps the two independent by overriding __lt__ instead of
    relying on either role for ordering.
    """

    def __init__(self, text: str, sort_key):
        super().__init__(text)
        self._sort_key = sort_key

    def __lt__(self, other):
        other_key = getattr(other, "_sort_key", None)
        if other_key is None:
            return super().__lt__(other)
        return self._sort_key < other_key


# ---------------------------------------------------------------------------
# Cell-paint delegates (docs/ui.md 6.3: mini bars / range bars / sparklines
# need QStyledItemDelegate + QPainter -- Qt has no CSS-grid/minmax or
# cell-level border-radius equivalent, and a QWidget per cell for 300+ rows
# is exactly the "per-row cellWidget" pattern the mockup's own issue #7
# flags as a performance/visual-noise problem). All three delegates read
# their content from Qt.ItemDataRole.UserRole (a plain dict) rather than the
# item's display text, since none of these cells are single-line text.
# ---------------------------------------------------------------------------
class IdentityDelegate(QStyledItemDelegate):
    """Column 0: status marker + name + "ticker · market" meta + status
    badge, replacing the old separate Name/Market/Ticker columns and the
    per-row Pf button (docs/ui.md 2.2, 1.7)."""

    def paint(self, painter, option, index):
        painter.save()
        rect = option.rect
        self._paint_background(painter, option)

        data = index.data(Qt.ItemDataRole.UserRole) or {}
        name = index.data(Qt.ItemDataRole.DisplayRole) or ""
        meta = data.get("meta", "")
        status = data.get("status", "-")

        marker_color = QColor(_STATUS_MARKER.get(status, LINE))
        marker_h = 18
        painter.fillRect(rect.x() + 4, rect.y() + (rect.height() - marker_h) // 2, 3, marker_h, marker_color)

        badge = _STATUS_BADGE.get(status)
        badge_font = self._badge_font(option.font)
        badge_w = 0
        if badge:
            badge_w = QFontMetrics(badge_font).horizontalAdvance(badge[0]) + 14 + 6

        text_x = rect.x() + 4 + 3 + 8
        text_w = max(10, rect.width() - (text_x - rect.x()) - badge_w - 6)

        name_font = QFont(option.font)
        painter.setFont(name_font)
        painter.setPen(QColor("#1c1e2c"))
        fm = QFontMetrics(name_font)
        elided = fm.elidedText(name, Qt.TextElideMode.ElideRight, text_w)
        name_rect = QRect(text_x, rect.y(), text_w, rect.height() // 2 + (2 if meta else 0) + (rect.height() % 2))
        painter.drawText(name_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom if meta else Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, elided)

        if meta:
            meta_font = QFont(option.font)
            meta_font.setPointSize(max(6, option.font.pointSize() - 1))
            painter.setFont(meta_font)
            painter.setPen(QColor(TEXT_FAINT))
            meta_rect = QRect(text_x, rect.y() + rect.height() // 2, text_w, rect.height() // 2)
            painter.drawText(meta_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, meta)

        if badge:
            label, fg, border, bg = badge
            bw = QFontMetrics(badge_font).horizontalAdvance(label) + 14
            bh = 16
            bx = rect.right() - bw - 6
            by = rect.y() + (rect.height() - bh) // 2
            painter.setPen(QPen(QColor(border)))
            painter.setBrush(QColor(bg))
            painter.drawRoundedRect(bx, by, bw, bh, 4, 4)
            painter.setPen(QColor(fg))
            painter.setFont(badge_font)
            painter.drawText(QRect(bx, by, bw, bh), Qt.AlignmentFlag.AlignCenter, label)

        painter.restore()

    @staticmethod
    def _badge_font(base):
        f = QFont(base)
        f.setPointSize(max(6, base.pointSize() - 2))
        f.setBold(True)
        return f

    @staticmethod
    def _paint_background(painter, option):
        if option.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(option.rect, QColor(ACCENT_BG))
        elif option.features & option.ViewItemFeature.Alternate:
            painter.fillRect(option.rect, option.palette.alternateBase())
        else:
            painter.fillRect(option.rect, option.palette.base())

    def sizeHint(self, option, index):
        return QSize(super().sizeHint(option, index).width(), option.rect.height())


class RangeBarDelegate(QStyledItemDelegate):
    """52W Range column: a thin track with a position marker, plus
    low/position%/high labels underneath (docs/ui.md 2.3), replacing the
    four separate 52W High/High Diff/Low/Low Diff columns."""

    def paint(self, painter, option, index):
        painter.save()
        rect = option.rect
        IdentityDelegate._paint_background(painter, option)

        data = index.data(Qt.ItemDataRole.UserRole)
        if not data:
            painter.restore()
            return

        pad = 10
        track_y = rect.y() + 6
        track_h = 5
        track_x = rect.x() + pad
        track_w = max(1, rect.width() - 2 * pad)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(LINE_SOFT))
        painter.drawRoundedRect(track_x, track_y, track_w, track_h, 2, 2)

        pos = max(0.0, min(1.0, data["pos"]))
        marker_x = track_x + int(pos * track_w) - 1
        painter.setBrush(QColor(data["color"]))
        painter.drawRoundedRect(marker_x, track_y - 1, 2, track_h + 2, 1, 1)

        label_font = QFont(option.font)
        label_font.setPointSize(max(6, option.font.pointSize() - 2))
        painter.setFont(label_font)
        label_y = track_y + track_h + 3
        label_rect = QRect(track_x, label_y, track_w, rect.bottom() - label_y)
        fm = QFontMetrics(label_font)

        painter.setPen(QColor(TEXT_FAINT))
        painter.drawText(label_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, data["low"])
        painter.drawText(label_rect, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop, data["high"])
        painter.setPen(QColor(data["color"]))
        pct_text = data["pos_label"]
        pct_w = fm.horizontalAdvance(pct_text)
        painter.drawText(track_x + (track_w - pct_w) // 2, label_rect.y() + fm.ascent(), pct_text)

        painter.restore()


class TrendDelegate(QStyledItemDelegate):
    """Trend column: a small polyline of the 3D/20D/60D/120D momentum
    readings (docs/ui.md 2.2's "1Y" sparkline) -- honest about what data
    backs it (four real change-% points, not a year of daily closes, which
    this app doesn't fetch per watchlist row), rather than faking a smooth
    year-long chart."""

    def paint(self, painter, option, index):
        painter.save()
        IdentityDelegate._paint_background(painter, option)
        data = index.data(Qt.ItemDataRole.UserRole)
        if not data or len(data["points"]) < 2:
            painter.restore()
            return

        rect = option.rect
        pad_x, pad_y = 8, 5
        x0, y0 = rect.x() + pad_x, rect.y() + pad_y
        w = max(1, rect.width() - 2 * pad_x)
        h = max(1, rect.height() - 2 * pad_y)

        pts = data["points"]
        lo, hi = min(pts), max(pts)
        span = (hi - lo) or 1.0
        n = len(pts)
        coords = [
            (x0 + w * i / (n - 1), y0 + h - (v - lo) / span * h)
            for i, v in enumerate(pts)
        ]

        pen = QPen(QColor(data["color"]))
        pen.setWidthF(1.4)
        pen.setCosmetic(True)
        painter.setPen(pen)
        for (x1, y1), (x2, y2) in zip(coords, coords[1:]):
            painter.drawLine(int(x1), int(y1), int(x2), int(y2))
        painter.restore()


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
            "QFrame { background: white; border: 1px solid #aaa; border-radius: 4px; }"
        )

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
    # Market's dropdown filter moved to toolbar ALL/KOSPI/KOSDAQ buttons
    # (docs/ui.md 2.1); no column currently owns a filter icon. Left as an
    # empty set (not deleted) since docs/ui.md 2.6's "saved views" pass will
    # want per-column filters again for numeric-range/category columns.
    FILTER_COLS = set()

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
    """Trading Universe watchlist table (docs/ui.md 2). Rows are plain
    equities only -- index/bond/commodity rows are UniverseTab's Market
    Rail's job, not this table's (docs/ui.md 2.4).

    Per-row actions used to be three QPushButtons wired via
    add_action_buttons()/setCellWidget(); that's gone (docs/ui.md's own
    issue #7: 900 button widgets for 300 rows, always-visible Delete risking
    misclicks). What replaced it:
      - status badge: painted inline by IdentityDelegate, toggled via the
        context menu instead of clicked directly
      - MA chart: double-click or Enter/Return on a row (ma_chart_requested)
      - Delete: context menu, still confirmed by the caller (delete_requested)
      - AI Stock Report: context menu (ai_report_requested, unchanged)
      - Ctrl+C: copies the selected rows as TSV
    """
    col_filter_changed = pyqtSignal()  # emitted when any column filter changes
    ai_report_requested = pyqtSignal(str)  # emitted with the row's ticker (roadmap 2-1)
    ma_chart_requested = pyqtSignal(str)   # emitted with the row's ticker
    delete_requested = pyqtSignal(str)     # emitted with the row's ticker
    toggle_requested = pyqtSignal(str)     # emitted with the row's ticker

    # docs/ui.md 1.4 density toggle; "compact" is the default.
    DENSITY_ROW_HEIGHTS = {"compact": 28, "normal": 34, "spacious": 42}

    def __init__(self):
        super().__init__()
        self.setColumnCount(len(COLUMNS))
        self.setHorizontalHeaderLabels([c.label for c in COLUMNS])
        # Table font: Malgun Gothic Semilight 9pt (set appropriate size to prevent text cutoff)
        self.setFont(create_font(9, style_name="Semilight"))
        # Tabular-numerals font for numeric cells (docs/ui.md 1.3); built once
        # per table instance rather than per cell.
        self._numeric_font = create_numeric_font(9)
        self.setStyleSheet(
            "QTableWidget { gridline-color: #d0d0d0; " + FONT_FAMILY_CSS + " font-size: 9pt; }"
            "QTableWidget::item { padding: 1px 3px; }"
        )
        self._identity_delegate = IdentityDelegate(self)
        self._range_delegate = RangeBarDelegate(self)
        self._trend_delegate = TrendDelegate(self)
        self.setItemDelegateForColumn(COL_IDENTITY, self._identity_delegate)
        self.setItemDelegateForColumn(COL_RANGE52W, self._range_delegate)
        self.setItemDelegateForColumn(COL_TREND, self._trend_delegate)
        # Use filterable header
        self._filter_header = FilterableHeader(Qt.Orientation.Horizontal, self)
        self._filter_header.setFont(create_font(9, QFont.Weight.Bold))
        self._filter_header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self._filter_header.filter_requested.connect(self._show_filter_popup)
        self.setHorizontalHeader(self._filter_header)
        # Sort state the user last chose, so load_data() can reapply it after
        # a full reload instead of always resetting to insertion order
        # (docs/ui.md 1.8/1.9). -1 means "no sort / insertion order".
        self._sort_col = -1
        self._sort_order = Qt.SortOrder.AscendingOrder
        self._filter_header.sortIndicatorChanged.connect(self._on_sort_indicator_changed)
        self._col_filters = {}  # col_index -> frozenset | None
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        self.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        # docs/ui.md 1.8: row selection + keyboard navigation + Ctrl+C, in
        # place of the old NoSelection/NoFocus (which blocked both).
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAlternatingRowColors(True)
        self.setSortingEnabled(True)
        # docs/ui.md 1.5: don't force-fit columns into the viewport -- once
        # min-widths exceed it, a real horizontal scrollbar should appear
        # instead of silently shrinking every column below its floor.
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.doubleClicked.connect(self._on_row_activated)

        self._build_frozen_column()
        # A manual header drag on column 0 bypasses _stretch_columns, so the
        # overlay needs its own hook to stay the same width as the column.
        self._filter_header.sectionResized.connect(self._on_section_resized)
        self.set_density("compact")

    def _build_frozen_column(self):
        """Pins the Name column on horizontal scroll (docs/ui.md 1.5:
        identifier column stays visible) via a second, borderless
        QTableWidget mirroring column 0's content, overlaid on the main
        viewport's left edge. QTableWidget has no shared-model
        frozen-column support the way QTableView does, so this duplicates
        Qt's "Frozen Column Example" pattern at the item level.

        It covers only the data rows, not the header: the header cell
        (sort click, filter-icon painting) is left alone rather than adding
        a second click-passthrough layer to reason about, so clicking
        "Name" to sort still works exactly as before.
        """
        self._frozen = QTableWidget(self)
        self._frozen.setColumnCount(1)
        self._frozen.horizontalHeader().setVisible(False)
        self._frozen.verticalHeader().setVisible(False)
        self._frozen.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        self._frozen.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._frozen.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self._frozen.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._frozen.setAlternatingRowColors(True)
        self._frozen.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._frozen.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._frozen.setFont(self.font())
        self._frozen.setFrameShape(QFrame.Shape.NoFrame)
        self._frozen.setStyleSheet(
            "QTableWidget { gridline-color: #d0d0d0; border: none; border-right: 1px solid #b7bac7; "
            + FONT_FAMILY_CSS + " font-size: 9pt; }"
            "QTableWidget::item { padding: 1px 3px; }"
        )
        # Purely a visual mirror -- never the target of a click/hover, so
        # every real interaction (row selection, context menu, sort) still
        # reaches the main table underneath it.
        self._frozen.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._frozen.setItemDelegateForColumn(0, self._identity_delegate)
        self.verticalScrollBar().valueChanged.connect(self._frozen.verticalScrollBar().setValue)

    def _reposition_frozen(self):
        header_h = self.horizontalHeader().height()
        col_w = self.columnWidth(0)
        self._frozen.setGeometry(
            self.frameWidth(),
            header_h + self.frameWidth(),
            col_w,
            self.viewport().height(),
        )
        # The overlay's own column must track the main table's column 0
        # width too; left at QTableWidget's 100px default, the overlay paints
        # the identity cell at 100px and blank viewport for the rest of its
        # width -- which reads as a gap between Name and Price.
        self._frozen.setColumnWidth(0, col_w)
        self._frozen.raise_()

    def _on_section_resized(self, logical, _old, _new):
        if logical == 0:
            self._reposition_frozen()

    def _sync_frozen_column(self):
        """Mirror column 0's cell content and row-hidden state into the
        frozen overlay. Called after anything that reorders or (un)hides
        rows -- load_data()'s sort reapply and apply_col_filters()'s
        row-hide pass. Not needed after update_changed_rows(): the
        lightweight refresh never touches the identity cell (see its
        docstring). IdentityDelegate reads Qt.ItemDataRole.UserRole, so that
        has to be copied along with the display text."""
        if self._frozen.rowCount() != self.rowCount():
            self._frozen.setRowCount(self.rowCount())
        for row in range(self.rowCount()):
            src = self.item(row, 0)
            if src is None:
                continue
            mirror = QTableWidgetItem(src.text())
            mirror.setTextAlignment(src.textAlignment())
            mirror.setData(Qt.ItemDataRole.UserRole, src.data(Qt.ItemDataRole.UserRole))
            self._frozen.setItem(row, 0, mirror)
            self._frozen.setRowHidden(row, self.isRowHidden(row))

    def set_density(self, level: str):
        """Row-height toggle (docs/ui.md 1.4); 'compact' is the default."""
        h = self.DENSITY_ROW_HEIGHTS.get(level, self.DENSITY_ROW_HEIGHTS["compact"])
        self._density = level
        self.verticalHeader().setDefaultSectionSize(h)
        self._frozen.verticalHeader().setDefaultSectionSize(h)

    def set_column_group_visible(self, group: str, visible: bool):
        """Show/hide every column in `group` (docs/ui.md 2.5 column-group
        toggle). The identity column (group=None) is structural and never
        affected."""
        changed = False
        for col, spec in enumerate(COLUMNS):
            if spec.group == group and self.isColumnHidden(col) != (not visible):
                self.setColumnHidden(col, not visible)
                changed = True
        if changed:
            self._stretch_columns()

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

    def _row_identity(self, row):
        """The (ticker, status) UserRole payload for a view row, or None.
        Reads it straight from the clicked/activated row's own cell rather
        than an index into the data list passed to load_data(), so it stays
        correct after the user re-sorts a column (setSortingEnabled(True)
        means visual row order != load order)."""
        item = self.item(row, COL_IDENTITY)
        if item is None:
            return None
        data = item.data(Qt.ItemDataRole.UserRole)
        if not data or not data.get("ticker"):
            return None
        return data

    def contextMenuEvent(self, event):
        """Right-click menu (docs/ui.md 1.8): the per-row action buttons
        this table used to have (Tg/MA/Del) live here now, plus the
        pre-existing AI Stock Report action."""
        row = self.rowAt(event.pos().y())
        if row < 0:
            return
        data = self._row_identity(row)
        if data is None:
            return
        ticker = data["ticker"]

        menu = QMenu(self)
        status = data.get("status", "-")
        toggle_label = {"On": "Set as Target", "Tg": "Clear Watch/Target"}.get(status, "Add to Watch")
        toggle_action = menu.addAction(toggle_label)
        ma_action = menu.addAction("📈 View MA Chart")
        report_action = menu.addAction("🤖 AI Stock Report")
        menu.addSeparator()
        delete_action = menu.addAction("Delete...")

        chosen = menu.exec(event.globalPos())
        if chosen is toggle_action:
            self.toggle_requested.emit(ticker)
        elif chosen is ma_action:
            self.ma_chart_requested.emit(ticker)
        elif chosen is report_action:
            self.ai_report_requested.emit(ticker)
        elif chosen is delete_action:
            self.delete_requested.emit(ticker)

    def _on_row_activated(self, index):
        """Double-click opens the MA chart (docs/ui.md 1.8), replacing the
        old per-row 📈 button."""
        data = self._row_identity(index.row())
        if data is not None:
            self.ma_chart_requested.emit(data["ticker"])

    def keyPressEvent(self, event):
        if event.matches(QKeySequence.StandardKey.Copy):
            self._copy_selection_tsv()
            return
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            row = self.currentRow()
            data = self._row_identity(row) if row >= 0 else None
            if data is not None:
                self.ma_chart_requested.emit(data["ticker"])
                return
        super().keyPressEvent(event)

    def _copy_selection_tsv(self):
        """Ctrl+C: copy the selected rows as tab-separated text, one line
        per row, in on-screen column order (docs/ui.md 1.8)."""
        rows = sorted({idx.row() for idx in self.selectedIndexes()})
        if not rows:
            return
        lines = []
        for row in rows:
            identity = self._row_identity(row) or {}
            cells = [identity.get("name", ""), identity.get("meta", "")]
            for col in range(1, self.columnCount()):
                if self.isColumnHidden(col):
                    continue
                item = self.item(row, col)
                cells.append(item.text() if item else "")
            lines.append("\t".join(cells))
        QApplication.clipboard().setText("\n".join(lines))

    def _stretch_columns(self):
        """Assigns every visible column's width from COLUMNS: fixed columns
        (weight=None) get their min_width outright; flexible columns share
        the remaining viewport width by weight, floored at their own
        min_width (docs/ui.md 1.5) instead of one flat constant. Once the
        min-width floor of the visible columns exceeds the viewport, widths
        simply stop shrinking below it and a horizontal scrollbar appears
        (ScrollBarAsNeeded) rather than columns being squeezed unreadable.
        Hidden columns (docs/ui.md 2.5 group toggle) are excluded from the
        budget entirely, so hiding a group hands its space to what remains.
        """
        if self.rowCount() == 0:
            return

        vp_w = int(self.viewport().width() * 0.99)
        if vp_w <= 0:
            return

        fixed_total = 0
        flex_specs = []  # (col, spec) for visible, flexible columns
        for col, spec in enumerate(COLUMNS):
            if self.isColumnHidden(col):
                continue
            if spec.weight is None:
                fixed_total += spec.min_width
            else:
                flex_specs.append((col, spec))

        flex_w = vp_w - fixed_total
        total_weight = sum(spec.weight for _, spec in flex_specs) or 1.0
        unit = flex_w / total_weight if flex_specs else 0.0

        new_widths = {}
        for col, spec in enumerate(COLUMNS):
            if spec.weight is None:
                new_widths[col] = spec.min_width
        for col, spec in flex_specs:
            new_widths[col] = max(spec.min_width, int(spec.weight * unit))

        # Rounding residual - absorb into the identity column (index 0),
        # when visible, using exact arithmetic to prevent per-frame pixel
        # drift that causes the viewport to oscillate.
        if not self.isColumnHidden(0):
            used = sum(w for c, w in new_widths.items() if not self.isColumnHidden(c))
            residual = vp_w - used
            new_widths[0] = max(COLUMNS[0].min_width, new_widths[0] + residual)

        # Skip redundant setColumnWidth calls when nothing has changed.
        # Each setColumnWidth() fires an internal geometry update that can
        # re-trigger resizeEvent, so avoiding no-op calls breaks the loop.
        if getattr(self, '_last_col_widths', None) != new_widths:
            self._last_col_widths = new_widths
            for col, w in new_widths.items():
                self.setColumnWidth(col, w)

        self._reposition_frozen()

    def _on_sort_indicator_changed(self, col, order):
        self._sort_col = col
        self._sort_order = order
        # A live header click reaches here through Qt's own internal
        # sortIndicatorChanged handling, not through load_data()/
        # apply_col_filters(), so the frozen overlay needs its own resync
        # hook for this path. Whether Qt's own reorder has already run by
        # the time this slot fires depends on connection order, which shifts
        # every time load_data() cycles setSortingEnabled() (confirmed by
        # trial: it's fired both before and after depending on how many
        # reloads preceded it) -- not something to rely on. Deferring to the
        # next event-loop tick sidesteps that entirely: by then every slot
        # for this emission, in whatever order, has finished.
        QTimer.singleShot(0, self._sync_frozen_column)

    def load_data(self, data, highlights=None):
        if highlights is None:
            highlights = {}

        self.setUpdatesEnabled(False)  # UI Batch Repaint Optimization
        try:
            self.setSortingEnabled(False)
            self.clearContents()
            if self.rowCount() != len(data):
                self.setRowCount(len(data))

            for row, item in enumerate(data):
                self._populate_row(row, item, highlights)

            # MA20 buttons are added externally after load_data via add_ma20_button
            # Reapply whatever sort the user last chose (docs/ui.md 1.8: sort
            # state must survive a data refresh) instead of always resetting
            # to insertion order (Index-KOSPI-KOSDAQ-NASDAQ-S&P500, by market
            # cap) -- that reset is still what happens the first time, since
            # _sort_col starts at -1. setSortingEnabled(True) internally
            # triggers Qt's sortByColumn() using whatever the header's
            # indicator is at that moment, so the indicator must be
            # positioned BEFORE re-enabling sorting, not after.
            self._filter_header.setSortIndicator(self._sort_col, self._sort_order)
            self.setSortingEnabled(True)
            self._stretch_columns()
            self._sync_frozen_column()
        finally:
            self.setUpdatesEnabled(True)

    def _ticker_to_row(self) -> dict:
        """Ticker -> current view row, read fresh off the identity column's
        UserRole payload.

        Built on demand rather than incrementally maintained: with
        setSortingEnabled(True), a header click reorders items via Qt's own
        internal sort machinery, and there is no documented, reliable hook to
        patch an incremental cache in lock-step with that reorder. A fresh
        scan costs one QTableWidgetItem.data() read per row (a few hundred
        for this table) -- negligible next to the cell rebuild it's guarding,
        so it can never go stale.
        """
        mapping = {}
        for row in range(self.rowCount()):
            data = self._row_identity(row)
            if data is not None:
                mapping[data["ticker"]] = row
        return mapping

    def update_row_status(self, ticker: str, status: str):
        """Repaints just the identity cell's badge/marker for one ticker
        (docs/ui.md 1.7), in place of the old add_action_buttons() call that
        used to rebuild the Tg/MA/Del cell widgets after a status toggle."""
        row = self._ticker_to_row().get(ticker)
        if row is None:
            return
        item = self.item(row, COL_IDENTITY)
        if item is None:
            return
        data = dict(item.data(Qt.ItemDataRole.UserRole) or {})
        data["status"] = status
        item.setData(Qt.ItemDataRole.UserRole, data)
        self.viewport().update()
        self._sync_frozen_column()

    def update_changed_rows(self, data, changed_rows, highlights=None):
        """Incremental counterpart to load_data(): re-renders only the rows
        named in `changed_rows` (indices into `data`), instead of rebuilding
        every row's ~20 columns from scratch.

        Used by the 60s lightweight auto-refresh (UniverseLightweightFetchThread),
        which only ever changes price/changes for a subset of tickers -- name,
        market, ticker, market cap and PER never change on that path. Rebuilding
        every cell of every row on every tick regardless (as load_data() does)
        means tens of thousands of QTableWidgetItem allocations a minute for a
        few-hundred-row universe, for cells whose displayed value didn't move.

        `changed_rows` indices are positions in `data`, NOT view rows: with
        setSortingEnabled(True), a user sort reorders the displayed rows, so
        data index != screen row (docs/ui.md 1.9). Each changed item is
        therefore looked up by ticker to find where it currently sits on
        screen, instead of writing straight into `data`'s row number.
        """
        if not changed_rows:
            return
        if highlights is None:
            highlights = {}

        row_of_ticker = self._ticker_to_row()

        self.setUpdatesEnabled(False)
        try:
            self.setSortingEnabled(False)
            for data_idx in changed_rows:
                if not (0 <= data_idx < len(data)):
                    continue
                item = data[data_idx]
                view_row = row_of_ticker.get(item.get('ticker'))
                if view_row is None or not (0 <= view_row < self.rowCount()):
                    continue
                self._populate_row(view_row, item, highlights)
            self.setSortingEnabled(True)
        finally:
            self.setUpdatesEnabled(True)

    def _populate_row(self, row, item, highlights):
        """Renders every cell of one row from the redesigned 13-column spec
        (docs/ui.md 2.2). This table only ever receives plain equities now
        -- UniverseTab filters index/bond/commodity rows out to the Market
        Rail before calling load_data()/update_changed_rows() (docs/ui.md
        2.4) -- so the bp/abs/is_bond/is_index branching the old 22-column
        version needed for those rows is gone."""
        right = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        numeric_font = self._numeric_font

        currency = item.get('currency', '')
        ticker = item.get('ticker', '')
        is_usd = (currency == '$' and 'usd_price' in item)
        price_raw = float(item.get('usd_price', 0) if is_usd else item.get('price', 0))
        price_text = f"${price_raw:,.2f}" if is_usd else f"{int(price_raw):,}"

        # col 0: identity (name + "ticker · market" meta + status badge) --
        # see IdentityDelegate. Sorts by name (DisplayRole), same as the old
        # dedicated Name column did.
        status = highlights.get(ticker, "-")
        identity_item = QTableWidgetItem(item.get('name', ticker))
        identity_item.setData(Qt.ItemDataRole.UserRole, {
            "ticker": ticker,
            "name": item.get('name', ticker),
            "market": item.get('market', ''),
            "meta": f"{ticker} · {item.get('market', '')}",
            "status": status,
        })
        self.setItem(row, COL_IDENTITY, identity_item)

        # col 1: Price -- NumericItem (see its docstring: setData(EditRole)
        # + setText() alone would silently make this column sort as text).
        price_item = NumericItem(price_text, price_raw)
        price_item.setTextAlignment(right)
        price_item.setFont(numeric_font)
        self.setItem(row, COL_PRICE, price_item)

        changes = item.get('changes', {})

        # col 2: Chg -- day-over-day change (data/cache.py's _TD_PERIODS has
        # a "1d" entry now, computed the same way as 3D/20D/60D/120D).
        chg = float(changes.get("1d", 0.0) or 0.0)
        chg_item = NumericItem(f"{chg:+.1f}%", chg)
        chg_item.setTextAlignment(right)
        chg_item.setFont(numeric_font)
        chg_item.setForeground(fg_for(chg))
        bg = heatmap_bg(chg, COLUMNS[COL_CHG].scale)
        if bg is not None:
            chg_item.setBackground(bg)
        self.setItem(row, COL_CHG, chg_item)

        # col 3: Cap (market_cap is in KRW; 100,000,000 = 100M KRW)
        try:
            cap_eok = int(item.get('market_cap', 0)) // 100_000_000
        except Exception:
            cap_eok = 0
        cap_item = NumericItem(f"{cap_eok:,}", cap_eok)
        cap_item.setTextAlignment(right)
        cap_item.setFont(numeric_font)
        self.setItem(row, COL_CAP, cap_item)

        # col 4: 52W Range bar (docs/ui.md 2.3) -- replaces the old separate
        # 52W High/High Diff/Low/Low Diff columns. UserRole is left unset
        # (RangeBarDelegate paints nothing) when there's no valid range yet.
        low = float(changes.get("52w_low", 0.0) or 0.0)
        high = float(changes.get("52w_high", 0.0) or 0.0)
        if high > low > 0:
            pos = max(0.0, min(1.0, (price_raw - low) / (high - low)))
            color = PROFIT if pos > 0.8 else LOSS if pos < 0.25 else FLAT
            range_item = NumericItem("", pos)
            range_item.setData(Qt.ItemDataRole.UserRole, {
                "low": f"{low:,.0f}", "high": f"{high:,.0f}",
                "pos": pos, "pos_label": f"{pos * 100:.0f}%", "color": color,
            })
        else:
            range_item = NumericItem("", -1.0)
        self.setItem(row, COL_RANGE52W, range_item)

        # cols 5/6: tPER / fPER. tPER's color is a cheap/expensive value
        # judgment (docs/ui.md mockup: green <12x, warning-red >60x), a
        # different axis from the PROFIT/LOSS up/down convention, so it
        # intentionally does not reuse those two colors.
        for col, key in ((COL_TPER, 'trailing_per'), (COL_FPER, 'forward_per')):
            val = item.get(key)
            if val is not None:
                cell = NumericItem(f"{val:.1f}", float(val))
                cell.setTextAlignment(right)
                cell.setFont(numeric_font)
                if col == COL_TPER:
                    color = "#2e7d5b" if val < 12 else "#a32f26" if val > 60 else "#1c1e2c"
                    cell.setForeground(QColor(color))
            else:
                cell = NumericItem("-", float('-inf'))
                cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                cell.setForeground(QColor("#c3c6d4"))
            self.setItem(row, col, cell)

        # col 7: MA20 Div (Div(50) dropped -- the redesign doesn't carry it)
        ma20_div = float(changes.get("ma20_div", 0.0) or 0.0)
        if ma20_div != 0.0:
            diff = ma20_div - 100.0
            ma_item = NumericItem(f"{ma20_div:.1f}%", ma20_div)
            ma_item.setTextAlignment(right)
            ma_item.setFont(numeric_font)
            ma_item.setForeground(fg_for(diff))
            bg = heatmap_bg(diff, COLUMNS[COL_MA20DIV].scale)
            if bg is not None:
                ma_item.setBackground(bg)
        else:
            ma_item = NumericItem("-", float('-inf'))
            ma_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            ma_item.setForeground(QColor("#c3c6d4"))
        self.setItem(row, COL_MA20DIV, ma_item)

        # cols 8-11: 3D / 20D / 60D / 120D momentum (5D/10D dropped)
        trend_points = []
        for col, key in _MOMENTUM_COLS:
            val = float(changes.get(key, 0.0) or 0.0)
            trend_points.append(val)
            cell = NumericItem(f"{val:+.1f}%", val)
            cell.setTextAlignment(right)
            cell.setFont(numeric_font)
            if val != 0:
                cell.setForeground(fg_for(val))
                bg = heatmap_bg(val, COLUMNS[col].scale)
                if bg is not None:
                    cell.setBackground(bg)
            self.setItem(row, col, cell)

        # col 12: Trend -- a small chart of the four momentum readings above
        # (docs/ui.md's "1Y" sparkline, honestly relabeled: see TrendDelegate)
        trend_item = NumericItem("", trend_points[-1] if trend_points else 0.0)
        trend_item.setData(Qt.ItemDataRole.UserRole, {
            "points": trend_points,
            "color": PROFIT if trend_points and trend_points[-1] >= 0 else LOSS,
        })
        self.setItem(row, COL_TREND, trend_item)

    # ---Excel-style column filters (docs/ui.md 2.6: reserved for a future
    # "saved views" pass -- no column currently registers a FILTER_COLS
    # entry, since Market moved to the toolbar's ALL/KOSPI/KOSDAQ buttons) ---
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

    def apply_col_filters(self, text_filter="", tg_only=False, market="ALL"):
        """Apply the toolbar's market filter, Target List toggle and text
        search. Called by UniverseTab.filter_table(). `market` is one of
        "ALL"/"KOSPI"/"KOSDAQ" (docs/ui.md 2.1: replaced the old Market
        column's Excel-style dropdown with plain toolbar buttons, since
        Market is no longer a table column at all -- see the identity cell's
        "ticker · market" meta text instead)."""
        text_lower = text_filter.lower()

        hdr = self._filter_header
        sort_col = hdr.sortIndicatorSection()
        sort_order = hdr.sortIndicatorOrder()
        self.setUpdatesEnabled(False)  # suppress repaints while toggling row visibility
        self.setSortingEnabled(False)

        for row in range(self.rowCount()):
            hidden = False
            data = self._row_identity(row) or {}

            if market != "ALL" and data.get("market") != market:
                hidden = True

            if not hidden and tg_only and data.get("status") != "Tg":
                hidden = True

            if not hidden and text_lower:
                name = data.get("name", "").lower()
                ticker = data.get("ticker", "").lower()
                if text_lower not in name and text_lower not in ticker:
                    hidden = True

            self.setRowHidden(row, hidden)

        # Re-enable sorting and restore the exact same sort indicator so Qt
        # does not apply a different sort order.
        self.setSortingEnabled(True)
        hdr.setSortIndicator(sort_col, sort_order)
        self._sync_frozen_column()
        self.setUpdatesEnabled(True)


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

    def __init__(self, sections, sub_labels, parent=None, group_h=22, sub_h=22, sortable=False):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self._GROUP_H = group_h
        self._SUB_H = sub_h
        self._sections   = sections    # [(label, start, span, color), ...]
        self._sub_labels = sub_labels  # [str, ...]
        self.setDefaultSectionSize(72)
        # Clicking a section is what QTableView's own sortingEnabled hooks
        # into (QHeaderView.mousePressEvent -> setSortIndicator ->
        # sortIndicatorChanged); leaving this False (the default, matching
        # every caller before Total Assets' Phase 4) makes a table's
        # setSortingEnabled(True) inert -- there's no click for it to react to.
        self.setSectionsClickable(sortable)

    def set_sub_labels(self, sub_labels):
        """Swap the bottom-row labels in place (e.g. Total Assets tab's
        currency toggle relabels a "Total Assets" column KRW/USD) and repaint."""
        self._sub_labels = sub_labels
        self.viewport().update()

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
        for _, start, span, color in self._sections:
            if start <= logical_index < start + span:
                col_color = color
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
