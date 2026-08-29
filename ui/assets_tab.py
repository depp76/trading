"""ui/assets_tab.py — TradingRecordTab (Phase 4 분리)

분리 출처: main.py (2026-08-29 feat/3-1-modularize, Phase 4)
포함 클래스:
  TradingRecordTab
"""
import logging
import os
import json
import datetime as _dt
from datetime import datetime

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem,
    QLineEdit, QPushButton, QLabel, QHeaderView, QComboBox, QMessageBox,
    QInputDialog,
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QFont

from data_fetcher import get_usd_krw_rate_for_date, get_index_close_for_date
from ui.widgets import GroupedHeaderView
from ui.dialogs import TotalAssetsGraphDialog

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lazy helper -- avoid circular imports with main (main.py imports this
# module at load time, so this module cannot import main at load time in
# return). Forwards to main's implementation so the class body below can
# call create_font(...) unchanged from its original form in main.py.
# ---------------------------------------------------------------------------
def _get_create_font():
    import main as _m
    return _m.create_font


def create_font(*args, **kwargs):
    return _get_create_font()(*args, **kwargs)


class TradingRecordTab(QWidget):
    """Tab for recording periodic total-asset snapshots with weekly/cumulative return calculations."""

    _JSON_FILE = "trading_record.json"
    _COLS_SUB = [
        "Date", "KOSPI", "", "", "Total Assets", "", "", "", "",
        "USD/KRW", "Total Assets($)", "", "", "", ""
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._records: list[dict] = []   # [{"date": str, "total": float}, ...]
        self._build_ui()
        self._load_records()
        self._schedule_daily_sync()

    # ---UI ---
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(8)
        root.setContentsMargins(12, 10, 12, 10)

        # ---Controls bar ---
        ctrl = QHBoxLayout()

        self._date_combo = QComboBox()
        self._date_combo.setFont(create_font(10, style_name="Semilight"))
        self._date_combo.setFixedWidth(150)
        self._date_combo.setStyleSheet(
            "QComboBox { border:1px solid #ccc; border-radius:4px; padding:4px 6px; font-family: 'Malgun Gothic Semilight', '맑은 고딕 Semilight', 'Malgun Gothic'; font-size: 10pt; }"
        )
        for label, _ in self._friday_dates():
            self._date_combo.addItem(label)
        # Default to the most recent (last) Friday
        if self._date_combo.count() > 0:
            self._date_combo.setCurrentIndex(self._date_combo.count() - 1)

        self._asset_edit = QLineEdit()
        self._asset_edit.setFont(create_font(10, style_name="Semilight"))
        self._asset_edit.setPlaceholderText("Total Assets")
        self._asset_edit.setFixedWidth(120)
        self._asset_edit.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._asset_edit.setStyleSheet(
            "QLineEdit { border:1px solid #ccc; border-radius:4px; padding:4px 6px; font-family: 'Malgun Gothic Semilight', '맑은 고딕 Semilight', 'Malgun Gothic'; font-size: 10pt; }"
        )
        self._asset_edit.textEdited.connect(self._fmt_asset_input)

        add_btn = QPushButton("\u2795  Add Record")
        add_btn.setFont(create_font(10, QFont.Weight.Bold))
        add_btn.setFixedHeight(32)
        add_btn.setStyleSheet(
            "QPushButton { background:#0078d4; color:white; border-radius:4px; padding:4px 14px; font-weight:bold; font-family: 'Malgun Gothic Semilight', '맑은 고딕 Semilight', 'Malgun Gothic'; }"
            "QPushButton:hover { background:#005a9e; }"
        )
        add_btn.clicked.connect(self._add_record)

        del_btn = QPushButton("\U0001f5d1  Delete Selected")
        del_btn.setFont(create_font(10, QFont.Weight.Bold))
        del_btn.setFixedHeight(32)
        del_btn.setStyleSheet(
            "QPushButton { background:#c0392b; color:white; border-radius:4px; padding:4px 14px; font-weight:bold; font-family: 'Malgun Gothic Semilight', '맑은 고딕 Semilight', 'Malgun Gothic'; }"
            "QPushButton:hover { background:#a93226; }"
        )
        del_btn.clicked.connect(self._delete_selected)

        today_btn = QPushButton("\U0001f4c5  This Week")
        today_btn.setFont(create_font(10, QFont.Weight.Bold))
        today_btn.setFixedHeight(32)
        today_btn.setStyleSheet(
            "QPushButton { background:#107c10; color:white; border-radius:4px; padding:4px 14px; font-weight:bold; font-family: 'Malgun Gothic Semilight', '맑은 고딕 Semilight', 'Malgun Gothic'; }"
            "QPushButton:hover { background:#0b5e0b; }"
        )
        def _select_latest():
            self._date_combo.setCurrentIndex(self._date_combo.count() - 1)
        today_btn.clicked.connect(_select_latest)

        lbl_date = QLabel("Date:")
        lbl_date.setFont(create_font(10, style_name="Semilight"))
        lbl_assets = QLabel("Total Assets:")
        lbl_assets.setFont(create_font(10, style_name="Semilight"))

        live_asset_title = QLabel("Current Total Asset:")
        live_asset_title.setFont(create_font(10, style_name="Semilight"))

        self.live_asset_lbl = QLineEdit("-")
        self.live_asset_lbl.setReadOnly(True)
        self.live_asset_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.live_asset_lbl.setFont(create_font(10, QFont.Weight.Bold))
        self.live_asset_lbl.setStyleSheet("QLineEdit { background:transparent; color:#2c3e50; border:1px solid #ccc; border-radius:4px; padding:3px 6px; }")
        self.live_asset_lbl.setFixedWidth(120)
        
        live_diff_title = QLabel("Weekly P/L:")
        live_diff_title.setFont(create_font(10, style_name="Semilight"))

        self.live_diff_lbl = QLineEdit("-")
        self.live_diff_lbl.setReadOnly(True)
        self.live_diff_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.live_diff_lbl.setFont(create_font(10, QFont.Weight.Bold))
        self.live_diff_lbl.setStyleSheet("QLineEdit { background:transparent; color:#2c3e50; border:1px solid #ccc; border-radius:4px; padding:3px 6px; }")
        self.live_diff_lbl.setFixedWidth(150)

        ctrl.addWidget(lbl_date)
        ctrl.addWidget(self._date_combo)
        ctrl.addSpacing(12)
        ctrl.addWidget(lbl_assets)
        ctrl.addWidget(self._asset_edit)
        ctrl.addSpacing(12)
        ctrl.addWidget(live_asset_title)
        ctrl.addWidget(self.live_asset_lbl)
        ctrl.addSpacing(12)
        ctrl.addWidget(live_diff_title)
        ctrl.addWidget(self.live_diff_lbl)
        ctrl.addSpacing(6)
        ctrl.addWidget(today_btn)
        ctrl.addSpacing(6)
        ctrl.addWidget(add_btn)

        graph_btn = QPushButton("\U0001f4c8  Graph")
        graph_btn.setFont(create_font(10, QFont.Weight.Bold))
        graph_btn.setFixedHeight(32)
        graph_btn.setStyleSheet(
            "QPushButton { background:#8e44ad; color:white; border-radius:4px; padding:4px 14px; font-weight:bold; font-family: 'Malgun Gothic Semilight', '맑은 고딕 Semilight', 'Malgun Gothic'; }"
            "QPushButton:hover { background:#732d91; }"
        )
        graph_btn.clicked.connect(self._show_graph)
        ctrl.addSpacing(6)
        ctrl.addWidget(graph_btn)

        ctrl.addStretch()
        ctrl.addWidget(del_btn)
        root.addLayout(ctrl)

        # ---Table ---
        self._table = QTableWidget()
        self._table.setFont(create_font(9, style_name="Semilight"))
        self._table.setColumnCount(len(self._COLS_SUB))
        
        sections = [
            ("Date", 0, 1, "#444444"),
            ("KOSPI", 1, 3, "#444444"),
            ("Total Assets", 4, 1, "#444444"),
            ("Weekly P/L", 5, 2, "#1a6b3c"),
            ("Cumulative P/L", 7, 2, "#0078d4"),
            ("USD/KRW", 9, 1, "#444444"),
            ("Total Assets($)", 10, 1, "#444444"),
            ("Weekly P/L($)", 11, 2, "#1a6b3c"),
            ("Cumulative P/L($)", 13, 2, "#0078d4"),
        ]
        grouped_hdr = GroupedHeaderView(sections, self._COLS_SUB, self._table, group_h=28, sub_h=0)
        self._table.setHorizontalHeader(grouped_hdr)
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setAlternatingRowColors(True)
        self._table.setSortingEnabled(False)
        widths = [110, 80, 80, 80, 100, 100, 80, 100, 80, 90, 100, 90, 80, 90, 80]
        for col_idx, width in enumerate(widths):
            self._table.horizontalHeader().setSectionResizeMode(col_idx, QHeaderView.ResizeMode.Fixed)
            self._table.setColumnWidth(col_idx, width)
        self._table.setStyleSheet(
            "QTableWidget { gridline-color: #d0d0d0; font-family: 'Malgun Gothic Semilight', '맑은 고딕 Semilight', 'Malgun Gothic'; font-size: 9pt; }"
            "QTableWidget::item { padding: 1px 3px; }"
        )
        self._table.cellDoubleClicked.connect(self._on_cell_double_clicked)
        root.addWidget(self._table)

    # ---Friday date helpers ---
    @staticmethod
    def _friday_dates() -> list[tuple[str, str]]:
        """Return list of (label, iso_date) for every Friday from W01 of the current year to today."""
        today = _dt.date.today()
        year  = today.year
        # First Friday on or after Jan 1 of this year
        jan1  = _dt.date(year, 1, 1)
        days_until_fri = (4 - jan1.weekday()) % 7   # weekday(): Mon=0 - Fri=4
        first_fri = jan1 + _dt.timedelta(days=days_until_fri)

        results = []
        cur = first_fri
        while cur <= today:
            week_num = cur.isocalendar()[1]
            label = f"W{week_num:02d} ({cur.strftime('%Y-%m-%d')})"
            results.append((label, cur.strftime("%Y-%m-%d")))
            cur += _dt.timedelta(weeks=1)
        return results

    def _selected_date(self) -> str:
        """Return the ISO date string for the currently selected combo item."""
        friday_dates = self._friday_dates()
        _, dates = zip(*friday_dates) if friday_dates else ([], [])
        idx = self._date_combo.currentIndex()
        return dates[idx] if 0 <= idx < len(dates) else ""

    def _sync_friday_combo(self):
        """Append any newly available Friday dates to the combo (called daily at midnight)."""
        all_dates = self._friday_dates()
        existing_count = self._date_combo.count()
        if len(all_dates) > existing_count:
            was_at_latest = (self._date_combo.currentIndex() == existing_count - 1)
            for label, _ in all_dates[existing_count:]:
                self._date_combo.addItem(label)
            # Auto-advance only if the user was already on the last item
            if was_at_latest:
                self._date_combo.setCurrentIndex(self._date_combo.count() - 1)
        # Re-schedule for the next midnight
        self._schedule_daily_sync()

    def _schedule_daily_sync(self):
        """Start a one-shot timer that fires 5 s after the next midnight."""
        now = _dt.datetime.now()
        tomorrow_midnight = (now + _dt.timedelta(days=1)).replace(
            hour=0, minute=0, second=5, microsecond=0
        )
        ms = int((tomorrow_midnight - now).total_seconds() * 1000)
        self._sync_timer = QTimer(self)
        self._sync_timer.setSingleShot(True)
        self._sync_timer.timeout.connect(self._sync_friday_combo)
        self._sync_timer.start(ms)

    # ---Format helper ---
    def _fmt_asset_input(self, text: str):
        raw = text.replace(',', '').strip()
        if raw.isdigit() and raw:
            formatted = f"{int(raw):,}"
            if formatted != text:
                pos = self._asset_edit.cursorPosition()
                delta = len(formatted) - len(text)
                self._asset_edit.blockSignals(True)
                self._asset_edit.setText(formatted)
                self._asset_edit.setCursorPosition(max(0, pos + delta))
                self._asset_edit.blockSignals(False)

    # ---JSON load/save ---
    def _load_records(self):
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), self._JSON_FILE)
        try:
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._records = sorted(data, key=lambda r: r["date"])
        except Exception as e:
            print(f"[TradingRecord] Load error: {e}")
        self._refresh_table()

    def _save_records(self):
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), self._JSON_FILE)
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self._records, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"[TradingRecord] Save error: {e}")

    # ---CRUD ---
    def _add_record(self):
        date_str = self._selected_date()
        raw_amt  = self._asset_edit.text().replace(',', '').strip()
        if not date_str:
            QMessageBox.warning(self, "Error", "No Friday date selected.")
            return
        try:
            total = float(raw_amt)
        except ValueError:
            QMessageBox.warning(self, "Error", "Total Assets must be a number.")
            return

        # Update if same date exists, otherwise append
        for r in self._records:
            if r["date"] == date_str:
                r["total"] = total
                break
        else:
            self._records.append({"date": date_str, "total": total})

        self._records.sort(key=lambda r: r["date"])
        self._save_records()
        self._refresh_table()
        self._asset_edit.clear()

    def _delete_selected(self):
        rows = sorted({idx.row() for idx in self._table.selectedIndexes()}, reverse=True)
        if not rows:
            return
        reply = QMessageBox.question(
            self, "Delete", f"Are you sure you want to delete {len(rows)} record(s)?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        for row in rows:
            if 0 <= row < len(self._records):
                self._records.pop(row)
        self._save_records()
        self._refresh_table()

    def _rate_kospi_for_date(self, date_str: str) -> tuple:
        """USD/KRW rate + KOSPI close for a date, cached for the tab's lifetime.

        Both values come from an O(N) polars filter over the full history
        each time they're recomputed, so this cache is shared across
        _refresh_table and _show_graph to avoid redoing that scan for dates
        already looked up (e.g. reopening the graph after a table refresh).

        Past dates never change, so they're cached permanently. Today's date
        is always recomputed: get_usd_krw_rate_for_date/get_index_close_for_date
        delegate to data_fetcher's own today-staleness-aware caches, so this
        just makes sure a newly-published close isn't hidden behind a tuple
        pinned here from earlier in the session (before it was published).
        """
        cache = getattr(self, '_date_metrics_cache', None)
        if cache is None:
            cache = self._date_metrics_cache = {}
        today_str = datetime.now().strftime("%Y-%m-%d")
        if date_str not in cache or date_str == today_str:
            cache[date_str] = (get_usd_krw_rate_for_date(date_str), get_index_close_for_date("KS11", date_str))
        return cache[date_str]

    def _show_graph(self):
        if len(self._records) == 0:
            QMessageBox.information(self, "Graph", "No data to plot.")
            return

        dates = []
        kospi_returns = []
        asset_returns = []
        usd_asset_returns = []
        totals = []
        usd_totals = []

        first_total = None
        first_kospi = None
        first_usd_total = None

        for rec in self._records:
            date_str = rec["date"]
            total = rec["total"]

            usd_rate, kospi = self._rate_kospi_for_date(date_str)

            usd_val = total / usd_rate if usd_rate > 0 else 0
            
            if first_total is None:
                first_total = total
                first_kospi = kospi
                first_usd_total = usd_val
                
            dates.append(date_str)
            
            # KOSPI return
            k_pct = ((kospi - first_kospi) / first_kospi * 100) if first_kospi and first_kospi > 0 and kospi > 0 else 0.0
            kospi_returns.append(k_pct)
            
            # Asset return
            a_pct = ((total - first_total) / first_total * 100) if first_total and first_total > 0 else 0.0
            asset_returns.append(a_pct)
            
            # USD Asset return
            u_pct = ((usd_val - first_usd_total) / first_usd_total * 100) if first_usd_total and first_usd_total > 0 else 0.0
            usd_asset_returns.append(u_pct)
            
            totals.append(total)
            usd_totals.append(usd_val)
            
        dlg = TotalAssetsGraphDialog(dates, kospi_returns, asset_returns, usd_asset_returns, totals, usd_totals, self)
        dlg.exec()

    def _on_cell_double_clicked(self, row, col):
        if col == 1:
            current_val = self._records[row].get("total", 0)
            text, ok = QInputDialog.getText(self, "Edit Total Assets", "Enter new Total Assets amount:", text=f"{current_val:,.0f}")
            if ok:
                try:
                    val = float(text.replace(',', '').strip())
                    self._records[row]["total"] = val
                    self._save_records()
                    self._refresh_table()
                except ValueError:
                    QMessageBox.warning(self, "Error", "Invalid number format.")

    # ---Table rendering ---
    @staticmethod
    def _pct_item(val: float | None) -> QTableWidgetItem:
        if val is None:
            it = QTableWidgetItem("-")
            it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            return it
        it = QTableWidgetItem(f"{val:+.2f}%")
        it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if val > 0:
            it.setForeground(QColor("#c0392b"))
        elif val < 0:
            it.setForeground(QColor("#2980b9"))
        return it

    @staticmethod
    def _amt_item(val: float | None) -> QTableWidgetItem:
        if val is None:
            it = QTableWidgetItem("-")
            it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            return it
        it = QTableWidgetItem(f"{val:+,.0f}")
        it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if val > 0:
            it.setForeground(QColor("#c0392b"))
        elif val < 0:
            it.setForeground(QColor("#2980b9"))
        return it

    @staticmethod
    def _amt_usd_item(val: float | None) -> QTableWidgetItem:
        if val is None:
            it = QTableWidgetItem("-")
            it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            return it
        sign = "+" if val > 0 else "-" if val < 0 else ""
        it = QTableWidgetItem(f"{sign}${abs(val):,.0f}")
        it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if val > 0:
            it.setForeground(QColor("#c0392b"))
        elif val < 0:
            it.setForeground(QColor("#2980b9"))
        return it

    def _refresh_table(self):
        tbl = self._table
        tbl.setUpdatesEnabled(False)  # UI Batch Repaint Optimization
        try:
            self._refresh_table_impl()
        finally:
            tbl.setUpdatesEnabled(True)

    def _refresh_table_impl(self):
        self._table.setRowCount(0)
        records = self._records
        n = len(records)
        self._table.setRowCount(n)

        first_total = records[0]["total"] if n > 0 else None

        # Pre-compute all USD/KRW rates and index prices in one pass
        rate_cache: dict = {}
        kospi_cache: dict = {}
        for rec in records:
            d = rec["date"]
            if d not in rate_cache:
                rate_cache[d], kospi_cache[d] = self._rate_kospi_for_date(d)

        first_usd_total = None
        first_kospi = 0.0
        if n > 0:
            r0 = rate_cache.get(records[0]["date"], 0)
            first_usd_total = records[0]["total"] / r0 if r0 > 0 else 0
            first_kospi = kospi_cache.get(records[0]["date"], 0)

        # Pre-parse each record's date once (was parsed separately for the
        # weekly KRW/USD block and again for the weekly KOSPI block below).
        # A malformed date yields None so that record is simply treated as
        # non-weekly rather than aborting the whole refresh.
        def _safe_parse_date(d):
            try:
                return _dt.datetime.strptime(d, "%Y-%m-%d").date()
            except Exception:
                return None
        parsed_dates = [_safe_parse_date(rec["date"]) for rec in records]

        for i, rec in enumerate(records):
            date_str  = rec["date"]
            total     = rec["total"]

            usd_rate = rate_cache.get(date_str, 0)
            usd_val = total / usd_rate if usd_rate > 0 else 0

            # Weekly: compare to the immediately preceding record if it is within 7 calendar days.
            # Records are always sorted ascending by date, so i-1 is the only candidate (O(N) total).
            weekly_pct = None; weekly_amt = None
            weekly_usd_amt = None; weekly_usd_pct = None
            is_weekly = False
            try:
                if i > 0 and parsed_dates[i] is not None and parsed_dates[i - 1] is not None:
                    prev_rec = records[i - 1]
                    is_weekly = (parsed_dates[i] - parsed_dates[i - 1]).days <= 7
                    if is_weekly:
                        prev     = prev_rec["total"]
                        r_prev   = rate_cache.get(prev_rec["date"], 0)
                        prev_usd = prev / r_prev if r_prev > 0 else 0

                        weekly_amt = total - prev
                        if prev:
                            weekly_pct = weekly_amt / prev * 100

                        weekly_usd_amt = usd_val - prev_usd
                        if prev_usd:
                            weekly_usd_pct = weekly_usd_amt / prev_usd * 100
            except Exception:
                logger.debug("Weekly change calculation failed at row index=%d", i, exc_info=True)

            # Cumulative: relative to the very first record
            cumulative_pct = None; cumulative_amt = None
            if first_total is not None and i > 0:
                cumulative_amt = total - first_total
            if first_total and first_total != 0 and i > 0:
                cumulative_pct = cumulative_amt / first_total * 100

            cumulative_usd_pct = None; cumulative_usd_amt = None
            if first_usd_total is not None and i > 0:
                cumulative_usd_amt = usd_val - first_usd_total
            if first_usd_total and first_usd_total != 0 and i > 0:
                cumulative_usd_pct = cumulative_usd_amt / first_usd_total * 100

            # Date cell
            d_it = QTableWidgetItem(date_str)
            d_it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setItem(i, 0, d_it)

            # KOSPI cell
            k_val = kospi_cache.get(date_str, 0.0)
            k_it = QTableWidgetItem(f"{k_val:,.0f}" if k_val > 0 else "-")
            k_it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self._table.setItem(i, 1, k_it)
            
            k_weekly_pct = None
            if i > 0 and is_weekly:
                prev_k = kospi_cache.get(records[i - 1]["date"], 0)
                if prev_k > 0 and k_val > 0:
                    k_weekly_pct = (k_val - prev_k) / prev_k * 100
            self._table.setItem(i, 2, self._pct_item(k_weekly_pct))
            
            k_pct = ((k_val - first_kospi) / first_kospi * 100) if first_kospi > 0 and k_val > 0 else None
            self._table.setItem(i, 3, self._pct_item(k_pct))

            # Total Assets cell (KRW)
            t_it = QTableWidgetItem(f"{total:,.0f}")
            t_it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self._table.setItem(i, 4, t_it)

            # Weekly / Cumulative (KRW)
            self._table.setItem(i, 5, self._amt_item(weekly_amt))
            self._table.setItem(i, 6, self._pct_item(weekly_pct))
            self._table.setItem(i, 7, self._amt_item(cumulative_amt))
            self._table.setItem(i, 8, self._pct_item(cumulative_pct))
            
            # USD/KRW Rate cell
            r_it = QTableWidgetItem(f"{usd_rate:,.1f}" if usd_rate > 0 else "-")
            r_it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self._table.setItem(i, 9, r_it)

            # Total Assets ($) cell
            u_it = QTableWidgetItem(f"$ {usd_val:,.0f}" if usd_val > 0 else "-")
            u_it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self._table.setItem(i, 10, u_it)

            # Weekly / Cumulative ($)
            self._table.setItem(i, 11, self._amt_usd_item(weekly_usd_amt))
            self._table.setItem(i, 12, self._pct_item(weekly_usd_pct))
            self._table.setItem(i, 13, self._amt_usd_item(cumulative_usd_amt))
            self._table.setItem(i, 14, self._pct_item(cumulative_usd_pct))

        self._update_live_asset_labels()

    def update_live_asset(self, current_total: float):
        self._current_live_asset = current_total
        self._update_live_asset_labels()

    def _update_live_asset_labels(self):
        if not hasattr(self, 'live_asset_lbl'): return
        
        curr_val = getattr(self, '_current_live_asset', 0.0)
        if curr_val > 0:
            self.live_asset_lbl.setText(f"{curr_val:,.0f}")
        else:
            self.live_asset_lbl.setText("-")
            
        if curr_val > 0 and self._records:
            last_record = self._records[-1]
            last_total = last_record.get('total', 0.0)
            if last_total > 0:
                diff = curr_val - last_total
                ratio = (diff / last_total) * 100
                color = "#c0392b" if diff > 0 else ("#2980b9" if diff < 0 else "#2c3e50")
                sign = "+" if diff > 0 else ""
                self.live_diff_lbl.setText(f"{sign}{diff:,.0f} ({sign}{ratio:.2f}%)")
                self.live_diff_lbl.setStyleSheet(f"QLineEdit {{ background:transparent; color: {color}; font-weight: bold; border:1px solid #ccc; border-radius:4px; padding:3px 6px; }}")
            else:
                self.live_diff_lbl.setText("-")
                self.live_diff_lbl.setStyleSheet("QLineEdit { background:transparent; color: #2c3e50; font-weight: bold; border:1px solid #ccc; border-radius:4px; padding:3px 6px; }")
        else:
            self.live_diff_lbl.setText("-")
            self.live_diff_lbl.setStyleSheet("QLineEdit { background:transparent; color: #2c3e50; font-weight: bold; border:1px solid #ccc; border-radius:4px; padding:3px 6px; }")
