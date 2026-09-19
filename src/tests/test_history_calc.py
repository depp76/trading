"""tests/test_history_calc.py — Pure calculation helpers of TradingHistoryTab
(_compute_pl_fields, _build_monthly_rows), roadmap 6-3b.
"""
import unittest
import datetime as _dt

from PyQt6.QtWidgets import QApplication

app = QApplication.instance() or QApplication([])

from ui.history_tab import TradingHistoryTab  # noqa: E402
from ui.history_calc import summarize_positions  # noqa: E402


def _rec(**kw):
    base = {
        "company": "X", "buy_date": "2026-01-10", "buy_price": 100.0, "qty": 10.0,
        "buy_amount": 1000.0, "sell_date": "", "sell_price": 0.0, "sell_qty": 0.0,
        "sell_amount": 0.0,
    }
    base.update(kw)
    return base


class TestComputePlFields(unittest.TestCase):

    def test_full_sale_pl_and_days(self):
        rec = _rec(sell_date="2026-02-09", sell_price=120.0, sell_qty=10.0, sell_amount=1200.0)
        TradingHistoryTab._compute_pl_fields(rec)
        self.assertAlmostEqual(rec["pl"], 200.0)
        self.assertAlmostEqual(rec["pl_pct"], 20.0)
        self.assertEqual(rec["days_held"], 30)
        self.assertEqual(rec["curr_days"], 0)

    def test_partial_sale_prorates_cost_basis(self):
        # Sold 4 of 10 shares: cost basis for P/L is 40% of the buy amount.
        rec = _rec(sell_date="2026-02-09", sell_price=150.0, sell_qty=4.0, sell_amount=600.0)
        TradingHistoryTab._compute_pl_fields(rec)
        self.assertAlmostEqual(rec["pl"], 600.0 - 400.0)
        self.assertAlmostEqual(rec["pl_pct"], 50.0)

    def test_open_position_counts_days_from_buy(self):
        buy = (_dt.date.today() - _dt.timedelta(days=12)).strftime("%Y-%m-%d")
        rec = _rec(buy_date=buy)
        TradingHistoryTab._compute_pl_fields(rec)
        self.assertEqual(rec["pl"], 0.0)
        self.assertEqual(rec["days_held"], 0)
        self.assertEqual(rec["curr_days"], 12)

    def test_zero_amounts_give_zero_pl(self):
        rec = _rec(buy_amount=0.0, sell_date="2026-02-09", sell_amount=0.0)
        TradingHistoryTab._compute_pl_fields(rec)
        self.assertEqual(rec["pl"], 0.0)
        self.assertEqual(rec["pl_pct"], 0.0)

    def test_bad_date_does_not_raise(self):
        rec = _rec(buy_date="not-a-date")
        TradingHistoryTab._compute_pl_fields(rec)  # must not raise
        self.assertNotIn("days_held", rec)


class TestBuildMonthlyRows(unittest.TestCase):

    def test_summary_row_after_each_month(self):
        rows = [
            ("closed", _rec(buy_date="2026-01-05", buy_amount=1000.0, pl=100.0)),
            ("closed", _rec(buy_date="2026-01-20", buy_amount=500.0, pl=-50.0)),
            ("open",   _rec(buy_date="2026-02-03", buy_amount=800.0, curr_pl=40.0)),
        ]
        out = TradingHistoryTab._build_monthly_rows(rows)
        kinds = [k for k, _ in out]
        self.assertEqual(kinds, ["closed", "closed", "monthly", "open", "monthly"])

        jan = out[2][1]
        self.assertEqual(jan["buy_date"], "2026-01")
        self.assertAlmostEqual(jan["buy_amount"], 1500.0)
        self.assertAlmostEqual(jan["pl"], 50.0)

        feb = out[4][1]
        self.assertEqual(feb["buy_date"], "2026-02")
        self.assertAlmostEqual(feb["buy_amount"], 800.0)
        self.assertAlmostEqual(feb["pl"], 40.0)  # unrealized counts too

    def test_missing_buy_date_goes_to_unknown_group(self):
        out = TradingHistoryTab._build_monthly_rows([("open", _rec(buy_date=""))])
        self.assertEqual(out[1][0], "monthly")
        self.assertEqual(out[1][1]["buy_date"], "Unknown")

    def test_empty_input(self):
        self.assertEqual(TradingHistoryTab._build_monthly_rows([]), [])


class TestSummarizePositions(unittest.TestCase):
    TODAY = _dt.date(2026, 9, 19)

    def _run(self, open_data, closed_data=(), deposit=0.0, withdrawal=0.0, principal=0.0):
        return summarize_positions(open_data, list(closed_data), deposit=deposit,
                                   withdrawal=withdrawal, principal=principal, today=self.TODAY)

    def test_kr_us_split_and_totals(self):
        kr = _rec(market="KOSPI", buy_amount=1000.0, qty=10.0, curr_price=120.0)   # eval 1200
        us = _rec(market="NASDAQ", buy_amount=2000.0, qty=4.0, curr_price=450.0)   # eval 1800
        agg = self._run([kr, us], deposit=500.0, withdrawal=100.0, principal=3000.0)

        self.assertAlmostEqual(agg["kr_cost"], 1000.0)
        self.assertAlmostEqual(agg["kr_pl"], 200.0)
        self.assertAlmostEqual(agg["kr_pl_pct"], 20.0)
        self.assertAlmostEqual(agg["us_cost"], 2000.0)
        self.assertAlmostEqual(agg["us_pl"], -200.0)
        self.assertAlmostEqual(agg["us_pl_pct"], -10.0)
        self.assertAlmostEqual(agg["cost_total"], 3000.0)
        self.assertAlmostEqual(agg["eval_total"], 3000.0)
        self.assertAlmostEqual(agg["pos_pl"], 0.0)
        # total = eval + deposit + withdrawal
        self.assertAlmostEqual(agg["total"], 3600.0)
        self.assertAlmostEqual(agg["total_pl"], 600.0)
        self.assertAlmostEqual(agg["total_pl_pct"], 20.0)
        self.assertAlmostEqual(agg["total_invest"], 3500.0)
        self.assertAlmostEqual(agg["deposit_pct"], 500.0 / 3500.0 * 100)

        # per-row fields
        self.assertAlmostEqual(kr["curr_pl"], 200.0)
        self.assertAlmostEqual(kr["curr_pl_pct"], 20.0)
        self.assertAlmostEqual(kr["position_w"], 1200.0 / 3500.0 * 100)
        self.assertAlmostEqual(kr["curr_pct_pl"], 20.0 * kr["position_w"] / 100.0)

    def test_sp500_label_counts_as_us(self):
        # Regression: the price thread and the summary used to disagree on this label.
        row = _rec(market="S&P500", buy_amount=1000.0, qty=1.0, curr_price=1100.0)
        agg = self._run([row])
        self.assertAlmostEqual(agg["us_cost"], 1000.0)
        self.assertAlmostEqual(agg["kr_cost"], 0.0)

    def test_missing_price_carries_position_at_cost(self):
        row = _rec(market="KOSPI", buy_amount=1000.0, qty=10.0, curr_price=0.0)
        agg = self._run([row], principal=1000.0)
        self.assertEqual(row["curr_pl"], 0.0)
        self.assertEqual(row["curr_pl_pct"], 0.0)
        self.assertAlmostEqual(agg["eval_total"], 1000.0)
        self.assertAlmostEqual(agg["total_pl"], 0.0)
        self.assertAlmostEqual(row["position_w"], 100.0)

    def test_curr_days_from_sell_date_or_buy_date(self):
        closed = _rec(buy_date="2026-01-01", sell_date="2026-09-09", sell_price=1.0)
        opened = _rec(buy_date="2026-09-01")
        bad = _rec(buy_date="garbage")
        self._run([opened, bad], [closed])
        self.assertEqual(closed["curr_days"], 10)
        self.assertEqual(opened["curr_days"], 18)
        self.assertNotIn("curr_days", bad)
        self.assertEqual(closed["position_w"], 0.0)
        self.assertEqual(closed["curr_pct_pl"], 0.0)

    def test_zero_denominators_do_not_raise(self):
        agg = self._run([], [], deposit=0.0, withdrawal=0.0, principal=0.0)
        self.assertEqual(agg["total"], 0.0)
        self.assertEqual(agg["total_pl_pct"], 0.0)
        self.assertEqual(agg["deposit_pct"], 0.0)
        self.assertEqual(agg["pos_pl_pct"], 0.0)


if __name__ == "__main__":
    unittest.main()
