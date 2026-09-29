"""ui/dialogs/trend_score.py — TrendScoreDialog: the Trading Universe's weekly
"trend + pullback" recommendation (trend_following.md 2-5, scoring.py).

Two tables, Top N (buy candidates: gate passed on the latest session,
highest weekly-average total score) and Bottom N (weakest: lowest weekly
average among the liquid names), with the five raw indicators of the latest
session next to each score. Non-modal; double-clicking a row emits
``ticker_activated`` so the Universe tab can open its MA chart.
"""
from __future__ import annotations

import math

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QTableWidget, QTableWidgetItem, QHeaderView, QSplitter,
    QWidget,
)

from strategy.trend_following.scoring import Recommendation, ScoredName
from ui.colors import fg_for, QC_FLAT
from ui.common import create_font, FONT_HEADING, FONT_BODY, FONT_SMALL
from ui.theme import TEXT_MUTED
from ui.widgets import NumericItem

# (header, width, kind): kind "text" | "num" | "pct" | "score" | "ratio"
COLUMNS = [
    ("#", 32, "text"),
    ("Name / Ticker", 170, "text"),
    ("Mkt", 58, "text"),
    ("Price", 76, "num"),
    ("Week avg", 68, "score"),
    ("Latest", 60, "score"),
    ("Rank", 52, "ratio"),
    ("Gate", 110, "text"),
    ("MA50Div %", 72, "pct"),
    ("Range52", 62, "ratio"),
    ("MA20Div %", 72, "pct"),
    ("3D (R3) %", 68, "pct"),
    ("10D (R10) %", 74, "pct"),
    ("20D %", 58, "pct"),
    ("Days", 44, "num"),
]
COL_NAME, COL_WEEK_AVG, COL_GATE = 1, 4, 7
assert COLUMNS[COL_NAME][0] == "Name / Ticker" and COLUMNS[COL_WEEK_AVG][0] == "Week avg" and COLUMNS[COL_GATE][0] == "Gate"
COL_TICKER_ROLE = Qt.ItemDataRole.UserRole


def _finite(v) -> bool:
    try:
        return v is not None and math.isfinite(float(v))
    except (TypeError, ValueError):
        return False


def _cell(kind: str, value, text: str | None = None) -> QTableWidgetItem:
    """A right-aligned NumericItem for numbers ("-" and a -inf sort key when
    missing), a plain left-aligned item for text."""
    if kind == "text":
        item = QTableWidgetItem("" if value is None else str(value))
        item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        return item
    if not _finite(value):
        item = NumericItem("-", float("-inf"))
        item.setForeground(QC_FLAT)
    else:
        v = float(value)
        if text is None:
            if kind == "num":
                text = f"{v:,.0f}"
            elif kind == "pct":
                text = f"{v * 100:+.1f}"
            elif kind == "ratio":
                text = f"{v:.2f}"
            else:   # score
                text = f"{v:+.2f}"
        item = NumericItem(text, v)
        if kind == "pct":
            item.setForeground(fg_for(v))
    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    return item


def row_values(rank: int, s: ScoredName) -> list:
    """The 14 cell values of one recommendation row, in COLUMNS order."""
    gate = "Pass" if s.gate else ", ".join(s.reasons) or "Fail"
    return [
        str(rank), f"{s.name}  {s.ticker}", s.market, s.close, s.week_avg, s.latest_total, s.latest_rank,
        gate, s.ma50_div, s.range52, s.ma20_div, s.r3, s.r10, s.r20, s.sessions,
    ]


class _ScoreTable(QTableWidget):
    ticker_activated = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFont(create_font(FONT_SMALL))
        self.setColumnCount(len(COLUMNS))
        self.setHorizontalHeaderLabels([h for h, _, _ in COLUMNS])
        self.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.setAlternatingRowColors(True)
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(26)
        header = self.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(False)
        for i, (_, w, _) in enumerate(COLUMNS):
            self.setColumnWidth(i, w)
        # The Gate column holds the longest text ("MA50Div<0, Range52<0.70"),
        # so it is the one that absorbs the remaining width.
        header.setSectionResizeMode(COL_GATE, QHeaderView.ResizeMode.Stretch)
        self.setSortingEnabled(True)
        self.doubleClicked.connect(self._on_activated)

    def fill(self, names: list[ScoredName]):
        self.setSortingEnabled(False)
        self.setUpdatesEnabled(False)
        try:
            self.setRowCount(len(names))
            for r, s in enumerate(names):
                for c, (_, _, kind) in enumerate(COLUMNS):
                    item = _cell(kind, row_values(r + 1, s)[c])
                    if c == COL_NAME:
                        item.setData(COL_TICKER_ROLE, s.ticker)
                        item.setToolTip(f"{s.name} ({s.ticker}, {s.market})")
                    self.setItem(r, c, item)
        finally:
            self.setUpdatesEnabled(True)
            self.setSortingEnabled(True)

    def _on_activated(self, index):
        item = self.item(index.row(), COL_NAME)
        ticker = item.data(COL_TICKER_ROLE) if item is not None else None
        if ticker:
            self.ticker_activated.emit(str(ticker))


class TrendScoreDialog(QDialog):
    """Weekly Top/Bottom recommendation of the 2-5 scoring mode."""
    ticker_activated = pyqtSignal(str)

    def __init__(self, rec: Recommendation, parent=None):
        super().__init__(parent)
        self.rec = rec
        self.setWindowTitle("Trend + Pullback Score")
        self.setModal(False)
        self.resize(1180, 720)
        self._build_ui()
        self.populate(rec)

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(6)
        root.setContentsMargins(12, 10, 12, 10)

        title = QLabel("Trend + Pullback Score — weekly Top / Bottom (trend_following.md 2-5)")
        title.setFont(create_font(FONT_HEADING, QFont.Weight.Bold))
        root.addWidget(title)

        self.summary_lbl = QLabel()
        self.summary_lbl.setObjectName("muted")
        self.summary_lbl.setFont(create_font(FONT_SMALL))
        self.summary_lbl.setWordWrap(True)
        root.addWidget(self.summary_lbl)

        splitter = QSplitter(Qt.Orientation.Vertical)
        self.top_table = _ScoreTable()
        self.bottom_table = _ScoreTable()
        self.top_lbl = QLabel()
        self.bottom_lbl = QLabel()
        for lbl, table in ((self.top_lbl, self.top_table), (self.bottom_lbl, self.bottom_table)):
            lbl.setFont(create_font(FONT_BODY, QFont.Weight.Bold))
            box = QWidget()
            lay = QVBoxLayout(box)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(4)
            lay.addWidget(lbl)
            lay.addWidget(table)
            splitter.addWidget(box)
            table.ticker_activated.connect(self.ticker_activated)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        root.addWidget(splitter, 1)

        note = QLabel(
            "Total = pct(MA50Div) + pct(Range52) − pct(R3) − 0.5 × pct(R10); percentiles are taken daily among the "
            "liquid names (20-day average value ≥ floor), R3 / R10 / MA20Div are divided by ATR20 / close. Gate = "
            "MA50Div > 0, Range52 ≥ 0.70, not in the top 5% of MA20Div. \"Week avg\" is the mean of the daily total "
            "over the listed sessions; \"Rank\" is the latest total's percentile within the gated names. Top: gate "
            "passed, highest week average. Bottom: liquid names with the lowest week average. 3D / 10D are the score's R3 / R10 "
            "(close-to-close), 20D is shown for context only. Double-click a row for its MA chart. Research signal only, not investment advice."
        )
        note.setObjectName("muted")
        note.setFont(create_font(FONT_SMALL))
        note.setWordWrap(True)
        note.setStyleSheet(f"color: {TEXT_MUTED};")
        root.addWidget(note)

    def populate(self, rec: Recommendation):
        self.rec = rec
        week = rec.week_dates
        span = f"{week[0]:%Y-%m-%d} → {week[-1]:%Y-%m-%d}" if week else "-"
        regime = {True: "Risk-on", False: "Risk-off"}.get(rec.regime_on, "n/a")
        self.summary_lbl.setText(
            f"As of {rec.as_of:%Y-%m-%d} · week {span} ({len(week)} sessions) · universe {rec.n_universe} · "
            f"liquid {rec.n_liquid} · gate {rec.n_gated} · KOSPI regime (L1): {regime}"
        )
        n = max(len(rec.top), len(rec.bottom), 1)
        self.top_lbl.setText(f"Top {len(rec.top)} — buy candidates (gate passed, highest week-average score)")
        self.bottom_lbl.setText(f"Bottom {len(rec.bottom)} — weakest (lowest week-average score among liquid names)")
        self.top_table.fill(rec.top)
        self.bottom_table.fill(rec.bottom)
        if n:
            self.top_table.sortItems(COL_WEEK_AVG, Qt.SortOrder.DescendingOrder)
            self.bottom_table.sortItems(COL_WEEK_AVG, Qt.SortOrder.AscendingOrder)


def show_trend_score(parent, rec: Recommendation) -> TrendScoreDialog:
    """Build, show and return the dialog (the caller keeps the reference)."""
    dlg = TrendScoreDialog(rec, parent)
    dlg.show()
    return dlg
