"""strategy/trend_following — Donchian channel breakout trend following.

Spec: trend_following.md in this folder. Package facade (strategy/rebalance pattern):
callers import from `strategy.trend_following`.

  config.py    TrendFollowingConfig — entry_n / exit_n, risk gate, costs
  signals.py   donchian_signal(df, config) — channel + entry/exit/position (no lookahead)
  backtest.py    run_backtest(df, config), run_backtest_for_ticker(ticker, start, config)
  portfolio.py   v3: run_portfolio_backtest(histories, config) — equal-sleeve constant mix
  validation.py  holdout_validation / walk_forward_validation — IS/OOS parameter selection

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
]
