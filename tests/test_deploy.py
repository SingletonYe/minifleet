"""Unit tests for deployment supervision and probes (acceptance A-3 and A-4)."""

from __future__ import annotations

import http.server
import socketserver
import sys
import tempfile
import threading
import time
import unittest

from minifleet.deploy import Supervisor, smoke, soak


class SupervisorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    @staticmethod
    def _script(body: str) -> list[str]:
        return [sys.executable, "-c", body]

    def test_ready_line_is_parsed_and_the_child_is_torn_down(self):
        supervisor = Supervisor(
            self._script(
                'import time; print(\'MINIFLEET_READY {"port": 4242}\', flush=True); time.sleep(30)'
            ),
            cwd=self.tmp.name,
            timeout=10.0,
        )
        self.assertFalse(supervisor.running)
        supervisor.start()
        ready = supervisor.wait_ready()
        self.assertTrue(ready["ready"], ready)
        self.assertEqual(ready["port"], 4242)
        self.assertLess(ready["seconds"], 10.0)
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
        self.assertIsNone(ready["port"])
        self.assertLess(time.time() - started, 8.0)
        self.assertTrue(ready["stdout_tail"] is not None)
        supervisor.stop()
        self.assertFalse(supervisor.running)

    def test_child_that_exits_early_reports_not_ready(self):
        supervisor = Supervisor(self._script("print('nope')"), cwd=self.tmp.name, timeout=10.0)
        supervisor.start()
        ready = supervisor.wait_ready()
        self.assertFalse(ready["ready"])
        self.assertIn("nope", ready["stdout_tail"])
        supervisor.stop()

    def test_ready_line_without_a_payload_yields_no_port(self):
        supervisor = Supervisor(
            self._script("import time; print('MINIFLEET_READY', flush=True); time.sleep(30)"),
            cwd=self.tmp.name,
            timeout=5.0,
        )
        supervisor.start()
        ready = supervisor.wait_ready()
        self.assertTrue(ready["ready"])
        self.assertIsNone(ready["port"])
        supervisor.stop()

    def test_soak_style_kill_escalation_when_the_child_ignores_sigterm(self):
        body = (
            "import signal, time\n"
            "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            "print('MINIFLEET_READY {\"port\": 1}', flush=True)\n"
            "time.sleep(60)\n"
        )
        supervisor = Supervisor(self._script(body), cwd=self.tmp.name, timeout=5.0)
        supervisor.start()
        self.assertTrue(supervisor.wait_ready()["ready"])
        code = supervisor.stop(timeout=0.5)
        self.assertEqual(code, -9)
        self.assertFalse(supervisor.running)


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path == "/boom":
            self.send_response(503)
            self.end_headers()
            self.wfile.write(b"nope")
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"status": "ok"}')

    def log_message(self, *args):  # silence
        return


class ProbeTests(unittest.TestCase):
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

    def test_smoke_reports_status_ok_and_seconds(self):
        good = smoke(f"http://127.0.0.1:{self.port}/healthz", timeout=5.0)
        self.assertEqual(good["status"], 200)
        self.assertIs(good["ok"], True)
        self.assertGreaterEqual(good["seconds"], 0.0)
        bad = smoke(f"http://127.0.0.1:{self.port}/boom", timeout=5.0)
        self.assertEqual(bad["status"], 503)
        self.assertIs(bad["ok"], False)

    def test_soak_counts_samples_and_reports_latency(self):
        result = soak(f"http://127.0.0.1:{self.port}/healthz", seconds=0.8, interval=0.2)
        self.assertGreaterEqual(result["samples"], 3)
        self.assertEqual(result["failures"], 0)
        self.assertIs(result["ok"], True)
        self.assertGreaterEqual(result["p50_ms"], 0.0)
        self.assertGreaterEqual(result["max_ms"], result["p50_ms"])

    def test_soak_flags_a_dead_endpoint_instead_of_raising(self):
        result = soak("http://127.0.0.1:1/nothing", seconds=0.4, interval=0.15, timeout=0.5)
        self.assertGreater(result["samples"], 0)
        self.assertEqual(result["failures"], result["samples"])
        self.assertIs(result["ok"], False)

    def test_soak_flags_error_responses(self):
        result = soak(f"http://127.0.0.1:{self.port}/boom", seconds=0.4, interval=0.2)
        self.assertEqual(result["failures"], result["samples"])
        self.assertIs(result["ok"], False)


if __name__ == "__main__":
    unittest.main()
