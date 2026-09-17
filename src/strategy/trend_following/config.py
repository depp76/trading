"""strategy/trend_following/config.py — TrendFollowingConfig, the single source of
strategy parameters and risk-gate thresholds (trend_following.md 3, 4).

v1 (Donchian breakout only) is the default: every v2 overlay below is off unless its
parameter is set, so a default config reproduces the v1 results exactly.
"""
from dataclasses import dataclass


@dataclass
class TrendFollowingConfig:
    # ── v1: Donchian channel (trend_following.md 3) ───────────────────────────
    # 20/10 are the prototype values; the final numbers are to be chosen from real-data
    # backtests (trend_following.md 6).
    entry_n: int = 20      # buy when Close breaks above the highest High of the prior entry_n days
    exit_n: int = 10       # sell when Close breaks below the lowest Low of the prior exit_n days

    # ── v2 overlays (trend_following.md 3, "v2") — 0 = off ───────────────────
    # Regime filter: new entries only while Close > SMA(regime_ma_n). Exits are unaffected.
    regime_ma_n: int = 0
    # ATR stop: exit when Close < stop. "trailing" (chandelier) re-anchors the stop to the
    # highest close since entry each day; "fixed" keeps the level set at entry.
    stop_atr_mult: float = 0.0
    stop_mode: str = "trailing"          # "trailing" | "fixed"
    atr_n: int = 14
    # Volatility-target sizing: position weight = min(max_weight, vol_target / realized vol),
    # fixed for the life of the trade using the vol known at entry.
    vol_target_pct: float = 0.0          # annualised, e.g. 20.0
    vol_n: int = 20                      # lookback (days) for realized vol
    max_weight: float = 1.0              # cap (1.0 = no leverage); also the weight when sizing is off

    # ── Risk gate (trend_following.md 1): Sharpe >= sharpe_min and MDD <= mdd_max_pct ──
    sharpe_min: float = 1.5
    mdd_max_pct: float = 15.0

    # ── Costs per side, as a fraction of the traded weight ────────────────────
    # Whether to model them is still an open decision (trend_following.md 6), so both
    # default to 0 = frictionless.
    fee_rate: float = 0.0
    slippage_rate: float = 0.0

    # ── Annualisation / Sharpe inputs ─────────────────────────────────────────
    trading_days_per_year: int = 252
    risk_free_rate: float = 0.0   # annual, as a fraction (e.g. 0.03)

    def __post_init__(self):
        if self.entry_n < 1 or self.exit_n < 1:
            raise ValueError("entry_n and exit_n must be >= 1")
        if self.fee_rate < 0 or self.slippage_rate < 0:
            raise ValueError("fee_rate and slippage_rate must be >= 0")
        if self.regime_ma_n < 0 or self.stop_atr_mult < 0 or self.vol_target_pct < 0:
            raise ValueError("regime_ma_n, stop_atr_mult and vol_target_pct must be >= 0 (0 = off)")
        if self.atr_n < 1 or self.vol_n < 2:
            raise ValueError("atr_n must be >= 1 and vol_n >= 2")
        if self.stop_mode not in ("trailing", "fixed"):
            raise ValueError("stop_mode must be 'trailing' or 'fixed'")
        if self.max_weight <= 0:
            raise ValueError("max_weight must be > 0")

    @property
    def cost_per_side(self) -> float:
        return self.fee_rate + self.slippage_rate

    @property
    def regime_enabled(self) -> bool:
        return self.regime_ma_n > 0

    @property
    def stop_enabled(self) -> bool:
        return self.stop_atr_mult > 0

    @property
    def sizing_enabled(self) -> bool:
        return self.vol_target_pct > 0

    @property
    def is_v1(self) -> bool:
        return not (self.regime_enabled or self.stop_enabled or self.sizing_enabled) and self.max_weight == 1.0
