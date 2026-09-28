"""data/flows.py — Daily per-stock investor net-purchase history (foreigner /
institution / retail) with a per-ticker JSON cache under cache/investor_flows/.

Source: the Naver Finance frgn page scraped by
``data.collectors.naver._fetch_investor_trend_naver`` (20 rows per page, back
years). Quantities are net shares; the Trend Following dataset multiplies them
by the same row's close to get a KRW amount. Investor data is final only after
the close, so anything built from day t is acted on at t+1 (spec 5) -- the
caller's job, this module just serves the rows.

Sits at the collectors' level (imports only ``data.collectors.naver`` and
``paths``); ``data.market`` must not be imported here.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import date, datetime, timedelta

import polars as pl

from paths import FLOWS_CACHE_DIR
from data.collectors.naver import _fetch_investor_trend_naver

logger = logging.getLogger(__name__)

_FLOW_COLUMNS = ["Date", "Close", "Foreigner", "Institution", "Retail"]
_CACHE_LOCK = threading.Lock()
# Serialise Naver page bursts across dataset threads: one ticker's fetch is
# already 8 concurrent pages.
_FETCH_SEMAPHORE = threading.BoundedSemaphore(2)


def _cache_path(ticker: str) -> str:
    return os.path.join(FLOWS_CACHE_DIR, f"{ticker}.json")


def _parse_date(s) -> date | None:
    s = str(s or "").strip().replace(".", "-")
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _load_cache(ticker: str) -> dict:
    path = _cache_path(ticker)
    if not os.path.exists(path):
        return {"covers_from": None, "rows": {}}
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        rows = {r["Date"]: r for r in raw.get("rows", []) if r.get("Date")}
        return {"covers_from": raw.get("covers_from"), "rows": rows}
    except Exception:
        logger.debug("investor flow cache unreadable for %s, refetching", ticker, exc_info=True)
        return {"covers_from": None, "rows": {}}


def _save_cache(ticker: str, cache: dict) -> None:
    try:
        os.makedirs(FLOWS_CACHE_DIR, exist_ok=True)
        path = _cache_path(ticker)
        tmp = path + ".tmp"
        payload = {
            "covers_from": cache.get("covers_from"),
            "saved_at": datetime.now().isoformat(timespec="seconds"),
            "rows": [cache["rows"][k] for k in sorted(cache["rows"])],
        }
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        logger.warning("investor flow cache save failed for %s", ticker, exc_info=True)


def _business_days_between(a: date, b: date) -> int:
    if b <= a:
        return 0
    days = 0
    d = a
    while d < b:
        d += timedelta(days=1)
        if d.weekday() < 5:
            days += 1
    return days


def _last_expected_session(today: date) -> date:
    """Latest date a completed row can exist for: yesterday on weekdays, the
    previous Friday over the weekend (today's row is only final after the close)."""
    d = today - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def _normalise_rows(raw_rows: list) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for r in raw_rows or []:
        d = _parse_date(r.get("Date"))
        if d is None:
            continue
        try:
            out[d.isoformat()] = {
                "Date": d.isoformat(),
                "Close": float(r.get("Close", 0) or 0),
                "Foreigner": float(r.get("Foreigner", 0) or 0),
                "Institution": float(r.get("Institution", 0) or 0),
                "Retail": float(r.get("Retail", 0) or 0),
            }
        except (TypeError, ValueError):
            continue
    return out


def _rows_to_frame(rows: dict[str, dict], start: date) -> pl.DataFrame:
    recs = [r for k, r in sorted(rows.items()) if _parse_date(k) >= start]
    if not recs:
        return pl.DataFrame(schema={"Date": pl.Date, "Close": pl.Float64, "Foreigner": pl.Float64,
                                    "Institution": pl.Float64, "Retail": pl.Float64})
    return pl.DataFrame({
        "Date": [_parse_date(r["Date"]) for r in recs],
        "Close": [r["Close"] for r in recs],
        "Foreigner": [r["Foreigner"] for r in recs],
        "Institution": [r["Institution"] for r in recs],
        "Retail": [r["Retail"] for r in recs],
    })


def get_investor_flows(ticker: str, start: str, today: date | None = None) -> pl.DataFrame:
    """Daily rows (Date, Close, Foreigner, Institution, Retail) from `start`
    (YYYY-MM-DD) to the last completed session, served from the cache when it
    already covers that range and fetched (and cached) otherwise."""
    ticker = str(ticker).zfill(6)
    start_d = _parse_date(start) or date(2000, 1, 1)
    today = today or date.today()
    last_needed = _last_expected_session(today)

    with _CACHE_LOCK:
        cache = _load_cache(ticker)
    rows = cache["rows"]
    covers_from = _parse_date(cache.get("covers_from")) if cache.get("covers_from") else None
    have_dates = sorted(rows)
    newest = _parse_date(have_dates[-1]) if have_dates else None

    need_history = covers_from is None or covers_from > start_d
    need_recent = newest is None or newest < last_needed
    if need_history or need_recent:
        if need_history or newest is None:
            days = _business_days_between(start_d, today) + 10
        else:
            days = _business_days_between(newest, today) + 5
        try:
            with _FETCH_SEMAPHORE:
                fetched = _fetch_investor_trend_naver(ticker, days=days)
        except Exception:
            logger.warning("investor flow fetch failed for %s", ticker, exc_info=True)
            fetched = []
        new_rows = _normalise_rows(fetched)
        if new_rows:
            rows.update(new_rows)
            if need_history or newest is None:
                cache["covers_from"] = start_d.isoformat()
            with _CACHE_LOCK:
                _save_cache(ticker, cache)
    return _rows_to_frame(rows, start_d)
