"""Frozen acceptance tests for minifleet/quarantine.py.

A gate that fails on an unlucky run is worse than no gate at all: it trains
people to ignore red. Quarantine is the fleet's answer - decide from repeated
outcomes whether a gate is flaky, and refuse to decide when the sample is too
small. These tests are exact about the boundaries, because a boundary error in
this module silently mislabels stable gates as flaky.
"""

from __future__ import annotations

import unittest

from minifleet.quarantine import classify, quarantine


class ClassifyTest(unittest.TestCase):
    def test_stable_pass(self):
        self.assertEqual(
            classify([True, True, True]),
            {"verdict": "stable-pass", "pass_rate": 1.0, "samples": 3, "dominant": "pass"},
        )

    def test_stable_fail(self):
        self.assertEqual(
            classify([False, False, False]),
            {"verdict": "stable-fail", "pass_rate": 0.0, "samples": 3, "dominant": "fail"},
        )

    def test_mixed_outcomes_are_flaky(self):
        result = classify([True, False, True])
        self.assertEqual(result["verdict"], "flaky")
        self.assertEqual(result["samples"], 3)
        self.assertAlmostEqual(result["pass_rate"], 0.667, places=3)
        self.assertEqual(result["dominant"], "pass")

    def test_empty_outcomes_are_insufficient_data_not_a_failure(self):
        result = classify([])
        self.assertEqual(result["verdict"], "insufficient-data")
        self.assertIsNone(result["pass_rate"])
        self.assertEqual(result["samples"], 0)
        self.assertIsNone(result["dominant"])

    def test_a_sample_smaller_than_the_minimum_is_insufficient_data(self):
        result = classify([True, False], min_samples=3)
        self.assertEqual(result["verdict"], "insufficient-data")
        self.assertIsNone(result["pass_rate"])
        self.assertEqual(result["samples"], 2)

    def test_exactly_min_samples_is_enough_to_decide(self):
        result = classify([True, True, False], min_samples=3)
        self.assertEqual(result["verdict"], "flaky")
        self.assertEqual(result["samples"], 3)
        self.assertAlmostEqual(result["pass_rate"], 0.667, places=3)

    def test_dominance_follows_the_flake_threshold(self):
        self.assertEqual(classify([True, True, False, False])["dominant"], "pass")
        self.assertEqual(classify([True, True, False, False], flake_threshold=0.75)["dominant"], "fail")
        self.assertEqual(classify([True, True, True, False])["dominant"], "pass")
        self.assertEqual(classify([True, True, True, False], flake_threshold=0.9)["dominant"], "fail")


class QuarantineTest(unittest.TestCase):
    def test_only_flaky_gates_are_quarantined_and_sorted(self):
        gates = {
            "G-zeta": [True, False, True],
            "G-alpha": [False, True, False],
            "G-stable": [True, True, True],
            "G-broken": [False, False, False],
            "G-tiny": [True],
        }
        self.assertEqual(quarantine(gates), ["G-alpha", "G-zeta"])

    def test_insufficient_data_is_never_quarantined(self):
        self.assertEqual(quarantine({"G-tiny": [True], "G-empty": []}), [])

    def test_quarantine_accepts_the_same_thresholds(self):
        gates = {"G-a": [True, True, False, False]}
        self.assertEqual(quarantine(gates, flake_threshold=0.9), ["G-a"])


if __name__ == "__main__":
    unittest.main()
