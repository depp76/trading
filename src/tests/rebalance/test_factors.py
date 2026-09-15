"""
tests/rebalance/test_factors.py — data/rebalance/factors.py 검증 (trading.md 11-3)
"""
import os
import sys
import unittest

_PROJ_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _PROJ_ROOT)


class TestRebalanceFactorExtraction(unittest.TestCase):

    def test_negative_and_zero_per_filtered_out(self):
        from data_fetcher import _extract_live_candidates

        sample_universe = [
            {
                "ticker": "005930",
                "name": "Samsung",
                "market": "KOSPI",
                "is_index": False,
                "trailing_per": -15.5,  # negative PER (loss-making)
                "changes": {
                    "ma20_div": 105.0,
                    "ma50_div": 102.0,
                    "52w_high_diff": -5.0,
                    "20d": 3.5,
                    "60d": 8.0,
                },
            },
            {
                "ticker": "000660",
                "name": "SK Hynix",
                "market": "KOSPI",
                "is_index": False,
                "trailing_per": 12.0,  # positive PER
                "changes": {
                    "ma20_div": 108.0,
                    "ma50_div": 104.0,
                    "52w_high_diff": -2.0,
                    "20d": 5.0,
                    "60d": 12.0,
                },
            },
            {
                "ticker": "035420",
                "name": "NAVER",
                "market": "KOSPI",
                "is_index": False,
                "trailing_per": 0.0,  # zero PER
                "changes": {
                    "ma20_div": 98.0,
                    "ma50_div": 95.0,
                    "52w_high_diff": -15.0,
                    "20d": -2.0,
                    "60d": -5.0,
                },
            },
        ]

        candidates = _extract_live_candidates(sample_universe)
        self.assertEqual(len(candidates), 3)

        cand_map = {c["ticker"]: c for c in candidates}
        # Negative PER -> None
        self.assertIsNone(cand_map["005930"]["raw"]["value_per"])
        # Positive PER -> 12.0
        self.assertEqual(cand_map["000660"]["raw"]["value_per"], 12.0)
        # Zero PER -> None
        self.assertIsNone(cand_map["035420"]["raw"]["value_per"])


if __name__ == "__main__":
    unittest.main()
