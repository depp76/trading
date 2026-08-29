"""data/collectors/krx.py — KRX Open API (VKOSPI, derivative indices) and JP10Y bond data."""
import os
import json
import requests
from datetime import datetime, date as _date
from concurrent.futures import ThreadPoolExecutor, as_completed
import pandas as pd
import polars as pl
import logging

from data.cache import (
    _START_DATE,
    _JP10Y_CACHE,
    _pdf_is_stale,
    safe_float,
)

logger = logging.getLogger(__name__)

VKOSPI_INDEX_NAME = "코스피 200 변동성지수"
_KRX_DERIV_IDX_URL = "https://openapi.krx.co.kr/OPN/DER/01/0101/der_0101_tab1.jsp"
_KRX_KEY_CACHE: dict = {}
_KRX_VKOSPI_CACHE_PATH = "vkospi_cache.json"


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

    bas_dd: 'YYYYMMDD'.
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
    """Returns the VKOSPI closing value for a given date (default: today)."""
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
    except Exception:
        logger.warning("VKOSPI cache save error", exc_info=True)


def fetch_vkospi_history(start: str, end: str = None, max_workers: int = 8) -> pl.DataFrame:
    """Builds daily VKOSPI OHLC history for [start, end] (default end: today)."""
    end = end or datetime.now().strftime("%Y-%m-%d")
    bas_dds = [d.strftime("%Y%m%d") for d in pd.bdate_range(start, end)]

    cache = _load_vkospi_cache()
    today_bd = datetime.now().strftime("%Y%m%d")
    missing = [bd for bd in bas_dds if bd not in cache or (cache[bd] is None and bd == today_bd)]

    if missing:
        with ThreadPoolExecutor(max_workers=max_workers) as exe:
            futures = {exe.submit(fetch_krx_derivative_index_day, bd): bd for bd in missing}
            for fut in as_completed(futures):
                bd = futures[fut]
                try:
                    item = fut.result()
                except Exception:
                    logger.warning("KRX VKOSPI fetch error for date=%s", bd, exc_info=True)
                    continue
                if item:
                    cache[bd] = item
                elif bd != today_bd:
                    cache[bd] = None
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
    """pandas/DatetimeIndex wrapper matching the _get_kr3y_df / _get_jp10y_df convention."""
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


def _get_jp10y_df():
    """Fetch Japan 10-year government bond yield."""
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
    except Exception:
        logger.error("JP10Y fetch failed", exc_info=True)
        return cached
