"""threads/strategy_threads.py — QThread workers for the Strategy tab.

Every network call the Trend Following tab triggers (index/stock histories,
the KOSPI listing, investor flows, the benchmark ETF, the CD91 rate) plus the
CPU-bound matrix run happens here, never in a UI slot (CLAUDE.md threads rule).
"""
from __future__ import annotations

import logging

from PyQt6.QtCore import QThread, pyqtSignal

from strategy.trend_following import ResearchRequest, load_dataset, run_research

logger = logging.getLogger(__name__)


class TrendFollowingResearchThread(QThread):
    """Loads the dataset for `request` and runs the 6-2 matrix / 6-1 event
    study. ``finished(result | None, error)``; ``progress(text)`` carries short
    status lines for the tab's status label."""
    progress = pyqtSignal(str)
    finished = pyqtSignal(object, str)

    def __init__(self, request: ResearchRequest):
        super().__init__()
        self.request = request

    def run(self):
        req = self.request
        try:
            ds = load_dataset(
                req.start, req.end, universe_size=req.params.universe_size,
                include_flows=req.include_flows, params=req.params, progress=self.progress.emit,
            )
            result = run_research(ds, req, progress=self.progress.emit)
            self.finished.emit(result, "")
        except Exception as e:
            logger.warning("Trend Following research run failed", exc_info=True)
            self.finished.emit(None, str(e))
