"""strategy/trend_following/universe.py — Candidate population and yearly member
selection for the KR Donchian portfolio strategy (trend_following.md 2-1).

  kr_universe_candidates(all_data)   the KR stock codes in UniverseTab.all_data
                                     (indices, bonds and commodities dropped)
  yearly_members(histories, year, cfg)
                                     the top-M codes by average trading value over the
                                     last `trading_value_n` trading days before Jan 1 of
                                     `year`, after the liquidity and listing-age floors

Selection uses only rows dated before the selection date, so appending later data
never changes the result (tests/strategy/trend_following/test_universe.py).
"""
from datetime import date as _date

import polars as pl

from data.cache import is_kr_code
from strategy.trend_following.config_v1 import KrTrendConfig


def kr_universe_candidates(all_data) -> list:
    """KR stock codes from UniverseTab.all_data rows ({ticker, is_index, is_bond, ...}),
    in table order, de-duplicated. `is_kr_code()` already rejects the index aliases
    ("^KS11"), bond tickers ("KR3YT") and futures ("CL=F"), so only the explicit
    is_index / is_bond flags are checked on top."""
    seen, out = set(), []
    for item in all_data or []:
        ticker = str(item.get("ticker") or "").strip()
        if not ticker or item.get("is_index") or item.get("is_bond"):
            continue
        if not is_kr_code(ticker) or ticker in seen:
            continue
        seen.add(ticker)
        out.append(ticker)
    return out


def average_trading_value(df: pl.DataFrame, as_of, n: int) -> tuple:
    """(mean of Close * Volume over the last n rows dated strictly before `as_of`,
    number of rows before as_of). Returns (None, count) when fewer than n rows exist
    or the frame lacks Volume."""
    if df is None or df.is_empty() or "Volume" not in df.columns or "Close" not in df.columns:
        return None, 0
    as_of = _to_date(as_of)
    hist = df.with_columns(pl.col("Date").cast(pl.Date)).filter(pl.col("Date") < as_of).sort("Date")
    count = hist.height
    if count < n:
        return None, count
    tail = hist.tail(n)
    value = (tail.get_column("Close").cast(pl.Float64) * tail.get_column("Volume").cast(pl.Float64)).mean()
    return (float(value) if value is not None else None), count


def yearly_members(histories: dict, year: int, cfg: KrTrendConfig = None, as_of=None) -> list:
    """2-1: candidates allowed to be *bought* during `year`, ranked by average trading value
    (descending). `as_of` defaults to Jan 1 of `year`; only data dated before it is used.

    Excluded: fewer than cfg.min_history_days rows before as_of (listing age), average
    trading value below cfg.min_trading_value, or no Volume column. With
    cfg.universe_mode == "fixed" every ticker is a member every year (the "current
    market-cap list, fixed" survivorship-bias comparison of trend_following.md 4).
    """
    cfg = cfg or KrTrendConfig()
    as_of = _to_date(as_of) if as_of is not None else _date(year, 1, 1)
    if cfg.universe_mode == "fixed":
        return [t for t, df in histories.items() if df is not None and not df.is_empty()]

    scored = []
    for ticker, df in histories.items():
        value, count = average_trading_value(df, as_of, cfg.trading_value_n)
        if value is None or count < cfg.min_history_days:
            continue
        if value < cfg.min_trading_value:
            continue
        scored.append((value, ticker))
    scored.sort(key=lambda p: (-p[0], p[1]))
    return [t for _, t in scored[:cfg.universe_top_m]]


def _to_date(d):
    if isinstance(d, _date):
        return d
    return _date.fromisoformat(str(d)[:10])
