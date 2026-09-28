"""strategy/trend_following/metrics.py — 6-4 evaluation metrics.

Absolute (strategy and benchmarks alike, all after costs): CAGR, yearly
returns, volatility, Sharpe over BM4, MDD, Calmar, turnover, win rate,
average holding period, total costs, average cash weight.

Relative to a benchmark: excess return, information ratio, beta/alpha from a
daily regression, monthly up/down capture, and the 2021-22 / 2023-24 /
2025-26 sub-period scorecard. Everything works on the chained ``nav`` series a
BacktestResult carries, so the annual-reset runs and the benchmarks compare on
the same footing.
"""
from __future__ import annotations

from datetime import date

import numpy as np

from strategy.trend_following.backtest import BacktestResult, TRIM_REASON
from strategy.trend_following.config import PERIODS

TRADING_DAYS = 252


def nav_returns(nav: np.ndarray) -> np.ndarray:
    nav = np.asarray(nav, dtype=float)
    out = np.zeros(len(nav))
    if len(nav) > 1:
        out[1:] = nav[1:] / nav[:-1] - 1.0
    return out


def total_return(nav: np.ndarray) -> float:
    nav = np.asarray(nav, dtype=float)
    return float(nav[-1] / nav[0] - 1.0) if len(nav) and nav[0] > 0 else 0.0


def cagr(nav: np.ndarray, dates: list[date]) -> float:
    if len(nav) < 2 or nav[0] <= 0:
        return 0.0
    days = (dates[-1] - dates[0]).days
    if days <= 0:
        return 0.0
    return float((nav[-1] / nav[0]) ** (365.0 / days) - 1.0)


def annual_vol(rets: np.ndarray) -> float:
    r = np.asarray(rets, dtype=float)[1:]
    return float(np.std(r, ddof=1) * np.sqrt(TRADING_DAYS)) if len(r) > 2 else 0.0


def sharpe(rets: np.ndarray, rf_period: np.ndarray) -> float:
    ex = np.asarray(rets, dtype=float)[1:] - np.asarray(rf_period, dtype=float)[1:]
    if len(ex) < 3:
        return 0.0
    sd = np.std(ex, ddof=1)
    return float(np.mean(ex) / sd * np.sqrt(TRADING_DAYS)) if sd > 0 else 0.0


def max_drawdown(nav: np.ndarray) -> float:
    """Most negative peak-to-trough drawdown (<= 0)."""
    nav = np.asarray(nav, dtype=float)
    if len(nav) == 0:
        return 0.0
    peak = np.maximum.accumulate(nav)
    with np.errstate(divide="ignore", invalid="ignore"):
        dd = np.where(peak > 0, nav / peak - 1.0, 0.0)
    return float(np.min(dd))


def calmar(cagr_value: float, mdd: float) -> float:
    return float(cagr_value / abs(mdd)) if mdd < 0 else 0.0


def yearly_returns(nav: np.ndarray, dates: list[date]) -> dict[int, float]:
    out: dict[int, float] = {}
    prev_nav = nav[0] if len(nav) else 1.0
    year = dates[0].year if dates else None
    for i in range(len(nav)):
        if dates[i].year != year:
            out[year] = float(nav[i - 1] / prev_nav - 1.0)
            prev_nav = nav[i - 1]
            year = dates[i].year
    if year is not None:
        out[year] = float(nav[-1] / prev_nav - 1.0)
    return out


def monthly_returns(nav: np.ndarray, dates: list[date]) -> dict[tuple[int, int], float]:
    out: dict[tuple[int, int], float] = {}
    if len(nav) == 0:
        return out
    prev_nav = nav[0]
    key = (dates[0].year, dates[0].month)
    for i in range(len(nav)):
        k = (dates[i].year, dates[i].month)
        if k != key:
            out[key] = float(nav[i - 1] / prev_nav - 1.0)
            prev_nav = nav[i - 1]
            key = k
    out[key] = float(nav[-1] / prev_nav - 1.0)
    return out


def capture_ratios(strat_m: dict, bm_m: dict) -> tuple[float, float]:
    """(up capture, down capture) on common months, as ratios of mean returns."""
    keys = [k for k in bm_m if k in strat_m]
    ups = [k for k in keys if bm_m[k] > 0]
    downs = [k for k in keys if bm_m[k] < 0]

    def _ratio(ks):
        if not ks:
            return float("nan")
        b = np.mean([bm_m[k] for k in ks])
        s = np.mean([strat_m[k] for k in ks])
        return float(s / b) if b != 0 else float("nan")

    return _ratio(ups), _ratio(downs)


def beta_alpha(strat_rets: np.ndarray, bm_rets: np.ndarray, rf_period: np.ndarray) -> tuple[float, float]:
    """Daily excess-return regression strat = alpha + beta x bm; alpha annualised."""
    ys = np.asarray(strat_rets, dtype=float)[1:] - np.asarray(rf_period, dtype=float)[1:]
    xs = np.asarray(bm_rets, dtype=float)[1:] - np.asarray(rf_period, dtype=float)[1:]
    n = min(len(xs), len(ys))
    if n < 3:
        return float("nan"), float("nan")
    xs, ys = xs[:n], ys[:n]
    var = np.var(xs, ddof=1)
    if var <= 0:
        return float("nan"), float("nan")
    beta = float(np.cov(xs, ys, ddof=1)[0, 1] / var)
    alpha = float((np.mean(ys) - beta * np.mean(xs)) * TRADING_DAYS)
    return beta, alpha


def information_ratio(strat_rets: np.ndarray, bm_rets: np.ndarray) -> float:
    d = np.asarray(strat_rets, dtype=float)[1:] - np.asarray(bm_rets, dtype=float)[1:]
    if len(d) < 3:
        return 0.0
    sd = np.std(d, ddof=1)
    return float(np.mean(d) / sd * np.sqrt(TRADING_DAYS)) if sd > 0 else 0.0


def period_returns(nav: np.ndarray, dates: list[date], periods=PERIODS) -> dict[str, float | None]:
    out: dict[str, float | None] = {}
    years = np.array([d.year for d in dates])
    for label, y0, y1 in periods:
        idx = np.flatnonzero((years >= y0) & (years <= y1))
        if idx.size == 0:
            out[label] = None
            continue
        i0, i1 = idx[0], idx[-1]
        base = nav[i0 - 1] if i0 > 0 else nav[i0]
        out[label] = float(nav[i1] / base - 1.0) if base > 0 else None
    return out


def trade_stats(res: BacktestResult) -> dict:
    real = [t for t in res.trades if t.reason != TRIM_REASON]
    n = len(real)
    wins = sum(1 for t in real if t.pnl > 0)
    avg_hold = float(np.mean([t.holding_days for t in real])) if real else 0.0
    return {"n_trades": n, "win_rate": (wins / n) if n else 0.0, "avg_holding_days": avg_hold}


def _years(dates: list[date]) -> float:
    if len(dates) < 2:
        return 0.0
    return max((dates[-1] - dates[0]).days / 365.0, 1e-9)


def summarize_run(res: BacktestResult, bm1: BacktestResult | None = None,
                  bm3: BacktestResult | None = None) -> dict:
    """The 6-4 scorecard for one run (plus the BM1/BM3-relative block)."""
    nav, dates = res.nav, res.dates
    rets = nav_returns(nav)
    rf = res.rf_period
    c = cagr(nav, dates)
    mdd = max_drawdown(nav)
    years = _years(dates)
    mean_equity = float(np.nanmean(res.equity)) if len(res.equity) else 0.0
    costs = res.total_costs
    out = {
        "id": res.id,
        "cost_multiplier": res.cost_multiplier,
        "total_return": total_return(nav),
        "cagr": c,
        "vol": annual_vol(rets),
        "sharpe": sharpe(rets, rf),
        "mdd": mdd,
        "calmar": calmar(c, mdd),
        "yearly": yearly_returns(nav, dates),
        "periods": period_returns(nav, dates),
        "turnover": (res.total_traded_value / mean_equity / years) if mean_equity > 0 and years > 0 else 0.0,
        "cost_total": costs.total,
        "cost_commission": costs.commission,
        "cost_tax": costs.tax,
        "cost_slippage": costs.slippage,
        "cost_pct_per_year": (costs.total / mean_equity / years) if mean_equity > 0 and years > 0 else 0.0,
        "avg_cash_weight": float(1.0 - np.nanmean(res.exposure)) if len(res.exposure) else 1.0,
        "banked": float(sum(res.banked_by_year.values())),
        "topped_up": float(sum(res.topup_by_year.values())),
        "open_positions": len(res.open_positions),
    }
    out.update(trade_stats(res))
    for prefix, bm in (("bm1", bm1), ("bm3", bm3)):
        if bm is None or len(bm.nav) != len(nav):
            continue
        b_rets = nav_returns(bm.nav)
        beta, alpha = beta_alpha(rets, b_rets, rf)
        up, down = capture_ratios(monthly_returns(nav, dates), monthly_returns(bm.nav, bm.dates))
        b_cagr = cagr(bm.nav, bm.dates)
        b_mdd = max_drawdown(bm.nav)
        b_periods = period_returns(bm.nav, bm.dates)
        out[f"{prefix}_excess_cagr"] = c - b_cagr
        out[f"{prefix}_ir"] = information_ratio(rets, b_rets)
        out[f"{prefix}_beta"] = beta
        out[f"{prefix}_alpha"] = alpha
        out[f"{prefix}_up_capture"] = up
        out[f"{prefix}_down_capture"] = down
        out[f"{prefix}_period_win"] = {
            k: (None if v is None or b_periods.get(k) is None else v > b_periods[k]) for k, v in out["periods"].items()
        }
        out[f"{prefix}_beats_risk_adjusted"] = bool(
            out["sharpe"] > sharpe(b_rets, bm.rf_period) and out["calmar"] > calmar(b_cagr, b_mdd))
    return out
