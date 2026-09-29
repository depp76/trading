"""strategy/trend_following/benchmarks.py — The four 6-3 benchmarks, run
through the same Engine (fills, interest, annual reset, costs) as the strategy.

  BM1  KOSPI 200 total return: buy-and-hold the TR ETF (KODEX 200TR).
  BM2  Universe equal weight: every liquid name at equal weight, rebalanced to
       equal weight on each year's first session (the annual reset point).
  BM3  Simple trend following: hold the ETF only while close > MA200, else
       cash; next-open fills like the strategy.
  BM4  Risk-free (CD 91-day): cash compounding at ``Dataset.rf_period``.

ETF runs use ``CostModel.for_etf()`` (0.02% slippage, no transaction tax);
BM2 uses the strategy's own cost model.
"""
from __future__ import annotations

from dataclasses import replace
import math

import numpy as np

from strategy.trend_following.backtest import BacktestResult, Engine, Order, Policy
from strategy.trend_following.config import StrategyParams, BM_ETF_SLIPPAGE
from strategy.trend_following.costs import CostModel
from strategy.trend_following.signals import Features, rolling


class BuyAndHoldPolicy(Policy):
    """BM1: all cash into `ticker`; reinvest cash again after a year-end top-up."""

    def __init__(self, ticker: str):
        self.ticker = ticker

    def _buy_all(self, eng: Engine, price: float) -> list[Order]:
        if not (np.isfinite(price) and price > 0) or eng.cash < price:
            return []
        return [Order(self.ticker, "buy", int(math.floor(eng.cash / price)), "hold")]

    def initial_orders(self, eng: Engine, t0: int) -> list[Order]:
        b = eng.book
        px = b.open[t0, 0] if np.isfinite(b.open[t0, 0]) else b.close[t0, 0]
        return self._buy_all(eng, px)

    def decide(self, t: int, eng: Engine) -> list[Order]:
        if eng.has_pending(self.ticker, "buy"):
            return []
        c = eng.book.close[t, 0]
        if self.ticker not in eng.positions or eng.is_year_end(t):
            return self._buy_all(eng, c)
        return []


class TrendTimingPolicy(Policy):
    """BM3: hold `ticker` while close > MA(`ma_window`), otherwise cash."""

    def __init__(self, ticker: str, close: np.ndarray, ma_window: int):
        self.ticker = ticker
        self.ma = rolling(np.asarray(close, dtype=float), ma_window, "mean")

    def decide(self, t: int, eng: Engine) -> list[Order]:
        c = eng.book.close[t, 0]
        m = self.ma[t]
        if not (np.isfinite(c) and np.isfinite(m) and c > 0):
            return []
        held = self.ticker in eng.positions
        if held and c < m:
            if eng.has_pending(self.ticker, "sell"):
                return []
            return [Order(self.ticker, "sell", None, "below_ma")]
        if c > m and eng.cash >= c and not eng.has_pending(self.ticker, "buy"):
            return [Order(self.ticker, "buy", int(math.floor(eng.cash / c)), "above_ma")]
        return []


class EqualWeightPolicy(Policy):
    """BM2: equal weight across the eligible names, set on the first session
    and re-set after each year-end reset."""

    def __init__(self, eligible: np.ndarray | None):
        self.eligible = eligible

    def _targets(self, t: int, eng: Engine, prices: np.ndarray, equity: float) -> list[Order]:
        b = eng.book
        ok = np.isfinite(prices) & (prices > 0)
        if self.eligible is not None:
            ok &= self.eligible[t]
        cols = np.flatnonzero(ok)
        orders: list[Order] = []
        for tk in list(eng.positions):
            if not ok[b.col(tk)] and not eng.has_pending(tk, "sell"):
                orders.append(Order(tk, "sell", None, "ineligible"))
        if cols.size == 0:
            return orders
        per_name = equity / cols.size
        for j in cols:
            tk = b.tickers[j]
            target_qty = int(math.floor(per_name / prices[j]))
            held_qty = eng.positions[tk].qty if tk in eng.positions else 0
            diff = target_qty - held_qty
            if diff > 0:
                orders.append(Order(tk, "buy", diff, "equal_weight"))
            elif diff < 0:
                orders.append(Order(tk, "sell", -diff, "equal_weight"))
        return orders

    def initial_orders(self, eng: Engine, t0: int) -> list[Order]:
        b = eng.book
        prices = np.where(np.isfinite(b.open[t0]), b.open[t0], b.close[t0])
        return self._targets(t0, eng, prices, eng.cash)

    def decide(self, t: int, eng: Engine) -> list[Order]:
        if not eng.is_year_end(t):
            return []
        return self._targets(t, eng, eng.book.close[t], eng.equity_now)


def _etf_model(cost_model: CostModel) -> CostModel:
    return cost_model.for_etf(BM_ETF_SLIPPAGE)


def run_bm1(ds, params: StrategyParams, cost_model: CostModel) -> BacktestResult | None:
    if ds.bm is None:
        return None
    eng = Engine(ds.bm, ds.rf_period, params, _etf_model(cost_model), ds.start_idx, "BM1")
    return eng.run(BuyAndHoldPolicy(ds.bm.tickers[0]))


BM2_SEED_SCALE = 1000.0
"""BM2 runs on ``seed_krw * BM2_SEED_SCALE``. With the real seed (10M KRW) and
200 names the per-name budget is 50,000 KRW, so every share priced above that
(most large caps) rounds to zero shares and the "equal weight" book is mostly
cash (review_agy.md 2.1, 2026-09-29). The benchmark is a return index, not an
order plan, so the run is scaled up and its KRW ledger scaled back down; NAV
and every ratio metric are unaffected."""


def _rescale_result(res: BacktestResult, factor: float) -> BacktestResult:
    """Divide every KRW-denominated field of `res` by `factor` (share counts are
    left as run, so per-trade quantities stay consistent with the fill ledger)."""
    res.equity = res.equity / factor
    res.cash = res.cash / factor
    res.costs_by_year = {y: {k: v / factor for k, v in c.items()} for y, c in res.costs_by_year.items()}
    res.traded_value_by_year = {y: v / factor for y, v in res.traded_value_by_year.items()}
    res.banked_by_year = {y: v / factor for y, v in res.banked_by_year.items()}
    res.topup_by_year = {y: v / factor for y, v in res.topup_by_year.items()}
    for tr in res.trades:
        tr.pnl /= factor
        tr.cost = tr.cost.scaled(1.0 / factor)
    for p in res.open_positions:
        p["entry_costs"] /= factor
        p["unrealized_pnl"] /= factor
    return res


def run_bm2(ds, feats: Features, params: StrategyParams, cost_model: CostModel) -> BacktestResult:
    scaled = replace(params, seed_krw=params.seed_krw * BM2_SEED_SCALE)
    # adv is not passed: the liquidity slippage term (order value / ADV) would
    # see the scaled-up orders, and the benchmark is not an execution plan.
    eng = Engine(ds.stocks, ds.rf_period, scaled, cost_model, ds.start_idx, "BM2")
    res = eng.run(EqualWeightPolicy(feats.liquid))
    res.params = params.as_dict()
    res.notes.append(f"run at {BM2_SEED_SCALE:g}x seed so whole shares of every name fit; KRW ledger rescaled")
    return _rescale_result(res, BM2_SEED_SCALE)


def run_bm3(ds, params: StrategyParams, cost_model: CostModel) -> BacktestResult | None:
    if ds.bm is None:
        return None
    eng = Engine(ds.bm, ds.rf_period, params, _etf_model(cost_model), ds.start_idx, "BM3")
    return eng.run(TrendTimingPolicy(ds.bm.tickers[0], ds.bm.close[:, 0], params.bm_ma))


def run_bm4(ds, params: StrategyParams) -> BacktestResult:
    t0 = ds.start_idx
    rf = np.asarray(ds.rf_period[t0:], dtype=float)
    growth = np.cumprod(1.0 + np.concatenate([[0.0], rf[1:]]))
    nav = growth / growth[0]
    n = len(nav)
    seed = params.seed_krw
    return BacktestResult(
        id="BM4", dates=list(ds.dates[t0:]), nav=nav, equity=nav * seed, cash=nav * seed,
        exposure=np.zeros(n), n_positions=np.zeros(n), trades=[], costs_by_year={}, traded_value_by_year={},
        banked_by_year={}, topup_by_year={}, open_positions=[], rf_period=rf, cost_multiplier=0.0,
        notes=[f"risk-free source: {ds.rf_source}"], params=params.as_dict(),
    )


def run_benchmarks(ds, feats: Features, params: StrategyParams, cost_model: CostModel) -> dict[str, BacktestResult]:
    out: dict[str, BacktestResult] = {}
    bm1 = run_bm1(ds, params, cost_model)
    if bm1 is not None:
        out["BM1"] = bm1
    out["BM2"] = run_bm2(ds, feats, params, cost_model)
    bm3 = run_bm3(ds, params, cost_model)
    if bm3 is not None:
        out["BM3"] = bm3
    out["BM4"] = run_bm4(ds, params)
    return out
