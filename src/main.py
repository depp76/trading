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
    apply_matplotlib_font,
    FONT_FAMILY_CSS,
    FONT_TITLE,
)
from ui.theme import app_qss, ACCENT_TEXT

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

# --- roadmap 7-1: Auto Trading + Trend Following (+ future MA Cross) sub-tabs
# behind a shared "Today's Signals" summary bar ---
from ui.strategy_tab import StrategyTab


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
        title_label.setFont(create_font(FONT_TITLE, QFont.Weight.Bold))
        header_layout.addWidget(title_label)
        
        header_layout.addStretch()

        # Global Auto Timer
        self.global_auto_timer = QTimer(self)
        self.global_auto_timer.setInterval(60000)
        self.global_auto_timer.timeout.connect(self._on_global_auto_timer)

        # Add Auto Refresh Checkbox
        self.auto_refresh_cb = QCheckBox("Auto Update (1 min)")
        self.auto_refresh_cb.setFont(create_font(10, QFont.Weight.Bold))
        self.auto_refresh_cb.setStyleSheet(f"QCheckBox {{ color: {ACCENT_TEXT}; margin-right: 15px; }}")
        self.auto_refresh_cb.toggled.connect(self._toggle_global_auto_timer)
        header_layout.addWidget(self.auto_refresh_cb)
        
        # Check by default (this will trigger the toggled signal and start the timer)
        self.auto_refresh_cb.setChecked(True)

        main_layout.addLayout(header_layout)

        # Tab System
        self.tabs = QTabWidget()
        self.tabs.setObjectName("MainTabs")
        main_layout.addWidget(self.tabs)

        # Schema + one-time JSON migrations before any tab reads the DB
        # (TradingRecordTab loads asset_records in its constructor).
        trade_db.init_db()

        # 1. Trading Universe Tab
        self.universe_tab = UniverseTab()
        self.tabs.addTab(self.universe_tab, "Trading Universe")

        # 3. Trading History Tab
        self.trading_history_tab = TradingHistoryTab()
        self.tabs.addTab(self.trading_history_tab, "Trading History")

        # 4. Total Assets Tab (Trading Record)
        self.trading_record_tab = TradingRecordTab()
        self.tabs.addTab(self.trading_record_tab, "Total Assets")

        # 5. Strategy Tab (roadmap 7-1) — Auto Trading (rebalance.md 3-1) and Trend
        # Following (trend_following.md 4) sub-tabs behind a shared summary bar; same
        # on-demand read of self.universe_tab.all_data the two sub-tabs used directly
        # before this split (see ui/strategy_tab.py docstring).
        self.strategy_tab = StrategyTab(self.universe_tab)
        self.tabs.addTab(self.strategy_tab, "Strategy")

        self.trading_history_tab.total_asset_updated.connect(self.trading_record_tab.update_live_asset)
        self.trading_history_tab.status_message.connect(self._on_thread_status_message)

        # Native status bar: shows short-lived progress text from background fetch threads
        # (e.g. "Fetching KOSPI quotes... (3/5)", "Waiting for Yahoo Finance response...").
        self.statusBar().showMessage("Ready")

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

        # Automatic backup of portfolio.db + custom_settings.json + trading_record.json (roadmap 2-4).
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

        if hasattr(self, 'strategy_tab'):
            threads_to_stop.extend(self.strategy_tab.collect_threads_to_stop())

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
    # Windows' native style silently ignores parts of the stylesheet below
    # (notably QTabBar and QHeaderView theming); Fusion renders every rule
    # (docs/ui.md 6.4).
    app.setStyle("Fusion")
    app_font = create_font(style_name="Semilight")
    app.setFont(app_font)
    apply_matplotlib_font()
    app.setStyleSheet(app_qss(FONT_FAMILY_CSS))

    window = MainWindow()
    window.showMaximized()
    ret = app.exec()
    # Ensure all remaining non-daemon threads (ThreadPoolExecutor workers
    # started by data_fetcher) don't block process exit.
    os._exit(ret if ret else 0)

