"""data/cache.py — In-memory caching, sessions, and timing helpers."""
import datetime as _dt
from datetime import datetime, timedelta, date as _date
from collections import OrderedDict
import threading
import logging
import urllib3
import requests
from requests.adapters import HTTPAdapter
import polars as pl
import pandas as pd

logger = logging.getLogger(__name__)

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
    "KS11", "KQ11",                          # Korean indices without caret
})

# Module-level USD/KRW rate cache (refreshed once per process run)
_USD_KRW_CACHE: dict = {"rate": None, "df": None}
_INDEX_CLOSE_CACHE: dict = {}
_JP10Y_CACHE: dict = {"df": None}
_KR3Y_CACHE: dict = {"df": None}
_VKOSPI_CACHE: dict = {}

# Shared history cache — only non-empty DataFrames are stored,
# so transient fetch failures (e.g. during parallel startup) are retried.
# Capped at _HIST_CACHE_MAX entries; least-recently-used entries are evicted first.
_HIST_CACHE: "OrderedDict[tuple, pl.DataFrame]" = OrderedDict()
_HIST_CACHE_LOCK = threading.Lock()
_HIST_CACHE_MAX = 1000  # Maximum number of tickers cached in memory

# ── Cache efficiency monitoring (see get_historical_data / _log_hist_cache_stats) ──
_HIST_CACHE_HITS = 0
_HIST_CACHE_MISSES = 0
_HIST_CACHE_LOG_INTERVAL = 100  # log the hit/miss ratio every N lookups


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
_YF_SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
})
_YF_CRUMB = None
_YF_CRUMB_LOCK = threading.Lock()

# Shared session for all Naver Finance endpoints — reuses TCP/TLS connections
# across the many per-ticker requests fired by the collectors in
# data/collectors/naver.py (real-time price batches, PER lookups, listing
# pages, investor-trend pages), instead of each call opening a fresh connection.
_NAVER_SESSION = requests.Session()
_NAVER_SESSION.mount("https://", HTTPAdapter(pool_maxsize=100))
_NAVER_SESSION.headers.update({"User-Agent": "Mozilla/5.0"})

# Shared session for the Kiwoom Securities REST API (data/collectors/kiwoom.py).
_KIWOOM_SESSION = requests.Session()


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


def _log_hist_cache_stats() -> None:
    """Log _HIST_CACHE's cumulative hit/miss ratio at INFO level every
    _HIST_CACHE_LOG_INTERVAL lookups, so cache effectiveness is visible in app.log
    without adding per-call noise."""
    try:
        import data_fetcher as _df_mod
        hits = getattr(_df_mod, "_HIST_CACHE_HITS", _HIST_CACHE_HITS)
        misses = getattr(_df_mod, "_HIST_CACHE_MISSES", _HIST_CACHE_MISSES)
        interval = getattr(_df_mod, "_HIST_CACHE_LOG_INTERVAL", _HIST_CACHE_LOG_INTERVAL)
        log_obj = getattr(_df_mod, "logger", logger)
        max_size = getattr(_df_mod, "_HIST_CACHE_MAX", _HIST_CACHE_MAX)
    except Exception:
        hits = _HIST_CACHE_HITS
        misses = _HIST_CACHE_MISSES
        interval = _HIST_CACHE_LOG_INTERVAL
        log_obj = logger
        max_size = _HIST_CACHE_MAX

    total = hits + misses
    if total and total % interval == 0:
        hit_rate = hits / total * 100
        log_obj.info(
            "_HIST_CACHE stats: %d hits / %d misses (%.1f%% hit rate), size=%d/%d",
            hits, misses, hit_rate, len(_HIST_CACHE), max_size,
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


def safe_float(value, default=0.0):
    """Safely converts a value to float. Returns default if conversion fails or value is None/NaN."""
    if value is None:
        return default
    if isinstance(value, (int, float)):
        import math
        return default if math.isnan(value) else float(value)
    try:
        val_str = str(value).replace(',', '').strip()
        f = float(val_str)
        import math
        return default if math.isnan(f) else f
    except (ValueError, TypeError):
        return default
