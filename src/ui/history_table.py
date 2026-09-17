"""ui/history_table.py — Cell factories and row rendering for the Trading History
table (split out of TradingHistoryTab on 2026-09-17)."""
from PyQt6.QtWidgets import QTableWidgetItem
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor


def si(text, align=Qt.AlignmentFlag.AlignCenter):
    it = QTableWidgetItem(text)
    it.setTextAlignment(align)
    return it


def ni(val, fmt="{:,.0f}"):
    it = QTableWidgetItem()
    it.setData(Qt.ItemDataRole.EditRole, round(float(val), 4))
    it.setText(fmt.format(val))
    it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    return it


def pi(val: float):
    it = QTableWidgetItem()
    it.setData(Qt.ItemDataRole.EditRole, round(val, 4))
    it.setText(f"{val:+.1f}%")
    it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    if val > 0:
        it.setForeground(QColor("#c0392b"))
    elif val < 0:
        it.setForeground(QColor("#2980b9"))
    return it


def wi(val: float):
    it = QTableWidgetItem()
    it.setData(Qt.ItemDataRole.EditRole, round(val, 4))
    it.setText(f"{val:.1f}%")
    it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    return it


def dash():
    it = QTableWidgetItem("-")
    it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
    it.setForeground(QColor("#aaaaaa"))
    return it


def loading_item():
    it = QTableWidgetItem("Total")
    it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
    it.setForeground(QColor("#999999"))
    return it


def fill_table_rows(tbl, rows: list) -> list:
    """Render (kind, rec) rows into the unified history table; returns the
    per-row (kind, rec) list the tab keeps for double-click editing."""
    n_rows = len(rows)
    cur_rows = tbl.rowCount()
    # Adjust row count without full reset when possible
    if cur_rows != n_rows:
        tbl.setRowCount(n_rows)

    L = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
    bg_even = QColor("#ffffff")
    bg_odd  = QColor("#f5f7fa")
    bg_open = QColor("#edfbf0")   # mint for current holdings
    n_cols  = tbl.columnCount()

    # Pre-build colour-constant items to avoid repeated QColor() in inner loop
    col_red  = QColor("#c0392b")
    col_blue = QColor("#2980b9")
    bg_summary = QColor("#fff5e6")

    row_data = []
    closed_idx = 0
    for r, (kind, rec) in enumerate(rows):
        row_data.append((kind, rec))  # preserve reference for double-click editing

        if kind == "monthly":
            tbl.setItem(r, 0, si(rec["company"], Qt.AlignmentFlag.AlignCenter))
            tbl.setItem(r, 1, dash())
            tbl.setItem(r, 2, dash())

            tbl.setItem(r, 3, si(rec["buy_date"]))
            tbl.setItem(r, 4, dash())
            tbl.setItem(r, 5, dash())
            tbl.setItem(r, 6, ni(rec["buy_amount"]))

            tbl.setItem(r, 7, dash())
            tbl.setItem(r, 8, dash())
            tbl.setItem(r, 9, dash())
            tbl.setItem(r, 10, dash())
            tbl.setItem(r, 11, dash())

            pl_it = ni(rec["pl"])
            if rec["pl"] > 0: pl_it.setForeground(col_red)
            elif rec["pl"] < 0: pl_it.setForeground(col_blue)
            tbl.setItem(r, 12, pl_it)
            tbl.setItem(r, 13, dash())

            for c in range(14, tbl.columnCount()):
                tbl.setItem(r, c, dash())

            # Highlight summary row
            for c in range(tbl.columnCount()):
                if tbl.item(r, c):
                    tbl.item(r, c).setBackground(bg_summary)
                    font = tbl.item(r, c).font()
                    font.setBold(True)
                    tbl.item(r, c).setFont(font)
            continue

        # ---Col 0-2: Company ---
        tbl.setItem(r, 0, si(rec["company"], L))
        tbl.setItem(r, 1, si(rec.get("market", ""), Qt.AlignmentFlag.AlignCenter))
        tbl.setItem(r, 2, si(rec.get("ticker", ""), Qt.AlignmentFlag.AlignCenter))

        # ---Col 3-6: Buy section ---
        tbl.setItem(r, 3, si(rec["buy_date"]))
        tbl.setItem(r, 4, ni(rec["buy_price"]))
        tbl.setItem(r, 5, ni(rec["qty"]))
        tbl.setItem(r, 6, ni(rec["buy_amount"]))

        is_closed = bool(rec.get("sell_date") or rec.get("sell_price"))

        # ---Col 7-13: Sell section ---
        if is_closed:
            tbl.setItem(r, 7,  si(rec.get("sell_date", "")) if rec.get("sell_date") else dash())
            tbl.setItem(r, 8,  ni(rec.get("days_held", 0)))
            tbl.setItem(r, 9,  ni(rec.get("sell_price", 0.0)))
            tbl.setItem(r, 10, ni(rec.get("sell_qty", 0.0)))
            tbl.setItem(r, 11, ni(rec.get("sell_amount", 0.0)))
            pl_it = ni(rec.get("pl", 0.0))
            if rec.get("pl", 0.0) > 0:   pl_it.setForeground(col_red)
            elif rec.get("pl", 0.0) < 0: pl_it.setForeground(col_blue)
            tbl.setItem(r, 12, pl_it)
            tbl.setItem(r, 13, pi(rec.get("pl_pct", 0.0)))
        else:
            for c in range(7, 14):
                tbl.setItem(r, c, dash())

        # ---Col 14-17: Position section ---
        is_open_row = (kind == "open")
        curr_price  = rec.get("curr_price", 0)

        # _refresh_summary already sets curr_days = (today - sell_date).days for closed rows
        hide_past_info = (kind == "closed" and rec.get("curr_days", 0) > 30)

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
                if rec.get("curr_pl", 0.0) > 0:   pl_cur.setForeground(col_red)
                elif rec.get("curr_pl", 0.0) < 0: pl_cur.setForeground(col_blue)
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
                        if opp_pl > 0:   pl_cur.setForeground(col_red)
                        elif opp_pl < 0: pl_cur.setForeground(col_blue)
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

        # ---Row background (single pass via setBackground per item) ---
        if kind == "closed":
            bg = bg_even if closed_idx % 2 == 0 else bg_odd
            closed_idx += 1
        else:
            bg = bg_open

        for c in range(n_cols):
            it = tbl.item(r, c)
            if it:
                it.setBackground(bg)
    return row_data
