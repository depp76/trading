"""strategy/trend_following/signals.py — Donchian channel breakout signals with the
v2 overlays: regime filter, ATR stop, volatility-target sizing (trend_following.md 3, 5).

No-lookahead contract: every value on row t uses data up to and including the
close of day t only. The channel on day t is built from the *prior* entry_n /
exit_n days (t-n .. t-1), so a breakout is "today's close beyond the range the
market traded in before today". The stop level that applies on day t is fixed at
the close of day t-1. The resulting `position`/`weight` are the state held at the
close of day t; the backtest applies them from day t+1 (shift(1)).
"""
import math

import numpy as np
import polars as pl

from strategy.trend_following.config import TrendFollowingConfig

REQUIRED_COLUMNS = ("Date", "High", "Low", "Close")
SIGNAL_COLUMNS = ("upper", "lower", "atr", "regime_ma", "regime_ok", "entry", "exit",
                  "position", "weight", "stop", "exit_reason")


def donchian_signal(df: pl.DataFrame, config: TrendFollowingConfig = None) -> pl.DataFrame:
    """Add Donchian channel + v2 overlay columns + entry/exit/position to a daily OHLC frame.

    Input: polars DataFrame with at least Date, High, Low, Close (ascending by Date),
    as returned by data.history.get_historical_data(). Rows with a null Close are
    dropped; rows inside the warm-up window (fewer than entry_n prior days) never
    signal.

    Output columns (added):
      upper       highest High of the prior entry_n days (null during warm-up)
      lower       lowest Low of the prior exit_n days (null during warm-up)
      atr         simple rolling mean of the true range over atr_n days
      regime_ma   SMA(Close, regime_ma_n) (null when the regime filter is off)
      regime_ok   True when entries are allowed today (Close > regime_ma, or filter off)
      entry       Close > upper  (raw breakout, ignores state and regime)
      exit        Close < lower  (raw breakdown, ignores state)
      position    1 while long, else 0 — long-only, single position, no pyramiding:
                  flat -> long on `entry` if regime_ok; long -> flat on `exit` or on a stop
                  hit (Close < stop). When entry and exit fire on the same day the current
                  state decides (a flat book enters, a long book exits).
      weight      position size in [0, max_weight] while long (1.0 when sizing is off), 0 flat
      stop        the stop level in force on day t (set at the close of t-1); null when flat
      exit_reason "channel" | "stop" on the exit day, else null
    """
    config = config or TrendFollowingConfig()
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"donchian_signal: missing columns {missing}")
    if df.is_empty():
        return _empty_signal_frame(df)

    tdpy = config.trading_days_per_year
    out = (
        df.sort("Date")
        .filter(pl.col("Close").is_not_null())
        .with_columns([
            pl.col("High").cast(pl.Float64),
            pl.col("Low").cast(pl.Float64),
            pl.col("Close").cast(pl.Float64),
        ])
        .with_columns(pl.col("Close").shift(1).alias("_prev_close"))
        .with_columns([
            # shift(1) so day t's channel covers t-n .. t-1 and never includes day t itself
            pl.col("High").shift(1).rolling_max(window_size=config.entry_n).alias("upper"),
            pl.col("Low").shift(1).rolling_min(window_size=config.exit_n).alias("lower"),
            pl.max_horizontal(
                pl.col("High") - pl.col("Low"),
                (pl.col("High") - pl.col("_prev_close")).abs(),
                (pl.col("Low") - pl.col("_prev_close")).abs(),
            ).alias("_tr"),
            (pl.col("Close") / pl.col("_prev_close") - 1.0).alias("_ret"),
        ])
        .with_columns([
            pl.col("_tr").rolling_mean(window_size=config.atr_n).alias("atr"),
            (pl.col("Close").rolling_mean(window_size=config.regime_ma_n) if config.regime_enabled
             else pl.lit(None, dtype=pl.Float64)).alias("regime_ma"),
            (pl.col("_ret").rolling_std(window_size=config.vol_n) * math.sqrt(tdpy) if config.sizing_enabled
             else pl.lit(None, dtype=pl.Float64)).alias("_vol"),
        ])
        .with_columns([
            (pl.col("Close") > pl.col("upper")).fill_null(False).alias("entry"),
            (pl.col("Close") < pl.col("lower")).fill_null(False).alias("exit"),
            ((pl.col("Close") > pl.col("regime_ma")).fill_null(False) if config.regime_enabled
             else pl.lit(True)).alias("regime_ok"),
        ])
    )

    n = out.height
    entry = out.get_column("entry").to_numpy()
    exit_ = out.get_column("exit").to_numpy()
    regime_ok = out.get_column("regime_ok").to_numpy()
    close = out.get_column("Close").to_numpy()
    atr = out.get_column("atr").fill_null(float("nan")).to_numpy()
    vol = out.get_column("_vol").fill_null(float("nan")).to_numpy() if config.sizing_enabled else None

    position = np.zeros(n, dtype=np.int8)
    weight = np.zeros(n, dtype=float)
    stop_col = np.full(n, np.nan)
    reason = [None] * n

    in_pos = False
    highest = math.nan
    w_cur = 0.0
    stop_level = math.nan        # level in force for the current day (set at the previous close)

    for i in range(n):
        if in_pos:
            stop_col[i] = stop_level
            stop_hit = config.stop_enabled and not math.isnan(stop_level) and close[i] < stop_level
            if exit_[i] or stop_hit:
                in_pos = False
                reason[i] = "channel" if exit_[i] else "stop"
                w_cur = 0.0
                stop_level = math.nan
            else:
                if config.stop_mode == "trailing":
                    highest = max(highest, close[i])
                    stop_level = _stop_from(highest, atr[i], config)
        elif entry[i] and regime_ok[i]:
            in_pos = True
            highest = close[i]
            w_cur = _weight_at_entry(vol[i] if vol is not None else math.nan, config)
            stop_level = _stop_from(close[i], atr[i], config)
        position[i] = 1 if in_pos else 0
        weight[i] = w_cur if in_pos else 0.0

    return (
        out.drop(["_prev_close", "_tr", "_ret", "_vol"])
        .with_columns([
            pl.Series("position", position, dtype=pl.Int8),
            pl.Series("weight", weight, dtype=pl.Float64),
            pl.Series("stop", stop_col, dtype=pl.Float64).fill_nan(None),
            pl.Series("exit_reason", reason, dtype=pl.Utf8),
        ])
    )


def _stop_from(anchor: float, atr_value: float, config: TrendFollowingConfig) -> float:
    if not config.stop_enabled or math.isnan(atr_value):
        return math.nan
    return anchor - config.stop_atr_mult * atr_value


def _weight_at_entry(realized_vol: float, config: TrendFollowingConfig) -> float:
    """Volatility-target weight fixed at entry; 1.0 (capped by max_weight) when sizing is off
    or the vol estimate is not available yet."""
    if not config.sizing_enabled:
        return min(1.0, config.max_weight)
    if math.isnan(realized_vol) or realized_vol <= 0:
        return min(1.0, config.max_weight)
    return float(min(config.max_weight, (config.vol_target_pct / 100.0) / realized_vol))


def _empty_signal_frame(df: pl.DataFrame) -> pl.DataFrame:
    return df.with_columns([
        pl.lit(None, dtype=pl.Float64).alias("upper"),
        pl.lit(None, dtype=pl.Float64).alias("lower"),
        pl.lit(None, dtype=pl.Float64).alias("atr"),
        pl.lit(None, dtype=pl.Float64).alias("regime_ma"),
        pl.lit(True).alias("regime_ok"),
        pl.lit(False).alias("entry"),
        pl.lit(False).alias("exit"),
        pl.lit(0, dtype=pl.Int8).alias("position"),
        pl.lit(0.0, dtype=pl.Float64).alias("weight"),
        pl.lit(None, dtype=pl.Float64).alias("stop"),
        pl.lit(None, dtype=pl.Utf8).alias("exit_reason"),
    ])
