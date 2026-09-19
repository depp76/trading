"""Shared OHLCV frame builder for the trend-following tests (was copied into
each test module as a private `_frame`)."""
from datetime import date, timedelta

import polars as pl


def make_frame(closes, highs=None, lows=None, start=date(2025, 1, 1), skip=()):
    """Daily OHLCV polars frame from a list of closes.

    High/Low default to close +/- 1; `skip` drops the given row indices so a
    sleeve can have missing days (portfolio alignment tests).
    """
    closes = [float(c) for c in closes]
    highs = list(highs) if highs is not None else [c + 1.0 for c in closes]
    lows = list(lows) if lows is not None else [c - 1.0 for c in closes]
    rows = [
        (start + timedelta(days=i), c, h, lo)
        for i, (c, h, lo) in enumerate(zip(closes, highs, lows))
        if i not in skip
    ]
    return pl.DataFrame({
        "Date": [r[0] for r in rows],
        "Open": [r[1] for r in rows],
        "High": [r[2] for r in rows],
        "Low": [r[3] for r in rows],
        "Close": [r[1] for r in rows],
        "Volume": [1.0] * len(rows),
    })
