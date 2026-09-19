"""ui/history_calc.py — Pure P/L maths for the Trading History tab (no Qt).

Split out of TradingHistoryTab on 2026-09-17; the class keeps thin delegates
(_compute_pl_fields / _build_monthly_rows) for existing callers and tests.
"""
import datetime as _dt
import logging

logger = logging.getLogger(__name__)


def compute_pl_fields(rec: dict):
    """Recalculate pl, pl_pct, days_held and curr_days in-place."""
    s_amt = rec.get("sell_amount", 0.0)
    b_amt = rec.get("buy_amount", 0.0)
    s_qty = rec.get("sell_qty", 0.0)
    b_qty = rec.get("qty", 0.0)

    if s_qty > 0 and b_qty > 0 and s_qty < b_qty:
        prorated_b_amt = b_amt * (s_qty / b_qty)
        rec["pl"]     = s_amt - prorated_b_amt if (s_amt > 0 and prorated_b_amt > 0) else 0.0
        rec["pl_pct"] = (rec["pl"] / prorated_b_amt * 100) if prorated_b_amt > 0 else 0.0
    else:
        rec["pl"]     = s_amt - b_amt if (s_amt > 0 and b_amt > 0) else 0.0
        rec["pl_pct"] = (rec["pl"] / b_amt * 100) if b_amt > 0 else 0.0
    try:
        bd = _dt.datetime.strptime(rec.get("buy_date", ""), "%Y-%m-%d").date()
        sd_str = rec.get("sell_date", "")
        if sd_str:
            sd = _dt.datetime.strptime(sd_str, "%Y-%m-%d").date()
            rec["days_held"] = (sd - bd).days
            rec["curr_days"] = 0
        else:
            rec["days_held"] = 0
            rec["curr_days"] = (_dt.date.today() - bd).days
    except Exception:
        logger.debug(
            "Days-held calculation failed for company=%s buy_date=%s",
            rec.get("company"), rec.get("buy_date"), exc_info=True,
        )


def build_monthly_rows(all_rows: list):
    """Insert a ("monthly", summary_rec) row after each buy-month group.

    all_rows: [(kind, rec), ...] already sorted by buy_date (ascending or
    descending -- rows are grouped by the month of the first row seen for it,
    so either direction keeps each month's rows contiguous). The summary
    carries the month total buy amount and the realized (pl) + unrealized
    (curr_pl) P/L of that month's positions. Pure function so it can be
    unit-tested without a widget (roadmap 6-3b).
    """
    rows = []
    month_groups: dict = {}
    for kind, rec in all_rows:
        b_date = rec.get("buy_date", "")
        month = str(b_date)[:7] if b_date else "Unknown"
        month_groups.setdefault(month, []).append((kind, rec))

    for month, m_rows in month_groups.items():
        total_buy = 0.0
        total_pl = 0.0
        for k, r in m_rows:
            rows.append((k, r))
            b_amt = r.get("buy_amount")
            if b_amt:
                total_buy += float(b_amt)
            pl = r.get("pl", 0.0)
            curr_pl = r.get("curr_pl", 0.0)
            total_pl += (float(pl) if pl else 0.0) + (float(curr_pl) if curr_pl else 0.0)

        rows.append(("monthly", {
            "company": f"Monthly Summary [{month}]",
            "buy_date": month,
            "buy_amount": total_buy,
            "sell_date": "",
            "sell_amount": 0,
            "pl": total_pl,
            "pl_pct": 0.0,
            "sell_price": 0, "buy_price": 0, "qty": 0, "sell_qty": 0, "days_held": 0, "curr_days": 0,
        }))
    return rows


def _parse_ymd(s, cache: dict):
    """Parse a 'YYYY-MM-DD...' string (memoised); None for blank or malformed."""
    if not s:
        return None
    if s not in cache:
        try:
            cache[s] = _dt.datetime.strptime(s[:10], "%Y-%m-%d").date()
        except Exception:
            cache[s] = None
    return cache[s]


def summarize_positions(open_data: list, closed_data: list, *, deposit: float,
                        withdrawal: float, principal: float, today=None) -> dict:
    """Aggregate the Trading History positions for the dashboard cards.

    Mutates the records in place the same way the tab used to:
      * every record gets `curr_days` (days since sell for closed rows, days
        held for open rows);
      * open records get `curr_pl` / `curr_pl_pct` from `curr_price * qty`
        (zero while no price has arrived), plus `position_w` (evaluation
        weight in total invested capital) and `curr_pct_pl`;
      * closed records get `position_w = curr_pct_pl = 0`.

    Returns a dict with the KR / US / total cost, evaluation, P/L and P/L %
    figures, the total asset value and its P/L versus `principal`, and the
    deposit ratio. Pure function (no Qt) so it is unit-testable; the tab only
    renders the result.
    """
    from data.cache import is_us_market

    today = today or _dt.date.today()
    date_cache: dict = {}
    for r in closed_data + open_data:
        sell_dt = _parse_ymd(r.get("sell_date", ""), date_cache)
        if sell_dt:
            r["curr_days"] = (today - sell_dt).days
        else:
            buy_dt = _parse_ymd(r.get("buy_date", ""), date_cache)
            if buy_dt:
                r["curr_days"] = (today - buy_dt).days

    kr_cost = kr_eval = us_cost = us_eval = 0.0
    for r in open_data:
        buy_amt = r.get("buy_amount", 0.0)
        qty = r.get("qty", 0.0)
        price = r.get("curr_price", 0.0)
        if price > 0 and qty > 0:
            eval_val = price * qty
            r["curr_pl"] = eval_val - buy_amt
            r["curr_pl_pct"] = (r["curr_pl"] / buy_amt * 100) if buy_amt else 0.0
        else:
            eval_val = buy_amt          # no quote yet: carry at cost
            r["curr_pl"] = 0.0
            r["curr_pl_pct"] = 0.0
        if is_us_market(r.get("market", "")):
            us_cost += buy_amt
            us_eval += eval_val
        else:
            kr_cost += buy_amt
            kr_eval += eval_val

    cost_total = kr_cost + us_cost
    eval_total = kr_eval + us_eval

    def _pct(num, den):
        return (num / den * 100) if den else 0.0

    total = eval_total + deposit + withdrawal
    total_pl = total - principal
    total_invest = total - withdrawal

    for r in open_data:
        qty = r.get("qty", 0.0)
        price = r.get("curr_price", 0.0)
        ev = (price * qty) if price > 0 and qty > 0 else r.get("buy_amount", 0.0)
        r["position_w"] = _pct(ev, total_invest) if total_invest > 0 else 0.0
        r["curr_pct_pl"] = r["curr_pl_pct"] * (r["position_w"] / 100.0) if r["position_w"] else 0.0
    for r in closed_data:
        r["position_w"] = 0.0
        r["curr_pct_pl"] = 0.0

    return {
        "kr_cost": kr_cost, "kr_pl": kr_eval - kr_cost, "kr_pl_pct": _pct(kr_eval - kr_cost, kr_cost),
        "us_cost": us_cost, "us_pl": us_eval - us_cost, "us_pl_pct": _pct(us_eval - us_cost, us_cost),
        "cost_total": cost_total, "eval_total": eval_total,
        "pos_pl": eval_total - cost_total, "pos_pl_pct": _pct(eval_total - cost_total, cost_total),
        "total": total, "total_pl": total_pl,
        "total_pl_pct": _pct(total_pl, principal) if principal > 0 else 0.0,
        "total_invest": total_invest,
        "deposit_pct": _pct(deposit, total_invest) if total_invest > 0 else 0.0,
    }
