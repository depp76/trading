"""tests/strategy/test_base.py — strategy.base data model (review_agy.md
Section 4, Phase 1)."""
import unittest

from strategy.base import BacktestResult, BaseStrategyConfig, Trade


class TestTrade(unittest.TestCase):

    def test_construction_with_required_fields_only(self):
        t = Trade(ticker="005930", entry_date="2026-01-01", exit_date="2026-02-01",
                   entry_price=1000.0, exit_price=1100.0, qty=10.0, weight=0.5,
                   price_return_pct=10.0, net_return_pct=9.8)
        self.assertEqual(t.ticker, "005930")
        self.assertIsNone(t.exit_reason)
        self.assertEqual(t.days_held, 0)


class TestBacktestResult(unittest.TestCase):

    def test_defaults_are_empty_and_not_shared_between_instances(self):
        a = BacktestResult(strategy_name="s", ticker="005930", summary={})
        b = BacktestResult(strategy_name="s", ticker="000660", summary={})
        a.trades.append("x")
        self.assertEqual(a.trades, ["x"])
        self.assertEqual(b.trades, [])  # mutable-default field(default_factory=list) protects this
        self.assertEqual(a.equity_curve, [])
        self.assertEqual(a.benchmark_curve, [])


class TestBaseStrategyConfig(unittest.TestCase):

    def test_to_dict_round_trips_fields(self):
        cfg = BaseStrategyConfig(strategy_name="rsi_divergence", initial_capital=1.0)
        self.assertEqual(cfg.to_dict(), {"strategy_name": "rsi_divergence", "initial_capital": 1.0})

    def test_to_dict_is_a_copy_not_the_live_dict(self):
        cfg = BaseStrategyConfig()
        d = cfg.to_dict()
        d["strategy_name"] = "mutated"
        self.assertEqual(cfg.strategy_name, "")


if __name__ == "__main__":
    unittest.main()
