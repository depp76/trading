"""ui/assets_tab.py — TradingRecordTab (Phase 4 split)

Split out from: main.py (2026-08-29 feat/3-1-modularize, Phase 4)
Contains:
  TradingRecordTab
"""
import csv
import logging
import datetime as _dt
from datetime import datetime

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem,
    QLineEdit, QPushButton, QLabel, QHeaderView, QComboBox, QMessageBox,
    QInputDialog, QFileDialog,
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QFont

import matplotlib.dates as mdates
from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas

from paths import TRADING_RECORD_FILE
from data_fetcher import get_usd_krw_rate, get_usd_krw_rate_for_date, get_index_close_for_date
from threads.fetch_threads import AssetMetricsPreloadThread
from ui.widgets import GroupedHeaderView, ColSpec, NumericItem
from ui.dialogs import TotalAssetsGraphDialog
from ui.colors import PROFIT, LOSS
from ui.theme import ACCENT, TEXT, TEXT_SUB, TEXT_MUTED

logger = logging.getLogger(__name__)


class _AssetsTable(QTableWidget):
    """QTableWidget subclass so resize/show can drive a column-stretch
    callback directly (docs/ui.md 1.5), the same pattern StockTable uses --
    more reliable than a QEvent.Resize event filter for catching every
    layout-driven resize (e.g. the initial layout pass before the window is
    shown maximized)."""

    def __init__(self, stretch_cb):
        super().__init__()
        self._stretch_cb = stretch_cb

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._stretch_cb()

    def showEvent(self, event):
        super().showEvent(event)
        self._stretch_cb()


from ui.common import (
    create_font, atomic_save_json, safe_load_json, FONT_FAMILY_CSS, ThreadOwnerMixin,
)


class TradingRecordTab(ThreadOwnerMixin, QWidget):
    """Tab for recording periodic total-asset snapshots with weekly/cumulative return calculations."""

    _JSON_FILE = TRADING_RECORD_FILE

    # docs/ui.md 4.2/4.3: KRW-7-col + USD-6-col = 15 columns collapsed to a
    # single currency toggle + 10 columns (the one place label/min-width/
    # weight live -- see _stretch_columns). Total Assets' own sub-label
    # ("KRW"/"USD") is relabeled live by _apply_currency_toggle(); the rest
    # are fixed.
    _GROUPS = [
        ("Date", 0, 1, TEXT_SUB),
        ("KOSPI", 1, 3, TEXT_SUB),
        ("Total Assets", 4, 1, TEXT_SUB),
        ("Weekly P/L", 5, 2, "#1a6b3c"),
        ("Cumulative P/L", 7, 2, "#0078d4"),
        ("vs KOSPI", 9, 1, "#6d28d9"),
    ]
    # Sub-labels are English to match the rest of the app's UI (docs/ui.md's
    # own table uses Korean sub-labels -- 종가/주간 %/누적 % -- but every
    # other tab's column header is English, so these are the same grouping
    # translated to stay consistent rather than the literal doc text).
    _COLUMNS = [
        ColSpec("date",       "Week · Date", 140, 1.3, None, None),
        ColSpec("kospi",      "Close",             80, 0.8, None, None),
        ColSpec("kospi_wk",   "Weekly %",          70, 0.7, None, None),
        ColSpec("kospi_cum",  "Cumulative %",      80, 0.8, None, None),
        ColSpec("total",      "KRW",              110, 1.1, None, None),
        ColSpec("weekly_amt", "Amount",           100, 1.0, None, None),
        ColSpec("weekly_pct", "%",                 70, 0.7, None, None),
        ColSpec("cum_amt",    "Amount",           100, 1.0, None, None),
        ColSpec("cum_pct",    "%",                 70, 0.7, None, None),
        ColSpec("excess",     "vs KOSPI (%p)",     90, 0.9, None, None),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._records: list[dict] = []   # [{"date": str, "total": float, "manual": bool}, ...]
        # USD/KRW rate and KOSPI-close lookups hit the network on their first
        # call per session (fx.py/market.py's own staleness-aware caches), so
        # the first table render skips them (see _refresh_table_impl) and
        # AssetMetricsPreloadThread warms those caches in the background
        # instead of blocking __init__ (roadmap 2026-09-18, review.md 1-2).
        self._metrics_ready = False
        self._metrics_thread = None
        self._currency = "KRW"          # docs/ui.md 4.3 toggle
        self._current_live_asset = 0.0
        self._asset_edit_manual = False  # user typed over the auto-filled value (docs/ui.md 4.5)
        self._build_ui()
        self._load_records()
        self._schedule_daily_sync()
        self._start_metrics_preload()

    # ---UI ---
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(8)
        root.setContentsMargins(12, 10, 12, 10)

        # ---Header row: currency toggle + USD/KRW rate (docs/ui.md 4.1/4.3) ---
        header_row = QHBoxLayout()
        lbl_currency = QLabel("Currency:")
        lbl_currency.setFont(create_font(10, style_name="Semilight"))
        header_row.addWidget(lbl_currency)

        self._currency_combo = QComboBox()
        self._currency_combo.setFont(create_font(10, style_name="Semilight"))
        self._currency_combo.addItems(["KRW", "USD"])
        self._currency_combo.setFixedWidth(90)
        self._currency_combo.currentTextChanged.connect(self._on_currency_changed)
        header_row.addWidget(self._currency_combo)

        header_row.addSpacing(12)
        self._rate_lbl = QLabel("USD/KRW: -")
        self._rate_lbl.setFont(create_font(10, style_name="Semilight"))
        self._rate_lbl.setStyleSheet(f"color: {TEXT_MUTED};")
        header_row.addWidget(self._rate_lbl)
        header_row.addStretch()
        root.addLayout(header_row)

        # ---Inline chart (docs/ui.md 4.4): always-visible weekly trend,
        # asset vs KOSPI rebased to first week = 100. The _show_graph modal
        # stays available for a zoomed/detailed view. ---
        self._chart_fig = Figure(figsize=(6, 2), constrained_layout=True)
        self._chart_ax = self._chart_fig.add_subplot(111)
        self._chart_canvas = FigureCanvas(self._chart_fig)
        self._chart_canvas.setFixedHeight(200)
        root.addWidget(self._chart_canvas)

        # ---Controls bar ---
        ctrl = QHBoxLayout()

        self._date_combo = QComboBox()
        self._date_combo.setFont(create_font(10, style_name="Semilight"))
        self._date_combo.setFixedWidth(150)
        for label, _ in self._friday_dates():
            self._date_combo.addItem(label)
        # Default to the most recent (last) Friday
        if self._date_combo.count() > 0:
            self._date_combo.setCurrentIndex(self._date_combo.count() - 1)
        self._date_combo.currentIndexChanged.connect(self._on_date_combo_changed)

        self._asset_edit = QLineEdit()
        self._asset_edit.setFont(create_font(10, style_name="Semilight"))
        self._asset_edit.setPlaceholderText("Total Assets (auto-filled)")
        self._asset_edit.setFixedWidth(120)
        self._asset_edit.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._asset_edit.textEdited.connect(self._fmt_asset_input)
        self._asset_edit.textEdited.connect(self._on_asset_edit_user_typed)

        # Add Record is this tab's one primary action (docs/ui.md 1.6);
        # Delete is destructive ("danger"); This Week/Graph/Export are
        # secondary utilities and stay neutral.
        add_btn = QPushButton("\u2795  Add Record")
        add_btn.setObjectName("primary")
        add_btn.setFont(create_font(10, QFont.Weight.Bold))
        add_btn.setFixedHeight(32)
        add_btn.clicked.connect(self._add_record)

        del_btn = QPushButton("\U0001f5d1  Delete Selected")
        del_btn.setObjectName("danger")
        del_btn.setFont(create_font(10, QFont.Weight.Bold))
        del_btn.setFixedHeight(32)
        del_btn.clicked.connect(self._delete_selected)

        today_btn = QPushButton("\U0001f4c5  This Week")
        today_btn.setFont(create_font(10, QFont.Weight.Bold))
        today_btn.setFixedHeight(32)
        def _select_latest():
            self._date_combo.setCurrentIndex(self._date_combo.count() - 1)
        today_btn.clicked.connect(_select_latest)

        lbl_date = QLabel("Date:")
        lbl_date.setFont(create_font(10, style_name="Semilight"))
        lbl_assets = QLabel("Total Assets:")
        lbl_assets.setFont(create_font(10, style_name="Semilight"))

        live_asset_title = QLabel("Current Total Asset:")
        live_asset_title.setFont(create_font(10, style_name="Semilight"))

        # docs/ui.md issue #7 (mockup): these were QLineEdit(readOnly), which
        # look editable (bordered, input-shaped) despite never accepting
        # input -- plain KPI-style labels now, matching History's KPI strip.
        self.live_asset_lbl = QLabel("-")
        self.live_asset_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.live_asset_lbl.setFont(create_font(10, QFont.Weight.Bold))
        self.live_asset_lbl.setStyleSheet(f"color:{TEXT};")
        self.live_asset_lbl.setFixedWidth(120)

        live_diff_title = QLabel("Weekly P/L:")
        live_diff_title.setFont(create_font(10, style_name="Semilight"))

        self.live_diff_lbl = QLabel("-")
        self.live_diff_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.live_diff_lbl.setFont(create_font(10, QFont.Weight.Bold))
        self.live_diff_lbl.setStyleSheet(f"color:{TEXT};")
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
        graph_btn.clicked.connect(self._show_graph)
        ctrl.addSpacing(6)
        ctrl.addWidget(graph_btn)

        export_btn = QPushButton("\U0001f4e5  Export")
        export_btn.setFont(create_font(10, QFont.Weight.Bold))
        export_btn.setFixedHeight(32)
        export_btn.setToolTip("Export the asset snapshot table to Excel or CSV")
        export_btn.clicked.connect(self._on_export_clicked)
        ctrl.addSpacing(6)
        ctrl.addWidget(export_btn)

        ctrl.addStretch()
        ctrl.addWidget(del_btn)
        root.addLayout(ctrl)

        # ---Table (docs/ui.md 4.2/4.5: one spec -- label/min-width/weight --
        # instead of a separate _COLS_SUB list and a flat widths=[...] list) ---
        self._table = _AssetsTable(self._stretch_columns)
        self._table.setFont(create_font(9, style_name="Semilight"))
        self._table.setColumnCount(len(self._COLUMNS))

        self._grouped_hdr = GroupedHeaderView(
            self._GROUPS, self._current_sub_labels(), self._table, group_h=22, sub_h=18, sortable=True,
        )
        self._table.setHorizontalHeader(self._grouped_hdr)
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setAlternatingRowColors(True)
        # docs/ui.md issue #9 (mockup): "정렬이 꺼져 있다" -- every column is
        # sortable now (a superset of the doc's minimum ask, Weekly P/L and
        # vs KOSPI). Default stays chronological (most recent first, matching
        # Trading History's own default) -- pinned explicitly rather than
        # relying on QHeaderView's own default indicator state, since
        # setSortingEnabled(True) immediately applies whatever that is.
        self._grouped_hdr.setSortIndicator(0, Qt.SortOrder.DescendingOrder)
        self._table.setSortingEnabled(True)
        for col_idx, spec in enumerate(self._COLUMNS):
            self._table.horizontalHeader().setSectionResizeMode(col_idx, QHeaderView.ResizeMode.Interactive)
            self._table.setColumnWidth(col_idx, spec.min_width)
        self._table.setStyleSheet(
            "QTableWidget { gridline-color: #d0d0d0; " + FONT_FAMILY_CSS + " font-size: 9pt; }"
            "QTableWidget::item { padding: 1px 3px; }"
        )
        self._table.cellDoubleClicked.connect(self._on_cell_double_clicked)
        root.addWidget(self._table)

    def _stretch_columns(self):
        """Distributes column widths from _COLUMNS (min-width + weight),
        the same system Universe's StockTable uses (docs/ui.md 1.5, Phase 3)
        -- replaces the flat widths=[...] list this table used to hardcode."""
        if self._table.rowCount() == 0:
            return
        vp_w = int(self._table.viewport().width() * 0.99)
        if vp_w <= 0:
            return
        total_weight = sum(c.weight for c in self._COLUMNS)
        unit = vp_w / total_weight
        widths = {i: max(c.min_width, int(c.weight * unit)) for i, c in enumerate(self._COLUMNS)}
        # Rounding residual - absorb into the Date column (index 0) using
        # exact arithmetic, same rationale as StockTable._stretch_columns.
        residual = vp_w - sum(widths.values())
        widths[0] = max(self._COLUMNS[0].min_width, widths[0] + residual)
        if getattr(self, '_last_col_widths', None) == widths:
            return
        self._last_col_widths = widths
        for col, w in widths.items():
            self._table.setColumnWidth(col, w)

    def _current_sub_labels(self) -> list:
        labels = [c.label for c in self._COLUMNS]
        labels[4] = self._currency  # "Total Assets" column: "KRW" or "USD"
        return labels

    def _on_currency_changed(self, text: str):
        self._currency = text
        self._grouped_hdr.set_sub_labels(self._current_sub_labels())
        self._refresh_table()  # also redraws the inline chart for the new currency

    def _update_rate_label(self, rate: float):
        self._rate_lbl.setText(f"USD/KRW: {rate:,.1f}" if rate > 0 else "USD/KRW: -")

    def _on_date_combo_changed(self, _index):
        self._asset_edit_manual = False
        self._maybe_autofill_asset_edit()

    def _on_asset_edit_user_typed(self, _text):
        self._asset_edit_manual = True

    def _maybe_autofill_asset_edit(self):
        """Auto-fill Total Assets from the live position total (docs/ui.md
        4.5) -- only for the latest (current) Friday, and only while the
        user hasn't typed over it; a historical date or a user-edited value
        is left alone."""
        is_latest = (
            self._date_combo.count() > 0
            and self._date_combo.currentIndex() == self._date_combo.count() - 1
        )
        if not is_latest or self._asset_edit_manual:
            return
        if self._current_live_asset > 0:
            self._asset_edit.setText(f"{self._current_live_asset:,.0f}")

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
        data = safe_load_json(self._JSON_FILE, default=[])
        if data:
            self._records = sorted(data, key=lambda r: r.get("date", ""))
        self._refresh_table()

    def _save_records(self):
        try:
            atomic_save_json(self._JSON_FILE, self._records, indent=2)
        except Exception as e:
            logger.warning("[TradingRecord] Save error: %s", e, exc_info=True)

    # ---Background metrics warm-up (roadmap 2026-09-18, review.md 1-2) ---
    def _start_metrics_preload(self):
        thread = self._track_thread(AssetMetricsPreloadThread(), '_metrics_thread')
        thread.finished.connect(self._on_metrics_preloaded)
        thread.start()

    def _on_metrics_preloaded(self):
        self._metrics_ready = True
        self._refresh_table()

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

        # docs/ui.md 4.5: the value was auto-filled from the live position
        # total unless the user typed over it (or there was nothing to
        # auto-fill, e.g. a historical date) -- flag it for later comparison.
        is_manual = self._asset_edit_manual

        # Update if same date exists, otherwise append
        for r in self._records:
            if r["date"] == date_str:
                r["total"] = total
                r["manual"] = is_manual
                break
        else:
            self._records.append({"date": date_str, "total": total, "manual": is_manual})

        self._records.sort(key=lambda r: r["date"])
        self._save_records()
        self._refresh_table()
        self._asset_edit.clear()
        self._asset_edit_manual = False

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
        # Same guard as _refresh_table_impl: until AssetMetricsPreloadThread
        # has warmed the USD/KRW and KOSPI caches, _rate_kospi_for_date()
        # would do its first-call network fetch right here on the UI thread.
        if not self._metrics_ready:
            QMessageBox.information(
                self, "Graph", "USD/KRW and KOSPI history are still loading — try again in a moment.",
            )
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

    # ---Export (review.md 2-2) ---
    # Always the full KRW+USD dump regardless of the on-screen currency
    # toggle (docs/ui.md 4.3): export is a complete data backup/analysis
    # artifact, not a screenshot of the current view. Built from
    # _compute_records_metrics() directly rather than scraping self._table,
    # since the table now only ever shows one currency's 10 columns.
    _EXPORT_HEADERS = [
        "Date", "KOSPI", "KOSPI Weekly %", "KOSPI Cumulative %",
        "Total Assets", "Weekly P/L", "Weekly P/L %", "Cumulative P/L", "Cumulative P/L %",
        "USD/KRW", "Total Assets ($)", "Weekly P/L ($)", "Weekly P/L (%) [$]",
        "Cumulative P/L ($)", "Cumulative P/L (%) [$]",
        "Excess Return (%p)", "Excess Return (%p) [$]", "Manual",
    ]

    @staticmethod
    def _export_fmt(val, fmt="{:,.0f}") -> str:
        return "" if val is None else fmt.format(val)

    def _on_export_clicked(self):
        metrics = self._compute_records_metrics()
        if not metrics:
            QMessageBox.information(self, "Export", "No data to export.")
            return

        default_name = f"total_assets_{datetime.now().strftime('%Y%m%d')}.xlsx"
        path, selected_filter = QFileDialog.getSaveFileName(
            self, "Export Total Assets", default_name,
            "Excel Workbook (*.xlsx);;CSV File (*.csv)",
        )
        if not path:
            return

        want_csv = "csv" in selected_filter.lower() or path.lower().endswith(".csv")
        if want_csv and not path.lower().endswith(".csv"):
            path += ".csv"
        elif not want_csv and not path.lower().endswith(".xlsx"):
            path += ".xlsx"

        f = self._export_fmt
        rows = []
        for m in metrics:
            rows.append([
                m["date"],
                f(m["kospi_close"]) if m["kospi_close"] else "",
                f(m["kospi_weekly_pct"], "{:+.2f}%"),
                f(m["kospi_cum_pct"], "{:+.2f}%"),
                f(m["total_krw"]),
                f(m["weekly_amt_krw"], "{:+,.0f}"),
                f(m["weekly_pct_krw"], "{:+.2f}%"),
                f(m["cum_amt_krw"], "{:+,.0f}"),
                f(m["cum_pct_krw"], "{:+.2f}%"),
                f(m["usd_rate"], "{:,.1f}") if m["usd_rate"] else "",
                f(m["total_usd"]) if m["total_usd"] else "",
                f(m["weekly_amt_usd"], "{:+,.0f}"),
                f(m["weekly_pct_usd"], "{:+.2f}%"),
                f(m["cum_amt_usd"], "{:+,.0f}"),
                f(m["cum_pct_usd"], "{:+.2f}%"),
                f(m["excess_pct_krw"], "{:+.2f}%"),
                f(m["excess_pct_usd"], "{:+.2f}%"),
                "Yes" if m["manual"] else "",
            ])

        try:
            if want_csv:
                with open(path, "w", newline="", encoding="utf-8-sig") as f:
                    writer = csv.writer(f)
                    writer.writerow(self._EXPORT_HEADERS)
                    writer.writerows(rows)
            else:
                from openpyxl import Workbook
                wb = Workbook()
                ws = wb.active
                ws.title = "Total Assets"
                ws.append(self._EXPORT_HEADERS)
                for row in rows:
                    ws.append(row)
                wb.save(path)
        except Exception as e:
            logger.warning("Total assets export failed: %s", e, exc_info=True)
            QMessageBox.warning(self, "Export Error", f"Failed to export:\n{e}")
            return

        QMessageBox.information(self, "Export", f"Exported {len(rows)} row(s) to {path}")

    def _on_cell_double_clicked(self, row, col):
        # col 4 is "Total Assets" (docs/ui.md 4.2) in both the old 15-column
        # and new 10-column layout; this previously checked col == 1
        # (KOSPI's close-price column), a stale index from before this
        # table had separate KOSPI weekly/cumulative columns in between.
        if col == 4:
            current_val = self._records[row].get("total", 0)
            text, ok = QInputDialog.getText(self, "Edit Total Assets", "Enter new Total Assets amount:", text=f"{current_val:,.0f}")
            if ok:
                try:
                    val = float(text.replace(',', '').strip())
                    self._records[row]["total"] = val
                    # A direct table edit is as "manual" as typing over the
                    # auto-filled input (docs/ui.md 4.5).
                    self._records[row]["manual"] = True
                    self._save_records()
                    self._refresh_table()
                except ValueError:
                    QMessageBox.warning(self, "Error", "Invalid number format.")

    # ---Table rendering ---
    # docs/ui.md issue #9 (mockup): "정렬이 꺼져 있다" -- sorting is enabled
    # below now, so every numeric cell needs a real sort key independent of
    # its display text. Plain QTableWidgetItem(text) has none (Qt falls back
    # to comparing the display string), which is exactly the bug
    # ui.widgets.NumericItem exists to avoid -- see its docstring.
    @staticmethod
    def _pct_item(val: float | None) -> QTableWidgetItem:
        if val is None:
            it = NumericItem("-", float('-inf'))
            it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            return it
        it = NumericItem(f"{val:+.2f}%", val)
        it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if val > 0:
            it.setForeground(QColor(PROFIT))
        elif val < 0:
            it.setForeground(QColor(LOSS))
        return it

    @staticmethod
    def _amt_item(val: float | None) -> QTableWidgetItem:
        if val is None:
            it = NumericItem("-", float('-inf'))
            it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            return it
        it = NumericItem(f"{val:+,.0f}", val)
        it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if val > 0:
            it.setForeground(QColor(PROFIT))
        elif val < 0:
            it.setForeground(QColor(LOSS))
        return it

    @staticmethod
    def _amt_usd_item(val: float | None) -> QTableWidgetItem:
        if val is None:
            it = NumericItem("-", float('-inf'))
            it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            return it
        sign = "+" if val > 0 else "-" if val < 0 else ""
        it = NumericItem(f"{sign}${abs(val):,.0f}", val)
        it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if val > 0:
            it.setForeground(QColor(PROFIT))
        elif val < 0:
            it.setForeground(QColor(LOSS))
        return it

    def _refresh_table(self):
        tbl = self._table
        tbl.setUpdatesEnabled(False)  # UI Batch Repaint Optimization
        try:
            self._refresh_table_impl()
        finally:
            tbl.setUpdatesEnabled(True)

    def _compute_records_metrics(self) -> list:
        """One structured dict per record with every KRW/USD/KOSPI metric
        this tab displays (docs/ui.md 4) -- shared by table rendering
        (which shows a currency-aware subset), export (full KRW+USD dump)
        and the inline chart, so the underlying math lives in exactly one
        place instead of being duplicated per consumer.
        """
        records = self._records
        n = len(records)
        if n == 0:
            return []

        first_total = records[0]["total"]

        # Pre-compute all USD/KRW rates and index prices in one pass.
        # Skipped until the background preload thread warms the underlying
        # caches (self._metrics_ready) so this first render never blocks the
        # UI thread on a network fetch; rows just show "-" until it lands and
        # _on_metrics_preloaded() re-runs this with real values.
        rate_cache: dict = {}
        kospi_cache: dict = {}
        if self._metrics_ready:
            for rec in records:
                d = rec["date"]
                if d not in rate_cache:
                    rate_cache[d], kospi_cache[d] = self._rate_kospi_for_date(d)

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

        out = []
        for i, rec in enumerate(records):
            date_str = rec["date"]
            total    = rec["total"]

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
            if i > 0:
                cumulative_amt = total - first_total
                if first_total:
                    cumulative_pct = cumulative_amt / first_total * 100

            cumulative_usd_pct = None; cumulative_usd_amt = None
            if i > 0:
                cumulative_usd_amt = usd_val - first_usd_total
                if first_usd_total:
                    cumulative_usd_pct = cumulative_usd_amt / first_usd_total * 100

            k_val = kospi_cache.get(date_str, 0.0)
            k_weekly_pct = None
            if i > 0 and is_weekly:
                prev_k = kospi_cache.get(records[i - 1]["date"], 0)
                if prev_k > 0 and k_val > 0:
                    k_weekly_pct = (k_val - prev_k) / prev_k * 100
            k_cum_pct = ((k_val - first_kospi) / first_kospi * 100) if first_kospi > 0 and k_val > 0 else None

            # docs/ui.md 4.2 "vs KOSPI": the asset's own cumulative return
            # minus KOSPI's, per currency. None wherever either side is None
            # (row 0, or KOSPI/rate data not warmed yet).
            excess_krw = (cumulative_pct - k_cum_pct) if cumulative_pct is not None and k_cum_pct is not None else None
            excess_usd = (cumulative_usd_pct - k_cum_pct) if cumulative_usd_pct is not None and k_cum_pct is not None else None

            week_label = f"W{parsed_dates[i].isocalendar()[1]:02d}" if parsed_dates[i] is not None else ""

            out.append({
                "date": date_str, "week_label": week_label,
                "kospi_close": k_val, "kospi_weekly_pct": k_weekly_pct, "kospi_cum_pct": k_cum_pct,
                "total_krw": total, "weekly_amt_krw": weekly_amt, "weekly_pct_krw": weekly_pct,
                "cum_amt_krw": cumulative_amt, "cum_pct_krw": cumulative_pct,
                "usd_rate": usd_rate, "total_usd": usd_val,
                "weekly_amt_usd": weekly_usd_amt, "weekly_pct_usd": weekly_usd_pct,
                "cum_amt_usd": cumulative_usd_amt, "cum_pct_usd": cumulative_usd_pct,
                "excess_pct_krw": excess_krw, "excess_pct_usd": excess_usd,
                "manual": bool(rec.get("manual", False)),
            })
        return out

    def _refresh_table_impl(self):
        metrics = self._compute_records_metrics()
        # docs/ui.md issue #9: sorting is enabled on this table now, so the
        # full rebuild below must not run while Qt could auto-resort
        # mid-loop (the same class of bug Phase 2's fix addressed for
        # Universe -- setItem(i, ...) writing into row i only means what we
        # think it means while sorting is off).
        self._table.setSortingEnabled(False)
        self._table.setRowCount(0)
        self._table.setRowCount(len(metrics))

        usd = (self._currency == "USD")
        for i, m in enumerate(metrics):
            # Date cell: week badge + date together (docs/ui.md 4.6)
            label = f"{m['week_label']} · {m['date']}" if m['week_label'] else m['date']
            d_it = QTableWidgetItem(label)
            d_it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if m["manual"]:
                d_it.setToolTip("Manually entered/edited")
            self._table.setItem(i, 0, d_it)

            k_it = NumericItem(f"{m['kospi_close']:,.0f}" if m['kospi_close'] > 0 else "-", m['kospi_close'])
            k_it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self._table.setItem(i, 1, k_it)
            self._table.setItem(i, 2, self._pct_item(m["kospi_weekly_pct"]))
            self._table.setItem(i, 3, self._pct_item(m["kospi_cum_pct"]))

            if usd:
                total_val = m["total_usd"]
                total_it = NumericItem(f"$ {total_val:,.0f}" if total_val > 0 else "-", total_val)
                weekly_amt_it = self._amt_usd_item(m["weekly_amt_usd"])
                weekly_pct_it = self._pct_item(m["weekly_pct_usd"])
                cum_amt_it    = self._amt_usd_item(m["cum_amt_usd"])
                cum_pct_it    = self._pct_item(m["cum_pct_usd"])
                excess_it     = self._pct_item(m["excess_pct_usd"])
            else:
                total_val = m["total_krw"]
                total_it = NumericItem(f"{total_val:,.0f}", total_val)
                weekly_amt_it = self._amt_item(m["weekly_amt_krw"])
                weekly_pct_it = self._pct_item(m["weekly_pct_krw"])
                cum_amt_it    = self._amt_item(m["cum_amt_krw"])
                cum_pct_it    = self._pct_item(m["cum_pct_krw"])
                excess_it     = self._pct_item(m["excess_pct_krw"])
            total_it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self._table.setItem(i, 4, total_it)
            self._table.setItem(i, 5, weekly_amt_it)
            self._table.setItem(i, 6, weekly_pct_it)
            self._table.setItem(i, 7, cum_amt_it)
            self._table.setItem(i, 8, cum_pct_it)
            self._table.setItem(i, 9, excess_it)

        self._table.setSortingEnabled(True)

        if self._metrics_ready:
            try:
                self._update_rate_label(get_usd_krw_rate())
            except Exception:
                self._update_rate_label(0.0)

        self._stretch_columns()
        self._update_live_asset_labels()
        self._update_chart(metrics)

    def _update_chart(self, metrics=None):
        """Inline weekly-trend chart (docs/ui.md 4.4): Total Assets (in the
        active currency) vs KOSPI, both rebased to first week = 100, with
        the gap between them shaded to show excess return. The _show_graph
        modal is kept separately for a zoomed/detailed view."""
        if metrics is None:
            metrics = self._compute_records_metrics()
        ax = self._chart_ax
        ax.clear()
        if len(metrics) < 2:
            ax.text(0.5, 0.5, "Not enough data yet", ha="center", va="center",
                     transform=ax.transAxes, color=TEXT_MUTED, fontsize=9)
            self._chart_canvas.draw()
            return

        usd = (self._currency == "USD")
        total_key = "total_usd" if usd else "total_krw"
        first_total = metrics[0][total_key]
        first_kospi = metrics[0]["kospi_close"]

        x = [mdates.date2num(_dt.datetime.strptime(m["date"], "%Y-%m-%d")) for m in metrics]
        asset_rebased = [(100.0 * m[total_key] / first_total) if first_total else 100.0 for m in metrics]
        kospi_rebased = [(100.0 * m["kospi_close"] / first_kospi) if first_kospi else 100.0 for m in metrics]

        ax.plot(x, asset_rebased, color=ACCENT, linewidth=2, label=f"Total Assets ({self._currency})")
        ax.plot(x, kospi_rebased, color=TEXT_MUTED, linewidth=1.4, label="KOSPI")
        ax.fill_between(x, kospi_rebased, asset_rebased, color=PROFIT, alpha=0.14)

        ax.xaxis.set_major_formatter(mdates.DateFormatter("%y.%m.%d"))
        self._chart_fig.autofmt_xdate(rotation=30)
        ax.margins(x=0.02)
        ax.grid(True, linestyle=":", alpha=0.4)
        ax.legend(fontsize=8, loc="upper left")
        ax.set_ylabel("Index (first week = 100)", fontsize=8)

        self._chart_canvas.draw()

    def update_live_asset(self, current_total: float):
        self._current_live_asset = current_total
        self._update_live_asset_labels()
        self._maybe_autofill_asset_edit()

    def _update_live_asset_labels(self):
        
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
                color = PROFIT if diff > 0 else (LOSS if diff < 0 else TEXT)
                sign = "+" if diff > 0 else ""
                self.live_diff_lbl.setText(f"{sign}{diff:,.0f} ({sign}{ratio:.2f}%)")
                self.live_diff_lbl.setStyleSheet(f"color: {color};")
            else:
                self.live_diff_lbl.setText("-")
                self.live_diff_lbl.setStyleSheet(f"color:{TEXT};")
        else:
            self.live_diff_lbl.setText("-")
            self.live_diff_lbl.setStyleSheet(f"color:{TEXT};")
