"""data/rebalance.py — Weekly factor scoring, portfolio rebalancing signals, and walk-forward backtesting."""
import bisect
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
import numpy as np
import polars as pl
import logging

from data.indicators import _compute_indicators
from data.market import get_historical_data

logger = logging.getLogger(__name__)

# factor_name -> (extractor(universe_item) -> float | None, higher_is_better)
_REBALANCE_FACTORS = {
    "value_per":         (lambda it: it.get("trailing_per"), False),
    "ma20_momentum":     (lambda it: (it.get("changes") or {}).get("ma20_div"), True),
    "ma50_momentum":     (lambda it: (it.get("changes") or {}).get("ma50_div"), True),
    "high52w_proximity": (lambda it: (it.get("changes") or {}).get("52w_high_diff"), True),
    "ret_20d":           (lambda it: (it.get("changes") or {}).get("20d"), True),
    "ret_60d":           (lambda it: (it.get("changes") or {}).get("60d"), True),
}
_REBALANCE_MIN_FACTORS = 3


def _extract_live_candidates(universe_data: list) -> list:
    """Step 1 of the rebalance pipeline: raw factor extraction from live
    Trading Universe rows. Skips index rows, entries with no ticker, and
    entries with fewer than _REBALANCE_MIN_FACTORS valid factors."""
    candidates = []
    for item in universe_data:
        if item.get("is_index"):
            continue
        ticker = item.get("ticker")
        if not ticker:
            continue
        raw = {}
        for fname, (extractor, _higher_better) in _REBALANCE_FACTORS.items():
            try:
                v = extractor(item)
                if v is not None:
                    v_float = float(v)
                    # Non-positive PER indicates net loss or invalid ratio; treat as None
                    # to prevent loss-making companies from scoring artificially high in value
                    if fname == "value_per" and v_float <= 0:
                        raw[fname] = None
                    else:
                        raw[fname] = v_float
                else:
                    raw[fname] = None
            except (TypeError, ValueError):
                raw[fname] = None
        if sum(1 for v in raw.values() if v is not None) < _REBALANCE_MIN_FACTORS:
            continue
        candidates.append({
            "ticker": ticker,
            "name": item.get("name", ticker),
            "market": item.get("market", ""),
            "raw": raw,
        })
    return candidates


def _score_and_rank(candidates: list) -> list:
    """Steps 2+3 of the rebalance pipeline: z-score each factor across
    `candidates` (sign-flipped so higher is always better), average into one
    composite score per stock, sort, and rank.
    """
    zscores = {fname: {} for fname in _REBALANCE_FACTORS}
    for fname, (_extractor, higher_better) in _REBALANCE_FACTORS.items():
        values = np.array(
            [c["raw"][fname] for c in candidates if c["raw"].get(fname) is not None],
            dtype=float,
        )
        if values.size == 0:
            continue
        mean = float(values.mean())
        std = float(values.std())
        for c in candidates:
            v = c["raw"].get(fname)
            if v is None:
                continue
            z = 0.0 if std == 0 else (v - mean) / std
            zscores[fname][c["ticker"]] = z if higher_better else -z

    ranked = []
    for c in candidates:
        z_vals = [
            zscores[fname][c["ticker"]]
            for fname in _REBALANCE_FACTORS
            if c["ticker"] in zscores[fname]
        ]
        score = float(np.mean(z_vals)) if z_vals else float("-inf")
        ranked.append({
            "ticker": c["ticker"],
            "name": c["name"],
            "market": c["market"],
            "score": score,
            "factors": {fname: zscores[fname].get(c["ticker"]) for fname in _REBALANCE_FACTORS},
            "raw": c["raw"],
        })

    ranked.sort(key=lambda r: r["score"], reverse=True)
    for i, r in enumerate(ranked, start=1):
        r["rank"] = i
    return ranked


def _classify_buy_sell_hold(ranked: list, current_holdings, top_n: int, band_multiplier: float) -> dict:
    """Step 4 of the rebalance pipeline: classify a ranked universe into
    buy/sell/hold given a set of currently-held tickers."""
    current_holdings = set(current_holdings or [])
    rank_by_ticker = {r["ticker"]: r["rank"] for r in ranked}
    sell_threshold_rank = int(top_n * band_multiplier)

    buy_candidates = [
        r for r in ranked if r["rank"] <= top_n and r["ticker"] not in current_holdings
    ]
    hold = [
        r for r in ranked if r["ticker"] in current_holdings and r["rank"] <= sell_threshold_rank
    ]
    sell_candidates = [
        r for r in ranked if r["ticker"] in current_holdings and r["rank"] > sell_threshold_rank
    ]
    for ticker in current_holdings:
        if ticker not in rank_by_ticker:
            sell_candidates.append({
                "ticker": ticker, "name": ticker, "market": "",
                "score": None, "factors": {}, "raw": {}, "rank": None,
            })

    return {
        "sell_threshold_rank": sell_threshold_rank,
        "buy_candidates": buy_candidates,
        "sell_candidates": sell_candidates,
        "hold": hold,
    }


def compute_weekly_rebalance_signals(
    universe_data: list,
    current_holdings=None,
    top_n: int = 20,
    band_multiplier: float = 1.5,
) -> dict:
    """Factor-score and rank the Trading Universe for weekly rebalancing."""
    candidates = _extract_live_candidates(universe_data)
    excluded_count = len(universe_data) - len(candidates)
    ranked = _score_and_rank(candidates)
    classification = _classify_buy_sell_hold(ranked, current_holdings, top_n, band_multiplier)

    return {
        "as_of": datetime.now().strftime("%Y-%m-%d"),
        "top_n": top_n,
        "excluded_count": excluded_count,
        "ranked": ranked,
        **classification,
    }


def _rebalance_friday_dates(start_date, end_date) -> list:
    """Every Friday in [start_date, end_date] (both date objects), inclusive."""
    days_ahead = (4 - start_date.weekday()) % 7
    d = start_date + timedelta(days=days_ahead)
    out = []
    while d <= end_date:
        out.append(d)
        d += timedelta(days=7)
    return out


def _compute_historical_factor_series(ticker: str, start_date: str):
    """One ticker's full historical time series of the 5 price-derived rebalance factors."""
    df = get_historical_data(ticker, start_date)
    if df.is_empty() or df.height < 20:
        return None
    ind = _compute_indicators(df, windows=(20,))
    ind = ind.with_columns([
        pl.col("Close").rolling_max_by("Date", window_size="365d").alias("_roll_high_52w"),
        (pl.col("Close") / pl.col("Close").shift(20) - 1).mul(100).alias("ret_20d"),
        (pl.col("Close") / pl.col("Close").shift(60) - 1).mul(100).alias("ret_60d"),
    ])
    ind = ind.with_columns(
        ((pl.col("Close") - pl.col("_roll_high_52w")) / pl.col("_roll_high_52w") * 100).alias("high52w_diff")
    )
    return ind.select(["Date", "Close", "MA20_Div", "MA50_Div", "high52w_diff", "ret_20d", "ret_60d"])


def _factor_snapshot_as_of(series, as_of_date):
    """The row of `series` at or immediately before as_of_date."""
    sub = series.filter(pl.col("Date") <= as_of_date)
    if sub.is_empty():
        return None
    row = sub.tail(1)

    def _get(col):
        v = row[col][0]
        return float(v) if v is not None else None

    return {
        "close": _get("Close"),
        "ma20_momentum": _get("MA20_Div"),
        "ma50_momentum": _get("MA50_Div"),
        "high52w_proximity": _get("high52w_diff"),
        "ret_20d": _get("ret_20d"),
        "ret_60d": _get("ret_60d"),
    }


_SNAPSHOT_COLUMNS = {
    "close": "Close",
    "ma20_momentum": "MA20_Div",
    "ma50_momentum": "MA50_Div",
    "high52w_proximity": "high52w_diff",
    "ret_20d": "ret_20d",
    "ret_60d": "ret_60d",
}


def _build_snapshot_lookup(series: pl.DataFrame) -> dict:
    """Pre-extract one ticker's series as plain Python lists (Date ascending),
    so the walk-forward simulation's inner date x ticker loop can find the
    as-of row via bisect instead of re-filtering the whole Polars DataFrame
    on every (date, ticker) pair — the dominant cost of run_rebalance_backtest
    for a multi-year, multi-hundred-ticker universe."""
    return {
        "dates": series.get_column("Date").to_list(),
        **{key: series.get_column(col).to_list() for key, col in _SNAPSHOT_COLUMNS.items()},
    }


def _factor_snapshot_at(lookup: dict, as_of_date):
    """Equivalent to _factor_snapshot_as_of, but against a pre-built
    _build_snapshot_lookup() lookup instead of a Polars DataFrame."""
    dates = lookup["dates"]
    idx = bisect.bisect_right(dates, as_of_date) - 1
    if idx < 0:
        return None

    def _get(key):
        v = lookup[key][idx]
        return float(v) if v is not None else None

    return {key: _get(key) for key in _SNAPSHOT_COLUMNS}


def _run_walkforward_simulation(
    series_map: dict,
    rebalance_dates: list,
    top_n: int,
    band_multiplier: float,
    initial_capital: float,
    buy_fee_rate: float = 0.00015,
    sell_fee_rate: float = 0.00015,
    sell_tax_rate: float = 0.0018,
) -> dict:
    """Pure portfolio simulation over precomputed historical factor series with realistic fees and taxes."""
    cash = initial_capital
    shares: dict = {}
    last_price: dict = {}
    entry_price: dict = {}

    equity_curve = []
    trades = []
    rebalance_log = []
    closed_trade_returns = []
    total_cost_paid = 0.0

    # Extracted once per ticker (not once per rebalance date) — see
    # _build_snapshot_lookup's docstring for why this matters.
    lookup_map = {t: _build_snapshot_lookup(series) for t, series in series_map.items()}

    for d in rebalance_dates:
        raw_candidates = []
        prices_today = {}
        for t, lookup in lookup_map.items():
            snap = _factor_snapshot_at(lookup, d)
            if snap is None:
                continue
            if snap["close"] is not None:
                prices_today[t] = snap["close"]
                last_price[t] = snap["close"]
            raw_candidates.append({
                "ticker": t, "name": t, "market": "",
                "raw": {
                    "value_per": None,
                    "ma20_momentum": snap["ma20_momentum"],
                    "ma50_momentum": snap["ma50_momentum"],
                    "high52w_proximity": snap["high52w_proximity"],
                    "ret_20d": snap["ret_20d"],
                    "ret_60d": snap["ret_60d"],
                },
            })
        raw_candidates = [
            c for c in raw_candidates
            if sum(1 for v in c["raw"].values() if v is not None) >= _REBALANCE_MIN_FACTORS
        ]

        def _mark_to_market():
            return cash + sum(
                shares[t] * prices_today.get(t, last_price.get(t, entry_price.get(t, 0.0)))
                for t in shares
            )

        if not raw_candidates:
            equity_curve.append({"date": d.strftime("%Y-%m-%d"), "value": _mark_to_market()})
            continue

        ranked = _score_and_rank(raw_candidates)
        current_holdings = set(shares.keys())
        cls = _classify_buy_sell_hold(ranked, current_holdings, top_n, band_multiplier)

        # -- Execute sells: deduct sell broker commission + securities transaction tax --
        for r in cls["sell_candidates"]:
            t = r["ticker"]
            if t not in shares:
                continue
            px = prices_today.get(t, last_price.get(t))
            if px is None or px <= 0:
                continue
            qty = shares[t]
            gross_sell = qty * px
            fee = gross_sell * sell_fee_rate
            tax = gross_sell * sell_tax_rate
            net_sell = gross_sell - fee - tax
            cash += net_sell
            total_cost_paid += (fee + tax)

            ep = entry_price.get(t)
            if ep and ep > 0:
                eff_entry = ep * (1.0 + buy_fee_rate)
                eff_exit = px * (1.0 - sell_fee_rate - sell_tax_rate)
                net_ret = (eff_exit - eff_entry) / eff_entry * 100.0
                closed_trade_returns.append(net_ret)

            trades.append({
                "date": d.strftime("%Y-%m-%d"),
                "ticker": t,
                "action": "sell",
                "price": px,
                "shares": qty,
                "gross_amount": gross_sell,
                "fee": fee,
                "tax": tax,
                "net_amount": net_sell,
            })
            del shares[t]
            entry_price.pop(t, None)

        # -- Execute buys: allocate available cash evenly with buy commission included --
        buy_list = [r for r in cls["buy_candidates"] if prices_today.get(r["ticker"], 0) > 0]
        if buy_list and cash > 0:
            alloc = cash / len(buy_list)
            for r in buy_list:
                t = r["ticker"]
                px = prices_today[t]
                if px <= 0:
                    continue
                qty = alloc / (px * (1.0 + buy_fee_rate))
                gross_buy = qty * px
                fee = gross_buy * buy_fee_rate
                total_spent = gross_buy + fee

                shares[t] = shares.get(t, 0.0) + qty
                entry_price[t] = px
                cash -= total_spent
                total_cost_paid += fee
                trades.append({
                    "date": d.strftime("%Y-%m-%d"),
                    "ticker": t,
                    "action": "buy",
                    "price": px,
                    "shares": qty,
                    "gross_amount": gross_buy,
                    "fee": fee,
                    "tax": 0.0,
                    "net_amount": total_spent,
                })

        equity_curve.append({"date": d.strftime("%Y-%m-%d"), "value": _mark_to_market()})
        rebalance_log.append({
            "date": d.strftime("%Y-%m-%d"),
            "n_ranked": len(ranked), "n_buy": len(cls["buy_candidates"]),
            "n_sell": len(cls["sell_candidates"]), "n_hold": len(cls["hold"]),
        })

    return {
        "equity_curve": equity_curve,
        "trades": trades,
        "rebalance_log": rebalance_log,
        "closed_trade_returns": closed_trade_returns,
        "total_cost_paid": total_cost_paid,
    }


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
            "max_drawdown_pct": 0.0, "n_rebalances": 0, "n_trades": 0, "win_rate_pct": 0.0,
            "total_cost_amount": 0.0, "total_cost_drag_pct": 0.0,
        }

    final_value = equity_curve[-1]["value"]
    total_return_pct = (final_value / initial_capital - 1) * 100 if initial_capital else 0.0

    benchmark_return_pct = 0.0
    if benchmark_curve:
        benchmark_return_pct = (benchmark_curve[-1]["value"] / initial_capital - 1) * 100

    n_days = max(1, (
        datetime.strptime(equity_curve[-1]["date"], "%Y-%m-%d")
        - datetime.strptime(equity_curve[0]["date"], "%Y-%m-%d")
    ).days)
    years = n_days / 365.25
    cagr_pct = (
        ((final_value / initial_capital) ** (1 / years) - 1) * 100
        if years > 0 and initial_capital > 0 and final_value > 0 else 0.0
    )

    peak = equity_curve[0]["value"]
    max_dd = 0.0
    for pt in equity_curve:
        peak = max(peak, pt["value"])
        if peak > 0:
            max_dd = min(max_dd, (pt["value"] - peak) / peak * 100)

    win_rate_pct = (
        100.0 * sum(1 for r in closed_trade_returns if r > 0) / len(closed_trade_returns)
        if closed_trade_returns else 0.0
    )

    total_cost_drag_pct = (total_cost_paid / initial_capital * 100) if initial_capital else 0.0

    return {
        "total_return_pct": total_return_pct,
        "benchmark_return_pct": benchmark_return_pct,
        "cagr_pct": cagr_pct,
        "max_drawdown_pct": max_dd,
        "n_rebalances": n_rebalances,
        "n_trades": n_trades,
        "win_rate_pct": win_rate_pct,
        "total_cost_amount": total_cost_paid,
        "total_cost_drag_pct": total_cost_drag_pct,
    }


def run_rebalance_backtest(
    tickers: list,
    lookback_years: int = 3,
    top_n: int = 20,
    band_multiplier: float = 1.5,
    initial_capital: float = 100_000_000.0,
    benchmark_ticker: str = "KS11",
    buy_fee_rate: float = 0.00015,
    sell_fee_rate: float = 0.00015,
    sell_tax_rate: float = 0.0018,
    progress_callback=None,
) -> dict:
    """Walk-forward backtest of compute_weekly_rebalance_signals's algorithm with fees and taxes."""
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
        top_n,
        band_multiplier,
        initial_capital,
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
        "top_n": top_n,
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
