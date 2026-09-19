"""tests/strategy/trend_following/test_v2.py — v2 overlays: regime filter, ATR stop,
volatility-target sizing (trend_following.md 3 "v2"), plus v1-equivalence of the defaults."""
import math
import unittest

import numpy as np
import polars as pl

from strategy.trend_following import TrendFollowingConfig, donchian_signal, run_backtest
from tests.strategy.trend_following.frames import make_frame as _frame




def _random_walk(n=200, seed=3):
    rnd = np.random.default_rng(seed)
    closes = [100.0]
    for _ in range(n - 1):
        closes.append(max(1.0, closes[-1] * (1 + rnd.normal(0.0005, 0.02))))
    return _frame(closes)


class TestConfig(unittest.TestCase):

    def test_defaults_are_v1(self):
        cfg = TrendFollowingConfig()
        self.assertTrue(cfg.is_v1)
        self.assertFalse(cfg.regime_enabled or cfg.stop_enabled or cfg.sizing_enabled)

    def test_validation(self):
        with self.assertRaises(ValueError):
            TrendFollowingConfig(stop_mode="bogus")
        with self.assertRaises(ValueError):
            TrendFollowingConfig(regime_ma_n=-1)
        with self.assertRaises(ValueError):
            TrendFollowingConfig(max_weight=0)


class TestV1Equivalence(unittest.TestCase):

    def test_default_config_matches_pure_v1_columns(self):
        df = _random_walk()
        out = donchian_signal(df, TrendFollowingConfig(entry_n=10, exit_n=5))
        # weight is exactly the position and no stop/regime columns are populated
        self.assertEqual(out["weight"].to_list(), [float(p) for p in out["position"].to_list()])
        self.assertTrue(out["stop"].is_null().all())
        self.assertTrue(out["regime_ma"].is_null().all())
        self.assertTrue(out["regime_ok"].all())
        self.assertTrue(all(r in (None, "channel") for r in out["exit_reason"].to_list()))


class TestRegimeFilter(unittest.TestCase):

    def test_entries_blocked_below_regime_ma(self):
        # falling series: any breakout must be blocked while Close < SMA
        closes = [100 - i for i in range(30)]
        closes[15] = 200.0   # one-day spike: a breakout, but far below... actually above the MA
        df = _frame(closes)
        cfg = TrendFollowingConfig(entry_n=3, exit_n=2, regime_ma_n=10)
        out = donchian_signal(df, cfg)
        # the spike day is above the 10-day SMA so it may enter; the day after is below -> exits.
        # Construct a cleaner case: block entirely when regime_ok is False on the breakout day.
        blocked = out.filter(pl.col("entry") & ~pl.col("regime_ok"))
        for row in blocked.iter_rows(named=True):
            self.assertEqual(row["position"], 0)

    def test_regime_allows_entries_above_ma(self):
        closes = [100 + 2 * i for i in range(40)]  # steady uptrend (step > High-Close gap): always above its SMA
        cfg = TrendFollowingConfig(entry_n=3, exit_n=2, regime_ma_n=10)
        out = donchian_signal(_frame(closes), cfg)
        self.assertTrue(out["regime_ok"][-1])
        self.assertEqual(out["position"][-1], 1)
        v1 = donchian_signal(_frame(closes), TrendFollowingConfig(entry_n=3, exit_n=2))
        # in an uptrend the regime filter changes nothing once the SMA warm-up is over
        self.assertEqual(out["position"].to_list()[12:], v1["position"].to_list()[12:])


class TestAtrStop(unittest.TestCase):

    def _stop_case(self, mode):
        # enter at 20, drift up to 30 (ATR(3) = 2.5 -> trailing stop 30 - 0.5*2.5 = 28.75),
        # then close 28: below the stop but above the prior-2-day low channel (27.5), so the
        # stop, not the Donchian exit, fires
        closes = [10, 10, 10, 20, 22, 24, 26, 28, 30, 28, 27.5, 27]
        cfg = TrendFollowingConfig(entry_n=3, exit_n=2, stop_atr_mult=0.5, atr_n=3, stop_mode=mode)
        return donchian_signal(_frame(closes), cfg)

    def test_trailing_stop_exits_with_reason(self):
        out = self._stop_case("trailing")
        reasons = out["exit_reason"].to_list()
        self.assertIn("stop", reasons)
        i = reasons.index("stop")
        self.assertEqual(out["position"][i], 0)
        # the stop in force that day was set from the previous close (no lookahead)
        self.assertIsNotNone(out["stop"][i])
        self.assertLess(out["Close"][i], out["stop"][i])

    def test_trailing_stop_ratchets_up(self):
        out = self._stop_case("trailing")
        stops = [s for s in out["stop"].to_list() if s is not None]
        self.assertTrue(all(b >= a - 1e-9 for a, b in zip(stops, stops[1:])) or len(stops) < 2)

    def test_fixed_stop_never_moves(self):
        out = self._stop_case("fixed")
        stops = [s for s in out["stop"].to_list() if s is not None]
        self.assertTrue(len(set(round(s, 9) for s in stops)) <= 1)

    def test_stop_off_leaves_v1_behaviour(self):
        closes = [10, 10, 10, 20, 22, 24, 26, 28, 30, 26, 25.5, 25]
        out = donchian_signal(_frame(closes), TrendFollowingConfig(entry_n=3, exit_n=2))
        self.assertNotIn("stop", out["exit_reason"].to_list())
        self.assertTrue(out["stop"].is_null().all())


class TestVolSizing(unittest.TestCase):

    def test_weight_scales_with_vol_target_and_cap(self):
        df = _random_walk(seed=11)
        # entry_n > vol_n so the vol estimate always exists at entry (otherwise weight falls back to 1.0)
        cfg_lo = TrendFollowingConfig(entry_n=15, exit_n=5, vol_target_pct=5.0, vol_n=10, max_weight=1.0)
        cfg_hi = TrendFollowingConfig(entry_n=15, exit_n=5, vol_target_pct=50.0, vol_n=10, max_weight=1.0)
        w_lo = [w for w in donchian_signal(df, cfg_lo)["weight"].to_list() if w > 0]
        w_hi = [w for w in donchian_signal(df, cfg_hi)["weight"].to_list() if w > 0]
        self.assertTrue(w_lo and w_hi)
        self.assertTrue(all(0 < w <= 1.0 for w in w_lo + w_hi))
        self.assertLess(np.mean(w_lo), np.mean(w_hi))          # lower target -> smaller size
        # 5% target on a ~30% vol walk sits well below the cap on average
        self.assertLess(np.mean(w_lo), 0.5)

    def test_warmup_entry_falls_back_to_full_weight(self):
        # entry before vol_n days of returns exist -> weight 1.0 (capped by max_weight)
        closes = [10, 10, 10, 20, 22, 24]
        cfg = TrendFollowingConfig(entry_n=3, exit_n=2, vol_target_pct=10.0, vol_n=30, max_weight=0.8)
        out = donchian_signal(_frame(closes), cfg)
        self.assertEqual(out["weight"][3], 0.8)

    def test_weight_fixed_for_trade_life(self):
        df = _random_walk(seed=5)
        out = donchian_signal(df, TrendFollowingConfig(entry_n=5, exit_n=3, vol_target_pct=20.0, vol_n=10))
        pos = out["position"].to_list()
        w = out["weight"].to_list()
        i = 0
        while i < len(pos):
            if pos[i] == 1:
                j = i
                while j < len(pos) and pos[j] == 1:
                    j += 1
                self.assertEqual(len(set(round(x, 12) for x in w[i:j])), 1)
                i = j
            else:
                i += 1

    def test_half_weight_halves_returns_and_costs(self):
        closes = [10, 10, 10, 20, 25, 30, 2, 2]
        full = run_backtest(_frame(closes), TrendFollowingConfig(entry_n=3, exit_n=2, fee_rate=0.001))
        # max_weight=0.5 with sizing off caps the weight at 0.5 (weight = min(1, max_weight))
        half = run_backtest(_frame(closes), TrendFollowingConfig(entry_n=3, exit_n=2, fee_rate=0.001, max_weight=0.5))
        sr_full = full["signals"]["strategy_return"].to_numpy()
        sr_half = half["signals"]["strategy_return"].to_numpy()
        np.testing.assert_allclose(sr_half, sr_full * 0.5, atol=1e-12)
        self.assertAlmostEqual(half["trades"][0]["weight"], 0.5)
        self.assertAlmostEqual(half["summary"]["avg_weight"], 0.5)


class TestNoLookaheadV2(unittest.TestCase):

    def test_truncating_future_does_not_change_past(self):
        df = _random_walk(n=160, seed=21)
        cfg = TrendFollowingConfig(entry_n=8, exit_n=4, regime_ma_n=20, stop_atr_mult=2.0, atr_n=5,
                                   vol_target_pct=20.0, vol_n=10)
        full = donchian_signal(df, cfg)
        for k in (30, 61, 97, 140):
            part = donchian_signal(df.head(k), cfg)
            for col in ("upper", "lower", "atr", "regime_ma", "regime_ok", "entry", "exit",
                        "position", "weight", "stop", "exit_reason"):
                a, b = part[col].to_list(), full[col].head(k).to_list()
                if col in ("atr", "regime_ma", "weight", "stop"):
                    for x, y in zip(a, b):
                        if x is None or y is None:
                            self.assertEqual(x, y, f"{col} at k={k}")
                        else:
                            self.assertTrue(math.isclose(x, y, rel_tol=1e-12), f"{col} at k={k}")
                else:
                    self.assertEqual(a, b, f"{col} differs at k={k}")


class TestBacktestV2Summary(unittest.TestCase):

    def test_exit_reason_counts_and_v2_block(self):
        closes = [10, 10, 10, 20, 22, 24, 26, 28, 30, 28, 27.5, 27]   # see TestAtrStop._stop_case
        cfg = TrendFollowingConfig(entry_n=3, exit_n=2, stop_atr_mult=0.5, atr_n=3)
        res = run_backtest(_frame(closes), cfg)
        s = res["summary"]
        self.assertEqual(s["n_stop_exits"] + s["n_channel_exits"], s["n_closed_trades"])
        self.assertGreaterEqual(s["n_stop_exits"], 1)
        self.assertEqual(s["v2"]["stop_mode"], "trailing")
        self.assertEqual(res["trades"][0]["exit_reason"], "stop")
        self.assertAlmostEqual(res["trades"][0]["price_return_pct"], (closes[res["trades"][0]["days_held"] + 3] / 20 - 1) * 100)


if __name__ == "__main__":
    unittest.main()
