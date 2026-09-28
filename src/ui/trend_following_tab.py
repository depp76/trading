"""ui/trend_following_tab.py — TrendFollowingTab: the research/backtest UI for
strategy.trend_following (trend_following.md v03).

One form (window, universe, sizing, variant matrix, cost scenarios, flows,
event study) -> one background run (threads.strategy_threads) -> a scorecard
table for every variant and benchmark per cost multiplier, a rebased NAV
chart, the event-study table, and "Save Report" for the markdown the spec
asks to keep per version. Signal generation / research only: nothing here
places orders (docs/ui.md 5.6 banner).
"""
from __future__ import annotations

import logging
import os
from datetime import date, datetime

from PyQt6.QtCore import Qt, QDate
from PyQt6.QtGui import QFont, QBrush, QColor
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton, QCheckBox, QComboBox,
    QSpinBox, QDateEdit, QTableWidget, QTableWidgetItem, QHeaderView, QFileDialog, QMessageBox,
    QSplitter, QFrame,
)

from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas

from paths import REPORTS_DIR
from strategy.trend_following import (
    SPEC_VERSION, StrategyParams, VARIANTS, COST_MULTIPLIERS, ResearchRequest, ResearchResult,
)
from strategy.trend_following.research import SUMMARY_COLUMNS
from strategy.trend_following.event_study import HORIZONS
from threads.strategy_threads import TrendFollowingResearchThread
from ui.common import create_font, ThreadOwnerMixin, FONT_HEADING, FONT_SMALL, FONT_BODY
from ui.theme import TEXT_MUTED, LINE, DANGER

logger = logging.getLogger(__name__)

_WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri"]
_COST_CHOICES = [("0x / 1x / 2x (4-1 sensitivity)", COST_MULTIPLIERS), ("1x only", (1.0,))]
_BENCHMARK_STYLE = {"BM1": ("#595d6c", "--"), "BM2": ("#9397ab", ":"), "BM3": ("#1c1e2c", "-."), "BM4": ("#c3c6d4", ":")}


def _fmt_event(value, pct=True, digits=1) -> str:
    if value is None:
        return "-"
    try:
        if value != value:
            return "-"
    except Exception:
        return "-"
    return f"{value * 100:.{digits}f}%" if pct else str(value)


class TrendFollowingTab(ThreadOwnerMixin, QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._result: ResearchResult | None = None
        self._research_thread = None
        self._build_ui()

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(8)
        root.setContentsMargins(12, 10, 12, 10)

        title = QLabel("Trend Following")
        title.setFont(create_font(FONT_HEADING, QFont.Weight.Bold))
        subtitle = QLabel(f"Research backtest per trend_following.md {SPEC_VERSION}: variants A0–A5 and B "
                          f"against BM1–BM4 with the 0x/1x/2x cost scenarios and the 6-1 event study.")
        subtitle.setObjectName("muted")
        subtitle.setFont(create_font(FONT_SMALL))
        subtitle.setWordWrap(True)
        root.addWidget(title)
        root.addWidget(subtitle)

        form = QGridLayout()
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(6)

        self.start_edit = QDateEdit(QDate(2021, 1, 1))
        self.end_edit = QDateEdit(QDate.currentDate())
        for de in (self.start_edit, self.end_edit):
            de.setCalendarPopup(True)
            de.setDisplayFormat("yyyy-MM-dd")
            de.setMaximumDate(QDate.currentDate())
            de.setFont(create_font(FONT_BODY))

        self.universe_spin = QSpinBox()
        self.universe_spin.setRange(20, 300)
        self.universe_spin.setSingleStep(10)
        self.universe_spin.setValue(StrategyParams.universe_size)

        self.liquidity_spin = QSpinBox()
        self.liquidity_spin.setRange(0, 5000)
        self.liquidity_spin.setSingleStep(10)
        self.liquidity_spin.setValue(int(StrategyParams.min_avg_trading_value / 1e8))
        self.liquidity_spin.setSuffix(" 억")

        self.positions_spin = QSpinBox()
        self.positions_spin.setRange(3, 12)
        self.positions_spin.setValue(StrategyParams.max_positions)

        self.weekday_combo = QComboBox()
        self.weekday_combo.addItems(_WEEKDAYS)
        self.weekday_combo.setCurrentIndex(StrategyParams.check_weekday)

        self.cost_combo = QComboBox()
        for label, _ in _COST_CHOICES:
            self.cost_combo.addItem(label)

        self.flows_cb = QCheckBox("Investor flows (F/FX; Naver, slow on first run)")
        self.event_cb = QCheckBox("Event study (6-1)")
        self.event_cb.setChecked(True)

        for w in (self.universe_spin, self.liquidity_spin, self.positions_spin, self.weekday_combo,
                  self.cost_combo, self.flows_cb, self.event_cb):
            w.setFont(create_font(FONT_BODY))

        def _lbl(text):
            lbl = QLabel(text)
            lbl.setFont(create_font(FONT_BODY))
            return lbl

        form.addWidget(_lbl("Start"), 0, 0)
        form.addWidget(self.start_edit, 0, 1)
        form.addWidget(_lbl("End"), 0, 2)
        form.addWidget(self.end_edit, 0, 3)
        form.addWidget(_lbl("Universe (KOSPI top N)"), 0, 4)
        form.addWidget(self.universe_spin, 0, 5)
        form.addWidget(_lbl("Liquidity floor (20d avg value)"), 0, 6)
        form.addWidget(self.liquidity_spin, 0, 7)
        form.addWidget(_lbl("Max positions"), 1, 0)
        form.addWidget(self.positions_spin, 1, 1)
        form.addWidget(_lbl("Weekly check day"), 1, 2)
        form.addWidget(self.weekday_combo, 1, 3)
        form.addWidget(_lbl("Cost scenarios"), 1, 4)
        form.addWidget(self.cost_combo, 1, 5)
        form.addWidget(self.flows_cb, 1, 6)
        form.addWidget(self.event_cb, 1, 7)
        form.setColumnStretch(8, 1)
        root.addLayout(form)

        variants_row = QHBoxLayout()
        variants_row.addWidget(_lbl("Variants"))
        self.variant_cbs: dict[str, QCheckBox] = {}
        for vid, v in VARIANTS.items():
            cb = QCheckBox(vid)
            cb.setChecked(True)
            cb.setToolTip(v.label)
            cb.setFont(create_font(FONT_BODY))
            self.variant_cbs[vid] = cb
            variants_row.addWidget(cb)
        variants_row.addStretch()
        self.run_btn = QPushButton("▶  Run Research")
        self.run_btn.setObjectName("primary")
        self.run_btn.setFont(create_font(FONT_BODY, QFont.Weight.Bold))
        self.run_btn.clicked.connect(self._on_run_clicked)
        self.save_btn = QPushButton("Save Report")
        self.save_btn.setFont(create_font(FONT_BODY))
        self.save_btn.setEnabled(False)
        self.save_btn.clicked.connect(self._on_save_clicked)
        variants_row.addWidget(self.run_btn)
        variants_row.addWidget(self.save_btn)
        root.addLayout(variants_row)

        # Status and errors live in their own line, never over the results (docs/ui.md 5.2).
        self.status_lbl = QLabel("Ready")
        self.status_lbl.setObjectName("muted")
        self.status_lbl.setFont(create_font(FONT_SMALL))
        self.status_lbl.setWordWrap(True)
        root.addWidget(self.status_lbl)

        splitter = QSplitter(Qt.Orientation.Vertical)

        self.table = QTableWidget()
        self.table.setFont(create_font(FONT_SMALL))
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        headers = ["ID", "Cost"] + [h for h, _ in SUMMARY_COLUMNS[1:]]
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setDefaultSectionSize(72)
        self.table.setColumnWidth(0, 60)
        self.table.setColumnWidth(1, 50)
        splitter.addWidget(self.table)

        self.fig = Figure(figsize=(6, 2.6), constrained_layout=True)
        self.ax = self.fig.add_subplot(111)
        self.canvas = FigureCanvas(self.fig)
        self.canvas.setMinimumHeight(180)
        splitter.addWidget(self.canvas)

        self.event_table = QTableWidget()
        self.event_table.setFont(create_font(FONT_SMALL))
        self.event_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.event_table.setAlternatingRowColors(True)
        self.event_table.verticalHeader().setVisible(False)
        ev_headers = ["Breakout group", "n"] + [f"{s} {h}d" for h in HORIZONS for s in ("mean", "hit", "vs KOSPI")]
        self.event_table.setColumnCount(len(ev_headers))
        self.event_table.setHorizontalHeaderLabels(ev_headers)
        self.event_table.setColumnWidth(0, 300)
        splitter.addWidget(self.event_table)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setStretchFactor(2, 2)
        root.addWidget(splitter, 1)

        banner = QFrame()
        banner.setObjectName("DashboardCard")
        banner_layout = QHBoxLayout(banner)
        banner_layout.setContentsMargins(10, 6, 10, 6)
        notice = QLabel("Research and backtesting signal generator, not investment advice. Backtests use today's "
                        "KOSPI constituents (survivorship bias) and the cost assumptions of trend_following.md 4-1; "
                        "nothing here places orders.")
        notice.setWordWrap(True)
        notice.setFont(create_font(FONT_BODY))
        notice.setStyleSheet(f"color: {DANGER};")
        banner_layout.addWidget(notice)
        root.addWidget(banner)

    # --------------------------------------------------------------- request
    def _selected_variants(self) -> tuple[str, ...]:
        return tuple(vid for vid, cb in self.variant_cbs.items() if cb.isChecked())

    def build_request(self) -> ResearchRequest:
        start = self.start_edit.date().toPyDate()
        end = self.end_edit.date().toPyDate()
        params = StrategyParams(
            universe_size=int(self.universe_spin.value()),
            min_avg_trading_value=float(self.liquidity_spin.value()) * 1e8,
            max_positions=int(self.positions_spin.value()),
            check_weekday=int(self.weekday_combo.currentIndex()),
        )
        mults = _COST_CHOICES[self.cost_combo.currentIndex()][1]
        return ResearchRequest(
            start=start, end=end, params=params, variant_ids=self._selected_variants(),
            cost_multipliers=tuple(mults), include_flows=self.flows_cb.isChecked(),
            run_backtests=True, run_event_study=self.event_cb.isChecked(),
        )

    def _on_run_clicked(self):
        if self.start_edit.date() >= self.end_edit.date():
            QMessageBox.warning(self, "Trend Following", "Start must be before End.")
            return
        if not self._selected_variants() and not self.event_cb.isChecked():
            QMessageBox.warning(self, "Trend Following", "Select at least one variant or the event study.")
            return
        req = self.build_request()
        thread = TrendFollowingResearchThread(req)
        thread.progress.connect(self._on_progress)
        thread.finished.connect(self._on_research_finished)
        self._track_thread(thread, "_research_thread")
        self.run_btn.setEnabled(False)
        self.status_lbl.setText("Starting...")
        thread.start()

    def _on_progress(self, text: str):
        self.status_lbl.setText(text)

    def _on_research_finished(self, result, error: str):
        self.run_btn.setEnabled(True)
        if result is None:
            self.status_lbl.setText(f"Run failed: {error}")
            return
        self._result = result
        self.save_btn.setEnabled(True)
        self.populate(result)
        n_runs = len(result.runs)
        warn = f" · {len(result.warnings)} warning(s), see the saved report" if result.warnings else ""
        self.status_lbl.setText(
            f"Done {result.created_at:%H:%M:%S} · {n_runs} runs · {result.dataset_info.get('n_tickers', 0)} names · "
            f"{result.dataset_info.get('eval_start', '')} → {result.dataset_info.get('eval_end', '')}{warn}")

    # --------------------------------------------------------------- results
    def populate(self, result: ResearchResult):
        self._fill_summary(result)
        self._draw_chart(result)
        self._fill_events(result)

    def _fill_summary(self, result: ResearchResult):
        rows = sorted(result.runs, key=lambda r: (r.cost_mult, r.kind != "strategy", r.id))
        self.table.setUpdatesEnabled(False)
        try:
            self.table.setRowCount(len(rows))
            for i, r in enumerate(rows):
                values = [r.id, f"{r.cost_mult:g}x"] + [fn(r) for _, fn in SUMMARY_COLUMNS[1:]]
                for c, text in enumerate(values):
                    item = QTableWidgetItem(text)
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                                          if c >= 2 else Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
                    if r.kind == "benchmark":
                        item.setForeground(QBrush(QColor(TEXT_MUTED)))
                    if c == 0:
                        item.setToolTip(r.label)
                    self.table.setItem(i, c, item)
        finally:
            self.table.setUpdatesEnabled(True)

    def _draw_chart(self, result: ResearchResult):
        ax = self.ax
        ax.clear()
        base_mult = 1.0 if result.runs_at(1.0) else (result.request.cost_multipliers[0] if result.runs else 1.0)
        for r in result.runs_at(base_mult):
            if len(r.nav) == 0:
                continue
            if r.kind == "benchmark":
                color, style = _BENCHMARK_STYLE.get(r.id, ("#9397ab", ":"))
                ax.plot(r.dates, r.nav * 100.0, linestyle=style, linewidth=1.2, color=color, label=r.id)
            else:
                ax.plot(r.dates, r.nav * 100.0, linewidth=1.5, label=r.id)
        ax.set_title(f"NAV rebased to 100 ({base_mult:g}x costs)", fontsize=FONT_SMALL)
        ax.grid(True, color=LINE, linewidth=0.6)
        ax.tick_params(labelsize=FONT_SMALL - 1)
        for spine in ("top", "right"):
            ax.spines[spine].set_visible(False)
        if ax.lines:
            ax.legend(fontsize=FONT_SMALL - 1, ncol=min(6, len(ax.lines)), frameon=False)
        self.canvas.draw_idle()

    def _fill_events(self, result: ResearchResult):
        rows = result.event_study or []
        self.event_table.setUpdatesEnabled(False)
        try:
            self.event_table.setRowCount(len(rows))
            for i, e in enumerate(rows):
                values = [e.get("group", ""), _fmt_event(e.get("n", 0), pct=False)]
                for h in HORIZONS:
                    values += [_fmt_event(e.get(f"mean_{h}")), _fmt_event(e.get(f"hit_{h}"), digits=0),
                               _fmt_event(e.get(f"excess_{h}"))]
                for c, text in enumerate(values):
                    item = QTableWidgetItem(text)
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                                          if c >= 1 else Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
                    self.event_table.setItem(i, c, item)
        finally:
            self.event_table.setUpdatesEnabled(True)

    # ---------------------------------------------------------------- report
    def _on_save_clicked(self):
        if self._result is None:
            return
        os.makedirs(REPORTS_DIR, exist_ok=True)
        default = os.path.join(REPORTS_DIR, f"trend_following_{SPEC_VERSION}_{datetime.now():%Y%m%d_%H%M}.md")
        path, _ = QFileDialog.getSaveFileName(self, "Save backtest report", default, "Markdown (*.md)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self._result.to_markdown())
            self.status_lbl.setText(f"Report saved: {path}")
        except Exception as e:
            logger.warning("Failed to save the Trend Following report", exc_info=True)
            self.status_lbl.setText(f"Save failed: {e}")

    def default_window(self) -> tuple[date, date]:
        return self.start_edit.date().toPyDate(), self.end_edit.date().toPyDate()
