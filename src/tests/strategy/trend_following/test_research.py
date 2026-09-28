"""trend_following.md 6-1 event study, 6-2 matrix runner, 4-1 sensitivity
table and the markdown report."""
import unittest
from datetime import date

from strategy.trend_following.config import StrategyParams, SPEC_VERSION
from strategy.trend_following.event_study import run_event_study
from strategy.trend_following.research import ResearchRequest, run_research
from strategy.trend_following.signals import compute_features
from tests.strategy.trend_following.helpers import synthetic_dataset


class TestEventStudy(unittest.TestCase):
    def test_groups_partition_the_breakouts(self):
        ds = synthetic_dataset(with_flows=True)
        p = StrategyParams()
        rows = run_event_study(ds, compute_features(ds, p), p)
        by = {r["group"]: r for r in rows}
        self.assertGreater(by["all breakouts"]["n"], 100)
        self.assertEqual(by["V: volume >= 1.5x avg"]["n"] + by["V: volume < 1.5x avg"]["n"], by["all breakouts"]["n"])
        self.assertIn("F: FI net buy (20d)", by)
        self.assertIn("retail-only breakout (5d: FI <= 0, retail > 0)", by)
        for k in ("mean_5", "hit_20", "excess_60", "median_60"):
            self.assertIn(k, by["all breakouts"])
        self.assertGreater(by["all breakouts"]["mean_20"], 0)   # synthetic breakouts trend on

    def test_no_flows_no_flow_groups(self):
        ds = synthetic_dataset(with_flows=False)
        p = StrategyParams()
        rows = run_event_study(ds, compute_features(ds, p), p)
        self.assertEqual([r["group"] for r in rows],
                         ["all breakouts", "V: volume >= 1.5x avg", "V: volume < 1.5x avg"])


class TestResearchRunner(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ds = synthetic_dataset(with_flows=True)
        cls.req = ResearchRequest(start=date(2021, 1, 4), variant_ids=("A0", "A1", "A5", "B"),
                                  cost_multipliers=(0.0, 1.0, 2.0), include_flows=True)
        cls.msgs = []
        cls.result = run_research(cls.ds, cls.req, progress=cls.msgs.append)

    def test_runs_cover_variants_and_benchmarks_per_multiplier(self):
        r = self.result
        for m in (0.0, 1.0, 2.0):
            ids = {x.id for x in r.runs_at(m)}
            self.assertEqual(ids, {"A0", "A1", "A5", "B", "BM1", "BM2", "BM3", "BM4"})
        self.assertEqual({x.kind for x in r.runs}, {"strategy", "benchmark"})
        self.assertTrue(self.msgs)

    def test_cost_multiplier_ordering_for_a0(self):
        ends = {m: next(x for x in self.result.runs_at(m) if x.id == "A0").nav[-1] for m in (0.0, 1.0, 2.0)}
        self.assertGreaterEqual(ends[0.0], ends[1.0])
        self.assertGreaterEqual(ends[1.0], ends[2.0])

    def test_markdown_report(self):
        md = self.result.to_markdown()
        self.assertIn(SPEC_VERSION, md)
        self.assertIn("## Results at 1x costs (base case)", md)
        self.assertIn("## Cost sensitivity", md)
        self.assertIn("## 6-1 event study", md)
        self.assertIn("## 6-4 verdict", md)
        self.assertIn("BM3", md)
        self.assertIn("| A0 |", md)
        self.assertIn("2021-22", md)

    def test_flow_variants_skipped_without_flows(self):
        ds = synthetic_dataset(with_flows=False)
        req = ResearchRequest(start=date(2021, 1, 4), variant_ids=("A0", "A3"), cost_multipliers=(1.0,),
                              run_event_study=False)
        res = run_research(ds, req)
        self.assertEqual({x.id for x in res.runs_at(1.0, "strategy")}, {"A0"})
        self.assertTrue(any("Investor-flow" in w for w in res.warnings))
        self.assertEqual(res.event_study, [])

    def test_request_defaults_cover_the_whole_matrix(self):
        req = ResearchRequest(start=date(2021, 1, 4))
        self.assertEqual(req.variant_ids, ("A0", "A1", "A2", "A3", "A4", "A5", "B"))
        self.assertEqual(req.cost_multipliers, (0.0, 1.0, 2.0))


if __name__ == "__main__":
    unittest.main()
