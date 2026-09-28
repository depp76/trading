"""tests/strategy/trend_following/test_annual.py — year-end harvest, loss-year top-up
and the cumulative summary (trend_following.md 2-5, 3-3)."""
import unittest

from strategy.costs import KRX_STOCK_COST, TransactionCostModel
from strategy.trend_following import annual_summary, plan_year_end_harvest, topup_amount

BASE = 10_000_000.0
NO_COST = TransactionCostModel()


class TestPlanYearEndHarvest(unittest.TestCase):

    def test_pro_rata_quantities_withdrawal_and_rounding_residue(self):
        # cash 2M + A 100 x 50,000 (5M) + B 60 x 100,000 (6M) = 13M -> X = 3M, f = 3/13
        holdings = {"A": (100, 50_000.0), "B": (60, 100_000.0)}
        plan = plan_year_end_harvest(2_000_000.0, holdings, BASE, KRX_STOCK_COST, "pro_rata")
        self.assertAlmostEqual(plan.value, 13_000_000.0)
        self.assertAlmostEqual(plan.excess, 3_000_000.0)
        self.assertEqual(plan.sells, {"A": 23, "B": 14})            # round(23.08), round(13.85)
        gross = 23 * 50_000 + 14 * 100_000
        costs = gross * (0.00015 + 0.0018)
        self.assertAlmostEqual(plan.gross, gross)
        self.assertAlmostEqual(plan.costs, costs)
        self.assertAlmostEqual(plan.withdrawal, 3_000_000.0 - costs)
        # the portfolio is worth exactly base afterwards; rounding sits in cash
        remaining = (100 - 23) * 50_000 + (60 - 14) * 100_000
        self.assertAlmostEqual(plan.cash_after + remaining, BASE)
        self.assertAlmostEqual(plan.cash_after, 1_550_000.0)

    def test_no_excess_is_empty(self):
        plan = plan_year_end_harvest(1_000_000.0, {"A": (100, 50_000.0)}, BASE, KRX_STOCK_COST)
        self.assertTrue(plan.is_empty)
        self.assertEqual(plan.sells, {})
        self.assertEqual(plan.withdrawal, 0.0)
        self.assertEqual(plan.cash_after, 1_000_000.0)

    def test_mode_none_never_sells(self):
        plan = plan_year_end_harvest(5_000_000.0, {"A": (200, 50_000.0)}, BASE, KRX_STOCK_COST, "none")
        self.assertTrue(plan.is_empty)
        self.assertEqual(plan.excess, 0.0)

    def test_cash_first_takes_cash_before_stock(self):
        holdings = {"A": (100, 50_000.0), "B": (60, 100_000.0)}
        plan = plan_year_end_harvest(4_000_000.0, holdings, BASE, NO_COST, "cash_first")     # V = 15M, X = 5M
        # 4M from cash, 1M / 11M of each position: round(9.09) = 9, round(5.45) = 5
        self.assertEqual(plan.sells, {"A": 9, "B": 5})
        self.assertAlmostEqual(plan.withdrawal + plan.cash_after + (91 * 50_000 + 55 * 100_000), 15_000_000.0)
        self.assertGreaterEqual(plan.cash_after, 0.0)

    def test_cash_never_negative_when_rounding_sells_too_little(self):
        # cash 0.2M, X = 3M -> cash_first sells 2.8M/12.8M of the stock; rounding down to whole
        # shares leaves the cash leg short, so the withdrawal is trimmed instead of going negative
        holdings = {"A": (1, 12_800_000.0)}
        plan = plan_year_end_harvest(200_000.0, holdings, BASE, NO_COST, "cash_first")
        self.assertEqual(plan.sells, {})                    # round(1 * 0.219) = 0
        self.assertEqual(plan.cash_after, 0.0)
        self.assertAlmostEqual(plan.withdrawal, 200_000.0)

    def test_full_close_when_year_more_than_doubles(self):
        plan = plan_year_end_harvest(0.0, {"A": (10, 3_000_000.0)}, BASE, NO_COST)      # V = 30M, f = 2/3
        self.assertEqual(plan.sells, {"A": 7})
        self.assertAlmostEqual(plan.withdrawal, 20_000_000.0)
        self.assertAlmostEqual(plan.cash_after + 3 * 3_000_000.0, BASE)


class TestTopup(unittest.TestCase):

    def test_topup_amount(self):
        self.assertAlmostEqual(topup_amount(9_200_000.0, BASE), 800_000.0)
        self.assertEqual(topup_amount(10_500_000.0, BASE), 0.0)
        self.assertEqual(topup_amount(9_200_000.0, BASE, topup_on_loss=False), 0.0)


class TestAnnualSummary(unittest.TestCase):

    def _year(self, y, ret, withdrawal=0.0, topup=0.0, mdd=5.0, kospi=0.0, complete=True):
        return {"year": y, "complete": complete, "return_pct": ret, "withdrawal": withdrawal, "topup": topup,
                "mdd_pct": mdd, "excess_vs_kospi_pct": ret - kospi if kospi is not None else None}

    def test_net_pnl_and_statistics(self):
        years = [
            self._year(2017, 12.0, withdrawal=1_200_000.0, kospi=20.0),
            self._year(2018, -8.0, topup=800_000.0, mdd=14.0, kospi=-17.0),
            self._year(2019, 3.0, withdrawal=300_000.0, kospi=7.0),
            self._year(2020, 1.5, complete=False),                       # YTD row: excluded from stats
        ]
        s = annual_summary(years, BASE, 10_150_000.0)
        self.assertEqual(s["n_years"], 3)
        self.assertAlmostEqual(s["cumulative_withdrawal"], 1_500_000.0)
        self.assertAlmostEqual(s["cumulative_topup"], 800_000.0)
        self.assertAlmostEqual(s["net_pnl"], 1_500_000.0 - 800_000.0 + 150_000.0)
        self.assertAlmostEqual(s["annual_return_median_pct"], 3.0)
        self.assertAlmostEqual(s["annual_return_mean_pct"], (12.0 - 8.0 + 3.0) / 3)
        self.assertEqual((s["annual_return_min_pct"], s["annual_return_max_pct"]), (-8.0, 12.0))
        self.assertEqual(s["n_positive_years"], 2)
        self.assertEqual(s["n_years_beat_kospi"], 1)
        self.assertEqual(s["n_years_with_kospi"], 3)
        self.assertAlmostEqual(s["max_year_mdd_pct"], 14.0)
        self.assertFalse(s["compounding"])

    def test_empty(self):
        s = annual_summary([], BASE, BASE)
        self.assertEqual(s["n_years"], 0)
        self.assertEqual(s["net_pnl"], 0.0)
        self.assertEqual(s["annual_return_median_pct"], 0.0)
