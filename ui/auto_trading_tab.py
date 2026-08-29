"""ui/auto_trading_tab.py — AutoTradingTab (trading.md section 3-1 implementation)

Weekly rebalance signal tab: factor-scores and ranks the Trading Universe
(trading.md 3-1: "팩터 스코어링 + 순위 리밸런싱"), classifying stocks into buy/
sell/hold candidates against the account's currently open positions.

Signal generation only — this tab never places orders (trading.md section 1
explicitly separates signal generation from execution). Results are a
starting point for backtesting/paper trading (trading.md section 6), not
investment advice; see the disclaimer label built into the tab.

Also hosts the walk-forward backtest UI (trading.md section 6): pick a
lookback of 1-5 years and run data_fetcher.run_rebalance_backtest() in a
background thread (RebalanceBacktestThread), showing the result in
BacktestResultDialog. The backtest reuses the exact same scoring/
classification functions as the live signal computation above
(data_fetcher._score_and_rank / _classify_buy_sell_hold), so tuning the
algorithm in data_fetcher.py changes both consistently.

Unlike the other tabs, this one reads UniverseTab.all_data directly (passed
in at construction) rather than subscribing to a signal: the read only
happens once, on demand, when the user clicks "Compute This Week's
Signals" -- there is no live state to keep in sync, so the extra signal
plumbing the other tabs use for push updates would add complexity with no
benefit here.
"""
import logging

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTableWidget,
    QTableWidgetItem, QHeaderView, QMessageBox, QComboBox,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont

import trade_db
from data_fetcher import compute_weekly_rebalance_signals
from threads.fetch_threads import RebalanceBacktestThread
from ui.dialogs import BacktestResultDialog

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lazy helper -- avoid circular imports with main (main.py imports this
# module at load time, so this module cannot import main at load time in
# return). Forwards to main's implementation so the class body below can
# call create_font(...) unchanged from its original form elsewhere in the app.
# ---------------------------------------------------------------------------
def _get_create_font():
    import main as _m
    return _m.create_font


def create_font(*args, **kwargs):
    return _get_create_font()(*args, **kwargs)


class AutoTradingTab(QWidget):
    """Weekly rebalance signal tab (trading.md 3-1: factor scoring + rank rebalancing).

    trading.md decisions this implementation follows:
      - top_n = 20 target holdings
      - band_multiplier = 1.5 -> a held stock is only a sell candidate once
        its rank falls below 30 (reduces weekly turnover)
    Factor weights are an equal-weighted v1 default (see
    data_fetcher.compute_weekly_rebalance_signals docstring) -- tune based on
    backtest results per trading.md section 6, not fixed here.
    """

    TOP_N = 20
    BAND_MULTIPLIER = 1.5
    INITIAL_CAPITAL = 100_000_000.0  # arbitrary notional for the backtest (trading.md section 6)

    def __init__(self, universe_tab, parent=None):
        super().__init__(parent)
        self._universe_tab = universe_tab
        self._last_result = None
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

        subtitle = QLabel(
            f"Factor scoring + rank rebalancing (trading.md 3-1) — target {self.TOP_N} holdings, "
            f"sell band at rank > {int(self.TOP_N * self.BAND_MULTIPLIER)}"
        )
        subtitle.setFont(create_font(9, style_name="Semilight"))
        subtitle.setStyleSheet("color:#7f8c8d;")
        root.addWidget(subtitle)

        ctrl_row = QHBoxLayout()
        self._compute_btn = QPushButton("🔄 Compute This Week's Signals")
        self._compute_btn.setFont(create_font(10, QFont.Weight.Bold))
        self._compute_btn.setFixedHeight(32)
        self._compute_btn.setStyleSheet(
            "QPushButton { background:#0078d4; color:white; border-radius:4px; padding:4px 14px; font-weight:bold; }"
            "QPushButton:hover { background:#005a9e; }"
        )
        self._compute_btn.clicked.connect(self._on_compute_clicked)
        ctrl_row.addWidget(self._compute_btn)

        self._as_of_label = QLabel("Not yet computed")
        self._as_of_label.setFont(create_font(9, style_name="Semilight"))
        self._as_of_label.setStyleSheet("color:#7f8c8d;")
        ctrl_row.addWidget(self._as_of_label)
        ctrl_row.addStretch()
        root.addLayout(ctrl_row)

        # Walk-forward backtest controls (trading.md section 6)
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
        self._backtest_btn.setStyleSheet(
            "QPushButton { background:#8e44ad; color:white; border-radius:4px; padding:4px 14px; font-weight:bold; }"
            "QPushButton:hover { background:#732d91; }"
            "QPushButton:disabled { background:#bbb; }"
        )
        self._backtest_btn.clicked.connect(self._on_backtest_clicked)
        backtest_row.addWidget(self._backtest_btn)

        self._backtest_status_label = QLabel("")
        self._backtest_status_label.setFont(create_font(9, style_name="Semilight"))
        self._backtest_status_label.setStyleSheet("color:#7f8c8d;")
        backtest_row.addWidget(self._backtest_status_label)
        backtest_row.addStretch()
        root.addLayout(backtest_row)

        # Buy / Sell candidate tables side by side
        lists_row = QHBoxLayout()

        buy_col = QVBoxLayout()
        buy_lbl = QLabel("🟢 Buy Candidates")
        buy_lbl.setFont(create_font(11, QFont.Weight.Bold))
        buy_col.addWidget(buy_lbl)
        self._buy_table = self._make_candidate_table()
        buy_col.addWidget(self._buy_table)
        lists_row.addLayout(buy_col)

        sell_col = QVBoxLayout()
        sell_lbl = QLabel("🔴 Sell Candidates")
        sell_lbl.setFont(create_font(11, QFont.Weight.Bold))
        sell_col.addWidget(sell_lbl)
        self._sell_table = self._make_candidate_table()
        sell_col.addWidget(self._sell_table)
        lists_row.addLayout(sell_col)

        root.addLayout(lists_row, 1)

        # Full ranking table, for transparency / manual sanity-checking of the algorithm
        rank_lbl = QLabel("Full Ranking")
        rank_lbl.setFont(create_font(11, QFont.Weight.Bold))
        root.addWidget(rank_lbl)
        self._rank_table = QTableWidget(0, 11)
        self._rank_table.setHorizontalHeaderLabels([
            "Rank", "Ticker", "Name", "Market", "Score",
            "PER", "MA20Div", "MA50Div", "52wHighDiff%", "Ret20D%", "Ret60D%",
        ])
        self._rank_table.setFont(create_font(9, style_name="Semilight"))
        self._rank_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._rank_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._rank_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self._rank_table.verticalHeader().setVisible(False)
        root.addWidget(self._rank_table, 2)

        disclaimer = QLabel(
            "⚠️ Research/backtesting signal generator, not investment advice. "
            "Verify with backtesting and paper trading before using real capital (trading.md section 6)."
        )
        disclaimer.setFont(create_font(8, style_name="Semilight"))
        disclaimer.setStyleSheet("color:#888;")
        root.addWidget(disclaimer)

    def _make_candidate_table(self):
        tbl = QTableWidget(0, 5)
        tbl.setHorizontalHeaderLabels(["Rank", "Ticker", "Name", "Market", "Score"])
        tbl.setFont(create_font(9, style_name="Semilight"))
        tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        tbl.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        tbl.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        tbl.verticalHeader().setVisible(False)
        return tbl

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
            top_n=self.TOP_N,
            band_multiplier=self.BAND_MULTIPLIER,
        )
        self._last_result = result
        self._render(result)

    def _render(self, result):
        self._as_of_label.setText(
            f"As of {result['as_of']} — {len(result['ranked'])} ranked "
            f"({result['excluded_count']} excluded), sell threshold rank > {result['sell_threshold_rank']}"
        )
        self._fill_candidate_table(self._buy_table, result["buy_candidates"])
        self._fill_candidate_table(self._sell_table, result["sell_candidates"])
        self._fill_rank_table(result["ranked"])

    @staticmethod
    def _fill_candidate_table(tbl, rows):
        tbl.setRowCount(len(rows))
        for r, item in enumerate(rows):
            rank_str = str(item["rank"]) if item["rank"] is not None else "-"
            score_str = f"{item['score']:.2f}" if item.get("score") is not None else "-"
            for c, val in enumerate([rank_str, item["ticker"], item["name"], item["market"], score_str]):
                cell = QTableWidgetItem(val)
                cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                tbl.setItem(r, c, cell)

    def _fill_rank_table(self, ranked):
        tbl = self._rank_table
        tbl.setRowCount(len(ranked))

        def fmt(v):
            return f"{v:.2f}" if isinstance(v, (int, float)) else "-"

        for r, item in enumerate(ranked):
            raw = item["raw"]
            values = [
                str(item["rank"]), item["ticker"], item["name"], item["market"],
                f"{item['score']:.2f}",
                fmt(raw.get("value_per")), fmt(raw.get("ma20_momentum")), fmt(raw.get("ma50_momentum")),
                fmt(raw.get("high52w_proximity")), fmt(raw.get("ret_20d")), fmt(raw.get("ret_60d")),
            ]
            for c, val in enumerate(values):
                cell = QTableWidgetItem(val)
                cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                tbl.setItem(r, c, cell)

    # ---Backtest (trading.md section 6) ---
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

        lookback_years = self._lookback_combo.currentData()
        self._backtest_btn.setEnabled(False)
        self._backtest_status_label.setText(f"Fetching history for {len(tickers)} tickers... (0/{len(tickers)})")

        self._backtest_thread = RebalanceBacktestThread(
            tickers, lookback_years, self.TOP_N, self.BAND_MULTIPLIER, self.INITIAL_CAPITAL,
        )
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
        dlg = BacktestResultDialog(result, parent=self)
        dlg.exec()

    def collect_threads_to_stop(self):
        """Return every QThread this tab may have started, for MainWindow.closeEvent
        (mirrors UniverseTab.collect_threads_to_stop() / TradingHistoryTab's)."""
        bt = getattr(self, "_backtest_thread", None)
        return [bt] if bt is not None else []
