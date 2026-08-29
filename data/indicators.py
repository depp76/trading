"""data/indicators.py — Technical indicator calculations using Polars & NumPy."""
from datetime import datetime, timedelta
import polars as pl
import pandas as pd
import numpy as np
import logging

from data.cache import _START_DATE, _CHANGE_KEYS, _TD_PERIODS

logger = logging.getLogger(__name__)


def _to_polars(df_pd):
    if isinstance(df_pd, pl.DataFrame):
        return df_pd
    if df_pd is None or (hasattr(df_pd, 'empty') and getattr(df_pd, 'empty')):
        return pl.DataFrame()
    df = pl.from_pandas(df_pd, include_index=True)
    for alias in ("index", "date", "None"):
        if alias in df.columns:
            df = df.rename({alias: "Date"})
            break
    if "Date" in df.columns:
        date_dtype = df["Date"].dtype
        if isinstance(date_dtype, pl.Datetime):
            if date_dtype.time_zone:
                df = df.with_columns(pl.col("Date").dt.replace_time_zone(None))
            df = df.with_columns(pl.col("Date").dt.date())
        elif not isinstance(date_dtype, pl.Date):
            df = df.with_columns(pl.col("Date").cast(pl.Datetime).dt.date())
        df = df.unique(subset=["Date"], keep="last").sort("Date")
    return df


def _compute_indicators(df: pl.DataFrame, windows=(10, 20, 60)) -> pl.DataFrame:
    """Add RSI14 and MA columns to a Polars OHLCV DataFrame (in-place style)."""
    # Ensure all OHLCV columns exist — batch-add all missing columns in one call
    missing = {c for c in ("Date", "Open", "High", "Low", "Close", "Volume") if c not in df.columns}
    if missing:
        df = df.with_columns([pl.lit(0.0).alias(c) for c in missing])
    df = df.select(["Date", "Open", "High", "Low", "Close", "Volume"])

    # Fallback: fill zero OHL from Close
    is_zero = (pl.col("Open") == 0) & (pl.col("High") == 0) & (pl.col("Low") == 0)
    df = df.with_columns([
        pl.when(is_zero).then(pl.col("Close")).otherwise(pl.col("Open")).alias("Open"),
        pl.when(is_zero).then(pl.col("Close")).otherwise(pl.col("High")).alias("High"),
        pl.when((pl.col("Open") == 0) & (pl.col("Low") == 0))
          .then(pl.col("Close")).otherwise(pl.col("Low")).alias("Low"),
    ])

    # RSI 14 (Wilder EMA) + MA — single consolidated with_columns pass
    # Computing gain/loss inline avoids two separate mutation calls.
    delta     = pl.col("Close") - pl.col("Close").shift(1)
    gain      = pl.when(delta > 0).then(delta).otherwise(0)
    loss      = pl.when(delta < 0).then(-delta).otherwise(0)
    avg_gain  = gain.ewm_mean(com=13, adjust=False)
    avg_loss  = loss.ewm_mean(com=13, adjust=False)
    rsi14     = 100 - (100 / (1 + (avg_gain / avg_loss).fill_nan(1e9)))

    df = df.with_columns([
        *[pl.col("Close").rolling_mean(window_size=w, min_samples=1).alias(f"MA{w}") for w in windows],
        # MA50 is always computed (needed for divergence chart regardless of windows)
        pl.col("Close").rolling_mean(window_size=50, min_samples=1).alias("MA50"),
        rsi14.alias("RSI14"),
    ])
    # MA divergence ratios (base = 100%): current_price / MA_N * 100
    div_exprs = [(pl.col("Close") / pl.col("MA50") * 100).alias("MA50_Div")]
    if "MA20" in df.columns:
        div_exprs.append((pl.col("Close") / pl.col("MA20") * 100).alias("MA20_Div"))
    df = df.with_columns(div_exprs)
    return df


def fetch_historical_changes(ticker, current_price, df_pd=None, mode='pct'):
    """Calculates historical changes (single-pass numpy, no redundant I/O).

    mode: 'pct' | 'bp' | 'abs'
    """
    changes = {k: 0.0 for k in _CHANGE_KEYS}
    changes.update({
        "52w_high": 0.0, "52w_low": 0.0,
        "52w_high_diff": 0.0, "52w_low_diff": 0.0,
        "ma20_div": 0.0, "ma50_div": 0.0
    })

    if current_price <= 0:
        return changes

    try:
        if df_pd is None:
            from data.market import get_historical_data
            df = get_historical_data(ticker, _START_DATE)
        else:
            df = _to_polars(df_pd)
        if df.is_empty():
            return changes

        today = datetime.now()

        # ── 52-week high/low (based on closing price at market close) ────────────────
        cutoff_52w = (today - timedelta(days=365)).date()
        df_52w = df.filter(pl.col("Date") >= cutoff_52w)
        if df_52w.is_empty():
            df_52w = df
        high_val = df_52w.select(pl.col("Close").max()).item()
        low_val  = df_52w.select(pl.col("Close").min()).item()
        high_52w = float(high_val) if high_val is not None else 0.0
        low_52w  = float(low_val)  if low_val  is not None else 0.0
        changes["52w_high"] = high_52w
        changes["52w_low"]  = low_52w

        if high_52w > 0:
            if mode == 'bp':
                changes["52w_high_diff"] = (current_price - high_52w) * 100
            elif mode == 'abs':
                changes["52w_high_diff"] = current_price - high_52w
            else:
                changes["52w_high_diff"] = (current_price - high_52w) / high_52w * 100
        if low_52w > 0:
            if mode == 'bp':
                changes["52w_low_diff"] = (current_price - low_52w) * 100
            elif mode == 'abs':
                changes["52w_low_diff"] = current_price - low_52w
            else:
                changes["52w_low_diff"] = (current_price - low_52w) / low_52w * 100

        # ── Period changes (single numpy array pass) ───────────────────
        closes = df.filter(pl.col("Close").is_not_null()).sort("Date").get_column("Close").to_numpy()
        n = len(closes)
        if n == 0:
            return changes

        for label, td in _TD_PERIODS.items():
            idx = n - 1 - td
            if 0 <= idx < n:
                old_price = float(closes[idx])
                if old_price > 0:
                    if mode == 'bp':
                        changes[label] = (current_price - old_price) * 100
                    elif mode == 'abs':
                        changes[label] = old_price
                    else:
                        changes[label] = (current_price - old_price) / old_price * 100

        # ── MA20 divergence ratio (pct mode only, base = 100%) ───────────
        if mode == 'pct' and n >= 20:
            ma20 = float(np.mean(closes[-20:]))
            if ma20 > 0:
                changes["ma20_div"] = current_price / ma20 * 100

        # ── MA50 divergence ratio (pct mode only, base = 100%) ───────────
        if mode == 'pct' and n >= 50:
            ma50 = float(np.mean(closes[-50:]))
            if ma50 > 0:
                changes["ma50_div"] = current_price / ma50 * 100

    except Exception as e:
        logger.error("fetch_historical_changes failed for ticker=%s", ticker, exc_info=True)

    return changes
