"""trend_following.md 2-3/2-4/3/4: the engine's t+1 fills, sizing caps,
exits, annual reset, cost ledger and the variant switches on synthetic data."""
import unittest
from datetime import date

import numpy as np

from strategy.trend_following.backtest import run_backtest, TRIM_REASON
from strategy.trend_following.config import StrategyParams, VARIANTS
from strategy.trend_following.costs import CostModel
from strategy.trend_following.signals import compute_features
from tests.strategy.trend_following.helpers import synthetic_dataset


def _run(ds, vid, params=None, mult=1.0):
    p = params or StrategyParams()
    feats = compute_features(ds, p)
    return run_backtest(ds, feats, p, VARIANTS[vid], CostModel().scaled(mult)), feats


class TestEngineFills(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ds = synthetic_dataset()
        cls.res, cls.feats = _run(cls.ds, "A0")

    def test_window_and_nav_start(self):
        self.assertEqual(self.res.dates[0], date(2021, 1, 4))
        self.assertAlmostEqual(self.res.nav[0], 1.0, places=6)
        self.assertEqual(len(self.res.nav), len(self.ds.eval_dates))
        self.assertTrue(np.isfinite(self.res.nav).all())

    def test_up_is_bought_at_the_next_open_after_a_breakout_close(self):
        b = self.ds.stocks
        j = b.col("UP")
        # UP breaks out on the first evaluation day; the fill is the next session's open, tick-snapped up.
        t0 = self.ds.start_idx
        self.assertTrue(self.feats.breakout[t0, j])
        held_from = next(i for i, n in enumerate(self.res.n_positions) if n > 0)
        self.assertEqual(held_from, 1)
        fill_day = t0 + 1
        raw_open = b.open[fill_day, j]
        expected = CostModel().fill_price(b.dates[fill_day], "KOSPI", raw_open, "buy")
        entries = [t for t in self.res.trades if t.ticker == "UP"]
        self.assertTrue(entries)                        # year-end trims produce trade rows
        self.assertAlmostEqual(entries[0].entry_price, expected)
        self.assertGreaterEqual(expected, raw_open)

    def test_single_name_cap_binds(self):
        b = self.ds.stocks
        j = b.col("UP")
        t0 = self.ds.start_idx
        px = b.close[t0, j]
        # UP is never exited, only trimmed at year end, so the shares bought at entry are the
        # trimmed shares plus what is still held. Sizing at decision: floor(0.2 * seed / close)
        # (the 1%/(2 x ATR) risk budget would allow far more for this low-range path).
        held_qty_at_entry = sum(t.qty for t in self.res.trades if t.ticker == "UP") \
            + sum(p["qty"] for p in self.res.open_positions if p["ticker"] == "UP")
        self.assertEqual(held_qty_at_entry, int(np.floor(0.2 * 10_000_000 / px)))

    def test_flat_is_never_traded_and_spike_exits_by_channel(self):
        tickers = {t.ticker for t in self.res.trades} | {p["ticker"] for p in self.res.open_positions}
        self.assertNotIn("FLAT", tickers)
        spike = [t for t in self.res.trades if t.ticker == "SPIKE" and t.reason != TRIM_REASON]
        self.assertTrue(spike)
        self.assertEqual(spike[0].reason, "channel")
        self.assertGreater(spike[0].pnl, 0)

    def test_max_positions_respected(self):
        self.assertLessEqual(self.res.n_positions.max(), StrategyParams().max_positions)

    def test_cost_ledger_matches_trades_and_open_positions(self):
        ledger = self.res.total_costs.total
        from_trades = sum(t.cost.total for t in self.res.trades)
        from_open = sum(p["entry_costs"] for p in self.res.open_positions)
        self.assertAlmostEqual(ledger, from_trades + from_open, places=4)
        self.assertGreater(self.res.total_costs.tax, 0)
        self.assertGreater(self.res.total_costs.commission, 0)
        self.assertGreater(self.res.total_costs.slippage, 0)


class TestAnnualReset(unittest.TestCase):
    def setUp(self):
        self.ds = synthetic_dataset()
        self.res, _ = _run(self.ds, "A0")

    def test_equity_restarts_at_seed_and_excess_is_banked(self):
        seed = StrategyParams().seed_krw
        last_2021 = max(i for i, d in enumerate(self.res.dates) if d.year == 2021)
        eq = self.res.equity[last_2021]
        self.assertLess(abs(eq - seed) / seed, 0.02)      # integer-share leftovers only
        self.assertGreater(self.res.banked_by_year.get(2021, 0.0), 0.0)
        self.assertTrue(any(t.reason == TRIM_REASON for t in self.res.trades))

    def test_nav_is_continuous_across_the_reset(self):
        last_2021 = max(i for i, d in enumerate(self.res.dates) if d.year == 2021)
        nav_before = self.res.nav[last_2021 - 1]
        nav_at = self.res.nav[last_2021]
        nav_after = self.res.nav[last_2021 + 1]
        self.assertLess(abs(nav_at / nav_before - 1.0), 0.05)
        self.assertLess(abs(nav_after / nav_at - 1.0), 0.05)
        self.assertGreater(self.res.nav[-1], 1.5)          # UP compounds strongly

    def test_without_reset_no_trims(self):
        p = StrategyParams(annual_reset=False)
        res, _ = _run(self.ds, "A0", p)
        self.assertFalse(any(t.reason == TRIM_REASON for t in res.trades))
        self.assertEqual(res.banked_by_year, {})
        self.assertGreater(res.equity[-1], p.seed_krw)


class TestVariantSwitches(unittest.TestCase):
    def test_regime_gate_blocks_entries_in_a_falling_market(self):
        ds = synthetic_dataset(index_up=False)
        a0, _ = _run(ds, "A0")
        a1, feats = _run(ds, "A1")
        self.assertFalse(feats.regime_on[ds.start_idx:].any())
        self.assertGreater(len(a0.trades) + len(a0.open_positions), 0)
        self.assertEqual(len(a1.trades) + len(a1.open_positions), 0)
        self.assertTrue(np.allclose(a1.exposure, 0.0))

    def test_cash_earns_the_risk_free_rate_when_idle(self):
        ds = synthetic_dataset(index_up=False, rf=0.02)
        a1, _ = _run(ds, "A1")
        years = (ds.eval_dates[-1] - ds.eval_dates[0]).days / 365.0
        self.assertAlmostEqual(a1.nav[-1], (1.02) ** years, delta=0.01)

    def test_volume_filter_only_enters_on_spike_days(self):
        ds = synthetic_dataset()
        a2, feats = _run(ds, "A2")
        j = ds.stocks.col("UP")
        entries = [t for t in a2.trades if t.ticker == "UP"]
        self.assertTrue(entries or a2.open_positions)
        entry_day = entries[0].entry_date if entries else date.fromisoformat(a2.open_positions[0]["entry_date"])
        t_fill = ds.dates.index(entry_day)
        self.assertTrue(feats.vol_ok[t_fill - 1, j])       # decision day had the volume spike

    def test_flow_variants_need_flows(self):
        ds = synthetic_dataset(with_flows=False)
        a3, _ = _run(ds, "A3")
        self.assertEqual(len(a3.trades) + len(a3.open_positions), 0)
        self.assertTrue(a3.notes)

    def test_flow_exit_fires_before_channel_on_a_slow_drift(self):
        ds = synthetic_dataset(with_flows=True)
        a4, _ = _run(ds, "A4")
        a5, _ = _run(ds, "A5")
        d5 = [t for t in a5.trades if t.ticker == "DRIFT" and t.reason != TRIM_REASON]
        self.assertTrue(d5)
        self.assertEqual(d5[0].reason, "flow")
        d4 = [t for t in a4.trades if t.ticker == "DRIFT" and t.reason != TRIM_REASON]
        if d4:
            self.assertGreater(d4[0].exit_date, d5[0].exit_date)

    def test_variant_b_holds_top_names_by_high_proximity(self):
        ds = synthetic_dataset()
        b, _ = _run(ds, "B")
        self.assertGreater(len(b.trades) + len(b.open_positions), 0)
        held = {t.ticker for t in b.trades} | {p["ticker"] for p in b.open_positions}
        self.assertIn("UP", held)
        self.assertNotIn("FLAT", held)


class TestCostSensitivity(unittest.TestCase):
    def test_more_costs_never_help(self):
        ds = synthetic_dataset()
        ends = [_run(ds, "A0", mult=m)[0].nav[-1] for m in (0.0, 1.0, 2.0)]
        self.assertGreaterEqual(ends[0], ends[1])
        self.assertGreaterEqual(ends[1], ends[2])
        self.assertEqual(_run(ds, "A0", mult=0.0)[0].total_costs.total, 0.0)


if __name__ == "__main__":
    unittest.main()
