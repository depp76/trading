"""strategy/trend_following/engine.py — Event-driven daily simulation of the KR Donchian
20/10 portfolio strategy (trend_following.md 2, 3-2, 3-3).

run_kr_trend(histories, index_df, cfg, start, end, benchmarks) walks one trading
calendar (the union of the input frames' dates inside [start, end]) and, on every day:

  1. fills the orders decided at the previous close at today's open (2-3): exits first,
     then 20% trims, then new buys with whatever cash and slots the sells freed; an order
     whose stock has no bar or zero volume today is cancelled (2-3 "거래정지");
  2. marks the book at the close, and on the year's last trading day runs the 2-5
     harvest at the close (pro-rata sells, withdrawal of the excess over base_capital);
  3. decides tomorrow's orders: exits every day (close < exit channel, or close < the
     trailing stop fixed at yesterday's close), and on the week's last trading day the
     trims and the ranked new entries (2-3 weekly conditions 1-4, 2-4 sizing).

Signals reuse signals.donchian_signal() for the channel/ATR columns (upper, lower, atr,
entry, exit; all no-lookahead); the trailing stop is tracked here per *portfolio*
position because entries are decided weekly, not by the single-instrument state
machine in signals.py. All quantities are whole shares and cash never goes negative.
Daily returns are time-weighted (harvest withdrawals and loss-year top-ups are flows,
not performance), which is what the 3-4 Sharpe is computed from.
"""
from dataclasses import dataclass, field
from datetime import date as _date
import logging
import math

import numpy as np
import polars as pl

from strategy.metrics import calculate_returns_metrics
from strategy.trend_following.annual import annual_summary, plan_year_end_harvest, topup_amount
from strategy.trend_following.config import TrendFollowingConfig
from strategy.trend_following.config_v1 import KrTrendConfig
from strategy.trend_following.signals import donchian_signal
from strategy.trend_following.universe import yearly_members

logger = logging.getLogger(__name__)

_EPS = 1e-9
# The last trading day of a year cannot be recognised inside the data for the final
# year of the run, so that year counts as complete only when its last bar is this late.
_YEAR_END_EARLIEST_DAY = 26


@dataclass
class Position:
    col: int
    qty: int
    entry_i: int
    entry_date: _date
    entry_price: float          # fill price, slippage included
    entry_qty: int
    entry_cost: float           # buy fee/tax on the whole entry (per-share share is pro-rated on partial sells)
    entry_slippage: float
    highest_close: float = math.nan
    stop: float = math.nan      # stop in force for the *next* session (set at the latest close)


@dataclass
class Order:
    kind: str                   # "sell" | "trim" | "buy"
    col: int
    qty: int = 0                # sell/trim
    weight: float = 0.0         # buy
    equity_ref: float = 0.0     # buy: E of 2-4
    reason: str = ""            # sell: "channel" | "stop"; trim: "trim"
    strength: float = 0.0       # buy: ranking score
    decided_i: int = -1


@dataclass
class _YearAcc:
    year: int
    capital_start: float
    topup: float = 0.0
    days: int = 0
    sum_npos: float = 0.0
    sum_cash_pct: float = 0.0
    costs: float = 0.0
    n_skipped_zero_qty: int = 0
    n_skipped_cash: int = 0
    n_skipped_slots: int = 0
    n_cancelled: int = 0
    n_entries: int = 0
    index_level: float = 1.0
    index_peak: float = 1.0
    mdd: float = 0.0
    value_end: float = math.nan
    harvest_costs: float = 0.0
    withdrawal: float = 0.0
    complete: bool = False
    first_date: _date = None
    last_date: _date = None
    trades: list = field(default_factory=list)


def run_kr_trend(histories: dict, index_df: pl.DataFrame = None, cfg: KrTrendConfig = None,
                 start=None, end=None, benchmarks: dict = None) -> dict:
    """Simulate the strategy on `histories` ({code: daily OHLCV polars frame}).

    index_df    KOSPI OHLC frame for the 2-2 market filter (required when cfg.regime_enabled)
    start/end   first/last trading date of the simulation (inclusive; ISO string or date).
                Rows before `start` warm the signals up and feed the yearly selection.
    benchmarks  {"KOSPI": frame, "KOSDAQ": frame} for the 3-3 per-year comparison; index_df is
                used as "KOSPI" when not given explicitly.

    Returns {summary, years, trades, daily, next_orders, positions, members, cfg}:
      summary      annual_summary() + sharpe / annual_vol_pct / total_return_pct of the
                   time-weighted daily returns, passes_risk_gate (3-4), n_trades, counts
      years        one dict per calendar year (3-3 연도별 표)
      trades       closed and open trades, harvest/trim partial sells flagged partial=True
      daily        polars frame: Date, equity, equity_pre_flow, cash, n_positions, exposure,
                   daily_return, regime_ok, flow
      next_orders  the orders decided at the last close (what to do at the next open)
    """
    cfg = cfg or KrTrendConfig()
    if cfg.regime_enabled and (index_df is None or index_df.is_empty()):
        raise ValueError("run_kr_trend: index_df is required while index_regime_ma_n > 0")

    tickers = [t for t, df in histories.items() if df is not None and not df.is_empty() and "Close" in df.columns]
    sigs = {t: _signal_frame(histories[t], cfg) for t in tickers}
    calendar = _calendar(sigs.values(), index_df, start, end)
    n = len(calendar)
    if n == 0 or not tickers:
        return _empty_result(cfg)
    cal = pl.DataFrame({"Date": pl.Series(calendar, dtype=pl.Date)})

    m = len(tickers)
    open_, close, vol, upper, lower, atr = (np.full((n, m), np.nan) for _ in range(6))
    entry = np.zeros((n, m), dtype=bool)
    close_ff = np.full((n, m), np.nan)
    for j, t in enumerate(tickers):
        al = cal.join(sigs[t], on="Date", how="left")
        open_[:, j] = _col(al, "Open")
        close[:, j] = _col(al, "Close")
        vol[:, j] = _col(al, "Volume")
        upper[:, j] = _col(al, "upper")
        lower[:, j] = _col(al, "lower")
        atr[:, j] = _col(al, "atr")
        entry[:, j] = al.get_column("entry").fill_null(False).to_numpy()
        close_ff[:, j] = al.get_column("Close").fill_null(strategy="forward").fill_null(float("nan")).to_numpy()

    regime_ok = _regime_series(cal, index_df, cfg)
    years_in_cal = sorted({d.year for d in calendar})
    col_of = {t: j for j, t in enumerate(tickers)}
    members_by_year = {}
    for y in years_in_cal:
        mem = yearly_members(histories, y, cfg)
        members_by_year[y] = [col_of[t] for t in mem if t in col_of]

    week_key = [d.isocalendar()[:2] for d in calendar]
    is_week_end = np.array([i == n - 1 or week_key[i + 1] != week_key[i] for i in range(n)])
    year_of = np.array([d.year for d in calendar])
    is_year_end = np.array([
        (i < n - 1 and year_of[i + 1] != year_of[i])
        or (i == n - 1 and calendar[i].month == 12 and calendar[i].day >= _YEAR_END_EARLIEST_DAY)
        for i in range(n)
    ])
    bench_returns = _benchmark_year_returns(benchmarks, index_df, years_in_cal)

    cm = cfg.cost_model
    cash = float(cfg.base_capital)
    positions: dict = {}
    pending: list = []
    pending_topup = 0.0
    prev_equity_post = cash
    trades: list = []
    year_accs: dict = {}
    week_start_i = 0
    cum_withdrawal = 0.0
    cum_topup = 0.0

    d_equity = np.zeros(n)
    d_equity_pre = np.zeros(n)
    d_cash = np.zeros(n)
    d_npos = np.zeros(n, dtype=np.int64)
    d_expo = np.zeros(n)
    d_ret = np.zeros(n)
    d_flow = np.zeros(n)

    def holdings_value(i):
        return sum(p.qty * close_ff[i, p.col] for p in positions.values())

    def record_sale(p: Position, i, sold: int, price: float, cost: float, slippage: float, reason: str, partial: bool):
        frac = sold / p.entry_qty
        entry_cost = p.entry_cost * frac
        cost_total = entry_cost + cost
        gross_in = sold * p.entry_price
        pnl = sold * price - gross_in - cost_total
        trades.append(_native({
            "ticker": tickers[p.col], "entry_date": _iso(p.entry_date), "exit_date": _iso(calendar[i]),
            "entry_price": p.entry_price, "exit_price": price, "qty": sold,
            "cost": cost_total, "slippage": p.entry_slippage * frac + slippage,
            "pnl": pnl, "return_pct": pnl / gross_in * 100.0 if gross_in else 0.0,
            "exit_reason": reason, "days_held": int(i - p.entry_i), "partial": partial,
        }))
        year_accs[int(year_of[i])].trades.append(trades[-1])

    for i in range(n):
        today = calendar[i]
        year = int(year_of[i])
        if i == 0 or week_key[i - 1] != week_key[i]:
            week_start_i = i

        # ── year start: top-up (2-5 loss year), new candidate list ─────────────
        if year not in year_accs:
            topup = pending_topup
            pending_topup = 0.0
            if topup > 0:
                cash += topup
                cum_topup += topup
                prev_equity_post += topup
                d_flow[i] += topup
            year_accs[year] = _YearAcc(year=year, capital_start=prev_equity_post, topup=topup, first_date=today)
        acc = year_accs[year]
        members = members_by_year.get(year, [])

        # ── 1. fill yesterday's orders at today's open (2-3) ───────────────────
        if pending and cfg.fill_at == "next_open":
            cash = _execute(pending, i, open_, vol, positions, cash, cfg, acc, record_sale, calendar, tickers)
            pending = []

        # ── 2. mark to market, year-end harvest (2-5) ──────────────────────────
        equity_mark = cash + holdings_value(i)
        withdrawal = 0.0
        if is_year_end[i]:
            acc.value_end = equity_mark
            acc.complete = True
            acc.last_date = today
            if cfg.harvest_mode != "none":
                plan = plan_year_end_harvest(
                    cash, {p.col: (p.qty, close_ff[i, p.col]) for p in positions.values()},
                    cfg.base_capital, cm, cfg.harvest_mode)
                if not plan.is_empty:
                    for col, sold in plan.sells.items():
                        p = positions[col]
                        px = close_ff[i, col]
                        cost = cm.sell_cost(sold * px)
                        record_sale(p, i, sold, px, cost, 0.0, "harvest", partial=sold < p.qty)
                        p.qty -= sold
                        if p.qty <= 0:
                            del positions[col]
                    cash = plan.cash_after
                    withdrawal = plan.withdrawal
                    acc.harvest_costs = plan.costs
                    acc.withdrawal = withdrawal
                    acc.costs += plan.costs
                    cum_withdrawal += withdrawal
                    d_flow[i] -= withdrawal
            pending_topup = topup_amount(acc.value_end, cfg.base_capital, cfg.topup_on_loss)

        # ── 3. decide tomorrow's orders ────────────────────────────────────────
        equity_dec = cash + holdings_value(i)
        orders = _decide(i, positions, close, lower, atr, upper, entry, regime_ok, members, cfg,
                         is_week_end[i], week_start_i, equity_dec)
        if orders and cfg.fill_at == "signal_close":
            cash = _execute(orders, i, close, vol, positions, cash, cfg, acc, record_sale, calendar, tickers)
            orders = []
        _update_stops(i, positions, close, atr, cfg)   # after the fills so a same-close entry gets its stop
        pending = orders

        # ── 4. record the day ──────────────────────────────────────────────────
        equity_end = cash + holdings_value(i)
        equity_pre_flow = equity_end + withdrawal
        r = equity_pre_flow / prev_equity_post - 1.0 if prev_equity_post > 0 else 0.0
        d_ret[i], d_equity[i], d_equity_pre[i], d_cash[i] = r, equity_end, equity_pre_flow, cash
        d_npos[i] = len(positions)
        d_expo[i] = (equity_end - cash) / equity_end if equity_end > 0 else 0.0
        prev_equity_post = equity_end
        acc.days += 1
        acc.sum_npos += len(positions)
        acc.sum_cash_pct += cash / equity_end if equity_end > 0 else 1.0
        acc.index_level *= 1.0 + r
        acc.index_peak = max(acc.index_peak, acc.index_level)
        if acc.index_peak > 0:
            acc.mdd = max(acc.mdd, 1.0 - acc.index_level / acc.index_peak)
        acc.last_date = today

    # ── wrap up ───────────────────────────────────────────────────────────────
    last = n - 1
    for p in positions.values():
        px = close_ff[last, p.col]
        gross_in = p.qty * p.entry_price
        pnl = p.qty * px - gross_in - p.entry_cost * p.qty / p.entry_qty
        trades.append(_native({
            "ticker": tickers[p.col], "entry_date": _iso(p.entry_date), "exit_date": None,
            "entry_price": p.entry_price, "exit_price": px, "qty": p.qty,
            "cost": p.entry_cost * p.qty / p.entry_qty, "slippage": p.entry_slippage * p.qty / p.entry_qty,
            "pnl": pnl, "return_pct": pnl / gross_in * 100.0 if gross_in else 0.0,
            "exit_reason": None, "days_held": int(last - p.entry_i), "partial": False,
        }))
    final_equity = float(d_equity[last])
    for acc in year_accs.values():
        if math.isnan(acc.value_end):
            acc.value_end = d_equity_pre[np.where(year_of == acc.year)[0][-1]]

    years = [_year_row(acc, cfg, bench_returns) for acc in year_accs.values()]
    summary = annual_summary(years, cfg.base_capital, final_equity)
    metrics = calculate_returns_metrics(d_ret, dates=list(calendar), periods_per_year=cfg.trading_days_per_year)
    closed = [t for t in trades if t["exit_date"] is not None and not t["partial"] and t["exit_reason"] in ("channel", "stop")]
    summary.update(_native({
        "sharpe": metrics["sharpe"],
        "annual_vol_pct": metrics["annual_vol_pct"],
        "total_return_pct": metrics["total_return_pct"],     # time-weighted, flows excluded
        "passes_risk_gate": bool(metrics["sharpe"] >= cfg.sharpe_min and summary["max_year_mdd_pct"] <= cfg.mdd_max_pct),
        "n_trades": len(closed),
        "n_open_positions": len(positions),
        "n_partial_sales": sum(1 for t in trades if t["partial"]),
        "n_harvest_sales": sum(1 for t in trades if t["exit_reason"] == "harvest"),
        "n_trim_sales": sum(1 for t in trades if t["exit_reason"] == "trim"),
        "win_rate_pct": _win_rate(closed),
        "profit_factor": _profit_factor(closed),
        "avg_days_held": float(np.mean([t["days_held"] for t in closed])) if closed else 0.0,
        "total_cost": sum(t["cost"] for t in trades),
        "cumulative_withdrawal": cum_withdrawal,
        "cumulative_topup": cum_topup,
        "n_skipped_zero_qty": sum(a.n_skipped_zero_qty for a in year_accs.values()),
        "n_cancelled_orders": sum(a.n_cancelled for a in year_accs.values()),
        "start_date": _iso(calendar[0]), "end_date": _iso(calendar[last]), "n_days": n,
        "n_tickers": m, "label": cfg.label(),
    }))
    daily = pl.DataFrame({
        "Date": pl.Series(calendar, dtype=pl.Date), "equity": d_equity, "equity_pre_flow": d_equity_pre,
        "cash": d_cash, "n_positions": d_npos, "exposure": d_expo, "daily_return": d_ret,
        "regime_ok": regime_ok, "flow": d_flow,
    })
    return {
        "summary": summary, "years": years, "trades": trades, "daily": daily,
        "next_orders": [_order_dict(o, tickers) for o in pending],
        "positions": [_native({"ticker": tickers[p.col], "qty": p.qty, "entry_date": _iso(p.entry_date),
                               "entry_price": p.entry_price, "stop": None if math.isnan(p.stop) else p.stop})
                      for p in positions.values()],
        "members": {y: [tickers[c] for c in cols] for y, cols in members_by_year.items()},
        "cfg": cfg,
    }


# ── decision / execution ─────────────────────────────────────────────────────

def _decide(i, positions, close, lower, atr, upper, entry, regime_ok, members, cfg: KrTrendConfig,
            week_end: bool, week_start_i: int, equity: float) -> list:
    """Orders for the next fill, decided at the close of day i (2-3 table, 2-4)."""
    orders = []
    exiting = set()
    if cfg.exit_check == "daily" or week_end:
        for col, p in positions.items():
            c = close[i, col]
            if math.isnan(c):
                continue                                   # no bar today: nothing new to act on
            lo = lower[i, col]
            if not math.isnan(lo) and c < lo:
                orders.append(Order("sell", col, qty=p.qty, reason="channel", decided_i=i))
                exiting.add(col)
            elif cfg.stop_enabled and not math.isnan(p.stop) and c < p.stop:
                orders.append(Order("sell", col, qty=p.qty, reason="stop", decided_i=i))
                exiting.add(col)

    if week_end:                                           # weekly trim (2-4 "보유 중 조정")
        for col, p in positions.items():
            if col in exiting:
                continue
            c = close[i, col]
            if math.isnan(c) or equity <= 0:
                continue
            if p.qty * c / equity > cfg.max_position_weight + _EPS:
                target = int(math.floor(equity * cfg.max_position_weight / c))
                if p.qty - target > 0:
                    orders.append(Order("trim", col, qty=p.qty - target, reason="trim", decided_i=i))

    entries_today = week_end if cfg.entry_check == "weekly" else True
    if entries_today and regime_ok[i]:
        slots = cfg.max_positions - (len(positions) - len(exiting))
        if slots > 0:
            cands = []
            weekly_window = cfg.entry_check == "weekly" and cfg.weekly_entry_rule == "held_breakout"
            for col in members:
                if col in positions:
                    continue
                c, lo, a = close[i, col], lower[i, col], atr[i, col]
                if math.isnan(c) or math.isnan(lo) or math.isnan(a) or a <= 0 or c <= 0:
                    continue
                if c < lo:                                 # condition 2: already in exit territory
                    continue
                if weekly_window:                          # condition 1: broke out this week and still above that upper
                    win = entry[week_start_i:i + 1, col]
                    if not win.any():
                        continue
                    d = week_start_i + int(np.argmax(win))
                    ref = upper[d, col]
                else:                                      # strict / daily: close above today's upper
                    if not entry[i, col]:
                        continue
                    ref = upper[i, col]
                if math.isnan(ref) or c <= ref:
                    continue
                cands.append((float((c - ref) / a), col))
            cands.sort(key=lambda s: (-s[0], s[1]))
            for strength, col in cands[:slots]:
                orders.append(Order("buy", col, weight=cfg.position_weight(close[i, col], atr[i, col]),
                                    equity_ref=equity, strength=strength, decided_i=i))
    return orders


def _update_stops(i, positions, close, atr, cfg: KrTrendConfig):
    """Trailing stop for the next session: highest close since entry - mult * ATR, only ever
    raised (2-2). Positions with no bar today keep yesterday's stop."""
    if not cfg.stop_enabled:
        return
    for p in positions.values():
        c = close[i, p.col]
        if math.isnan(c):
            continue
        p.highest_close = c if math.isnan(p.highest_close) else max(p.highest_close, c)
        a = atr[i, p.col]
        if math.isnan(a):
            continue
        new_stop = p.highest_close - cfg.stop_atr_mult * a
        p.stop = new_stop if math.isnan(p.stop) else max(p.stop, new_stop)


def _execute(orders, i, price_mat, vol, positions, cash, cfg: KrTrendConfig, acc: _YearAcc,
             record_sale, calendar, tickers) -> float:
    """Fill `orders` at price_mat[i] (open or close) in the 2-3 order: sells, trims, buys.
    A stock with no price or zero volume today has its order cancelled."""
    cm, slip = cfg.cost_model, cfg.slippage_rate
    rank = {"sell": 0, "trim": 1, "buy": 2}
    for o in sorted(orders, key=lambda o: (rank[o.kind], -o.strength)):
        px_raw = price_mat[i, o.col]
        v = vol[i, o.col]
        if math.isnan(px_raw) or px_raw <= 0 or math.isnan(v) or v <= 0:
            acc.n_cancelled += 1
            continue
        if o.kind in ("sell", "trim"):
            p = positions.get(o.col)
            if p is None:
                continue
            qty = min(o.qty, p.qty)
            if qty <= 0:
                continue
            fill = px_raw * (1.0 - slip)
            gross = qty * fill
            cost = cm.sell_cost(gross)
            slippage = qty * px_raw * slip
            cash += gross - cost
            acc.costs += cost + slippage
            record_sale(p, i, qty, fill, cost, slippage, o.reason, partial=qty < p.qty)
            p.qty -= qty
            if p.qty <= 0:
                del positions[o.col]
        else:
            if o.col in positions:
                continue
            if len(positions) >= cfg.max_positions:
                acc.n_skipped_slots += 1
                continue
            fill = px_raw * (1.0 + slip)
            qty = int(math.floor(o.equity_ref * o.weight / fill))
            if qty <= 0:
                acc.n_skipped_zero_qty += 1
                continue
            per_share = fill * (1.0 + cm.buy_fee_rate + cm.slippage_rate)
            affordable = int(math.floor(cash / per_share)) if per_share > 0 else 0
            if affordable <= 0:
                acc.n_skipped_cash += 1
                continue
            qty = min(qty, affordable)
            gross = qty * fill
            cost = cm.buy_cost(gross)
            slippage = qty * px_raw * slip
            cash -= gross + cost
            acc.costs += cost + slippage
            acc.n_entries += 1
            positions[o.col] = Position(col=o.col, qty=qty, entry_i=i, entry_date=calendar[i], entry_price=fill,
                                        entry_qty=qty, entry_cost=cost, entry_slippage=slippage)
    return cash


# ── inputs ───────────────────────────────────────────────────────────────────

def _signal_frame(df: pl.DataFrame, cfg: KrTrendConfig) -> pl.DataFrame:
    """donchian_signal() channel/ATR columns on a normalised OHLCV frame. A frame without
    Open falls back to Close (logged); one without Volume assumes the stock traded."""
    out = df.sort("Date").with_columns(pl.col("Date").cast(pl.Date))
    if "Open" not in out.columns:
        logger.warning("run_kr_trend: frame has no Open column; filling opens with closes")
        out = out.with_columns(pl.col("Close").alias("Open"))
    if "Volume" not in out.columns:
        out = out.with_columns(pl.lit(1.0).alias("Volume"))
    sig = donchian_signal(out, TrendFollowingConfig(entry_n=cfg.entry_n, exit_n=cfg.exit_n, atr_n=cfg.atr_n))
    return sig.select(["Date", "Open", "High", "Low", "Close", "Volume", "upper", "lower", "atr", "entry", "exit"])


def _calendar(frames, index_df, start, end) -> list:
    dates = set()
    for f in frames:
        dates.update(f.get_column("Date").to_list())
    if index_df is not None and not index_df.is_empty():
        dates.update(index_df.sort("Date").get_column("Date").cast(pl.Date).to_list())
    start = _to_date(start) if start else None
    end = _to_date(end) if end else None
    return sorted(d for d in dates if (start is None or d >= start) and (end is None or d <= end))


def _regime_series(cal: pl.DataFrame, index_df, cfg: KrTrendConfig) -> np.ndarray:
    """2-2 market filter per calendar day: KOSPI close > SMA(n) (False while the SMA is
    not yet defined). Missing index days carry the latest known verdict forward."""
    n = cal.height
    if not cfg.regime_enabled:
        return np.ones(n, dtype=bool)
    idx = (index_df.sort("Date").with_columns(pl.col("Date").cast(pl.Date))
           .filter(pl.col("Close").is_not_null())
           .with_columns(pl.col("Close").cast(pl.Float64).rolling_mean(window_size=cfg.index_regime_ma_n).alias("_sma"))
           .with_columns((pl.col("Close") > pl.col("_sma")).fill_null(False).alias("_ok"))
           .select(["Date", "_ok"]))
    al = cal.join(idx, on="Date", how="left").with_columns(pl.col("_ok").fill_null(strategy="forward").fill_null(False))
    return al.get_column("_ok").to_numpy().astype(bool)


def _benchmark_year_returns(benchmarks, index_df, years) -> dict:
    """{name: {year: pct}} from the last close of each year vs. the previous year's last close."""
    frames = dict(benchmarks or {})
    if index_df is not None and "KOSPI" not in frames:
        frames["KOSPI"] = index_df
    out = {}
    for name, df in frames.items():
        if df is None or df.is_empty():
            continue
        d = df.sort("Date").with_columns(pl.col("Date").cast(pl.Date)).filter(pl.col("Close").is_not_null())
        dates = d.get_column("Date").to_list()
        closes = d.get_column("Close").cast(pl.Float64).to_list()
        per_year = {}
        for y in years:
            last_prev = None
            last_this = None
            first_this = None
            for dt, c in zip(dates, closes):
                if dt.year < y:
                    last_prev = c
                elif dt.year == y:
                    last_this = c
                    if first_this is None:
                        first_this = c
                else:
                    break
            base = last_prev if last_prev is not None else first_this
            if base and last_this is not None:
                per_year[y] = (last_this / base - 1.0) * 100.0
        out[name] = per_year
    return out


# ── reporting ────────────────────────────────────────────────────────────────

def _year_row(acc: _YearAcc, cfg: KrTrendConfig, bench: dict) -> dict:
    closed = [t for t in acc.trades if not t["partial"] and t["exit_reason"] in ("channel", "stop")]
    base = acc.capital_start if acc.capital_start > 0 else cfg.base_capital
    ret = (acc.value_end / base - 1.0) * 100.0
    ret_net = ((acc.value_end - acc.harvest_costs) / base - 1.0) * 100.0
    kospi = bench.get("KOSPI", {}).get(acc.year)
    kosdaq = bench.get("KOSDAQ", {}).get(acc.year)
    return _native({
        "year": acc.year, "complete": acc.complete,
        "start_date": _iso(acc.first_date), "end_date": _iso(acc.last_date),
        "capital_start": acc.capital_start, "value_end": acc.value_end,
        "return_pct": ret, "return_net_pct": ret_net,
        "withdrawal": acc.withdrawal, "topup": acc.topup,
        "mdd_pct": acc.mdd * 100.0,
        "n_trades": len(closed), "n_entries": acc.n_entries,
        "win_rate_pct": _win_rate(closed), "profit_factor": _profit_factor(closed),
        "avg_days_held": float(np.mean([t["days_held"] for t in closed])) if closed else 0.0,
        "avg_n_positions": acc.sum_npos / acc.days if acc.days else 0.0,
        "avg_cash_pct": acc.sum_cash_pct / acc.days * 100.0 if acc.days else 100.0,
        "total_cost": acc.costs,
        "n_skipped_zero_qty": acc.n_skipped_zero_qty, "n_skipped_cash": acc.n_skipped_cash,
        "n_skipped_slots": acc.n_skipped_slots, "n_cancelled": acc.n_cancelled,
        "n_channel_exits": sum(1 for t in closed if t["exit_reason"] == "channel"),
        "n_stop_exits": sum(1 for t in closed if t["exit_reason"] == "stop"),
        "n_harvest_sales": sum(1 for t in acc.trades if t["exit_reason"] == "harvest"),
        "n_trim_sales": sum(1 for t in acc.trades if t["exit_reason"] == "trim"),
        "kospi_return_pct": kospi, "kosdaq_return_pct": kosdaq,
        "excess_vs_kospi_pct": (ret - kospi) if kospi is not None else None,
        "n_days": acc.days,
    })


def _win_rate(closed) -> float:
    return sum(1 for t in closed if t["pnl"] > 0) / len(closed) * 100.0 if closed else 0.0


def _profit_factor(closed) -> float:
    gains = sum(t["pnl"] for t in closed if t["pnl"] > 0)
    losses = -sum(t["pnl"] for t in closed if t["pnl"] < 0)
    if losses > 0:
        return gains / losses
    return 99.0 if gains > 0 else 0.0


def _native(d: dict) -> dict:
    """numpy scalars -> Python numbers, so results are JSON-friendly and print cleanly."""
    out = {}
    for k, v in d.items():
        if isinstance(v, np.floating):
            v = float(v)
        elif isinstance(v, np.integer):
            v = int(v)
        elif isinstance(v, np.bool_):
            v = bool(v)
        out[k] = v
    return out


def _order_dict(o: Order, tickers) -> dict:
    return {"kind": o.kind, "ticker": tickers[o.col], "qty": o.qty, "weight": o.weight,
            "reason": o.reason, "strength": o.strength}


def _empty_result(cfg: KrTrendConfig) -> dict:
    summary = annual_summary([], cfg.base_capital, cfg.base_capital)
    summary.update({"sharpe": 0.0, "annual_vol_pct": 0.0, "total_return_pct": 0.0, "passes_risk_gate": False,
                    "n_trades": 0, "n_open_positions": 0, "n_partial_sales": 0, "n_harvest_sales": 0,
                    "n_trim_sales": 0, "win_rate_pct": 0.0, "profit_factor": 0.0, "avg_days_held": 0.0,
                    "total_cost": 0.0, "n_skipped_zero_qty": 0, "n_cancelled_orders": 0,
                    "start_date": None, "end_date": None, "n_days": 0, "n_tickers": 0, "label": cfg.label()})
    return {"summary": summary, "years": [], "trades": [], "daily": pl.DataFrame(), "next_orders": [],
            "positions": [], "members": {}, "cfg": cfg}


def _col(al: pl.DataFrame, name: str) -> np.ndarray:
    return al.get_column(name).cast(pl.Float64).fill_null(float("nan")).to_numpy()


def _to_date(d):
    if isinstance(d, _date):
        return d
    return _date.fromisoformat(str(d)[:10])


def _iso(d) -> str:
    if d is None:
        return None
    if isinstance(d, _date):
        return d.strftime("%Y-%m-%d")
    return str(d)[:10]
