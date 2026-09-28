"""tests/strategy/trend_following/test_engine.py — the daily loop of the KR Donchian
portfolio strategy (trend_following.md 2-3, 2-4, 2-5, 3-2): cash >= 0, holdings <= max,
open fills with slippage and costs, cancelled orders on suspended stocks, the market
filter, the weekly entry rule, the 20% trim, harvest / top-up and no-lookahead.

Calendar: weekdays from Monday 2025-01-06, so day index 4, 9, 14, ... are Fridays
(week ends) and 5, 10, 15, ... the following Mondays. Short channels (5/3, ATR 5)
keep the warm-up to five days.
"""
from dataclasses import replace
from datetime import date
import math
import unittest

import numpy as np
import polars as pl

from strategy.trend_following import KrTrendConfig, donchian_signal, run_kr_trend
from strategy.trend_following.config import TrendFollowingConfig
from tests.strategy.trend_following.v1_frames import bdays, ohlcv, random_walk

START = date(2025, 1, 6)
CFG = KrTrendConfig(entry_n=5, exit_n=3, atr_n=5, index_regime_ma_n=0, universe_mode="fixed", max_positions=3)
FEE, TAX, SLIP = 0.00015, 0.0018, 0.001


def _flat_breakout(n=40, breakout_day=9, after_step=100.0, breakout_close=11_000.0, monday_open=11_100.0):
    """Flat at 10,000, closes at `breakout_close` on `breakout_day` (a Friday by default),
    then keeps rising `after_step` a day. Monday's open is pinned to `monday_open`."""
    closes = [10_000.0] * breakout_day + [breakout_close] + [breakout_close + after_step * (k + 1) for k in range(n - breakout_day - 1)]
    opens = list(closes)
    if breakout_day + 1 < n:
        opens[breakout_day + 1] = monday_open
    return ohlcv(bdays(START, n), closes, opens=opens)


def _atr_on(df, i, cfg=CFG):
    sig = donchian_signal(df, TrendFollowingConfig(entry_n=cfg.entry_n, exit_n=cfg.exit_n, atr_n=cfg.atr_n))
    return float(sig.get_column("atr")[i])


def _run(hist, cfg=CFG, **kw):
    return run_kr_trend(hist, None, cfg, **kw)


class TestFillsAndSizing(unittest.TestCase):

    def test_breakout_buys_at_next_open_with_slippage_and_fee(self):
        df = _flat_breakout()
        res = _run({"A": df})
        trades = [t for t in res["trades"] if t["ticker"] == "A"]
        self.assertEqual(len(trades), 1)
        t = trades[0]
        self.assertEqual(t["entry_date"], "2025-01-20")                 # Monday after the Friday breakout
        fill = 11_100.0 * (1 + SLIP)
        self.assertAlmostEqual(t["entry_price"], fill)
        atr = _atr_on(df, 9)
        weight = min(0.0075 / (3.0 * atr / 11_000.0), 0.20)
        qty = math.floor(10_000_000.0 * weight / fill)
        self.assertEqual(t["qty"], qty)
        daily = res["daily"]
        cash_mon = daily.filter(pl.col("Date") == date(2025, 1, 20)).get_column("cash")[0]
        self.assertAlmostEqual(cash_mon, 10_000_000.0 - qty * fill * (1 + FEE))
        self.assertEqual(daily.filter(pl.col("Date") < date(2025, 1, 20)).get_column("n_positions").max(), 0)

    def test_signal_close_fills_on_the_decision_day(self):
        df = _flat_breakout()
        res = _run({"A": df}, replace(CFG, fill_at="signal_close"))
        t = res["trades"][0]
        self.assertEqual(t["entry_date"], "2025-01-17")
        self.assertAlmostEqual(t["entry_price"], 11_000.0 * (1 + SLIP))

    def test_zero_share_candidate_is_skipped(self):
        df = _flat_breakout(breakout_close=11_000.0, monday_open=11_100.0)
        cfg = replace(CFG, base_capital=50_000.0)                       # 20% cap = 10,000 < one share
        res = _run({"A": df}, cfg)
        self.assertEqual(res["trades"], [])
        self.assertGreaterEqual(res["years"][0]["n_skipped_zero_qty"], 1)
        self.assertEqual(res["daily"].get_column("n_positions").max(), 0)

    def test_channel_exit_sells_at_next_open(self):
        n = 30
        closes = [10_000.0] * 9 + [11_000.0] + [11_100.0, 11_200.0, 11_300.0, 11_400.0, 11_500.0]   # entry Mon d10
        closes += [11_450.0, 10_500.0]                                    # d16 close < lowest low of d13-15
        closes += [10_400.0] * (n - len(closes))
        opens = list(closes)
        opens[17] = 10_300.0
        df = ohlcv(bdays(START, n), closes, opens=opens)
        res = _run({"A": df})
        t = res["trades"][0]
        self.assertEqual((t["exit_date"], t["exit_reason"], t["partial"]), ("2025-01-29", "channel", False))
        exit_fill = 10_300.0 * (1 - SLIP)
        self.assertAlmostEqual(t["exit_price"], exit_fill)
        gross_in = t["qty"] * t["entry_price"]
        expected_cost = gross_in * FEE + t["qty"] * exit_fill * (FEE + TAX)
        self.assertAlmostEqual(t["cost"], expected_cost)
        self.assertAlmostEqual(t["pnl"], t["qty"] * exit_fill - gross_in - expected_cost)
        self.assertEqual(res["summary"]["n_trades"], 1)
        self.assertLess(res["summary"]["win_rate_pct"], 100.0)

    def test_trailing_stop_exit(self):
        cfg = replace(CFG, stop_atr_mult=1.0, exit_n=2)
        n = 30
        # entry on Mon d10 at 11,100; then a gentle rise; d16 drops below the 1-ATR stop but stays
        # above the 2-day low thanks to a wide bar on d15
        closes = [10_000.0] * 9 + [11_000.0] + [11_100.0, 11_150.0, 11_200.0, 11_250.0, 11_300.0]
        closes += [11_280.0, 10_950.0] + [10_900.0] * (n - 17)
        lows = [c * 0.995 for c in closes]
        lows[15] = 10_000.0                                                # the wide bar: lower(d16) = 10,000
        df = ohlcv(bdays(START, n), closes, lows=lows)
        res = run_kr_trend({"A": df}, None, cfg)
        exits = [t for t in res["trades"] if t["exit_reason"] in ("channel", "stop")]
        self.assertEqual(len(exits), 1)
        self.assertEqual(exits[0]["exit_reason"], "stop")
        self.assertEqual(exits[0]["exit_date"], "2025-01-29")
        # the 1-ATR stop makes the risk-based weight hit the 20% cap, so the rise also triggered a trim
        self.assertTrue(any(t["exit_reason"] == "trim" for t in res["trades"]))


class TestSchedule(unittest.TestCase):

    def test_weekly_held_breakout_rule(self):
        n = 25
        # Wednesday d7 breaks out (11,000 > 10,050) but Friday d9 closes back under the
        # breakout day's upper -> no entry (condition 1 fails)
        closes = [10_000.0] * 7 + [11_000.0, 10_500.0, 10_040.0] + [10_040.0] * (n - 10)
        res = _run({"A": ohlcv(bdays(START, n), closes)})
        self.assertEqual(res["trades"], [])
        # Friday closes at 10,100: above the Wednesday upper (10,050) -> entry under held_breakout,
        # but not above Friday's own upper (11,055) -> no entry under "strict"
        closes = [10_000.0] * 7 + [11_000.0, 10_500.0, 10_100.0] + [10_100.0 + 50 * k for k in range(n - 10)]
        df = ohlcv(bdays(START, n), closes)
        held = _run({"A": df})
        self.assertEqual([t["entry_date"] for t in held["trades"]], ["2025-01-20"])
        strict = _run({"A": df}, replace(CFG, weekly_entry_rule="strict"))
        self.assertEqual(strict["trades"], [])

    def test_midweek_breakout_waits_for_friday_unless_entry_check_daily(self):
        df = _flat_breakout(breakout_day=7, monday_open=11_100.0)        # Wednesday breakout
        weekly = _run({"A": df})
        self.assertEqual(weekly["trades"][0]["entry_date"], "2025-01-20")   # Monday after Friday's decision
        daily = _run({"A": df}, replace(CFG, entry_check="daily"))
        self.assertEqual(daily["trades"][0]["entry_date"], "2025-01-16")    # Thursday open

    def test_market_filter_blocks_new_buys_only(self):
        n = 30
        df = _flat_breakout(n=n)
        dates = bdays(START, n)
        falling = ohlcv(dates, [3000.0 - 10 * k for k in range(n)])
        rising = ohlcv(dates, [2000.0 + 10 * k for k in range(n)])
        cfg = replace(CFG, index_regime_ma_n=3)
        self.assertEqual(run_kr_trend({"A": df}, falling, cfg)["trades"], [])
        self.assertEqual(len(run_kr_trend({"A": df}, rising, cfg)["trades"]), 1)
        with self.assertRaises(ValueError):
            run_kr_trend({"A": df}, None, cfg)

    def test_regime_false_while_index_sma_undefined(self):
        n = 30
        df = _flat_breakout(n=n)
        idx = ohlcv(bdays(START, n), [2000.0 + 10 * k for k in range(n)])
        res = run_kr_trend({"A": df}, idx, replace(CFG, index_regime_ma_n=200))
        self.assertFalse(res["daily"].get_column("regime_ok").any())
        self.assertEqual(res["trades"], [])

    def test_suspended_stock_cancels_the_buy(self):
        df = _flat_breakout(n=25)
        vols = [100_000.0] * 25
        vols[10] = 0.0                                                    # Monday: no trading
        df = df.with_columns(pl.Series("Volume", vols))
        res = _run({"A": df})
        daily = res["daily"]
        self.assertEqual(daily.filter(pl.col("Date") == date(2025, 1, 20)).get_column("n_positions")[0], 0)
        self.assertEqual(res["years"][0]["n_cancelled"], 1)
        # the rising close keeps breaking out, so the next weekly decision buys on the following Monday
        self.assertEqual([t["entry_date"] for t in res["trades"]], ["2025-01-27"])

    def test_suspended_stock_defers_the_sell(self):
        n = 30
        closes = [10_000.0] * 9 + [11_000.0] + [11_100.0, 11_200.0, 11_300.0, 11_400.0, 11_500.0]
        closes += [11_450.0, 10_500.0, 10_400.0] + [10_300.0] * (n - 18)     # d16 exit signal; d17 suspended
        vols = [100_000.0] * n
        vols[17] = 0.0
        df = ohlcv(bdays(START, n), closes, volumes=vols)
        res = _run({"A": df})
        t = res["trades"][0]
        self.assertEqual(t["exit_date"], "2025-01-30")                    # d18: one session later
        self.assertEqual(res["years"][0]["n_cancelled"], 1)

    def test_exit_frees_the_slot_for_a_same_day_buy(self):
        n = 30
        # A: enters Mon d10, exit signal on Fri d14 (close < 3-day low). B: breaks out on Fri d14.
        a = [10_000.0] * 9 + [11_000.0] + [11_100.0, 11_200.0, 11_300.0, 11_400.0, 10_000.0] + [9_900.0] * (n - 15)
        b = [10_000.0] * 14 + [11_000.0] + [11_100.0 + 100 * k for k in range(n - 15)]
        hist = {"A": ohlcv(bdays(START, n), a), "B": ohlcv(bdays(START, n), b)}
        res = _run(hist, replace(CFG, max_positions=1))
        by = {t["ticker"]: t for t in res["trades"]}
        self.assertEqual(by["A"]["exit_date"], "2025-01-27")
        self.assertEqual(by["B"]["entry_date"], "2025-01-27")
        self.assertEqual(res["daily"].get_column("n_positions").max(), 1)

    def test_weekly_trim_caps_a_position_at_max_weight(self):
        n = 30
        closes = [10_000.0] * 9 + [11_000.0] + [12_000.0 + 2_000.0 * k for k in range(n - 10)]   # to 50,000+
        df = ohlcv(bdays(START, n), closes)
        res = _run({"A": df})
        trims = [t for t in res["trades"] if t["exit_reason"] == "trim"]
        self.assertTrue(trims)
        first = trims[0]
        self.assertTrue(first["partial"])
        self.assertEqual(date.fromisoformat(first["exit_date"]).weekday(), 0)   # a Monday fill
        daily = res["daily"]
        dates = bdays(START, n)
        mon_i = dates.index(date.fromisoformat(first["exit_date"]))
        fri_i = mon_i - 1
        fri = daily.filter(pl.col("Date") == dates[fri_i])
        mon = daily.filter(pl.col("Date") == dates[mon_i])
        # the Friday before the trim: exposure > 20%; on Monday the trimmed position is worth at most
        # 20% of Friday's equity marked at Monday's close
        self.assertGreater(fri.get_column("exposure")[0], 0.20)
        e_fri = fri.get_column("equity")[0]
        self.assertLessEqual(mon.get_column("equity")[0] - mon.get_column("cash")[0],
                             0.20 * e_fri * closes[mon_i] / closes[fri_i] + 1e-6)
        # the trim leaves qty = floor(E_fri * 0.2 / close_fri) in the (still open) position
        open_pos = [t for t in res["trades"] if t["exit_reason"] is None]
        self.assertEqual(len(open_pos), 1)
        expected_left = math.floor(e_fri * 0.20 / closes[fri_i])
        self.assertEqual(open_pos[0]["qty"] + sum(t["qty"] for t in trims[1:]), expected_left)


class TestAnnualCycle(unittest.TestCase):

    def _two_years(self, closes_fn):
        dates = bdays(date(2025, 1, 6), 300)               # through mid-March 2026
        closes = [closes_fn(k) for k in range(len(dates))]
        return dates, ohlcv(dates, closes)

    def test_profit_year_is_harvested_at_the_close(self):
        dates, df = self._two_years(lambda k: 10_000.0 if k < 9 else 11_000.0 * 1.004 ** (k - 9))
        res = _run({"A": df})
        y2025 = next(y for y in res["years"] if y["year"] == 2025)
        self.assertTrue(y2025["complete"])
        self.assertGreater(y2025["value_end"], 10_000_000.0)
        self.assertGreater(y2025["withdrawal"], 0.0)
        self.assertAlmostEqual(y2025["return_pct"], (y2025["value_end"] / 10_000_000.0 - 1) * 100.0)
        self.assertLess(y2025["return_net_pct"], y2025["return_pct"])
        harvest = [t for t in res["trades"] if t["exit_reason"] == "harvest"]
        self.assertEqual(len(harvest), 1)
        self.assertEqual(harvest[0]["exit_date"], "2025-12-31")
        self.assertTrue(harvest[0]["partial"])
        self.assertAlmostEqual(harvest[0]["exit_price"], float(df.filter(pl.col("Date") == date(2025, 12, 31)).get_column("Close")[0]))
        daily = res["daily"]
        ye = daily.filter(pl.col("Date") == date(2025, 12, 31))
        self.assertAlmostEqual(ye.get_column("equity")[0], 10_000_000.0, places=3)     # exactly base after the harvest
        self.assertAlmostEqual(ye.get_column("flow")[0], -y2025["withdrawal"])
        # time-weighted: the withdrawal is a flow, not a loss
        prev = daily.filter(pl.col("Date") == date(2025, 12, 30)).get_column("equity")[0]
        pre_flow = ye.get_column("equity_pre_flow")[0]
        self.assertAlmostEqual(pre_flow, 10_000_000.0 + y2025["withdrawal"], places=3)
        self.assertAlmostEqual(ye.get_column("daily_return")[0], pre_flow / prev - 1.0, places=9)
        self.assertGreater(ye.get_column("daily_return")[0], -0.01)
        y2026 = next(y for y in res["years"] if y["year"] == 2026)
        self.assertFalse(y2026["complete"])
        self.assertAlmostEqual(y2026["capital_start"], 10_000_000.0)
        self.assertEqual(y2026["topup"], 0.0)
        s = res["summary"]
        self.assertAlmostEqual(s["cumulative_withdrawal"], y2025["withdrawal"])
        self.assertAlmostEqual(s["net_pnl"], s["cumulative_withdrawal"] + s["final_equity"] - 10_000_000.0)

    def test_loss_year_is_topped_up_next_year(self):
        # breakout, then a slow slide -> channel exit at a loss, cash for the rest of the year
        def closes(k):
            if k < 9:
                return 10_000.0
            if k < 14:
                return 11_000.0 + 50.0 * (k - 9)
            return max(9_000.0, 11_250.0 - 40.0 * (k - 13))
        dates, df = self._two_years(closes)
        res = _run({"A": df})
        y2025 = next(y for y in res["years"] if y["year"] == 2025)
        self.assertLess(y2025["value_end"], 10_000_000.0)
        self.assertEqual(y2025["withdrawal"], 0.0)
        y2026 = next(y for y in res["years"] if y["year"] == 2026)
        self.assertAlmostEqual(y2026["topup"], 10_000_000.0 - y2025["value_end"])
        self.assertAlmostEqual(y2026["capital_start"], 10_000_000.0)
        daily = res["daily"]
        first_2026 = daily.filter(pl.col("Date") >= date(2026, 1, 1)).head(1)   # the synthetic calendar has no holidays
        self.assertAlmostEqual(first_2026.get_column("flow")[0], y2026["topup"])
        self.assertAlmostEqual(first_2026.get_column("daily_return")[0], 0.0)      # all cash, flow excluded
        self.assertAlmostEqual(res["summary"]["cumulative_topup"], y2026["topup"])
        no_topup = _run({"A": df}, replace(CFG, topup_on_loss=False))
        self.assertEqual(next(y for y in no_topup["years"] if y["year"] == 2026)["topup"], 0.0)
        self.assertAlmostEqual(next(y for y in no_topup["years"] if y["year"] == 2026)["capital_start"], y2025["value_end"])

    def test_harvest_mode_none_reinvests(self):
        dates, df = self._two_years(lambda k: 10_000.0 if k < 9 else 11_000.0 * 1.004 ** (k - 9))
        res = _run({"A": df}, replace(CFG, harvest_mode="none"))
        y2025 = next(y for y in res["years"] if y["year"] == 2025)
        self.assertEqual(y2025["withdrawal"], 0.0)
        self.assertEqual([t for t in res["trades"] if t["exit_reason"] == "harvest"], [])
        y2026 = next(y for y in res["years"] if y["year"] == 2026)
        self.assertAlmostEqual(y2026["capital_start"], y2025["value_end"])


class TestInvariants(unittest.TestCase):

    def _random_universe(self, n_days=520, n_tickers=12, seed=0):
        dates = bdays(date(2025, 1, 6), n_days)
        hist = {f"{k:06d}": random_walk(dates, seed + k) for k in range(n_tickers)}
        idx = random_walk(dates, 999, drift=0.0003, vol=0.01, p0=2500.0)
        return hist, idx

    def test_cash_non_negative_and_holdings_within_limit(self):
        hist, idx = self._random_universe()
        cfg = replace(CFG, index_regime_ma_n=20, max_positions=3)
        res = run_kr_trend(hist, idx, cfg)
        daily = res["daily"]
        self.assertGreaterEqual(daily.get_column("cash").min(), -1e-6)
        self.assertLessEqual(daily.get_column("n_positions").max(), 3)
        self.assertGreater(res["summary"]["n_trades"], 0)
        for t in res["trades"]:
            self.assertGreater(t["qty"], 0)
            if t["exit_date"]:
                self.assertLessEqual(t["entry_date"], t["exit_date"])
        # equity identity: cash + holdings, and the recorded return chain reproduces equity_pre_flow
        eq = daily.get_column("equity").to_numpy()
        pre = daily.get_column("equity_pre_flow").to_numpy()
        ret = daily.get_column("daily_return").to_numpy()
        flow = daily.get_column("flow").to_numpy()
        for i in range(1, len(eq)):
            base = eq[i - 1] + max(flow[i], 0.0)
            self.assertAlmostEqual(pre[i], base * (1 + ret[i]), places=3)

    def test_no_lookahead_truncating_the_future_does_not_change_the_past(self):
        hist, idx = self._random_universe(n_days=400)
        cfg = replace(CFG, index_regime_ma_n=20)
        cutoff = date(2025, 9, 17)                                          # a Wednesday
        full = run_kr_trend(hist, idx, cfg)
        trunc = run_kr_trend({t: df.filter(pl.col("Date") <= cutoff) for t, df in hist.items()},
                             idx.filter(pl.col("Date") <= cutoff), cfg)
        a = full["daily"].filter(pl.col("Date") <= cutoff)
        b = trunc["daily"]
        self.assertEqual(a.height, b.height)
        np.testing.assert_allclose(a.get_column("equity").to_numpy(), b.get_column("equity").to_numpy(), rtol=1e-12)
        np.testing.assert_array_equal(a.get_column("n_positions").to_numpy(), b.get_column("n_positions").to_numpy())
        closed_full = sorted((t["ticker"], t["entry_date"], t["exit_date"]) for t in full["trades"]
                             if t["exit_date"] and t["exit_date"] <= cutoff.isoformat())
        closed_trunc = sorted((t["ticker"], t["entry_date"], t["exit_date"]) for t in trunc["trades"] if t["exit_date"])
        self.assertEqual(closed_full, closed_trunc)

    def test_yearly_members_restrict_buys(self):
        hist, idx = self._random_universe(n_days=300, n_tickers=6)
        # with data starting in 2025 there is no pre-year history, so yearly_top selects nobody for
        # 2025 (no 2025 trades); 2026's members come from 2025's trading values
        cfg = replace(CFG, universe_mode="yearly_top", universe_top_m=2, min_history_days=0, trading_value_n=5)
        res = run_kr_trend(hist, idx, cfg)
        self.assertEqual(res["members"][2025], [])
        self.assertEqual(len(res["members"][2026]), 2)
        self.assertTrue(all(t["entry_date"] >= "2026-01-01" for t in res["trades"]))
        self.assertTrue({t["ticker"] for t in res["trades"]} <= set(res["members"][2026]))
        # with the frames starting in 2024 the 2025 members are the two most traded names
        dates = bdays(date(2024, 11, 4), 300)
        hist2 = {f"{k:06d}": random_walk(dates, 100 + k) for k in range(6)}
        idx2 = random_walk(dates, 998, drift=0.0003, vol=0.01, p0=2500.0)
        res2 = run_kr_trend(hist2, idx2, cfg, start="2025-01-01")
        self.assertEqual(len(res2["members"][2025]), 2)
        self.assertTrue({t["ticker"] for t in res2["trades"]} <= set(res2["members"][2025]))

    def test_empty_inputs(self):
        res = run_kr_trend({}, None, CFG)
        self.assertEqual(res["years"], [])
        self.assertFalse(res["summary"]["passes_risk_gate"])
        res = run_kr_trend({"A": pl.DataFrame()}, None, CFG)
        self.assertEqual(res["trades"], [])

    def test_next_orders_report_the_last_decision(self):
        df = _flat_breakout(n=10)                                            # ends on the breakout Friday
        res = _run({"A": df})
        self.assertEqual(res["trades"], [])
        self.assertEqual([(o["kind"], o["ticker"]) for o in res["next_orders"]], [("buy", "A")])
        self.assertGreater(res["next_orders"][0]["weight"], 0.0)
