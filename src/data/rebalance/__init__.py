"""data/rebalance/ — Weekly factor scoring, portfolio rebalancing signals, and walk-forward backtesting.

Package split from the former flat data/rebalance.py (trading.md 11-2, migration
step 11-4.1) — pure reorganization, no logic changes. Re-exports the same names
the flat module exposed so data/__init__.py, data_fetcher.py, ui/auto_trading_tab.py
and tests/ need no changes.
"""
from data.rebalance.config import RebalanceConfig
from data.rebalance.factors import (
    _REBALANCE_FACTORS,
    _REBALANCE_MIN_FACTORS,
    _extract_live_candidates,
    _score_and_rank,
)
from data.rebalance.classify import (
    _DEFAULT_TOP_N_BY_MARKET,
    _classify_buy_sell_hold,
)
from data.rebalance.signals import compute_weekly_rebalance_signals
from data.rebalance.walkforward import (
    _rebalance_friday_dates,
    _compute_historical_factor_series,
    _factor_snapshot_as_of,
    _build_snapshot_lookup,
    _factor_snapshot_at,
    _run_walkforward_simulation,
)
from data.rebalance.backtest import (
    _summarize_backtest,
    run_rebalance_backtest,
)
