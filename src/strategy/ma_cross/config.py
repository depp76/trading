"""strategy/ma_cross/config.py — MaCrossConfig, the single source of the
strategy's parameters (ma_cross.md 3, 6).

Until 2026-09-19 these were literals inside backtest.py (1.10 entry multiple,
1.30 take-profit / overheat multiple, MA20/MA60); the defaults here reproduce
that behaviour exactly.
"""
from dataclasses import dataclass


@dataclass
class MaCrossConfig:
    fast_n: int = 20             # fast moving average window (days)
    slow_n: int = 60             # slow moving average window (days)
    # Entry: fast MA crosses above slow MA x entry_mult (ma_cross.md 3, 진입).
    entry_mult: float = 1.10
    # Exit 1: take profit once the running max close >= buy price x take_profit_mult.
    take_profit_mult: float = 1.30
    # Exit 2: overheat, fast MA >= slow MA x overheat_mult. (Exit 3, the dead
    # cross fast < slow, has no parameter.)
    overheat_mult: float = 1.30
    # Trailing calendar days of history when no target_year is given.
    days: int = 1095

    def __post_init__(self):
        if self.fast_n < 1 or self.slow_n < 1:
            raise ValueError("fast_n and slow_n must be >= 1")
        if self.fast_n >= self.slow_n:
            raise ValueError("fast_n must be shorter than slow_n")
        if self.entry_mult <= 0 or self.take_profit_mult <= 1.0 or self.overheat_mult <= 1.0:
            raise ValueError("entry_mult must be > 0; take_profit_mult and overheat_mult must be > 1")
        if self.days < self.slow_n:
            raise ValueError("days must cover at least one slow_n window")

    @property
    def fast_col(self) -> str:
        return f"MA{self.fast_n}"

    @property
    def slow_col(self) -> str:
        return f"MA{self.slow_n}"

    @property
    def windows(self) -> tuple:
        """MA windows to ask data.market.fetch_stock_ma_multi() for."""
        return tuple(sorted({10, self.fast_n, self.slow_n}))
