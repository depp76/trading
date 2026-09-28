"""strategy/trend_following/signals.py — Layer signals of trend_following.md
(2-1 market regime, 2-2 stock trend, 2-3 entry trigger, 2-4 ranking inputs,
3 exit conditions) computed as date x ticker arrays.

Everything here is vectorised over a ``Dataset`` (see dataset.py): the engine
in backtest.py only indexes these arrays at decision time. Rolling windows use
``min_periods == window`` so the first rows are NaN, which every comparison
turns into False -- a name with too little history can neither pass a filter
nor trigger an entry. Flow-based signals stay all-False (and the strength all
NaN) when the dataset has no investor-flow data.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

from strategy.trend_following.config import StrategyParams


# ---------------------------------------------------------------------------
# Rolling helpers (pandas does the windowing; inputs/outputs are numpy)
# ---------------------------------------------------------------------------
def rolling(a: np.ndarray, window: int, fn: str, min_periods: int | None = None) -> np.ndarray:
    """Rolling `fn` ('mean' | 'sum' | 'max' | 'min' | 'std') along axis 0."""
    arr = np.asarray(a, dtype=float)
    two_d = arr.ndim == 2
    frame = pd.DataFrame(arr if two_d else arr[:, None])
    out = getattr(frame.rolling(window, min_periods=min_periods or window), fn)().to_numpy()
    return out if two_d else out[:, 0]


def shift(a: np.ndarray, n: int) -> np.ndarray:
    """Shift along axis 0 by n rows (positive = look back), NaN-filled."""
    arr = np.asarray(a, dtype=float)
    out = np.full_like(arr, np.nan)
    if n <= 0:
        return arr.copy() if n == 0 else out
    if n < arr.shape[0]:
        out[n:] = arr[:-n]
    return out


def true_range(high: np.ndarray, low: np.ndarray, close: np.ndarray) -> np.ndarray:
    prev_close = shift(close, 1)
    hl = high - low
    hc = np.abs(high - prev_close)
    lc = np.abs(low - prev_close)
    return np.fmax(hl, np.fmax(hc, lc))


def atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, window: int) -> np.ndarray:
    """Average True Range as a simple rolling mean of the true range."""
    return rolling(true_range(high, low, close), window, "mean")


# ---------------------------------------------------------------------------
# 2-1 L1 market regime (weekly check)
# ---------------------------------------------------------------------------
def weekly_check_days(dates: list[date], weekday: int) -> np.ndarray:
    """Boolean mask of the one trading day per ISO week on which the weekly
    checks run: the first trading day whose weekday is >= `weekday`
    (0=Mon .. 4=Fri), or the week's last trading day if that weekday and every
    later one were holidays."""
    n = len(dates)
    mask = np.zeros(n, dtype=bool)
    if n == 0:
        return mask
    weekday = int(min(max(weekday, 0), 4))
    start = 0
    while start < n:
        y, w, _ = dates[start].isocalendar()
        end = start
        while end + 1 < n and dates[end + 1].isocalendar()[:2] == (y, w):
            end += 1
        pick = end
        for i in range(start, end + 1):
            if dates[i].weekday() >= weekday:
                pick = i
                break
        mask[pick] = True
        start = end + 1
    return mask


def regime_state(index_close: np.ndarray, dates: list[date], params: StrategyParams) -> tuple[np.ndarray, np.ndarray]:
    """(regime_on, check_day): risk-on when KOSPI close > MA60 and MA60 is
    above its value 5 sessions earlier, evaluated on the weekly check day and
    held until the next one (2-1)."""
    close = np.asarray(index_close, dtype=float)
    ma = rolling(close, params.regime_ma, "mean")
    raw_ok = (close > ma) & (ma > shift(ma, params.regime_slope_lookback))
    check_day = weekly_check_days(dates, params.check_weekday)
    state = np.zeros(len(close), dtype=bool)
    current = False
    seen_check = False
    for t in range(len(close)):
        if check_day[t] or not seen_check:
            current = bool(raw_ok[t])
            seen_check = seen_check or check_day[t]
        state[t] = current
    return state, check_day


# ---------------------------------------------------------------------------
# Feature bundle
# ---------------------------------------------------------------------------
@dataclass
class Features:
    """date x ticker signal arrays (bool unless noted) plus the 1-D regime."""
    trend_ok: np.ndarray        # 2-2 L2: close > MA60, MA60 rising, MA20 > MA60
    breakout: np.ndarray        # 2-3 price condition: close > prior 20-session high
    vol_ok: np.ndarray          # 2-3 V
    flow_ok: np.ndarray         # 2-3 F
    liquid: np.ndarray          # 2-0 liquidity floor on 20-day average trading value
    exit_channel: np.ndarray    # 3-1: close < prior 10-session low
    flow_exit: np.ndarray       # 3-3 FX: 10-day net sell and close < MA20
    atr: np.ndarray             # float: ATR20
    adv: np.ndarray             # float: 20-day average trading value (KRW)
    flow_strength: np.ndarray   # float: 20-day FI net buy / 20-day avg trading value (NaN without flows)
    rs: np.ndarray              # float: 60-day return minus KOSPI 60-day return
    high52_prox: np.ndarray     # float: close / 252-session high (variant B)
    ma_short: np.ndarray        # float: MA20
    regime_on: np.ndarray       # 1-D bool, weekly-held L1 state
    check_day: np.ndarray       # 1-D bool, weekly check days
    has_flows: bool
    flow_coverage: float        # fraction of (date, ticker) cells with a flow value


def compute_features(ds, params: StrategyParams) -> Features:
    """All layer signals for `ds` (a dataset.Dataset) under `params`."""
    b = ds.stocks
    close, high, low, vol = b.close, b.high, b.low, b.volume
    p = params

    value = close * vol
    adv = rolling(value, p.volume_window, "mean")
    liquid = adv >= p.min_avg_trading_value

    ma_s = rolling(close, p.trend_ma_short, "mean")
    ma_l = rolling(close, p.trend_ma_long, "mean")
    trend_ok = (close > ma_l) & (ma_l > shift(ma_l, p.trend_slope_lookback)) & (ma_s > ma_l)

    prev_high = shift(rolling(high, p.breakout_window, "max"), 1)
    breakout = close > prev_high

    vol_avg_prev = shift(rolling(vol, p.volume_window, "mean"), 1)
    vol_ok = vol >= p.volume_ratio_min * vol_avg_prev

    prev_low = shift(rolling(low, p.exit_window, "min"), 1)
    exit_channel = close < prev_low

    atr_arr = atr(high, low, close, p.atr_window)

    ret_w = close / shift(close, p.rs_window) - 1.0
    idx = np.asarray(ds.index_close, dtype=float)
    idx_ret = idx / shift(idx, p.rs_window) - 1.0
    rs = ret_w - idx_ret[:, None]

    high52 = rolling(high, p.high_52w_window, "max", min_periods=max(20, int(p.high_52w_window * 0.8)))
    high52_prox = close / high52

    fi = getattr(ds, "flow_fi", None)
    has_flows = fi is not None and bool(np.isfinite(fi).any())
    if has_flows:
        fi = np.asarray(fi, dtype=float)
        flow_long = rolling(fi, p.flow_long_window, "sum")
        flow_short = rolling(fi, p.flow_short_window, "sum")
        flow_exit_sum = rolling(fi, p.flow_exit_window, "sum")
        with np.errstate(divide="ignore", invalid="ignore"):
            flow_strength = np.where(adv > 0, flow_long / adv, np.nan)
        flow_ok = (flow_long > 0) & (flow_short > 0)
        flow_exit = (flow_exit_sum < 0) & (close < ma_s)
        flow_coverage = float(np.isfinite(fi).mean()) if fi.size else 0.0
    else:
        flow_strength = np.full(close.shape, np.nan)
        flow_ok = np.zeros(close.shape, dtype=bool)
        flow_exit = np.zeros(close.shape, dtype=bool)
        flow_coverage = 0.0

    regime_on, check_day = regime_state(idx, b.dates, p)

    return Features(
        trend_ok=trend_ok, breakout=breakout, vol_ok=vol_ok, flow_ok=flow_ok, liquid=liquid,
        exit_channel=exit_channel, flow_exit=flow_exit, atr=atr_arr, adv=adv,
        flow_strength=flow_strength, rs=rs, high52_prox=high52_prox, ma_short=ma_s,
        regime_on=regime_on, check_day=check_day, has_flows=has_flows, flow_coverage=flow_coverage,
    )


def rank_desc_score(*columns: np.ndarray) -> np.ndarray:
    """2-4 ranking score: sum of ascending ranks (1 = lowest) of each column,
    so the best candidate has the highest score. NaN ranks lowest."""
    if not columns:
        return np.zeros(0)
    n = len(columns[0])
    score = np.zeros(n, dtype=float)
    for col in columns:
        v = np.where(np.isfinite(col), col, -np.inf)
        order = np.argsort(v, kind="stable")
        ranks = np.empty(n, dtype=float)
        ranks[order] = np.arange(1, n + 1, dtype=float)
        score += ranks
    return score
