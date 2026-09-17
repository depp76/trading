"""data/fx.py — USD/KRW rate (FinanceDataReader) with a session cache."""
from datetime import datetime
import logging
import polars as pl
import FinanceDataReader as fdr

from data.cache import _USD_KRW_CACHE, _MISC_CACHE_LOCK, _hist_df_is_stale
from data.frames import _to_polars

logger = logging.getLogger(__name__)


def get_usd_krw_rate():
    """Returns USD/KRW FX rate."""
    usd_cache = _USD_KRW_CACHE
    with _MISC_CACHE_LOCK:
        if usd_cache["rate"] is not None and not _hist_df_is_stale(usd_cache["df"]):
            return usd_cache["rate"]
        try:
            df = _to_polars(fdr.DataReader('USD/KRW'))
            if not df.is_empty():
                usd_cache["df"] = df
                close_s = df.get_column("Close").drop_nulls()
                rate = float(close_s[-1]) if len(close_s) > 0 else 1450.0
            else:
                rate = usd_cache["rate"] if usd_cache["rate"] is not None else 1450.0
        except Exception:
            rate = usd_cache["rate"] if usd_cache["rate"] is not None else 1450.0
            logger.warning("USD/KRW rate fetch failed, using cached/fallback rate=%.1f", rate, exc_info=True)
        usd_cache["rate"] = rate
        return rate


def get_usd_krw_rate_for_date(date_str: str) -> float:
    """Returns USD/KRW rate for a specific date (YYYY-MM-DD)."""
    get_usd_krw_rate()
    df = _USD_KRW_CACHE.get("df")
    if df is not None and not df.is_empty():
        try:
            target = datetime.strptime(date_str, "%Y-%m-%d").date()
            sub = df.filter(pl.col("Date") <= target)
            if not sub.is_empty():
                return float(sub.get_column("Close")[-1])
        except Exception:
            logger.debug("get_usd_krw_rate_for_date failed for date=%s", date_str, exc_info=True)
    return get_usd_krw_rate()
