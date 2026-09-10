"""Acceptance for the wiring task: the new capabilities must be reachable from the CLI."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

FIXTURE = Path("harness/fixtures/failing-run")
METRIC_KEYS = {
    "tasks", "merged", "failed", "attempts_total", "retries",
    "gates_total", "gates_passed", "gates_failed",
    "critical_path_seconds", "slowest_task", "verdict",
}


def run_cli(*args: str, cwd: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "minifleet", *args],
        capture_output=True, text=True, cwd=cwd or ".",
    )


class CliWiringTest(unittest.TestCase):
    def test_help_lists_the_new_capabilities(self):
        result = run_cli("--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        for command in ("repair", "deploy", "fleetmetrics"):
            self.assertIn(command, result.stdout, f"{command} is not wired into the CLI")

    def test_fleetmetrics_reads_a_run_record(self):
        result = run_cli("fleetmetrics", "--run", str(FIXTURE))
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout[result.stdout.index("{"):])
        self.assertTrue(METRIC_KEYS <= set(payload), sorted(METRIC_KEYS - set(payload)))
        self.assertEqual(payload["tasks"], 5)

    def test_repair_writes_a_repair_packet_for_every_failed_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            workdir = Path(tmp) / "run"
            shutil.copytree(FIXTURE, workdir)
            result = run_cli("repair", "--run", str(workdir))
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            packets = sorted((workdir / "packets").glob("*.repair.json"))
            self.assertEqual([p.name for p in packets], ["T-e.repair.json"], result.stdout)
            payload = json.loads(packets[0].read_text())
            self.assertEqual(payload["task_id"], "T-e")
            self.assertTrue(payload["instructions"])
            self.assertEqual(payload["scope"], ["e.py"])
            self.assertFalse(payload["escalate"])

    def test_repair_reports_cleanly_when_nothing_failed(self):
        with tempfile.TemporaryDirectory() as tmp:
            workdir = Path(tmp) / "run"
            shutil.copytree(FIXTURE, workdir)
            record = json.loads((workdir / "run.json").read_text())
            for task in record["design"]["tasks"]:
                task["state"] = "merged"
            (workdir / "run.json").write_text(json.dumps(record))
            result = run_cli("repair", "--run", str(workdir))
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            self.assertIn("no failed tasks", result.stdout.lower())


if __name__ == "__main__":
    unittest.main()
