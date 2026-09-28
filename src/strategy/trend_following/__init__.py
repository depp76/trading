"""strategy/trend_following — Donchian channel breakout trend following.

Spec: trend_following.md in this folder. Package facade (strategy/rebalance pattern):
callers import from `strategy.trend_following`.

  config.py    TrendFollowingConfig — entry_n / exit_n, risk gate, costs
  signals.py   donchian_signal(df, config) — channel + entry/exit/position (no lookahead)
  backtest.py    run_backtest(df, config), run_backtest_for_ticker(ticker, start, config)
  portfolio.py   v3: run_portfolio_backtest(histories, config) — equal-sleeve constant mix
  validation.py  holdout_validation / walk_forward_validation — IS/OOS parameter selection

The modules above implement the *previous* design (single instrument + overlays, equal
sleeves); the spec was rewritten on 2026-09-28 (trend_following.md 5 "기존 코드와의 관계")
and the current strategy — the KR Donchian 20/10 portfolio with yearly capital reset —
lives in the v1 modules, which reuse only donchian_signal():

  config_v1.py      KrTrendConfig — every parameter of trend_following.md 2
  universe.py       kr_universe_candidates(all_data), yearly_members(histories, year, cfg)  (2-1)
  engine.py         run_kr_trend(histories, index_df, cfg, start, end)  (3-2 daily loop)
  annual.py         plan_year_end_harvest / topup_amount / annual_summary  (2-5, 3-3)
  validation_v1.py  compare_assumptions / parameter_sensitivity / walk_forward_years  (4장)

Data comes from data.history.get_historical_data(); UI in ui/trend_following_tab.py.
"""
from strategy.trend_following.config import TrendFollowingConfig
from strategy.trend_following.signals import donchian_signal, REQUIRED_COLUMNS
from strategy.trend_following.backtest import run_backtest, run_backtest_for_ticker, return_metrics
from strategy.trend_following.portfolio import run_portfolio_backtest, run_portfolio_backtest_for_tickers
from strategy.trend_following.validation import (
    DEFAULT_GRID,
    holdout_validation,
    walk_forward_validation,
    yearly_folds,
    window_metrics,
)
from strategy.trend_following.config_v1 import KrTrendConfig
from strategy.trend_following.universe import kr_universe_candidates, yearly_members, average_trading_value
from strategy.trend_following.engine import run_kr_trend
from strategy.trend_following.annual import HarvestPlan, plan_year_end_harvest, topup_amount, annual_summary
from strategy.trend_following.validation_v1 import (
    ASSUMPTION_VARIANTS,
    compare_assumptions,
    parameter_sensitivity,
    walk_forward_years,
    abnormal_return_rows,
    summarize_run,
)

__all__ = [
    "TrendFollowingConfig",
    "donchian_signal",
    "REQUIRED_COLUMNS",
    "run_backtest",
    "run_backtest_for_ticker",
    "return_metrics",
    "run_portfolio_backtest",
    "run_portfolio_backtest_for_tickers",
    "DEFAULT_GRID",
    "holdout_validation",
    "walk_forward_validation",
    "yearly_folds",
    "window_metrics",
    # v1 (trend_following.md 2026-09-28)
    "KrTrendConfig",
    "kr_universe_candidates",
    "yearly_members",
    "average_trading_value",
    "run_kr_trend",
    "HarvestPlan",
    "plan_year_end_harvest",
    "topup_amount",
    "annual_summary",
    "ASSUMPTION_VARIANTS",
    "compare_assumptions",
    "parameter_sensitivity",
    "walk_forward_years",
    "abnormal_return_rows",
    "summarize_run",
]
