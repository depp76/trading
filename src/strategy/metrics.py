"""strategy/metrics.py — Shared return/trade performance metrics
(review_agy.md Section 4, Phase 1).

Purely additive (see base.py's module docstring). rebalance and
trend_following already compute Sharpe/CAGR/MDD themselves
(strategy/rebalance/backtest.py::_sharpe_and_vol,
strategy/trend_following/backtest.py::return_metrics) and ma_cross computes
none at all. Migrating any of them onto this module is a deliberate,
separate follow-up -- not done here -- since it must first be verified to
reproduce identical numbers: rebalance.md/trend_following.md record real-data
backtest results that would go stale otherwise.
"""
import math

import numpy as np

from strategy.base import Trade

# Used in place of float("inf") when there are no losing trades: a profit
# factor of infinity is mathematically correct but awkward for a UI to
# display, sort or chart, so it's capped instead (same convention
# review_agy.md's Section 4 sketch used).
_UNCAPPED_PROFIT_FACTOR = 99.0


def calculate_returns_metrics(
    returns,
    dates=None,
    periods_per_year: int = 252,
    risk_free_rate: float = 0.0,
) -> dict:
    """CAGR/Sharpe/volatility/max-drawdown from a period-return series
    (e.g. daily or weekly returns, not prices).

    When `dates` is given (one per return, same length), CAGR is computed
    from actual elapsed calendar time (dates[-1] - dates[0]) the way
    trend_following.backtest.return_metrics does; otherwise it falls back to
    `len(returns) / periods_per_year` years, the way rebalance's weekly
    equity curve is annualized.
    """
    returns = np.asarray(returns, dtype=float)
    n = len(returns)
    if n == 0:
        return {
            "total_return_pct": 0.0, "cagr_pct": 0.0, "annual_vol_pct": 0.0,
            "sharpe": 0.0, "max_drawdown_pct": 0.0,
        }

    equity = np.cumprod(1.0 + returns)
    final = float(equity[-1])
    total_return_pct = (final - 1.0) * 100.0

    days = None
    if dates is not None and len(dates) == n and n > 1:
        try:
            days = (dates[-1] - dates[0]).days
        except Exception:
            days = None
    years = (max(days, 1) / 365.25) if days is not None else (n / periods_per_year)
    cagr = (final ** (1.0 / years) - 1.0) if (years > 0 and final > 0) else -1.0

    if n > 1:
        vol = float(np.std(returns, ddof=1)) * math.sqrt(periods_per_year)
        excess = returns - risk_free_rate / periods_per_year
        excess_sd = float(np.std(excess, ddof=1))
        sharpe = (float(np.mean(excess)) / excess_sd * math.sqrt(periods_per_year)) if excess_sd > 0 else 0.0
    else:
        vol = 0.0
        sharpe = 0.0

    peak = np.maximum.accumulate(equity)
    drawdown = equity / peak - 1.0
    max_drawdown_pct = -float(drawdown.min()) * 100.0

    return {
        "total_return_pct": total_return_pct,
        "cagr_pct": cagr * 100.0,
        "annual_vol_pct": vol * 100.0,
        "sharpe": sharpe,
        "max_drawdown_pct": max_drawdown_pct,
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
