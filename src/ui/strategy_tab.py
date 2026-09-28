"""ui/strategy_tab.py — StrategyTab: the "Strategy" top-level tab. One sub-tab
per strategy package under src/strategy/ (only Trend Following for now); the
sub-tabs own their worker threads and this widget just merges
collect_threads_to_stop() for MainWindow.closeEvent.
"""
from __future__ import annotations

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QTabWidget

from ui.common import ThreadOwnerMixin
from ui.trend_following_tab import TrendFollowingTab


class StrategyTab(ThreadOwnerMixin, QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.sub_tabs = QTabWidget()
        self.trend_following_tab = TrendFollowingTab()
        self.sub_tabs.addTab(self.trend_following_tab, "Trend Following")
        root.addWidget(self.sub_tabs)

    def collect_threads_to_stop(self) -> list:
        out = super().collect_threads_to_stop()
        out.extend(self.trend_following_tab.collect_threads_to_stop())
        return out
