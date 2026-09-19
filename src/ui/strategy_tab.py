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

Redesign (docs/ui.md "Strategy Redesign" mockup, roadmap 7-1 Phase 1/2/4):
  - the old single-line f-string summary ("Buy 5 · Sell 3 | ... in position
    12 · flat 18") is 4 signal cards now (issue #1: a status string and an
    error string used to share the same label, indistinguishable at a
    glance) -- see _build_signal_cards/_on_summary_finished.
  - the trend-following coverage cap (_TF_SUMMARY_MAX_TICKERS) used to be
    visible only inside the summary string's parentheses (issue #2); it is
    a banner now, with a combo to widen it (_build_coverage_banner).
  - MA Cross was a disabled "Coming soon" tab (issue #3) until
    ui/ma_cross_tab.py landed the same day; it is a full sub-tab now.
"""
import logging
import time
from datetime import date, timedelta

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTabWidget,
    QFrame, QComboBox,
)
from PyQt6.QtGui import QFont

import trade_db
from threads.fetch_threads import StrategySummaryThread
from ui.auto_trading_tab import AutoTradingTab
from ui.trend_following_tab import TrendFollowingTab
from ui.ma_cross_tab import MaCrossTab
from ui.common import create_font, ThreadOwnerMixin, FONT_KPI
from ui.theme import ACCENT_TEXT

logger = logging.getLogger(__name__)

# Bounds on the trend-following half of the summary bar (roadmap 7-1 open issue):
# checking every watchlist ticker would mean one history fetch per ticker on every
# refresh, so only the top-N by market cap are checked. A coverage banner now
# shows this cap explicitly (docs/ui.md Strategy Redesign issue #2) with a combo
# to widen it, instead of it being knowable only from a summary-string suffix.
_TF_COVERAGE_CHOICES = [30, 60, 100]  # "All" is appended dynamically once universe size is known
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
        self._summary_start_ts = None
        self._tf_top_n = _TF_COVERAGE_CHOICES[0]
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(8)

        title = QLabel("Strategy")
        title.setFont(create_font(16, QFont.Weight.Bold))
        root.addWidget(title)

        root.addWidget(self._build_signal_cards())
        root.addWidget(self._build_coverage_banner())

        self._sub_tabs = QTabWidget()
        root.addWidget(self._sub_tabs, 1)

        self.auto_trading_tab = AutoTradingTab(self._universe_tab)
        self._sub_tabs.addTab(self.auto_trading_tab, "Auto Trading")

        self.trend_following_tab = TrendFollowingTab(self._universe_tab)
        self._sub_tabs.addTab(self.trend_following_tab, "Trend Following")

        self.ma_cross_tab = MaCrossTab(self._universe_tab)
        self._sub_tabs.addTab(self.ma_cross_tab, "MA Cross")

    # ── signal cards (docs/ui.md Strategy Redesign issue #1) ────────────────
    def _build_signal_cards(self) -> QFrame:
        """4 read-only KPI cards replacing the old one-line f-string summary
        (_summary_lbl): Buy/Sell candidate counts and trend-following
        in-position/flat counts, each in its own cell so a glance tells you
        which number moved -- a single concatenated string couldn't. A
        computed-at timestamp + duration and the Refresh button live in the
        same card row (docs/ui.md issue #8's "계산 시점을 알 수 없다" fix)."""
        card = QFrame()
        card.setObjectName("DashboardCard")
        outer = QVBoxLayout(card)
        outer.setContentsMargins(16, 8, 16, 6)
        outer.setSpacing(4)

        cells_row = QHBoxLayout()
        cells_row.setSpacing(0)
        self._signal_labels = {}  # key -> (value QLabel, sub QLabel)

        def cell(key, label, sub_text=""):
            box = QVBoxLayout()
            box.setSpacing(2)
            lbl = QLabel(label.upper())
            lbl.setObjectName("kpiLabel")
            box.addWidget(lbl)
            value_lbl = QLabel("—")
            value_lbl.setObjectName("kpiValue")
            value_lbl.setFont(create_font(FONT_KPI, style_name="Semilight"))
            box.addWidget(value_lbl)
            sub_lbl = QLabel(sub_text)
            sub_lbl.setObjectName("kpiSub")
            box.addWidget(sub_lbl)
            self._signal_labels[key] = (value_lbl, sub_lbl)
            cells_row.addLayout(box, 1)

        cell("buy", "Buy Candidates", "Rebalance band, top entry")
        cell("sell", "Sell Candidates", "Fell below per-market rank threshold")
        cell("tf_in", "Trend Following", "Held, channel breakout intact")
        cell("tf_flat", "Flat", "Checked, no active breakout")
        outer.addLayout(cells_row)

        status_row = QHBoxLayout()
        status_row.setSpacing(8)
        self._summary_status_lbl = QLabel("Not yet computed")
        self._summary_status_lbl.setFont(create_font(9, style_name="Semilight"))
        self._summary_status_lbl.setStyleSheet(f"color:{ACCENT_TEXT};")
        status_row.addWidget(self._summary_status_lbl)
        status_row.addStretch()
        self._summary_refresh_btn = QPushButton("\U0001f504 Refresh Signals")
        self._summary_refresh_btn.setFont(create_font(9, style_name="Semilight"))
        self._summary_refresh_btn.clicked.connect(self._refresh_summary)
        status_row.addWidget(self._summary_refresh_btn)
        outer.addLayout(status_row)

        return card

    # ── coverage banner (docs/ui.md Strategy Redesign issue #2) ─────────────
    def _build_coverage_banner(self) -> QFrame:
        """Trend-following's market-cap cap used to be visible only inside
        the old summary string's "(top 30 by mkt cap)" suffix. This banner
        states it plainly and lets the user pick a wider N -- the same
        StrategySummaryThread/run_backtest_for_ticker cost tradeoff applies
        (one history fetch per ticker checked), so widening it is an
        explicit choice, not a silent default."""
        banner = QFrame()
        banner.setStyleSheet(
            "QFrame { background:#fdf4e6; border:1px solid #f0dcb8; border-radius:6px; }"
        )
        row = QHBoxLayout(banner)
        row.setContentsMargins(10, 6, 10, 6)
        row.setSpacing(8)

        tag = QLabel("COVERAGE")
        tag.setFont(create_font(8, QFont.Weight.Bold))
        tag.setStyleSheet(
            "color:#8a5a12; background:#fff; border:1px solid #f0dcb8; border-radius:4px; padding:1px 6px;"
        )
        row.addWidget(tag)

        self._coverage_lbl = QLabel("Trend following coverage: not yet computed")
        self._coverage_lbl.setFont(create_font(9, style_name="Semilight"))
        self._coverage_lbl.setStyleSheet("color:#6b5836;")
        row.addWidget(self._coverage_lbl, 1)

        widen_lbl = QLabel("Check:")
        widen_lbl.setFont(create_font(8, style_name="Semilight"))
        widen_lbl.setStyleSheet("color:#8a5a12;")
        row.addWidget(widen_lbl)

        self._tf_top_n_combo = QComboBox()
        self._tf_top_n_combo.setFont(create_font(8, style_name="Semilight"))
        self._tf_top_n_combo.setFixedWidth(90)
        for n in _TF_COVERAGE_CHOICES:
            self._tf_top_n_combo.addItem(f"Top {n}", userData=n)
        self._tf_top_n_combo.addItem("All", userData=None)
        self._tf_top_n_combo.currentIndexChanged.connect(self._on_tf_top_n_changed)
        row.addWidget(self._tf_top_n_combo)

        return banner

    def _on_tf_top_n_changed(self, _index):
        n = self._tf_top_n_combo.currentData()
        universe_data = getattr(self._universe_tab, "all_data", None) or []
        total = sum(1 for it in universe_data if it.get("ticker") and not it.get("is_index"))
        self._tf_top_n = n if n is not None else total
        self._refresh_summary()

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
            self._summary_status_lbl.setText("Trading Universe has no data yet — refresh it first")
            return

        try:
            open_trades = trade_db.get_open_trades()
        except Exception:
            logger.warning("Failed to read open trades for the strategy summary bar", exc_info=True)
            open_trades = []
        current_holdings = {t.get("ticker") for t in open_trades if t.get("ticker")}

        stocks = [it for it in universe_data if it.get("ticker") and not it.get("is_index")]
        stocks.sort(key=lambda it: -float(it.get("market_cap", 0) or 0))
        tf_tickers = [it["ticker"] for it in stocks[:self._tf_top_n]]
        tf_start = (date.today() - timedelta(days=_TF_SUMMARY_LOOKBACK_DAYS)).strftime("%Y-%m-%d")

        self._summary_status_lbl.setText("Computing...")
        self._summary_refresh_btn.setEnabled(False)
        self._summary_start_ts = time.perf_counter()
        self._track_thread(StrategySummaryThread(
            universe_data, current_holdings,
            AutoTradingTab.TOP_N_BY_MARKET, AutoTradingTab.BAND_MULTIPLIER,
            tf_tickers, tf_start,
        ), '_summary_thread')
        self._summary_thread.finished.connect(self._on_summary_finished)
        self._summary_thread.start()

    def _on_summary_finished(self, result, error):
        self._summary_refresh_btn.setEnabled(True)
        elapsed = time.perf_counter() - self._summary_start_ts if self._summary_start_ts else 0.0
        now_str = time.strftime("%H:%M:%S")
        if error or not result:
            self._summary_status_lbl.setText(f"Computation failed ({error or 'unknown error'}) — {now_str}")
            return

        def set_cell(key, text, sub_text=None):
            val_lbl, sub_lbl = self._signal_labels[key]
            val_lbl.setText(text)
            if sub_text is not None:
                sub_lbl.setText(sub_text)

        set_cell("buy", str(result["buy_count"]))
        set_cell("sell", str(result["sell_count"]))
        set_cell("tf_in", f"{result['tf_in_position']} / {result['tf_total']}")
        set_cell("tf_flat", f"{result['tf_flat']} / {result['tf_total']}")

        errors_str = f" · {result['tf_errors']} errors" if result["tf_errors"] else ""
        self._summary_status_lbl.setText(f"Calculated {now_str} · {elapsed:.1f}s{errors_str}")

        universe_data = getattr(self._universe_tab, "all_data", None) or []
        total = sum(1 for it in universe_data if it.get("ticker") and not it.get("is_index"))
        tf_total = result["tf_total"]
        pct = (tf_total / total * 100) if total else 0.0
        self._coverage_lbl.setText(
            f"Trend following only checks the top {tf_total} stocks by market cap "
            f"({tf_total} of {total} total, {pct:.1f}%). Checking more costs one price-history "
            f"fetch per extra ticker."
        )

    # ── thread cleanup (roadmap: MainWindow.closeEvent) ─────────────────────
    def collect_threads_to_stop(self) -> list:
        out = super().collect_threads_to_stop()
        out.extend(self.auto_trading_tab.collect_threads_to_stop())
        out.extend(self.trend_following_tab.collect_threads_to_stop())
        out.extend(self.ma_cross_tab.collect_threads_to_stop())
        return out
