"""Unit tests for bounded self-repair (acceptance A-1 and A-2)."""

from __future__ import annotations

import json
import unittest

from minifleet.repair import build_repair_packet, failing_gates, render_repair_markdown

TASK = {
    "id": "T-road",
    "owns": ["pkg/road.py", "tests/test_road.py"],
    "submitted_files": ["pkg/road.py"],
}

EVIDENCE = [
    {"gate_id": "G-b", "status": "fail", "detail": "exit=1: two tests failed"},
    {"gate_id": "G-a", "status": "pass", "detail": "exit=0"},
    {"gate_id": "G-c", "status": "error", "detail": "metric never produced"},
]


class FailingGateTests(unittest.TestCase):
    def test_only_failing_gates_are_kept_and_ordered(self):
        rows = failing_gates(EVIDENCE)
        self.assertEqual([row["gate_id"] for row in rows], ["G-b", "G-c"])
        self.assertEqual([row["status"] for row in rows], ["fail", "error"])

    def test_pass_status_only_filter_is_exact(self):
        rows = failing_gates([{"gate_id": "G-x", "status": "passed", "detail": ""}])
        self.assertEqual(len(rows), 1, "an unknown status must not be treated as a pass")


class RepairPacketTests(unittest.TestCase):
    def test_shape_ordering_and_scope(self):
        packet = build_repair_packet(TASK, EVIDENCE, attempt=1, max_attempts=3)
        self.assertEqual(
            set(packet),
            {
                "task_id", "attempt", "max_attempts", "escalate", "reason",
                "failing_gates", "instructions", "scope", "carry_forward",
            },
        )
        self.assertEqual(packet["task_id"], "T-road")
        self.assertIs(packet["escalate"], False)
        self.assertEqual([row["gate_id"] for row in packet["failing_gates"]], ["G-b", "G-c"])
        self.assertEqual(packet["scope"], ["pkg/road.py", "tests/test_road.py"])
        self.assertEqual(packet["carry_forward"], ["pkg/road.py"])
        self.assertTrue(all(row["status"] != "pass" for row in packet["failing_gates"]))

    def test_instructions_name_every_failing_gate(self):
        packet = build_repair_packet(TASK, EVIDENCE, attempt=1, max_attempts=3)
        joined = " ".join(packet["instructions"])
        self.assertIn("G-b", joined)
        self.assertIn("G-c", joined)
        self.assertNotIn("G-a", joined)

    def test_attempt_budget_is_bounded(self):
        self.assertIs(build_repair_packet(TASK, EVIDENCE, 2, 3)["escalate"], False)
        exhausted = build_repair_packet(TASK, EVIDENCE, 3, 3)
        self.assertIs(exhausted["escalate"], True)
        self.assertIn("escalate", " ".join(exhausted["instructions"]).lower())
        self.assertIn("exhausted", exhausted["reason"])

    def test_empty_evidence_still_produces_a_usable_packet(self):
        packet = build_repair_packet(TASK, [], attempt=1, max_attempts=2)
        self.assertEqual(packet["failing_gates"], [])
        self.assertTrue(packet["instructions"])
        self.assertTrue(packet["reason"])

    def test_inputs_are_not_mutated(self):
        before_task = json.dumps(TASK, sort_keys=True)
        before_evidence = json.dumps(EVIDENCE, sort_keys=True)
        build_repair_packet(TASK, EVIDENCE, attempt=2, max_attempts=3)
        self.assertEqual(json.dumps(TASK, sort_keys=True), before_task)
        self.assertEqual(json.dumps(EVIDENCE, sort_keys=True), before_evidence)

    def test_scope_and_carry_forward_are_copies(self):
        packet = build_repair_packet(TASK, [], attempt=1, max_attempts=3)
        packet["scope"].append("elsewhere.py")
        packet["carry_forward"].append("elsewhere.py")
        self.assertEqual(TASK["owns"], ["pkg/road.py", "tests/test_road.py"])
        self.assertEqual(TASK["submitted_files"], ["pkg/road.py"])

    def test_markdown_renders_the_escalation(self):
        body = render_repair_markdown(build_repair_packet(TASK, EVIDENCE, 3, 3))
        self.assertIn("Repair packet T-road", body)
        self.assertIn("G-b", body)
        self.assertIn("Escalation required", body)


if __name__ == "__main__":
    unittest.main()
