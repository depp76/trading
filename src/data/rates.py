"""data/rates.py — CD 91-day rate series (the Trend Following BM4 / Sharpe
risk-free rate, trend_following.md 6-3).

Sources, in order: the Bank of Korea ECOS API when ``ECOS_API_KEY`` is set in
.env (statistic 817Y002 "시장금리(일별)", item 010502000 = CD(91일); verify the
item code on ecos.bok.or.kr if the response is empty), else a hand-made
``cd91.csv`` at the repo root (columns Date, Rate in percent), else nothing --
the strategy then falls back to its constant ``risk_free_fallback``. Fetched
rows are cached in cache/cd91.json.
"""
from __future__ import annotations

import csv
import json
import logging
import os
from datetime import date, datetime

import polars as pl
import requests

from paths import CD91_CACHE_FILE, CD91_CSV_FILE

logger = logging.getLogger(__name__)

ECOS_STAT_CODE = "817Y002"
ECOS_CD91_ITEM = "010502000"
_ECOS_URL = "https://ecos.bok.or.kr/api/StatisticSearch/{key}/json/kr/1/100000/{stat}/D/{start}/{end}/{item}"


def _empty() -> pl.DataFrame:
    return pl.DataFrame(schema={"Date": pl.Date, "Rate": pl.Float64})


def _frame(rows: dict[str, float]) -> pl.DataFrame:
    if not rows:
        return _empty()
    keys = sorted(rows)
    return pl.DataFrame({"Date": [datetime.strptime(k, "%Y-%m-%d").date() for k in keys],
                         "Rate": [float(rows[k]) for k in keys]})


def _load_cache() -> dict[str, float]:
    if not os.path.exists(CD91_CACHE_FILE):
        return {}
    try:
        with open(CD91_CACHE_FILE, "r", encoding="utf-8") as f:
            return {k: float(v) for k, v in json.load(f).items()}
    except Exception:
        logger.debug("CD91 cache unreadable, ignoring", exc_info=True)
        return {}


def _save_cache(rows: dict[str, float]) -> None:
    try:
        os.makedirs(os.path.dirname(CD91_CACHE_FILE), exist_ok=True)
        tmp = CD91_CACHE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(rows, f)
        os.replace(tmp, CD91_CACHE_FILE)
    except Exception:
        logger.warning("CD91 cache save failed", exc_info=True)


def fetch_ecos_cd91(start: str, end: str, api_key: str, timeout: int = 15) -> dict[str, float]:
    """{YYYY-MM-DD: rate %} from ECOS for [start, end] (YYYY-MM-DD)."""
    url = _ECOS_URL.format(key=api_key, stat=ECOS_STAT_CODE, start=start.replace("-", ""),
                           end=end.replace("-", ""), item=ECOS_CD91_ITEM)
    res = requests.get(url, timeout=timeout)
    res.raise_for_status()
    payload = res.json()
    block = payload.get("StatisticSearch") or {}
    rows = block.get("row") or []
    out: dict[str, float] = {}
    for r in rows:
        t = str(r.get("TIME", "")).strip()
        v = r.get("DATA_VALUE")
        if len(t) != 8 or v in (None, ""):
            continue
        try:
            out[f"{t[:4]}-{t[4:6]}-{t[6:8]}"] = float(v)
        except (TypeError, ValueError):
            continue
    if not out and payload.get("RESULT"):
        logger.warning("ECOS CD91 query returned no rows: %s", payload.get("RESULT"))
    return out


def read_cd91_csv(path: str | None = None) -> dict[str, float]:
    """{YYYY-MM-DD: rate %} from the hand-made CSV (default: paths.CD91_CSV_FILE,
    resolved at call time so tests can point it elsewhere)."""
    path = path or CD91_CSV_FILE
    out: dict[str, float] = {}
    if not os.path.exists(path):
        return out
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            d = str(row.get("Date", "")).strip().replace(".", "-").replace("/", "-")[:10]
            try:
                datetime.strptime(d, "%Y-%m-%d")
                out[d] = float(str(row.get("Rate", "")).replace("%", "").strip())
            except (TypeError, ValueError):
                continue
    return out


def get_cd91_series(start: str, end: str | None = None) -> pl.DataFrame:
    """(Date, Rate %) rows covering [start, end]; empty when no source is available."""
    end = end or date.today().isoformat()
    cached = _load_cache()
    api_key = os.getenv("ECOS_API_KEY", "").strip()
    if api_key:
        have = sorted(k for k in cached if start <= k <= end)
        covered = bool(have) and have[0] <= start[:8] + "07" and have[-1] >= end[:8] + "01"
        if not covered:
            try:
                fetched = fetch_ecos_cd91(start, end, api_key)
                if fetched:
                    cached.update(fetched)
                    _save_cache(cached)
            except Exception:
                logger.warning("ECOS CD91 fetch failed", exc_info=True)
    rows = {k: v for k, v in cached.items() if start <= k <= end}
    if not rows:
        rows = {k: v for k, v in read_cd91_csv().items() if start <= k <= end}
    return _frame(rows)
