"""strategy/metrics.py — Shared return/trade performance metrics
(review_agy.md Section 4, Phase 1-2).

Two entry points, one per input shape the existing strategies produce:

  calculate_returns_metrics(returns, dates, ...)   a per-period return series
        -> what strategy.trend_following.backtest.return_metrics() delegates to
  calculate_equity_metrics(values, dates, initial_capital, ...)   an equity curve
        -> what strategy.rebalance.backtest._summarize_backtest() /
           _sharpe_and_vol() delegate to

Both compute the same Sharpe/volatility (sample std, ddof=1, sqrt(periods_per_year)
annualisation, optional risk-free rate) and the same calendar-day CAGR; they
differ only in what total return / drawdown are measured against (see each
docstring). Max drawdown is always reported as a positive magnitude here;
rebalance's summary keeps its historical negative sign by negating in its own
wrapper, so nothing the UI shows changed when it migrated.

ma_cross computes no metrics at all yet; giving it some is a separate step.
"""
import math

import numpy as np

from strategy.base import Trade

# Used in place of float("inf") when there are no losing trades: a profit
# factor of infinity is mathematically correct but awkward for a UI to
# display, sort or chart, so it's capped instead (same convention
# review_agy.md's Section 4 sketch used).
_UNCAPPED_PROFIT_FACTOR = 99.0

_EMPTY = {
    "total_return_pct": 0.0, "cagr_pct": 0.0, "annual_vol_pct": 0.0,
    "sharpe": 0.0, "max_drawdown_pct": 0.0,
}


def _years_elapsed(dates, n, periods_per_year):
    """Calendar years spanned by `dates` when given (one per period; a single
    period still counts as one day, like trend_following's max(days, 1)),
    else `n / periods_per_year` -- the annualisation a caller without dates
    (e.g. a bare weekly equity curve) implies."""
    if dates is not None and len(dates) == n and n > 0:
        try:
            return max((dates[-1] - dates[0]).days, 1) / 365.25
        except Exception:
            pass
    return n / periods_per_year


def _sharpe_and_vol(period_returns, periods_per_year, risk_free_rate):
    """Annualised volatility and Sharpe of a period-return series; both 0.0
    below two observations (no sample variance) or at zero variance."""
    if len(period_returns) < 2:
        return 0.0, 0.0
    vol = float(np.std(period_returns, ddof=1)) * math.sqrt(periods_per_year)
    excess = period_returns - risk_free_rate / periods_per_year
    excess_sd = float(np.std(excess, ddof=1))
    sharpe = (float(np.mean(excess)) / excess_sd * math.sqrt(periods_per_year)) if excess_sd > 0 else 0.0
    return vol, sharpe


def _cagr(final_over_base, years):
    return (final_over_base ** (1.0 / years) - 1.0) if (years > 0 and final_over_base > 0) else -1.0


def calculate_returns_metrics(
    returns,
    dates=None,
    periods_per_year: int = 252,
    risk_free_rate: float = 0.0,
) -> dict:
    """CAGR/Sharpe/volatility/max-drawdown from a period-return series
    (e.g. daily or weekly returns, not prices).

    Total return and drawdown are measured against the equity implied by
    compounding `returns` from 1.0, so the first period's return counts
    toward both. Drawdown is peak-relative on that equity; a total wipeout
    on the very first period (equity 0 / peak 0) propagates as NaN, exactly
    as trend_following.backtest.return_metrics always has.
    """
    returns = np.asarray(returns, dtype=float)
    n = len(returns)
    if n == 0:
        return dict(_EMPTY)

    equity = np.cumprod(1.0 + returns)
    final = float(equity[-1])
    years = _years_elapsed(dates, n, periods_per_year)
    vol, sharpe = _sharpe_and_vol(returns, periods_per_year, risk_free_rate)

    peak = np.maximum.accumulate(equity)
    drawdown = equity / peak - 1.0
    max_drawdown_pct = -float(drawdown.min()) * 100.0

    return {
        "total_return_pct": (final - 1.0) * 100.0,
        "cagr_pct": _cagr(final, years) * 100.0,
        "annual_vol_pct": vol * 100.0,
        "sharpe": sharpe,
        "max_drawdown_pct": max_drawdown_pct,
    }


def calculate_equity_metrics(
    values,
    dates=None,
    initial_capital: float | None = None,
    periods_per_year: int = 252,
    risk_free_rate: float = 0.0,
) -> dict:
    """The same metrics from an equity curve's levels (one per period).

    Total return and CAGR are measured against `initial_capital` when given
    -- a curve's first point is typically already net of the first period's
    trading costs, so it is not the capital that was put in -- else against
    the first point. Sharpe/volatility come from the curve's own
    period-over-period returns and drawdown tracks the running peak from
    the first point, so neither is affected by that choice. With a
    non-positive base nothing can be measured against it: total return and
    CAGR are 0.0.
    """
    values = np.asarray(values, dtype=float)
    n = len(values)
    if n == 0:
        return dict(_EMPTY)

    base = float(initial_capital) if initial_capital is not None else float(values[0])
    final = float(values[-1])
    years = _years_elapsed(dates, n, periods_per_year)

    if base > 0:
        total_return_pct = (final / base - 1.0) * 100.0
        cagr_pct = _cagr(final / base, years) * 100.0
    else:
        total_return_pct = 0.0
        cagr_pct = 0.0

    period_returns = np.diff(values) / values[:-1] if n > 1 else np.empty(0)
    vol, sharpe = _sharpe_and_vol(period_returns, periods_per_year, risk_free_rate)

    peak = float(values[0])
    max_dd = 0.0
    for v in values:
        peak = max(peak, float(v))
        if peak > 0:
            max_dd = min(max_dd, (float(v) - peak) / peak)

    return {
        "total_return_pct": total_return_pct,
        "cagr_pct": cagr_pct,
        "annual_vol_pct": vol * 100.0,
        "sharpe": sharpe,
        "max_drawdown_pct": -max_dd * 100.0,
    }


def calculate_trade_metrics(trades: list[Trade]) -> dict:
    """Win rate/profit factor/average return from a list of base.Trade."""
    if not trades:
        return {"n_trades": 0, "win_rate_pct": 0.0, "profit_factor": 0.0, "avg_trade_pct": 0.0}

    returns = [t.net_return_pct for t in trades]
    wins = [r for r in returns if r > 0]
    losses = [r for r in returns if r <= 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    if gross_loss > 0:
        profit_factor = gross_win / gross_loss
    else:
        profit_factor = _UNCAPPED_PROFIT_FACTOR if gross_win > 0 else 0.0

    return {
        "n_trades": len(trades),
        "win_rate_pct": len(wins) / len(trades) * 100.0,
        "profit_factor": profit_factor,
        "avg_trade_pct": float(np.mean(returns)),
    }
