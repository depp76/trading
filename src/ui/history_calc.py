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

    all_rows: [(kind, rec), ...] already sorted by buy_date ascending.
    The summary carries the month total buy amount and the realized (pl)
    + unrealized (curr_pl) P/L of that month's positions. Pure function so
    it can be unit-tested without a widget (roadmap 6-3b).
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
