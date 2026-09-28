"""Synthetic datasets for the Trend Following tests: deterministic price
paths with known breakout / exit behaviour, no network."""
from datetime import date, timedelta

import numpy as np
import polars as pl

from strategy.trend_following.dataset import build_dataset

TOP = 400   # session index where SPIKE / DRIFT peak


def business_days(start: date, n: int) -> list[date]:
    out = []
    d = start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def path(start_price: float, steps: list[tuple[int, float]]) -> np.ndarray:
    """Close path: `steps` is a list of (n_days, daily_return)."""
    closes = [start_price]
    for n, r in steps:
        for _ in range(n):
            closes.append(closes[-1] * (1.0 + r))
    return np.array(closes[1:])


def make_frame(dates: list[date], closes, volume=2e6, spread: float = 0.002,
               volume_spikes: dict[int, float] | None = None) -> pl.DataFrame:
    closes = np.asarray(closes, dtype=float)
    n = min(len(dates), len(closes))
    closes = closes[:n]
    dts = dates[:n]
    opens = np.concatenate([[closes[0]], closes[:-1]])
    highs = np.maximum(closes, opens) * (1.0 + spread)
    lows = np.minimum(closes, opens) * (1.0 - spread)
    vols = np.full(n, float(volume))
    for i, k in (volume_spikes or {}).items():
        if 0 <= i < n:
            vols[i] *= k
    return pl.DataFrame({"Date": dts, "Open": opens, "High": highs, "Low": lows, "Close": closes, "Volume": vols})


def flow_frame(dates: list[date], closes, fi_sign: list[tuple[int, float]], retail=None) -> pl.DataFrame:
    """Investor-flow rows: `fi_sign` segments of (n_days, foreign+institution net shares per day)."""
    closes = np.asarray(closes, dtype=float)
    n = min(len(dates), len(closes))
    fi = np.zeros(n)
    i = 0
    for cnt, v in fi_sign:
        fi[i:i + cnt] = v
        i += cnt
    if i < n:
        fi[i:] = fi_sign[-1][1] if fi_sign else 0.0
    fo = fi / 2.0
    inst = fi - fo
    ret = -fi if retail is None else np.full(n, float(retail))
    return pl.DataFrame({"Date": dates[:n], "Close": closes[:n], "Foreigner": fo, "Institution": inst, "Retail": ret})


def synthetic_dataset(index_up: bool = True, with_flows: bool = False, n_days: int = 780,
                      first_day: date = date(2020, 1, 2), start: date = date(2021, 1, 4),
                      with_bm: bool = True, rf: float = 0.02):
    """Calendar: `n_days` sessions from 2020-01-02 (~262 warm-up sessions before the
    2021-01-04 evaluation start). Names (TOP = session 400, mid-2021):
      UP     rises 0.3%/day: breakout + trend every session, never exits (year-end trims only)
      FLAT   constant: never a candidate
      SPIKE  +1%/day until TOP then -2%/day: Donchian-10 exit on the second down session
      DRIFT  +1%/day until TOP then -0.2%/day with a 0.5% intraday range: no channel/stop exit
             within the window; flows turn negative at TOP -> the FX exit is the first to fire
    Volume spikes x3 on UP every 7th session and on DRIFT every 5th (V filter).
    """
    dates = business_days(first_day, n_days)
    n = len(dates)
    idx_ret = 0.0005 if index_up else -0.001
    index_close = 2000.0 * np.cumprod(np.full(n, 1.0 + idx_ret))

    up = path(10_000.0, [(n, 0.003)])
    flat = np.full(n, 20_000.0)
    spike = path(15_000.0, [(TOP, 0.01), (n - TOP, -0.02)])
    drift = path(15_000.0, [(TOP, 0.01), (n - TOP, -0.002)])
    frames = {
        "UP": make_frame(dates, up, volume_spikes={i: 3.0 for i in range(0, n, 7)}),
        "FLAT": make_frame(dates, flat),
        "SPIKE": make_frame(dates, spike),
        "DRIFT": make_frame(dates, drift, spread=0.005, volume_spikes={i: 3.0 for i in range(0, n, 5)}),
    }
    markets = {k: "KOSPI" for k in frames}
    names = {k: k for k in frames}

    flow_frames = None
    if with_flows:
        flow_frames = {
            "UP": flow_frame(dates, up, [(n, 500.0)]),
            "FLAT": flow_frame(dates, flat, [(n, 0.0)]),
            "SPIKE": flow_frame(dates, spike, [(TOP, 500.0), (n - TOP, -500.0)]),
            "DRIFT": flow_frame(dates, drift, [(TOP, 500.0), (n - TOP, -500.0)]),
        }

    bm_frame = make_frame(dates, path(30_000.0, [(n, 0.0006)])) if with_bm else None
    rf_frame = pl.DataFrame({"Date": dates, "Rate": np.full(n, rf * 100.0)})
    return build_dataset(dates, index_close, frames, start, markets, names,
                         bm_frame=bm_frame, bm_ticker="ETF_TR", bm_is_total_return=True,
                         flow_frames=flow_frames, rf_frame=rf_frame, rf_source="test")
