"""strategy/ma_cross/backtest.py — fills and P/L for the MA cross strategy
(ma_cross.md 3, 4). Signals come from signals.py, parameters from config.py."""
import gc
from datetime import datetime, timedelta
import numpy as np
import pandas as pd
from yahooquery import Ticker as YQTicker
import logging

from data.indicators import _to_polars, _compute_indicators
from data.market import fetch_stock_ma_multi
from strategy.ma_cross.config import MaCrossConfig
from strategy.ma_cross.signals import entry_signal, exit_condition, next_true_index

logger = logging.getLogger(__name__)


def run_backtest_strategy(df, buy_sell_points=False, target_year=None, config: MaCrossConfig = None):
    """Fast/slow MA cross strategy (vectorised numpy, ma_cross.md 3).

    cumulative_return is compounded across trades (growth_factor = prod(1 + r_i),
    not sum(r_i)) so back-to-back winning trades compound the way they actually
    would in a single account, consistent with rebalance/trend_following's
    cumprod-based equity curves.

    Returns:
        buy_sell_points=False: (total_trades, win_count, cumulative_return)
        buy_sell_points=True:  (total_trades, win_count, cumulative_return,
                                buy_dates, buy_prices, sell_dates, sell_prices, bt_buy_date_list)
    """
    cfg = config or MaCrossConfig()
    empty = (0, 0, 0.0, [], [], [], [], []) if buy_sell_points else (0, 0, 0.0)
    if df is None or df.is_empty() or cfg.fast_col not in df.columns or cfg.slow_col not in df.columns:
        return empty

    ma_fast = df.get_column(cfg.fast_col).to_numpy()
    ma_slow = df.get_column(cfg.slow_col).to_numpy()
    df_close = df.get_column("Close").to_numpy()
    df_open = df.get_column("Open").to_numpy()
    df_dates = df.get_column("Date").to_numpy()

    buy_idx = np.where(entry_signal(ma_fast, ma_slow, cfg))[0]
    if target_year is not None:
        years = (df_dates.astype("datetime64[Y]").astype(int) + 1970)
        buy_idx = buy_idx[years[buy_idx] == target_year]

    buy_dates, buy_prices = [], []
    sell_dates, sell_prices = [], []
    bt_buy_date_list = []
    total_trades = win_count = 0
    growth_factor = 1.0

    if len(buy_idx) == 0:
        return empty

    n = len(df)
    next_exit_idx = next_true_index(exit_condition(ma_fast, ma_slow, cfg))

    last_sell_idx = -1
    for b in buy_idx:
        if b <= last_sell_idx:
            continue                      # one position at a time (ma_cross.md 3)

        b_price = df_close[b]
        b_date = df_dates[b]

        start = b + 1
        if start >= n:
            break

        # Exit 1 (take profit): first day the running max close reaches the target.
        threshold = b_price * cfg.take_profit_mult
        cummax_suffix = np.maximum.accumulate(df_close[start:])
        pos = np.searchsorted(cummax_suffix, threshold, side='left')
        idx1 = start + pos if pos < len(cummax_suffix) else n
        # Exits 2/3 (overheat / dead cross): first qualifying day after entry.
        idx2 = next_exit_idx[start]
        s = idx1 if idx1 < idx2 else idx2

        if s >= n:
            break

        if s + 1 < n:                     # fill at the next day's open
            exec_sell_idx = s + 1
            s_price = df_open[exec_sell_idx]
            s_date = df_dates[exec_sell_idx]

            trade_return = (s_price - b_price) / b_price * 100
            growth_factor *= 1.0 + trade_return / 100
            total_trades += 1
            if trade_return > 0:
                win_count += 1
            if buy_sell_points:
                buy_dates.append(b_date)
                buy_prices.append(b_price)
                bt_buy_date_list.append(b_date)
                sell_dates.append(s_date)
                sell_prices.append(s_price)
            last_sell_idx = s

    cumulative_return = (growth_factor - 1.0) * 100
    return (total_trades, win_count, cumulative_return,
            buy_dates, buy_prices, sell_dates, sell_prices, bt_buy_date_list) if buy_sell_points \
           else (total_trades, win_count, cumulative_return)


def run_backtest_for_stock(ticker, market, days=None, target_year=None, df=None, config: MaCrossConfig = None):
    """Fetches the stock's OHLCV + MAs (unless `df` is given) and runs the strategy.

    Returns {"ticker", "trades": [...], "error", "summary": {n_trades, win_count,
    win_rate_pct, cumulative_return_pct}, "df": the polars frame used (None on
    error)} -- summary/df feed the MA Cross tab's KPI strip and chart."""
    cfg = config or MaCrossConfig()
    days = cfg.days if days is None else days
    err = ""
    if df is None:
        df, err = fetch_stock_ma_multi(ticker, market, windows=cfg.windows, days=days, target_year=target_year)
    if err or df is None or df.is_empty():
        return {"ticker": ticker, "trades": [], "error": err or "No data", "summary": _summary(0, 0, 0.0), "df": None}

    res = run_backtest_strategy(df, buy_sell_points=True, target_year=target_year, config=cfg)
    total, wins, cum_ret, _b_dates, b_prices, s_dates, s_prices, bt_buy_dates = res

    trade_list = []
    for i in range(len(s_dates)):
        buy_date = bt_buy_dates[i]
        buy_price = b_prices[i]
        sell_date = s_dates[i]
        sell_price = s_prices[i]
        trade_return = (sell_price - buy_price) / buy_price * 100
        days_held = max(1, int(
            (np.datetime64(sell_date) - np.datetime64(buy_date)) / np.timedelta64(1, 'D')
        ))
        yr_held = days_held / 365.25
        ann_return = ((1 + trade_return / 100) ** (1 / yr_held) - 1) * 100 if yr_held > 0 else trade_return
        trade_list.append({
            "buy_date":   buy_date,
            "buy_price":  buy_price,
            "sell_date":  sell_date,
            "sell_price": sell_price,
            "return_pct": trade_return,
            "days_held":  days_held,
            "ann_return": ann_return,
        })

    return {"ticker": ticker, "trades": trade_list, "error": "", "summary": _summary(total, wins, cum_ret), "df": df}


def _summary(total: int, wins: int, cum_ret: float) -> dict:
    return {
        "n_trades": total,
        "win_count": wins,
        "win_rate_pct": (wins / total * 100.0) if total else 0.0,
        "cumulative_return_pct": cum_ret,
    }


def run_bulk_backtest_chunk(tickers, market, days=None, target_year=None, config: MaCrossConfig = None):
    """Bulk-fetches history via yahooquery and runs the strategy on each ticker."""
    cfg = config or MaCrossConfig()
    days = cfg.days if days is None else days

    def get_yf_symbol(t):
        if market == "KOSPI":
            return str(t).zfill(6) + ".KS"
        elif market == "KOSDAQ":
            return str(t).zfill(6) + ".KQ"
        return str(t).replace(".", "-")

    yf_symbols = [get_yf_symbol(t) for t in tickers]

    if target_year is not None:
        start_date = f"{target_year - 1}-09-01"
        end_date   = f"{target_year}-12-31"
    else:
        start_date = (datetime.now() - timedelta(days=days + 150)).strftime("%Y-%m-%d")
        end_date   = None

    try:
        yq = YQTicker(yf_symbols, asynchronous=False)
        bulk_history = yq.history(start=start_date, end=end_date) if end_date else yq.history(start=start_date)
    except Exception:
        logger.warning("Yahooquery backtest bulk history fetch failed", exc_info=True)
        bulk_history = None

    results = []
    if isinstance(bulk_history, pd.DataFrame) and not bulk_history.empty:
        level_vals = bulk_history.index.get_level_values('symbol')
        for t in tickers:
            df = None
            target_yf = get_yf_symbol(t)
            try:
                if target_yf in level_vals:
                    df_pd = bulk_history.loc[target_yf].copy()
                    df_pd.rename(columns={'open': 'Open', 'high': 'High', 'low': 'Low',
                                          'close': 'Close', 'volume': 'Volume'}, inplace=True)
                    p_df = _to_polars(df_pd)
                    if p_df.height >= cfg.slow_n:
                        df = _compute_indicators(p_df, windows=cfg.windows)
            except Exception:
                logger.debug("Backtest bulk history slice failed for ticker=%s", t, exc_info=True)
            results.append(run_backtest_for_stock(t, market, days=days, target_year=target_year, df=df, config=cfg))
    else:
        for t in tickers:
            results.append(run_backtest_for_stock(t, market, days=days, target_year=target_year, config=cfg))

    del bulk_history
    gc.collect()
    return results
