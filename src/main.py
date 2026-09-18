import sys
import os
from dotenv import load_dotenv
import trade_db
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QTabWidget,
    QCheckBox,
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont, QShortcut, QKeySequence

from paths import ENV_FILE, APP_LOG_FILE
from ui.common import (
    create_font,
    _ACCENT_COLOR,
    _ACCENT_HOVER_COLOR,
    FONT_FAMILY_CSS,
)

load_dotenv(ENV_FILE)

import logging
logging.getLogger('matplotlib.font_manager').setLevel(logging.ERROR)

# ── Structured logging setup ──────────────────────────────────────────────────
# Root logger at INFO: WARNING+ goes to both app.log and the console, and INFO
# (e.g. _HIST_CACHE hit/miss stats, auto-backup completion) goes to app.log only.
# Leaving the root at DEBUG would make every third-party library's debug records
# get built and then thrown away by the handlers (roadmap 6-1a).
_log_formatter = logging.Formatter(
    "%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
_file_handler = logging.FileHandler(APP_LOG_FILE, encoding="utf-8")
_file_handler.setLevel(logging.INFO)
_file_handler.setFormatter(_log_formatter)
_stream_handler = logging.StreamHandler()
_stream_handler.setLevel(logging.WARNING)
_stream_handler.setFormatter(_log_formatter)
logging.basicConfig(level=logging.INFO, handlers=[_file_handler, _stream_handler])
logger = logging.getLogger(__name__)  # module-level logger for main.py
# ─────────────────────────────────────────────────────────────────────────────


# --- Phase 3-1: split out to the threads/ package ---
from threads.fetch_threads import AutoBackupThread

# --- Phase 4: split out to ui/history_tab.py, ui/assets_tab.py ---
from ui.history_tab import TradingHistoryTab
from ui.assets_tab import TradingRecordTab

# --- Phase 5: split out to ui/universe_tab.py ---
from ui.universe_tab import UniverseTab

# --- rebalance.md 3-1: weekly rebalance signal tab ---
from ui.auto_trading_tab import AutoTradingTab

# --- trend_following.md 4: Donchian breakout backtest tab ---
from ui.trend_following_tab import TrendFollowingTab


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Portfolio Management")
        self.resize(1100, 850)

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)

        # Header
        header_layout = QHBoxLayout()
        title_label = QLabel("Portfolio Management")
        title_label.setFont(create_font(18, QFont.Weight.Bold))
        header_layout.addWidget(title_label)
        
        header_layout.addStretch()

        # Global Auto Timer
        self.global_auto_timer = QTimer(self)
        self.global_auto_timer.setInterval(60000)
        self.global_auto_timer.timeout.connect(self._on_global_auto_timer)

        # Add Auto Refresh Checkbox
        self.auto_refresh_cb = QCheckBox("Auto Update (1 min)")
        self.auto_refresh_cb.setFont(create_font(10, QFont.Weight.Bold))
        self.auto_refresh_cb.setStyleSheet(f"QCheckBox {{ color: {_ACCENT_COLOR}; margin-right: 15px; }}")
        self.auto_refresh_cb.toggled.connect(self._toggle_global_auto_timer)
        header_layout.addWidget(self.auto_refresh_cb)
        
        # Check by default (this will trigger the toggled signal and start the timer)
        self.auto_refresh_cb.setChecked(True)

        main_layout.addLayout(header_layout)

        # Tab System
        self.tabs = QTabWidget()
        main_layout.addWidget(self.tabs)
        
        # 1. Trading Universe Tab
        self.universe_tab = UniverseTab()
        self.tabs.addTab(self.universe_tab, "Trading Universe")

        # 3. Trading History Tab
        self.trading_history_tab = TradingHistoryTab()
        self.tabs.addTab(self.trading_history_tab, "Trading History")

        # 4. Total Assets Tab (Trading Record)
        self.trading_record_tab = TradingRecordTab()
        self.tabs.addTab(self.trading_record_tab, "Total Assets")

        # 5. Auto Trading Tab (rebalance.md 3-1 weekly rebalance signals) —
        # reads self.universe_tab.all_data on demand (see ui/auto_trading_tab.py
        # docstring for why this is a direct reference rather than a signal).
        self.auto_trading_tab = AutoTradingTab(self.universe_tab)
        self.tabs.addTab(self.auto_trading_tab, "Auto Trading")

        # 6. Trend Following Tab (trend_following.md 4) — same on-demand read of
        # self.universe_tab.all_data, only to offer watchlist tickers in a combo.
        self.trend_following_tab = TrendFollowingTab(self.universe_tab)
        self.tabs.addTab(self.trend_following_tab, "Trend Following")

        self.trading_history_tab.total_asset_updated.connect(self.trading_record_tab.update_live_asset)
        self.trading_history_tab.status_message.connect(self._on_thread_status_message)

        # Native status bar: shows short-lived progress text from background fetch threads
        # (e.g. "Fetching KOSPI quotes... (3/5)", "Waiting for Yahoo Finance response...").
        self.statusBar().showMessage("Ready")

        # Initialise the SQLite database (creates tables + migrates legacy JSON on first run).
        trade_db.init_db()

        # Load Trading History from the database (primary source).
        self.trading_history_tab.load_from_db()

        # Status Bar (Common)
        status_layout = QHBoxLayout()
        status_layout.setContentsMargins(0, 0, 0, 0)
        self.status_label = QLabel("Ready")
        status_layout.addWidget(self.status_label)
        status_layout.addStretch()

        self.update_time_label = QLabel("Update Time: -")
        self.update_time_label.setFont(create_font(9, style_name="Semilight"))
        self.update_time_label.setStyleSheet("color: #7f8c8d;")
        status_layout.addWidget(self.update_time_label)

        main_layout.addLayout(status_layout)

        # Wire Trading Universe tab -> shared footer / native status bar / cross-tab triggers
        # (mirrors the total_asset_updated/status_message wiring above for TradingHistoryTab).
        self.universe_tab.status_text_changed.connect(self.status_label.setText)
        self.universe_tab.sync_time_changed.connect(self.update_time_label.setText)
        self.universe_tab.status_message.connect(self._on_thread_status_message)
        self.universe_tab.refresh_started.connect(self.trading_history_tab._reload_current)
        self.universe_tab.auto_lightweight_tick.connect(self.trading_history_tab._start_realtime_price_update)

        self.universe_tab.refresh_data()

        # ---Tab movement shortcuts (Ctrl+Tab / Ctrl+Shift+Tab) ---
        # QShortcut(WindowShortcut) works across the entire window regardless of focus position
        sc_next = QShortcut(QKeySequence("Ctrl+Tab"), self)
        sc_next.setContext(Qt.ShortcutContext.WindowShortcut)
        sc_next.activated.connect(self._tab_next)

        sc_prev = QShortcut(QKeySequence("Ctrl+Shift+Tab"), self)
        sc_prev.setContext(Qt.ShortcutContext.WindowShortcut)
        sc_prev.activated.connect(self._tab_prev)

        # Automatic backup of portfolio.db + custom_settings.json (roadmap 2-4).
        # Runs in the background so it never blocks startup.
        self._auto_backup_thread = AutoBackupThread()
        self._auto_backup_thread.backup_done.connect(self._on_thread_status_message)
        self._auto_backup_thread.start()

    def _tab_next(self):
        """Ctrl+Tab: Move to the Next Tab (Circular)."""
        count = self.tabs.count()
        if count > 1:
            self.tabs.setCurrentIndex((self.tabs.currentIndex() + 1) % count)

    def _tab_prev(self):
        """Ctrl+Shift+Tab: Move to the Previous Tab (Circular)."""
        count = self.tabs.count()
        if count > 1:
            self.tabs.setCurrentIndex((self.tabs.currentIndex() - 1) % count)

    def _toggle_global_auto_timer(self, checked):
        if checked:
            self.global_auto_timer.start()
            # Optionally trigger immediately when turned on if desired, 
            # but let's just wait 1 minute for the first tick to avoid spamming if user clicks back and forth.
        else:
            self.global_auto_timer.stop()

    def _on_global_auto_timer(self):
        self.universe_tab.auto_update_tick()

    def _on_thread_status_message(self, msg: str):
        """Show a short-lived progress message from a background fetch thread
        in the native status bar (roadmap 2-3)."""
        self.statusBar().showMessage(msg, 5000)

    # ---Clean shutdown ---
    def closeEvent(self, event):
        """Stop all background QThread workers so the process exits cleanly."

        Without this, ThreadPoolExecutors inside QThread.run() keep non-daemon
        Python threads alive after the Qt event loop ends, which blocks the
        terminal from returning to the shell prompt.
        """
        # Collect every QThread this window may have started
        threads_to_stop = []

        abt = getattr(self, '_auto_backup_thread', None)
        if abt is not None:
            threads_to_stop.append(abt)

        if hasattr(self, 'universe_tab'):
            threads_to_stop.extend(self.universe_tab.collect_threads_to_stop())

        if hasattr(self, 'trading_history_tab'):
            threads_to_stop.extend(self.trading_history_tab.collect_threads_to_stop())

        if hasattr(self, 'trading_record_tab'):
            threads_to_stop.extend(self.trading_record_tab.collect_threads_to_stop())

        if hasattr(self, 'auto_trading_tab'):
            threads_to_stop.extend(self.auto_trading_tab.collect_threads_to_stop())

        if hasattr(self, 'trend_following_tab'):
            threads_to_stop.extend(self.trend_following_tab.collect_threads_to_stop())

        for t in threads_to_stop:
            try:
                if t.isRunning():
                    t.quit()
                    if not t.wait(2000):   # wait up to 2 s
                        t.terminate()     # force-kill if still alive
                        t.wait(500)
            except Exception:
                logger.warning("Failed to cleanly stop background thread on close", exc_info=True)

        QApplication.instance().quit()
        event.accept()


if __name__ == "__main__":
    print("Starting Portfolio Management...")
    app = QApplication(sys.argv)
    app_font = create_font(10, style_name="Semilight")
    app.setFont(app_font)
    app.setStyleSheet(f"""
        QMainWindow {{ background-color: #f0f0f0; }}
        QTableWidget {{
            background-color: white;
            alternate-background-color: #f9f9f9;
            gridline-color: #d0d0d0;
            {FONT_FAMILY_CSS}
        }}
        QHeaderView::section {{
            background-color: #e0e0e0;
            padding: 4px;
            border: 1px solid #d0d0d0;
            font-weight: bold;
            {FONT_FAMILY_CSS}
        }}
        QLineEdit {{
            padding: 5px;
            border: 1px solid #c0c0c0;
            border-radius: 4px;
            {FONT_FAMILY_CSS}
        }}
        QComboBox {{
            padding: 5px;
            border: 1px solid #c0c0c0;
            border-radius: 4px;
            {FONT_FAMILY_CSS}
        }}
        QPushButton {{
            padding: 8px 16px;
            background-color: {_ACCENT_COLOR};
            color: white;
            border: none;
            border-radius: 4px;
            font-weight: bold;
            {FONT_FAMILY_CSS}
        }}
        QPushButton:hover {{ background-color: {_ACCENT_HOVER_COLOR}; }}
        QPushButton:disabled {{ background-color: #cccccc; }}
        QPushButton:checked {{ background-color: {_ACCENT_HOVER_COLOR}; border: 2px solid #003f7f; }}

        QTabWidget::pane {{
            border: 1px solid #d0d0d0;
            background: white;
            border-radius: 4px;
        }}
        QTabBar::tab {{
            background: #e0e0e0;
            border: 1px solid #d0d0d0;
            padding: 10px 30px;
            font-weight: bold;
            {FONT_FAMILY_CSS}
        }}
        QTabBar::tab:selected {{
            background: white;
            border-bottom: 2px solid {_ACCENT_COLOR};
        }}
    """)

    window = MainWindow()
    window.showMaximized()
    ret = app.exec()
    # Ensure all remaining non-daemon threads (ThreadPoolExecutor workers
    # started by data_fetcher) don't block process exit.
    os._exit(ret if ret else 0)

