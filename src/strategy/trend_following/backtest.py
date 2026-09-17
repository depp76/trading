"""strategy/trend_following/backtest.py — Vectorised single-instrument backtest of the
Donchian breakout strategy with the metrics listed in trend_following.md 5.

Position handling: `position` from donchian_signal() is the state at the close of
day t; it earns day t+1's return (position.shift(1) * daily_return). A change in
the lagged position on day t means a fill happened at day t's close, so the
per-side cost (fee + slippage) is charged on that day.
"""
from datetime import date as _date
import logging
import math

import numpy as np
import polars as pl

from data.history import get_historical_data
from strategy.trend_following.config import TrendFollowingConfig
from strategy.trend_following.signals import donchian_signal

logger = logging.getLogger(__name__)


def run_backtest(df: pl.DataFrame, config: TrendFollowingConfig = None, initial_capital: float = 1.0) -> dict:
    """Backtest one instrument's daily OHLC history.

    Returns a dict with:
      summary       total_return_pct, cagr_pct, annual_vol_pct, sharpe, max_drawdown_pct,
                    n_trades, win_rate_pct, avg_trade_return_pct, exposure_pct,
                    passes_risk_gate, start_date, end_date, n_days
      trades        [{entry_date, exit_date (None if still open), days_held, return_pct}]
      equity_curve  [{date, value}] (value = initial_capital * cumulative strategy return)
      signals       the donchian_signal() frame plus daily_return / strategy_return / equity
    """
    config = config or TrendFollowingConfig()
    sig = donchian_signal(df, config)
    n = sig.height
    if n < 2:
        return {"summary": _empty_summary(config), "trades": [], "equity_curve": [], "signals": sig}

    close = sig.get_column("Close").to_numpy().astype(float)
    pos = sig.get_column("position").to_numpy().astype(float)
    dates = sig.get_column("Date").to_list()

    daily_ret = np.zeros(n)
    daily_ret[1:] = close[1:] / close[:-1] - 1.0

    pos_lag = np.zeros(n)              # position that earns day t's return
    pos_lag[1:] = pos[:-1]
    fills = np.zeros(n)                # 1 on days where a fill happened at the close
    fills[1:] = np.abs(pos[1:] - pos[:-1])
    strategy_ret = pos_lag * daily_ret - fills * config.cost_per_side

    equity = initial_capital * np.cumprod(1.0 + strategy_ret)

    trades = _extract_trades(dates, pos, strategy_ret)
    summary = _summarize(dates, strategy_ret, equity, pos_lag, trades, config, initial_capital)

    signals = sig.with_columns([
        pl.Series("daily_return", daily_ret),
        pl.Series("strategy_return", strategy_ret),
        pl.Series("equity", equity),
    ])
    equity_curve = [{"date": _iso(d), "value": float(v)} for d, v in zip(dates, equity)]
    return {"summary": summary, "trades": trades, "equity_curve": equity_curve, "signals": signals}


def run_backtest_for_ticker(ticker: str, start: str, config: TrendFollowingConfig = None,
                            initial_capital: float = 1.0) -> dict:
    """Fetch history via data.history.get_historical_data() and run run_backtest()."""
    df = get_historical_data(ticker, start)
    if df.is_empty():
        logger.warning("trend_following: no history for ticker=%s from %s", ticker, start)
        return {"ticker": ticker, "error": "No data", "summary": _empty_summary(config or TrendFollowingConfig()),
                "trades": [], "equity_curve": [], "signals": df}
    res = run_backtest(df, config, initial_capital)
    res["ticker"] = ticker
    res["error"] = ""
    return res


# ── internals ────────────────────────────────────────────────────────────────

def _extract_trades(dates, pos, strategy_ret) -> list:
    """One trade per 0->1->0 cycle of `pos` (state at close). The trade earns the
    strategy returns of the days on which its lagged position was active, i.e.
    from the day after entry through the exit day inclusive."""
    trades = []
    n = len(pos)
    i = 0
    while i < n:
        if pos[i] == 1 and (i == 0 or pos[i - 1] == 0):
            entry_i = i
            j = i + 1
            while j < n and pos[j] == 1:
                j += 1
            # lagged position is active on days entry_i+1 .. j (inclusive, if j < n)
            last = j if j < n else n - 1
            seg = strategy_ret[entry_i + 1:last + 1]
            ret = float(np.prod(1.0 + seg) - 1.0) if seg.size else 0.0
            trades.append({
                "entry_date": _iso(dates[entry_i]),
                "exit_date": _iso(dates[j]) if j < n else None,
                "days_held": int(last - entry_i),
                "return_pct": ret * 100.0,
            })
            i = j
        else:
            i += 1
    return trades


def _summarize(dates, strategy_ret, equity, pos_lag, trades, config, initial_capital) -> dict:
    n = len(strategy_ret)
    final = float(equity[-1])
    total_return = final / initial_capital - 1.0

    try:
        days = (dates[-1] - dates[0]).days
    except Exception:
        days = n
    years = max(days, 1) / 365.25
    cagr = (final / initial_capital) ** (1.0 / years) - 1.0 if final > 0 and years > 0 else -1.0

    tdpy = config.trading_days_per_year
    vol = float(np.std(strategy_ret, ddof=1)) * math.sqrt(tdpy) if n > 1 else 0.0
    excess = strategy_ret - config.risk_free_rate / tdpy
    sd = float(np.std(excess, ddof=1)) if n > 1 else 0.0
    sharpe = float(np.mean(excess)) / sd * math.sqrt(tdpy) if sd > 0 else 0.0

    peak = np.maximum.accumulate(equity)
    drawdown = equity / peak - 1.0
    mdd = float(drawdown.min()) if n else 0.0   # <= 0

    closed = [t for t in trades if t["exit_date"] is not None]
    wins = sum(1 for t in closed if t["return_pct"] > 0)
    win_rate = wins / len(closed) * 100.0 if closed else 0.0
    avg_trade = float(np.mean([t["return_pct"] for t in trades])) if trades else 0.0
    exposure = float(np.mean(pos_lag)) * 100.0 if n else 0.0

    mdd_pct = -mdd * 100.0
    return {
        "total_return_pct": total_return * 100.0,
        "cagr_pct": cagr * 100.0,
        "annual_vol_pct": vol * 100.0,
        "sharpe": sharpe,
        "max_drawdown_pct": mdd_pct,
        "n_trades": len(trades),
        "n_closed_trades": len(closed),
        "win_rate_pct": win_rate,
        "avg_trade_return_pct": avg_trade,
        "exposure_pct": exposure,
        "passes_risk_gate": bool(sharpe >= config.sharpe_min and mdd_pct <= config.mdd_max_pct),
        "start_date": _iso(dates[0]),
        "end_date": _iso(dates[-1]),
        "n_days": n,
        "entry_n": config.entry_n,
        "exit_n": config.exit_n,
        "cost_per_side": config.cost_per_side,
    }


def _empty_summary(config: TrendFollowingConfig) -> dict:
    return {
        "total_return_pct": 0.0, "cagr_pct": 0.0, "annual_vol_pct": 0.0, "sharpe": 0.0,
        "max_drawdown_pct": 0.0, "n_trades": 0, "n_closed_trades": 0, "win_rate_pct": 0.0,
        "avg_trade_return_pct": 0.0, "exposure_pct": 0.0, "passes_risk_gate": False,
        "start_date": None, "end_date": None, "n_days": 0,
        "entry_n": config.entry_n, "exit_n": config.exit_n, "cost_per_side": config.cost_per_side,
    }


def _iso(d) -> str:
    if isinstance(d, _date):
        return d.strftime("%Y-%m-%d")
    return str(d)[:10]
