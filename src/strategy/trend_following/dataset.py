"""strategy/trend_following/dataset.py — Aligned date x ticker arrays for the
backtest (trend_following.md 5 "데이터와 look-ahead", 6-3 benchmark data).

``build_dataset`` is pure (frames in, arrays out) so tests can feed synthetic
histories; ``load_dataset`` is the network path that pulls everything through
the ``data`` layer: KOSPI index and per-stock OHLCV from ``data.history``, the
current KOSPI top-N listing from the Naver collector (with the FDR fallback),
investor flows from ``data.flows`` and the CD 91-day rate from ``data.rates``.

Look-ahead notes (spec 5): every signal built from a day's close/volume/flow is
only acted on at the next day's open by the engine; the universe is today's
constituents (survivorship bias -- flagged in ``Dataset.info``); the flow
amount is net quantity x that day's close from the same Naver page.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
import polars as pl

from data.history import get_historical_data
from data.collectors.naver import _fetch_kr_listing_naver
from data.listing import _fetch_kr_listing_fdr_fallback
from data.flows import get_investor_flows
from data.rates import get_cd91_series
from strategy.trend_following.config import (
    StrategyParams, BM_TR_TICKER, BM_PRICE_FALLBACK_TICKER,
)

logger = logging.getLogger(__name__)

# Calendar days of history loaded before the evaluation start so the widest
# windows (252-session 52-week high, BM3's MA200) are warm on day one.
WARMUP_CALENDAR_DAYS = 450
_OHLCV = ["Open", "High", "Low", "Close", "Volume"]


def _say(progress, msg: str) -> None:
    if progress is not None:
        try:
            progress(msg)
        except Exception:
            logger.debug("progress callback failed", exc_info=True)


def _to_date(d) -> date:
    if isinstance(d, datetime):
        return d.date()
    if isinstance(d, date):
        return d
    return datetime.strptime(str(d)[:10], "%Y-%m-%d").date()


def align_frame(df: pl.DataFrame | None, calendar: list[date], cols: list[str]) -> dict[str, np.ndarray]:
    """Reindex a polars frame with a Date column onto `calendar`; missing
    dates become NaN. Returns one float array per requested column."""
    if df is None or df.is_empty() or "Date" not in df.columns:
        return {c: np.full(len(calendar), np.nan) for c in cols}
    present = [c for c in cols if c in df.columns]
    pdf = df.select(["Date"] + present).to_pandas()
    pdf["Date"] = pd.to_datetime(pdf["Date"]).dt.date
    pdf = pdf.drop_duplicates("Date", keep="last").set_index("Date").reindex(calendar)
    out = {}
    for c in cols:
        if c in present:
            out[c] = pd.to_numeric(pdf[c], errors="coerce").to_numpy(dtype=float)
        else:
            out[c] = np.full(len(calendar), np.nan)
    return out


@dataclass
class PriceBook:
    """OHLCV for a set of tickers on one trading calendar (T x N float arrays,
    NaN where a name has no bar)."""
    dates: list[date]
    tickers: list[str]
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    volume: np.ndarray
    markets: dict[str, str] = field(default_factory=dict)
    names: dict[str, str] = field(default_factory=dict)

    def __post_init__(self):
        self._col = {t: i for i, t in enumerate(self.tickers)}

    @property
    def T(self) -> int:
        return len(self.dates)

    @property
    def N(self) -> int:
        return len(self.tickers)

    def col(self, ticker: str) -> int:
        return self._col[ticker]

    def market_of(self, ticker: str) -> str:
        return self.markets.get(ticker, "KOSPI")

    @classmethod
    def from_frames(cls, frames: dict[str, pl.DataFrame], calendar: list[date],
                    markets: dict[str, str] | None = None, names: dict[str, str] | None = None) -> "PriceBook":
        tickers = list(frames)
        T, N = len(calendar), len(tickers)
        arrays = {c: np.full((T, N), np.nan) for c in _OHLCV}
        for j, tk in enumerate(tickers):
            cols = align_frame(frames[tk], calendar, _OHLCV)
            for c in _OHLCV:
                arrays[c][:, j] = cols[c]
        return cls(
            dates=list(calendar), tickers=tickers,
            open=arrays["Open"], high=arrays["High"], low=arrays["Low"],
            close=arrays["Close"], volume=arrays["Volume"],
            markets=dict(markets or {}), names=dict(names or {}),
        )


@dataclass
class Dataset:
    """Everything one research run needs, on one calendar.

    ``start_idx`` is the first calendar index inside the evaluation window;
    rows before it are warm-up history for the rolling windows. ``rf_period``
    is the simple risk-free return earned over the calendar gap ending at each
    row (annual rate x gap days / 365; 0 on the first row)."""
    stocks: PriceBook
    index_close: np.ndarray
    bm: PriceBook | None
    bm_is_total_return: bool
    rf_annual: np.ndarray
    rf_period: np.ndarray
    rf_source: str
    flow_fi: np.ndarray | None
    flow_retail: np.ndarray | None
    start_idx: int
    info: dict = field(default_factory=dict)

    @property
    def dates(self) -> list[date]:
        return self.stocks.dates

    @property
    def eval_dates(self) -> list[date]:
        return self.stocks.dates[self.start_idx:]


def _rf_period_from_annual(dates: list[date], rf_annual: np.ndarray) -> np.ndarray:
    out = np.zeros(len(dates))
    for t in range(1, len(dates)):
        gap = (dates[t] - dates[t - 1]).days
        out[t] = rf_annual[t] * gap / 365.0
    return out


def build_dataset(calendar: list[date], index_close: np.ndarray, stock_frames: dict[str, pl.DataFrame],
                  start: date, markets: dict[str, str] | None = None, names: dict[str, str] | None = None,
                  bm_frame: pl.DataFrame | None = None, bm_ticker: str = BM_TR_TICKER,
                  bm_is_total_return: bool = True, flow_frames: dict[str, pl.DataFrame] | None = None,
                  rf_frame: pl.DataFrame | None = None, rf_fallback: float = 0.03,
                  rf_source: str = "", info: dict | None = None) -> Dataset:
    """Assemble a Dataset from already-fetched frames (no network)."""
    calendar = [_to_date(d) for d in calendar]
    stocks = PriceBook.from_frames(stock_frames, calendar, markets, names)
    index_close = np.asarray(index_close, dtype=float)

    bm = None
    if bm_frame is not None and not bm_frame.is_empty():
        bm = PriceBook.from_frames({bm_ticker: bm_frame}, calendar, {bm_ticker: "ETF"}, {bm_ticker: bm_ticker})

    flow_fi = flow_retail = None
    if flow_frames:
        T, N = stocks.T, stocks.N
        flow_fi = np.full((T, N), np.nan)
        flow_retail = np.full((T, N), np.nan)
        for tk, fdf in flow_frames.items():
            if tk not in stocks._col or fdf is None or fdf.is_empty():
                continue
            j = stocks.col(tk)
            cols = align_frame(fdf, calendar, ["Foreigner", "Institution", "Retail", "Close"])
            px = cols["Close"]
            # Amount proxy: net quantity x the same page's close (spec 2-3 normalises
            # by trading value, so both sides are in KRW).
            flow_fi[:, j] = (cols["Foreigner"] + cols["Institution"]) * px
            # With the Naver frgn source "Retail" is -(Foreigner + Institution)
            # (no retail column there), so flow_retail == -flow_fi and carries
            # no independent information; a four-party source would change that.
            flow_retail[:, j] = cols["Retail"] * px

    if rf_frame is not None and not rf_frame.is_empty() and "Rate" in rf_frame.columns:
        rate = align_frame(rf_frame, calendar, ["Rate"])["Rate"]
        rate = pd.Series(rate).ffill().bfill().to_numpy(dtype=float) / 100.0
        rate = np.where(np.isfinite(rate), rate, rf_fallback)
        rf_source = rf_source or "series"
    else:
        rate = np.full(len(calendar), float(rf_fallback))
        rf_source = rf_source or f"constant {rf_fallback:.2%}"
    rf_period = _rf_period_from_annual(calendar, rate)

    start = _to_date(start)
    start_idx = next((i for i, d in enumerate(calendar) if d >= start), len(calendar))
    if start_idx >= len(calendar):
        raise ValueError(f"No trading days on or after {start} in the loaded calendar")

    meta = {
        "n_tickers": stocks.N,
        "calendar_start": calendar[0].isoformat() if calendar else "",
        "eval_start": calendar[start_idx].isoformat(),
        "eval_end": calendar[-1].isoformat() if calendar else "",
        "bm_ticker": bm_ticker if bm is not None else "",
        "bm_is_total_return": bool(bm_is_total_return and bm is not None),
        "rf_source": rf_source,
        "has_flows": flow_fi is not None,
        "universe": "current KOSPI constituents by market cap (survivorship bias: no point-in-time list)",
    }
    meta.update(info or {})
    return Dataset(
        stocks=stocks, index_close=index_close, bm=bm, bm_is_total_return=bool(bm_is_total_return and bm is not None),
        rf_annual=rate, rf_period=rf_period, rf_source=rf_source,
        flow_fi=flow_fi, flow_retail=flow_retail, start_idx=start_idx, info=meta,
    )


def _load_listing(universe_size: int) -> list[dict]:
    try:
        rows = _fetch_kr_listing_naver("KOSPI", universe_size)
    except Exception:
        logger.warning("Naver KOSPI listing failed, trying FDR fallback", exc_info=True)
        rows = []
    if not rows:
        rows = _fetch_kr_listing_fdr_fallback("KOSPI", universe_size)
    return rows or []


class ResearchCancelled(RuntimeError):
    """Raised by load_dataset / run_research when the caller's `should_stop`
    callable returns True (the Stop button of the Trend Following tab)."""


def _check_stop(should_stop, futures=None) -> None:
    if should_stop is not None and should_stop():
        for f in futures or ():
            f.cancel()      # queued fetches never start; in-flight ones finish
        raise ResearchCancelled("cancelled")


def load_dataset(start, end=None, universe_size: int = 200, include_flows: bool = False,
                 params: StrategyParams | None = None, progress=None, max_workers: int = 8,
                 min_rows: int = 60, should_stop=None) -> Dataset:
    """Network path: KOSPI calendar + top-N KOSPI names + optional flows + BM
    ETF + CD91, warmed up WARMUP_CALENDAR_DAYS before `start`. `should_stop`
    (no-arg callable) is polled between fetches; True raises ResearchCancelled."""
    p = params or StrategyParams()
    start = _to_date(start)
    end = _to_date(end) if end else date.today()
    warm_start = start - timedelta(days=WARMUP_CALENDAR_DAYS)
    ws = warm_start.isoformat()

    _check_stop(should_stop)
    _say(progress, "Loading KOSPI index history...")
    idx_df = get_historical_data("KS11", ws)
    if idx_df is None or idx_df.is_empty():
        raise RuntimeError("KOSPI index history unavailable (data.history returned nothing)")
    idx_dates = [_to_date(d) for d in idx_df.get_column("Date").to_list()]
    calendar = [d for d in idx_dates if warm_start <= d <= end]
    if not calendar:
        raise RuntimeError(f"No KOSPI trading days between {warm_start} and {end}")
    index_close = align_frame(idx_df, calendar, ["Close"])["Close"]

    _say(progress, f"Loading KOSPI top {universe_size} listing...")
    listing = _load_listing(universe_size)
    if not listing:
        raise RuntimeError("KOSPI listing unavailable (Naver and FDR both failed)")
    codes = [str(r["Code"]).zfill(6) for r in listing]
    names = {str(r["Code"]).zfill(6): str(r.get("Name", "")) for r in listing}

    _check_stop(should_stop)
    _say(progress, f"Loading daily history for {len(codes)} names...")
    frames: dict[str, pl.DataFrame] = {}
    done = 0
    with ThreadPoolExecutor(max_workers=max_workers) as exe:
        futures = {exe.submit(get_historical_data, c, ws): c for c in codes}
        for fut in as_completed(futures):
            _check_stop(should_stop, futures)
            c = futures[fut]
            done += 1
            try:
                df = fut.result()
            except Exception:
                logger.warning("history fetch failed for %s", c, exc_info=True)
                df = None
            if df is not None and not df.is_empty() and df.height >= min_rows:
                frames[c] = df
            if done % 25 == 0 or done == len(codes):
                _say(progress, f"Daily history {done}/{len(codes)} ({len(frames)} usable)")
    if not frames:
        raise RuntimeError("No usable stock histories were loaded")
    frames = {c: frames[c] for c in codes if c in frames}   # keep market-cap order
    markets = {c: "KOSPI" for c in frames}

    flow_frames = None
    if include_flows:
        flow_frames = {}
        done = 0
        with ThreadPoolExecutor(max_workers=max(1, min(max_workers, 4))) as exe:
            futures = {exe.submit(get_investor_flows, c, ws): c for c in frames}
            for fut in as_completed(futures):
                _check_stop(should_stop, futures)
                c = futures[fut]
                done += 1
                try:
                    fdf = fut.result()
                except Exception:
                    logger.warning("investor flow fetch failed for %s", c, exc_info=True)
                    fdf = None
                if fdf is not None and not fdf.is_empty():
                    flow_frames[c] = fdf
                if done % 10 == 0 or done == len(frames):
                    _say(progress, f"Investor flows {done}/{len(frames)} ({len(flow_frames)} with data)")

    _check_stop(should_stop)
    _say(progress, "Loading benchmark ETF...")
    bm_ticker, bm_tr = BM_TR_TICKER, True
    bm_df = get_historical_data(bm_ticker, ws)
    if bm_df is None or bm_df.is_empty():
        bm_ticker, bm_tr = BM_PRICE_FALLBACK_TICKER, False
        bm_df = get_historical_data(bm_ticker, ws)

    _say(progress, "Loading CD 91-day rate...")
    rf_source = ""
    try:
        rf_df = get_cd91_series(ws, end.isoformat())
        if rf_df is not None and not rf_df.is_empty():
            rf_source = "CD91 (data.rates)"
    except Exception:
        logger.warning("CD91 rate load failed, using the constant fallback", exc_info=True)
        rf_df = None

    return build_dataset(
        calendar, index_close, frames, start, markets, names,
        bm_frame=bm_df, bm_ticker=bm_ticker, bm_is_total_return=bm_tr,
        flow_frames=flow_frames, rf_frame=rf_df, rf_fallback=p.risk_free_fallback, rf_source=rf_source,
        info={"universe_size_requested": universe_size, "flows_requested": include_flows},
    )
