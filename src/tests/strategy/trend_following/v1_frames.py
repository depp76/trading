"""Frame builders for the KR Donchian portfolio (v1) tests: business-day calendars and
OHLCV frames whose opens/highs/lows/volumes can be pinned per row."""
from datetime import date, timedelta

import numpy as np
import polars as pl


def bdays(start: date, n: int) -> list:
    """`n` consecutive weekdays from `start` (no holiday calendar)."""
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def ohlcv(dates, closes, opens=None, highs=None, lows=None, volumes=None) -> pl.DataFrame:
    closes = [float(c) for c in closes]
    n = len(closes)
    opens = [float(o) for o in opens] if opens is not None else list(closes)
    highs = [float(h) for h in highs] if highs is not None else [max(o, c) * 1.005 for o, c in zip(opens, closes)]
    lows = [float(lo) for lo in lows] if lows is not None else [min(o, c) * 0.995 for o, c in zip(opens, closes)]
    volumes = [float(v) for v in volumes] if volumes is not None else [100000.0] * n
    return pl.DataFrame({
        "Date": pl.Series(list(dates)[:n], dtype=pl.Date), "Open": opens, "High": highs,
        "Low": lows, "Close": closes, "Volume": volumes,
    })


def random_walk(dates, seed, drift=0.0004, vol=0.02, p0=50000.0) -> pl.DataFrame:
    n = len(dates)
    r = np.random.default_rng(seed)
    c = [p0]
    for _ in range(n - 1):
        c.append(max(100.0, c[-1] * (1 + r.normal(drift, vol))))
    c = np.array(c)
    o = c * (1 + r.normal(0, 0.005, n))
    h = np.maximum(o, c) * (1 + np.abs(r.normal(0, 0.008, n)))
    lo = np.minimum(o, c) * (1 - np.abs(r.normal(0, 0.008, n)))
    v = r.integers(50000, 500000, n).astype(float)
    return ohlcv(dates, c, o, h, lo, v)
