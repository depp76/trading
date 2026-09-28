"""strategy/trend_following/config_v1.py — KrTrendConfig, the parameters of the KR
Donchian 20/10 portfolio strategy (trend_following.md 2, 5).

Every default reproduces the base case of trend_following.md 2 exactly; the string
"mode" fields exist so validation_v1.py can run the alternatives listed in
trend_following.md 4 (table "1단계 — 가정별 영향 비교") through the same engine.
"""
from dataclasses import dataclass, field

from strategy.costs import KRX_STOCK_COST, TransactionCostModel

EXIT_CHECKS = ("daily", "weekly")
ENTRY_CHECKS = ("weekly", "daily")
WEEKLY_ENTRY_RULES = ("held_breakout", "strict")
RANK_BYS = ("breakout_strength",)
FILL_ATS = ("next_open", "signal_close")
HARVEST_MODES = ("pro_rata", "cash_first", "none")
UNIVERSE_MODES = ("yearly_top", "fixed")


@dataclass
class KrTrendConfig:
    # ── 2-2 signals ──────────────────────────────────────────────────────────
    entry_n: int = 20                 # breakout above the highest High of the prior entry_n days
    exit_n: int = 10                  # breakdown below the lowest Low of the prior exit_n days
    atr_n: int = 20                   # ATR window (simple mean of the true range)
    stop_atr_mult: float = 3.0        # trailing stop = highest close since entry - mult * ATR (0 = off)
    index_regime_ma_n: int = 200      # new buys only while KOSPI close > SMA(n); 0 = no market filter

    # ── 2-1 yearly candidate selection ───────────────────────────────────────
    universe_mode: str = "yearly_top"     # "yearly_top" (2-1) | "fixed" (all tickers every year; 4장 survivorship check)
    universe_top_m: int = 100             # top-M by 60-day average trading value
    trading_value_n: int = 60             # lookback (trading days) for the average trading value
    min_trading_value: float = 1e9        # KRW; below this a stock is never a candidate
    min_history_days: int = 250           # trading days listed before the selection date

    # ── 2-4 position sizing ──────────────────────────────────────────────────
    max_positions: int = 10
    risk_per_trade: float = 0.0075        # fraction of equity lost if the initial stop is hit
    max_position_weight: float = 0.20     # cap at entry and the weekly trim threshold

    # ── 2-3 schedule ─────────────────────────────────────────────────────────
    exit_check: str = "daily"             # "daily" | "weekly"
    entry_check: str = "weekly"           # "weekly" | "daily"
    weekly_entry_rule: str = "held_breakout"   # "held_breakout" (2-3 condition 1) | "strict" (close > this week's upper)
    rank_by: str = "breakout_strength"    # (close - upper_ref) / ATR, descending
    fill_at: str = "next_open"            # "next_open" | "signal_close"

    # ── 2-6 costs ────────────────────────────────────────────────────────────
    cost_model: TransactionCostModel = field(default_factory=lambda: KRX_STOCK_COST)
    slippage_rate: float = 0.001          # one-way, applied to open fills (not to the year-end close fill)

    # ── 2-5 annual capital ───────────────────────────────────────────────────
    base_capital: float = 10_000_000.0
    harvest_mode: str = "pro_rata"        # "pro_rata" | "cash_first" | "none"
    topup_on_loss: bool = True

    # ── 3-4 risk gate ────────────────────────────────────────────────────────
    sharpe_min: float = 1.5
    mdd_max_pct: float = 15.0
    trading_days_per_year: int = 252

    def __post_init__(self):
        if self.entry_n < 1 or self.exit_n < 1 or self.atr_n < 1:
            raise ValueError("entry_n, exit_n and atr_n must be >= 1")
        if self.stop_atr_mult < 0 or self.index_regime_ma_n < 0:
            raise ValueError("stop_atr_mult and index_regime_ma_n must be >= 0 (0 = off)")
        if self.universe_mode not in UNIVERSE_MODES:
            raise ValueError(f"universe_mode must be one of {UNIVERSE_MODES}")
        if self.universe_top_m < 1 or self.trading_value_n < 1 or self.min_history_days < 0:
            raise ValueError("universe_top_m and trading_value_n must be >= 1, min_history_days >= 0")
        if self.max_positions < 1:
            raise ValueError("max_positions must be >= 1")
        if not 0 < self.risk_per_trade <= 1 or not 0 < self.max_position_weight <= 1:
            raise ValueError("risk_per_trade and max_position_weight must be in (0, 1]")
        if self.exit_check not in EXIT_CHECKS:
            raise ValueError(f"exit_check must be one of {EXIT_CHECKS}")
        if self.entry_check not in ENTRY_CHECKS:
            raise ValueError(f"entry_check must be one of {ENTRY_CHECKS}")
        if self.weekly_entry_rule not in WEEKLY_ENTRY_RULES:
            raise ValueError(f"weekly_entry_rule must be one of {WEEKLY_ENTRY_RULES}")
        if self.rank_by not in RANK_BYS:
            raise ValueError(f"rank_by must be one of {RANK_BYS}")
        if self.fill_at not in FILL_ATS:
            raise ValueError(f"fill_at must be one of {FILL_ATS}")
        if self.harvest_mode not in HARVEST_MODES:
            raise ValueError(f"harvest_mode must be one of {HARVEST_MODES}")
        if self.slippage_rate < 0:
            raise ValueError("slippage_rate must be >= 0")
        if self.base_capital <= 0:
            raise ValueError("base_capital must be > 0")

    @property
    def stop_enabled(self) -> bool:
        return self.stop_atr_mult > 0

    @property
    def regime_enabled(self) -> bool:
        return self.index_regime_ma_n > 0

    def position_weight(self, close: float, atr: float) -> float:
        """2-4: weight = min(risk_per_trade / (stop_atr_mult * ATR / close), max_position_weight).
        Without a stop the risk-based term is undefined, so the cap applies."""
        if not self.stop_enabled or atr is None or close is None or atr <= 0 or close <= 0:
            return self.max_position_weight
        stop_frac = self.stop_atr_mult * atr / close
        return float(min(self.risk_per_trade / stop_frac, self.max_position_weight))

    def label(self) -> str:
        return (f"{self.entry_n}/{self.exit_n} atr{self.atr_n}x{self.stop_atr_mult:g} "
                f"pos{self.max_positions} risk{self.risk_per_trade * 100:g}% "
                f"{self.entry_check}/{self.exit_check} {self.fill_at} {self.harvest_mode}")
