"""ui/strategy_tab.py — StrategyTab (roadmap 7-1): top-level container for the
strategy-execution tabs.

Auto Trading (rebalance.md) and Trend Following (trend_following.md) are both
"run a strategy, look at signals/backtest results" tools operating on the same
Trading Universe watchlist, so they live as sub-tabs here instead of side by
side at the top level (a future MA Cross UI belongs here too -- its sub-tab is
a placeholder until that tab exists). A summary bar above the sub-tabs shows a
combined "Today's Signals" snapshot so a user does not have to open each
sub-tab just to see whether anything changed; it does not re-run either
sub-tab's own backtest button, only a fast read (rebalance) plus a
market-cap-capped background check (trend following) -- see
StrategySummaryThread in threads/fetch_threads.py for the cost tradeoff.

Reads UniverseTab.all_data on demand (same direct-reference pattern as
ui/auto_trading_tab.py and ui/trend_following_tab.py) rather than a signal.
"""
import logging
from datetime import date, timedelta

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTabWidget
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont

import trade_db
from threads.fetch_threads import StrategySummaryThread
from ui.auto_trading_tab import AutoTradingTab
from ui.trend_following_tab import TrendFollowingTab
from ui.common import create_font, ThreadOwnerMixin

logger = logging.getLogger(__name__)

# Bounds on the trend-following half of the summary bar (roadmap 7-1 open issue):
# checking every watchlist ticker would mean one history fetch per ticker on every
# refresh, so only the top-N by market cap are checked.
_TF_SUMMARY_MAX_TICKERS = 30
_TF_SUMMARY_LOOKBACK_DAYS = 400  # enough warm-up for the default entry_n=20/exit_n=10


class StrategyTab(ThreadOwnerMixin, QWidget):
    """Universe/History/Assets/Strategy top-level layout (roadmap 7-1): hosts
    Auto Trading and Trend Following as QTabWidget sub-tabs behind a shared
    "Today's Signals" summary bar."""

    def __init__(self, universe_tab, parent=None):
        super().__init__(parent)
        self._universe_tab = universe_tab
        self._summary_thread = None
        self._summary_requested_once = False
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(8)

        title = QLabel("Strategy")
        title.setFont(create_font(16, QFont.Weight.Bold))
        root.addWidget(title)

        bar = QHBoxLayout()
        self._summary_lbl = QLabel("Today's Signals: not yet computed")
        self._summary_lbl.setFont(create_font(10, QFont.Weight.Bold))
        bar.addWidget(self._summary_lbl)
        bar.addStretch()
        self._summary_refresh_btn = QPushButton("\U0001f504 Refresh Signals")
        self._summary_refresh_btn.setFont(create_font(9, style_name="Semilight"))
        self._summary_refresh_btn.clicked.connect(self._refresh_summary)
        bar.addWidget(self._summary_refresh_btn)
        root.addLayout(bar)

        self._sub_tabs = QTabWidget()
        root.addWidget(self._sub_tabs, 1)

        self.auto_trading_tab = AutoTradingTab(self._universe_tab)
        self._sub_tabs.addTab(self.auto_trading_tab, "Auto Trading")

        self.trend_following_tab = TrendFollowingTab(self._universe_tab)
        self._sub_tabs.addTab(self.trend_following_tab, "Trend Following")

        ma_cross_placeholder = QLabel("MA Cross — UI not yet implemented (strategy/ma_cross/ma_cross.md)")
        ma_cross_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ma_cross_placeholder.setFont(create_font(11, style_name="Semilight"))
        ma_cross_placeholder.setStyleSheet("color:#7f8c8d;")
        self._sub_tabs.addTab(ma_cross_placeholder, "MA Cross")

    # ── summary bar (roadmap 7-1) ────────────────────────────────────────────
    def showEvent(self, event):
        super().showEvent(event)
        if not self._summary_requested_once:
            self._summary_requested_once = True
            self._refresh_summary()

    def _refresh_summary(self):
        if self._summary_thread is not None and self._summary_thread.isRunning():
            return
        universe_data = getattr(self._universe_tab, "all_data", None) or []
        if not universe_data:
            self._summary_lbl.setText("Today's Signals: Trading Universe has no data yet — refresh it first")
            return

        try:
            open_trades = trade_db.get_open_trades()
        except Exception:
            logger.warning("Failed to read open trades for the strategy summary bar", exc_info=True)
            open_trades = []
        current_holdings = {t.get("ticker") for t in open_trades if t.get("ticker")}

        stocks = [it for it in universe_data if it.get("ticker") and not it.get("is_index")]
        stocks.sort(key=lambda it: -float(it.get("market_cap", 0) or 0))
        tf_tickers = [it["ticker"] for it in stocks[:_TF_SUMMARY_MAX_TICKERS]]
        tf_start = (date.today() - timedelta(days=_TF_SUMMARY_LOOKBACK_DAYS)).strftime("%Y-%m-%d")

        self._summary_lbl.setText("Today's Signals: computing...")
        self._summary_refresh_btn.setEnabled(False)
        self._track_thread(StrategySummaryThread(
            universe_data, current_holdings,
            AutoTradingTab.TOP_N_BY_MARKET, AutoTradingTab.BAND_MULTIPLIER,
            tf_tickers, tf_start,
        ), '_summary_thread')
        self._summary_thread.finished.connect(self._on_summary_finished)
        self._summary_thread.start()

    def _on_summary_finished(self, result, error):
        self._summary_refresh_btn.setEnabled(True)
        if error or not result:
            self._summary_lbl.setText(f"Today's Signals: computation failed ({error or 'unknown error'})")
            return
        errors_str = f" · {result['tf_errors']} errors" if result["tf_errors"] else ""
        self._summary_lbl.setText(
            f"Today's Signals — Rebalance: Buy {result['buy_count']} · Sell {result['sell_count']} "
            f"| Trend Following (top {result['tf_total']} by mkt cap): "
            f"in position {result['tf_in_position']} · flat {result['tf_flat']}{errors_str}"
        )

    # ── thread cleanup (roadmap: MainWindow.closeEvent) ─────────────────────
    def collect_threads_to_stop(self) -> list:
        out = super().collect_threads_to_stop()
        out.extend(self.auto_trading_tab.collect_threads_to_stop())
        out.extend(self.trend_following_tab.collect_threads_to_stop())
        return out
