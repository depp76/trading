"""trend_following.md 2-5 (scoring.py): indicators, gate, percentile scores and
the weekly Top/Bottom recommendation on synthetic price paths."""
import unittest
from datetime import date

import numpy as np

from strategy.trend_following.config import StrategyParams
from strategy.trend_following.dataset import PriceBook
from strategy.trend_following.scoring import (
    compute_scores, weekly_recommendation, pct_rank_rows, INDICATORS,
)
from tests.strategy.trend_following.helpers import business_days, make_frame, path

N_DAYS = 320          # > 80% of the 252-session range window, so Range52 is defined
LAST = 5              # sessions of the final leg (the "week")


def score_book(volume_thin: float = 1e3):
    """Six names on one calendar (all liquid but THIN):
      UP     +0.4%/day throughout                -> gate passes, rising into the week
      PULL   +0.4%/day then -1%/day for 5 days   -> gate passes, pulled back (low R3/R10)
      SPIKE  +0.4%/day then +6%/day for 5 days   -> overheated (top MA20Div), gate fails
      DOWN   -0.3%/day throughout                -> MA50Div < 0 and Range52 ~ 0, gate fails
      BOUNCE -0.3%/day then +2%/day for 5 days   -> Range52 low, strongest R3: the weakest total
      THIN   like UP with negligible volume      -> illiquid, never scored
    """
    dates = business_days(date(2025, 1, 2), N_DAYS)
    n = len(dates)
    up = path(10_000.0, [(n, 0.004)])
    pull = path(10_000.0, [(n - LAST, 0.004), (LAST, -0.01)])
    spike = path(10_000.0, [(n - LAST, 0.004), (LAST, 0.06)])
    down = path(50_000.0, [(n, -0.003)])
    bounce = path(50_000.0, [(n - LAST, -0.003), (LAST, 0.02)])
    frames = {
        "UP": make_frame(dates, up), "PULL": make_frame(dates, pull), "SPIKE": make_frame(dates, spike),
        "DOWN": make_frame(dates, down), "BOUNCE": make_frame(dates, bounce),
        "THIN": make_frame(dates, up, volume=volume_thin),
    }
    names = {k: k.title() for k in frames}
    markets = {k: "KOSPI" for k in frames}
    return PriceBook.from_frames(frames, dates, markets, names), dates


class TestPctRank(unittest.TestCase):
    def test_ranks_within_mask_only(self):
        v = np.array([[3.0, 1.0, 2.0, 9.0], [np.nan, 1.0, 2.0, 3.0]])
        m = np.array([[True, True, True, False], [True, True, True, True]])
        r = pct_rank_rows(v, m)
        np.testing.assert_allclose(r[0, :3], [1.0, 1 / 3, 2 / 3])
        self.assertTrue(np.isnan(r[0, 3]))       # masked out
        self.assertTrue(np.isnan(r[1, 0]))       # NaN input
        np.testing.assert_allclose(r[1, 1:], [1 / 3, 2 / 3, 1.0])


class TestComputeScores(unittest.TestCase):
    def setUp(self):
        self.book, self.dates = score_book()
        self.p = StrategyParams()
        self.sb = compute_scores(self.book, self.p)
        self.j = {t: i for i, t in enumerate(self.sb.tickers)}
        self.t = self.sb.T - 1

    def test_indicators_match_their_definitions(self):
        sb, t, j = self.sb, self.t, self.j["UP"]
        close = self.book.close[:, j]
        self.assertAlmostEqual(sb.ma50_div[t, j], close[t] / close[t - 49:t + 1].mean() - 1.0)
        self.assertAlmostEqual(sb.ma20_div[t, j], close[t] / close[t - 19:t + 1].mean() - 1.0)
        self.assertAlmostEqual(sb.r10[t, j], close[t] / close[t - 10] - 1.0)
        self.assertAlmostEqual(sb.r3[t, j], close[t] / close[t - 3] - 1.0)
        hi = self.book.high[t - 251:t + 1, j].max()
        lo = self.book.low[t - 251:t + 1, j].min()
        self.assertAlmostEqual(sb.range52[t, j], (close[t] - lo) / (hi - lo))
        self.assertGreater(sb.range52[t, j], 0.99)
        self.assertLess(sb.range52[t, self.j["DOWN"]], 0.01)

    def test_percentiles_cover_liquid_names_only(self):
        sb, t = self.sb, self.t
        thin = self.j["THIN"]
        self.assertFalse(sb.liquid[t, thin])
        for k in INDICATORS:
            col = sb.pct[k][t]
            self.assertTrue(np.isnan(col[thin]))
            live = col[sb.liquid[t]]
            self.assertTrue(np.all((live > 0) & (live <= 1.0)))
            self.assertAlmostEqual(live.max(), 1.0)
        self.assertTrue(np.isnan(sb.total[t, thin]))

    def test_gate_and_reasons(self):
        sb, t, j = self.sb, self.t, self.j
        self.assertTrue(sb.gate[t, j["UP"]])
        self.assertTrue(sb.gate[t, j["PULL"]])
        self.assertFalse(sb.gate[t, j["SPIKE"]])
        self.assertTrue(sb.overheated[t, j["SPIKE"]])      # the top normalised MA20Div of 5 liquid names
        self.assertEqual(int(sb.overheated[t].sum()), 1)
        self.assertFalse(sb.gate[t, j["DOWN"]])
        self.assertLess(sb.ma50_div[t, j["DOWN"]], 0.0)
        self.assertFalse(sb.gate[t, j["BOUNCE"]])
        self.assertLess(sb.range52[t, j["BOUNCE"]], self.p.gate_range_min)
        self.assertFalse(sb.gate[t, j["THIN"]])

    def test_scores_compose_and_rank_within_gate(self):
        sb, t = self.sb, self.t
        p = self.p
        for tk, j in self.j.items():
            if not sb.liquid[t, j]:
                continue
            self.assertAlmostEqual(sb.trend_score[t, j], sb.pct["ma50_div"][t, j] + sb.pct["range52"][t, j])
            self.assertAlmostEqual(sb.timing_score[t, j],
                                   -sb.pct["r3"][t, j] - p.score_r_mid_weight * sb.pct["r10"][t, j])
            self.assertAlmostEqual(sb.total[t, j], sb.trend_score[t, j] + sb.timing_score[t, j])
        gated = sb.gate[t]
        self.assertTrue(np.isnan(sb.total_rank[t, ~gated]).all())
        ranks = sb.total_rank[t, gated]
        totals = sb.total[t, gated]
        self.assertEqual(list(np.argsort(ranks)), list(np.argsort(totals)))
        self.assertAlmostEqual(ranks.max(), 1.0)

    def test_pullback_scores_better_timing_than_the_riser(self):
        sb, t, j = self.sb, self.t, self.j
        self.assertGreater(sb.timing_score[t, j["PULL"]], sb.timing_score[t, j["UP"]])
        self.assertLess(sb.r3[t, j["PULL"]], 0.0)
        self.assertGreater(sb.r3[t, j["UP"]], 0.0)
        # The dead-cat bounce carries the weakest total of the universe.
        live = [k for k in j if sb.liquid[t, j[k]]]
        self.assertEqual(min(live, key=lambda k: sb.total[t, j[k]]), "BOUNCE")

    def test_options_raw_values_and_high52_prox(self):
        p_raw = StrategyParams(score_vol_normalize=False, score_range_mode="high52_prox")
        sb = compute_scores(self.book, p_raw)
        t, j = sb.T - 1, self.j["UP"]
        hi = self.book.high[t - 251:t + 1, j].max()
        self.assertAlmostEqual(sb.range52[t, j], self.book.close[t, j] / hi)
        raw = pct_rank_rows(sb.r3, sb.liquid)
        np.testing.assert_allclose(sb.pct["r3"][t], raw[t], equal_nan=True)

    def test_early_rows_are_nan_not_false_positives(self):
        sb = self.sb
        self.assertTrue(np.isnan(sb.range52[0]).all())
        self.assertFalse(sb.gate[0].any())
        self.assertTrue(np.isnan(sb.total[0]).all())


class TestWeeklyRecommendation(unittest.TestCase):
    def setUp(self):
        self.book, self.dates = score_book()
        self.p = StrategyParams()
        self.sb = compute_scores(self.book, self.p)

    def test_top_and_bottom_follow_the_week_average(self):
        sb, p = self.sb, self.p
        rec = weekly_recommendation(sb, self.book, p, n_top=10)
        self.assertEqual(rec.as_of, self.dates[-1])
        self.assertEqual(rec.week_dates, self.dates[-p.score_week_sessions:])
        self.assertEqual(rec.n_universe, 6)
        self.assertEqual(rec.n_liquid, 5)
        self.assertEqual(rec.n_gated, 2)
        self.assertIsNone(rec.regime_on)

        t = sb.T - 1
        week = sb.total[t - p.score_week_sessions + 1:]
        avg = {tk: float(np.nanmean(week[:, i])) for i, tk in enumerate(sb.tickers) if sb.liquid[t, i]}
        top = [s.ticker for s in rec.top]
        self.assertEqual(set(top), {"UP", "PULL"})
        self.assertEqual(top, sorted(top, key=lambda k: -avg[k]))
        bottom = [s.ticker for s in rec.bottom]
        self.assertEqual(set(bottom), {"SPIKE", "DOWN", "BOUNCE"})
        self.assertEqual(bottom, sorted(bottom, key=lambda k: avg[k]))
        self.assertEqual(bottom[0], "BOUNCE")
        for s in rec.top + rec.bottom:
            self.assertAlmostEqual(s.week_avg, avg[s.ticker])
            self.assertEqual(s.sessions, p.score_week_sessions)
            self.assertEqual(s.name, s.ticker.title())
            self.assertEqual(s.market, "KOSPI")
            j = sb.tickers.index(s.ticker)
            self.assertAlmostEqual(s.r20, self.book.close[t, j] / self.book.close[t - 20, j] - 1.0)
            self.assertAlmostEqual(s.r3, sb.r3[t, j])
        self.assertEqual(rec.top[0].reasons, ())
        self.assertTrue(all(s.gate for s in rec.top))
        reasons = {s.ticker: s.reasons for s in rec.bottom}
        self.assertEqual(reasons["SPIKE"], ("Overheated",))
        self.assertIn("MA50Div<0", reasons["DOWN"])
        self.assertIn("Range52<0.70", reasons["BOUNCE"])

    def test_n_top_caps_both_lists_and_keeps_them_disjoint(self):
        rec = weekly_recommendation(self.sb, self.book, self.p, n_top=1)
        self.assertEqual(len(rec.top), 1)
        self.assertEqual(len(rec.bottom), 1)
        self.assertNotEqual(rec.top[0].ticker, rec.bottom[0].ticker)

    def test_names_scored_on_too_few_sessions_are_left_out(self):
        book, _ = score_book()
        j = book.tickers.index("UP")
        book.volume[:-2, j] = 1e3      # liquid on the last two sessions only
        sb = compute_scores(book, self.p)
        rec = weekly_recommendation(sb, book, self.p)
        self.assertNotIn("UP", [s.ticker for s in rec.top + rec.bottom])
        self.assertEqual(rec.info["n_eligible"], 4)

    def test_regime_from_the_index(self):
        n = len(self.dates)
        rising = 2000.0 * np.cumprod(np.full(n, 1.001))
        falling = 2000.0 * np.cumprod(np.full(n, 0.999))
        self.assertTrue(weekly_recommendation(self.sb, self.book, self.p, index_close=rising).regime_on)
        self.assertFalse(weekly_recommendation(self.sb, self.book, self.p, index_close=falling).regime_on)


if __name__ == "__main__":
    unittest.main()
