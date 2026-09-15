"""data/rebalance/config.py — RebalanceConfig, single source of truth for strategy
parameters (trading.md 8-H / 11-4 migration step 3).

Before this file, top_n_by_market/band_multiplier were duplicated in two
places (data/rebalance's function defaults and ui/auto_trading_tab.py's
AutoTradingTab class constants, trading.md 8-2). Collecting them in one
dataclass removes that duplication and gives 8-A/8-B/8-C (sector cap, VKOSPI
regime overlay, position sizing) a single place to add their parameters —
those fields are placeholders here, unused until each feature lands.
"""
from dataclasses import dataclass, field


@dataclass
class RebalanceConfig:
    top_n_by_market: dict = field(default_factory=lambda: {"KOSPI": 10, "KOSDAQ": 10})
    band_multiplier: float = 1.5
    max_per_sector: int = 3            # 8-A (unused until sector cap lands)
    vkospi_elevated_z: float = 1.0     # 8-B (unused until regime overlay lands)
    vkospi_crisis_z: float = 2.0       # 8-B (unused until regime overlay lands)
    sizing_method: str = "equal"       # 8-C: "equal" | "inverse_vol" (unused until sizing lands)
    max_position_weight: float = 0.15  # 8-C (unused until sizing lands)
