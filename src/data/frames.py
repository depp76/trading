"""data/frames.py — pandas <-> polars conversion helpers (no other data.* dependencies)."""
import polars as pl


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
        elif date_dtype == pl.String:
            # e.g. a frame whose date index was already stringified upstream
            df = df.with_columns(pl.col("Date").str.slice(0, 10).str.to_date("%Y-%m-%d", strict=False))
        elif not isinstance(date_dtype, pl.Date):
            df = df.with_columns(pl.col("Date").cast(pl.Datetime).dt.date())
        df = df.unique(subset=["Date"], keep="last").sort("Date")
    return df
