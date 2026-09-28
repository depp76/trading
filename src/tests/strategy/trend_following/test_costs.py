"""trend_following.md 4-1: tax table by year, tick ladders (2023 reform),
commission / slippage decomposition, multiplier, ETF variant."""
import unittest
from datetime import date

from strategy.trend_following.costs import (
    CostModel, TradeCost, tick_size, snap_to_tick, normalize_market, DEFAULT_TAX_TABLE,
)


class TestTaxTable(unittest.TestCase):
    def test_rates_by_year_match_the_spec_table(self):
        cm = CostModel()
        self.assertAlmostEqual(cm.tax_rate(date(2021, 6, 1), "KOSPI"), 0.0023)
        self.assertAlmostEqual(cm.tax_rate(date(2022, 6, 1), "KOSDAQ"), 0.0023)
        self.assertAlmostEqual(cm.tax_rate(date(2023, 6, 1), "KOSPI"), 0.0020)
        self.assertAlmostEqual(cm.tax_rate(date(2024, 6, 1), "KOSPI"), 0.0018)
        self.assertAlmostEqual(cm.tax_rate(date(2025, 6, 1), "KOSPI"), 0.0015)
        self.assertAlmostEqual(cm.tax_rate(date(2026, 6, 1), "KOSPI"), 0.0020)

    def test_years_outside_the_table_clamp_to_the_nearest_row(self):
        cm = CostModel()
        self.assertAlmostEqual(cm.tax_rate(date(2019, 1, 1), "KOSPI"), DEFAULT_TAX_TABLE[2021]["KOSPI"])
        self.assertAlmostEqual(cm.tax_rate(date(2031, 1, 1), "KOSPI"), DEFAULT_TAX_TABLE[2026]["KOSPI"])

    def test_etf_sales_are_exempt(self):
        cm = CostModel()
        self.assertEqual(cm.tax_rate(date(2024, 3, 1), "ETF"), 0.0)
        self.assertEqual(cm.sell_cost(date(2024, 3, 1), "ETF", 30_000, 100).tax, 0.0)

    def test_unknown_market_is_treated_as_kospi(self):
        self.assertEqual(normalize_market(None), "KOSPI")
        self.assertEqual(normalize_market("kosdaq"), "KOSDAQ")


class TestTickSizes(unittest.TestCase):
    def test_pre_reform_ladders_differ_by_market(self):
        d = date(2022, 6, 1)
        self.assertEqual(tick_size(d, "KOSPI", 150_000), 500)
        self.assertEqual(tick_size(d, "KOSDAQ", 150_000), 100)
        self.assertEqual(tick_size(d, "KOSPI", 1_500), 5)
        self.assertEqual(tick_size(d, "KOSPI", 7_000), 10)

    def test_post_reform_ladder_is_unified(self):
        d = date(2023, 6, 1)
        for m in ("KOSPI", "KOSDAQ"):
            self.assertEqual(tick_size(d, m, 150_000), 100)
            self.assertEqual(tick_size(d, m, 1_500), 1)
            self.assertEqual(tick_size(d, m, 7_000), 10)
            self.assertEqual(tick_size(d, m, 600_000), 1000)
        self.assertEqual(tick_size(date(2023, 1, 25), "KOSPI", 150_000), 100)
        self.assertEqual(tick_size(date(2023, 1, 24), "KOSPI", 150_000), 500)

    def test_etf_tick_is_five_won(self):
        self.assertEqual(tick_size(date(2022, 1, 3), "ETF", 33_333), 5)
        self.assertEqual(tick_size(date(2025, 1, 3), "ETF", 333_333), 5)

    def test_snap_rounds_against_the_trader(self):
        self.assertEqual(snap_to_tick(150_010, 100, "buy"), 150_100)
        self.assertEqual(snap_to_tick(150_090, 100, "sell"), 150_000)
        self.assertEqual(snap_to_tick(150_000, 100, "buy"), 150_000)
        self.assertEqual(snap_to_tick(150_000, 100, "sell"), 150_000)

    def test_fill_price_uses_the_date_ladder(self):
        cm = CostModel()
        self.assertEqual(cm.fill_price(date(2022, 6, 1), "KOSPI", 150_010, "buy"), 150_500)
        self.assertEqual(cm.fill_price(date(2023, 6, 1), "KOSPI", 150_010, "buy"), 150_100)
        self.assertEqual(CostModel(round_to_tick=False).fill_price(date(2023, 6, 1), "KOSPI", 150_010, "buy"), 150_010)


class TestCostAmounts(unittest.TestCase):
    def test_buy_and_sell_decomposition(self):
        cm = CostModel()
        d = date(2024, 5, 2)
        buy = cm.buy_cost(d, "KOSPI", 50_000, 40)     # value 2,000,000
        self.assertAlmostEqual(buy.commission, 2_000_000 * 0.00015)
        self.assertAlmostEqual(buy.slippage, 2_000_000 * 0.0010)
        self.assertEqual(buy.tax, 0.0)
        sell = cm.sell_cost(d, "KOSPI", 50_000, 40)
        self.assertAlmostEqual(sell.tax, 2_000_000 * 0.0018)
        self.assertAlmostEqual(sell.total, 2_000_000 * (0.00015 + 0.0010 + 0.0018))

    def test_round_trip_2026_is_about_43_bp(self):
        cm = CostModel()
        d = date(2026, 3, 2)
        value = 1_000_000
        rt = cm.buy_cost(d, "KOSPI", 1000, 1000).total + cm.sell_cost(d, "KOSPI", 1000, 1000).total
        self.assertAlmostEqual(rt / value, 0.0043, places=6)

    def test_multiplier_scales_everything_and_zero_is_free(self):
        d = date(2024, 5, 2)
        base = CostModel().sell_cost(d, "KOSPI", 10_000, 10)
        twice = CostModel().scaled(2.0).sell_cost(d, "KOSPI", 10_000, 10)
        free = CostModel().scaled(0.0).sell_cost(d, "KOSPI", 10_000, 10)
        self.assertAlmostEqual(twice.total, 2 * base.total)
        self.assertAlmostEqual(twice.tax, 2 * base.tax)
        self.assertEqual(free.total, 0.0)

    def test_min_commission_and_liquidity_slippage(self):
        d = date(2024, 5, 2)
        cm = CostModel(min_commission=1_000.0)
        self.assertEqual(cm.buy_cost(d, "KOSPI", 1_000, 10).commission, 1_000.0)   # 10,000 value -> 1.5 < 1,000
        cm2 = CostModel(slippage_liquidity_coeff=0.1)
        small = cm2.buy_cost(d, "KOSPI", 10_000, 100, adv=1e9).slippage            # 1e6 / 1e9 -> +0.01%
        plain = CostModel().buy_cost(d, "KOSPI", 10_000, 100).slippage
        self.assertAlmostEqual(small, plain + 1e6 * 0.1 * (1e6 / 1e9))

    def test_zero_quantity_costs_nothing(self):
        self.assertEqual(CostModel().buy_cost(date(2024, 1, 2), "KOSPI", 10_000, 0), TradeCost())

    def test_etf_variant_changes_only_slippage(self):
        cm = CostModel()
        etf = cm.for_etf()
        self.assertEqual(etf.slippage_rate, 0.0002)
        self.assertEqual(etf.commission_rate, cm.commission_rate)
        self.assertEqual(etf.cost_multiplier, cm.cost_multiplier)

    def test_limit_move_detection(self):
        cm = CostModel()
        self.assertTrue(cm.is_limit_up(13_000, 10_000))
        self.assertFalse(cm.is_limit_up(12_000, 10_000))
        self.assertTrue(cm.is_limit_down(7_000, 10_000))
        self.assertFalse(cm.is_limit_down(8_000, 10_000))

    def test_tradecost_addition(self):
        a = TradeCost(1, 2, 3)
        b = TradeCost(10, 20, 30)
        self.assertEqual((a + b).total, 66)
        self.assertEqual(a.scaled(2).slippage, 6)


if __name__ == "__main__":
    unittest.main()
