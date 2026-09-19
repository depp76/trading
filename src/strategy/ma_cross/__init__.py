"""strategy/ma_cross — Single-stock fast/slow MA (20/60) golden-cross backtest.

Spec: ma_cross.md in this folder. Package facade: callers (ui/ma_cross_tab.py,
threads/fetch_threads.py, tests/strategy/ma_cross/) import from
`strategy.ma_cross`. config.py holds the parameters (MaCrossConfig), signals.py
the entry/exit conditions, backtest.py the fills and aggregation.
"""
from strategy.ma_cross.config import MaCrossConfig
from strategy.ma_cross.signals import entry_signal, exit_condition, next_true_index
from strategy.ma_cross.backtest import (
    run_backtest_strategy,
    run_backtest_for_stock,
    run_bulk_backtest_chunk,
)
