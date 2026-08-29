"""data/market.py — Market data aggregation, stock listings, historical prices, and index indicators."""
import re
import threading
from collections import OrderedDict
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
import pandas as pd
import polars as pl
import FinanceDataReader as fdr
import logging

from data.cache import (
    _START_DATE,
    _FDR_ONLY_TICKERS,
    _HIST_CACHE,
    _HIST_CACHE_MAX,
    _HIST_CACHE_HITS,
    _HIST_CACHE_MISSES,
    _HIST_CACHE_LOG_INTERVAL,
    _HIST_CACHE_LOCK,
    _USD_KRW_CACHE,
    _YF_SESSION,
    _NAVER_SESSION,
    _hist_df_is_stale,
    _log_hist_cache_stats,
    safe_float,
)
from data.indicators import (
    _to_polars,
    _compute_indicators,
    fetch_historical_changes,
)
from data.collectors.naver import (
    _fast_kr_history,
    fetch_naver_realtime_prices,
    fetch_naver_per_batch,
    _fetch_naver_per_single,
    _fetch_naver_info,
    _fetch_kr_listing_naver,
    _get_kr3y_df,
)
from data.collectors.yahoo import (
    fetch_us_market_data,
    _YF_BULK_CACHE,
)
from data.collectors.krx import (
    _get_vkospi_pdf,
    _get_jp10y_df,
)

logger = logging.getLogger(__name__)

# Mapping: display label -> (FDR ticker, color)
INDEX_TICKERS = {
    "KOSPI":     ("^KS11",  "#4a90d9"),
    "KOSDAQ":    ("^KQ11",  "#27ae60"),
    "S&P500":    ("^GSPC",  "#9b59b6"),
    "NASDAQ":    ("^IXIC",  "#e87040"),
    "NASDAQ 100":("^NDX",   "#d35400"),
    "Dow Jones": ("^DJI",   "#8e44ad"),
    "US10YT":    ("^TNX",   "#1abc9c"),
    "JP10YT":    ("JP10YT", "#e74c3c"),
    "KR3YT":     ("KR3YT",  "#2ecc71"),
    "VIX":       ("^VIX",   "#f39c12"),
    "WTI":       ("CL=F",   "#8e44ad"),
    "VKOSPI":    ("VKOSPI", "#c0392b"),
}

_INDEX_DISPLAY_NAMES = {
    "KOSPI":     "KOSPI",
    "KOSDAQ":    "KOSDAQ",
    "S&P500":    "S&P500",
    "NASDAQ":    "Nasdaq",
    "NASDAQ 100":"Nasdaq 100",
    "Dow Jones": "Dow Jones",
    "US10YT":    "US 10Y Treasury",
    "JP10YT":    "JP 10Y Bond",
    "KR3YT":     "KR 3Y Bond",
    "VIX":       "VIX",
    "WTI":       "WTI Crude Oil",
    "VKOSPI":    "VKOSPI",
}
_INDEX_ORDER = {
    "S&P500": 0, "Nasdaq": 1, "Nasdaq 100": 2, "Dow Jones": 3,
    "KOSPI": 4, "KOSDAQ": 5,
    "US 10Y Treasury": 6, "JP 10Y Bond": 7, "KR 3Y Bond": 8,
    "VIX": 9, "VKOSPI": 10, "WTI Crude Oil": 11,
}


def _singleflight_cache(maxsize):
    """Like functools.lru_cache(maxsize), but de-duplicates concurrent calls:
    if a second thread requests a key that's still being computed by another
    thread, it waits for and reuses that in-flight result instead of firing
    its own redundant (here, network-bound) computation. Plain lru_cache only
    guarantees the cache structure itself isn't corrupted by concurrent
    access -- it does not prevent two threads from both missing on the same
    key and both calling the wrapped function.

    Only meant for single-argument functions keyed on that argument, which
    is all get_stock_listing/_get_listing_with_norm need."""
    def decorator(fn):
        cache: "OrderedDict[str, pd.DataFrame]" = OrderedDict()
        cache_lock = threading.Lock()
        key_locks: dict = {}

        def wrapper(key):
            with cache_lock:
                if key in cache:
                    cache.move_to_end(key)
                    return cache[key]
                key_lock = key_locks.setdefault(key, threading.Lock())

            with key_lock:
                with cache_lock:
                    if key in cache:
                        cache.move_to_end(key)
                        return cache[key]
                result = fn(key)
                with cache_lock:
                    cache[key] = result
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
        except Exception as e:
            logger.warning("KrxStockListing failed for market=%s, falling back to fdr.StockListing", market, exc_info=True)

    try:
        return fdr.StockListing(market)
    except Exception as e:
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
        df = fdr.StockListing(market)
        if df is None or df.empty or 'Code' not in df.columns or 'Name' not in df.columns:
            return []
        marcap_col = 'Marcap' if 'Marcap' in df.columns else None
        rows = [
            {
                'Code': str(row['Code']).zfill(6),
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


def _bump_hist_cache_counter(_df_mod, _dc, counter_name: str) -> None:
    """Increments a _HIST_CACHE_HITS/_HIST_CACHE_MISSES counter and mirrors it
    into the data_fetcher facade module.

    Plain module-level ints don't share state across re-exports (rebinding
    one copy doesn't touch another), and tests/test_data_fetcher.py patches
    and reads these counters via `data_fetcher.<name>` for backward
    compatibility with pre-modularization callers -- so both copies have to
    be kept in sync by hand here rather than just writing to `_dc`.
    """
    value = getattr(_df_mod, counter_name, getattr(_dc, counter_name)) + 1
    setattr(_dc, counter_name, value)
    if hasattr(_df_mod, counter_name):
        setattr(_df_mod, counter_name, value)
    log_stats_fn = getattr(_df_mod, "_log_hist_cache_stats", _log_hist_cache_stats)
    log_stats_fn()


def get_historical_data(ticker: str, start: str) -> pl.DataFrame:
    """Historical data with a smart cache that skips empty DataFrames.

    _HIST_CACHE is a plain OrderedDict shared across every fetch thread
    (up to 20 concurrent workers during a full-universe refresh), so all
    reads/writes to it are serialized under _HIST_CACHE_LOCK. The network
    fetch itself happens outside the lock so concurrent misses still run
    in parallel — only the dict bookkeeping is made atomic.
    """
    import data.cache as _dc
    import data_fetcher as _df_mod

    cache_key = (ticker, start)
    stale_check = getattr(_df_mod, "_hist_df_is_stale", _hist_df_is_stale)

    with _dc._HIST_CACHE_LOCK:
        cached = _dc._HIST_CACHE.get(cache_key)
        if cached is not None:
            _dc._HIST_CACHE.move_to_end(cache_key)

    if cached is not None and not stale_check(cached):
        _bump_hist_cache_counter(_df_mod, _dc, "_HIST_CACHE_HITS")
        return cached

    _bump_hist_cache_counter(_df_mod, _dc, "_HIST_CACHE_MISSES")

    fetch_fn = getattr(_df_mod, "_fetch_historical_uncached", _fetch_historical_uncached)
    df = fetch_fn(ticker, start)
    if not df.is_empty():
        max_size = getattr(_df_mod, "_HIST_CACHE_MAX", _dc._HIST_CACHE_MAX)
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
        if len(ticker) == 6 and "." not in ticker and any(c.isdigit() for c in ticker):
            df = _fast_kr_history(ticker, start)
            if not df.is_empty():
                return df

        if ticker in _FDR_ONLY_TICKERS:
            if ticker == "KR3YT":
                rows = []
                start_d = datetime.strptime(start, "%Y-%m-%d").date() if start else None
                for page in range(1, 40):
                    url = f"https://finance.naver.com/marketindex/interestDailyQuote.naver?marketindexCd=IRR_GOVT03Y&page={page}"
                    try:
                        res = _NAVER_SESSION.get(url, timeout=5)
                        soup = BeautifulSoup(res.text, 'html.parser')
                        tr_list = soup.select('table.tbl_exchange.today tbody tr')
                        if not tr_list:
                            break

                        done = False
                        for tr in tr_list:
                            tds = tr.select('td')
                            if len(tds) < 4:
                                continue
                            date_str = tds[0].text.strip()
                            if not date_str:
                                continue
                            val = float(tds[1].text.strip())
                            d_obj = datetime.strptime(date_str, "%Y.%m.%d").date()
                            if start_d and d_obj < start_d:
                                done = True
                                break
                            rows.append({"Date": d_obj, "Close": val, "Open": val, "High": val, "Low": val, "Volume": 0})
                        if done:
                            break
                    except Exception as e:
                        logger.warning("Error scraping KR3YT page %d", page, exc_info=True)
                        break
                if rows:
                    return pl.DataFrame(rows).sort("Date")

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


def _build_kr_stock_res(code, name, market, marcap):
    if marcap <= 0 or not name or name.startswith('KR_'):
        n_nv, mc_nv = _fetch_naver_info(code)
        if n_nv:
            name = n_nv
        if mc_nv > 0:
            marcap = mc_nv

    naver_prices = fetch_naver_realtime_prices([code])
    current_price = naver_prices.get(code, 0.0)

    if current_price == 0:
        try:
            start = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")
            df_p = get_historical_data(code, start)
            if not df_p.is_empty():
                current_price = float(df_p.get_column('Close')[-1])
        except Exception:
            logger.debug("Historical price fallback failed for code=%s", code, exc_info=True)

    df_history = None
    changes = fetch_historical_changes(code, current_price, df_history)
    _, tper, fper = _fetch_naver_per_single(code)
    return {
        "ticker": code,
        "name": name,
        "market": market,
        "price": current_price,
        "market_cap": int(marcap),
        "currency": "₩",
        "changes": changes,
        "trailing_per": tper,
        "forward_per": fper,
    }


def fetch_kr_market_data(market="KOSPI", top_n=200, progress_callback=None):
    try:
        try:
            results_list = _fetch_kr_listing_naver(market, top_n)
        except Exception:
            logger.warning("Naver market-listing scrape failed for market=%s", market, exc_info=True)
            results_list = []

        if not results_list:
            logger.info("fetch_kr_market_data: Naver listing empty for market=%s, trying FDR fallback", market)
            results_list = _fetch_kr_listing_fdr_fallback(market, top_n)

        if not results_list:
            logger.error("fetch_kr_market_data: all listing sources failed for market=%s", market)
            return []

        total = len(results_list)
        all_codes = [r['Code'] for r in results_list]
        _bg_exe = ThreadPoolExecutor(max_workers=2)
        try:
            _f_prices = _bg_exe.submit(fetch_naver_realtime_prices, all_codes)
            _f_per    = _bg_exe.submit(fetch_naver_per_batch, all_codes)
            naver_prices = _f_prices.result()

            done_count = 0
            _lock = threading.Lock()

            def _process_one(item):
                nonlocal done_count
                ticker = item['Code']
                name   = item['Name']
                marcap = item['Marcap']

                current_price = naver_prices.get(ticker, 0.0)
                if current_price == 0:
                    try:
                        start_dt = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")
                        df_p = get_historical_data(ticker, start_dt)
                        if not df_p.is_empty():
                            current_price = float(df_p.get_column('Close')[-1])
                    except Exception:
                        logger.debug("Historical price fallback failed for ticker=%s", ticker, exc_info=True)

                changes = fetch_historical_changes(ticker, current_price)
                with _lock:
                    done_count += 1
                    count = done_count
                if progress_callback:
                    progress_callback(min(count, total), total)
                return ticker, str(name), marcap, current_price, changes

            hist_data: dict = {}
            with ThreadPoolExecutor(max_workers=20) as exe:
                futures = [exe.submit(_process_one, item) for item in results_list]
                for f in as_completed(futures):
                    t, n, mc, cp, ch = f.result()
                    hist_data[t] = (n, mc, cp, ch)

            trailing_per_dict, forward_per_dict = _f_per.result()
        finally:
            _bg_exe.shutdown(wait=False)

        results = []
        for item in results_list:
            ticker = item['Code']
            if ticker not in hist_data:
                continue
            name, marcap, current_price, changes = hist_data[ticker]
            results.append({
                "ticker": ticker,
                "name": name,
                "market": market,
                "price": current_price,
                "market_cap": marcap,
                "currency": "₩",
                "changes": changes,
                "trailing_per": trailing_per_dict.get(ticker),
                "forward_per": forward_per_dict.get(ticker),
            })

        return results
    except Exception as e:
        logger.error("fetch_kr_market_data failed for market=%s", market, exc_info=True)
        return []


def fetch_market_data(market, top_n, progress_callback=None):
    if market in ("KOSPI", "KOSDAQ"):
        return fetch_kr_market_data(market, top_n, progress_callback)
    return fetch_us_market_data(market, top_n, progress_callback)


def fetch_single_stock(market, ticker):
    """Fetches data for a single stock by market and ticker symbol."""
    try:
        if market in ("KOSPI", "KOSDAQ"):
            try:
                df_krx = get_stock_listing('KRX')
                row_krx = df_krx[df_krx['Code'] == ticker]
                if row_krx.empty and 'ISU_CD' in df_krx.columns:
                    row_krx = df_krx[df_krx['ISU_CD'] == ticker]

                search_term = re.sub(r'[\s_]+', '', ticker.upper())
                if row_krx.empty:
                    df_krx_norm = _get_listing_with_norm('KRX')
                    row_exact = df_krx_norm[df_krx_norm['NameNorm'] == search_term]
                    row_krx = row_exact if not row_exact.empty else \
                              df_krx_norm[df_krx_norm['NameNorm'].str.contains(search_term, na=False)]

                if row_krx.empty:
                    df_etf_norm = _get_listing_with_norm('ETF/KR')
                    row_etf = df_etf_norm[df_etf_norm['Symbol'] == ticker]
                    if row_etf.empty:
                        row_etf = df_etf_norm[df_etf_norm['NameNorm'].str.contains(search_term, na=False)]
                    if not row_etf.empty:
                        target = row_etf.iloc[0]
                        code = str(target.get('Symbol', ticker))
                        if code.isdigit():
                            code = code.zfill(6)
                        name = str(target.get('Name', ticker))
                        return _build_kr_stock_res(code, name, market, 0), None
                else:
                    target = row_krx.iloc[0]
                    code = str(target.get('Code', ticker)).zfill(6)
                    name = str(target.get('Name', ticker))
                    marcap = safe_float(target.get('Marcap', 0))
                    return _build_kr_stock_res(code, name, market, marcap), None
            except Exception as exc:
                logger.warning("KRX search error for ticker='%s'", ticker, exc_info=True)

            if len(ticker) == 6 and "." not in ticker and any(c.isdigit() for c in ticker):
                return _build_kr_stock_res(ticker, "", market, 0), None

            df_listing = get_stock_listing(market)
            row = df_listing[df_listing['Code'] == ticker]
            if row.empty:
                row = df_listing[df_listing['Name'].str.upper().str.contains(ticker.upper(), na=False)]
            if not row.empty:
                target = row.iloc[0]
                code = str(target.get('Code', ticker)).zfill(6)
                name = str(target.get('Name', ticker))
                marcap = safe_float(target.get('Marcap', target.get('MarCap', 0)))
                return _build_kr_stock_res(code, name, market, marcap), None

            return None, f"Ticker/Name '{ticker}' not found in {market}"

        else:
            fx_rate = get_usd_krw_rate()
            yf_symbol = ticker.replace(".", "-")

            df_p = pl.DataFrame()
            usd_price = 0.0
            try:
                df_pd = fdr.DataReader(ticker, _START_DATE)
                df_p = _to_polars(df_pd)
                if not df_p.is_empty():
                    close_s = df_p.get_column("Close").drop_nulls()
                    usd_price = float(close_s[-1]) if len(close_s) > 0 else 0.0
            except Exception:
                logger.debug("fdr.DataReader failed for ticker=%s", ticker, exc_info=True)

            if usd_price == 0:
                return None, f"Could not fetch price for '{ticker}'"

            name = ticker
            usd_marcap = 0
            inf: dict = {}

            try:
                import yfinance as yf
                yft = yf.Ticker(yf_symbol, session=_YF_SESSION)
                inf = yft.info
                usd_marcap = inf.get('marketCap') or inf.get('totalAssets') or 0
                name = inf.get('longName') or inf.get('shortName') or ticker
            except Exception:
                logger.debug("yfinance info failed for %s", yf_symbol, exc_info=True)

            if usd_marcap == 0:
                try:
                    from yahooquery import Ticker as YQTicker
                    yq = YQTicker(yf_symbol)
                    detail = yq.summary_detail.get(yf_symbol, {})
                    qt = yq.quote_type.get(yf_symbol, {})
                    if isinstance(detail, dict):
                        usd_marcap = detail.get('marketCap') or detail.get('totalAssets', 0) or 0
                    if isinstance(qt, dict):
                        name = qt.get('longName') or qt.get('shortName') or ticker
                except Exception:
                    logger.debug("yahooquery summary_detail failed for %s", yf_symbol, exc_info=True)

            changes = fetch_historical_changes(yf_symbol, usd_price, df_p)
            trailing_per = None
            forward_per = None
            try:
                tpe = inf.get('trailingPE') if isinstance(inf, dict) else None
                fpe = inf.get('forwardPE') if isinstance(inf, dict) else None
                if tpe is not None and isinstance(tpe, (int, float)) and tpe == tpe:
                    trailing_per = round(float(tpe), 1)
                if fpe is not None and isinstance(fpe, (int, float)) and fpe == fpe:
                    forward_per = round(float(fpe), 1)
            except Exception:
                logger.debug("PER extraction failed for %s", ticker, exc_info=True)

            return {
                "ticker": ticker,
                "name": name,
                "market": market,
                "price": usd_price * fx_rate,
                "market_cap": usd_marcap * fx_rate,
                "usd_price": usd_price,
                "currency": "$",
                "changes": changes,
                "trailing_per": trailing_per,
                "forward_per": forward_per,
            }, None

    except Exception as e:
        return None, str(e)


def get_usd_krw_rate():
    """Returns USD/KRW FX rate."""
    import data_fetcher as _df_mod
    fdr_mod = getattr(_df_mod, "fdr", fdr)
    usd_cache = getattr(_df_mod, "_USD_KRW_CACHE", _USD_KRW_CACHE)
    stale_check = getattr(_df_mod, "_hist_df_is_stale", _hist_df_is_stale)
    if usd_cache["rate"] is not None and not stale_check(usd_cache["df"]):
        return usd_cache["rate"]
    try:
        df = _to_polars(fdr_mod.DataReader('USD/KRW'))
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


def get_index_close_for_date(ticker: str, date_str: str) -> float:
    """Returns the closing price for an index/ticker for a specific date (YYYY-MM-DD)."""
    df = get_historical_data(ticker, _START_DATE)
    if df is not None and not df.is_empty():
        try:
            target = datetime.strptime(date_str, "%Y-%m-%d").date()
            sub = df.filter(pl.col("Date") <= target)
            if not sub.is_empty():
                return float(sub.get_column("Close")[-1])
        except Exception:
            logger.debug("get_index_close_for_date failed for ticker=%s date=%s", ticker, date_str, exc_info=True)
    return 0.0


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


def fetch_index_mas(fdr_ticker, days=365):
    """Fetches closing prices for any FDR index ticker and computes 20 & 50-day MA."""
    try:
        start = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        df_pd = fdr.DataReader(fdr_ticker, start)
        df = _to_polars(df_pd)
        if df.is_empty():
            return None, f"No data for ticker '{fdr_ticker}'."
        df = df.select([
            pl.col("Date"),
            pl.col("Close").cast(pl.Float64),
            pl.col("Close").cast(pl.Float64).rolling_mean(window_size=10, min_samples=1).alias("MA10"),
            pl.col("Close").cast(pl.Float64).rolling_mean(window_size=20, min_samples=1).alias("MA20"),
            pl.col("Close").cast(pl.Float64).rolling_mean(window_size=50, min_samples=1).alias("MA50"),
        ])
        return df, None
    except Exception as e:
        return None, str(e)


def fetch_all_indices_mas(days=365):
    """Fetch 20 & 50-day MA data for major indices concurrently."""
    def _fetch(label_ticker):
        label, (fdr_ticker, _color) = label_ticker
        df, err = fetch_index_mas(fdr_ticker, days)
        return label, df, err

    results = {}
    items = [item for item in INDEX_TICKERS.items()
             if item[0] not in ("Dow Jones", "US10YT", "JP10YT", "KR3YT", "VIX", "WTI", "VKOSPI")]
    with ThreadPoolExecutor(max_workers=len(items)) as executor:
        futures = {executor.submit(_fetch, item): item for item in items}
        for future in as_completed(futures):
            label, df, err = future.result()
            results[label] = (df, err)
    return results


def fetch_stock_ma_multi(ticker, market, windows=(10, 20, 60), days=1825, target_year=None):
    """Fetches OHLCV for a single stock and computes MA/RSI using Polars."""
    try:
        if target_year is not None:
            start = f"{target_year - 1}-09-01"
            end   = f"{target_year}-12-31"
            if ticker == "JP10YT":
                df_pd = _get_jp10y_df()
                if df_pd is not None:
                    df_pd = df_pd[(df_pd.index >= start) & (df_pd.index <= end)]
            elif ticker == "KR3YT":
                df_pd = _get_kr3y_df()
                if df_pd is not None:
                    df_pd = df_pd[(df_pd.index >= start) & (df_pd.index <= end)]
            elif ticker == "VKOSPI":
                df_pd = _get_vkospi_pdf()
                if df_pd is not None:
                    df_pd = df_pd[(df_pd.index >= start) & (df_pd.index <= end)]
            else:
                df_pd = fdr.DataReader(ticker, start, end)
        else:
            max_window = max(windows)
            start = (datetime.now() - timedelta(days=max(days, max_window * 3))).strftime("%Y-%m-%d")
            if ticker == "JP10YT":
                df_pd = _get_jp10y_df()
                if df_pd is not None:
                    df_pd = df_pd[df_pd.index >= start]
            elif ticker == "KR3YT":
                df_pd = _get_kr3y_df()
                if df_pd is not None:
                    df_pd = df_pd[df_pd.index >= start]
            elif ticker == "VKOSPI":
                df_pd = _get_vkospi_pdf()
                if df_pd is not None:
                    df_pd = df_pd[df_pd.index >= start]
            else:
                df_pd = fdr.DataReader(ticker, start)

        df = _to_polars(df_pd)
        if df.is_empty():
            return None, f"No data for '{ticker}'."
        return _compute_indicators(df, windows), None
    except Exception as e:
        return None, str(e)


def fetch_indice_as_stock(label_ticker):
    label, (fdr_ticker, _color) = label_ticker
    is_bond = label in ("US10YT", "JP10YT", "KR3YT")

    try:
        if label == "JP10YT":
            df = _to_polars(_get_jp10y_df())
        elif label == "KR3YT":
            df = _to_polars(_get_kr3y_df())
        elif label == "VKOSPI":
            df = _to_polars(_get_vkospi_pdf())
        else:
            df = get_historical_data(fdr_ticker, _START_DATE)

        if df.is_empty():
            return None
        close_series = df.get_column("Close").drop_nulls()
        val = close_series[-1] if len(close_series) > 0 else None
        current_price = float(val) if val is not None else 0.0

        if is_bond:
            chg_mode = 'bp'
        elif label in ('VIX', 'WTI', 'VKOSPI'):
            chg_mode = 'abs'
        else:
            chg_mode = 'pct'

        changes = fetch_historical_changes(fdr_ticker, current_price, df, mode=chg_mode)
        name = _INDEX_DISPLAY_NAMES.get(label, label)
        order = _INDEX_ORDER.get(name, 99)

        return {
            "ticker": fdr_ticker,
            "name": name,
            "market": "Index",
            "price": current_price,
            "market_cap": float('inf'),
            "usd_price": current_price,
            "currency": "%" if is_bond else ("$" if label == "WTI" else ""),
            "changes": changes,
            "is_index": True,
            "is_bond": is_bond,
            "change_mode": chg_mode,
            "index_order": order,
        }
    except Exception as e:
        logger.error("Index fetch failed for label=%s", label, exc_info=True)
        return None


def fetch_major_indices_as_stocks():
    n = len(INDEX_TICKERS)
    results = []
    with ThreadPoolExecutor(max_workers=n) as executor:
        futures = {executor.submit(fetch_indice_as_stock, item): item for item in INDEX_TICKERS.items()}
        for future in as_completed(futures):
            res = future.result()
            if res:
                results.append(res)
    results.sort(key=lambda x: x.get('index_order', 99))
    return results
