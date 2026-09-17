"""data/history.py — Daily OHLCV history with the shared LRU cache (_HIST_CACHE).

Lowest fetch layer above the collectors: routes KR codes/indices to Naver, bond
yields to their cached pandas series, and everything else to yfinance ->
yahooquery -> FinanceDataReader. Does not import data.market or
data.collectors.yahoo, so indicators/yahoo can import it without a cycle."""
import logging
import pandas as pd
import polars as pl
import FinanceDataReader as fdr

import data.cache as _dc
from data.cache import (
    _FDR_ONLY_TICKERS,
    _YF_BULK_CACHE,
    _hist_df_is_stale,
    _record_hist_cache_lookup,
    is_kr_code,
)
from data.frames import _to_polars
from data.collectors.naver import _fast_kr_history, _get_kr3y_df

logger = logging.getLogger(__name__)


def get_historical_data(ticker: str, start: str) -> pl.DataFrame:
    """Historical data with a smart cache that skips empty DataFrames.

    _HIST_CACHE is a plain OrderedDict shared across every fetch thread
    (up to 20 concurrent workers during a full-universe refresh), so all
    reads/writes to it are serialized under _HIST_CACHE_LOCK. The network
    fetch itself happens outside the lock so concurrent misses still run
    in parallel — only the dict bookkeeping is made atomic.

    The cache dict/lock/max are read through the `data.cache` module object
    (`_dc`) rather than imported names so tests can rebind e.g.
    `data.cache._HIST_CACHE_MAX` and have it take effect here. Tests patch
    `data.history._fetch_historical_uncached` / `_hist_df_is_stale`.
    """
    cache_key = (ticker, start)

    with _dc._HIST_CACHE_LOCK:
        cached = _dc._HIST_CACHE.get(cache_key)
        if cached is not None:
            _dc._HIST_CACHE.move_to_end(cache_key)

    if cached is not None and not _hist_df_is_stale(cached):
        _record_hist_cache_lookup(hit=True)
        return cached

    _record_hist_cache_lookup(hit=False)

    df = _fetch_historical_uncached(ticker, start)
    if not df.is_empty():
        max_size = _dc._HIST_CACHE_MAX
        with _dc._HIST_CACHE_LOCK:
            if cache_key not in _dc._HIST_CACHE and len(_dc._HIST_CACHE) >= max_size:
                try:
                    _dc._HIST_CACHE.popitem(last=False)
                except Exception:
                    logger.debug("LRU cache eviction failed", exc_info=True)
            _dc._HIST_CACHE[cache_key] = df
            _dc._HIST_CACHE.move_to_end(cache_key)
        return df
    return cached if cached is not None else df


def _fetch_historical_uncached(ticker: str, start: str) -> pl.DataFrame:
    """Actual fetch — called only on cache miss."""
    try:
        # Fast path for Korean indices via Naver (includes intraday live data)
        if ticker in ("^KS11", "KS11", "KOSPI"):
            df = _fast_kr_history("KOSPI", start)
            if not df.is_empty():
                return df
        elif ticker in ("^KQ11", "KQ11", "KOSDAQ"):
            df = _fast_kr_history("KOSDAQ", start)
            if not df.is_empty():
                return df

        if is_kr_code(ticker):
            df = _fast_kr_history(ticker, start)
            if not df.is_empty():
                return df

        if ticker in _FDR_ONLY_TICKERS:
            if ticker == "KR3YT":
                df_pd = _get_kr3y_df()
                if df_pd is not None and not df_pd.empty:
                    if start:
                        df_pd = df_pd[df_pd.index >= start]
                    return _to_polars(df_pd)
                return pl.DataFrame()

            df_pd = fdr.DataReader(ticker, start)
            return _to_polars(df_pd)

        bulk_key = f"{ticker}_{start}"
        if bulk_key in _YF_BULK_CACHE:
            return _YF_BULK_CACHE[bulk_key]

        import yfinance as yf
        yf_ticker = ticker.replace(".", "-")
        df_pd = None
        try:
            _yf_df = yf.Ticker(yf_ticker).history(start=start, timeout=10, auto_adjust=True)
            if _yf_df is not None and not _yf_df.empty:
                if isinstance(_yf_df.columns, pd.MultiIndex):
                    _yf_df.columns = _yf_df.columns.get_level_values(0)
                df_pd = _yf_df
        except Exception:
            logger.debug("yfinance history fetch failed for %s", ticker, exc_info=True)

        if df_pd is None or (hasattr(df_pd, 'empty') and df_pd.empty):
            try:
                from yahooquery import Ticker as YQTicker
                _yq = YQTicker(ticker, asynchronous=False)
                _yq_df = _yq.history(start=start)
                if isinstance(_yq_df, pd.DataFrame) and not _yq_df.empty:
                    _yq_df = _yq_df.reset_index()
                    if 'date' in _yq_df.columns:
                        _yq_df = _yq_df.rename(columns={'date': 'Date', 'close': 'Close', 'high': 'High', 'low': 'Low', 'open': 'Open', 'volume': 'Volume'})
                    df_pd = _yq_df
            except Exception:
                logger.debug("yahooquery history fetch failed for %s", ticker, exc_info=True)

        if df_pd is None or (hasattr(df_pd, 'empty') and df_pd.empty):
            df_pd = fdr.DataReader(ticker, start)

        return _to_polars(df_pd)
    except Exception:
        logger.warning("All history sources failed for ticker=%s, returning empty DataFrame", ticker, exc_info=True)
        return pl.DataFrame()
