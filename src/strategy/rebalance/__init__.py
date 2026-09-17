"""strategy/rebalance/ — Weekly factor scoring, portfolio rebalancing signals, and walk-forward backtesting.

Package split from the former flat data/rebalance.py (rebalance.md 11-2, migration
step 11-4.1) and moved from data/rebalance/ to strategy/rebalance/ on 2026-09-17
(rebalance.md 11-5) — pure reorganization, no logic changes. This __init__ is the
package facade: callers (ui/auto_trading_tab.py, threads/fetch_threads.py,
tests/strategy/rebalance/) import from `strategy.rebalance`, not from
data_fetcher, which no longer re-exports strategy symbols.
"""
from strategy.rebalance.config import RebalanceConfig
from strategy.rebalance.factors import (
    _REBALANCE_FACTORS,
    _REBALANCE_MIN_FACTORS,
    _extract_live_candidates,
    _score_and_rank,
)
from strategy.rebalance.classify import (
    _DEFAULT_TOP_N_BY_MARKET,
    _classify_buy_sell_hold,
)
from strategy.rebalance.signals import compute_weekly_rebalance_signals
from strategy.rebalance.walkforward import (
    _rebalance_friday_dates,
    _compute_historical_factor_series,
    _factor_snapshot_as_of,
    _build_snapshot_lookup,
    _factor_snapshot_at,
    _run_walkforward_simulation,
)
from strategy.rebalance.backtest import (
    _summarize_backtest,
    run_rebalance_backtest,
)
