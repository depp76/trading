import gc
import json
import re
import io
import time
import threading
import requests
import FinanceDataReader as fdr
import pandas as pd
import polars as pl
import numpy as np
from bs4 import BeautifulSoup
from yahooquery import Ticker as YQTicker
from dotenv import load_dotenv
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, date as _date
from collections import OrderedDict
import functools
import os
import ast

load_dotenv()

# Suppress noisy internal logging from yfinance / yahooquery
import logging as _logging
_logging.getLogger('yahooquery').setLevel(_logging.CRITICAL)
_logging.getLogger('yfinance').setLevel(_logging.CRITICAL)
_logging.getLogger('peewee').setLevel(_logging.CRITICAL)

logger = _logging.getLogger(__name__)  # 'data_fetcher'

import urllib3
from requests.adapters import HTTPAdapter

class YFTlsAdapter(HTTPAdapter):
    def init_poolmanager(self, *args, **kwargs):
        ctx = urllib3.util.ssl_.create_urllib3_context()
        try:
            ctx.set_ciphers('DEFAULT@SECLEVEL=1')
        except Exception:
            logger.debug("TLS cipher SECLEVEL=1 not supported, using default context")
        kwargs['ssl_context'] = ctx
        return super().init_poolmanager(*args, **kwargs)

_YF_SESSION = requests.Session()
_YF_SESSION.mount("https://", YFTlsAdapter())
_YF_SESSION.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"})
_YF_CRUMB = None
_YF_CRUMB_LOCK = threading.Lock()

def _get_yf_crumb(force_refresh: bool = False):
    global _YF_CRUMB
    with _YF_CRUMB_LOCK:
        if _YF_CRUMB is not None and not force_refresh:
            return _YF_CRUMB
        _YF_CRUMB = None  # reset before re-fetch
        try:
            _YF_SESSION.get("https://fc.yahoo.com", timeout=5)
            _YF_CRUMB = _YF_SESSION.get("https://query2.finance.yahoo.com/v1/test/getcrumb", timeout=5).text
        except Exception:
            logger.warning("Failed to fetch Yahoo Finance crumb, quote batches may fail", exc_info=True)
            _YF_CRUMB = ""
        return _YF_CRUMB


def yf_quote_batch(symbols: list, timeout: int = 15, chunk_size: int = 50) -> dict:
    """Batch-fetch raw Yahoo Finance v7 quote results for a list of symbols.

    Handles chunking, crumb retrieval/refresh-on-401, and retry-with-backoff
    in one place, shared by every caller that needs Yahoo quote data
    (real-time prices, market cap / PE / name lookups, etc).

    Returns {symbol: raw_quote_item_dict} for every symbol Yahoo returned data for.
    Symbols are looked up exactly as passed in (callers are responsible for any
    "." -> "-" ticker translation and for mapping results back).
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
        for attempt in range(3):
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
                if attempt == 2:
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
        except Exception as e:
            logger.warning("YF v7 quote batch error", exc_info=True)
    else:
        with ThreadPoolExecutor(max_workers=min(len(chunks), 4)) as exe:
            futures = {exe.submit(_fetch_chunk, c): c for c in chunks}
            for fut in as_completed(futures):
                try:
                    results.update(fut.result())
                except Exception as e:
                    logger.warning("YF v7 quote batch error", exc_info=True)

    return results


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


# Pre-compute start_date at module level — 410 days ≈ 285 trading days,
# safely covers 200D + YTD lookback.
_START_DATE = (datetime.now() - timedelta(days=410)).strftime("%Y-%m-%d")
_TD_PERIODS = {"3d": 3, "5d": 5, "10d": 10, "20d": 20, "60d": 60, "120d": 120}
_CHANGE_KEYS = tuple(_TD_PERIODS.keys())

# Tickers that FDR resolves natively but yfinance cannot (needs ^ prefix, or is KR-only).
# These are routed directly to fdr.DataReader, bypassing yfinance entirely to
# prevent "possibly delisted; no timezone found" warnings.
_FDR_ONLY_TICKERS = frozenset({
    "JP10YT", "KR3YT",                       # Bond yields (handled elsewhere too)
    "USD/KRW",                               # FX pair
})

# Module-level USD/KRW rate cache (refreshed once per process run)
_USD_KRW_CACHE: dict = {"rate": None, "df": None}

# Shared history cache — only non-empty DataFrames are stored,
# so transient fetch failures (e.g. during parallel startup) are retried.
# Capped at _HIST_CACHE_MAX entries; least-recently-used entries are evicted first.
_HIST_CACHE: "OrderedDict[tuple, pl.DataFrame]" = OrderedDict()
_HIST_CACHE_MAX = 1000  # Maximum number of tickers cached in memory

# ── Cache efficiency monitoring (see get_historical_data / _log_hist_cache_stats) ──
_HIST_CACHE_HITS = 0
_HIST_CACHE_MISSES = 0
_HIST_CACHE_LOG_INTERVAL = 100  # log the hit/miss ratio every N lookups


def _log_hist_cache_stats() -> None:
    """Log _HIST_CACHE's cumulative hit/miss ratio at INFO level every
    _HIST_CACHE_LOG_INTERVAL lookups, so cache effectiveness is visible in app.log
    without adding per-call noise."""
    total = _HIST_CACHE_HITS + _HIST_CACHE_MISSES
    if total and total % _HIST_CACHE_LOG_INTERVAL == 0:
        hit_rate = _HIST_CACHE_HITS / total * 100
        logger.info(
            "_HIST_CACHE stats: %d hits / %d misses (%.1f%% hit rate), size=%d/%d",
            _HIST_CACHE_HITS, _HIST_CACHE_MISSES, hit_rate, len(_HIST_CACHE), _HIST_CACHE_MAX,
        )


def _hist_df_is_stale(df: "pl.DataFrame") -> bool:
    """True if a cached polars daily-history df (with a "Date" column) predates
    today and today could plausibly have new data (i.e. today is a weekday).
    Used so session-lifetime caches don't keep serving yesterday's snapshot
    once the current day's close is actually published."""
    if df is None or df.is_empty():
        return False
    if datetime.now().weekday() >= 5:  # Sat/Sun — markets closed, nothing new to fetch
        return False
    try:
        last_date = df.get_column("Date")[-1]
    except Exception:
        logger.debug("_hist_df_is_stale: failed to read last date from df", exc_info=True)
        return False
    return last_date < datetime.now().date()


def _pdf_is_stale(pdf) -> bool:
    """Same as _hist_df_is_stale but for pandas DataFrames indexed by date
    (used by the JP10Y/KR3Y/VKOSPI wrappers)."""
    if pdf is None or pdf.empty:
        return False
    if datetime.now().weekday() >= 5:
        return False
    try:
        last_date = pdf.index[-1]
        if hasattr(last_date, "date"):
            last_date = last_date.date()
    except Exception:
        logger.debug("_pdf_is_stale: failed to read last date from pdf index", exc_info=True)
        return False
    return last_date < datetime.now().date()


@functools.lru_cache(maxsize=16)
def get_stock_listing(market: str) -> pd.DataFrame:
    """Cached version of fdr.StockListing to prevent redundant network requests."""
    # FinanceDataReader's default KRX-DESC uses a Github cache that often causes HTTP 404 Not Found.
    # We bypass it by fetching the descriptive listing directly from the KIND portal.
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


@functools.lru_cache(maxsize=4)
def _get_listing_with_norm(market: str) -> pd.DataFrame:
    """Cached listing with pre-computed NameNorm column (upper, stripped)."""
    df = get_stock_listing(market).copy()
    df['NameNorm'] = df['Name'].str.upper().str.replace(r'[\s_]+', '', regex=True)
    return df


def _fast_kr_history(ticker: str, start: str) -> pl.DataFrame:
    try:
        start_str = start.replace("-", "")
        end_str = datetime.now().strftime("%Y%m%d")
        url = f"https://api.finance.naver.com/siseJson.naver?symbol={ticker}&requestType=1&startTime={start_str}&endTime={end_str}&timeframe=day"
        
        res = None
        for attempt in range(3):
            try:
                res = requests.get(url, timeout=8)
                res.raise_for_status()
                break
            except Exception as e:
                if attempt == 2:
                    raise e
                time.sleep(1)
                
        text = res.text.replace('\n', '').replace('\t', '').replace(' ', '')
        text = re.sub(r'\'', '"', text)
        
        try:
            data = json.loads(text.strip())
        except Exception:
            logger.debug("json.loads failed for Naver history response, retrying with ast.literal_eval")
            data = ast.literal_eval(text.strip())

        if len(data) > 1:
            rows = []
            for row in data[1:]:
                try:
                    ymd = int(row[0])
                    d_obj = _date(ymd // 10000, (ymd % 10000) // 100, ymd % 100)
                    rows.append({"Date": d_obj, "Open": row[1], "High": row[2], "Low": row[3], "Close": row[4], "Volume": row[5]})
                except Exception:
                    logger.debug("Naver history row parse error, skipping row", exc_info=True)
            if rows:
                return pl.DataFrame(rows).sort("Date")
    except Exception as e:
        logger.error("Naver fast history fetch failed for ticker=%s", ticker, exc_info=True)
    return pl.DataFrame()


# Store bulk history prefetches for US markets (populated by fetch_us_stock_data_bulk)
_YF_BULK_CACHE: dict = {}


def get_historical_data(ticker: str, start: str) -> pl.DataFrame:
    """Historical data with a smart cache that skips empty DataFrames.

    Unlike @lru_cache, transient fetch failures during parallel startup
    are NOT permanently cached — the next call will retry the fetch.

    Routing:
      - 6-digit KR codes  → Naver Finance JSON API (fastest)
      - _FDR_ONLY_TICKERS → fdr.DataReader directly
      - Everything else   → yfinance primary, FDR fallback
    """
    global _HIST_CACHE_HITS, _HIST_CACHE_MISSES

    cache_key = (ticker, start)
    cached = _HIST_CACHE.get(cache_key)
    if cached is not None:
        _HIST_CACHE.move_to_end(cache_key)  # mark as most-recently-used
        if not _hist_df_is_stale(cached):
            _HIST_CACHE_HITS += 1
            _log_hist_cache_stats()
            return cached
        # Cached data predates today on a weekday — today's close may have been
        # published since the last fetch, so retry instead of serving stale data.

    _HIST_CACHE_MISSES += 1
    _log_hist_cache_stats()

    df = _fetch_historical_uncached(ticker, start)
    if not df.is_empty():
        # Evict least-recently-used entry if cache is full (only when adding a new key)
        if cache_key not in _HIST_CACHE and len(_HIST_CACHE) >= _HIST_CACHE_MAX:
            try:
                _HIST_CACHE.popitem(last=False)
            except Exception:
                logger.debug("LRU cache eviction failed (cache may be empty)", exc_info=True)
        _HIST_CACHE[cache_key] = df
        _HIST_CACHE.move_to_end(cache_key)
        return df
    # Fresh fetch failed/empty — fall back to the stale cached copy rather than nothing.
    return cached if cached is not None else df


def _fetch_historical_uncached(ticker: str, start: str) -> pl.DataFrame:
    """Actual fetch — called only on cache miss."""
    try:
        # ── Korean stock / ETF ────────────────────────────────────────────
        if len(ticker) == 6 and "." not in ticker and any(c.isdigit() for c in ticker):
            df = _fast_kr_history(ticker, start)
            if not df.is_empty():
                return df

        # ── FDR-native indices (IXIC, KS11, VIX, …) ─────────────────────
        if ticker in _FDR_ONLY_TICKERS:
            if ticker == "KR3YT":
                rows = []
                start_d = datetime.strptime(start, "%Y-%m-%d").date() if start else None
                for page in range(1, 40):
                    url = f"https://finance.naver.com/marketindex/interestDailyQuote.naver?marketindexCd=IRR_GOVT03Y&page={page}"
                    try:
                        res = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=5)
                        soup = BeautifulSoup(res.text, 'html.parser')
                        tr_list = soup.select('table.tbl_exchange.today tbody tr')
                        if not tr_list: break
                        
                        done = False
                        for tr in tr_list:
                            tds = tr.select('td')
                            if len(tds) < 4: continue
                            date_str = tds[0].text.strip()
                            if not date_str: continue
                            val = float(tds[1].text.strip())
                            d_obj = datetime.strptime(date_str, "%Y.%m.%d").date()
                            if start_d and d_obj < start_d:
                                done = True
                                break
                            rows.append({"Date": d_obj, "Close": val, "Open": val, "High": val, "Low": val, "Volume": 0})
                        if done: break
                    except Exception as e:
                        logger.warning("Error scraping KR3YT page %d", page, exc_info=True)
                        break
                if rows:
                    return pl.DataFrame(rows).sort("Date")

            df_pd = fdr.DataReader(ticker, start)
            return _to_polars(df_pd)

        # ── US stocks / ETFs: check bulk cache, then yfinance, FDR fallback ─
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
            logger.debug("yfinance history fetch failed for %s, falling through to yahooquery/FDR", ticker, exc_info=True)

        if df_pd is None or (hasattr(df_pd, 'empty') and df_pd.empty):
            try:
                from yahooquery import Ticker as YQTicker
                _yq = YQTicker(ticker, asynchronous=False)
                _yq_df = _yq.history(start=start)
                if isinstance(_yq_df, pd.DataFrame) and not _yq_df.empty:
                    # YQ returns MultiIndex (symbol, date). We just need to reset it.
                    _yq_df = _yq_df.reset_index()
                    if 'date' in _yq_df.columns:
                        _yq_df = _yq_df.rename(columns={'date': 'Date', 'close': 'Close', 'high': 'High', 'low': 'Low', 'open': 'Open', 'volume': 'Volume'})
                    df_pd = _yq_df
            except Exception:
                logger.debug("yahooquery history fetch failed for %s, falling through to FDR", ticker, exc_info=True)

        if df_pd is None or (hasattr(df_pd, 'empty') and df_pd.empty):
            df_pd = fdr.DataReader(ticker, start)

        return _to_polars(df_pd)
    except Exception:
        logger.warning("All history sources failed for ticker=%s, returning empty DataFrame", ticker, exc_info=True)
        return pl.DataFrame()


def safe_float(value, default=0.0):
    if value is None:
        return default
    try:
        if isinstance(value, float) and (value != value):
            return default
        s = str(value).replace(',', '').strip()
        if s == '-' or not s:
            return default
        return float(s)
    except (ValueError, TypeError):
        return default


def fetch_naver_realtime_prices(tickers: list) -> dict:
    """Fetches real-time prices for Korean stocks/ETFs from Naver Finance API."""
    if not tickers:
        return {}
    prices = {}
    try:
        def _fetch_chunk(chunk):
            url = f"https://polling.finance.naver.com/api/realtime/domestic/stock/{','.join(chunk)}"
            return requests.get(url, timeout=5).json()

        chunk_size = 50
        chunks = [tickers[i:i + chunk_size] for i in range(0, len(tickers), chunk_size)]
        
        with ThreadPoolExecutor(max_workers=min(len(chunks), 4)) as exe:
            for data in exe.map(_fetch_chunk, chunks):
                if 'datas' in data:
                    for item in data['datas']:
                        code = item.get('itemCode')
                        price_str = None
                        over_info = item.get('overMarketPriceInfo')
                        if over_info and isinstance(over_info, dict) and over_info.get('overMarketStatus') == 'OPEN':
                            price_str = over_info.get('overPrice')
                        if not price_str:
                            price_str = item.get('closePrice')
                        if code and price_str:
                            prices[code] = safe_float(price_str)
    except Exception as e:
        logger.error("Naver real-time price fetch failed", exc_info=True)
    return prices


def fetch_us_realtime_prices(tickers: list) -> dict:
    """Fetches real-time prices for US stocks/indices from Yahoo Finance v7 quote API."""
    if not tickers:
        return {}
    prices = {}
    try:
        yf_symbols = [s.replace(".", "-") for s in tickers]
        quotes = yf_quote_batch(yf_symbols, timeout=10, chunk_size=50)
        for sym, item in quotes.items():
            orig_sym = sym.replace("-", ".")
            price = item.get("regularMarketPrice") or item.get("postMarketPrice")
            if price is not None:
                prices[orig_sym] = float(price)
    except Exception as e:
        logger.error("US real-time price fetch failed", exc_info=True)
    return prices


_NAVER_HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}


def _fetch_naver_per_single(code: str) -> tuple:
    """Fetch trailing PER and forward(consensus) PER for one KR stock from Naver mobile API.

    Returns (code, trailing_per | None, forward_per | None).
    API: https://m.stock.naver.com/api/stock/{code}/integration
    Fields in totalInfos:  code="per" -> trailing PER,  code="cnsPer" -> consensus(forward) PER
    Value format: "31.38x" -> strip the Korean unit suffix '배' and convert to float.
    """
    try:
        url = f"https://m.stock.naver.com/api/stock/{code}/integration"
        res = requests.get(url, headers=_NAVER_HEADERS, timeout=4)
        if res.status_code != 200:
            return code, None, None
        data = res.json()
        tper = fper = None
        for info in data.get('totalInfos', []):
            c = info.get('code', '')
            v = info.get('value', '')
            if not v or v in ('-', 'N/A', ''):
                continue
            # Strip Korean unit suffix and commas, then parse float
            num_str = v.replace('배', '').replace(',', '').strip()  # '배' = Korean unit suffix for 'times/multiple'
            try:
                num = float(num_str)
            except (ValueError, TypeError):
                continue
            if c == 'per':
                tper = round(num, 1)
            elif c == 'cnsPer':
                fper = round(num, 1)
        return code, tper, fper
    except Exception:
        logger.debug("Naver PER fetch failed for code=%s", code, exc_info=True)
        return code, None, None


def fetch_naver_per_batch(codes: list, max_workers: int = 30) -> tuple:
    """Fetch trailing PER and forward PER for a list of KR stock codes in parallel.

    Returns (trailing_per_dict, forward_per_dict)  keyed by code string.
    """
    trailing: dict = {}
    forward: dict = {}
    if not codes:
        return trailing, forward
    with ThreadPoolExecutor(max_workers=max_workers) as exe:
        for code, tper, fper in exe.map(_fetch_naver_per_single, codes):
            if tper is not None:
                trailing[code] = tper
            if fper is not None:
                forward[code] = fper
    return trailing, forward


# ─────────────────────────────────────────────
# Kiwoom REST API helpers
# ─────────────────────────────────────────────
_KIWOOM_TOKEN_CACHE: dict = {"token": None, "expires": 0}
_KIWOOM_KEYS_CACHE: dict = {}

def _get_kiwoom_keys():
    """Reads appkey and secretkey from the Kiwoom MCP folder, using cache to avoid disk I/O."""
    if "appkey" in _KIWOOM_KEYS_CACHE:
        return _KIWOOM_KEYS_CACHE["appkey"], _KIWOOM_KEYS_CACHE["secretkey"]
        
    key_dir = r"D:\Source Code\Kiwoom MCP"
    appkey_path = os.path.join(key_dir, "45573900_appkey.txt")
    secret_path = os.path.join(key_dir, "45573900_secretkey.txt")
    if not os.path.exists(appkey_path) or not os.path.exists(secret_path):
        raise FileNotFoundError("API Key files not found in D:\\Source Code\\Kiwoom MCP")
    with open(appkey_path, "r", encoding="utf-8") as f:
        appkey = f.read().strip()
    with open(secret_path, "r", encoding="utf-8") as f:
        appsecret = f.read().strip()
        
    _KIWOOM_KEYS_CACHE["appkey"] = appkey
    _KIWOOM_KEYS_CACHE["secretkey"] = appsecret
    return appkey, appsecret


def _get_kiwoom_token():
    """Returns a cached or newly-fetched Kiwoom REST API token."""
    now = time.time()
    if _KIWOOM_TOKEN_CACHE["token"] and now < _KIWOOM_TOKEN_CACHE["expires"]:
        appkey, appsecret = _get_kiwoom_keys()
        return _KIWOOM_TOKEN_CACHE["token"], appkey, appsecret

    appkey, appsecret = _get_kiwoom_keys()
    token_url = "https://api.kiwoom.com/oauth2/token"
    body = {"grant_type": "client_credentials", "appkey": appkey, "secretkey": appsecret}
    res = requests.post(token_url, headers={"content-type": "application/json;charset=UTF-8"}, json=body, timeout=5)
    res.raise_for_status()
    res_data = res.json()
    token = res_data.get("access_token") or res_data.get("token")
    if not token:
        msg = res_data.get("return_msg", "Unknown error")
        code = res_data.get("return_code", "N/A")
        if str(code) == "3" and "8050" in msg:
            raise ValueError(f"Kiwoom Securities designated terminal authentication failed (8050 error).\nPlease register 'Designated PC' in OpenAPI details on the Kiwoom homepage and try again.\n(Message: {msg})")
        raise ValueError(f"Failed to issue Kiwoom Securities API token: {msg} (Error Code: {code})")
    _KIWOOM_TOKEN_CACHE["token"] = token
    _KIWOOM_TOKEN_CACHE["expires"] = now + 3500  # ~1 hour
    return token, appkey, appsecret


def _kiwoom_parse_price(val_str):
    """Parses Kiwoom price strings that may have +/- prefix."""
    if not val_str or not val_str.strip():
        return 0
    return abs(int(val_str.replace("+", "").replace("-", "").replace(",", "").strip()))


def fetch_kiwoom_stock_info(code, token=None, appkey=None, appsecret=None):
    """Fetches current price, name, market cap via Kiwoom ka10007."""
    if not token:
        token, appkey, appsecret = _get_kiwoom_token()
    headers = {
        "content-type": "application/json;charset=UTF-8",
        "authorization": f"Bearer {token}",
        "appkey": appkey,
        "secretkey": appsecret,
        "api-id": "ka10007"
    }
    r = requests.post("https://api.kiwoom.com/api/dostk/mrkcond",
                      headers=headers, json={"stk_cd": code}, timeout=5)
    r.raise_for_status()
    d = r.json()
    if d.get("return_code", -1) != 0:
        return None
    price = _kiwoom_parse_price(d.get("cur_prc", ""))
    flo_stkcnt = int((d.get("flo_stkcnt", "0") or "0").strip())
    marcap = flo_stkcnt * 1000 * price if price > 0 else 0
    return {
        "name": d.get("stk_nm", ""),
        "price": price,
        "market_cap": marcap,
        "open": _kiwoom_parse_price(d.get("open_pric", "")),
        "high": _kiwoom_parse_price(d.get("high_pric", "")),
        "low": _kiwoom_parse_price(d.get("low_pric", "")),
    }


def fetch_kiwoom_daily_ohlcv(code, token=None, appkey=None, appsecret=None):
    """Fetches ~30 days of daily OHLCV via Kiwoom ka10005. Returns pd.DataFrame."""
    if not token:
        token, appkey, appsecret = _get_kiwoom_token()
    headers = {
        "content-type": "application/json;charset=UTF-8",
        "authorization": f"Bearer {token}",
        "appkey": appkey,
        "secretkey": appsecret,
        "api-id": "ka10005"
    }
    r = requests.post("https://api.kiwoom.com/api/dostk/mrkcond",
                      headers=headers, json={"stk_cd": code}, timeout=5)
    r.raise_for_status()
    d = r.json()
    items = d.get("stk_ddwkmm", [])
    if not items:
        return None
    rows = []
    for item in items:
        try:
            dt = pd.to_datetime(item["date"], format="%Y%m%d")
            rows.append({
                "Date": dt,
                "Open": _kiwoom_parse_price(item.get("open_pric", "")),
                "High": _kiwoom_parse_price(item.get("high_pric", "")),
                "Low": _kiwoom_parse_price(item.get("low_pric", "")),
                "Close": _kiwoom_parse_price(item.get("close_pric", "")),
                "Volume": int(item.get("trde_qty", "0") or "0"),
            })
        except Exception:
            continue
    if not rows:
        return None
    df = pd.DataFrame(rows).sort_values("Date").reset_index(drop=True)
    return df


def fetch_account_deposit(pwd: str = "") -> float:
    """
    Fetches the account deposit (available cash balance) using Kiwoom REST API kt00001.
    Reuses cached token and API keys.
    """
    access_token, appkey, appsecret = _get_kiwoom_token()

    # ── Step 1: Retrieve account number list (ka00001) → verify actual 10-digit acnt_no ──────
    acnt_no = "4557390001"  # fallback: 8-digit account + "01" suffix
    try:
        list_headers = {
            "content-type": "application/json;charset=UTF-8",
            "authorization": f"Bearer {access_token}",
            "appkey": appkey,
            "secretkey": appsecret,
            "api-id": "ka00001",
        }
        list_res = requests.post(
            "https://api.kiwoom.com/api/dostk/acnt",
            headers=list_headers,
            json={},
            timeout=5,
        )
        if list_res.status_code == 200:
            list_data = list_res.json()
            logger.debug("[ka00001 Account list response] %s", list_data)
            acct_list = list_data.get("acnt_list") or list_data.get("acctList") or []
            if acct_list:
                # Use the first account number
                first = acct_list[0]
                acnt_no = (
                    first.get("acnt_no")
                    or first.get("acno")
                    or first.get("acctNo")
                    or acnt_no
                )
    except Exception as e:
        logger.warning("[ka00001] Account list lookup failed (ignoring)", exc_info=True)

    # ── Step 2: Fetch available cash deposit (kt00001) ─────────────────────────────────────
    inquire_url = "https://api.kiwoom.com/api/dostk/acnt"
    headers = {
        "content-type": "application/json;charset=UTF-8",
        "authorization": f"Bearer {access_token}",
        "appkey": appkey,
        "secretkey": appsecret,
        "api-id": "kt00001",
    }
    params = {
        "acnt_no": acnt_no,
        "pwd": pwd,
        "qry_tp": "1",  # 1: Estimated lookup
    }
    logger.debug("[kt00001 Request] acnt_no=%s, qry_tp=1", acnt_no)
    dep_res = requests.post(inquire_url, headers=headers, json=params, timeout=5)
    dep_res.raise_for_status()
    dep_data = dep_res.json()
    logger.debug("[kt00001 Response] %s", dep_data)

    if str(dep_data.get("return_code", "-1")) == "0":
        # kt00001 field priority: d2_entra (D+2 estimated deposit) > d2_entr > pymn_alow_amt > ord_alow_amt > entr
        dep_keys = ["d2_entra", "d2_entr", "pymn_alow_amt", "ord_alow_amt", "entr"]
        deposit_str = "0"

        for k in dep_keys:
            if k in dep_data:
                val_str = str(dep_data[k]).strip()
                if val_str:
                    deposit_str = val_str
                    val_int = 0.0
                    try:
                        val_int = float(deposit_str.replace(",", ""))
                    except Exception:
                        logger.debug(
                            "Deposit field '%s' value not parseable as float: %r",
                            k, deposit_str, exc_info=True,
                        )

                    if k in ("d2_entra", "d2_entr") and val_int == 0.0:
                        pymn_str = str(dep_data.get("pymn_alow_amt", "0") or "0").strip()
                        try:
                            pymn_val = float(pymn_str.replace(",", ""))
                        except Exception:
                            pymn_val = 0.0
                        if pymn_val > 10.0:
                            continue
                    break

        return float(deposit_str.replace(",", "").strip())
    else:
        msg = dep_data.get("return_msg", "Unknown error")
        code = dep_data.get("return_code", "N/A")
        raise Exception(f"[kt00001] API Error (Code: {code}): {msg}")


# ─────────────────────────────────────────────
# KRX Open API helpers (VKOSPI / KOSPI 200 Volatility Index)
# ─────────────────────────────────────────────
_KRX_KEY_CACHE: dict = {}
_KRX_DERIV_IDX_URL = "https://data-dbg.krx.co.kr/svc/apis/idx/drvprod_dd_trd.json"
_KRX_VKOSPI_CACHE_PATH = "vkospi_cache.json"
VKOSPI_INDEX_NAME = "코스피 200 변동성지수"  # Korean name required by the KRX API — do not translate


def _get_krx_auth_key() -> str:
    """Reads the KRX Open API auth key from the KRX_AUTH_KEY env var (.env)."""
    if "key" in _KRX_KEY_CACHE:
        return _KRX_KEY_CACHE["key"]
    key = os.getenv("KRX_AUTH_KEY")
    if not key:
        raise ValueError(
            "KRX_AUTH_KEY is not set. Please add "
            "KRX_AUTH_KEY=your_issued_key to the .env file in the project root. "
            "(Sign up at data.krx.co.kr → apply for 'Derivative Index Market Price Info' API at openapi.krx.co.kr)"
        )
    _KRX_KEY_CACHE["key"] = key
    return key


def fetch_krx_derivative_index_day(bas_dd: str, index_name: str = VKOSPI_INDEX_NAME) -> dict | None:
    """Fetches one trading day's row for a KRX derivative index (default: VKOSPI).

    bas_dd: 'YYYYMMDD'. The underlying API (Derivative Index Market Price Info) is single-date only —
    there is no date-range mode. Returns None on weekends/holidays (empty OutBlock_1)
    or if index_name isn't present that day.
    """
    res = requests.get(
        _KRX_DERIV_IDX_URL,
        headers={"AUTH_KEY": _get_krx_auth_key()},
        params={"basDd": bas_dd},
        timeout=10,
    )
    if res.status_code != 200:
        try:
            err = res.json()
            msg = err.get("respMsg", res.text)
            code = err.get("respCode", res.status_code)
        except Exception:
            logger.debug("Failed to parse KRX API error response as JSON, using raw text", exc_info=True)
            msg, code = res.text, res.status_code
        raise ValueError(
            f"KRX Open API Error (Code: {code}): {msg}. "
            f"Please check the API approval status for 'Derivative Index Market Price Info' on the openapi.krx.co.kr my page."
        )
    for item in res.json().get("OutBlock_1", []) or []:
        if item.get("IDX_NM") == index_name:
            return item
    return None


def fetch_vkospi(date_str: str = None) -> float:
    """Returns the VKOSPI closing value for a given date (default: today).

    date_str: 'YYYY-MM-DD'. Returns 0.0 if no data yet for that date
    (weekend/holiday/not published).
    """
    bas_dd = (date_str or datetime.now().strftime("%Y-%m-%d")).replace("-", "")
    item = fetch_krx_derivative_index_day(bas_dd)
    return safe_float(item.get("CLSPRC_IDX")) if item else 0.0


def _load_vkospi_cache() -> dict:
    if os.path.exists(_KRX_VKOSPI_CACHE_PATH):
        try:
            with open(_KRX_VKOSPI_CACHE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            logger.debug("Failed to load VKOSPI cache file, starting fresh", exc_info=True)
    return {}


def _save_vkospi_cache(cache: dict):
    try:
        with open(_KRX_VKOSPI_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False)
    except Exception as e:
        logger.warning("VKOSPI cache save error", exc_info=True)


def fetch_vkospi_history(start: str, end: str = None, max_workers: int = 8) -> pl.DataFrame:
    """Builds daily VKOSPI OHLC history for [start, end] (default end: today).

    Results are cached to vkospi_cache.json keyed by date, so repeated calls only
    fetch newly-missing days from the KRX Open API instead of re-requesting the
    whole range every time (the API has no bulk/range endpoint — one call per day).
    """
    end = end or datetime.now().strftime("%Y-%m-%d")
    bas_dds = [d.strftime("%Y%m%d") for d in pd.bdate_range(start, end)]

    cache = _load_vkospi_cache()
    today_bd = datetime.now().strftime("%Y%m%d")
    # Re-check today even if already cached as null: KRX may not have published
    # the day's index yet at the time of an earlier call this session.
    missing = [bd for bd in bas_dds if bd not in cache or (cache[bd] is None and bd == today_bd)]

    if missing:
        with ThreadPoolExecutor(max_workers=max_workers) as exe:
            futures = {exe.submit(fetch_krx_derivative_index_day, bd): bd for bd in missing}
            for fut in as_completed(futures):
                bd = futures[fut]
                try:
                    item = fut.result()
                except Exception as e:
                    logger.warning("KRX VKOSPI fetch error for date=%s", bd, exc_info=True)
                    continue
                if item:
                    cache[bd] = item
                elif bd != today_bd:
                    # Confirmed no data for a past business day (public holiday) —
                    # cache permanently so it isn't retried forever.
                    cache[bd] = None
                # else: today not yet published by KRX — leave unresolved, retry next call.
        _save_vkospi_cache(cache)

    rows = []
    for bd in bas_dds:
        item = cache.get(bd)
        if item:
            rows.append({
                "Date": _date(int(bd[:4]), int(bd[4:6]), int(bd[6:8])),
                "Open": safe_float(item.get("OPNPRC_IDX")),
                "High": safe_float(item.get("HGPRC_IDX")),
                "Low": safe_float(item.get("LWPRC_IDX")),
                "Close": safe_float(item.get("CLSPRC_IDX")),
                "Volume": 0,
            })
    if not rows:
        return pl.DataFrame()
    return pl.DataFrame(rows).sort("Date")


_VKOSPI_PDF_CACHE: dict = {"df": None}


def _get_vkospi_pdf() -> pd.DataFrame:
    """pandas/DatetimeIndex wrapper matching the _get_kr3y_df / _get_jp10y_df convention,
    so VKOSPI can be dropped into the same dashboard/MA-chart code paths as those indices.

    Session-cached; re-fetched once the cached data predates today on a weekday
    (fetch_vkospi_history itself also retries today's disk cache entry on every
    call, so this just makes sure the in-process pandas snapshot picks that up).
    """
    cached = _VKOSPI_PDF_CACHE["df"]
    if cached is not None and not _pdf_is_stale(cached):
        return cached
    df = fetch_vkospi_history(_START_DATE)
    if df.is_empty():
        return cached
    pdf = df.to_pandas()
    pdf["Date"] = pd.to_datetime(pdf["Date"])
    pdf = pdf.set_index("Date")
    _VKOSPI_PDF_CACHE["df"] = pdf
    return pdf


def fetch_historical_changes(ticker, current_price, df_pd=None, mode='pct'):
    """Calculates historical changes (single-pass numpy, no redundant I/O).

    mode: 'pct' | 'bp' | 'abs'
    """
    changes = {k: 0.0 for k in _CHANGE_KEYS}
    changes.update({"52w_high": 0.0, "52w_low": 0.0, "52w_high_diff": 0.0, "52w_low_diff": 0.0, "ma20_div": 0.0, "ma50_div": 0.0})

    if current_price <= 0:
        return changes

    try:
        if df_pd is None:
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
        logger.warning("fetch_historical_changes failed for ticker=%s", ticker, exc_info=True)

    return changes


def fetch_kr_market_data(market="KOSPI", top_n=200, progress_callback=None):
    try:
        # ── Step 1: Scrape Naver sise_market_sum pages in parallel (price, marcap, PER all at once) ──
        sosok = 0 if market == "KOSPI" else 1
        max_pages = (top_n // 50) + 2

        def _fetch_page(pg):
            url = f"https://finance.naver.com/sise/sise_market_sum.naver?sosok={sosok}&page={pg}"
            r = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=5)
            soup = BeautifulSoup(r.content, 'html.parser', from_encoding='euc-kr')
            rows = []
            for tr in soup.select('table.type_2 tbody tr'):
                a_tag = tr.select_one('a.tltle')
                if not a_tag:
                    continue
                code = a_tag['href'].split('code=')[-1].zfill(6)
                name = a_tag.text.strip()
                cols = [td.text.strip().replace(',', '') for td in tr.select('td')]
                # cols layout: [rank, name, price, change, chg%, vol, marcap(100M KRW), shares, foreign%, trade_vol, PER, ROE, ...]
                marcap = int(cols[6]) * 100_000_000 if len(cols) > 6 and cols[6].isdigit() else 0
                rows.append({'Code': code, 'Name': name, 'Marcap': marcap})
            return rows

        results_list = []
        with ThreadPoolExecutor(max_workers=min(max_pages, 8)) as exe:
            # map() returns results in input order, preserving market-cap page order.
            # Collect all pages first, then truncate — do not break early to keep order.
            for rows in exe.map(_fetch_page, range(1, max_pages + 1)):
                results_list.extend(rows)

        results_list = results_list[:top_n]
        if not results_list:
            return []

        total = len(results_list)

        # ── Step 2: Start prices + PER in background; don't block on PER yet ──
        all_codes = [r['Code'] for r in results_list]
        _bg_exe = ThreadPoolExecutor(max_workers=2)
        try:
            _f_prices = _bg_exe.submit(fetch_naver_realtime_prices, all_codes)
            _f_per    = _bg_exe.submit(fetch_naver_per_batch, all_codes)
            naver_prices = _f_prices.result()  # Wait for prices only; PER continues in background

            # ── Step 3: Fetch historical changes WHILE PER is still being fetched ──
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

            # ── Step 4: PER should be done by now; assemble final results ──
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


def get_usd_krw_rate():
    """Returns USD/KRW FX rate. Session-cached, but re-checked once today's
    date rolls past the last cached close so it doesn't serve yesterday's
    rate all day once a fresh value is actually available."""
    if _USD_KRW_CACHE["rate"] is not None and not _hist_df_is_stale(_USD_KRW_CACHE["df"]):
        return _USD_KRW_CACHE["rate"]
    try:
        df = _to_polars(fdr.DataReader('USD/KRW'))
        if not df.is_empty():
            _USD_KRW_CACHE["df"] = df
            close_s = df.get_column("Close").drop_nulls()
            rate = float(close_s[-1]) if len(close_s) > 0 else 1450.0
        else:
            rate = _USD_KRW_CACHE["rate"] if _USD_KRW_CACHE["rate"] is not None else 1450.0
    except Exception:
        rate = _USD_KRW_CACHE["rate"] if _USD_KRW_CACHE["rate"] is not None else 1450.0
        logger.warning("USD/KRW rate fetch failed, using cached/fallback rate=%.1f", rate, exc_info=True)
    _USD_KRW_CACHE["rate"] = rate
    return rate

def get_index_close_for_date(ticker: str, date_str: str) -> float:
    """Returns the closing price for an index/ticker for a specific date (YYYY-MM-DD).

    Delegates straight to get_historical_data(), which already caches per
    (ticker, start) with its own today-staleness check — no need for a
    second, non-refreshing cache layer on top of it here.
    """
    df = get_historical_data(ticker, _START_DATE)
    if df is not None and not df.is_empty():
        try:
            from datetime import datetime as _datetime
            target = _datetime.strptime(date_str, "%Y-%m-%d").date()
            sub = df.filter(pl.col("Date") <= target)
            if not sub.is_empty():
                return float(sub.get_column("Close")[-1])
        except Exception:
            logger.debug("get_index_close_for_date failed for ticker=%s date=%s", ticker, date_str, exc_info=True)
    return 0.0

def get_usd_krw_rate_for_date(date_str: str) -> float:
    """Returns USD/KRW rate for a specific date (YYYY-MM-DD)."""
    get_usd_krw_rate()  # Ensure cache is populated
    df = _USD_KRW_CACHE.get("df")
    if df is not None and not df.is_empty():
        try:
            from datetime import datetime as _datetime
            target = _datetime.strptime(date_str, "%Y-%m-%d").date()
            sub = df.filter(pl.col("Date") <= target)
            if not sub.is_empty():
                return float(sub.get_column("Close")[-1])
        except Exception:
            logger.debug("get_usd_krw_rate_for_date failed for date=%s", date_str, exc_info=True)
    return get_usd_krw_rate()

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
                    if len(results) == 8:  # Just need 8 contracts
                        break
            except Exception:
                logger.debug("WTI contract parse error for sym=%s", sym, exc_info=True)
    return results


def fetch_us_stock_data_bulk(symbols_with_names, market_name, fx_rate, progress_callback=None):
    """Fetches US stock data using yahooquery (batched history + meta, per-stock parallel processing)."""
    total = len(symbols_with_names)
    # Smaller chunks prevent curl timeouts (30s+) when yahooquery fetches history for too many symbols
    chunk_size = 20
    chunks = [symbols_with_names[i:i + chunk_size] for i in range(0, total, chunk_size)]
    num_chunks = len(chunks)

    # ── Pre-fetch Naver market caps IN PARALLEL across pages ──────────────
    naver_mcaps = {}
    try:
        nm = "NASDAQ" if "NASDAQ" in market_name.upper() else ("NYSE" if "NYSE" in market_name.upper() else "AMEX")
        num_pages = max(3, (total // 100) + 2)

        def _fetch_naver_mcap_page(p):
            r = requests.get(
                f"https://api.stock.naver.com/stock/exchange/{nm}/marketValue?page={p}&pageSize=100",
                headers={'User-Agent': 'Mozilla/5.0'}, timeout=5
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
    # yf.download() has known thread-safety issues with its internal shared
    # state when called fully concurrently, so cap simultaneous downloads
    # with a semaphore instead of serializing all chunks behind a single lock.
    _yf_semaphore = threading.Semaphore(3)

    # Per-chunk metadata: try Yahoo Finance v7 quote first (fast, single request),
    # fall back to yahooquery with a generous timeout for any gaps.
    _YQ_META_TIMEOUT = 35  # seconds (fallback only)

    def process_chunk(chunk):
        yf_symbols = [s.replace(".", "-") for s, _ in chunk]
        market_cap_dict = {}
        name_dict = {}
        trailing_per_dict = {}
        forward_per_dict = {}

        # ── Step 0: Bulk Pre-fetch History via yfinance ─────────────────
        # Drastically speeds up startup by doing one bulk request per chunk
        # instead of N separate yf.download() requests.
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
                        # Extract single symbol DataFrame
                        if isinstance(bulk_df.columns, pd.MultiIndex):
                            # Default multi-index has names=['Price', 'Ticker']
                            if sym in bulk_df.columns.get_level_values(1):
                                single_df = bulk_df.xs(sym, level=1, axis=1).dropna(how='all')
                                if not single_df.empty:
                                    pl_df = _to_polars(single_df)
                                    _YF_BULK_CACHE[f"{orig_sym}_{_START_DATE}"] = pl_df
                        else:
                            # In older yfinance or edge cases where 1 ticker returns a flat index
                            if len(yf_symbols) == 1 and sym == yf_symbols[0]:
                                single_df = bulk_df.dropna(how='all')
                                if not single_df.empty:
                                    pl_df = _to_polars(single_df)
                                    _YF_BULK_CACHE[f"{orig_sym}_{_START_DATE}"] = pl_df
                    except Exception:
                        logger.debug("YF bulk history slice failed for sym=%s, skipping", sym, exc_info=True)
                
                # Removed cache_info() call since get_historical_data uses custom dict cache
        except Exception as e:
            logger.warning("YF bulk history fetch error (chunk)", exc_info=True)

        # ── Primary: Yahoo Finance v7 quote API ──────────────────────────
        # Single lightweight GET returns name / marketCap / PE for all
        # symbols in the chunk — avoids the curl timeout that plagued
        # yahooquery's per-symbol quoteSummary batches.
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
        except Exception as e:
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
                        logger.debug("yahooquery summary_detail timed out for chunk", exc_info=True)
                        details = {}
                    try:
                        qt = f_qt.result(timeout=_YQ_META_TIMEOUT)
                    except Exception:
                        logger.debug("yahooquery quote_type timed out for chunk", exc_info=True)
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
            except Exception as e:
                logger.warning("yahooquery fallback error (chunk)", exc_info=True)



        def process_symbol(symbol, name):
            nonlocal done_count
            yf_symbol = symbol.replace(".", "-")
            try:
                # Always use get_historical_data() — fdr/Naver, no curl timeouts
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
                # Last-resort: yfinance fast_info (single HTTP call, very light)
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
        # Capped at 5 (was 10): combined with the outer per-chunk pool
        # (max_workers=6), 10 here could spike to 60 concurrent OS threads.
        with ThreadPoolExecutor(max_workers=5) as exe:
            futures = [exe.submit(process_symbol, s, n) for s, n in chunk]
            for f in as_completed(futures):
                res = f.result()
                if res:
                    chunk_results.append(res)

        return chunk_results

    results = []
    # Process chunks in parallel — more workers = faster for large markets (S&P500/NASDAQ)
    with ThreadPoolExecutor(max_workers=min(num_chunks, 6)) as executor:
        for rs in executor.map(process_chunk, chunks):
            results.extend(rs)

    # _YF_BULK_CACHE is a temporary staging buffer used only during this bulk fetch.
    # Clear it after assembly to free the memory held by all those Polars DataFrames.
    _YF_BULK_CACHE.clear()

    return results


def fetch_us_market_data(market="NASDAQ 100", top_n=200, progress_callback=None):
    try:
        if market == "NASDAQ 100":
            try:
                import requests
                import io
                res = requests.get('https://en.wikipedia.org/wiki/Nasdaq-100', headers={'User-Agent': 'Mozilla/5.0'}, timeout=10)
                tables = pd.read_html(io.StringIO(res.text))
                df_list = None
                for tbl in tables:
                    if 'Ticker' in tbl.columns:
                        df_list = tbl
                        break
                if df_list is not None:
                    df_list = df_list.rename(columns={'Ticker': 'Symbol', 'Company': 'Name'})
                    logger.debug("NASDAQ 100 listing fetched: %d stocks", len(df_list))
                else:
                    logger.warning("NASDAQ 100 table not found on Wikipedia page")
                    return []
            except Exception as e:
                logger.error("Error fetching NASDAQ 100 listing", exc_info=True)
                return []
        else:
            df_list = get_stock_listing(market)
            if df_list.empty:
                return []

        # Filter to well-formed equity symbols only BEFORE taking head().
        # FDR listings include bond/warrant codes (e.g. '0162Y0', '0000A') that are
        # absent from Yahoo Finance and cause HTTP 404/timed out errors.
        # Valid equity symbols: 1-5 uppercase letters, optionally followed by
        # a single dot and up to 2 letters (share class, e.g. BRK.A).
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


def fetch_market_data(market, top_n, progress_callback=None):
    if market in ("KOSPI", "KOSDAQ"):
        return fetch_kr_market_data(market, top_n, progress_callback)
    return fetch_us_market_data(market, top_n, progress_callback)


def _fetch_naver_info(code):
    """Fallback: fetch market cap and real name from Naver Finance item page."""
    try:
        url = f"https://finance.naver.com/item/main.naver?code={code}"
        r = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=5)
        name = ""
        n_match = re.search(r'<title>(.*?)[\s]*:', r.text)
        if n_match:
            name = n_match.group(1).strip()
        m = re.search(r'id=\"_market_sum\">([\s\n\t]*)(.*?)[\s\n\t]*</em>', r.text, re.DOTALL)
        marcap = 0
        if m:
            txt = m.group(2).replace(',', '').replace('\n', '').replace('\t', '').strip()
            if '조' in txt:  # '조' = Korean unit for 1 trillion (10^12)
                parts = txt.split('조')
                jo = int(re.sub(r'[^\d]', '', parts[0]) or 0)
                eok = int(re.sub(r'[^\d]', '', parts[1]) or 0) if len(parts) > 1 else 0
                total_eok = jo * 10000 + eok
            else:
                cleaned = re.sub(r'[^\d]', '', txt)
                total_eok = int(cleaned) if cleaned else 0
            marcap = total_eok * 100_000_000
        return name, marcap
    except Exception:
        logger.debug("Naver info fetch failed for code=%s", code, exc_info=True)
    return "", 0


def _build_kr_stock_res(code, name, market, marcap):
    # ── Naver for name/marcap/price ──
    if marcap <= 0 or not name or name.startswith('KR_'):
        n_nv, mc_nv = _fetch_naver_info(code)
        if n_nv:
            name = n_nv
        if mc_nv > 0:
            marcap = mc_nv

    # Always fetch real-time price from Naver (current_price starts at 0)
    naver_prices = fetch_naver_realtime_prices([code])
    current_price = naver_prices.get(code, 0.0)

    if current_price == 0:
        try:
            start = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")
            df_p = get_historical_data(code, start)
            if not df_p.is_empty():
                current_price = float(df_p.get_column('Close')[-1])
        except Exception:
            logger.debug("Historical price fallback failed for code=%s in _build_kr_stock_res", code, exc_info=True)

    df_history = None
    changes = fetch_historical_changes(code, current_price, df_history)
    # Fetch PER from Naver mobile API (reliable for KR stocks)
    _, tper, fper = _fetch_naver_per_single(code)
    trailing_per = tper
    forward_per = fper
    return {
        "ticker": code,
        "name": name,
        "market": market,
        "price": current_price,
        "market_cap": int(marcap),
        "currency": "₩",
        "changes": changes,
        "trailing_per": trailing_per,
        "forward_per": forward_per,
    }


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
                    # Prefer exact name match first (e.g. 'TIME' → company named exactly 'TIME')
                    # Fall back to contains-search only if no exact match exists.
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
                logger.debug("fdr.DataReader failed for ticker=%s in fetch_single_stock", ticker, exc_info=True)

            if usd_price == 0:
                return None, f"Could not fetch price for '{ticker}'"

            name = ticker
            usd_marcap = 0
            inf: dict = {}  # pre-init so PER extraction below never hits NameError

            # 1. Try yfinance with custom session to bypass basic rate limits
            try:
                import yfinance as yf
                yft = yf.Ticker(yf_symbol, session=_YF_SESSION)
                inf = yft.info
                usd_marcap = inf.get('marketCap') or inf.get('totalAssets') or 0
                name = inf.get('longName') or inf.get('shortName') or ticker
            except Exception:
                logger.debug("yfinance info failed for %s, falling back to yahooquery", yf_symbol, exc_info=True)

            if usd_marcap == 0:
                # 2. Fallback to yahooquery
                try:
                    yq = YQTicker(yf_symbol)
                    detail = yq.summary_detail.get(yf_symbol, {})
                    qt = yq.quote_type.get(yf_symbol, {})
                    if isinstance(detail, dict):
                        usd_marcap = detail.get('marketCap') or detail.get('totalAssets', 0) or 0
                    if isinstance(qt, dict):
                        name = qt.get('longName') or qt.get('shortName') or ticker
                except Exception:
                    logger.debug("yahooquery summary_detail failed for %s", yf_symbol, exc_info=True)

            # Pass the already-converted polars df (roadmap 3-3): _to_polars() is a
            # no-op on a polars input, so this avoids re-converting the same pandas
            # DataFrame a second time inside fetch_historical_changes().
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


_JP10Y_CACHE: dict = {"df": None}


def _get_jp10y_df():
    """Session-cached; re-fetched once the cached data predates today on a
    weekday, so a stale in-process snapshot doesn't linger for the rest of
    the session after a fresh rate is actually published."""
    cached = _JP10Y_CACHE["df"]
    if cached is not None and not _pdf_is_stale(cached):
        return cached
    try:
        df1 = pd.read_csv(
            'https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate/historical/jgbcme_all.csv',
            skiprows=1, encoding='shift_jis')
        df2 = pd.read_csv(
            'https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate/jgbcme.csv',
            skiprows=1, encoding='shift_jis')
        df = pd.concat([df1, df2], ignore_index=True)
        df.rename(columns={'Date': 'Date', '10Y': 'Close'}, inplace=True)
        df['Date'] = pd.to_datetime(df['Date'], errors='coerce')
        df['Close'] = pd.to_numeric(df['Close'], errors='coerce')
        df = (df[['Date', 'Close']]
              .dropna(subset=['Date', 'Close'])
              .sort_values('Date')
              .set_index('Date'))
        _JP10Y_CACHE["df"] = df
        return df
    except Exception as e:
        logger.error("JP10Y fetch failed", exc_info=True)
        return cached


_KR3Y_CACHE: dict = {"df": None}


def _get_kr3y_df():
    """Fetch KR 3-year bond yield from Naver.

    Fetches pages until we have >= 365 days of data (max 20 pages = ~2 years).
    Session-cached; re-fetched once the cached data predates today on a
    weekday (same rationale as _get_jp10y_df).
    """
    cached = _KR3Y_CACHE["df"]
    if cached is not None and not _pdf_is_stale(cached):
        return cached
    try:
        url_base = 'https://finance.naver.com/marketindex/interestDailyQuote.naver?marketindexCd=IRR_GOVT03Y&page='

        def fetch_page(p):
            res = requests.get(url_base + str(p), timeout=5)
            df = pd.read_html(io.StringIO(res.text))[0]
            return df.dropna(subset=[df.columns[1]])

        # Fetch up to 20 pages in parallel (covers ~2 years; old code fetched 40)
        with ThreadPoolExecutor(max_workers=10) as exe:
            dfs = list(exe.map(fetch_page, range(1, 21)))

        hist = pd.concat(dfs, ignore_index=True)
        hist.rename(columns={hist.columns[0]: 'Date', hist.columns[1]: 'Close'}, inplace=True)
        hist['Date'] = pd.to_datetime(hist['Date'].astype(str).str.replace('.', '-'))
        hist['Close'] = pd.to_numeric(hist['Close'], errors='coerce')
        hist = (hist.dropna(subset=['Date', 'Close'])
                    .sort_values('Date')
                    .set_index('Date'))
        _KR3Y_CACHE["df"] = hist
        return hist
    except Exception as e:
        logger.error("KR3Y fetch failed", exc_info=True)
        return cached


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

        # df is already polars (roadmap 3-3): pass it directly so
        # fetch_historical_changes's _to_polars() call is a no-op instead of
        # re-converting the same pandas source (or re-fetching from
        # get_historical_data's cache) a second time.
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


def run_bulk_backtest_chunk(tickers, market, days=1095, target_year=None):
    """Bulk-fetches history via yahooquery and runs the strategy on each ticker."""
    def get_yf_symbol(t):
        if market == "KOSPI":
            return str(t).zfill(6) + ".KS"
        elif market == "KOSDAQ":
            return str(t).zfill(6) + ".KQ"
        return str(t).replace(".", "-")

    yf_symbols = [get_yf_symbol(t) for t in tickers]

    if target_year is not None:
        start_date = f"{target_year - 1}-09-01"
        end_date   = f"{target_year}-12-31"
    else:
        start_date = (datetime.now() - timedelta(days=days + 150)).strftime("%Y-%m-%d")
        end_date   = None

    try:
        yq = YQTicker(yf_symbols, asynchronous=False)
        bulk_history = yq.history(start=start_date, end=end_date) if end_date else yq.history(start=start_date)
    except Exception as e:
        logger.warning("Yahooquery backtest bulk history fetch failed", exc_info=True)
        bulk_history = None

    results = []
    if isinstance(bulk_history, pd.DataFrame) and not bulk_history.empty:
        level_vals = bulk_history.index.get_level_values('symbol')
        for t in tickers:
            df = None
            target_yf = get_yf_symbol(t)
            try:
                if target_yf in level_vals:
                    df_pd = bulk_history.loc[target_yf].copy()
                    df_pd.rename(columns={'open': 'Open', 'high': 'High', 'low': 'Low',
                                          'close': 'Close', 'volume': 'Volume'}, inplace=True)
                    p_df = _to_polars(df_pd)
                    if p_df.height >= 60:
                        df = _compute_indicators(p_df)
            except Exception:
                logger.debug("Backtest bulk history slice failed for ticker=%s", t, exc_info=True)
            results.append(run_backtest_for_stock(t, market, days=days, target_year=target_year, df=df))
    else:
        for t in tickers:
            results.append(run_backtest_for_stock(t, market, days=days, target_year=target_year))

    del bulk_history
    gc.collect()
    return results


def run_backtest_strategy(df, buy_sell_points=False, target_year=None):
    """MA20/MA60 Golden Cross + RSI strategy (vectorised numpy).

    Returns:
        buy_sell_points=False: (total_trades, win_count, cumulative_return)
        buy_sell_points=True:  (total_trades, win_count, cumulative_return,
                                buy_dates, buy_prices, sell_dates, sell_prices, bt_buy_date_list)
    """
    if df is None or df.is_empty() or "MA20" not in df.columns or "MA60" not in df.columns:
        return (0, 0, 0.0, [], [], [], [], []) if buy_sell_points else (0, 0, 0.0)

    df_ma20  = df.get_column("MA20").to_numpy()
    df_ma60  = df.get_column("MA60").to_numpy()
    df_close = df.get_column("Close").to_numpy()
    df_open  = df.get_column("Open").to_numpy()
    df_dates = df.get_column("Date").to_numpy()

    prev_20 = np.roll(df_ma20, 1); prev_20[0] = np.nan
    prev_60 = np.roll(df_ma60, 1); prev_60[0] = np.nan

    # NEW STRATEGY: 
    # Buy when MA20 is 10% above MA60 (MA20 crosses above MA60 * 1.10)
    buy_signals = (prev_20 <= prev_60 * 1.10) & (df_ma20 > df_ma60 * 1.10)
    
    buy_idx  = np.where(buy_signals)[0]
    if target_year is not None:
        years   = (df_dates.astype("datetime64[Y]").astype(int) + 1970)
        buy_idx = buy_idx[years[buy_idx] == target_year]

    buy_dates, buy_prices   = [], []
    sell_dates, sell_prices = [], []
    bt_buy_date_list        = []
    total_trades = win_count = 0
    cumulative_return = 0.0

    if len(buy_idx) == 0:
        return (total_trades, win_count, cumulative_return,
                buy_dates, buy_prices, sell_dates, sell_prices, bt_buy_date_list) if buy_sell_points \
               else (total_trades, win_count, cumulative_return)

    n = len(df)
    # Condition 2/3 (MA disparity, dead cross) don't depend on the buy price,
    # so precompute once: for each index, the nearest index >= it (or `n` if
    # none) where either condition holds.
    sell_indep_bool = (df_ma20 >= df_ma60 * 1.30) | (df_ma20 < df_ma60)
    idx_if_true = np.where(sell_indep_bool, np.arange(n), n)
    next_indep_idx = np.minimum.accumulate(idx_if_true[::-1])[::-1]

    last_sell_idx = -1
    for b in buy_idx:
        if b <= last_sell_idx:
            continue

        b_price = df_close[b]
        b_date = df_dates[b]

        start = b + 1
        if start >= n:
            break

        # Condition 1 (profit >= +30%): first index >= start where the
        # running max of Close reaches the threshold is the first index
        # where Close itself reaches it (running max is non-decreasing).
        threshold = b_price * 1.30
        cummax_suffix = np.maximum.accumulate(df_close[start:])
        pos = np.searchsorted(cummax_suffix, threshold, side='left')
        idx1 = start + pos if pos < len(cummax_suffix) else n

        idx2 = next_indep_idx[start]
        s = idx1 if idx1 < idx2 else idx2

        if s >= n:
            break

        if s + 1 < n:
            exec_sell_idx = s + 1
            s_price = df_open[exec_sell_idx]
            s_date = df_dates[exec_sell_idx]
            
            trade_return = (s_price - b_price) / b_price * 100
            cumulative_return += trade_return
            total_trades += 1
            if trade_return > 0:
                win_count += 1
            if buy_sell_points:
                buy_dates.append(b_date);   buy_prices.append(b_price)
                bt_buy_date_list.append(b_date)
                sell_dates.append(s_date);  sell_prices.append(s_price)
            last_sell_idx = s

    return (total_trades, win_count, cumulative_return,
            buy_dates, buy_prices, sell_dates, sell_prices, bt_buy_date_list) if buy_sell_points \
           else (total_trades, win_count, cumulative_return)


def run_backtest_for_stock(ticker, market, days=1095, target_year=None, df=None):
    """Fetches Polars DataFrame and runs the backtesting strategy."""
    err = ""
    if df is None:
        df, err = fetch_stock_ma_multi(ticker, market, windows=(10, 20, 60), days=days, target_year=target_year)
    if err or df is None or df.is_empty():
        return {"ticker": ticker, "trades": [], "error": err or "No data"}

    res = run_backtest_strategy(df, buy_sell_points=True, target_year=target_year)
    b_dates, b_prices, s_dates, s_prices, bt_buy_dates = res[3], res[4], res[5], res[6], res[7]

    trade_list = []
    for i in range(len(s_dates)):
        buy_date   = bt_buy_dates[i]
        buy_price  = b_prices[i]
        sell_date  = s_dates[i]
        sell_price = s_prices[i]
        trade_return = (sell_price - buy_price) / buy_price * 100
        days_held = max(1, int(
            (np.datetime64(sell_date) - np.datetime64(buy_date)) / np.timedelta64(1, 'D')
        ))
        yr_held = days_held / 365.25
        ann_return = ((1 + trade_return / 100) ** (1 / yr_held) - 1) * 100 if yr_held > 0 else trade_return
        trade_list.append({
            "buy_date":   buy_date,
            "buy_price":  buy_price,
            "sell_date":  sell_date,
            "sell_price": sell_price,
            "return_pct": trade_return,
            "days_held":  days_held,
            "ann_return": ann_return,
        })

    return {"ticker": ticker, "trades": trade_list, "error": ""}


def _kiwoom_parse_signed_int(val: str) -> int:
    """Parses a signed integer string from Kiwoom API (may include +/- prefix)."""
    if not val or not val.strip():
        return 0
    try:
        cleaned = val.replace(",", "").strip()
        return int(cleaned)
    except ValueError:
        return 0


def _fetch_index_investor_trend(market: str, days: int = 60) -> list:
    """Fetch daily investor trend for KOSPI / KOSDAQ indices using Naver Mobile API."""
    url_price = f"https://m.stock.naver.com/api/index/{market}/price?pageSize={days}&page=1"
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        res = requests.get(url_price, headers=headers, timeout=5)
        res.raise_for_status()
        price_data = res.json()
    except Exception as e:
        logger.error("Error fetching %s investor trend dates", market, exc_info=True)
        return []

    results = []
    
    def _fetch_day(item):
        date_str = item.get("localTradedAt", "")
        if not date_str: return None
        bizdate = date_str.replace("-", "")
        close_price = int(float(item.get("closePrice", "0").replace(",", "")))
        
        url_trend = f"https://m.stock.naver.com/api/index/{market}/trend?bizdate={bizdate}"
        try:
            r = requests.get(url_trend, headers=headers, timeout=5)
            r.raise_for_status()
            t_data = r.json()
            
            def _parse_val(val_str):
                if not val_str: return 0
                return int(str(val_str).replace(',', '').replace('+', ''))
                
            foreign = _parse_val(t_data.get("foreignValue", "0"))
            inst = _parse_val(t_data.get("institutionalValue", "0"))
            retail = _parse_val(t_data.get("personalValue", "0"))
            
            return {
                "Date": date_str.replace("-", "."),
                "Close": close_price,
                "Foreigner": foreign,
                "Institution": inst,
                "Retail": retail,
            }
        except Exception:
            logger.debug("Investor trend day fetch failed for market=%s date=%s", market, date_str, exc_info=True)
            return None

    with ThreadPoolExecutor(max_workers=10) as exe:
        raw = list(exe.map(_fetch_day, price_data))
    results = [r for r in raw if r]

    results.sort(key=lambda x: x["Date"], reverse=True)
    return results


def fetch_investor_trend(ticker: str, days: int = 60) -> list:
    """Fetches investor net-purchase data via Kiwoom REST API (ka10059).

    Returns list of dicts:
        {'Date': 'YYYY.MM.DD', 'Close': int,
         'Foreigner': int, 'Institution': int, 'Retail': int}
    Sorted newest-first (same ordering as the old Naver scraper).
    Falls back to Naver scraping if Kiwoom token is unavailable.
    """
    if isinstance(ticker, str) and ticker.isdigit():
        ticker = ticker.zfill(6)
    elif isinstance(ticker, int):
        ticker = f"{ticker:06d}"

    if ticker in ("KS11", "KQ11", "KOSPI", "KOSDAQ", "^KS11", "^KQ11"):
        market_str = "KOSPI" if ticker in ("KS11", "KOSPI", "^KS11") else "KOSDAQ"
        return _fetch_index_investor_trend(market_str, days)

    try:
        token, appkey, appsecret = _get_kiwoom_token()
    except Exception as e:
        logger.warning("[fetch_investor_trend] Kiwoom token failed, falling back to Naver", exc_info=True)
        return _fetch_investor_trend_naver(ticker, days)

    url = "https://api.kiwoom.com/api/dostk/stkinfo"
    base_headers = {
        "content-type": "application/json;charset=UTF-8",
        "authorization": f"Bearer {token}",
        "appkey": appkey,
        "secretkey": appsecret,
        "api-id": "ka10059",
    }
    # stk_cd format expected by ka10059: plain 6-digit code (e.g. "009150")
    stk_cd = str(ticker).zfill(6)

    rows = []
    cont_yn = "N"
    next_key = ""
    today_str = datetime.now().strftime("%Y%m%d")

    while len(rows) < days:
        headers = {**base_headers}
        if cont_yn == "Y":
            headers["cont-yn"] = "Y"
            headers["next-key"] = next_key

        body = {
            "dt": today_str,
            "stk_cd": stk_cd,
            "amt_qty_tp": "2",   # 2: quantity (수량)
            "trde_tp": "0",      # 0: net buy (순매수)
            "unit_tp": "1",      # 1: single share (단주)
        }

        try:
            res = requests.post(url, headers=headers, json=body, timeout=8)
            res.raise_for_status()
            data = res.json()
        except Exception as e:
            logger.warning("[fetch_investor_trend] Kiwoom ka10059 request error", exc_info=True)
            break

        if str(data.get("return_code", -1)) != "0":
            logger.warning("[fetch_investor_trend] Kiwoom ka10059 error: %s", data.get('return_msg'))
            break

        items = data.get("stk_invsr_orgn", [])
        if not items:
            break

        for item in items:
            dt_raw = item.get("dt", "").strip()        # "20260414"
            if not dt_raw or len(dt_raw) < 8:
                continue
            date_str = f"{dt_raw[:4]}.{dt_raw[4:6]}.{dt_raw[6:8]}"

            close_str = item.get("cur_prc", "0").replace("+", "").replace("-", "").replace(",", "")
            try:
                close_price = abs(int(close_str))
            except ValueError:
                close_price = 0

            acc_qty_str = item.get("acc_trde_qty", "0").replace(",", "").strip()
            try:
                acc_qty = abs(int(acc_qty_str))
            except ValueError:
                acc_qty = 0

            if acc_qty == 0 and close_price == 0:
                continue  # skip trading-halt days

            foreigner = _kiwoom_parse_signed_int(item.get("frgnr_invsr", "0"))
            institution = _kiwoom_parse_signed_int(item.get("orgn", "0"))
            retail = _kiwoom_parse_signed_int(item.get("ind_invsr", "0"))
            invst_trust = _kiwoom_parse_signed_int(item.get("invst_trust", "0"))
            pe_fund = _kiwoom_parse_signed_int(
                item.get("pe_fund") or item.get("pvt_eqt_fund") or item.get("prvt_eqt_fund") or item.get("pef", "0")
            )

            rows.append({
                "Date": date_str,
                "Close": close_price,
                "Foreigner": foreigner,
                "Institution": institution,
                "Retail": retail,
                "InvestmentTrust": invst_trust,
                "PrivateEquity": pe_fund,
            })

            if len(rows) >= days:
                break

        # Handle Kiwoom pagination
        resp_headers = res.headers
        cont_yn = resp_headers.get("cont-yn", "N")
        next_key = resp_headers.get("next-key", "")
        if cont_yn != "Y":
            break

    if not rows:
        logger.warning("[fetch_investor_trend] Kiwoom returned no rows or failed, falling back to Naver")
        return _fetch_investor_trend_naver(ticker, days)

    return rows

def _fetch_investor_trend_naver(ticker: str, days: int = 60) -> list:
    """Fallback: scrape net-purchase data from Naver Finance frgn.naver page."""
    if isinstance(ticker, str) and ticker.isdigit():
        ticker = ticker.zfill(6)
    elif isinstance(ticker, int):
        ticker = f"{ticker:06d}"

    url_base = f"https://finance.naver.com/item/frgn.naver?code={ticker}&page="
    num_pages = (days // 20) + 2

    def _fetch_page(page):
        page_rows = []
        try:
            res = requests.get(url_base + str(page), headers={"User-Agent": "Mozilla/5.0"}, timeout=5)
            res.encoding = "euc-kr"
            soup = BeautifulSoup(res.text, "html.parser")
            tables = soup.select("table.type2")
            if len(tables) < 2:
                return page_rows
            for tr in tables[1].select("tr"):
                tds = tr.select("td")
                if len(tds) >= 9:
                    date = tds[0].text.strip()
                    if not date:
                        continue
                    try:
                        vol = int(tds[4].text.strip().replace(",", ""))
                        if vol == 0:
                            continue
                        inst = int(tds[5].text.strip().replace(",", ""))
                        foreign = int(tds[6].text.strip().replace(",", ""))
                        close_price = int(tds[1].text.strip().replace(",", ""))
                        retail = -(inst + foreign)
                        page_rows.append({
                            "Date": date,
                            "Close": close_price,
                            "Foreigner": foreign,
                            "Institution": inst,
                            "Retail": retail,
                            "InvestmentTrust": 0,
                            "PrivateEquity": 0,
                        })
                    except ValueError:
                        pass
        except Exception as e:
            logger.warning("[_fetch_investor_trend_naver] Error for ticker=%s page=%d", ticker, page, exc_info=True)
        return page_rows

    rows = []
    with ThreadPoolExecutor(max_workers=min(num_pages, 8)) as exe:
        for page_rows in exe.map(_fetch_page, range(1, num_pages + 1)):
            rows.extend(page_rows)
            if len(rows) >= days:
                break

    return rows[:days]






def fetch_quarterly_financials(ticker: str, market: str):
    """Fetches the last 12 quarters (3 years) of income statement.
    Korean stocks: Uses Naver Finance Mobile API (since Kiwoom doesn't supply historical quarterly statements via REST).
    US stocks: Uses YahooQuery.
    """
    try:
        if market in ("KOSPI", "KOSDAQ"):
            t_str = ticker.zfill(6)
            url = f'https://m.stock.naver.com/api/stock/{t_str}/finance/quarter'
            r = requests.get(url, timeout=5)
            if r.status_code != 200: return []
            data = r.json()
            if 'financeInfo' not in data or not data['financeInfo']: return []
            
            info = data['financeInfo']
            keys = []
            periods = []
            for p in info.get('trTitleList', []):
                keys.append(p['key'])
                title = p.get('title', '').replace('.', '-')
                if title.endswith('-'): title = title[:-1]
                if len(title) == 7: # YYYY-MM
                    title += '-31' # roughly end of month
                periods.append(title)
                
            rev_dict, op_dict, net_dict = {}, {}, {}
            for row in info.get('rowList', []):
                if row.get('title') == '매출액': rev_dict = row.get('columns', {})       # '매출액' = Revenue
                elif row.get('title') == '영업이익': op_dict = row.get('columns', {})      # '영업이익' = Operating Income
                elif row.get('title') == '당기순이익': net_dict = row.get('columns', {})   # '당기순이익' = Net Income
                    
            rows = []
            for i, k in enumerate(keys):
                def parse_val(d_col):
                    if k not in d_col: return 0.0
                    v_str = d_col[k].get('value')
                    if not v_str or v_str.strip() == '-': return 0.0
                    return float(v_str.replace(',', '')) * 100000000
                    
                rev = parse_val(rev_dict)
                op = parse_val(op_dict)
                net = parse_val(net_dict)
                
                op_margin = (op / rev * 100) if rev else 0.0
                net_margin = (net / rev * 100) if rev else 0.0
                
                # Naver has Consensus (Expected) which isn't always real, but useful.
                # Just add them chronologically.
                rows.append({
                    'Date': periods[i],
                    'TotalRevenue': rev,
                    'OperatingIncome': op,
                    'NetIncome': net,
                    'OpMargin': op_margin,
                    'NetMargin': net_margin
                })
            
            # Sort newest first
            rows.sort(key=lambda x: x['Date'], reverse=True)
            return rows[:12]
            
        else:
            from yahooquery import Ticker
            t_str = ticker.replace(".", "-")
            yq_ticker = Ticker(t_str)
            df = yq_ticker.income_statement("q")
            
            if isinstance(df, dict) or df is None or getattr(df, 'empty', True):
                return []
                
            if 'periodType' in df.columns:
                df = df[df['periodType'] == '3M']
                
            if 'asOfDate' in df.columns:
                df = df.sort_values(by='asOfDate', ascending=False).head(12)
                
            rows = []
            for idx, row in df.iterrows():
                d_val = row.get('asOfDate', '')
                if hasattr(d_val, 'strftime'):
                    d_val = d_val.strftime("%Y-%m-%d")
                else:
                    d_val = str(d_val)[:10]
                
                def get_val(key):
                    v = row.get(key, 0)
                    if pd.isna(v): return 0.0
                    return float(v)

                rev = get_val('TotalRevenue')
                if rev == 0.0: rev = get_val('OperatingRevenue')
                op = get_val('OperatingIncome')
                net = get_val('NetIncome')
                if net == 0.0: net = get_val('NetIncomeCommonStockholders')
                
                op_margin = (op / rev * 100) if rev else 0.0
                net_margin = (net / rev * 100) if rev else 0.0
                
                rows.append({
                    'Date': str(d_val),
                    'TotalRevenue': rev,
                    'OperatingIncome': op,
                    'NetIncome': net,
                    'OpMargin': op_margin,
                    'NetMargin': net_margin
                })
                
            return rows
    except Exception as e:
        logger.error("Quarterly financials fetch failed for ticker=%s", ticker, exc_info=True)
        return []
