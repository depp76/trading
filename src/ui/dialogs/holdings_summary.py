"""ui/dialogs/holdings_summary.py — Per-company P/L summary popup for the Trading
History tab (split out of TradingHistoryTab on 2026-09-17)."""
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem, QHeaderView,
    QMessageBox, QLabel, QPushButton,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor

from ui.common import create_font


def show_holdings_summary(parent, closed_data: list, open_data: list):
    """Show a dialog with total P/L per company (closed realized + open unrealized),"
    sorted by total P/L descending."""
    # ---Accumulate per-company: buy amount, eval amount, P/L, days, buy_date ---
    pl_map:       dict[str, float] = {}   # company -> total P/L
    buy_map:      dict[str, float] = {}   # company -> total cost (buy amount)
    eval_map:     dict[str, float] = {}   # company -> total eval amount
    days_map:     dict[str, list]  = {}   # company -> list of days_held
    buy_date_map: dict[str, str]   = {}   # company -> earliest buy_date (str)

    for rec in closed_data:
        comp     = rec.get("company", "")
        buy_amt  = float(rec.get("buy_amount", 0.0))
        sell_amt = float(rec.get("sell_amount", 0.0))
        pl_val   = float(rec.get("pl", 0.0))
        days     = int(rec.get("days_held", 0) or 0)
        bd       = rec.get("buy_date", "")
        pl_map[comp]   = pl_map.get(comp, 0.0)   + pl_val
        buy_map[comp]  = buy_map.get(comp, 0.0)  + buy_amt
        # For closed: eval = sell amount (realized value)
        eval_map[comp] = eval_map.get(comp, 0.0) + sell_amt
        if days > 0:
            days_map.setdefault(comp, []).append(days)
        # Track earliest buy_date per company
        if bd and (comp not in buy_date_map or bd < buy_date_map[comp]):
            buy_date_map[comp] = bd

    for rec in open_data:
        comp    = rec.get("company", "")
        buy_amt = float(rec.get("buy_amount", 0.0))
        curr_pl = float(rec.get("curr_pl", 0.0))
        # If curr_price is not yet available, unrealized P/L = 0
        if rec.get("curr_price", 0.0) <= 0:
            curr_pl = 0.0
        eval_amt = buy_amt + curr_pl
        days     = int(rec.get("curr_days", 0) or 0)
        bd       = rec.get("buy_date", "")
        pl_map[comp]   = pl_map.get(comp, 0.0)   + curr_pl
        buy_map[comp]  = buy_map.get(comp, 0.0)  + buy_amt
        eval_map[comp] = eval_map.get(comp, 0.0) + eval_amt
        if days > 0:
            days_map.setdefault(comp, []).append(days)
        if bd and (comp not in buy_date_map or bd < buy_date_map[comp]):
            buy_date_map[comp] = bd

    if not pl_map:
        QMessageBox.information(parent, "Summary", "No trading data available.")
        return

    # ---Sort by total P/L descending (default) ---
    rows = sorted(pl_map.items(), key=lambda x: x[1], reverse=True)

    # ---Build dialog ---
    dlg = QDialog(parent)
    dlg.setWindowTitle("Holdings Summary - P/L by Company")
    dlg.resize(800, min(100 + 28 * len(rows) + 130, 780))

    v = QVBoxLayout(dlg)
    v.setContentsMargins(12, 10, 12, 10)
    v.setSpacing(8)

    # 5 columns: Company | Total Buy | Total Amount | P/L(- | P/L(%)
    tbl = QTableWidget(len(rows), 5)
    tbl.setHorizontalHeaderLabels(["Name", "Total Buy", "Total Amount", "P/L", "P/L (%)"])
    tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    tbl.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    tbl.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
    tbl.setAlternatingRowColors(True)
    tbl.verticalHeader().setVisible(False)
    tbl.setShowGrid(True)
    tbl.setStyleSheet(
        "QTableWidget { border:1px solid #d0d0d0; border-radius:6px; }"
        "QTableWidget::item { padding:2px 6px; }"
        "QHeaderView::section { background:#f0f2f5; font-weight:bold; padding:4px; "
        "border:none; border-right:1px solid #d0d0d0; border-bottom:1px solid #d0d0d0; }"
    )
    tbl_font = create_font(9, style_name="Semilight")
    tbl.setFont(tbl_font)
    tbl.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
    for c in range(1, 5):
        tbl.horizontalHeader().setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
    tbl.verticalHeader().setDefaultSectionSize(26)

    col_red  = QColor("#c0392b")
    col_blue = QColor("#2980b9")
    col_gray = QColor("#888888")

    def _ri(text, align=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, color=None):
        it = QTableWidgetItem(text)
        it.setTextAlignment(align)
        if color:
            it.setForeground(color)
        return it

    def _fill_summary_table(row_data):
        tbl.setRowCount(len(row_data))
        for r, (comp, pl) in enumerate(row_data):
            cost    = buy_map.get(comp, 0.0)
            eval_v  = eval_map.get(comp, 0.0)
            pct     = (pl / cost * 100) if cost > 0 else 0.0
            pl_col  = col_red if pl > 0 else (col_blue if pl < 0 else col_gray)

            # Col 0: Company name
            comp_it = QTableWidgetItem(comp)
            comp_it.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            tbl.setItem(r, 0, comp_it)

            # Col 1: Buy amount
            tbl.setItem(r, 1, _ri(f"{cost:,.0f}"))

            # Col 2: Eval amount
            tbl.setItem(r, 2, _ri(f"{eval_v:,.0f}"))

            # Col 3: P/L amount
            tbl.setItem(r, 3, _ri(f"{pl:+,.0f}", color=pl_col))

            # Col 4: P/L %
            tbl.setItem(r, 4, _ri(f"{pct:+.1f}%", color=pl_col))

    _fill_summary_table(rows)
    v.addWidget(tbl, 1)

    # ---Bottom summary panel ---
    pos_pl = sum(v2 for v2 in pl_map.values() if v2 > 0)
    neg_pl = sum(v2 for v2 in pl_map.values() if v2 < 0)

    def _html_val(val, positive=True):
        color = "#c0392b" if positive else "#2980b9"
        sign  = "+" if positive else ""
        return f"<b style='color:{color}'>{sign}{val:,.0f} KRW</b>"

    # (+)/(-) subtotals in a horizontal row
    subtotal_html = (
        f"(+) Total Profit:  {_html_val(pos_pl, positive=True)}"
        f"&nbsp;&nbsp;&nbsp;&nbsp;"
        f"(-) Total Loss:  {_html_val(neg_pl, positive=False)}"
    )
    subtotal_lbl = QLabel(subtotal_html)
    subtotal_lbl.setTextFormat(Qt.TextFormat.RichText)
    subtotal_lbl.setStyleSheet("padding:0px 4px 4px 4px;")
    subtotal_lbl.setFont(create_font(9, style_name="Semilight"))
    v.addWidget(subtotal_lbl)

    close_btn = QPushButton("Close")
    close_btn.setFixedWidth(90)
    close_btn.clicked.connect(dlg.accept)
    btn_row = QHBoxLayout()
    btn_row.addStretch()
    btn_row.addWidget(close_btn)
    v.addLayout(btn_row)

    dlg.exec()
