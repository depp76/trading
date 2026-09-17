"""strategy/ma_cross — Single-stock MA20/MA60 golden-cross backtest.

Spec: ma_cross.md in this folder. Package facade: callers import from
`strategy.ma_cross` (e.g. tests/strategy/test_ma_cross.py). The strategy logic
lives in backtest.py; parameters are still literals there (ma_cross.md 6).
"""
from strategy.ma_cross.backtest import (
    run_backtest_strategy,
    run_backtest_for_stock,
    run_bulk_backtest_chunk,
)
