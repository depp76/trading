"""strategy/trend_following/backtest.py — Portfolio engine and the Trend
Following policy (trend_following.md 2-1..2-4, 3, 4, 7 step 1).

Engine rules (spec 2-3, 3, 4, 4-1):
  * a decision made from day t's close is filled at day t+1's open, sells
    before buys; the open is snapped to the tick grid against the trader and
    a fill at the daily limit is deferred (``CostModel.block_limit_up_down``);
  * cash earns the risk-free rate (``Dataset.rf_period``) every session;
  * costs come from one ``CostModel`` per run and are booked per trade and
    per year, split into commission / tax / slippage;
  * with ``annual_reset`` the account restarts every year at ``seed_krw``:
    on the year's last session positions are trimmed pro-rata (at the close --
    the one deliberate exception to the t+1 rule, so the new year opens at
    exactly the seed) and the excess is banked, or a shortfall is topped up;
    ``nav`` chains the yearly results so metrics see one continuous curve;
  * a held name with no price for ``delist_after_missing`` sessions is closed
    at its last known close (reason "delisted").

The policy is deliberately separate from the engine so the 6-3 benchmarks
(benchmarks.py) run under exactly the same fills, interest, reset and costs.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date

import numpy as np

from strategy.trend_following.config import StrategyParams, Variant
from strategy.trend_following.costs import CostModel, TradeCost
from strategy.trend_following.dataset import PriceBook
from strategy.trend_following.signals import Features, rank_desc_score

TRIM_REASON = "year_end_trim"


class Order:
    __slots__ = ("ticker", "side", "qty", "reason", "stop_offset", "tries")

    def __init__(self, ticker: str, side: str, qty: int | None, reason: str, stop_offset: float | None = None):
        self.ticker = ticker
        self.side = side          # "buy" | "sell"
        self.qty = qty            # sells: None = the whole position
        self.reason = reason
        self.stop_offset = stop_offset
        self.tries = 0

    def __repr__(self):
        return f"Order({self.side} {self.ticker} x{self.qty} {self.reason})"


@dataclass
class Position:
    ticker: str
    col: int
    qty: int
    entry_price: float
    entry_idx: int
    stop_price: float | None
    entry_costs: TradeCost
    last_close: float
    missing_run: int = 0


@dataclass
class Trade:
    ticker: str
    entry_date: date
    exit_date: date
    entry_price: float
    exit_price: float
    qty: int
    pnl: float                  # net of both sides' costs
    ret_pct: float
    reason: str
    holding_days: int           # calendar days
    cost: TradeCost             # entry share + exit costs


@dataclass
class BacktestResult:
    id: str
    dates: list[date]
    nav: np.ndarray             # chained; 1.0 = seed. nav[0] < 1.0 when the policy fills on
                                # the first session (its costs and open-to-close move), so
                                # metrics measure from 1.0, never from nav[0]
    equity: np.ndarray          # in-year account value (KRW)
    cash: np.ndarray
    exposure: np.ndarray        # invested fraction of equity
    n_positions: np.ndarray
    trades: list[Trade]
    costs_by_year: dict[int, dict[str, float]]
    traded_value_by_year: dict[int, float]
    banked_by_year: dict[int, float]
    topup_by_year: dict[int, float]
    open_positions: list[dict]
    rf_period: np.ndarray
    cost_multiplier: float
    notes: list[str] = field(default_factory=list)
    params: dict = field(default_factory=dict)

    @property
    def total_costs(self) -> TradeCost:
        c = TradeCost()
        for y in self.costs_by_year.values():
            c = c + TradeCost(y["commission"], y["tax"], y["slippage"])
        return c

    @property
    def total_traded_value(self) -> float:
        return float(sum(self.traded_value_by_year.values()))


class Policy:
    """What the engine asks each session: orders to fill at the next open."""
    max_positions: int = 10 ** 6

    def initial_orders(self, eng: "Engine", t0: int) -> list[Order]:
        return []

    def decide(self, t: int, eng: "Engine") -> list[Order]:
        return []


class Engine:
    def __init__(self, book: PriceBook, rf_period: np.ndarray, params: StrategyParams, cost_model: CostModel,
                 start_idx: int, label: str, adv: np.ndarray | None = None):
        self.book = book
        self.rf_period = np.asarray(rf_period, dtype=float)
        self.params = params
        self.cm = cost_model
        self.start_idx = int(start_idx)
        self.label = label
        self.adv = adv
        self.notes: list[str] = []

        T = book.T
        self.cash = float(params.seed_krw)
        self.positions: dict[str, Position] = {}
        self.pending: list[Order] = []
        self.trades: list[Trade] = []
        self.equity_arr = np.full(T, np.nan)
        self.cash_arr = np.full(T, np.nan)
        self.exposure_arr = np.full(T, np.nan)
        self.npos_arr = np.zeros(T)
        self.nav_arr = np.full(T, np.nan)
        self.costs_by_year: dict[int, dict[str, float]] = {}
        self.traded_value_by_year: dict[int, float] = {}
        self.banked_by_year: dict[int, float] = {}
        self.topup_by_year: dict[int, float] = {}
        self.chain = 1.0
        self.year_start_equity = float(params.seed_krw)
        self.equity_now = float(params.seed_krw)
        self.max_positions = 10 ** 6

    # -- helpers -----------------------------------------------------------
    def is_year_end(self, t: int) -> bool:
        d = self.book.dates
        return t < len(d) - 1 and d[t + 1].year != d[t].year

    def _adv_at(self, t: int, col: int):
        if self.adv is None:
            return None
        v = self.adv[t, col]
        return float(v) if np.isfinite(v) else None

    def _ledger(self, t: int, cost: TradeCost, value: float) -> None:
        y = self.book.dates[t].year
        row = self.costs_by_year.setdefault(y, {"commission": 0.0, "tax": 0.0, "slippage": 0.0})
        row["commission"] += cost.commission
        row["tax"] += cost.tax
        row["slippage"] += cost.slippage
        self.traded_value_by_year[y] = self.traded_value_by_year.get(y, 0.0) + value

    def positions_value(self) -> float:
        return float(sum(p.qty * p.last_close for p in self.positions.values()))

    def has_pending(self, ticker: str, side: str) -> bool:
        return any(o.ticker == ticker and o.side == side for o in self.pending)

    def affordable_qty(self, t: int, market: str, price: float, col: int) -> int:
        if price <= 0 or self.cash <= 0:
            return 0
        cm = self.cm
        per_share = price * (1.0 + (cm.commission_rate + cm.slippage_rate) * cm.cost_multiplier)
        q = int(math.floor(self.cash / per_share))
        d = self.book.dates[t]
        adv = self._adv_at(t, col)
        while q > 0 and price * q + cm.buy_cost(d, market, price, q, adv).total > self.cash + 1e-9:
            q -= 1
        return q

    # -- execution ---------------------------------------------------------
    def _execute_buy(self, t: int, ticker: str, qty: int, price: float, reason: str, stop_offset) -> None:
        b = self.book
        col = b.col(ticker)
        market = b.market_of(ticker)
        cost = self.cm.buy_cost(b.dates[t], market, price, qty, self._adv_at(t, col))
        self.cash -= price * qty + cost.total
        self._ledger(t, cost, price * qty)
        pos = self.positions.get(ticker)
        stop = price - stop_offset if stop_offset is not None and np.isfinite(stop_offset) else None
        if pos is None:
            close = b.close[t, col]
            self.positions[ticker] = Position(
                ticker=ticker, col=col, qty=qty, entry_price=price, entry_idx=t, stop_price=stop,
                entry_costs=cost, last_close=float(close) if np.isfinite(close) else price,
            )
        else:
            total_qty = pos.qty + qty
            pos.entry_price = (pos.entry_price * pos.qty + price * qty) / total_qty
            pos.qty = total_qty
            pos.entry_costs = pos.entry_costs + cost
            if stop is not None and pos.stop_price is not None:
                pos.stop_price = min(pos.stop_price, stop)

    def _execute_sell(self, t: int, pos: Position, qty: int, price: float, reason: str) -> None:
        b = self.book
        qty = int(min(qty, pos.qty))
        if qty <= 0:
            return
        market = b.market_of(pos.ticker)
        cost = self.cm.sell_cost(b.dates[t], market, price, qty, self._adv_at(t, pos.col))
        self.cash += price * qty - cost.total
        self._ledger(t, cost, price * qty)
        share = qty / pos.qty
        entry_share = pos.entry_costs.scaled(share)
        pnl = (price - pos.entry_price) * qty - cost.total - entry_share.total
        basis = pos.entry_price * qty
        self.trades.append(Trade(
            ticker=pos.ticker, entry_date=b.dates[pos.entry_idx], exit_date=b.dates[t],
            entry_price=pos.entry_price, exit_price=price, qty=qty, pnl=pnl,
            ret_pct=(pnl / basis * 100.0) if basis > 0 else 0.0, reason=reason,
            holding_days=(b.dates[t] - b.dates[pos.entry_idx]).days, cost=entry_share + cost,
        ))
        pos.qty -= qty
        pos.entry_costs = pos.entry_costs.scaled(1.0 - share)
        if pos.qty <= 0:
            del self.positions[pos.ticker]

    def _fill(self, t: int) -> None:
        b = self.book
        d = b.dates[t]
        keep: list[Order] = []
        sells = [o for o in self.pending if o.side == "sell"]
        buys = [o for o in self.pending if o.side == "buy"]

        for o in sells:
            pos = self.positions.get(o.ticker)
            if pos is None:
                continue
            px = b.open[t, pos.col]
            prev_close = b.close[t - 1, pos.col] if t > 0 else np.nan
            if not np.isfinite(px) or px <= 0:
                o.tries += 1
                keep.append(o)
                continue
            if self.cm.block_limit_up_down and np.isfinite(prev_close) and self.cm.is_limit_down(
                    d, b.market_of(pos.ticker), px, prev_close):
                o.tries += 1
                keep.append(o)
                continue
            price = self.cm.fill_price(d, b.market_of(pos.ticker), px, "sell")
            self._execute_sell(t, pos, pos.qty if o.qty is None else o.qty, price, o.reason)

        for o in buys:
            if o.ticker not in b._col:
                continue
            col = b.col(o.ticker)
            if o.ticker not in self.positions and len(self.positions) >= self.max_positions:
                continue
            px = b.open[t, col]
            prev_close = b.close[t - 1, col] if t > 0 else np.nan
            blocked = (not np.isfinite(px) or px <= 0) or (
                self.cm.block_limit_up_down and np.isfinite(prev_close)
                and self.cm.is_limit_up(d, b.market_of(o.ticker), px, prev_close))
            if blocked:
                o.tries += 1
                if o.tries <= self.params.max_order_retries:
                    keep.append(o)
                continue
            market = b.market_of(o.ticker)
            price = self.cm.fill_price(d, market, px, "buy")
            qty = min(int(o.qty or 0), self.affordable_qty(t, market, price, col))
            if qty <= 0:
                continue
            self._execute_buy(t, o.ticker, qty, price, o.reason, o.stop_offset)

        self.pending = keep

    def _mark(self, t: int) -> float:
        b = self.book
        for pos in list(self.positions.values()):
            c = b.close[t, pos.col]
            if np.isfinite(c) and c > 0:
                pos.last_close = float(c)
                pos.missing_run = 0
            else:
                pos.missing_run += 1
                if pos.missing_run >= self.params.delist_after_missing:
                    price = self.cm.fill_price(b.dates[t], b.market_of(pos.ticker), pos.last_close, "sell")
                    self._execute_sell(t, pos, pos.qty, price, "delisted")
                    self.pending = [o for o in self.pending if o.ticker != pos.ticker]
        return self.cash + self.positions_value()

    def _year_end_reset(self, t: int) -> None:
        b = self.book
        seed = self.params.seed_krw
        year = b.dates[t].year
        equity = self.cash + self.positions_value()
        trim_costs = 0.0
        if equity > seed:
            frac = (equity - seed) / equity
            for pos in list(self.positions.values()):
                sell_qty = int(math.floor(pos.qty * frac))
                if sell_qty <= 0:
                    continue
                price = self.cm.fill_price(b.dates[t], b.market_of(pos.ticker), pos.last_close, "sell")
                before = self.cash
                self._execute_sell(t, pos, sell_qty, price, TRIM_REASON)
                trim_costs += price * sell_qty - (self.cash - before)
            after = self.cash + self.positions_value()
            bank = min(after - seed, self.cash)
            if bank > 0:
                self.cash -= bank
                self.banked_by_year[year] = self.banked_by_year.get(year, 0.0) + bank
        elif equity < seed:
            topup = seed - equity
            self.cash += topup
            self.topup_by_year[year] = self.topup_by_year.get(year, 0.0) + topup
        net_year_end = equity - trim_costs
        self.chain *= net_year_end / self.year_start_equity if self.year_start_equity > 0 else 1.0
        self.year_start_equity = self.cash + self.positions_value()

    def _record(self, t: int) -> None:
        eq = self.cash + self.positions_value()
        self.equity_now = eq
        self.equity_arr[t] = eq
        self.cash_arr[t] = self.cash
        self.exposure_arr[t] = (eq - self.cash) / eq if eq > 0 else 0.0
        self.npos_arr[t] = len(self.positions)
        self.nav_arr[t] = self.chain * eq / self.year_start_equity if self.year_start_equity > 0 else self.chain

    # -- main loop ---------------------------------------------------------
    def run(self, policy: Policy) -> BacktestResult:
        b = self.book
        T = b.T
        t0 = self.start_idx
        self.max_positions = int(getattr(policy, "max_positions", 10 ** 6))
        self.pending = list(policy.initial_orders(self, t0))
        for t in range(t0, T):
            self._fill(t)
            if t > t0:
                self.cash += self.cash * self.rf_period[t]
            self._mark(t)
            if self.params.annual_reset and self.is_year_end(t):
                self._year_end_reset(t)
            self._record(t)
            if t < T - 1:
                new_orders = policy.decide(t, self)
                if new_orders:
                    self.pending.extend(new_orders)
        return self._result(t0)

    def _result(self, t0: int) -> BacktestResult:
        b = self.book
        open_positions = [{
            "ticker": p.ticker, "name": b.names.get(p.ticker, ""), "qty": p.qty, "entry_price": p.entry_price,
            "entry_date": b.dates[p.entry_idx].isoformat(), "last_close": p.last_close,
            "entry_costs": p.entry_costs.total,
            "unrealized_pnl": (p.last_close - p.entry_price) * p.qty - p.entry_costs.total,
        } for p in self.positions.values()]
        return BacktestResult(
            id=self.label, dates=list(b.dates[t0:]),
            nav=self.nav_arr[t0:].copy(), equity=self.equity_arr[t0:].copy(), cash=self.cash_arr[t0:].copy(),
            exposure=self.exposure_arr[t0:].copy(), n_positions=self.npos_arr[t0:].copy(),
            trades=list(self.trades), costs_by_year=dict(self.costs_by_year),
            traded_value_by_year=dict(self.traded_value_by_year), banked_by_year=dict(self.banked_by_year),
            topup_by_year=dict(self.topup_by_year), open_positions=open_positions,
            rf_period=self.rf_period[t0:].copy(), cost_multiplier=self.cm.cost_multiplier,
            notes=list(self.notes), params=self.params.as_dict(),
        )


# ---------------------------------------------------------------------------
# The strategy policy (2-1 .. 2-4, 3)
# ---------------------------------------------------------------------------
class TrendFollowingPolicy(Policy):
    """A-series: Donchian-20 breakouts filtered by L1/L2 (+V/F), Donchian-10 /
    ATR-stop (+FX) exits, rank-based selection when candidates exceed the
    free slots. B: on weekly check days hold the top names by 52-week-high
    proximity instead of buying breakouts; the daily exits still apply."""

    def __init__(self, feats: Features, params: StrategyParams, variant: Variant):
        self.f = feats
        self.p = params
        self.v = variant
        self.max_positions = params.max_positions

    def _size(self, t: int, j: int, equity: float, close: float) -> int:
        """2-4: shares = min(1% equity / (2 x ATR20), 20% equity / price), whole shares."""
        a = self.f.atr[t, j]
        if not (np.isfinite(a) and a > 0 and np.isfinite(close) and close > 0 and equity > 0):
            return 0
        risk_qty = math.floor(equity * self.p.risk_per_trade / (self.p.atr_multiple * a))
        cap_qty = math.floor(equity * self.p.max_weight / close)
        return max(0, int(min(risk_qty, cap_qty)))

    def _equal_size(self, equity: float, close: float) -> int:
        if not (np.isfinite(close) and close > 0 and equity > 0):
            return 0
        weight = min(self.p.max_weight, 1.0 / max(1, self.p.max_positions))
        return max(0, int(math.floor(equity * weight / close)))

    def decide(self, t: int, eng: Engine) -> list[Order]:
        f, p, v, b = self.f, self.p, self.v, eng.book
        orders: list[Order] = []

        # 3: exits, checked every session
        exiting = {o.ticker for o in eng.pending if o.side == "sell"}
        for tk, pos in eng.positions.items():
            if tk in exiting:
                continue
            j = pos.col
            c = b.close[t, j]
            if not np.isfinite(c):
                continue
            reason = None
            if f.exit_channel[t, j]:
                reason = "channel"
            elif pos.stop_price is not None and c < pos.stop_price:
                reason = "stop"
            elif v.flow_exit and f.flow_exit[t, j]:
                reason = "flow"
            if reason:
                orders.append(Order(tk, "sell", None, reason))
                exiting.add(tk)

        pending_buys = {o.ticker for o in eng.pending if o.side == "buy"}
        staying = len([tk for tk in eng.positions if tk not in exiting])
        slots = p.max_positions - staying - len(pending_buys)
        if v.regime and not f.regime_on[t]:
            return orders     # 2-1: risk-off -> no new entries, exits only
        if v.mode == "high52":
            return orders + self._high52_rebalance(t, eng, exiting, pending_buys)
        if slots <= 0:
            return orders

        held = np.zeros(b.N, dtype=bool)
        for pos in eng.positions.values():
            held[pos.col] = True
        for tk in pending_buys:
            if tk in b._col:
                held[b.col(tk)] = True
        mask = f.trend_ok[t] & f.breakout[t] & f.liquid[t] & ~held
        if v.volume:
            mask &= f.vol_ok[t]
        if v.flow:
            mask &= f.flow_ok[t]
        cands = np.flatnonzero(mask)
        if cands.size == 0:
            return orders
        if cands.size > slots:
            score = rank_desc_score(f.flow_strength[t, cands], f.rs[t, cands])
            rs_c = np.where(np.isfinite(f.rs[t, cands]), f.rs[t, cands], -np.inf)
            cands = cands[np.lexsort((-rs_c, -score))][:slots]
        equity = eng.equity_now
        for j in cands:
            qty = self._size(t, j, equity, b.close[t, j])
            if qty > 0:
                orders.append(Order(b.tickers[j], "buy", qty, "entry", stop_offset=p.stop_atr_multiple * f.atr[t, j]))
        return orders

    def _high52_rebalance(self, t: int, eng: Engine, exiting: set, pending_buys: set) -> list[Order]:
        f, p, b = self.f, self.p, eng.book
        if not f.check_day[t]:
            return []
        prox = np.where(np.isfinite(f.high52_prox[t]), f.high52_prox[t], -np.inf)
        eligible = f.trend_ok[t] & f.liquid[t] & np.isfinite(f.high52_prox[t])
        cands = np.flatnonzero(eligible)
        if cands.size == 0:
            return []
        target_cols = cands[np.argsort(-prox[cands], kind="stable")][:p.max_positions]
        target = {b.tickers[j] for j in target_cols}
        orders: list[Order] = []
        for tk in list(eng.positions):
            if tk not in target and tk not in exiting:
                orders.append(Order(tk, "sell", None, "rank_out"))
                exiting.add(tk)
        equity = eng.equity_now
        for j in target_cols:
            tk = b.tickers[j]
            if tk in eng.positions or tk in pending_buys:
                continue
            qty = self._equal_size(equity, b.close[t, j])
            if qty > 0:
                orders.append(Order(tk, "buy", qty, "entry", stop_offset=p.stop_atr_multiple * f.atr[t, j]))
        return orders


def run_backtest(ds, feats: Features, params: StrategyParams, variant: Variant, cost_model: CostModel,
                 label: str | None = None) -> BacktestResult:
    """One variant of the 6-2 matrix over `ds` under `cost_model`."""
    eng = Engine(ds.stocks, ds.rf_period, params, cost_model, ds.start_idx, label or variant.id, adv=feats.adv)
    if variant.needs_flows and not feats.has_flows:
        eng.notes.append("variant needs investor flows but the dataset has none: no entries possible")
    return eng.run(TrendFollowingPolicy(feats, params, variant))
