"""strategy/costs.py — Standard per-market transaction cost models
(review_agy.md Section 4, Phase 1).

Purely additive (see base.py's module docstring). KRX_STOCK_COST mirrors the
fee/tax rates strategy.rebalance.backtest.run_rebalance_backtest already
defaults to; nothing authoritative like that exists for US-market costs, or
for trend_following (cost_per_side=0.001, a single flat KR/US-agnostic
figure) or ma_cross (no cost model at all). Adopting a shared model in any
of them is a deliberate, separate follow-up -- not done here -- since for
ma_cross specifically it changes actual backtest output (win rate,
cumulative return), not just how costs are computed.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class TransactionCostModel:
    """One-way commission/tax/slippage rates, applied to notional value
    (price * qty) on each side of a trade."""
    buy_fee_rate: float = 0.0
    sell_fee_rate: float = 0.0
    sell_tax_rate: float = 0.0
    slippage_rate: float = 0.0

    def buy_cost(self, notional: float) -> float:
        return notional * (self.buy_fee_rate + self.slippage_rate)

    def sell_cost(self, notional: float) -> float:
        return notional * (self.sell_fee_rate + self.sell_tax_rate + self.slippage_rate)


# Matches strategy.rebalance.backtest.run_rebalance_backtest's defaults
# (buy_fee_rate=sell_fee_rate=0.00015, sell_tax_rate=0.0018); rebalance has
# no separate slippage parameter, so that stays 0 here too.
KRX_STOCK_COST = TransactionCostModel(buy_fee_rate=0.00015, sell_fee_rate=0.00015, sell_tax_rate=0.0018)

# No US-market cost model exists anywhere in this codebase yet. This is a
# reference starting point only -- ~0 commission (matching most current US
# retail brokers) plus an approximation of the SEC Section 31 fee on sells
# -- not a figure that's been validated against real trading costs.
US_STOCK_COST = TransactionCostModel(buy_fee_rate=0.0, sell_fee_rate=0.0, sell_tax_rate=0.000028)
