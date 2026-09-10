"""The worker's own tests, written alongside its first (defective) attempt.

They cover the happy paths with 4-5 samples, which is exactly why the defect
survives the task gate and has to be caught by the frozen harness.
"""

from __future__ import annotations

import unittest

from minifleet.quarantine import classify, quarantine


class ClassificationTest(unittest.TestCase):
    def test_stable_cases(self):
        self.assertEqual(classify([True] * 4)["verdict"], "stable-pass")
        self.assertEqual(classify([False] * 5)["verdict"], "stable-fail")

    def test_mixed_case(self):
        result = classify([True, True, True, False, True])
        self.assertEqual(result["verdict"], "flaky")
        self.assertEqual(result["dominant"], "pass")

    def test_quarantine_lists_only_flaky_gates(self):
        gates = {"G-a": [True, True, False, True], "G-b": [True] * 4, "G-c": [False] * 4}
        self.assertEqual(quarantine(gates), ["G-a"])


if __name__ == "__main__":
    unittest.main()
