"""strategy/trend_following/signals.py — Donchian channel breakout signals
(trend_following.md 3, 5).

No-lookahead contract: every value on row t uses data up to and including the
close of day t only. The channel on day t is built from the *prior* entry_n /
exit_n days (t-n .. t-1), so a breakout is "today's close beyond the range the
market traded in before today". The resulting `position` is the state held at the
close of day t; the backtest applies it from day t+1 (position.shift(1)).
"""
import numpy as np
import polars as pl

from strategy.trend_following.config import TrendFollowingConfig

REQUIRED_COLUMNS = ("Date", "High", "Low", "Close")


def donchian_signal(df: pl.DataFrame, config: TrendFollowingConfig = None) -> pl.DataFrame:
    """Add Donchian channel + entry/exit/position columns to a daily OHLC frame.

    Input: polars DataFrame with at least Date, High, Low, Close (ascending by Date),
    as returned by data.history.get_historical_data(). Rows with a null Close are
    dropped; rows inside the warm-up window (fewer than entry_n prior days) never
    signal.

    Output columns (added): upper, lower, entry, exit, position
      upper    highest High of the prior entry_n days (null during warm-up)
      lower    lowest Low of the prior exit_n days (null during warm-up)
      entry    Close > upper                       (raw breakout, ignores state)
      exit     Close < lower                       (raw breakdown, ignores state)
      position 1 while long, else 0 — long-only, single position, no pyramiding:
               flat -> long on `entry`; long -> flat on `exit`; when both fire on the
               same day the current state decides (a flat book enters, a long book exits).
    """
    config = config or TrendFollowingConfig()
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"donchian_signal: missing columns {missing}")
    if df.is_empty():
        return df.with_columns([
            pl.lit(None, dtype=pl.Float64).alias("upper"),
            pl.lit(None, dtype=pl.Float64).alias("lower"),
            pl.lit(False).alias("entry"),
            pl.lit(False).alias("exit"),
            pl.lit(0, dtype=pl.Int8).alias("position"),
        ])

    out = (
        df.sort("Date")
        .filter(pl.col("Close").is_not_null())
        .with_columns([
            pl.col("High").cast(pl.Float64),
            pl.col("Low").cast(pl.Float64),
            pl.col("Close").cast(pl.Float64),
        ])
        .with_columns([
            # shift(1) so day t's channel covers t-n .. t-1 and never includes day t itself
            pl.col("High").shift(1).rolling_max(window_size=config.entry_n).alias("upper"),
            pl.col("Low").shift(1).rolling_min(window_size=config.exit_n).alias("lower"),
        ])
        .with_columns([
            (pl.col("Close") > pl.col("upper")).fill_null(False).alias("entry"),
            (pl.col("Close") < pl.col("lower")).fill_null(False).alias("exit"),
        ])
    )

    entry = out.get_column("entry").to_numpy()
    exit_ = out.get_column("exit").to_numpy()
    position = np.zeros(len(out), dtype=np.int8)
    in_pos = 0
    for i in range(len(out)):
        if in_pos == 0 and entry[i]:
            in_pos = 1
        elif in_pos == 1 and exit_[i]:
            in_pos = 0
        position[i] = in_pos

    return out.with_columns(pl.Series("position", position, dtype=pl.Int8))
