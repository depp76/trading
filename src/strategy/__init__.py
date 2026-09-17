"""strategy — Trading strategy logic (signals, classification, backtests).

`data/` is the pure data-access layer (quotes, history, indicators, caches);
everything that decides what to buy or sell lives here (rebalance.md 11-5):

  strategy/rebalance/        weekly factor-scoring portfolio rebalance (rebalance.md)
  strategy/ma_cross.py       single-stock MA20/MA60 golden-cross backtest
  strategy/trend_following/  Donchian channel breakout (trend_following.md, scaffold)

Strategy packages import from `data.*`; nothing in `data/` imports `strategy`.
Callers (UI, threads, tests) import strategy symbols from their own package,
e.g. `from strategy.rebalance import compute_weekly_rebalance_signals`, not
through the `data_fetcher` facade.
"""
