"""ui/dialogs/trade_history.py — StockTradeHistoryDialog — detailed trade history for a single company.

Split out of the former single ui/dialogs.py (2026-09-17)."""
import logging

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QTableWidget, QTableWidgetItem, QHeaderView, QStyledItemDelegate,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPen

from ui.colors import QC_PROFIT, QC_LOSS
from ui.theme import SURFACE, LINE, TEXT_MUTED, TEXT_FAINT


logger = logging.getLogger(__name__)



# ---------------------------------------------------------------------------
# StockTradeHistoryDialog — Detailed trade history for a single stock
# ---------------------------------------------------------------------------
class StockTradeHistoryDialog(QDialog):
    def __init__(self, company, matches, total_pl, total_buy, total_sell, parent=None, is_open_position=False):
        super().__init__(parent)
        title = f"[{company}] Current Holdings" if is_open_position else f"[{company}] Detailed Trading History"
        self.setWindowTitle(title)
        self.resize(1000, 600)

        layout = QVBoxLayout(self)

        # Table
        tbl = QTableWidget()
        if is_open_position:
            cols = ["Buy Date", "Buy Price", "Buy Q'ty", "Buy Amt", "Status", "Current Price", "Current Q'ty", "Current Amt", "P/L", "P/L(%)"]
        else:
            cols = ["Buy Date", "Buy Price", "Buy Q'ty", "Buy Amt", "Days", "Sell Date", "Sell Price", "Sell Q'ty", "Sell Amt", "P/L", "P/L(%)"]
        tbl.setColumnCount(len(cols))
        tbl.setHorizontalHeaderLabels(cols)
        # open: data rows + total row + weight row; closed: data rows + total row
        tbl.setRowCount(len(matches) + (2 if is_open_position else 1))
        tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        tbl.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)

        # Custom vertical header: data rows show row numbers; footer rows appear blank.
        _n_data = len(matches)
        _footer_rows = ({_n_data, _n_data + 1} if is_open_position else {_n_data})

        class _FooterBlankVHeader(QHeaderView):
            def paintSection(self, painter, rect, logical_index):
                if logical_index in _footer_rows:
                    painter.save()
                    painter.fillRect(rect, QColor(SURFACE))
                    painter.restore()
                else:
                    super().paintSection(painter, rect, logical_index)

        _vh = _FooterBlankVHeader(Qt.Orientation.Vertical, tbl)
        _vh.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        _vh.setDefaultSectionSize(tbl.verticalHeader().defaultSectionSize())
        tbl.setVerticalHeader(_vh)

        def _si(text):
            it = QTableWidgetItem(str(text))
            it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            return it

        def _ni(val, fmt="{:,.0f}"):
            it = QTableWidgetItem(fmt.format(val))
            it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            return it

        for r, rec in enumerate(matches):
            tbl.setItem(r, 0, _si(rec.get("buy_date", "")))
            tbl.setItem(r, 1, _ni(rec.get("buy_price", 0)))
            tbl.setItem(r, 2, _ni(rec.get("qty", 0)))
            tbl.setItem(r, 3, _ni(rec.get("buy_amount", 0)))

            if is_open_position:
                # Open: col4=Status, col5=CurrPrice, col6=CurrQty, col7=CurrAmt, col8=P/L, col9=P/L%
                tbl.setItem(r, 4, _si("Open"))
                curr_price = rec.get("curr_price", 0)
                qty = rec.get("qty", 0)
                tbl.setItem(r, 5, _ni(curr_price))
                tbl.setItem(r, 6, _ni(qty))
                tbl.setItem(r, 7, _ni(curr_price * qty))
                pl_col, pct_col = 8, 9
                pl_val = rec.get("curr_pl", 0.0)
                pl_pct_val = rec.get("curr_pl_pct", 0.0)
            else:
                # Closed: col4=Days, col5=SellDate, col6=SellPrice, col7=SellQty, col8=SellAmt, col9=P/L, col10=P/L%
                d = int(rec.get("days_held", 0) or 0)
                days_it = QTableWidgetItem(str(d) if d > 0 else "-")
                days_it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                days_it.setForeground(QColor(TEXT_FAINT))
                tbl.setItem(r, 4, days_it)
                tbl.setItem(r, 5, _si(rec.get("sell_date", "")))
                tbl.setItem(r, 6, _ni(rec.get("sell_price", 0)))
                tbl.setItem(r, 7, _ni(rec.get("sell_qty", 0)))
                tbl.setItem(r, 8, _ni(rec.get("sell_amount", 0)))
                pl_col, pct_col = 9, 10
                pl_val = rec.get("pl", 0.0)
                pl_pct_val = rec.get("pl_pct", 0.0)

            pl_it = _ni(pl_val)
            if pl_val > 0: pl_it.setForeground(QC_PROFIT)
            elif pl_val < 0: pl_it.setForeground(QC_LOSS)
            tbl.setItem(r, pl_col, pl_it)

            pl_pct_it = _ni(pl_pct_val, "{:+.1f}%")
            if pl_pct_val > 0: pl_pct_it.setForeground(QC_PROFIT)
            elif pl_pct_val < 0: pl_pct_it.setForeground(QC_LOSS)
            tbl.setItem(r, pct_col, pl_pct_it)

        # ---Total row (always present for both open and closed) ---
        total_row = len(matches)
        bold_font = tbl.item(0, 0).font() if tbl.rowCount() > 0 else tbl.font()
        bold_font.setBold(True)

        def _bold_label(text, align=Qt.AlignmentFlag.AlignCenter):
            it = QTableWidgetItem(text)
            it.setTextAlignment(align)
            it.setFont(bold_font)
            return it

        def _bold_ni(val, fmt="{:,.0f}"):
            it = _ni(val, fmt)
            it.setFont(bold_font)
            return it

        if is_open_position:
            # Span cols 0-2 for "Total" label
            tbl.setSpan(total_row, 0, 1, 3)
            tbl.setItem(total_row, 0, _bold_label("Total"))
            # Buy Amt (col 3)
            tbl.setItem(total_row, 3, _bold_ni(total_buy))
            # Status col blank
            tbl.setItem(total_row, 4, QTableWidgetItem(""))
            # Current Price / Current Q'ty blank
            tbl.setItem(total_row, 5, QTableWidgetItem(""))
            tbl.setItem(total_row, 6, QTableWidgetItem(""))
            # Current Amt (col 7)
            tbl.setItem(total_row, 7, _bold_ni(total_sell))
            # P/L (col 8)
            pl_tot_it = _bold_ni(total_pl)
            if total_pl > 0: pl_tot_it.setForeground(QC_PROFIT)
            elif total_pl < 0: pl_tot_it.setForeground(QC_LOSS)
            tbl.setItem(total_row, 8, pl_tot_it)
            # P/L % (col 9)
            if total_buy > 0:
                total_pct = (total_pl / total_buy) * 100
                pct_tot_it = _bold_ni(total_pct, "{:+.1f}%")
                if total_pct > 0: pct_tot_it.setForeground(QC_PROFIT)
                elif total_pct < 0: pct_tot_it.setForeground(QC_LOSS)
            else:
                pct_tot_it = QTableWidgetItem("")
                pct_tot_it.setFont(bold_font)
            tbl.setItem(total_row, 9, pct_tot_it)
            tbl.setVerticalHeaderItem(total_row, QTableWidgetItem(""))  # hide row number
        else:
            # Closed: 11 cols - span 0-3 for "Total", col4(Days) blank, col5-8 sell section, col9=P/L, col10=P/L%
            tbl.setSpan(total_row, 0, 1, 4)
            tbl.setItem(total_row, 0, _bold_label("Total"))
            tbl.setItem(total_row, 4, QTableWidgetItem(""))  # Days blank
            tbl.setSpan(total_row, 5, 1, 3)
            tbl.setItem(total_row, 5, QTableWidgetItem(""))
            tbl.setItem(total_row, 8, _bold_ni(total_sell))
            total_pl_it = _bold_ni(total_pl)
            if total_pl > 0: total_pl_it.setForeground(QC_PROFIT)
            elif total_pl < 0: total_pl_it.setForeground(QC_LOSS)
            tbl.setItem(total_row, 9, total_pl_it)
            if total_buy > 0:
                total_pct = (total_pl / total_buy) * 100
                pct_it = _bold_ni(total_pct, "{:+.1f}%")
                if total_pct > 0: pct_it.setForeground(QC_PROFIT)
                elif total_pct < 0: pct_it.setForeground(QC_LOSS)
            else:
                pct_it = QTableWidgetItem("")
                pct_it.setFont(bold_font)
            tbl.setItem(total_row, 10, pct_it)
            tbl.setVerticalHeaderItem(total_row, QTableWidgetItem(""))  # hide row number

        tbl.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)

        if not is_open_position:
            # Closed position: draw outline border around Total row via delegate.
            _closed_total_row = total_row
            _closed_n_cols = tbl.columnCount()
            _closed_grid_clr = QColor(LINE)

            class _ClosedDelegate(QStyledItemDelegate):
                def paint(self, painter, option, index):
                    super().paint(painter, option, index)
                    r = option.rect
                    row = index.row()
                    col = index.column()
                    if row != _closed_total_row:
                        # Normal grid lines for data rows
                        painter.save()
                        painter.setPen(QPen(_closed_grid_clr, 1))
                        painter.drawLine(r.left(), r.bottom(), r.right(), r.bottom())
                        if col < _closed_n_cols - 1:
                            painter.drawLine(r.right(), r.top(), r.right(), r.bottom())
                        painter.restore()
                    else:
                        # Outline border around the entire Total row
                        painter.save()
                        painter.setPen(QPen(_closed_grid_clr, 1))
                        # painter.drawLine(r.left(), r.top(), r.right(), r.top())        # top
                        painter.drawLine(r.left(), r.bottom(), r.right(), r.bottom())  # bottom
                        if col == 0:
                            painter.drawLine(r.left() + 1, r.top(), r.left() + 1, r.bottom())  # left
                        if col == _closed_n_cols - 1:
                            painter.drawLine(r.right(), r.top(), r.right(), r.bottom()) # right
                        painter.restore()

            tbl.setShowGrid(False)
            tbl._closed_delegate = _ClosedDelegate(tbl)
            tbl.setItemDelegate(tbl._closed_delegate)

        if is_open_position:
            # Weight row: weight % in col 7 (Current Amt), all other cols blank
            weight_row = total_row + 1
            total_w = sum(rec.get("position_w", 0.0) for rec in matches)
            for c in range(10):
                tbl.setItem(weight_row, c, QTableWidgetItem(""))
            w_it = _bold_ni(total_w, "{:.1f}%")
            w_it.setForeground(QColor(TEXT_MUTED))
            tbl.setItem(weight_row, 7, w_it)
            tbl.setVerticalHeaderItem(weight_row, QTableWidgetItem(""))  # hide row number

            # Remove borders from footer rows (Total + Weight):
            # hide the built-in grid and re-draw only for data rows via delegate.
            _footer_rows = {total_row, weight_row}
            _grid_clr = QColor(LINE)
            _n_cols = tbl.columnCount()

            class _PartialGridDelegate(QStyledItemDelegate):
                def paint(self, painter, option, index):
                    super().paint(painter, option, index)
                    r = option.rect
                    row = index.row()
                    col = index.column()
                    if row not in _footer_rows:
                        # Draw normal grid lines for data rows
                        painter.save()
                        painter.setPen(QPen(_grid_clr, 1))
                        painter.drawLine(r.left(), r.bottom(), r.right(), r.bottom())
                        if col < _n_cols - 1:
                            painter.drawLine(r.right(), r.top(), r.right(), r.bottom())
                        painter.restore()
                    elif row == total_row:
                        # Draw outline border around the entire Total row
                        painter.save()
                        painter.setPen(QPen(_grid_clr, 1))
                        # painter.drawLine(r.left(), r.top(), r.right(), r.top())       # top
                        painter.drawLine(r.left(), r.bottom(), r.right(), r.bottom()) # bottom
                        if col == 0:
                            painter.drawLine(r.left() + 1, r.top(), r.left() + 1, r.bottom()) # left edge
                        if col == _n_cols - 1:
                            painter.drawLine(r.right(), r.top(), r.right(), r.bottom()) # right edge
                        painter.restore()

            tbl.setShowGrid(False)
            tbl._partial_grid_delegate = _PartialGridDelegate(tbl)
            tbl.setItemDelegate(tbl._partial_grid_delegate)

        layout.addWidget(tbl)

        close_btn = QPushButton("Close")
        close_btn.setFixedHeight(30)
        close_btn.setFixedWidth(100)
        close_btn.clicked.connect(self.accept)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        btn_layout.addWidget(close_btn)
        layout.addLayout(btn_layout)

        # ---Determine scroll bar visibility based on content height ---
        # Compare total content height against the available table viewport height.
        hdr_h = tbl.horizontalHeader().height()
        row_h = tbl.verticalHeader().defaultSectionSize()
        n_rows = tbl.rowCount()
        content_h = hdr_h + row_h * n_rows + 4  # +4 for border
        CLOSE_BTN_H = 44  # close button row + margins
        PADDING = 20      # dialog layout margins
        available_tbl_h = 600 - CLOSE_BTN_H - PADDING
        if content_h <= available_tbl_h:
            tbl.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        else:
            tbl.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setFixedSize(1000, 600)
