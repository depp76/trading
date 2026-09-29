"""strategy/trend_following/scoring.py — 2-5 "trend + pullback" scoring mode
(trend_following.md v04, C-series): the five indicators, the gate, the
percentile scores, and the weekly Top/Bottom recommendation the Trading
Universe tab shows.

Layers, all pure except the loader at the bottom:
  compute_scores(book, params)      -> ScoreBook  (date x ticker arrays, 2-5 (1)-(3))
  weekly_recommendation(sb, book)   -> Recommendation (Top N / Bottom N over the last week)
  load_score_inputs(items)          -> PriceBook + KOSPI closes through data.history (network)
  run_universe_scoring(items)       -> the three above chained, for TrendScoreThread

Spec mapping (9-4 in the md lists the decisions this file makes beyond the
text): MA50Div / Range52 / MA20Div / R10 / R3 are computed from closes as in
2-5 (1); R3, R10 and MA20Div are divided by ATR20 / close when
``score_vol_normalize`` is on; every indicator is turned into a percentile
rank (0, 1] among the liquid names of that day; the gate is liquidity +
MA50Div > 0 + Range52 >= gate_range_min + not in the top (1 - gate_overheat_pct)
of normalised MA20Div; total = pct(MA50Div) + pct(Range52) - pct(R3) -
0.5 x pct(R10); the total's percentile is taken within the gated names only.

The Universe recommendation averages the daily total over the last
``score_week_sessions`` sessions ("지난 1주일"). Top N are the names that pass
the gate on the latest session, highest weekly average first (buy
candidates); Bottom N are the liquid names with the lowest weekly average
(weak trend that just bounced -- the profile the spec's sign convention
marks as the one to avoid or sell), gate not required.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd
import polars as pl

from data.cache import start_date
from data.history import get_historical_data
from strategy.trend_following.config import StrategyParams
from strategy.trend_following.dataset import PriceBook, _check_stop, _say, _to_date, align_frame
from strategy.trend_following.signals import atr, regime_state, rolling, shift

logger = logging.getLogger(__name__)

INDEX_TICKER = "KS11"          # KOSPI: the trading calendar and the L1 regime input
INDICATORS = ("ma50_div", "range52", "ma20_div", "r10", "r3")
DISPLAY_RETURN_WINDOW = 20     # the 20D change shown next to R3 / R10 (display only, not scored)


# ---------------------------------------------------------------------------
# 2-5 (1)-(3): indicators, gate, scores
# ---------------------------------------------------------------------------
def pct_rank_rows(values: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """Percentile rank in (0, 1] of each row's values among the columns where
    `mask` is True and the value is finite (average rank / count); NaN
    elsewhere. Rows are dates, columns are tickers."""
    v = np.where(mask & np.isfinite(values), values, np.nan)
    return pd.DataFrame(v).rank(axis=1, pct=True, method="average").to_numpy(dtype=float)


@dataclass
class ScoreBook:
    """date x ticker arrays of the 2-5 scoring mode. Raw indicators are the
    unnormalised values (what the tables show); `pct` holds the percentile of
    the value the score uses (normalised when score_vol_normalize is on)."""
    dates: list[date]
    tickers: list[str]
    ma50_div: np.ndarray
    range52: np.ndarray
    ma20_div: np.ndarray
    r10: np.ndarray
    r3: np.ndarray
    atr_pct: np.ndarray
    liquid: np.ndarray          # bool: 20-day average trading value >= floor
    overheated: np.ndarray      # bool: liquid and normalised MA20Div in the top (1 - gate_overheat_pct)
    gate: np.ndarray            # bool: liquid & MA50Div > 0 & Range52 >= min & not overheated
    trend_score: np.ndarray     # pct(MA50Div) + pct(Range52), 0..2
    timing_score: np.ndarray    # -pct(R3) - w x pct(R10), -(1+w)..0
    total: np.ndarray           # trend + timing (NaN unless liquid with full history)
    total_rank: np.ndarray      # percentile of total within the gated names, NaN elsewhere
    pct: dict[str, np.ndarray] = field(default_factory=dict)

    @property
    def T(self) -> int:
        return len(self.dates)

    @property
    def N(self) -> int:
        return len(self.tickers)


def compute_scores(book: PriceBook, params: StrategyParams | None = None) -> ScoreBook:
    """2-5 (1)-(3) on every session of `book`."""
    p = params or StrategyParams()
    close, high, low, vol = book.close, book.high, book.low, book.volume

    adv = rolling(close * vol, p.volume_window, "mean")
    with np.errstate(invalid="ignore"):
        liquid = adv >= p.min_avg_trading_value

    ma_l = rolling(close, p.score_ma_long, "mean")
    ma_s = rolling(close, p.score_ma_short, "mean")
    with np.errstate(divide="ignore", invalid="ignore"):
        ma50_div = close / ma_l - 1.0
        ma20_div = close / ma_s - 1.0
        r10 = close / shift(close, p.score_r_mid) - 1.0
        r3 = close / shift(close, p.score_r_short) - 1.0

        # 52-week range position. Like signals.high52_prox, 80% of the window
        # is enough history (a name listed ~10 months ago still gets a value).
        min_periods = max(20, int(p.score_range_window * 0.8))
        hi = rolling(high, p.score_range_window, "max", min_periods=min_periods)
        lo = rolling(low, p.score_range_window, "min", min_periods=min_periods)
        if p.score_range_mode == "high52_prox":
            range52 = close / hi
        else:
            range52 = np.where(hi > lo, (close - lo) / (hi - lo), np.nan)

        atr_pct = atr(high, low, close, p.atr_window) / close
        if p.score_vol_normalize:
            scale = np.where(atr_pct > 0, atr_pct, np.nan)
            ma20_n, r10_n, r3_n = ma20_div / scale, r10 / scale, r3 / scale
        else:
            ma20_n, r10_n, r3_n = ma20_div, r10, r3

    pct = {
        "ma50_div": pct_rank_rows(ma50_div, liquid),
        "range52": pct_rank_rows(range52, liquid),
        "ma20_div": pct_rank_rows(ma20_n, liquid),
        "r10": pct_rank_rows(r10_n, liquid),
        "r3": pct_rank_rows(r3_n, liquid),
    }

    with np.errstate(invalid="ignore"):
        overheated = liquid & (pct["ma20_div"] > p.gate_overheat_pct)
        gate = liquid & (ma50_div > 0) & (range52 >= p.gate_range_min) & ~overheated

    trend_score = pct["ma50_div"] + pct["range52"]
    timing_score = -pct["r3"] - p.score_r_mid_weight * pct["r10"]
    total = trend_score + timing_score
    total_rank = pct_rank_rows(total, gate)

    return ScoreBook(
        dates=list(book.dates), tickers=list(book.tickers),
        ma50_div=ma50_div, range52=range52, ma20_div=ma20_div, r10=r10, r3=r3, atr_pct=atr_pct,
        liquid=liquid, overheated=overheated, gate=gate,
        trend_score=trend_score, timing_score=timing_score, total=total, total_rank=total_rank, pct=pct,
    )


# ---------------------------------------------------------------------------
# Weekly Top / Bottom recommendation
# ---------------------------------------------------------------------------
@dataclass
class ScoredName:
    ticker: str
    name: str
    market: str
    close: float
    week_avg: float            # mean of the daily total over the scored sessions of the week
    latest_total: float        # total on the latest session
    latest_rank: float         # total percentile within gated names on the latest session (NaN if not gated)
    sessions: int              # sessions of the week the name was scored on
    liquid: bool
    gate: bool
    overheated: bool
    ma50_div: float
    range52: float
    ma20_div: float
    r10: float
    r3: float
    r20: float                 # 20-session close-to-close return (display only, spec has no R20)
    reasons: tuple[str, ...] = ()   # why the gate failed on the latest session ("" when it passed)


@dataclass
class Recommendation:
    as_of: date
    week_dates: list[date]
    top: list[ScoredName]
    bottom: list[ScoredName]
    n_universe: int
    n_liquid: int
    n_gated: int
    regime_on: bool | None     # L1 (2-1) on the latest session, None without index data
    params: StrategyParams
    info: dict = field(default_factory=dict)


def _gate_reasons(sb: ScoreBook, t: int, j: int, p: StrategyParams) -> tuple[str, ...]:
    if not sb.liquid[t, j]:
        return ("Illiquid",)
    out = []
    if not (sb.ma50_div[t, j] > 0):
        out.append("MA50Div<0" if np.isfinite(sb.ma50_div[t, j]) else "No MA50")
    if not (sb.range52[t, j] >= p.gate_range_min):
        out.append(f"Range52<{p.gate_range_min:.2f}" if np.isfinite(sb.range52[t, j]) else "No 52w range")
    if sb.overheated[t, j]:
        out.append("Overheated")
    return tuple(out)


def weekly_recommendation(sb: ScoreBook, book: PriceBook, params: StrategyParams | None = None,
                          n_top: int = 10, index_close: np.ndarray | None = None) -> Recommendation:
    """Top/Bottom `n_top` by the weekly average of the daily total score.

    The week is the last ``score_week_sessions`` sessions of the book; a name
    needs a score on the latest session and on at least
    ``score_week_min_sessions`` of them. Top: gated on the latest session,
    highest average first. Bottom: liquid on the latest session, lowest
    average first, never a Top name."""
    p = params or StrategyParams()
    if sb.T == 0:
        raise ValueError("No sessions to score")
    t_last = sb.T - 1
    week = max(1, int(p.score_week_sessions))
    t0 = max(0, sb.T - week)
    week_dates = list(sb.dates[t0:])
    min_sessions = max(1, min(int(p.score_week_min_sessions), len(week_dates)))

    window = sb.total[t0:, :]
    finite = np.isfinite(window)
    sessions = finite.sum(axis=0)
    sums = np.where(finite, window, 0.0).sum(axis=0)
    week_avg = np.where(sessions > 0, sums / np.maximum(sessions, 1), np.nan)
    eligible = np.isfinite(sb.total[t_last]) & (sessions >= min_sessions)

    def _ret(j: int, n: int) -> float:
        if t_last - n < 0:
            return float("nan")
        with np.errstate(divide="ignore", invalid="ignore"):
            return float(book.close[t_last, j] / book.close[t_last - n, j] - 1.0)

    def _scored(j: int) -> ScoredName:
        return ScoredName(
            ticker=sb.tickers[j], name=book.names.get(sb.tickers[j], sb.tickers[j]),
            market=book.market_of(sb.tickers[j]), close=float(book.close[t_last, j]),
            week_avg=float(week_avg[j]), latest_total=float(sb.total[t_last, j]),
            latest_rank=float(sb.total_rank[t_last, j]), sessions=int(sessions[j]),
            liquid=bool(sb.liquid[t_last, j]), gate=bool(sb.gate[t_last, j]),
            overheated=bool(sb.overheated[t_last, j]),
            ma50_div=float(sb.ma50_div[t_last, j]), range52=float(sb.range52[t_last, j]),
            ma20_div=float(sb.ma20_div[t_last, j]), r10=float(sb.r10[t_last, j]), r3=float(sb.r3[t_last, j]),
            r20=_ret(j, DISPLAY_RETURN_WINDOW),
            reasons=_gate_reasons(sb, t_last, j, p),
        )

    top_idx = [j for j in np.argsort(-week_avg, kind="stable") if eligible[j] and sb.gate[t_last, j]][:n_top]
    taken = set(top_idx)
    bottom_idx = [j for j in np.argsort(week_avg, kind="stable")
                  if eligible[j] and sb.liquid[t_last, j] and j not in taken][:n_top]

    regime_on = None
    if index_close is not None and len(index_close) == sb.T and np.isfinite(index_close).any():
        state, _ = regime_state(np.asarray(index_close, dtype=float), sb.dates, p)
        regime_on = bool(state[t_last])

    return Recommendation(
        as_of=sb.dates[t_last], week_dates=week_dates,
        top=[_scored(j) for j in top_idx], bottom=[_scored(j) for j in bottom_idx],
        n_universe=sb.N, n_liquid=int(sb.liquid[t_last].sum()), n_gated=int(sb.gate[t_last].sum()),
        regime_on=regime_on, params=p,
        info={"n_eligible": int(eligible.sum()), "week_sessions": len(week_dates), "min_sessions": min_sessions},
    )


# ---------------------------------------------------------------------------
# Network path (Universe rows -> PriceBook), and the whole chain
# ---------------------------------------------------------------------------
def load_score_inputs(items: list[dict], progress=None, should_stop=None, max_workers: int = 8,
                      start: str | None = None) -> tuple[PriceBook, np.ndarray | None]:
    """Daily OHLCV for the Universe rows `items` (dicts with ticker / name /
    market) on the KOSPI calendar, plus the aligned KOSPI closes (None when
    the index history is unavailable; the calendar is then the union of the
    stock dates). The lookback is data.cache.start_date() -- the same key the
    Universe refresh used for its own history fetch -- so right after a
    refresh every frame comes from _HIST_CACHE without a network call."""
    start = start or start_date()
    tickers = []
    names, markets = {}, {}
    for it in items:
        tk = str(it.get("ticker", "")).strip()
        if not tk or tk in names:
            continue
        tickers.append(tk)
        names[tk] = str(it.get("name", tk) or tk)
        markets[tk] = str(it.get("market", "") or "")
    if not tickers:
        raise ValueError("No tickers to score")

    _check_stop(should_stop)
    _say(progress, "Loading KOSPI index history...")
    try:
        idx_df = get_historical_data(INDEX_TICKER, start)
    except Exception:
        logger.warning("KOSPI index history failed; scoring on the union calendar", exc_info=True)
        idx_df = None

    _say(progress, f"Loading daily history for {len(tickers)} names...")
    frames: dict[str, pl.DataFrame] = {}
    done = 0
    with ThreadPoolExecutor(max_workers=max_workers) as exe:
        futures = {exe.submit(get_historical_data, tk, start): tk for tk in tickers}
        for fut in as_completed(futures):
            _check_stop(should_stop, futures)
            tk = futures[fut]
            done += 1
            try:
                df = fut.result()
            except Exception:
                logger.warning("history fetch failed for %s", tk, exc_info=True)
                df = None
            if df is not None and not df.is_empty() and "Close" in df.columns:
                frames[tk] = df
            if done % 25 == 0 or done == len(tickers):
                _say(progress, f"Daily history {done}/{len(tickers)} ({len(frames)} usable)")
    if not frames:
        raise RuntimeError("No usable stock histories were loaded")
    frames = {tk: frames[tk] for tk in tickers if tk in frames}   # keep the Universe order

    if idx_df is not None and not idx_df.is_empty() and "Date" in idx_df.columns:
        calendar = sorted({_to_date(d) for d in idx_df.get_column("Date").to_list()})
        index_close = align_frame(idx_df, calendar, ["Close"])["Close"]
    else:
        calendar = sorted({_to_date(d) for df in frames.values() for d in df.get_column("Date").to_list()})
        index_close = None
    book = PriceBook.from_frames(frames, calendar, markets, names)
    return book, index_close


def run_universe_scoring(items: list[dict], params: StrategyParams | None = None, n_top: int = 10,
                         progress=None, should_stop=None) -> Recommendation:
    """load_score_inputs -> compute_scores -> weekly_recommendation."""
    p = params or StrategyParams()
    book, index_close = load_score_inputs(items, progress=progress, should_stop=should_stop)
    _check_stop(should_stop)
    _say(progress, f"Scoring {book.N} names over {book.T} sessions...")
    sb = compute_scores(book, p)
    rec = weekly_recommendation(sb, book, p, n_top=n_top, index_close=index_close)
    rec.info["n_requested"] = len(items)
    rec.info["n_loaded"] = book.N
    return rec
