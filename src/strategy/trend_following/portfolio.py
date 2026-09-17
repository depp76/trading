"""strategy/trend_following/portfolio.py — v3: the Donchian strategy applied to several
instruments at once (trend_following.md 3 "v3", 5).

Each instrument is run through run_backtest() on its own (v1 rules plus whatever v2
overlays the config enables), which yields a daily strategy-return series that already
includes that sleeve's weight and costs. The portfolio is a constant-mix of equal
sleeves: on every trading day the capital is split 1/N across the N instruments, so
the portfolio return is the plain average of the sleeve returns. Days on which an
instrument has no bar (holiday, different exchange) contribute 0 for that sleeve.
Gross exposure is therefore the average sleeve weight and never exceeds max_weight.
"""
import logging

import numpy as np
import polars as pl

from data.history import get_historical_data
from strategy.trend_following.backtest import run_backtest, return_metrics, _iso
from strategy.trend_following.config import TrendFollowingConfig

logger = logging.getLogger(__name__)


def run_portfolio_backtest(histories: dict, config: TrendFollowingConfig = None,
                           initial_capital: float = 1.0, min_days: int = 30) -> dict:
    """Backtest the strategy on `histories` ({ticker: daily OHLC polars frame}).

    Returns:
      summary          return_metrics() of the portfolio + n_instruments, avg_gross_exposure_pct,
                       avg_n_positions, n_trades (sum over sleeves), instruments (list of per-sleeve
                       one-line summaries), skipped (tickers with too little data)
      daily            polars frame: Date, portfolio_return, equity, gross_exposure, n_positions
      equity_curve     [{date, value}]
      per_instrument   {ticker: run_backtest() summary}
      sleeve_returns   polars frame: Date + one column per ticker (daily strategy return, 0 when absent)
    """
    config = config or TrendFollowingConfig()
    per_instrument, frames, skipped, n_trades = {}, [], [], 0
    for ticker, df in histories.items():
        if df is None or df.is_empty() or df.height < min_days:
            skipped.append(ticker)
            continue
        res = run_backtest(df, config, 1.0)
        sig = res["signals"]
        if sig.height < 2:
            skipped.append(ticker)
            continue
        per_instrument[ticker] = res["summary"]
        n_trades += res["summary"]["n_trades"]
        w_lag = np.r_[0.0, sig.get_column("weight").to_numpy()[:-1]]
        frames.append(
            sig.select(["Date", "strategy_return"]).rename({"strategy_return": ticker})
            .with_columns(pl.Series(f"_w_{ticker}", w_lag))
        )

    if not frames:
        return {"summary": _empty_portfolio_summary(config, skipped), "daily": pl.DataFrame(),
                "equity_curve": [], "per_instrument": {}, "sleeve_returns": pl.DataFrame()}

    aligned = frames[0]
    for f in frames[1:]:
        aligned = aligned.join(f, on="Date", how="full", coalesce=True)
    aligned = aligned.sort("Date").fill_null(0.0)

    tickers = list(per_instrument.keys())
    n = len(tickers)
    ret_mat = aligned.select(tickers).to_numpy()
    w_mat = aligned.select([f"_w_{t}" for t in tickers]).to_numpy()
    port_ret = ret_mat.mean(axis=1)                      # equal sleeves, constant mix
    gross = w_mat.mean(axis=1)                           # average sleeve weight = gross exposure
    n_pos = (w_mat > 0).sum(axis=1)
    equity = initial_capital * np.cumprod(1.0 + port_ret)
    dates = aligned.get_column("Date").to_list()

    summary = return_metrics(dates, port_ret, config, initial_capital)
    summary.update({
        "n_instruments": n,
        "avg_gross_exposure_pct": float(gross.mean()) * 100.0,
        "avg_n_positions": float(n_pos.mean()),
        "n_trades": n_trades,
        "instruments": [
            {"ticker": t, "cagr_pct": s["cagr_pct"], "sharpe": s["sharpe"], "max_drawdown_pct": s["max_drawdown_pct"],
             "n_trades": s["n_trades"], "exposure_pct": s["exposure_pct"]}
            for t, s in per_instrument.items()
        ],
        "skipped": skipped,
        "entry_n": config.entry_n, "exit_n": config.exit_n, "cost_per_side": config.cost_per_side,
        "v2": per_instrument[tickers[0]]["v2"],
    })
    daily = pl.DataFrame({
        "Date": dates, "portfolio_return": port_ret, "equity": equity,
        "gross_exposure": gross, "n_positions": n_pos.astype(np.int64),
    })
    return {
        "summary": summary,
        "daily": daily,
        "equity_curve": [{"date": _iso(d), "value": float(v)} for d, v in zip(dates, equity)],
        "per_instrument": per_instrument,
        "sleeve_returns": aligned.select(["Date"] + tickers),
    }


def run_portfolio_backtest_for_tickers(tickers: list, start: str, config: TrendFollowingConfig = None,
                                       initial_capital: float = 1.0) -> dict:
    """Fetch each ticker via data.history.get_historical_data() and run run_portfolio_backtest()."""
    histories = {t: get_historical_data(t, start) for t in tickers}
    res = run_portfolio_backtest(histories, config, initial_capital)
    res["tickers"] = list(tickers)
    return res


def _empty_portfolio_summary(config: TrendFollowingConfig, skipped: list) -> dict:
    m = return_metrics([], [], config)
    m.update({"n_instruments": 0, "avg_gross_exposure_pct": 0.0, "avg_n_positions": 0.0, "n_trades": 0,
              "instruments": [], "skipped": skipped, "entry_n": config.entry_n, "exit_n": config.exit_n,
              "cost_per_side": config.cost_per_side, "v2": {}})
    return m
