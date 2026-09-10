"""CLI-level tests for the v0.2 subcommands (acceptance A-6 and A-7)."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

SERVER = textwrap.dedent(
    """
    import http.server, socketserver

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/healthz":
                body, status = b'{"ok": true}', 200
            else:
                body, status = b"nope", 404
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            return

    with socketserver.TCPServer(("127.0.0.1", 0), Handler) as httpd:
        print('MINIFLEET_READY {"port": %d}' % httpd.server_address[1], flush=True)
        httpd.serve_forever()
    """
).strip()


def run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "minifleet", *args], capture_output=True, text=True, cwd="."
    )


def _record() -> dict:
    return {
        "id": "fixture",
        "status": "failed",
        "verdict": "fail: G-unit=fail",
        "design": {
            "tasks": [
                {
                    "id": "T-a",
                    "depends_on": [],
                    "state": "merged",
                    "attempts": 1,
                    "owns": ["a.py"],
                    "submitted_files": ["a.py"],
                    "started_at": 1000.0,
                    "finished_at": 1004.0,
                },
                {
                    "id": "T-b",
                    "depends_on": ["T-a"],
                    "state": "failed",
                    "attempts": 2,
                    "owns": ["b.py"],
                    "submitted_files": ["b.py"],
                    "started_at": 1004.0,
                    "finished_at": 1009.0,
                    "evidence": [{"gate_id": "G-unit", "status": "fail", "detail": "exit=1"}],
                },
            ]
        },
        "evidence": [{"gate_id": "G-unit", "status": "fail", "detail": "exit=1"}],
    }


class CliTests(unittest.TestCase):
    def test_help_lists_the_new_capabilities(self):
        result = run_cli("--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        for command in ("repair", "deploy", "fleetmetrics"):
            self.assertIn(command, result.stdout)

    def test_fleetmetrics_prints_the_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "run.json").write_text(json.dumps(_record()))
            result = run_cli("fleetmetrics", "--run", tmp)
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout[result.stdout.index("{") :])
        self.assertEqual(payload["tasks"], 2)
        self.assertEqual(payload["failed"], 1)
        self.assertEqual(payload["retries"], 1)
        self.assertEqual(payload["critical_path_seconds"], 9.0)

    def test_repair_writes_one_packet_per_failed_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "run.json").write_text(json.dumps(_record()))
            result = run_cli("repair", "--run", tmp)
            packets = sorted((Path(tmp) / "packets").glob("*.repair.json"))
            payload = json.loads(packets[0].read_text())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([p.name for p in packets], ["T-b.repair.json"])
        self.assertIn("G-unit", " ".join(payload["instructions"]))
        self.assertEqual(payload["scope"], ["b.py"])
        self.assertIs(payload["escalate"], False)

    def test_repair_is_clean_when_nothing_failed(self):
        record = _record()
        for task in record["design"]["tasks"]:
            task["state"] = "merged"
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "run.json").write_text(json.dumps(record))
            result = run_cli("repair", "--run", tmp)
        self.assertEqual(result.returncode, 0)
        self.assertIn("no failed tasks", result.stdout.lower())

    def test_repair_escalates_once_the_budget_is_spent(self):
        record = _record()
        record["design"]["tasks"][1]["attempts"] = 5
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "run.json").write_text(json.dumps(record))
            run_cli("repair", "--run", tmp, "--max-attempts", "3")
            payload = json.loads((Path(tmp) / "packets" / "T-b.repair.json").read_text())
        self.assertIs(payload["escalate"], True)

    def test_deploy_starts_waits_probes_and_tears_down(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "server.py").write_text(SERVER)
            result = run_cli(
                "deploy", "--dir", tmp, "--cmd", f"{sys.executable} server.py",
                "--probe", "/healthz", "--seconds", "0.4", "--timeout", "10",
            )
        payload = json.loads(result.stdout[result.stdout.index("{") :])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIs(payload["ready"]["ready"], True)
        self.assertIs(payload["smoke"]["ok"], True)
        self.assertGreaterEqual(payload["soak"]["samples"], 1)
        self.assertEqual(payload["soak"]["failures"], 0)
        self.assertIsInstance(payload["exit_code"], int)

    def test_deploy_fails_loudly_when_the_probe_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "server.py").write_text(SERVER)
            result = run_cli(
                "deploy", "--dir", tmp, "--cmd", f"{sys.executable} server.py",
                "--probe", "/missing", "--timeout", "10",
            )
        payload = json.loads(result.stdout[result.stdout.index("{") :])
        self.assertEqual(result.returncode, 1)
        self.assertIs(payload["ok"], False)
        self.assertIs(payload["smoke"]["ok"], False)

    def test_deploy_fails_when_no_ready_line_arrives(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "silent.py").write_text("import time\ntime.sleep(30)\n")
            result = run_cli(
                "deploy", "--dir", tmp, "--cmd", f"{sys.executable} silent.py",
                "--probe", "/healthz", "--timeout", "1",
            )
        payload = json.loads(result.stdout[result.stdout.index("{") :])
        self.assertEqual(result.returncode, 1)
        self.assertIs(payload["ready"]["ready"], False)


if __name__ == "__main__":
    unittest.main()
