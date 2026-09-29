"""ui/dialogs/trend_score.py built headlessly from a synthetic Recommendation:
summary line, both tables, number formatting, and the double-click signal."""
import unittest
from datetime import date

from PyQt6.QtWidgets import QApplication

app = QApplication.instance() or QApplication([])

from strategy.trend_following.config import StrategyParams  # noqa: E402
from strategy.trend_following.scoring import (  # noqa: E402
    compute_scores, weekly_recommendation, Recommendation, ScoredName,
)
from tests.strategy.trend_following.test_scoring import score_book  # noqa: E402
from ui.dialogs.trend_score import TrendScoreDialog, COLUMNS, COL_TICKER_ROLE, row_values  # noqa: E402


def _rec():
    book, _ = score_book()
    p = StrategyParams()
    return weekly_recommendation(compute_scores(book, p), book, p, n_top=10)


class TestTrendScoreDialog(unittest.TestCase):
    def test_populates_both_tables(self):
        rec = _rec()
        dlg = TrendScoreDialog(rec)
        self.assertEqual(dlg.top_table.rowCount(), len(rec.top))
        self.assertEqual(dlg.bottom_table.rowCount(), len(rec.bottom))
        self.assertEqual(dlg.top_table.columnCount(), len(COLUMNS))
        self.assertIn(f"As of {rec.as_of:%Y-%m-%d}", dlg.summary_lbl.text())
        self.assertIn("universe 6", dlg.summary_lbl.text())
        self.assertIn("gate 2", dlg.summary_lbl.text())
        self.assertIn("n/a", dlg.summary_lbl.text())
        # Top sorted by week average descending, bottom ascending (column 4).
        top_avgs = [float(dlg.top_table.item(r, 4).text()) for r in range(dlg.top_table.rowCount())]
        self.assertEqual(top_avgs, sorted(top_avgs, reverse=True))
        bot_avgs = [float(dlg.bottom_table.item(r, 4).text()) for r in range(dlg.bottom_table.rowCount())]
        self.assertEqual(bot_avgs, sorted(bot_avgs))
        self.assertEqual(dlg.top_table.item(0, 7).text(), "Pass")
        self.assertEqual(dlg.bottom_table.item(0, 1).text(), "Bounce  BOUNCE")
        self.assertIn("Range52<0.70", dlg.bottom_table.item(0, 7).text())

    def test_missing_values_render_as_dash_and_sort_last(self):
        s = ScoredName(ticker="X", name="X", market="KOSDAQ", close=1234.0, week_avg=float("nan"),
                       latest_total=0.5, latest_rank=float("nan"), sessions=3, liquid=True, gate=False,
                       overheated=False, ma50_div=0.1, range52=float("nan"), ma20_div=-0.02, r10=0.0, r3=0.01,
                       reasons=("No 52w range",))
        rec = Recommendation(as_of=date(2026, 9, 25), week_dates=[date(2026, 9, 25)], top=[], bottom=[s],
                             n_universe=1, n_liquid=1, n_gated=0, regime_on=True, params=StrategyParams())
        dlg = TrendScoreDialog(rec)
        self.assertEqual(dlg.top_table.rowCount(), 0)
        self.assertEqual(dlg.bottom_table.item(0, 4).text(), "-")
        self.assertEqual(dlg.bottom_table.item(0, 9).text(), "-")
        self.assertEqual(dlg.bottom_table.item(0, 3).text(), "1,234")
        self.assertEqual(dlg.bottom_table.item(0, 8).text(), "+10.0")
        self.assertEqual(dlg.bottom_table.item(0, 7).text(), "No 52w range")
        self.assertIn("Risk-on", dlg.summary_lbl.text())
        self.assertEqual(len(row_values(1, s)), len(COLUMNS))

    def test_double_click_emits_the_ticker(self):
        rec = _rec()
        dlg = TrendScoreDialog(rec)
        got = []
        dlg.ticker_activated.connect(got.append)
        table = dlg.top_table
        table._on_activated(table.model().index(0, 3))
        self.assertEqual(got, [table.item(0, 1).data(COL_TICKER_ROLE)])
        self.assertIn(got[0], {s.ticker for s in rec.top})


if __name__ == "__main__":
    unittest.main()
