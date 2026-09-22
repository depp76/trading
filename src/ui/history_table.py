"""ui/history_table.py — Cell factories and row rendering for the Trading History
table (split out of TradingHistoryTab on 2026-09-17)."""
from PyQt6.QtWidgets import QTableWidget, QTableWidgetItem, QLabel
from PyQt6.QtCore import Qt, QEvent
from PyQt6.QtGui import QColor, QPainter, QPen

from ui.colors import PROFIT, LOSS, FLAT, DANGER, QC_PROFIT, QC_LOSS
from ui.common import create_font, FONT_SMALL
from ui.theme import ACCENT, ACCENT_TEXT, SURFACE, ZEBRA, GRP_BG, TEXT, TEXT_MUTED, TEXT_FAINT, TEXT_EMPTY
from ui.widgets import ColSpec, NumericItem

# Numeric cells use the one app font too (right-aligned; see ui.common), built once.
_NUMERIC_FONT = create_font(FONT_SMALL, style_name="Semilight")

# "-" -> a thin, muted em dash (docs/ui.md 3.3): closed rows' empty Position
# section and open rows' empty Sell section used to render as a bold "-",
# the same visual weight as a real value.
_DASH = "—"
_DASH_COLOR = TEXT_EMPTY

# ---------------------------------------------------------------------------
# Column spec (docs/ui.md issue #9 "섹션 구분선을 페인트 이벤트에서 직접
# 그린다": header labels, group/section colors and column-width minimums
# used to live in three unrelated places -- _COLS in history_tab.py, SECTIONS
# here, and a hand-aligned `mins` list in history_tab.py._fit_columns) that
# had to be kept in lock-step by hand. This is the one place now; SECTIONS
# (still consumed as-is by GroupedHeaderView/SectionTable) and the header
# label list are both derived from it.
#
# weight=None means a fixed-width column (docs/ui.md issue #8 already covers
# only the Company column flexing to fill the viewport -- see
# TradingHistoryTab._fit_columns -- the rest of this table's columns are
# genuinely fixed-content widths, unlike Universe's flex/weighted columns).
# scale is unused here (no heatmap-background columns in this table).
# ---------------------------------------------------------------------------
_SECTION_COLOR = {
    "Trading":  ("#75798c", "#595d6c"),
    "Buy":      ("#2e7d5b", "#2e7d5b"),
    "Sell":     (DANGER, "#a32f26"),
    "Position": (ACCENT, ACCENT_TEXT),
    "Past":     ("#cfd3e5", "#75798c"),
}

COLUMNS = [
    ColSpec("company",    "Company", 140, None, "Trading",  None),  # flex-absorbing, see _fit_columns
    ColSpec("market",     "Market",   62, None, "Trading",  None),
    ColSpec("ticker",     "Ticker",   64, None, "Trading",  None),
    ColSpec("buy_date",   "Date",     84, None, "Buy",      None),
    ColSpec("buy_price",  "Price",    78, None, "Buy",      None),
    ColSpec("buy_qty",    "Q'ty",     55, None, "Buy",      None),
    ColSpec("buy_amount", "Amount",   85, None, "Buy",      None),
    ColSpec("sell_date",  "Date",     84, None, "Sell",     None),
    ColSpec("sell_days",  "Days",     40, None, "Sell",     None),
    ColSpec("sell_price", "Price",    78, None, "Sell",     None),
    ColSpec("sell_qty",   "Q'ty",     55, None, "Sell",     None),
    ColSpec("sell_amount","Amount",   85, None, "Sell",     None),
    ColSpec("sell_pl",    "P/L",      85, None, "Sell",     None),
    ColSpec("sell_pl_pct","P/L(%)",   70, None, "Sell",     None),
    ColSpec("pos_days",   "Days",     40, None, "Position", None),
    ColSpec("pos_price",  "Price",    78, None, "Position", None),
    ColSpec("pos_pl",     "P/L",      85, None, "Position", None),
    ColSpec("pos_pl_pct", "P/L(%)",   70, None, "Position", None),
    ColSpec("p5",         "5D",       70, None, "Past",     None),
    ColSpec("p10",        "10D",      70, None, "Past",     None),
    ColSpec("p20",        "20D",      70, None, "Past",     None),
]


def _derive_sections(columns):
    """Groups consecutive same-`group` ColSpecs into GroupedHeaderView/
    SectionTable's (label, start_col, span, color) tuples."""
    sections = []
    start = 0
    cur = columns[0].group
    for i in range(1, len(columns) + 1):
        g = columns[i].group if i < len(columns) else None
        if g != cur:
            sections.append((cur, start, i - start, _SECTION_COLOR[cur][0]))
            start = i
            cur = g
    return sections


# Column groups of the history grid: (label, first column, span, separator colour).
# Shared by GroupedHeaderView (two-row header) and SectionTable (vertical rules).
SECTIONS = _derive_sections(COLUMNS)


class SectionTable(QTableWidget):
    """QTableWidget that paints a coloured vertical rule at the left edge of
    every column group (and the right edge of the last one) after the cells."""

    def __init__(self, sections=SECTIONS, parent=None):
        super().__init__(parent)
        self._secs = sections
        self._last_col = max(start + span - 1 for _, start, span, _ in sections)

    def viewportEvent(self, event):
        result = super().viewportEvent(event)
        if event.type() == QEvent.Type.Paint:
            self._draw_section_lines()
        return result

    def _draw_section_lines(self):
        vp = self.viewport()
        painter = QPainter(vp)
        if not painter.isActive():
            return
        painter.save()
        h = vp.height()
        pen = QPen()
        pen.setWidth(1)
        pen.setCosmetic(True)
        for _, start, span, color in self._secs:
            end_col = start + span - 1
            pen.setColor(QColor(color))
            painter.setPen(pen)
            xl = self.columnViewportPosition(start)
            painter.drawLine(xl, 0, xl, h - 1)
            if end_col == self._last_col:
                xr = self.columnViewportPosition(end_col) + self.columnWidth(end_col) - 1
                painter.drawLine(xr, 0, xr, h - 1)
        painter.restore()


def si(text, align=Qt.AlignmentFlag.AlignCenter):
    it = QTableWidgetItem(text)
    it.setTextAlignment(align)
    return it


# Numeric cells are NumericItems: a plain QTableWidgetItem aliases EditRole
# onto DisplayRole, so the old setData(EditRole, num) + setText(formatted)
# pair left the formatted string as the sort key -- harmless only while this
# grid keeps sorting off (see NumericItem's docstring in ui/widgets.py).
def ni(val, fmt="{:,.0f}"):
    it = NumericItem(fmt.format(val), float(val))
    it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    it.setFont(_NUMERIC_FONT)
    return it


def pi(val: float):
    it = NumericItem(f"{val:+.1f}%", float(val))
    it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    it.setFont(_NUMERIC_FONT)
    if val > 0:
        it.setForeground(QC_PROFIT)
    elif val < 0:
        it.setForeground(QC_LOSS)
    return it


def wi(val: float):
    it = NumericItem(f"{val:.1f}%", float(val))
    it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    it.setFont(_NUMERIC_FONT)
    return it


def dash():
    it = QTableWidgetItem(_DASH)
    it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
    it.setForeground(QColor(_DASH_COLOR))
    return it


def loading_item():
    it = QTableWidgetItem("Total")
    it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
    it.setForeground(QColor(TEXT_FAINT))
    return it


def _monthly_summary_label(rec: dict) -> QLabel:
    """One rich-text line for a month group-header row (docs/ui.md 3.4):
    month, trade count, buy amount, realized P/L, win rate. Rendered as a
    QLabel spanning the whole row (see fill_table_rows) rather than per-
    column cells, so it's structurally not a data row -- nothing to sort or
    double-click-edit."""
    pl = rec.get("pl", 0.0)
    pl_color = PROFIT if pl > 0 else LOSS if pl < 0 else FLAT
    win_pct = rec.get("win_rate_pct")
    win_text = f"{win_pct:.0f}%" if win_pct is not None else "—"
    month_label = rec.get("buy_date", "")

    def field(label, value, color=TEXT):
        return (f'<span style="color:{TEXT_MUTED};">{label}</span> '
                f'<b style="color:{color};">{value}</b>')

    html = (
        f'<span style="font-weight:600; color:{ACCENT_TEXT};">{month_label}</span>'
        '&nbsp;&nbsp;&nbsp;' + field("Trades", rec.get("trade_count", 0)) +
        '&nbsp;&nbsp;&nbsp;' + field("Buy", f"{rec.get('buy_amount', 0.0):,.0f}") +
        '&nbsp;&nbsp;&nbsp;' + field("Realized P/L", f"{pl:+,.0f}", pl_color) +
        '&nbsp;&nbsp;&nbsp;' + field("Win rate", win_text)
    )
    lbl = QLabel(html)
    lbl.setStyleSheet(f"background: {GRP_BG}; padding-left: 12px; font-size: 9pt;")
    return lbl


def fill_table_rows(tbl, rows: list, *, hide_stale_closed: bool = True) -> list:
    """Render (kind, rec) rows into the unified history table; returns the
    per-row (kind, rec) list the tab keeps for double-click editing.

    hide_stale_closed: docs/ui.md 3.5 -- when True, a closed position's
    Position/Past columns go blank once curr_days > 30 (a toolbar toggle in
    TradingHistoryTab now controls this instead of it being hardcoded).
    """
    n_rows = len(rows)
    cur_rows = tbl.rowCount()
    # Adjust row count without full reset when possible
    if cur_rows != n_rows:
        tbl.setRowCount(n_rows)

    L = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
    n_cols = tbl.columnCount()

    row_data = []
    for r, (kind, rec) in enumerate(rows):
        row_data.append((kind, rec))  # preserve reference for double-click editing

        if kind == "monthly":
            tbl.setSpan(r, 0, 1, n_cols)
            tbl.setItem(r, 0, QTableWidgetItem(""))  # keeps _row_data/row indexing simple; label does the drawing
            tbl.setCellWidget(r, 0, _monthly_summary_label(rec))
            continue

        # A row that was a group header on a previous render and is a normal
        # trade row now (row count/position can be reused, see setRowCount
        # above) must drop its stale span/widget before being repopulated.
        if tbl.columnSpan(r, 0) != 1:
            tbl.setSpan(r, 0, 1, 1)
        if tbl.cellWidget(r, 0) is not None:
            tbl.removeCellWidget(r, 0)

        is_closed = bool(rec.get("sell_date") or rec.get("sell_price"))

        # ---Col 0-2: Company (+ Open/Closed state marker+badge, TradeStateDelegate) ---
        company_item = si(rec["company"], L)
        company_item.setData(Qt.ItemDataRole.UserRole, {"state": "Closed" if is_closed else "Open"})
        tbl.setItem(r, 0, company_item)
        tbl.setItem(r, 1, si(rec.get("market", ""), Qt.AlignmentFlag.AlignCenter))
        tbl.setItem(r, 2, si(rec.get("ticker", ""), Qt.AlignmentFlag.AlignCenter))

        # ---Col 3-6: Buy section ---
        tbl.setItem(r, 3, si(rec["buy_date"]))
        tbl.setItem(r, 4, ni(rec["buy_price"]))
        tbl.setItem(r, 5, ni(rec["qty"]))
        tbl.setItem(r, 6, ni(rec["buy_amount"]))

        # ---Col 7-13: Sell section ---
        if is_closed:
            tbl.setItem(r, 7,  si(rec.get("sell_date", "")) if rec.get("sell_date") else dash())
            tbl.setItem(r, 8,  ni(rec.get("days_held", 0)))
            tbl.setItem(r, 9,  ni(rec.get("sell_price", 0.0)))
            tbl.setItem(r, 10, ni(rec.get("sell_qty", 0.0)))
            tbl.setItem(r, 11, ni(rec.get("sell_amount", 0.0)))
            pl_it = ni(rec.get("pl", 0.0))
            if rec.get("pl", 0.0) > 0:   pl_it.setForeground(QC_PROFIT)
            elif rec.get("pl", 0.0) < 0: pl_it.setForeground(QC_LOSS)
            tbl.setItem(r, 12, pl_it)
            tbl.setItem(r, 13, pi(rec.get("pl_pct", 0.0)))
        else:
            for c in range(7, 14):
                tbl.setItem(r, c, dash())

        # ---Col 14-17: Position section ---
        is_open_row = (kind == "open")
        curr_price  = rec.get("curr_price", 0)

        # _refresh_summary already sets curr_days = (today - sell_date).days for closed rows
        hide_past_info = hide_stale_closed and kind == "closed" and rec.get("curr_days", 0) > 30

        if hide_past_info:
            tbl.setItem(r, 14, dash())
        else:
            tbl.setItem(r, 14, ni(rec["curr_days"]) if rec["curr_days"] else dash())

        if hide_past_info:
            tbl.setItem(r, 15, dash())
            tbl.setItem(r, 16, dash())
            tbl.setItem(r, 17, dash())
        elif curr_price > 0:
            tbl.setItem(r, 15, ni(curr_price))
            if is_open_row:
                pl_cur = ni(rec.get("curr_pl", 0.0))
                if rec.get("curr_pl", 0.0) > 0:   pl_cur.setForeground(QC_PROFIT)
                elif rec.get("curr_pl", 0.0) < 0: pl_cur.setForeground(QC_LOSS)
                tbl.setItem(r, 16, pl_cur)
                tbl.setItem(r, 17, pi(rec.get("curr_pl_pct", 0.0)))
            else:
                # closed row: Do not display P/L amount (col 16)
                # EXCEPT if sold today, display P/L based on current price (user request)
                if rec.get("curr_days") == 0:
                    curr_price = rec.get("curr_price", 0.0)
                    sell_price = rec.get("sell_price", 0.0)
                    s_qty      = rec.get("sell_qty", 0.0)

                    if curr_price > 0 and sell_price > 0:
                        # Opportunity P/L for positions sold today: current price - sell price
                        opp_pl = (curr_price - sell_price) * s_qty
                        opp_pl_pct = (curr_price - sell_price) / sell_price * 100

                        pl_cur = ni(opp_pl)
                        if opp_pl > 0:   pl_cur.setForeground(QC_PROFIT)
                        elif opp_pl < 0: pl_cur.setForeground(QC_LOSS)
                        tbl.setItem(r, 16, pl_cur)
                        tbl.setItem(r, 17, pi(opp_pl_pct))
                    else:
                        tbl.setItem(r, 16, dash())
                        tbl.setItem(r, 17, pi(rec.get("curr_pl_pct", 0.0)))
                else:
                    tbl.setItem(r, 16, dash())
                    tbl.setItem(r, 17, pi(rec.get("curr_pl_pct", 0.0)))
        elif is_open_row:
            tbl.setItem(r, 15, loading_item())
            tbl.setItem(r, 16, loading_item())
            tbl.setItem(r, 17, loading_item())
        else:
            tbl.setItem(r, 15, dash())
            tbl.setItem(r, 16, dash())
            tbl.setItem(r, 17, dash())

        # ---Col 18-20: Past section ---
        if hide_past_info:
            tbl.setItem(r, 18, dash())
            tbl.setItem(r, 19, dash())
            tbl.setItem(r, 20, dash())
        else:
            tbl.setItem(r, 18, pi(rec["wk1"])  if rec["wk1"]  else dash())
            tbl.setItem(r, 19, pi(rec["wk2"])  if rec["wk2"]  else dash())
            tbl.setItem(r, 20, pi(rec["mth1"]) if rec["mth1"] else dash())

        # ---Row background: one zebra stripe (docs/ui.md 3.7/issue #5) --
        # state used to be a second background channel (bg_open mint);
        # that's the marker+badge's job now, so this is purely alternation.
        bg = QColor(SURFACE if r % 2 == 0 else ZEBRA)
        for c in range(n_cols):
            it = tbl.item(r, c)
            if it:
                it.setBackground(bg)
    return row_data
