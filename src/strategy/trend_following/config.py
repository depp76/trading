"""strategy/trend_following/config.py — Parameters of the Trend Following
strategy (trend_following.md v04, sections 2-4, 2-5 and 6-2).

Everything a backtest can vary lives here as a frozen dataclass so a run can
be reproduced from its parameter snapshot. Defaults are the spec's proposed
values; the spec itself says they are "구상안" until the backtests confirm
them, so nothing here is tuned.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict

SPEC_VERSION = "v04"
SPEC_FILE = "trend_following.md"


@dataclass(frozen=True)
class StrategyParams:
    # 2-0 universe
    universe_size: int = 200                 # KOSPI top-N by market cap (current constituents; see 5)
    min_avg_trading_value: float = 1e10      # 20-day average trading value floor (KRW, 100억)
    # 2-1 L1 market regime (weekly check)
    regime_ma: int = 60
    regime_slope_lookback: int = 5
    check_weekday: int = 0                   # 0=Mon .. 4=Fri; the weekly check day (6-4 robustness)
    # 2-2 L2 stock trend (daily)
    trend_ma_short: int = 20
    trend_ma_long: int = 60
    trend_slope_lookback: int = 5
    # 2-3 L3 entry trigger (daily, next-open fill)
    breakout_window: int = 20                # Donchian 20: close > max(high) of the prior 20 sessions
    volume_window: int = 20
    volume_ratio_min: float = 1.5            # V: breakout-day volume >= 1.5 x 20-day average
    flow_long_window: int = 20               # F: 20-day foreign+institution net buy > 0
    flow_short_window: int = 5               # F: 5-day foreign+institution net buy > 0
    # 2-4 L4 ranking and sizing
    max_positions: int = 6                   # 5~8 per the spec
    risk_per_trade: float = 0.01             # position risk = 1% of equity / (2 x ATR20)
    atr_window: int = 20
    atr_multiple: float = 2.0
    max_weight: float = 0.20                 # single-name cap
    rs_window: int = 60                      # relative strength = stock 60d return - KOSPI 60d return
    high_52w_window: int = 252               # variant B: 52-week-high proximity
    # 3 exits (daily, next-open fill)
    exit_window: int = 10                    # Donchian 10: close < min(low) of the prior 10 sessions
    stop_atr_multiple: float = 2.0           # close < entry - 2 x ATR20 (ATR at entry)
    flow_exit_window: int = 10               # FX: 10-day foreign+institution net sell and close < MA20
    # 4 operating assumptions
    seed_krw: float = 10_000_000.0
    annual_reset: bool = True                # restart every year at seed; excess trimmed pro-rata at year end
    max_order_retries: int = 3               # buy orders blocked by limit-up / missing open are retried this often
    delist_after_missing: int = 10           # a held name with no price for N sessions is closed at its last close
    # 6-3 benchmarks
    bm_ma: int = 200                         # BM3: hold the KOSPI 200 ETF when close > MA200
    risk_free_fallback: float = 0.03         # annual CD91 stand-in when no rate series is available
    # 2-5 scoring mode "trend + pullback" (C-series, v04; scoring.py)
    score_ma_long: int = 50                  # MA50Div = close / MA50 - 1 (trend direction, +)
    score_ma_short: int = 20                 # MA20Div = close / MA20 - 1 (overheat gate)
    score_r_short: int = 3                   # R3 = close / close[t-3] - 1 (entry timing, -)
    score_r_mid: int = 10                    # R10 = close / close[t-10] - 1 (short-term reversal, -)
    score_r_mid_weight: float = 0.5          # weight of pct(R10) in the timing score
    score_range_window: int = 252            # Range52 = (close - low252) / (high252 - low252)
    gate_range_min: float = 0.70             # gate: Range52 >= 0.70 (top 30% of the 52-week range)
    gate_overheat_pct: float = 0.95          # gate: MA20Div percentile above this -> no new entry
    score_buy_pct: float = 0.80              # buy: total-score percentile (within gated names) >= 0.80
    score_sell_pct: float = 0.50             # weekly relative sell: percentile < 0.50 (hysteresis)
    score_vol_normalize: bool = True         # divide R3, R10, MA20Div by ATR20 / close
    score_range_mode: str = "range"          # "range" (Range52) | "high52_prox" (close / high252, C3)
    score_week_sessions: int = 5             # Universe recommendation: sessions averaged ("last week")
    score_week_min_sessions: int = 3         # ... and how many of them a name must be scored on

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Variant:
    """One row of the 6-2 comparison matrix."""
    id: str
    regime: bool          # L1 market regime gate
    volume: bool          # V: volume confirmation on entry
    flow: bool            # F: foreign+institution net-buy confirmation on entry
    flow_exit: bool       # FX: flow-based exit
    mode: str = "breakout"   # "breakout" (A-series) or "high52" (B: hold top names by 52-week-high proximity)

    @property
    def needs_flows(self) -> bool:
        return self.flow or self.flow_exit

    @property
    def label(self) -> str:
        if self.mode == "high52":
            return "B: L1 + 52w-high proximity ranking"
        parts = ["L1" if self.regime else "-", "V" if self.volume else "-",
                 "F" if self.flow else "-", "FX" if self.flow_exit else "-"]
        return f"{self.id}: " + "/".join(parts)


VARIANTS: dict[str, Variant] = {
    "A0": Variant("A0", regime=False, volume=False, flow=False, flow_exit=False),
    "A1": Variant("A1", regime=True, volume=False, flow=False, flow_exit=False),
    "A2": Variant("A2", regime=True, volume=True, flow=False, flow_exit=False),
    "A3": Variant("A3", regime=True, volume=False, flow=True, flow_exit=False),
    "A4": Variant("A4", regime=True, volume=True, flow=True, flow_exit=False),
    "A5": Variant("A5", regime=True, volume=True, flow=True, flow_exit=True),
    "B": Variant("B", regime=True, volume=False, flow=False, flow_exit=False, mode="high52"),
}

# 4-1 mandatory sensitivity scenarios: no cost / base / conservative.
COST_MULTIPLIERS: tuple[float, ...] = (0.0, 1.0, 2.0)

# 6-4 sub-period scorecard: (label, first year, last year).
PERIODS: tuple[tuple[str, int, int], ...] = (("2021-22", 2021, 2022), ("2023-24", 2023, 2024), ("2025-26", 2025, 2026))

# 6-3 benchmark data. KODEX 200TR (278530) reinvests dividends, so its price is
# a total-return series with the fund's expense ratio already embedded (no
# separate daily fee deduction, 4-1 (4)); KODEX 200 (069500) is the price-only
# fallback and is flagged in the report because 6-3 forbids price indices.
BM_TR_TICKER = "278530"
BM_PRICE_FALLBACK_TICKER = "069500"
BM_ETF_SLIPPAGE = 0.0002
