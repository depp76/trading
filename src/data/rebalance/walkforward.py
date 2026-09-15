"""data/rebalance/walkforward.py — Historical factor series and walk-forward portfolio simulation (trading.md 11-2)."""
import bisect
from datetime import timedelta
import polars as pl

from data.indicators import _compute_indicators
from data.market import get_historical_data
from data.rebalance.factors import _REBALANCE_MIN_FACTORS, _score_and_rank
from data.rebalance.classify import _classify_buy_sell_hold


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
        (pl.col("MA20") / pl.col("MA20").shift(5) - 1).mul(100).alias("ma20_roc_1w"),
    ])
    ind = ind.with_columns(
        ((pl.col("Close") - pl.col("_roll_high_52w")) / pl.col("_roll_high_52w") * 100).alias("high52w_diff")
    )
    return ind.select(["Date", "Close", "MA20_Div", "MA50_Div", "high52w_diff", "ret_20d", "ret_60d", "ma20_roc_1w"])


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
        "ma20_slope_1w": _get("ma20_roc_1w"),
    }


_SNAPSHOT_COLUMNS = {
    "close": "Close",
    "ma20_momentum": "MA20_Div",
    "ma50_momentum": "MA50_Div",
    "high52w_proximity": "high52w_diff",
    "ret_20d": "ret_20d",
    "ret_60d": "ret_60d",
    "ma20_slope_1w": "ma20_roc_1w",
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
    top_n_by_market: dict,
    band_multiplier: float,
    initial_capital: float,
    market_by_ticker: dict = None,
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
    market_by_ticker = market_by_ticker or {}

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
                "ticker": t, "name": t, "market": market_by_ticker.get(t, ""),
                "raw": {
                    "value_per": None,
                    "ma20_momentum": snap["ma20_momentum"],
                    "ma50_momentum": snap["ma50_momentum"],
                    "high52w_proximity": snap["high52w_proximity"],
                    "ret_20d": snap["ret_20d"],
                    "ret_60d": snap["ret_60d"],
                    "ma20_slope_1w": snap["ma20_slope_1w"],
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
        cls = _classify_buy_sell_hold(ranked, current_holdings, top_n_by_market, band_multiplier)

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
