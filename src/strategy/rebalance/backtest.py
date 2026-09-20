"""strategy/rebalance/backtest.py — Walk-forward backtest orchestration and performance summary (rebalance.md 11-2)."""
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
import logging
import polars as pl

from data.history import get_historical_data
from strategy.metrics import calculate_equity_metrics
from strategy.rebalance.config import RebalanceConfig
from strategy.rebalance.classify import _DEFAULT_TOP_N_BY_MARKET
from strategy.rebalance.walkforward import (
    _compute_historical_factor_series,
    _rebalance_friday_dates,
    _run_walkforward_simulation,
)

logger = logging.getLogger(__name__)

# equity_curve has one point per rebalance Friday (rebalance.md 3-1), so
# annualizing Sharpe/volatility from period-over-period returns uses 52
# periods/year rather than trading_following's daily trading_days_per_year.
_REBALANCE_PERIODS_PER_YEAR = 52


def _curve_metrics(equity_curve: list, initial_capital: float | None) -> dict:
    """strategy.metrics.calculate_equity_metrics on a [{date, value}] curve
    (shared with the other strategies since Phase 2 of review_agy.md Section 4).
    No risk-free-rate config exists for this strategy (RebalanceConfig has
    none), so Sharpe is the simple 0% risk-free version."""
    return calculate_equity_metrics(
        [pt["value"] for pt in equity_curve],
        dates=[datetime.strptime(pt["date"], "%Y-%m-%d") for pt in equity_curve],
        initial_capital=initial_capital,
        periods_per_year=_REBALANCE_PERIODS_PER_YEAR,
    )


def _sharpe_and_vol(equity_curve: list) -> tuple:
    """Annualized volatility and Sharpe ratio from the weekly equity curve's
    period-over-period returns (see _curve_metrics)."""
    m = _curve_metrics(equity_curve, None)
    return m["annual_vol_pct"], m["sharpe"]


def _summarize_backtest(
    equity_curve: list,
    benchmark_curve: list,
    initial_capital: float,
    closed_trade_returns: list,
    n_rebalances: int,
    n_trades: int,
    total_cost_paid: float = 0.0,
) -> dict:
    """Performance summary for one run_rebalance_backtest() result."""
    if not equity_curve:
        return {
            "total_return_pct": 0.0, "benchmark_return_pct": 0.0, "cagr_pct": 0.0,
            "max_drawdown_pct": 0.0, "annual_vol_pct": 0.0, "sharpe": 0.0,
            "n_rebalances": 0, "n_trades": 0, "win_rate_pct": 0.0,
            "total_cost_amount": 0.0, "total_cost_drag_pct": 0.0,
        }

    final_value = equity_curve[-1]["value"]
    m = _curve_metrics(equity_curve, initial_capital)

    benchmark_return_pct = 0.0
    if benchmark_curve:
        benchmark_return_pct = (benchmark_curve[-1]["value"] / initial_capital - 1) * 100

    # This summary has always reported 0.0 (not -100%) for a curve that ends
    # at or below zero, and its drawdown as a negative percentage -- which
    # ui/dialogs/backtest_result.py displays as-is -- so both conventions are
    # kept here rather than in the shared calculator.
    cagr_pct = m["cagr_pct"] if final_value > 0 else 0.0

    win_rate_pct = (
        100.0 * sum(1 for r in closed_trade_returns if r > 0) / len(closed_trade_returns)
        if closed_trade_returns else 0.0
    )

    total_cost_drag_pct = (total_cost_paid / initial_capital * 100) if initial_capital else 0.0

    return {
        "total_return_pct": m["total_return_pct"],
        "benchmark_return_pct": benchmark_return_pct,
        "cagr_pct": cagr_pct,
        "max_drawdown_pct": -m["max_drawdown_pct"],
        "annual_vol_pct": m["annual_vol_pct"],
        "sharpe": m["sharpe"],
        "n_rebalances": n_rebalances,
        "n_trades": n_trades,
        "win_rate_pct": win_rate_pct,
        "total_cost_amount": total_cost_paid,
        "total_cost_drag_pct": total_cost_drag_pct,
    }


def run_rebalance_backtest(
    tickers: list,
    lookback_years: int = 3,
    top_n_by_market: dict = None,
    band_multiplier: float = RebalanceConfig().band_multiplier,
    initial_capital: float = 100_000_000.0,
    benchmark_ticker: str = "KS11",
    market_by_ticker: dict = None,
    buy_fee_rate: float = 0.00015,
    sell_fee_rate: float = 0.00015,
    sell_tax_rate: float = 0.0018,
    progress_callback=None,
) -> dict:
    """Walk-forward backtest of compute_weekly_rebalance_signals's algorithm with fees and taxes.

    top_n_by_market / market_by_ticker mirror compute_weekly_rebalance_signals's
    per-market buy cap (rebalance.md 3-5), e.g. {"KOSPI": 10, "KOSDAQ": 10} —
    market_by_ticker maps each ticker to its market so the walk-forward
    simulation can apply the same per-market cap historically.
    """
    top_n_by_market = top_n_by_market or _DEFAULT_TOP_N_BY_MARKET
    lookback_years = max(1, min(5, int(lookback_years)))
    end_date = datetime.now().date()
    fetch_start = end_date - timedelta(days=365 * lookback_years + 400)
    fetch_start_str = fetch_start.strftime("%Y-%m-%d")
    sim_start = end_date - timedelta(days=365 * lookback_years)

    series_map = {}
    skipped_tickers = []
    with ThreadPoolExecutor(max_workers=20) as exe:
        futures = {exe.submit(_compute_historical_factor_series, t, fetch_start_str): t for t in tickers}
        done = 0
        for fut in as_completed(futures):
            t = futures[fut]
            try:
                s = fut.result()
            except Exception:
                logger.warning("Historical factor series failed for ticker=%s", t, exc_info=True)
                s = None
            if s is None or s.is_empty():
                skipped_tickers.append(t)
            else:
                series_map[t] = s
            done += 1
            if progress_callback:
                progress_callback(done, len(tickers))

    rebalance_dates = _rebalance_friday_dates(sim_start, end_date)
    sim = _run_walkforward_simulation(
        series_map,
        rebalance_dates,
        top_n_by_market,
        band_multiplier,
        initial_capital,
        market_by_ticker=market_by_ticker,
        buy_fee_rate=buy_fee_rate,
        sell_fee_rate=sell_fee_rate,
        sell_tax_rate=sell_tax_rate,
    )

    benchmark_curve = []
    try:
        bench_df = get_historical_data(benchmark_ticker, fetch_start_str)
    except Exception:
        logger.warning("Benchmark history fetch failed for ticker=%s", benchmark_ticker, exc_info=True)
        bench_df = pl.DataFrame()
    if not bench_df.is_empty() and rebalance_dates:
        base_row = bench_df.filter(pl.col("Date") <= rebalance_dates[0])
        base_price = float(base_row.tail(1)["Close"][0]) if not base_row.is_empty() else float(bench_df["Close"][0])
        if base_price and base_price > 0:
            for d in rebalance_dates:
                sub = bench_df.filter(pl.col("Date") <= d)
                if sub.is_empty():
                    continue
                px = float(sub.tail(1)["Close"][0])
                benchmark_curve.append({"date": d.strftime("%Y-%m-%d"), "value": initial_capital * px / base_price})

    summary = _summarize_backtest(
        sim["equity_curve"],
        benchmark_curve,
        initial_capital,
        sim["closed_trade_returns"],
        len(rebalance_dates),
        len(sim["trades"]),
        total_cost_paid=sim.get("total_cost_paid", 0.0),
    )

    return {
        "lookback_years": lookback_years,
        "top_n_by_market": top_n_by_market,
        "band_multiplier": band_multiplier,
        "buy_fee_rate": buy_fee_rate,
        "sell_fee_rate": sell_fee_rate,
        "sell_tax_rate": sell_tax_rate,
        "start_date": sim_start.strftime("%Y-%m-%d"),
        "end_date": end_date.strftime("%Y-%m-%d"),
        "equity_curve": sim["equity_curve"],
        "benchmark_curve": benchmark_curve,
        "trades": sim["trades"],
        "rebalance_log": sim["rebalance_log"],
        "summary": summary,
        "skipped_tickers": skipped_tickers,
    }
