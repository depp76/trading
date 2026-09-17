"""strategy/trend_following/config.py — TrendFollowingConfig, the single source of
strategy parameters and risk-gate thresholds (trend_following.md 3, 4)."""
from dataclasses import dataclass


@dataclass
class TrendFollowingConfig:
    # Donchian channel lengths (trend_following.md 3). 20/10 are the prototype values;
    # the final numbers are to be chosen from real-data backtests (trend_following.md 6).
    entry_n: int = 20      # buy when Close breaks above the highest High of the prior entry_n days
    exit_n: int = 10       # sell when Close breaks below the lowest Low of the prior exit_n days

    # Risk gate (trend_following.md 1): Sharpe >= sharpe_min and MDD <= mdd_max_pct.
    sharpe_min: float = 1.5
    mdd_max_pct: float = 15.0

    # Costs per side, as a fraction of traded value. Whether to model them is still an
    # open decision (trend_following.md 6), so both default to 0 = frictionless.
    fee_rate: float = 0.0
    slippage_rate: float = 0.0

    # Annualisation / Sharpe inputs.
    trading_days_per_year: int = 252
    risk_free_rate: float = 0.0   # annual, as a fraction (e.g. 0.03)

    def __post_init__(self):
        if self.entry_n < 1 or self.exit_n < 1:
            raise ValueError("entry_n and exit_n must be >= 1")
        if self.fee_rate < 0 or self.slippage_rate < 0:
            raise ValueError("fee_rate and slippage_rate must be >= 0")

    @property
    def cost_per_side(self) -> float:
        return self.fee_rate + self.slippage_rate
