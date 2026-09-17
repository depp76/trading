"""strategy/trend_following — Donchian channel breakout trend following.

Spec: trend_following.md in this folder. Package facade (strategy/rebalance pattern):
callers import from `strategy.trend_following`.

  config.py    TrendFollowingConfig — entry_n / exit_n, risk gate, costs
  signals.py   donchian_signal(df, config) — channel + entry/exit/position (no lookahead)
  backtest.py  run_backtest(df, config), run_backtest_for_ticker(ticker, start, config)

Data comes from data.history.get_historical_data(); no UI wiring yet.
"""
from strategy.trend_following.config import TrendFollowingConfig
from strategy.trend_following.signals import donchian_signal, REQUIRED_COLUMNS
from strategy.trend_following.backtest import run_backtest, run_backtest_for_ticker

__all__ = [
    "TrendFollowingConfig",
    "donchian_signal",
    "REQUIRED_COLUMNS",
    "run_backtest",
    "run_backtest_for_ticker",
]
