"""strategy — Trading strategy logic (signals, classification, backtests).

`data/` is the pure data-access layer (quotes, history, indicators, caches);
everything that decides what to buy or sell lives here (rebalance.md 11-5):

  strategy/rebalance/        weekly factor-scoring portfolio rebalance (rebalance.md)
  strategy/ma_cross/         single-stock MA20/MA60 golden-cross backtest (ma_cross.md)
  strategy/trend_following/  Donchian channel breakout backtest (trend_following.md)

Each strategy is its own sub-package with its spec saved as <name>.md in the
same folder (user direction, 2026-09-17).

Strategy packages import from `data.*`; nothing in `data/` imports `strategy`.
Callers (UI, threads, tests) import strategy symbols from their own package,
e.g. `from strategy.rebalance import compute_weekly_rebalance_signals`, not
through the `data_fetcher` facade.

`base.py`/`metrics.py`/`costs.py` (review_agy.md Section 4, Phase 1) are
shared building blocks -- a common result/config data model, a Sharpe/CAGR/
MDD calculator, and standard per-market transaction cost models -- meant for
strategies to eventually converge on instead of each computing its own.
Purely additive for now: no existing strategy sub-package imports them yet.
"""
