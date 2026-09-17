"""data/listing.py — Stock listing lookups (FinanceDataReader) with a day-scoped,
single-flight cache. Sits below market.py so collectors can use listings without
importing the aggregation layer."""
import threading
from collections import OrderedDict
from datetime import datetime
import logging
import pandas as pd
import FinanceDataReader as fdr

from data.cache import safe_float

logger = logging.getLogger(__name__)


def _singleflight_cache(maxsize):
    """Like functools.lru_cache(maxsize), but de-duplicates concurrent calls:
    if a second thread requests a key that's still being computed by another
    thread, it waits for and reuses that in-flight result instead of firing
    its own redundant (here, network-bound) computation. Plain lru_cache only
    guarantees the cache structure itself isn't corrupted by concurrent
    access -- it does not prevent two threads from both missing on the same
    key and both calling the wrapped function.

    Entries expire at the day boundary: listings (constituents, market caps)
    change daily, and before this the cache lived for the whole process
    lifetime (roadmap 6-2f).

    Only meant for single-argument functions keyed on that argument, which
    is all get_stock_listing/_get_listing_with_norm need."""
    def decorator(fn):
        cache: "OrderedDict[str, tuple]" = OrderedDict()  # key -> (date, result)
        cache_lock = threading.Lock()
        key_locks: dict = {}

        def _lookup(key, today):
            hit = cache.get(key)
            if hit is not None and hit[0] == today:
                cache.move_to_end(key)
                return hit[1]
            return None

        def wrapper(key):
            today = datetime.now().date()
            with cache_lock:
                cached = _lookup(key, today)
                if cached is not None:
                    return cached
                key_lock = key_locks.setdefault(key, threading.Lock())

            with key_lock:
                with cache_lock:
                    cached = _lookup(key, today)
                    if cached is not None:
                        return cached
                result = fn(key)
                with cache_lock:
                    cache[key] = (today, result)
                    cache.move_to_end(key)
                    if len(cache) > maxsize:
                        cache.popitem(last=False)
                    key_locks.pop(key, None)
                return result

        def cache_clear():
            with cache_lock:
                cache.clear()
                key_locks.clear()

        wrapper.cache_clear = cache_clear
        return wrapper
    return decorator


@_singleflight_cache(maxsize=16)
def get_stock_listing(market: str) -> pd.DataFrame:
    """Cached version of fdr.StockListing to prevent redundant network requests."""
    if market in ('KRX-DESC', 'KRX', 'KOSPI', 'KOSDAQ'):
        try:
            from FinanceDataReader.krx.listing import KrxStockListing
            market_arg = f'{market}-DESC' if market in ('KOSPI', 'KOSDAQ') else 'KRX-DESC'
            return KrxStockListing(market_arg).read()
        except Exception:
            logger.warning("KrxStockListing failed for market=%s, falling back to fdr.StockListing", market, exc_info=True)

    try:
        return fdr.StockListing(market)
    except Exception:
        logger.error("fdr.StockListing('%s') failed", market, exc_info=True)
        return pd.DataFrame(columns=['Symbol', 'Code', 'Name', 'Market'])


@_singleflight_cache(maxsize=4)
def _get_listing_with_norm(market: str) -> pd.DataFrame:
    """Cached listing with pre-computed NameNorm column (upper, stripped)."""
    df = get_stock_listing(market).copy()
    df['NameNorm'] = df['Name'].str.upper().str.replace(r'[\s_]+', '', regex=True)
    return df


def _fetch_kr_listing_fdr_fallback(market, top_n):
    """Fallback KR market-listing source."""
    try:
        df = get_stock_listing(market)
        code_col = 'Code' if 'Code' in df.columns else ('Symbol' if 'Symbol' in df.columns else None)
        marcap_col = next((c for c in ('Marcap', 'MarCap', 'MarketCap') if c in df.columns), None)
        if df is None or df.empty or not code_col or 'Name' not in df.columns or not marcap_col:
            # Try KRX-DESC which contains Marcap for all KRX listings
            df_desc = get_stock_listing('KRX-DESC')
            if df_desc is not None and not df_desc.empty and 'Market' in df_desc.columns:
                df = df_desc[df_desc['Market'].str.upper() == market.upper()]
                code_col = 'Code' if 'Code' in df.columns else ('Symbol' if 'Symbol' in df.columns else None)
                marcap_col = next((c for c in ('Marcap', 'MarCap', 'MarketCap') if c in df.columns), None)

        if df is None or df.empty or not code_col or 'Name' not in df.columns:
            return []

        rows = [
            {
                'Code': str(row[code_col]).zfill(6),
                'Name': str(row['Name']),
                'Marcap': safe_float(row[marcap_col]) if marcap_col else 0.0,
            }
            for _, row in df.iterrows()
        ]
        rows.sort(key=lambda x: -x['Marcap'])
        return rows[:top_n]
    except Exception:
        logger.warning("FDR listing fallback failed for market=%s", market, exc_info=True)
        return []
