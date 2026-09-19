"""strategy/ma_cross/signals.py — entry/exit conditions of the MA cross strategy
(ma_cross.md 3), as pure numpy over the fast/slow MA arrays. backtest.py turns
these into fills and P/L; nothing here touches data or dates.
"""
import numpy as np

from strategy.ma_cross.config import MaCrossConfig


def entry_signal(ma_fast, ma_slow, config: MaCrossConfig = None):
    """True on the days the fast MA crosses above slow MA x entry_mult:
    yesterday fast <= slow x m and today fast > slow x m (ma_cross.md 3, 진입).
    Day 0 is never a signal (no previous day)."""
    cfg = config or MaCrossConfig()
    fast = np.asarray(ma_fast, dtype=float)
    slow = np.asarray(ma_slow, dtype=float)
    level = slow * cfg.entry_mult
    prev_fast = np.roll(fast, 1)
    prev_level = np.roll(level, 1)
    prev_fast[0] = np.nan
    prev_level[0] = np.nan
    return (prev_fast <= prev_level) & (fast > level)


def exit_condition(ma_fast, ma_slow, config: MaCrossConfig = None):
    """True on the days an open position must be closed for an MA reason
    (ma_cross.md 3, 청산 2·3): overheat (fast >= slow x overheat_mult) or the
    dead cross (fast < slow). The take-profit exit depends on the entry price,
    so it lives in backtest.py."""
    cfg = config or MaCrossConfig()
    fast = np.asarray(ma_fast, dtype=float)
    slow = np.asarray(ma_slow, dtype=float)
    return (fast >= slow * cfg.overheat_mult) | (fast < slow)


def next_true_index(mask) -> np.ndarray:
    """For every day i, the index of the first True at or after i (len(mask)
    when there is none) -- the "first exit day after entry" lookup that keeps
    the backtest loop proportional to the number of entries, not days."""
    mask = np.asarray(mask, dtype=bool)
    n = len(mask)
    idx_if_true = np.where(mask, np.arange(n), n)
    return np.minimum.accumulate(idx_if_true[::-1])[::-1]
