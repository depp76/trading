"""data/collectors/yahoo.py — Yahoo Finance batch quotes, US market data, and futures curves."""
import time
import re
import threading
import requests
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import numpy as np
import pandas as pd
import polars as pl
from yahooquery import Ticker as YQTicker
import logging

from data.cache import (
    _YF_SESSION,
    _NAVER_SESSION,
    _get_yf_crumb,
    _START_DATE,
    safe_float,
)
from data.indicators import _to_polars, fetch_historical_changes

logger = logging.getLogger(__name__)

# Temporary staging cache for yfinance bulk downloads
_YF_BULK_CACHE: dict = {}


def yf_quote_batch(symbols: list, timeout: int = 15, chunk_size: int = 50, max_retries: int = 3) -> dict:
    """Batch-fetch raw Yahoo Finance v7 quote results for a list of symbols.

    Handles chunking, crumb retrieval/refresh-on-401, and retry-with-backoff
    in one place, shared by every caller that needs Yahoo quote data.
    """
    if not symbols:
        return {}

    chunks = [symbols[i:i + chunk_size] for i in range(0, len(symbols), chunk_size)]

    def _fetch_chunk(chunk):
        crumb = _get_yf_crumb()
        params = {"symbols": ",".join(chunk)}
        if crumb:
            params["crumb"] = crumb

        resp = None
        for attempt in range(max_retries):
            try:
                resp = _YF_SESSION.get(
                    "https://query2.finance.yahoo.com/v7/finance/quote",
                    params=params,
                    timeout=timeout,
                )
                # 401 -> crumb expired: refresh and retry immediately
                if resp.status_code == 401:
                    crumb = _get_yf_crumb(force_refresh=True)
                    params["crumb"] = crumb
                    resp = _YF_SESSION.get(
                        "https://query2.finance.yahoo.com/v7/finance/quote",
                        params=params,
                        timeout=timeout,
                    )
                resp.raise_for_status()
                break
            except Exception as e:
                if attempt == max_retries - 1:
                    raise e
                time.sleep(1)

        chunk_results = {}
        if resp:
            for item in resp.json().get("quoteResponse", {}).get("result") or []:
                sym = item.get("symbol", "")
                if sym:
                    chunk_results[sym] = item
        return chunk_results

    results: dict = {}
    if len(chunks) == 1:
        try:
            results.update(_fetch_chunk(chunks[0]))
        except Exception:
            logger.warning("YF v7 quote batch error", exc_info=True)
    else:
        with ThreadPoolExecutor(max_workers=min(len(chunks), 4)) as exe:
            futures = {exe.submit(_fetch_chunk, c): c for c in chunks}
            for fut in as_completed(futures):
                try:
                    results.update(fut.result())
                except Exception:
                    logger.warning("YF v7 quote batch error", exc_info=True)

    return results


def fetch_us_realtime_prices(tickers: list) -> dict:
    """Batch-fetch real-time prices for US stocks using yf_quote_batch."""
    if not tickers:
        return {}

    yf_symbols = [str(t).replace(".", "-") for t in tickers]
    orig_map = {s: t for s, t in zip(yf_symbols, tickers)}

    quotes = yf_quote_batch(yf_symbols, timeout=15)
    results = {}
    for sym, item in quotes.items():
        price = item.get("regularMarketPrice")
        if price is not None:
            results[orig_map.get(sym, sym)] = float(price)

    return results


def fetch_wti_futures_curve():
    """Fetches the latest prices for the next 8 WTI futures contracts."""
    from dateutil.relativedelta import relativedelta
    month_codes = {1: 'F', 2: 'G', 3: 'H', 4: 'J', 5: 'K', 6: 'M', 7: 'N', 8: 'Q', 9: 'U', 10: 'V', 11: 'X', 12: 'Z'}
    now = datetime.now()

    symbols_to_try = []
    # Test up to next 12 months (some might be delisted or inactive)
    for i in range(12):
        dt = now + relativedelta(months=i)
        code = month_codes[dt.month]
        # YF usually uses 2-digit years for NYM futures
        yr = dt.strftime("%y")
        sym = f"CL{code}{yr}.NYM"
        symbols_to_try.append((sym, dt.strftime("%Y-%m")))

    import yfinance as yf
    td = yf.download(
        [s[0] for s in symbols_to_try],
        period="1d",
        group_by="ticker",
        progress=False,
        threads=True
    )

    results = []
    if td is not None and not td.empty:
        for sym, month_label in symbols_to_try:
            try:
                if len(symbols_to_try) == 1:
                    close_price = td['Close'].iloc[-1]
                else:
                    if sym in td.columns.levels[0]:
                        close_price = td[sym]['Close'].iloc[-1]
                    else:
                        continue

                if not np.isnan(close_price):
                    results.append({"Contract": month_label, "Symbol": sym, "Close": float(close_price)})
                    if len(results) == 8:
                        break
            except Exception:
                logger.debug("WTI contract parse error for sym=%s", sym, exc_info=True)
    return results


def fetch_us_stock_data_bulk(symbols_with_names, market_name, fx_rate, progress_callback=None):
    """Fetches US stock data using yahooquery (batched history + meta, per-stock parallel processing)."""
    from data.market import get_historical_data

    total = len(symbols_with_names)
    chunk_size = 20
    chunks = [symbols_with_names[i:i + chunk_size] for i in range(0, total, chunk_size)]
    num_chunks = len(chunks)

    # ── Pre-fetch Naver market caps IN PARALLEL across pages ──────────────
    naver_mcaps = {}
    try:
        nm = "NASDAQ" if "NASDAQ" in market_name.upper() else ("NYSE" if "NYSE" in market_name.upper() else "AMEX")
        num_pages = max(3, (total // 100) + 2)

        def _fetch_naver_mcap_page(p):
            r = _NAVER_SESSION.get(
                f"https://api.stock.naver.com/stock/exchange/{nm}/marketValue?page={p}&pageSize=100",
                timeout=5
            )
            d = r.json()
            page_data = {}
            if 'stocks' in d and d['stocks']:
                for s in d['stocks']:
                    page_data[s.get('reutersCode', '').split('.')[0]] = float(s.get('marketValueRaw') or 0)
            return page_data

        with ThreadPoolExecutor(max_workers=min(num_pages, 8)) as exe:
            for page_result in exe.map(_fetch_naver_mcap_page, range(1, num_pages + 1)):
                naver_mcaps.update(page_result)
    except Exception:
        logger.warning("Naver market cap prefetch failed, market caps may be incomplete", exc_info=True)

    done_count = 0
    _lock = threading.Lock()
    _yf_semaphore = threading.Semaphore(3)
    _YQ_META_TIMEOUT = 35

    def process_chunk(chunk):
        yf_symbols = [s.replace(".", "-") for s, _ in chunk]
        market_cap_dict = {}
        name_dict = {}
        trailing_per_dict = {}
        forward_per_dict = {}

        # ── Step 0: Bulk Pre-fetch History via yfinance ─────────────────
        try:
            import yfinance as yf
            with _yf_semaphore:
                bulk_df = yf.download(
                    yf_symbols,
                    start=_START_DATE,
                    progress=False,
                    timeout=15,
                    auto_adjust=True,
                    threads=False
                )
            if bulk_df is not None and not bulk_df.empty:
                for sym in yf_symbols:
                    try:
                        orig_sym = sym.replace("-", ".")
                        if isinstance(bulk_df.columns, pd.MultiIndex):
                            if sym in bulk_df.columns.get_level_values(1):
                                single_df = bulk_df.xs(sym, level=1, axis=1).dropna(how='all')
                                if not single_df.empty:
                                    pl_df = _to_polars(single_df)
                                    _YF_BULK_CACHE[f"{orig_sym}_{_START_DATE}"] = pl_df
                        else:
                            if len(yf_symbols) == 1 and sym == yf_symbols[0]:
                                single_df = bulk_df.dropna(how='all')
                                if not single_df.empty:
                                    pl_df = _to_polars(single_df)
                                    _YF_BULK_CACHE[f"{orig_sym}_{_START_DATE}"] = pl_df
                    except Exception:
                        logger.debug("YF bulk history slice failed for sym=%s, skipping", sym, exc_info=True)
        except Exception:
            logger.warning("YF bulk history fetch error (chunk)", exc_info=True)

        # ── Primary: Yahoo Finance v7 quote API ──────────────────────────
        try:
            quotes = yf_quote_batch(yf_symbols, timeout=25, chunk_size=len(yf_symbols) or 1)
            for sym, item in quotes.items():
                mc = item.get("marketCap") or item.get("totalAssets") or 0
                market_cap_dict[sym] = float(mc)
                n = item.get("longName") or item.get("shortName")
                if n:
                    name_dict[sym] = n
                tpe = item.get("trailingPE")
                fpe = item.get("forwardPE")
                if tpe is not None and isinstance(tpe, (int, float)) and tpe == tpe:
                    trailing_per_dict[sym] = round(float(tpe), 1)
                if fpe is not None and isinstance(fpe, (int, float)) and fpe == fpe:
                    forward_per_dict[sym] = round(float(fpe), 1)
        except Exception:
            logger.warning("YF v7 quote fetch error (chunk)", exc_info=True)

        # ── Fallback: yahooquery for any symbols still missing data ───────
        missing = [s for s in yf_symbols if s not in market_cap_dict and s not in name_dict]
        if missing:
            try:
                yq = YQTicker(missing, asynchronous=False, timeout=30)

                def _get_summary(): return yq.summary_detail
                def _get_qt():      return yq.quote_type

                with ThreadPoolExecutor(max_workers=2) as yq_exe:
                    f_summary = yq_exe.submit(_get_summary)
                    f_qt      = yq_exe.submit(_get_qt)
                    try:
                        details = f_summary.result(timeout=_YQ_META_TIMEOUT)
                    except Exception:
                        details = {}
                    try:
                        qt = f_qt.result(timeout=_YQ_META_TIMEOUT)
                    except Exception:
                        qt = {}

                if isinstance(details, dict):
                    for sym, data in details.items():
                        if isinstance(data, dict):
                            mc = data.get('marketCap') or data.get('totalAssets', 0)
                            if sym not in market_cap_dict:
                                market_cap_dict[sym] = mc or 0
                            tpe = data.get('trailingPE')
                            fpe = data.get('forwardPE')
                            if tpe is not None and isinstance(tpe, (int, float)) and tpe == tpe:
                                trailing_per_dict.setdefault(sym, round(float(tpe), 1))
                            if fpe is not None and isinstance(fpe, (int, float)) and fpe == fpe:
                                forward_per_dict.setdefault(sym, round(float(fpe), 1))
                if isinstance(qt, dict):
                    for sym, data in qt.items():
                        if isinstance(data, dict):
                            n = data.get('longName') or data.get('shortName')
                            if n and sym not in name_dict:
                                name_dict[sym] = n
            except Exception:
                logger.warning("yahooquery fallback error (chunk)", exc_info=True)

        def process_symbol(symbol, name):
            nonlocal done_count
            yf_symbol = symbol.replace(".", "-")
            try:
                df_history = get_historical_data(symbol, _START_DATE)
                usd_price = 0.0
                if not df_history.is_empty() and "Close" in df_history.columns:
                    close_valid = df_history.get_column('Close').drop_nulls()
                    if len(close_valid) > 0:
                        usd_price = safe_float(close_valid[-1])

                if usd_price == 0:
                    return None

                usd_marcap = market_cap_dict.get(yf_symbol, 0)
                if usd_marcap == 0:
                    usd_marcap = naver_mcaps.get(symbol.split('.')[0], 0)
                if usd_marcap == 0:
                    try:
                        import yfinance as yf
                        fi = yf.Ticker(yf_symbol, session=_YF_SESSION).fast_info
                        usd_marcap = float(getattr(fi, 'market_cap', 0) or 0)
                    except Exception:
                        logger.debug("yfinance fast_info failed for %s", yf_symbol, exc_info=True)

                display_name = name_dict.get(yf_symbol) or name or symbol
                changes = fetch_historical_changes(symbol, usd_price, df_history)
                return {
                    "ticker": symbol,
                    "name": display_name,
                    "market": market_name,
                    "price": usd_price * fx_rate,
                    "market_cap": usd_marcap * fx_rate,
                    "usd_price": usd_price,
                    "currency": "$",
                    "changes": changes,
                    "trailing_per": trailing_per_dict.get(yf_symbol),
                    "forward_per": forward_per_dict.get(yf_symbol),
                }
            except Exception as e:
                logger.error("US stock data fetch failed for symbol=%s", symbol, exc_info=True)
                return None
            finally:
                with _lock:
                    done_count += 1
                    _c = done_count
                if progress_callback:
                    progress_callback(min(_c, total), total)

        chunk_results = []
        with ThreadPoolExecutor(max_workers=5) as exe:
            futures = [exe.submit(process_symbol, s, n) for s, n in chunk]
            for f in as_completed(futures):
                res = f.result()
                if res:
                    chunk_results.append(res)

        return chunk_results

    results = []
    with ThreadPoolExecutor(max_workers=min(num_chunks, 6)) as executor:
        for rs in executor.map(process_chunk, chunks):
            results.extend(rs)

    _YF_BULK_CACHE.clear()
    return results


def fetch_us_market_data(market="NASDAQ 100", top_n=200, progress_callback=None):
    from data.market import get_stock_listing, get_usd_krw_rate

    try:
        df_list = pd.DataFrame()
        if market == "NASDAQ 100":
            try:
                import io
                res = requests.get('https://en.wikipedia.org/wiki/Nasdaq-100', headers={'User-Agent': 'Mozilla/5.0'}, timeout=10)
                tables = pd.read_html(io.StringIO(res.text), flavor='lxml')
                for tbl in tables:
                    if 'Ticker' in tbl.columns:
                        df_list = tbl.rename(columns={'Ticker': 'Symbol', 'Company': 'Name'})
                        break
                    elif 'Symbol' in tbl.columns:
                        df_list = tbl.rename(columns={'Company': 'Name'})
                        break
            except Exception:
                logger.debug("Wikipedia NASDAQ-100 table fetch error, falling back to NASDAQ listing", exc_info=True)

            if df_list.empty:
                df_list = get_stock_listing('NASDAQ')
        else:
            df_list = get_stock_listing(market)

        if df_list.empty:
            return []

        _VALID_SYM = re.compile(r'^[A-Z]{1,5}(\.[A-Z]{1,2})?$')
        mask = df_list['Symbol'].astype(str).str.strip().str.upper().str.match(_VALID_SYM)
        df_list = df_list[mask]

        raw_list = df_list[['Symbol', 'Name']].head(top_n).values.tolist()
        symbols_with_names = [
            (str(sym), str(nm))
            for sym, nm in raw_list
        ]
        fx_rate = get_usd_krw_rate()
        return fetch_us_stock_data_bulk(symbols_with_names, market, fx_rate, progress_callback)
    except Exception as e:
        logger.error("fetch_us_market_data failed for market=%s", market, exc_info=True)
        return []
