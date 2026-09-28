"""strategy/trend_following/validation_v1.py — Runners for the trend_following.md 4
validation plan of the KR Donchian portfolio strategy, plus the 3-1 data checks.

  compare_assumptions(...)      4장 1단계: base config vs. one change at a time
  parameter_sensitivity(...)    4장 2단계: channel x max_positions x risk_per_trade grid
  walk_forward_years(...)       4장 3단계: anchored yearly walk-forward over a parameter grid
  abnormal_return_rows(...)     3-1: |daily return| above the price-limit band (data-error candidates)

Every runner calls engine.run_kr_trend() once per configuration on the full period and
reduces the result to one row (`summarize_run`), so the tables can be pasted into the
spec. The walk-forward selects each OOS year's parameters on the daily time-weighted
returns of the years before it only; signals are backward-looking, and the yearly
capital reset (2-5) makes the years nearly independent, so evaluating one full run
per grid point is equivalent to re-running the engine per fold.
"""
from dataclasses import replace
import logging
import statistics

import numpy as np
import polars as pl

from strategy.costs import TransactionCostModel
from strategy.metrics import calculate_returns_metrics
from strategy.trend_following.config_v1 import KrTrendConfig
from strategy.trend_following.engine import run_kr_trend

logger = logging.getLogger(__name__)

# trend_following.md 4, "1단계 — 가정별 영향 비교" (base value -> alternative)
ASSUMPTION_VARIANTS = [
    ("base", {}),
    ("fill: signal-day close", {"fill_at": "signal_close"}),
    ("cost: none", {"cost_model": TransactionCostModel(), "slippage_rate": 0.0}),
    ("cost: slippage 0.3%", {"slippage_rate": 0.003}),
    ("entry check: daily", {"entry_check": "daily"}),
    ("exit check: weekly", {"exit_check": "weekly"}),
    ("weekly rule: strict", {"weekly_entry_rule": "strict"}),
    ("market filter: off", {"index_regime_ma_n": 0}),
    ("universe: fixed (survivorship)", {"universe_mode": "fixed"}),
    ("harvest: cash first", {"harvest_mode": "cash_first"}),
    ("harvest: none (reinvest)", {"harvest_mode": "none"}),
    ("loss year: no top-up", {"topup_on_loss": False}),
    ("channel 55/20", {"entry_n": 55, "exit_n": 20}),
    ("channel 100/50", {"entry_n": 100, "exit_n": 50}),
]

DEFAULT_CHANNELS = ((20, 10), (55, 20), (100, 50))
DEFAULT_MAX_POSITIONS = (8, 10, 12)
DEFAULT_RISKS = (0.005, 0.0075, 0.01)


def summarize_run(name: str, res: dict, params: dict = None) -> dict:
    """One comparison-table row from a run_kr_trend() result."""
    s = res["summary"]
    complete = [y for y in res["years"] if y["complete"]]
    rets = [y["return_pct"] for y in complete]
    return {
        "name": name, "params": dict(params or {}),
        "n_years": len(complete),
        "median_return_pct": statistics.median(rets) if rets else 0.0,
        "mean_return_pct": statistics.fmean(rets) if rets else 0.0,
        "min_return_pct": min(rets) if rets else 0.0,
        "min_return_year": min(complete, key=lambda y: y["return_pct"])["year"] if complete else None,
        "sharpe": s["sharpe"],
        "max_year_mdd_pct": s["max_year_mdd_pct"],
        "n_positive_years": s["n_positive_years"],
        "n_years_beat_kospi": s["n_years_beat_kospi"],
        "n_trades": s["n_trades"],
        "win_rate_pct": s["win_rate_pct"],
        "net_pnl": s["net_pnl"],
        "passes_risk_gate": s["passes_risk_gate"],
    }


def compare_assumptions(histories: dict, index_df, base: KrTrendConfig = None, start=None, end=None,
                        variants=None, benchmarks=None) -> list:
    """4장 1단계. Returns one summarize_run() row per (name, overrides) in `variants`
    (default ASSUMPTION_VARIANTS), base config first."""
    base = base or KrTrendConfig()
    rows = []
    for name, overrides in (variants or ASSUMPTION_VARIANTS):
        cfg = replace(base, **overrides)
        res = run_kr_trend(histories, index_df, cfg, start, end, benchmarks)
        rows.append(summarize_run(name, res, overrides))
    return rows


def parameter_sensitivity(histories: dict, index_df, base: KrTrendConfig = None, start=None, end=None,
                          channels=DEFAULT_CHANNELS, max_positions=DEFAULT_MAX_POSITIONS,
                          risks=DEFAULT_RISKS, benchmarks=None) -> list:
    """4장 2단계. One row per grid point; the base point is flagged with is_base=True."""
    base = base or KrTrendConfig()
    rows = []
    for e, x in channels:
        for mp in max_positions:
            for r in risks:
                params = {"entry_n": e, "exit_n": x, "max_positions": mp, "risk_per_trade": r}
                cfg = replace(base, **params)
                res = run_kr_trend(histories, index_df, cfg, start, end, benchmarks)
                row = summarize_run(f"{e}/{x} pos{mp} risk{r * 100:g}%", res, params)
                row["is_base"] = (e, x, mp, r) == (base.entry_n, base.exit_n, base.max_positions, base.risk_per_trade)
                rows.append(row)
    return rows


def walk_forward_years(histories: dict, index_df, base: KrTrendConfig = None, start=None, end=None,
                       grid: list = None, first_oos_year: int = None, objective: str = "sharpe",
                       benchmarks=None) -> dict:
    """4장 3단계: anchored yearly walk-forward.

    `grid` is a list of override dicts (default: the three channels). Each grid point is run
    once over [start, end]; for every OOS year Y >= first_oos_year the point with the best
    `objective` ("sharpe" | "median_return_pct" | "min_return_pct") on the daily returns of
    the years before Y is selected and its year-Y daily returns are stitched into the OOS
    series. Returns {folds: [{year, chosen, chosen_label, is_base, is_metric, oos_return_pct}],
    oos: metrics of the stitched series, n_base_chosen, runs: {label: summarize_run row}}.
    """
    base = base or KrTrendConfig()
    grid = grid or [{"entry_n": e, "exit_n": x} for e, x in DEFAULT_CHANNELS]
    runs = []
    for params in grid:
        cfg = replace(base, **params)
        res = run_kr_trend(histories, index_df, cfg, start, end, benchmarks)
        if res["daily"].is_empty():
            continue
        runs.append((params, cfg, res))
    if not runs:
        return {"folds": [], "oos": None, "n_base_chosen": 0, "runs": {}}

    years = sorted({y["year"] for _, _, r in runs for y in r["years"] if y["complete"]})
    if first_oos_year is None:
        first_oos_year = years[2] if len(years) > 2 else (years[-1] if years else None)
    folds, stitched = [], []
    base_key = (base.entry_n, base.exit_n)
    for y in years:
        if first_oos_year is None or y < first_oos_year:
            continue
        scored = []
        for params, cfg, res in runs:
            is_daily = res["daily"].filter(pl.col("Date").dt.year() < y)
            if is_daily.height < 2:
                continue
            scored.append((_objective(res, is_daily, y, objective, cfg), params, cfg, res))
        if not scored:
            continue
        scored.sort(key=lambda s: -s[0])
        score, params, cfg, res = scored[0]
        seg = res["daily"].filter(pl.col("Date").dt.year() == y)
        stitched.append(seg.select(["Date", "daily_return"]))
        yrow = next((r for r in res["years"] if r["year"] == y), None)
        folds.append({
            "year": y, "chosen": params, "chosen_label": _label(params), "is_metric": score,
            "is_base": (cfg.entry_n, cfg.exit_n) == base_key,
            "oos_return_pct": yrow["return_pct"] if yrow else None,
            "oos_mdd_pct": yrow["mdd_pct"] if yrow else None,
        })
    oos = None
    if stitched:
        all_oos = pl.concat(stitched).sort("Date")
        oos = calculate_returns_metrics(all_oos.get_column("daily_return").to_numpy(),
                                        dates=all_oos.get_column("Date").to_list(),
                                        periods_per_year=base.trading_days_per_year)
        oos["n_days"] = all_oos.height
    return {
        "folds": folds, "oos": oos, "objective": objective,
        "n_base_chosen": sum(1 for f in folds if f["is_base"]),
        "runs": {_label(p): summarize_run(_label(p), r, p) for p, _, r in runs},
    }


def abnormal_return_rows(histories: dict, threshold: float = 0.35) -> list:
    """3-1 data check: rows whose close-to-close return exceeds the KRX price-limit band
    (|r| > threshold). Returns [{ticker, date, return_pct}] sorted by ticker, date."""
    out = []
    for ticker, df in histories.items():
        if df is None or df.is_empty() or "Close" not in df.columns:
            continue
        d = (df.sort("Date").filter(pl.col("Close").is_not_null())
             .with_columns((pl.col("Close").cast(pl.Float64) / pl.col("Close").cast(pl.Float64).shift(1) - 1.0).alias("_r"))
             .filter(pl.col("_r").abs() > threshold))
        for dt, r in zip(d.get_column("Date").to_list(), d.get_column("_r").to_list()):
            out.append({"ticker": ticker, "date": str(dt)[:10], "return_pct": float(r) * 100.0})
    out.sort(key=lambda r: (r["ticker"], r["date"]))
    return out


def _objective(res: dict, is_daily: pl.DataFrame, oos_year: int, objective: str, cfg: KrTrendConfig) -> float:
    if objective == "sharpe":
        m = calculate_returns_metrics(is_daily.get_column("daily_return").to_numpy(),
                                      dates=is_daily.get_column("Date").to_list(),
                                      periods_per_year=cfg.trading_days_per_year)
        return float(m["sharpe"]) if not np.isnan(m["sharpe"]) else -np.inf
    rets = [y["return_pct"] for y in res["years"] if y["complete"] and y["year"] < oos_year]
    if not rets:
        return -np.inf
    if objective == "median_return_pct":
        return float(statistics.median(rets))
    if objective == "min_return_pct":
        return float(min(rets))
    raise ValueError(f"unknown objective {objective!r}")


def _label(params: dict) -> str:
    return ", ".join(f"{k}={v:g}" if isinstance(v, float) else f"{k}={v}" for k, v in params.items()) or "base"
