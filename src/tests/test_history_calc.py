"""tests/test_history_calc.py — Pure calculation helpers of TradingHistoryTab
(_compute_pl_fields, _build_monthly_rows), roadmap 6-3b.
"""
import unittest
import datetime as _dt

from PyQt6.QtWidgets import QApplication

app = QApplication.instance() or QApplication([])

from ui.history_tab import TradingHistoryTab  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
