"""Unit tests for fleet-level accounting (acceptance A-5)."""

from __future__ import annotations

import unittest

from minifleet.fleetmetrics import critical_path_seconds, summarize

START = 1_000_000.0


def _task(task_id: str, depends: list[str], state: str, attempts: int, start: float, seconds: float):
    return {
        "id": task_id,
        "depends_on": depends,
        "state": state,
        "attempts": attempts,
        "started_at": start,
        "finished_at": start + seconds,
    }


def _run(tasks: list[dict], evidence: list[dict] | None = None) -> dict:
    return {
        "id": "fixture",
        "status": "failed",
        "verdict": "fail: G-x=fail",
        "design": {"tasks": tasks},
        "evidence": evidence or [],
    }


class CriticalPathTests(unittest.TestCase):
    def test_longest_chain_wins_not_the_sum(self):
        # a(10) -> c(30) -> d(10) is 50; b(28) -> e(5) is 33; total duration is 83.
        tasks = [
            _task("T-a", [], "merged", 1, START, 10),
            _task("T-b", [], "merged", 1, START, 28),
            _task("T-c", ["T-a"], "merged", 1, START + 10, 30),
            _task("T-d", ["T-c"], "verified", 1, START + 40, 10),
            _task("T-e", ["T-b"], "failed", 2, START + 28, 5),
        ]
        self.assertEqual(critical_path_seconds(tasks), 50.0)

    def test_missing_dependencies_and_bounds_are_tolerated(self):
        tasks = [
            {"id": "T-a", "depends_on": ["T-ghost"], "state": "pending", "attempts": 0},
            _task("T-b", ["T-a"], "merged", 1, START, 4),
        ]
        self.assertEqual(critical_path_seconds(tasks), 4.0)

    def test_cycle_does_not_hang_the_report(self):
        tasks = [
            _task("T-a", ["T-b"], "merged", 1, START, 3),
            _task("T-b", ["T-a"], "merged", 1, START, 5),
        ]
        self.assertGreaterEqual(critical_path_seconds(tasks), 5.0)


class SummarizeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tasks = [
            _task("T-a", [], "merged", 1, START, 10),
            _task("T-b", [], "merged", 1, START, 28),
            _task("T-c", ["T-a"], "merged", 1, START + 10, 30),
            _task("T-d", ["T-c"], "verified", 1, START + 40, 10),
            _task("T-e", ["T-b"], "failed", 2, START + 28, 5),
        ]
        self.evidence = [
            {"gate_id": "G-1", "status": "pass"},
            {"gate_id": "G-2", "status": "pass"},
            {"gate_id": "G-3", "status": "pass"},
            {"gate_id": "G-4", "status": "fail"},
        ]

    def test_every_documented_key_is_present(self):
        summary = summarize(_run(self.tasks, self.evidence))
        self.assertEqual(
            set(summary),
            {
                "tasks", "merged", "failed", "attempts_total", "retries",
                "gates_total", "gates_passed", "gates_failed",
                "critical_path_seconds", "slowest_task", "verdict",
            },
        )

    def test_counts_attempts_retries_gates_and_states(self):
        summary = summarize(_run(self.tasks, self.evidence))
        self.assertEqual(summary["tasks"], 5)
        self.assertEqual(summary["merged"], 3)
        self.assertEqual(summary["failed"], 1)
        self.assertEqual(summary["attempts_total"], 6)
        self.assertEqual(summary["retries"], 1)
        self.assertEqual(summary["gates_total"], 4)
        self.assertEqual(summary["gates_passed"], 3)
        self.assertEqual(summary["gates_failed"], 1)
        self.assertEqual(summary["slowest_task"], {"id": "T-c", "seconds": 30.0})
        self.assertEqual(summary["verdict"], "fail: G-x=fail")

    def test_an_empty_run_summarises_without_raising(self):
        summary = summarize(_run([], []))
        self.assertEqual(summary["tasks"], 0)
        self.assertEqual(summary["attempts_total"], 0)
        self.assertEqual(summary["gates_total"], 0)
        self.assertEqual(summary["critical_path_seconds"], 0.0)
        self.assertIsNone(summary["slowest_task"])

    def test_a_run_without_a_design_or_evidence_is_tolerated(self):
        summary = summarize({})
        self.assertEqual(summary["tasks"], 0)
        self.assertEqual(summary["verdict"], "")


if __name__ == "__main__":
    unittest.main()
