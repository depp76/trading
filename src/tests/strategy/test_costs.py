"""tests/strategy/test_costs.py — strategy.costs (review_agy.md Section 4,
Phase 1)."""
import dataclasses
import unittest

from strategy.costs import KRX_STOCK_COST, US_STOCK_COST, TransactionCostModel


class TestTransactionCostModel(unittest.TestCase):

    def test_buy_cost_excludes_sell_tax(self):
        model = TransactionCostModel(buy_fee_rate=0.001, sell_fee_rate=0.002,
                                      sell_tax_rate=0.003, slippage_rate=0.0005)
        self.assertAlmostEqual(model.buy_cost(1_000_000), 1_500.0)  # (0.001 + 0.0005) * 1e6

    def test_sell_cost_includes_fee_tax_and_slippage(self):
        model = TransactionCostModel(buy_fee_rate=0.001, sell_fee_rate=0.002,
                                      sell_tax_rate=0.003, slippage_rate=0.0005)
        self.assertAlmostEqual(model.sell_cost(1_000_000), 5_500.0)  # (0.002+0.003+0.0005) * 1e6

    def test_default_model_has_no_cost(self):
        model = TransactionCostModel()
        self.assertEqual(model.buy_cost(1_000_000), 0.0)
        self.assertEqual(model.sell_cost(1_000_000), 0.0)

    def test_frozen_dataclass_cannot_be_mutated(self):
        with self.assertRaises(dataclasses.FrozenInstanceError):
            KRX_STOCK_COST.buy_fee_rate = 0.5


class TestStandardCostTemplates(unittest.TestCase):

    def test_krx_matches_rebalance_backtest_defaults(self):
        # strategy.rebalance.backtest.run_rebalance_backtest's own defaults.
        self.assertAlmostEqual(KRX_STOCK_COST.buy_fee_rate, 0.00015)
        self.assertAlmostEqual(KRX_STOCK_COST.sell_fee_rate, 0.00015)
        self.assertAlmostEqual(KRX_STOCK_COST.sell_tax_rate, 0.0018)

    def test_krx_round_trip_cost_on_a_sample_trade(self):
        notional = 10_000_000  # 10M KRW
        round_trip = KRX_STOCK_COST.buy_cost(notional) + KRX_STOCK_COST.sell_cost(notional)
        self.assertAlmostEqual(round_trip, 10_000_000 * (0.00015 * 2 + 0.0018))

    def test_us_template_has_no_buy_side_commission(self):
        self.assertEqual(US_STOCK_COST.buy_fee_rate, 0.0)
        self.assertEqual(US_STOCK_COST.buy_cost(1_000_000), 0.0)


if __name__ == "__main__":
    unittest.main()
