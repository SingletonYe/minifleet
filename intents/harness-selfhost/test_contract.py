"""Contract tests for the three v0.2 modules, written before they exist.

These are system gates: they only run on the integrated tree, where all three
modules are present. They import the frozen names from the contract and fail
loudly if a worker deviated from it.
"""

from __future__ import annotations

import http.server
import json
import socketserver
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from minifleet import fleetmetrics, repair
from minifleet.deploy import Supervisor, smoke, soak

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


class RepairPacketTest(unittest.TestCase):
    def test_packet_shape_and_ordering(self):
        packet = repair.build_repair_packet(TASK, EVIDENCE, attempt=1, max_attempts=3)
        self.assertEqual(packet["task_id"], "T-road")
        self.assertEqual(packet["attempt"], 1)
        self.assertEqual(packet["max_attempts"], 3)
        self.assertIs(packet["escalate"], False)
        self.assertEqual([g["gate_id"] for g in packet["failing_gates"]], ["G-b", "G-c"])
        self.assertTrue(all(g["status"] != "pass" for g in packet["failing_gates"]))
        self.assertEqual(packet["scope"], ["pkg/road.py", "tests/test_road.py"])
        self.assertEqual(packet["carry_forward"], ["pkg/road.py"])
        self.assertTrue(packet["instructions"])
        joined = " ".join(packet["instructions"])
        self.assertIn("G-b", joined)
        self.assertIn("G-c", joined)
        self.assertTrue(packet["reason"])

    def test_escalation_when_attempts_are_exhausted(self):
        packet = repair.build_repair_packet(TASK, EVIDENCE, attempt=3, max_attempts=3)
        self.assertIs(packet["escalate"], True)
        self.assertTrue(packet["reason"])

    def test_no_failing_evidence_still_produces_a_usable_packet(self):
        packet = repair.build_repair_packet(TASK, [], attempt=1, max_attempts=2)
        self.assertEqual(packet["failing_gates"], [])
        self.assertTrue(packet["instructions"])

    def test_inputs_are_not_mutated(self):
        before_task = json.dumps(TASK, sort_keys=True)
        before_evidence = json.dumps(EVIDENCE, sort_keys=True)
        repair.build_repair_packet(TASK, EVIDENCE, attempt=2, max_attempts=3)
        self.assertEqual(json.dumps(TASK, sort_keys=True), before_task)
        self.assertEqual(json.dumps(EVIDENCE, sort_keys=True), before_evidence)


class SupervisorTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def _script(self, body: str) -> list[str]:
        return [sys.executable, "-c", body]

    def test_ready_line_is_parsed_and_process_stops(self):
        supervisor = Supervisor(
            self._script('import time; print(\'MINIFLEET_READY {"port": 4242}\', flush=True); time.sleep(20)'),
            cwd=self.tmp.name,
            timeout=10.0,
        )
        supervisor.start()
        ready = supervisor.wait_ready()
        self.assertTrue(ready["ready"], ready)
        self.assertEqual(ready["port"], 4242)
        self.assertTrue(supervisor.running)
        code = supervisor.stop()
        self.assertIsInstance(code, int)
        self.assertFalse(supervisor.running)

    def test_missing_ready_line_times_out_without_hanging(self):
        supervisor = Supervisor(
            self._script("import time; time.sleep(30)"), cwd=self.tmp.name, timeout=1.0
        )
        supervisor.start()
        started = time.time()
        ready = supervisor.wait_ready()
        self.assertFalse(ready["ready"])
        self.assertLess(time.time() - started, 8.0)
        supervisor.stop()
        self.assertFalse(supervisor.running)


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path == "/boom":
            self.send_response(500)
            self.end_headers()
            self.wfile.write(b"nope")
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"status": "ok"}')

    def log_message(self, *args):  # silence
        return


class ProbeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _Handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def test_smoke_reports_status_and_ok(self):
        good = smoke(f"http://127.0.0.1:{self.port}/healthz", timeout=5.0)
        self.assertEqual(good["status"], 200)
        self.assertIs(good["ok"], True)
        bad = smoke(f"http://127.0.0.1:{self.port}/boom", timeout=5.0)
        self.assertEqual(bad["status"], 500)
        self.assertIs(bad["ok"], False)

    def test_soak_counts_samples_and_failures(self):
        result = soak(f"http://127.0.0.1:{self.port}/healthz", seconds=0.8, interval=0.2, timeout=5.0)
        self.assertGreaterEqual(result["samples"], 3)
        self.assertEqual(result["failures"], 0)
        self.assertIs(result["ok"], True)
        self.assertIn("p50_ms", result)
        self.assertIn("max_ms", result)

    def test_soak_flags_a_dead_endpoint(self):
        result = soak("http://127.0.0.1:1/nothing", seconds=0.4, interval=0.15, timeout=0.5)
        self.assertGreater(result["failures"], 0)
        self.assertIs(result["ok"], False)


def _run_fixture() -> dict:
    started = 1_000_000.0
    return {
        "id": "20260101T000000Z-fixture-aaaaaa",
        "intent_id": "fixture",
        "status": "failed",
        "verdict": "fail: G-x=fail",
        "intent": {"id": "fixture", "title": "Fixture", "summary": "", "deliverables": [],
                   "acceptance": [], "constraints": [], "budgets": {}, "out_of_scope": [],
                   "components": [], "gates": [], "contracts": [], "harness_dir": ""},
        "design": {
            "intent_id": "fixture",
            "components": [],
            "contracts": [],
            "gates": [],
            "tasks": [
                {"id": "T-a", "title": "a", "role": "builder", "goal": "a", "owns": ["a.py"],
                 "depends_on": [], "state": "merged", "attempts": 1, "submitted_files": ["a.py"],
                 "started_at": started, "finished_at": started + 10},
                {"id": "T-b", "title": "b", "role": "builder", "goal": "b", "owns": ["b.py"],
                 "depends_on": [], "state": "merged", "attempts": 1, "submitted_files": ["b.py"],
                 "started_at": started, "finished_at": started + 28},
                {"id": "T-c", "title": "c", "role": "builder", "goal": "c", "owns": ["c.py"],
                 "depends_on": ["T-a"], "state": "merged", "attempts": 1, "submitted_files": ["c.py"],
                 "started_at": started + 10, "finished_at": started + 40},
                {"id": "T-d", "title": "d", "role": "builder", "goal": "d", "owns": ["d.py"],
                 "depends_on": ["T-c"], "state": "verified", "attempts": 1, "submitted_files": ["d.py"],
                 "started_at": started + 40, "finished_at": started + 50},
                {"id": "T-e", "title": "e", "role": "builder", "goal": "e", "owns": ["e.py"],
                 "depends_on": ["T-b"], "state": "failed", "attempts": 2, "submitted_files": ["e.py"],
                 "started_at": started + 28, "finished_at": started + 33},
            ],
            "risks": [],
            "decisions": [],
            "repairs": [],
        },
        "evidence": [
            {"gate_id": "G-1", "status": "pass", "duration_s": 1.0, "metrics": {}, "detail": ""},
            {"gate_id": "G-2", "status": "pass", "duration_s": 1.0, "metrics": {}, "detail": ""},
            {"gate_id": "G-3", "status": "pass", "duration_s": 1.0, "metrics": {}, "detail": ""},
            {"gate_id": "G-4", "status": "fail", "duration_s": 1.0, "metrics": {}, "detail": "boom"},
        ],
        "events": [],
        "raw_intent": "fixture",
        "repo_dir": "",
        "integration_branch": "integration",
        "dispatcher": "packet",
        "created_at": started,
        "updated_at": started + 50,
    }


class FleetMetricsTest(unittest.TestCase):
    def test_summary_counts_and_critical_path(self):
        summary = fleetmetrics.summarize(_run_fixture())
        self.assertEqual(summary["tasks"], 5)
        self.assertEqual(summary["merged"], 3)
        self.assertEqual(summary["failed"], 1)
        self.assertEqual(summary["attempts_total"], 6)
        self.assertEqual(summary["retries"], 1)
        self.assertEqual(summary["gates_total"], 4)
        self.assertEqual(summary["gates_passed"], 3)
        self.assertEqual(summary["gates_failed"], 1)
        self.assertEqual(summary["slowest_task"]["id"], "T-c")
        self.assertAlmostEqual(summary["critical_path_seconds"], 50.0, places=3)

    def test_summary_of_an_empty_run_does_not_crash(self):
        run = _run_fixture()
        run["design"]["tasks"] = []
        run["evidence"] = []
        summary = fleetmetrics.summarize(run)
        self.assertEqual(summary["tasks"], 0)
        self.assertEqual(summary["critical_path_seconds"], 0.0)
        self.assertIsNone(summary["slowest_task"])


if __name__ == "__main__":
    unittest.main()
