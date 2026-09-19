"""ui/auto_trading_tab.py — AutoTradingTab (rebalance.md section 3-1 implementation)

Weekly rebalance signal tab: factor-scores and ranks the Trading Universe
(rebalance.md 3-1: "factor scoring + rank rebalancing"), classifying stocks into buy/
sell/hold candidates against the account's currently open positions.

Signal generation only — this tab never places orders (rebalance.md section 1
explicitly separates signal generation from execution). Results are a
starting point for backtesting/paper trading (rebalance.md section 6), not
investment advice; see the disclaimer banner built into the tab.

Also hosts the walk-forward backtest UI (rebalance.md section 6): pick a
lookback of 1-5 years and run strategy.rebalance.run_rebalance_backtest() in a
background thread (RebalanceBacktestThread), showing the result in
BacktestResultDialog. The backtest reuses the exact same scoring/
classification functions as the live signal computation above
(strategy.rebalance._score_and_rank / _classify_buy_sell_hold), so tuning the
algorithm in strategy/rebalance/ changes both consistently.

Unlike the other tabs, this one reads UniverseTab.all_data directly (passed
in at construction) rather than subscribing to a signal: the read only
happens once, on demand, when the user clicks "Compute This Week's
Signals" -- there is no live state to keep in sync, so the extra signal
plumbing the other tabs use for push updates would add complexity with no
benefit here.

Redesign (docs/ui.md "Strategy Redesign" mockup, roadmap 7-1 Phase 3/4):
  - Buy/Sell candidate tables and the 13-column Full Ranking table used to
    duplicate the same ranked list three times (issue #5); they are one
    table now (_signal_table), filterable by action, with a rank/action-
    colored left marker and the 7 score factors shown as heat-tinted cells
    in the same row as the badge that made them a candidate (issue #4).
  - emoji section labels ("🟢 Buy Candidates") are gone (issue #5); Buy/Sell/
    Hold/no-action are a text badge now, colored via ui.colors.ACTION_BUY/
    ACTION_SELL/theme.ACCENT -- deliberately not PROFIT/LOSS, see
    ui/colors.py's docstring on those two constants.
  - column widths are fixed (min-width, Interactive resize) instead of
    ResizeToContents, which used to reflow every column on every recompute
    (issue #6).
  - the disclaimer is a body-sized warning banner now, not an 8pt gray
    caption below the table (issue #7).
"""
import logging

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTableWidget,
    QTableWidgetItem, QHeaderView, QMessageBox, QComboBox, QFrame,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QColor

import trade_db
from strategy.rebalance import compute_weekly_rebalance_signals, RebalanceConfig
from threads.fetch_threads import RebalanceBacktestThread
from ui.dialogs import BacktestResultDialog
from ui.delegates import RankStockDelegate, ActionBadgeDelegate, ScoreBarDelegate
from ui.ma_chart import StockMaLauncherMixin
from ui.colors import ACTION_BUY, ACTION_SELL, PROFIT
from ui.theme import ACCENT, TEXT_FAINT, LINE

logger = logging.getLogger(__name__)


from ui.common import create_font, ThreadOwnerMixin


# ---------------------------------------------------------------------------
# Merged ranking table column spec (docs/ui.md Strategy Redesign issue #4/#5):
# Rank/Stock + Action + Score replace the old separate Buy/Sell candidate
# tables' 5 columns; the 7 raw factors are the old Full Ranking table's own
# columns (kept as 7, not the mockup's 6 -- MA20Slope1W is real data this app
# already computes and the mockup simply didn't have in its mock dataset;
# dropping a real factor to match a mockup with no data for it isn't a good
# trade). Widths are fixed mins for QHeaderView.ResizeMode.Interactive
# (issue #6: ResizeToContents used to reflow the whole table on every
# recompute).
# ---------------------------------------------------------------------------
_SIGNAL_COLUMNS = [
    ("stock",  "Rank / Stock", 210),
    ("action", "Action",        64),
    ("score",  "Score",         92),
    ("per",    "PER",           58),
    ("ma20div","MA20Div",       64),
    ("ma50div","MA50Div",       64),
    ("hi52",   "52wHigh%",      68),
    ("ret20",  "Ret20D%",       62),
    ("ret60",  "Ret60D%",       62),
    ("slope",  "MA20Slope1W%",  84),
    ("held",   "Held",          46),
]
COL_STOCK, COL_ACTION, COL_SCORE, COL_PER, COL_MA20DIV, COL_MA50DIV, \
    COL_HI52, COL_RET20, COL_RET60, COL_SLOPE, COL_HELD = range(len(_SIGNAL_COLUMNS))

def _contribution_bg(z):
    """Background tint for a factor cell: alpha scales with the factor's
    already sign-flipped z-score (higher = more favorable to the score,
    regardless of which raw direction that factor's "better" means), colored
    ACCENT throughout -- a single hue ramping low-to-high (docs/ui.md
    Strategy Redesign's "점수 기여 · 낮음 -> 높음" legend), not the diverging
    PROFIT/LOSS pair those colors mean elsewhere (up/down), since a factor's
    contribution isn't a price direction."""
    if z is None:
        return None
    alpha = 0.04 + max(0.0, min(1.0, (z + 2.0) / 4.0)) * 0.22
    color = QColor(ACCENT)
    color.setAlphaF(alpha)
    return color


class AutoTradingTab(StockMaLauncherMixin, ThreadOwnerMixin, QWidget):
    """Weekly rebalance signal tab (rebalance.md 3-1: factor scoring + rank rebalancing).

    rebalance.md decisions this implementation follows:
      - top_n_by_market = {"KOSPI": 10, "KOSDAQ": 10} target holdings
        (rebalance.md 3-5 -- ranked per market so KOSDAQ's higher volatility
        doesn't crowd out KOSPI in a combined top-N)
      - band_multiplier = 1.5 -> a held stock is only a sell candidate once
        its rank within its own market falls below 15 (reduces weekly turnover)
    Both values come from RebalanceConfig (rebalance.md 8-H / 11-4 step 3) --
    the single source of truth shared with strategy/rebalance's own defaults, so
    they're no longer duplicated as separate literals here.
    Factor weights are an equal-weighted v1 default (see
    strategy.rebalance.compute_weekly_rebalance_signals docstring) -- tune based on
    backtest results per rebalance.md section 6, not fixed here.
    """

    _REBALANCE_CONFIG = RebalanceConfig()
    TOP_N_BY_MARKET = _REBALANCE_CONFIG.top_n_by_market
    BAND_MULTIPLIER = _REBALANCE_CONFIG.band_multiplier
    INITIAL_CAPITAL = 100_000_000.0  # arbitrary notional for the backtest (rebalance.md section 6)

    def __init__(self, universe_tab, parent=None):
        super().__init__(parent)
        self._universe_tab = universe_tab
        self._last_result = None
        self._signal_rows = []       # every merged row (dict), unfiltered
        self._action_filter = "All"
        self._backtest_thread = None
        self._build_ui()

    # ---UI construction ---
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(8)

        title = QLabel("Auto Trading — Weekly Rebalance Signals")
        title.setFont(create_font(16, QFont.Weight.Bold))
        root.addWidget(title)

        top_n_str = ", ".join(f"{m} {n}" for m, n in self.TOP_N_BY_MARKET.items())
        subtitle = QLabel(
            f"Factor scoring + rank rebalancing (rebalance.md 3-1/3-5) — target {top_n_str}, "
            f"sell band at per-market rank > {int(next(iter(self.TOP_N_BY_MARKET.values())) * self.BAND_MULTIPLIER)}"
        )
        subtitle.setFont(create_font(9, style_name="Semilight"))
        subtitle.setObjectName("muted")
        root.addWidget(subtitle)

        ctrl_row = QHBoxLayout()
        # docs/ui.md 1.6: this tab's one accented action; Run Backtest below
        # is the neutral outline button.
        self._compute_btn = QPushButton("🔄 Compute This Week's Signals")
        self._compute_btn.setFont(create_font(10, QFont.Weight.Bold))
        self._compute_btn.setFixedHeight(32)
        self._compute_btn.setObjectName("primary")
        self._compute_btn.clicked.connect(self._on_compute_clicked)
        ctrl_row.addWidget(self._compute_btn)

        self._as_of_label = QLabel("Not yet computed")
        self._as_of_label.setFont(create_font(9, style_name="Semilight"))
        self._as_of_label.setObjectName("muted")
        ctrl_row.addWidget(self._as_of_label)
        ctrl_row.addStretch()
        root.addLayout(ctrl_row)

        # Walk-forward backtest controls (rebalance.md section 6)
        backtest_row = QHBoxLayout()
        lookback_lbl = QLabel("Backtest lookback:")
        lookback_lbl.setFont(create_font(10, style_name="Semilight"))
        backtest_row.addWidget(lookback_lbl)

        self._lookback_combo = QComboBox()
        self._lookback_combo.setFont(create_font(10, style_name="Semilight"))
        for y in (1, 2, 3, 4, 5):
            self._lookback_combo.addItem(f"{y} Year{'s' if y > 1 else ''}", userData=y)
        self._lookback_combo.setCurrentIndex(2)  # default 3 years
        self._lookback_combo.setFixedWidth(110)
        backtest_row.addWidget(self._lookback_combo)

        self._backtest_btn = QPushButton("▶ Run Backtest")
        self._backtest_btn.setFont(create_font(10, QFont.Weight.Bold))
        self._backtest_btn.setFixedHeight(32)
        self._backtest_btn.clicked.connect(self._on_backtest_clicked)
        backtest_row.addWidget(self._backtest_btn)

        self._backtest_status_label = QLabel("")
        self._backtest_status_label.setFont(create_font(9, style_name="Semilight"))
        self._backtest_status_label.setObjectName("muted")
        backtest_row.addWidget(self._backtest_status_label)
        backtest_row.addStretch()
        root.addLayout(backtest_row)

        root.addLayout(self._build_filter_row())
        root.addWidget(self._build_signal_table(), 1)
        root.addWidget(self._build_footer_row())
        root.addWidget(self._build_disclaimer_banner())

    def _build_filter_row(self) -> QHBoxLayout:
        """Buy/Sell/Hold/All action filter (docs/ui.md Strategy Redesign
        issue #5's fix: one table, filtered, instead of three permanently
        duplicated tables)."""
        row = QHBoxLayout()
        row.setSpacing(4)
        label = QLabel("Full Ranking")
        label.setFont(create_font(11, QFont.Weight.Bold))
        row.addWidget(label)
        row.addSpacing(12)

        self._filter_buttons = {}
        for name in ("All", "Buy", "Sell", "Hold"):
            btn = QPushButton(name)
            btn.setFont(create_font(9, style_name="Semilight"))
            btn.setFixedHeight(26)
            btn.setFixedWidth(56)
            btn.setCheckable(True)
            btn.setChecked(name == "All")
            btn.clicked.connect(lambda checked, n=name: self._on_filter_changed(n))
            row.addWidget(btn)
            self._filter_buttons[name] = btn
        row.addStretch()
        return row

    def _on_filter_changed(self, name: str):
        self._action_filter = name
        for n, btn in self._filter_buttons.items():
            btn.setChecked(n == name)
        self._apply_filter()

    def _build_signal_table(self) -> QTableWidget:
        tbl = self._signal_table = QTableWidget(0, len(_SIGNAL_COLUMNS))
        tbl.setHorizontalHeaderLabels([label for _, label, _ in _SIGNAL_COLUMNS])
        tbl.setFont(create_font(9, style_name="Semilight"))
        tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        tbl.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        tbl.verticalHeader().setVisible(False)
        tbl.verticalHeader().setDefaultSectionSize(30)
        tbl.setAlternatingRowColors(True)
        # docs/ui.md Strategy Redesign issue #6: fixed min-widths + Interactive
        # resize (user can still drag columns wider) in place of
        # ResizeToContents, which used to reflow every column's position on
        # every recompute.
        tbl.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        for col, (_, _label, width) in enumerate(_SIGNAL_COLUMNS):
            tbl.setColumnWidth(col, width)
        tbl.setItemDelegateForColumn(COL_STOCK, RankStockDelegate(tbl))
        tbl.setItemDelegateForColumn(COL_ACTION, ActionBadgeDelegate(tbl))
        tbl.setItemDelegateForColumn(COL_SCORE, ScoreBarDelegate(tbl))
        tbl.cellDoubleClicked.connect(self._on_row_double_clicked)
        return tbl

    def _build_footer_row(self) -> QLabel:
        self._footer_lbl = QLabel("")
        self._footer_lbl.setFont(create_font(8, style_name="Semilight"))
        self._footer_lbl.setStyleSheet(f"color:{TEXT_FAINT};")
        return self._footer_lbl

    def _build_disclaimer_banner(self) -> QFrame:
        """docs/ui.md Strategy Redesign issue #7: the "not investment advice"
        notice used to be an 8pt gray caption below the table -- the least
        readable text on the whole tab for arguably its most important
        sentence. It's a body-sized (12px), warning-toned banner now."""
        banner = QFrame()
        banner.setStyleSheet(
            "QFrame { background:#fdf0ef; border:1px solid #f0c4c1; border-radius:6px; }"
        )
        row = QHBoxLayout(banner)
        row.setContentsMargins(10, 8, 10, 8)
        row.setSpacing(8)

        tag = QLabel("NOTICE")
        tag.setFont(create_font(8, QFont.Weight.Bold))
        tag.setStyleSheet(
            f"color:{PROFIT}; background:#fff; border:1px solid #f0c4c1; border-radius:4px; padding:1px 6px;"
        )
        row.addWidget(tag)

        text = QLabel(
            "Research/backtesting signal generator, not investment advice. Verify with backtesting "
            "and paper trading before using real capital (rebalance.md section 6)."
        )
        text.setFont(create_font(9, style_name="Semilight"))
        text.setStyleSheet("color:#7a3b35;")
        text.setWordWrap(True)
        row.addWidget(text, 1)
        return banner

    # ---Compute + render ---
    def _on_compute_clicked(self):
        universe_data = getattr(self._universe_tab, "all_data", None) or []
        if not universe_data:
            QMessageBox.information(
                self, "No Data",
                "Trading Universe has no data yet — refresh it on the Trading Universe tab first.",
            )
            return

        try:
            open_trades = trade_db.get_open_trades()
        except Exception:
            logger.warning("Failed to read open trades for rebalance signal computation", exc_info=True)
            open_trades = []
        current_holdings = {t.get("ticker") for t in open_trades if t.get("ticker")}

        result = compute_weekly_rebalance_signals(
            universe_data,
            current_holdings=current_holdings,
            top_n_by_market=self.TOP_N_BY_MARKET,
            band_multiplier=self.BAND_MULTIPLIER,
        )
        self._last_result = result
        self._render(result, current_holdings)

    def _render(self, result, current_holdings):
        threshold_str = ", ".join(f"{m}>{n}" for m, n in result["sell_threshold_by_market"].items())
        self._as_of_label.setText(
            f"As of {result['as_of']} — {len(result['ranked'])} ranked "
            f"({result['excluded_count']} excluded), sell threshold per-market rank: {threshold_str}"
        )

        # ---Merge ranked + buy/sell/hold classification into one row list
        # (docs/ui.md Strategy Redesign issue #5) instead of three tables. ---
        action_by_ticker = {}
        for r in result["buy_candidates"]:
            action_by_ticker[r["ticker"]] = "Buy"
        for r in result["hold"]:
            action_by_ticker[r["ticker"]] = "Hold"
        for r in result["sell_candidates"]:
            action_by_ticker.setdefault(r["ticker"], "Sell")

        ranked_tickers = {r["ticker"] for r in result["ranked"]}
        rows = list(result["ranked"])
        # sell_candidates can include synthetic entries for held tickers that
        # fell out of the ranked universe entirely (classify.py's "unranked
        # -> sell" fallback) -- append those so a real sell signal is never
        # silently dropped from the merged table.
        for r in result["sell_candidates"]:
            if r["ticker"] not in ranked_tickers:
                rows.append(r)

        scores = [r["score"] for r in rows if isinstance(r.get("score"), (int, float)) and r["score"] != float("-inf")]
        score_lo, score_hi = (min(scores), max(scores)) if scores else (0.0, 1.0)
        score_span = (score_hi - score_lo) or 1.0

        self._signal_rows = []
        for r in rows:
            action = action_by_ticker.get(r["ticker"], "-")
            color = {"Buy": ACTION_BUY, "Sell": ACTION_SELL, "Hold": ACCENT, "-": LINE}[action]
            score = r.get("score")
            has_score = isinstance(score, (int, float)) and score != float("-inf")
            self._signal_rows.append({
                "ticker": r["ticker"], "name": r.get("name", r["ticker"]), "market": r.get("market", ""),
                "rank": r.get("rank"), "action": action, "color": color,
                "score": score, "score_pct": ((score - score_lo) / score_span) if has_score else 0.0,
                "raw": r.get("raw", {}), "factors": r.get("factors", {}),
                "held": r["ticker"] in current_holdings,
            })

        self._apply_filter()

    def _apply_filter(self):
        if self._action_filter == "All":
            rows = self._signal_rows
        else:
            rows = [r for r in self._signal_rows if r["action"] == self._action_filter]
        self._fill_signal_table(rows)

        counts = {n: sum(1 for r in self._signal_rows if n == "All" or r["action"] == n) for n in self._filter_buttons}
        for n, btn in self._filter_buttons.items():
            btn.setText(f"{n} ({counts[n]})")

        if self._last_result:
            threshold_str = ", ".join(f"{m}>{n}" for m, n in self._last_result["sell_threshold_by_market"].items())
            self._footer_lbl.setText(
                f"Ranked {len(self._last_result['ranked'])} · Excluded {self._last_result['excluded_count']} · "
                f"Sell threshold per-market rank {threshold_str} · Double-click a row for its MA20/MA60 chart"
            )

    @staticmethod
    def _fmt(v):
        return f"{v:.2f}" if isinstance(v, (int, float)) else "-"

    def _fill_signal_table(self, rows):
        tbl = self._signal_table
        tbl.setUpdatesEnabled(False)
        try:
            tbl.setRowCount(len(rows))
            for r, row in enumerate(rows):
                stock_item = QTableWidgetItem("")
                stock_item.setData(Qt.ItemDataRole.UserRole, {
                    "rank": row["rank"] if row["rank"] is not None else "-",
                    "name": row["name"], "meta": f"{row['ticker']} · {row['market']}",
                    "color": row["color"],
                })
                tbl.setItem(r, COL_STOCK, stock_item)

                action_item = QTableWidgetItem("")
                action_item.setData(Qt.ItemDataRole.UserRole, {"action": row["action"]})
                tbl.setItem(r, COL_ACTION, action_item)

                score_item = QTableWidgetItem("")
                score_item.setData(Qt.ItemDataRole.UserRole, {
                    "pct": row["score_pct"], "text": self._fmt(row["score"]),
                })
                tbl.setItem(r, COL_SCORE, score_item)

                raw = row["raw"]
                factors = row["factors"]
                for col, raw_key, fac_key in (
                    (COL_PER, "value_per", "value_per"),
                    (COL_MA20DIV, "ma20_momentum", "ma20_momentum"),
                    (COL_MA50DIV, "ma50_momentum", "ma50_momentum"),
                    (COL_HI52, "high52w_proximity", "high52w_proximity"),
                    (COL_RET20, "ret_20d", "ret_20d"),
                    (COL_RET60, "ret_60d", "ret_60d"),
                    (COL_SLOPE, "ma20_slope_1w", "ma20_slope_1w"),
                ):
                    cell = QTableWidgetItem(self._fmt(raw.get(raw_key)))
                    cell.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                    bg = _contribution_bg(factors.get(fac_key))
                    if bg is not None:
                        cell.setBackground(bg)
                    tbl.setItem(r, col, cell)

                held_item = QTableWidgetItem("●" if row["held"] else "")
                held_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                held_item.setForeground(QColor(ACCENT))
                tbl.setItem(r, COL_HELD, held_item)
        finally:
            tbl.setUpdatesEnabled(True)

    # ---MA chart on double-click (review.md 2-3) ---
    def _on_row_double_clicked(self, row, _col):
        if row >= len(self._filtered_rows()):
            return
        item = self._filtered_rows()[row]
        self._show_stock_ma(item["ticker"], item["name"], item["market"])

    def _filtered_rows(self):
        if self._action_filter == "All":
            return self._signal_rows
        return [r for r in self._signal_rows if r["action"] == self._action_filter]

    # ---Backtest (rebalance.md section 6) ---
    def _on_backtest_clicked(self):
        if self._backtest_thread is not None and self._backtest_thread.isRunning():
            return

        universe_data = getattr(self._universe_tab, "all_data", None) or []
        tickers = [it.get("ticker") for it in universe_data if it.get("ticker") and not it.get("is_index")]
        if not tickers:
            QMessageBox.information(
                self, "No Data",
                "Trading Universe has no data yet — refresh it on the Trading Universe tab first.",
            )
            return

        self._backtest_ticker_name_map = {
            it.get("ticker"): it.get("name", "") for it in universe_data if it.get("ticker")
        }
        market_by_ticker = {
            it.get("ticker"): it.get("market", "") for it in universe_data if it.get("ticker")
        }

        lookback_years = self._lookback_combo.currentData()
        self._backtest_btn.setEnabled(False)
        self._backtest_status_label.setText(f"Fetching history for {len(tickers)} tickers... (0/{len(tickers)})")

        self._track_thread(RebalanceBacktestThread(
            tickers, lookback_years, self.TOP_N_BY_MARKET, self.BAND_MULTIPLIER, self.INITIAL_CAPITAL,
            market_by_ticker=market_by_ticker,
        ), '_backtest_thread')
        self._backtest_thread.progress.connect(self._on_backtest_progress)
        self._backtest_thread.finished.connect(self._on_backtest_finished)
        self._backtest_thread.start()

    def _on_backtest_progress(self, done, total):
        self._backtest_status_label.setText(f"Fetching history... ({done}/{total})")

    def _on_backtest_finished(self, result, error):
        self._backtest_btn.setEnabled(True)
        if error:
            self._backtest_status_label.setText("Backtest failed — see app.log")
            QMessageBox.warning(self, "Backtest Error", f"Backtest failed:\n{error}")
            return
        self._backtest_status_label.setText(
            f"Last backtest: {result['start_date']} → {result['end_date']}, "
            f"return {result['summary']['total_return_pct']:+.1f}%"
        )
        dlg = BacktestResultDialog(
            result, parent=self,
            ticker_name_map=getattr(self, "_backtest_ticker_name_map", None),
        )
        dlg.exec()
