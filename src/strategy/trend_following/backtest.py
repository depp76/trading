"""strategy/trend_following/backtest.py — Vectorised single-instrument backtest of the
Donchian breakout strategy (v1 + v2 overlays) with the metrics listed in
trend_following.md 5.

Position handling: `weight` from donchian_signal() is the size held at the close of
day t (0 when flat, 1.0 in v1); it earns day t+1's return (weight.shift(1) *
daily_return). A change in the lagged weight on day t means a fill of that size
happened at day t's close, so the per-side cost (fee + slippage) is charged on the
traded weight that day.
"""
from datetime import date as _date
import logging

import numpy as np
import polars as pl

from data.history import get_historical_data
from strategy.metrics import calculate_returns_metrics
from strategy.trend_following.config import TrendFollowingConfig
from strategy.trend_following.signals import donchian_signal

logger = logging.getLogger(__name__)


def run_backtest(df: pl.DataFrame, config: TrendFollowingConfig = None, initial_capital: float = 1.0) -> dict:
    """Backtest one instrument's daily OHLC history.

    Returns a dict with:
      summary       total_return_pct, cagr_pct, annual_vol_pct, sharpe, max_drawdown_pct,
                    n_trades, n_closed_trades, win_rate_pct, avg_trade_return_pct, exposure_pct,
                    avg_weight, n_channel_exits, n_stop_exits, passes_risk_gate, start_date,
                    end_date, n_days, entry_n, exit_n, cost_per_side, v2 (dict of the overlays)
      trades        [{entry_date, exit_date (None if still open), days_held, weight,
                     price_return_pct (entry close -> exit/last close), return_pct (portfolio,
                     net of cost and weight), exit_reason ("channel" | "stop" | None)}]
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
    weight = sig.get_column("weight").to_numpy().astype(float)
    reasons = sig.get_column("exit_reason").to_list()
    dates = sig.get_column("Date").to_list()

    daily_ret = np.zeros(n)
    daily_ret[1:] = close[1:] / close[:-1] - 1.0

    w_lag = np.zeros(n)                # weight that earns day t's return
    w_lag[1:] = weight[:-1]
    fills = np.zeros(n)                # traded weight at day t's close (lagged into t+1)
    fills[1:] = np.abs(weight[1:] - weight[:-1])
    strategy_ret = w_lag * daily_ret - fills * config.cost_per_side

    equity = initial_capital * np.cumprod(1.0 + strategy_ret)

    trades = _extract_trades(dates, pos, weight, close, strategy_ret, reasons)
    summary = _summarize(dates, strategy_ret, equity, pos, w_lag, trades, config, initial_capital)

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

def _extract_trades(dates, pos, weight, close, strategy_ret, reasons) -> list:
    """One trade per 0->1->0 cycle of `pos` (state at close). The trade earns the
    strategy returns of the days on which its lagged weight was active, i.e.
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
            last = j if j < n else n - 1
            seg = strategy_ret[entry_i + 1:last + 1]
            ret = float(np.prod(1.0 + seg) - 1.0) if seg.size else 0.0
            trades.append({
                "entry_date": _iso(dates[entry_i]),
                "exit_date": _iso(dates[j]) if j < n else None,
                "days_held": int(last - entry_i),
                "weight": float(weight[entry_i]),
                "price_return_pct": (float(close[last]) / float(close[entry_i]) - 1.0) * 100.0 if close[entry_i] else 0.0,
                "return_pct": ret * 100.0,
                "exit_reason": reasons[j] if j < n else None,
            })
            i = j
        else:
            i += 1
    return trades


def return_metrics(dates, strategy_ret, config: TrendFollowingConfig, initial_capital: float = 1.0) -> dict:
    """Return-stream metrics shared by the single-instrument backtest, the portfolio
    backtest and the IS/OOS validation: total return, CAGR, annual vol, Sharpe, max
    drawdown and the risk-gate verdict, computed from a daily strategy-return series.

    The numbers come from strategy.metrics.calculate_returns_metrics (shared with
    the other strategies since Phase 2 of review_agy.md Section 4); this wrapper
    only adds the strategy's own risk-gate verdict and date span. `initial_capital`
    scales the equity curve but cancels out of every ratio here, so it no longer
    takes part in the calculation."""
    n = len(strategy_ret)
    if n == 0:
        return {"total_return_pct": 0.0, "cagr_pct": 0.0, "annual_vol_pct": 0.0, "sharpe": 0.0,
                "max_drawdown_pct": 0.0, "passes_risk_gate": False, "start_date": None, "end_date": None, "n_days": 0}
    m = calculate_returns_metrics(
        strategy_ret, dates=dates,
        periods_per_year=config.trading_days_per_year, risk_free_rate=config.risk_free_rate,
    )
    sharpe, mdd_pct = m["sharpe"], m["max_drawdown_pct"]
    m.update({
        "passes_risk_gate": bool(sharpe >= config.sharpe_min and mdd_pct <= config.mdd_max_pct),
        "start_date": _iso(dates[0]),
        "end_date": _iso(dates[-1]),
        "n_days": n,
    })
    return m


def _summarize(dates, strategy_ret, equity, pos, w_lag, trades, config, initial_capital) -> dict:
    n = len(strategy_ret)
    m = return_metrics(dates, strategy_ret, config, initial_capital)
    sharpe, mdd_pct = m["sharpe"], m["max_drawdown_pct"]

    closed = [t for t in trades if t["exit_date"] is not None]
    wins = sum(1 for t in closed if t["return_pct"] > 0)
    win_rate = wins / len(closed) * 100.0 if closed else 0.0
    avg_trade = float(np.mean([t["return_pct"] for t in trades])) if trades else 0.0
    exposure = float(np.mean(pos)) * 100.0 if n else 0.0
    in_pos_w = w_lag[w_lag > 0]
    avg_weight = float(np.mean(in_pos_w)) if in_pos_w.size else 0.0

    return {
        "total_return_pct": m["total_return_pct"],
        "cagr_pct": m["cagr_pct"],
        "annual_vol_pct": m["annual_vol_pct"],
        "sharpe": sharpe,
        "max_drawdown_pct": mdd_pct,
        "n_trades": len(trades),
        "n_closed_trades": len(closed),
        "win_rate_pct": win_rate,
        "avg_trade_return_pct": avg_trade,
        "exposure_pct": exposure,
        "avg_weight": avg_weight,
        "n_channel_exits": sum(1 for t in closed if t["exit_reason"] == "channel"),
        "n_stop_exits": sum(1 for t in closed if t["exit_reason"] == "stop"),
        "passes_risk_gate": bool(sharpe >= config.sharpe_min and mdd_pct <= config.mdd_max_pct),
        "start_date": _iso(dates[0]),
        "end_date": _iso(dates[-1]),
        "n_days": n,
        "entry_n": config.entry_n,
        "exit_n": config.exit_n,
        "cost_per_side": config.cost_per_side,
        "v2": _v2_summary(config),
    }


def _v2_summary(config: TrendFollowingConfig) -> dict:
    return {
        "regime_ma_n": config.regime_ma_n,
        "stop_atr_mult": config.stop_atr_mult,
        "stop_mode": config.stop_mode if config.stop_enabled else None,
        "atr_n": config.atr_n,
        "vol_target_pct": config.vol_target_pct,
        "vol_n": config.vol_n,
        "max_weight": config.max_weight,
    }


def _empty_summary(config: TrendFollowingConfig) -> dict:
    return {
        "total_return_pct": 0.0, "cagr_pct": 0.0, "annual_vol_pct": 0.0, "sharpe": 0.0,
        "max_drawdown_pct": 0.0, "n_trades": 0, "n_closed_trades": 0, "win_rate_pct": 0.0,
        "avg_trade_return_pct": 0.0, "exposure_pct": 0.0, "avg_weight": 0.0,
        "n_channel_exits": 0, "n_stop_exits": 0, "passes_risk_gate": False,
        "start_date": None, "end_date": None, "n_days": 0,
        "entry_n": config.entry_n, "exit_n": config.exit_n, "cost_per_side": config.cost_per_side,
        "v2": _v2_summary(config),
    }


def _iso(d) -> str:
    if isinstance(d, _date):
        return d.strftime("%Y-%m-%d")
    return str(d)[:10]
