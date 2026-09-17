"""strategy/trend_following/validation.py — In-sample / out-of-sample validation of the
Donchian strategy (trend_following.md 5, 6).

Two schemes, both built on the same idea: signals are always computed on the full
history (they only ever look backwards, so this is exactly what live trading sees),
and only the *evaluation* is restricted to a date window.

  holdout_validation   one split date: pick the best parameter set on [start, split) and
                       report how it does on [split, end]; also returns the whole grid so
                       the IS->OOS decay is visible.
  walk_forward_validation
                       anchored, expanding-window walk-forward: for each OOS fold (e.g. one
                       calendar year) choose the best parameters on all data before the fold,
                       trade the fold with them, then stitch the OOS folds into one return
                       series and score it. This is the number to quote.

Parameter grids are lists of dicts of TrendFollowingConfig overrides; the objective is a
key of return_metrics() ("sharpe" by default), maximised, with an optional MDD cap.
"""
from dataclasses import replace
from datetime import date as _date
import logging

import numpy as np
import polars as pl

from strategy.trend_following.backtest import return_metrics
from strategy.trend_following.config import TrendFollowingConfig
from strategy.trend_following.portfolio import run_portfolio_backtest

logger = logging.getLogger(__name__)

DEFAULT_GRID = [
    {"entry_n": e, "exit_n": x, "vol_target_pct": v, "stop_atr_mult": s}
    for e, x in ((20, 10), (55, 20), (100, 50))
    for v in (0.0, 15.0)
    for s in (0.0, 3.0)
]


def _to_date(d):
    if isinstance(d, _date):
        return d
    return _date.fromisoformat(str(d)[:10])


def _params_label(params: dict) -> str:
    return ", ".join(f"{k}={v:g}" if isinstance(v, float) else f"{k}={v}" for k, v in params.items())


def daily_returns_for_grid(histories: dict, grid: list, base: TrendFollowingConfig = None) -> list:
    """Run the portfolio backtest once per grid point on the full history.
    Returns [(params, config, daily_frame)] where daily_frame has Date and portfolio_return."""
    base = base or TrendFollowingConfig()
    out = []
    for params in grid:
        cfg = replace(base, **params)
        res = run_portfolio_backtest(histories, cfg)
        daily = res["daily"]
        if daily.is_empty():
            continue
        out.append((params, cfg, daily.select(["Date", "portfolio_return"])))
    return out


def window_metrics(daily: pl.DataFrame, start, end, config: TrendFollowingConfig) -> dict:
    """return_metrics() of the daily portfolio returns with start <= Date < end
    (end=None -> through the last day)."""
    start, end = _to_date(start), (_to_date(end) if end else None)
    sub = daily.filter(pl.col("Date") >= start)
    if end is not None:
        sub = sub.filter(pl.col("Date") < end)
    m = return_metrics(sub.get_column("Date").to_list(), sub.get_column("portfolio_return").to_numpy(), config)
    return m


def _select_best(rows: list, objective: str, mdd_cap: float = None) -> int:
    """Index of the best row by `objective` (higher is better) among rows whose MDD is
    within mdd_cap; falls back to the best unconstrained row if none qualifies."""
    def score(r):
        return r["is"][objective]
    eligible = [i for i, r in enumerate(rows) if mdd_cap is None or r["is"]["max_drawdown_pct"] <= mdd_cap]
    pool = eligible or list(range(len(rows)))
    return max(pool, key=lambda i: score(rows[i]))


def holdout_validation(histories: dict, split_date, grid: list = None, base: TrendFollowingConfig = None,
                       objective: str = "sharpe", mdd_cap: float = None, end_date=None) -> dict:
    """Single IS/OOS split. Returns {best_params, is, oos, grid (per-row is/oos metrics),
    is_oos_rank_corr, split_date}."""
    base = base or TrendFollowingConfig()
    grid = grid or DEFAULT_GRID
    runs = daily_returns_for_grid(histories, grid, base)
    rows = []
    for params, cfg, daily in runs:
        first = daily.get_column("Date")[0]
        rows.append({
            "params": params, "label": _params_label(params),
            "is": window_metrics(daily, first, split_date, cfg),
            "oos": window_metrics(daily, split_date, end_date, cfg),
        })
    if not rows:
        return {"best_params": None, "is": None, "oos": None, "grid": [], "is_oos_rank_corr": None,
                "split_date": str(split_date)}
    best = _select_best(rows, objective, mdd_cap)
    is_scores = [r["is"][objective] for r in rows]
    oos_scores = [r["oos"][objective] for r in rows]
    corr = _spearman(is_scores, oos_scores) if len(rows) > 2 else None
    return {
        "best_params": rows[best]["params"],
        "best_label": rows[best]["label"],
        "is": rows[best]["is"],
        "oos": rows[best]["oos"],
        "grid": rows,
        "is_oos_rank_corr": corr,
        "split_date": str(_to_date(split_date)),
        "objective": objective,
    }


def walk_forward_validation(histories: dict, folds: list, grid: list = None, base: TrendFollowingConfig = None,
                            objective: str = "sharpe", mdd_cap: float = None, min_is_days: int = 250) -> dict:
    """Anchored walk-forward. `folds` is a list of (oos_start, oos_end) date pairs, in order;
    for each fold the grid is scored on all data before oos_start (>= min_is_days days)
    and the winner is traded on [oos_start, oos_end). Returns {folds: [...], oos: metrics of
    the stitched OOS series, oos_daily: polars frame, n_folds}."""
    base = base or TrendFollowingConfig()
    grid = grid or DEFAULT_GRID
    runs = daily_returns_for_grid(histories, grid, base)
    if not runs:
        return {"folds": [], "oos": return_metrics([], [], base), "oos_daily": pl.DataFrame(), "n_folds": 0}
    first = min(d.get_column("Date")[0] for _, _, d in runs)

    fold_rows, stitched = [], []
    for oos_start, oos_end in folds:
        oos_start, oos_end = _to_date(oos_start), _to_date(oos_end)
        rows = []
        for params, cfg, daily in runs:
            is_m = window_metrics(daily, first, oos_start, cfg)
            if is_m["n_days"] < min_is_days:
                continue
            rows.append({"params": params, "label": _params_label(params), "cfg": cfg, "daily": daily,
                         "is": is_m, "oos": window_metrics(daily, oos_start, oos_end, cfg)})
        if not rows:
            continue
        b = _select_best(rows, objective, mdd_cap)
        win = rows[b]
        seg = win["daily"].filter((pl.col("Date") >= oos_start) & (pl.col("Date") < oos_end))
        stitched.append(seg.with_columns(pl.lit(win["label"]).alias("params")))
        fold_rows.append({
            "oos_start": str(oos_start), "oos_end": str(oos_end),
            "best_params": win["params"], "best_label": win["label"],
            "is": win["is"], "oos": win["oos"],
            "grid_oos_sharpe": {r["label"]: r["oos"]["sharpe"] for r in rows},
        })

    if not stitched:
        return {"folds": [], "oos": return_metrics([], [], base), "oos_daily": pl.DataFrame(), "n_folds": 0}
    oos_daily = pl.concat(stitched).sort("Date")
    oos = return_metrics(oos_daily.get_column("Date").to_list(), oos_daily.get_column("portfolio_return").to_numpy(), base)
    return {"folds": fold_rows, "oos": oos, "oos_daily": oos_daily, "n_folds": len(fold_rows), "objective": objective}


def yearly_folds(first_oos_year: int, last_oos_year: int) -> list:
    """[(Jan 1 of Y, Jan 1 of Y+1)] for Y in first_oos_year..last_oos_year."""
    return [(_date(y, 1, 1), _date(y + 1, 1, 1)) for y in range(first_oos_year, last_oos_year + 1)]


def _spearman(a, b):
    ra = np.argsort(np.argsort(a)).astype(float)
    rb = np.argsort(np.argsort(b)).astype(float)
    if ra.std() == 0 or rb.std() == 0:
        return 0.0
    return float(np.corrcoef(ra, rb)[0, 1])
