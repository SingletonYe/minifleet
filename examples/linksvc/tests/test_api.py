"""Worker-authored tests for linksvc's HTTP surface (task T-http).

These run the real server on an ephemeral port and talk to it over a socket, so
the routing, the status codes and the wire format are all exercised.
"""

from __future__ import annotations

import http.client
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from linksvc.server import READY_PREFIX, build_server


class RunningService:
    """In-process server on port 0, with the same handlers production uses."""

    def __init__(self, rate: float = 1000.0, burst: int = 1000, tmp: str | None = None) -> None:
        self.db = str(Path(tmp or tempfile.mkdtemp(prefix="api-test-")) / "links.db")
        self.server = build_server("127.0.0.1", 0, self.db, rate, burst)
        self.port = self.server.port
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05})
        self.thread.daemon = True

    def __enter__(self) -> "RunningService":
        self.thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.server.shutdown()
        self.thread.join(timeout=10)
        self.server.server_close()
        self.server.api.state.store.close()

    def request(self, method: str, path: str, body: dict | None = None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            payload = None if body is None else json.dumps(body).encode()
            conn.request(method, path, body=payload, headers={"Content-Type": "application/json"})
            response = conn.getresponse()
            data = response.read()
            return response.status, {k.lower(): v for k, v in response.getheaders()}, data
        finally:
            conn.close()

    def json(self, method: str, path: str, body: dict | None = None):
        status, headers, data = self.request(method, path, body)
        return status, (json.loads(data) if data else {}), headers


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.service = RunningService().__enter__()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.service.__exit__(None, None, None)

    def create(self, url: str = "https://example.com/x", **extra) -> dict:
        status, payload, _ = self.service.json("POST", "/links", {"url": url, **extra})
        self.assertEqual(status, 201, payload)
        return payload

    def test_health_readiness_and_metrics(self):
        status, payload, _ = self.service.json("GET", "/healthz")
        self.assertEqual((status, payload["status"]), (200, "ok"))
        status, payload, _ = self.service.json("GET", "/readyz")
        self.assertEqual((status, payload["status"]), (200, "ready"))
        status, payload, _ = self.service.json("GET", "/metrics")
        self.assertEqual(status, 200)
        for key in ("links_created", "redirects", "rate_limited", "not_found"):
            self.assertIn(key, payload)
            self.assertIsInstance(payload[key], (int, float))

    def test_create_returns_a_short_url_and_redirects(self):
        created = self.create("https://example.com/one")
        self.assertTrue(created["short_url"].endswith(created["code"]))
        self.assertIsNone(created["expires_at"])
        status, headers, _ = self.service.request("GET", f"/{created['code']}")
        self.assertEqual(status, 302)
        self.assertEqual(headers["location"], "https://example.com/one")

    def test_alias_conflict_is_409_and_invalid_input_is_400(self):
        self.create("https://example.com/alias", alias="worker-alias")
        status, payload, _ = self.service.json("POST", "/links", {"url": "https://example.com/2", "alias": "worker-alias"})
        self.assertEqual(status, 409)
        self.assertIn("error", payload)
        for bad in ({"url": "nope"}, {"url": ""}, {"url": "ftp://example.com/a"},
                    {"url": "https://example.com/a", "alias": "no spaces"},
                    {"url": "https://example.com/a", "ttl_seconds": -1}):
            status, _, _ = self.service.json("POST", "/links", bad)
            self.assertEqual(status, 400, f"{bad} must be rejected")

    def test_idempotency_key_returns_the_same_code(self):
        first = self.create("https://example.com/idem", idempotency_key="worker-key")
        second = self.create("https://example.com/idem", idempotency_key="worker-key")
        self.assertEqual(first["code"], second["code"])

    def test_unknown_and_expired_codes(self):
        status, _, _ = self.service.request("GET", "/nope-nope")
        self.assertEqual(status, 404)
        created = self.create("https://example.com/ttl", ttl_seconds=0.5)
        self.assertIsNotNone(created["expires_at"])
        time.sleep(0.7)
        status, headers, _ = self.service.request("GET", f"/{created['code']}")
        self.assertEqual(status, 410)

    def test_stats_count_clicks_exactly(self):
        created = self.create("https://example.com/stats")
        for _ in range(4):
            self.service.request("GET", f"/{created['code']}")
        status, payload, _ = self.service.json("GET", f"/links/{created['code']}/stats")
        self.assertEqual(status, 200)
        self.assertEqual(payload["clicks"], 4)
        status, _, _ = self.service.json("GET", "/links/missing/stats")
        self.assertEqual(status, 404)

    def test_top_links_are_ordered_by_clicks(self):
        busy = self.create("https://example.com/top/busy")
        quiet = self.create("https://example.com/top/quiet")
        for _ in range(6):
            self.service.request("GET", f"/{busy['code']}")
        for _ in range(5):
            self.service.request("GET", f"/{quiet['code']}")

        status, payload, _ = self.service.json("GET", "/links/top?limit=2")
        self.assertEqual(status, 200)
        self.assertEqual(payload["limit"], 2)
        codes = [row["code"] for row in payload["top"]]
        self.assertEqual(codes, [busy["code"], quiet["code"]])
        self.assertEqual(payload["top"][0]["clicks"], 6)
        self.assertIn("url", payload["top"][0])

    def test_top_defaults_to_ten_and_rejects_bad_limits(self):
        status, payload, _ = self.service.json("GET", "/links/top")
        self.assertEqual(status, 200)
        self.assertEqual(payload["limit"], 10)
        for query in ("?limit=0", "?limit=101", "?limit=abc", "?limit=1.5", "?limit=-2"):
            status, body, _ = self.service.json("GET", f"/links/top{query}")
            self.assertEqual(status, 400, f"{query} must be rejected, got {body}")

    def test_unsupported_methods_and_paths(self):
        status, headers, _ = self.service.request("DELETE", "/links")
        self.assertEqual(status, 405)
        self.assertIn("allow", headers)
        status, _, _ = self.service.request("GET", "/a/b/c/d")
        self.assertEqual(status, 404)


class RateLimitTests(unittest.TestCase):
    def test_burst_is_enforced_with_retry_after(self):
        with RunningService(rate=5.0, burst=5) as service:
            codes = []
            for _ in range(8):
                status, _, headers = service.json("POST", "/links", {"url": "https://example.com/rl"})
                codes.append(status)
                if status == 429:
                    self.assertIn("retry-after", headers)
                    break
            self.assertIn(429, codes, f"expected a 429 within 8 writes, got {codes}")
            status, payload, _ = service.json("GET", "/metrics")
            self.assertGreaterEqual(payload["rate_limited"], 1)

    def test_redirects_are_not_throttled_by_the_write_bucket(self):
        with RunningService(rate=1.0, burst=1) as service:
            status, payload, _ = service.json("POST", "/links", {"url": "https://example.com/read"})
            self.assertEqual(status, 201)
            for _ in range(5):
                status, _, _ = service.request("GET", f"/{payload['code']}")
                self.assertEqual(status, 302)


class ProcessTests(unittest.TestCase):
    def test_ready_line_and_sigterm_exit_zero(self):
        tmp = tempfile.mkdtemp(prefix="linksvc-proc-")
        proc = subprocess.Popen(
            [sys.executable, "-m", "linksvc", "--host", "127.0.0.1", "--port", "0",
             "--db", str(Path(tmp) / "links.db"), "--rate", "100", "--burst", "100"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            cwd=os.getcwd(), env=dict(os.environ, PYTHONUNBUFFERED="1"),
        )
        try:
            assert proc.stdout is not None
            line = proc.stdout.readline()
            self.assertTrue(line.startswith(READY_PREFIX), f"unexpected ready line: {line!r}")
            payload = json.loads(line[len(READY_PREFIX):].strip())
            self.assertIsInstance(payload["port"], int)
            self.assertGreater(payload["port"], 0)
        finally:
            proc.send_signal(signal.SIGTERM)
            status = proc.wait(timeout=15)
        self.assertEqual(status, 0, "SIGTERM must be a clean shutdown")


if __name__ == "__main__":
    unittest.main()
