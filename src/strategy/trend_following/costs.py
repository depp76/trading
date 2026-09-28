"""strategy/trend_following/costs.py — Trading cost model (trend_following.md 4-1).

Every rate is a config value on ``CostModel``, never a literal inside the
engine (4-1 principle 1); the securities-transaction-tax rate is looked up by
the fill date's year (principle 2); the same model is applied to the strategy
and to the benchmarks (principle 3); costs are computed on the fill amount,
deducted from cash and recorded per trade and per year by the engine
(principle 4). ``TradeCost`` keeps commission / tax / slippage apart so the
report can break them down (4-1 "구현 인터페이스").
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from datetime import date

# 4-1 (1): securities transaction tax + rural special tax, by fill year and
# market. ETF sales are exempt (BM1/BM3 pay 0). Years before the first row use
# the first row, years after the last use the last -- add a row when the law
# changes and re-check the table against the NTS/KRX notices before relying on
# a new year (the spec asks for that re-check before implementation).
DEFAULT_TAX_TABLE: dict[int, dict[str, float]] = {
    2021: {"KOSPI": 0.0023, "KOSDAQ": 0.0023, "ETF": 0.0},
    2022: {"KOSPI": 0.0023, "KOSDAQ": 0.0023, "ETF": 0.0},
    2023: {"KOSPI": 0.0020, "KOSDAQ": 0.0020, "ETF": 0.0},
    2024: {"KOSPI": 0.0018, "KOSDAQ": 0.0018, "ETF": 0.0},
    2025: {"KOSPI": 0.0015, "KOSDAQ": 0.0015, "ETF": 0.0},
    2026: {"KOSPI": 0.0020, "KOSDAQ": 0.0020, "ETF": 0.0},
}

_INF = float("inf")
# 4-1 (3): tick-size ladders as (exclusive upper price bound, tick in KRW).
# KRX unified the KOSPI/KOSDAQ ladders on 2023-01-25 (the "2023 호가단위 개편"
# the spec asks to reflect); ETF/ETN ticks are 5 KRW throughout.
_LADDER_UNIFIED = [(2000, 1), (5000, 5), (20000, 10), (50000, 50), (200000, 100), (500000, 500), (_INF, 1000)]
_LADDER_KOSPI_OLD = [(1000, 1), (5000, 5), (10000, 10), (50000, 50), (100000, 100), (500000, 500), (_INF, 1000)]
_LADDER_KOSDAQ_OLD = [(1000, 1), (5000, 5), (10000, 10), (50000, 50), (_INF, 100)]
_LADDER_ETF = [(_INF, 5)]

# Newest first; the first table whose effective date is <= the fill date wins.
TICK_SIZE_TABLES: list[tuple[date, dict[str, list[tuple[float, int]]]]] = [
    (date(2023, 1, 25), {"KOSPI": _LADDER_UNIFIED, "KOSDAQ": _LADDER_UNIFIED, "ETF": _LADDER_ETF}),
    (date.min, {"KOSPI": _LADDER_KOSPI_OLD, "KOSDAQ": _LADDER_KOSDAQ_OLD, "ETF": _LADDER_ETF}),
]


def normalize_market(market) -> str:
    """'KOSPI' | 'KOSDAQ' | 'ETF' -- anything unknown is treated as KOSPI."""
    m = str(market or "").strip().upper()
    if m in ("KOSDAQ", "ETF"):
        return m
    return "KOSPI"


def tick_size(trade_date: date, market, price: float) -> int:
    """Tick size (KRW) for `price` on `trade_date` in `market`."""
    m = normalize_market(market)
    for effective_from, ladders in TICK_SIZE_TABLES:
        if trade_date >= effective_from:
            for bound, tick in ladders[m]:
                if price < bound:
                    return tick
            return ladders[m][-1][1]
    return 1


def snap_to_tick(price: float, tick: int, side: str) -> float:
    """Round a raw price to the tick grid in the unfavourable direction:
    buys round up, sells round down (4-1 (3))."""
    if tick <= 0 or price <= 0:
        return float(price)
    units = price / tick
    n = math.ceil(units - 1e-9) if side == "buy" else math.floor(units + 1e-9)
    return float(n * tick)


@dataclass(frozen=True)
class TradeCost:
    """Cost of one fill, split the way the report aggregates it."""
    commission: float = 0.0
    tax: float = 0.0
    slippage: float = 0.0

    @property
    def total(self) -> float:
        return self.commission + self.tax + self.slippage

    def __add__(self, other: "TradeCost") -> "TradeCost":
        return TradeCost(self.commission + other.commission, self.tax + other.tax, self.slippage + other.slippage)

    def scaled(self, k: float) -> "TradeCost":
        return TradeCost(self.commission * k, self.tax * k, self.slippage * k)


@dataclass(frozen=True)
class CostModel:
    """4-1 cost assumptions. Rates are one-way fractions of the fill amount.

    ``cost_multiplier`` scales every cost (0 / 1 / 2 for the mandatory
    sensitivity analysis); ``slippage_liquidity_coeff`` adds
    ``coeff * order_value / adv20`` to the slippage rate when the caller passes
    the 20-day average trading value (the "유동성 연동 옵션"). Fill prices are
    snapped to the tick grid by ``fill_price`` when ``round_to_tick`` is set,
    and the engine defers a fill whose open is at the daily limit when
    ``block_limit_up_down`` is set (4-1 (3)).
    """
    commission_rate: float = 0.00015
    slippage_rate: float = 0.0010
    min_commission: float = 0.0
    tax_table: dict = field(default_factory=lambda: {y: dict(v) for y, v in DEFAULT_TAX_TABLE.items()})
    round_to_tick: bool = True
    block_limit_up_down: bool = True
    cost_multiplier: float = 1.0
    slippage_liquidity_coeff: float = 0.0
    limit_move_pct: float = 0.30      # KRX daily price limit (+-30% since 2015-06)

    # -- rates -----------------------------------------------------------------
    def tax_rate(self, trade_date: date, market) -> float:
        if not self.tax_table:
            return 0.0
        years = sorted(self.tax_table)
        year = trade_date.year
        if year not in self.tax_table:
            year = years[0] if year < years[0] else years[-1]
        return float(self.tax_table[year].get(normalize_market(market), 0.0))

    def _slippage_rate_for(self, value: float, adv) -> float:
        rate = self.slippage_rate
        if self.slippage_liquidity_coeff and adv is not None and adv > 0:
            rate += self.slippage_liquidity_coeff * (value / adv)
        return rate

    def _commission(self, value: float) -> float:
        if value <= 0:
            return 0.0
        return max(value * self.commission_rate, self.min_commission)

    # -- prices ----------------------------------------------------------------
    def fill_price(self, trade_date: date, market, raw_price: float, side: str) -> float:
        if not self.round_to_tick:
            return float(raw_price)
        return snap_to_tick(float(raw_price), tick_size(trade_date, market, float(raw_price)), side)

    def is_limit_up(self, open_price: float, prev_close: float) -> bool:
        return prev_close > 0 and open_price >= prev_close * (1.0 + self.limit_move_pct) * 0.999

    def is_limit_down(self, open_price: float, prev_close: float) -> bool:
        return prev_close > 0 and open_price <= prev_close * (1.0 - self.limit_move_pct) * 1.001

    # -- costs -----------------------------------------------------------------
    def buy_cost(self, trade_date: date, market, price: float, qty: int, adv=None) -> TradeCost:
        value = float(price) * int(qty)
        if value <= 0:
            return TradeCost()
        return TradeCost(
            commission=self._commission(value),
            tax=0.0,
            slippage=value * self._slippage_rate_for(value, adv),
        ).scaled(self.cost_multiplier)

    def sell_cost(self, trade_date: date, market, price: float, qty: int, adv=None) -> TradeCost:
        value = float(price) * int(qty)
        if value <= 0:
            return TradeCost()
        return TradeCost(
            commission=self._commission(value),
            tax=value * self.tax_rate(trade_date, market),
            slippage=value * self._slippage_rate_for(value, adv),
        ).scaled(self.cost_multiplier)

    # -- variants --------------------------------------------------------------
    def scaled(self, multiplier: float) -> "CostModel":
        """Same assumptions at another multiplier (sensitivity analysis)."""
        return replace(self, cost_multiplier=float(multiplier))

    def for_etf(self, slippage_rate: float = 0.0002) -> "CostModel":
        """The benchmark variant: ETF slippage assumption (4-1 (4)); the tax
        table already carries ETF = 0 and the fund's expense ratio is embedded
        in a total-return ETF's price, so nothing else changes."""
        return replace(self, slippage_rate=slippage_rate)
