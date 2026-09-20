"""strategy/base.py — Shared result/config data model for strategy backtests
(review_agy.md Section 4, Phase 1).

Purely additive: no existing strategy (rebalance/, trend_following/,
ma_cross/) imports this yet. Each currently returns its own ad hoc shape --
ma_cross.backtest.run_backtest_strategy returns a raw tuple, rebalance and
trend_following each return a differently-keyed dict -- and computes its own
Sharpe/CAGR/MDD and transaction costs. Migrating any of them onto this model
is a deliberate, separate follow-up (not done here), since ma_cross has no
cost model at all today and adopting one changes its actual backtest output,
not just how it's computed.
"""
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Trade:
    """One completed round-trip trade, in the shape every strategy's trade
    list should eventually converge on."""
    ticker: str
    entry_date: str
    exit_date: str | None
    entry_price: float
    exit_price: float | None
    qty: float
    weight: float
    price_return_pct: float
    net_return_pct: float
    exit_reason: str | None = None
    days_held: int = 0


@dataclass
class BacktestResult:
    """A single-instrument or portfolio backtest's result, independent of
    which strategy produced it -- so UI/report code can consume any
    strategy's output the same way instead of branching on strategy_name."""
    strategy_name: str
    ticker: str
    summary: dict[str, Any]              # CAGR/Sharpe/MDD/win-rate etc. -- see metrics.py
    trades: list[Trade] = field(default_factory=list)
    equity_curve: list[dict[str, Any]] = field(default_factory=list)     # [{"date": ..., "value": ...}, ...]
    benchmark_curve: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class BaseStrategyConfig:
    """Fields every strategy config is expected to carry. Existing configs
    (RebalanceConfig, TrendFollowingConfig, MaCrossConfig) are not required
    to inherit from this today -- it documents the common surface for a
    strategy that does adopt the shared base.py/metrics.py/costs.py."""
    strategy_name: str = ""
    initial_capital: float = 1.0

    def to_dict(self) -> dict:
        return dict(self.__dict__)
