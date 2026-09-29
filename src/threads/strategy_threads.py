"""threads/strategy_threads.py — QThread workers for the strategy package (the
Strategy tab's research run and the Universe tab's Trend Score button).

Every network call the Trend Following tab triggers (index/stock histories,
the KOSPI listing, investor flows, the benchmark ETF, the CD91 rate) plus the
CPU-bound matrix run happens here, never in a UI slot (CLAUDE.md threads rule).
"""
from __future__ import annotations

import logging

from PyQt6.QtCore import QThread, pyqtSignal

from strategy.trend_following import (
    ResearchCancelled, ResearchRequest, load_dataset, run_research, run_universe_scoring,
)

logger = logging.getLogger(__name__)

CANCELLED_MESSAGE = "Cancelled"


class TrendFollowingResearchThread(QThread):
    """Loads the dataset for `request` and runs the 6-2 matrix / 6-1 event
    study. ``finished(result | None, error)``; ``progress(text)`` carries short
    status lines for the tab's status label. ``cancel()`` (any thread) makes
    the loader and the matrix runner stop at their next check and finish with
    ``(None, CANCELLED_MESSAGE)``."""
    progress = pyqtSignal(str)
    finished = pyqtSignal(object, str)

    def __init__(self, request: ResearchRequest):
        super().__init__()
        self.request = request
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def is_cancelled(self) -> bool:
        return self._cancelled

    def run(self):
        req = self.request
        try:
            ds = load_dataset(
                req.start, req.end, universe_size=req.params.universe_size,
                include_flows=req.include_flows, params=req.params, progress=self.progress.emit,
                should_stop=self.is_cancelled,
            )
            result = run_research(ds, req, progress=self.progress.emit, should_stop=self.is_cancelled)
            self.finished.emit(result, "")
        except ResearchCancelled:
            logger.info("Trend Following research run cancelled by the user")
            self.finished.emit(None, CANCELLED_MESSAGE)
        except Exception as e:
            logger.warning("Trend Following research run failed", exc_info=True)
            self.finished.emit(None, str(e))


class TrendScoreThread(QThread):
    """Scores the Trading Universe's KR rows with the 2-5 "trend + pullback"
    mode and builds the weekly Top/Bottom recommendation (scoring.py).
    ``finished(Recommendation | None, error)``; ``progress(text)`` as above;
    ``cancel()`` finishes with ``(None, CANCELLED_MESSAGE)``."""
    progress = pyqtSignal(str)
    finished = pyqtSignal(object, str)

    def __init__(self, items: list, params=None, n_top: int = 10):
        super().__init__()
        self.items = list(items)
        self.params = params
        self.n_top = n_top
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def is_cancelled(self) -> bool:
        return self._cancelled

    def run(self):
        try:
            rec = run_universe_scoring(self.items, params=self.params, n_top=self.n_top,
                                       progress=self.progress.emit, should_stop=self.is_cancelled)
            self.finished.emit(rec, "")
        except ResearchCancelled:
            logger.info("Trend score run cancelled by the user")
            self.finished.emit(None, CANCELLED_MESSAGE)
        except Exception as e:
            logger.warning("Trend score run failed", exc_info=True)
            self.finished.emit(None, str(e))
