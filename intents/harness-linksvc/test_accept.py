"""Functional acceptance: the HTTP contract of linksvc."""

from __future__ import annotations

import json
import time
import unittest

from harness.client import Service


class AcceptanceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.service = Service().start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.service.kill()

    def test_health_and_readiness(self):
        status, payload, _ = self.service.json("GET", "/healthz")
        self.assertEqual(status, 200)
        self.assertEqual(payload.get("status"), "ok")
        status, payload, _ = self.service.json("GET", "/readyz")
        self.assertEqual(status, 200)
        self.assertEqual(payload.get("status"), "ready")

    def test_create_and_redirect(self):
        created = self.service.create("https://example.com/one")
        self.assertTrue(created["code"])
        self.assertTrue(created["short_url"].endswith(created["code"]))
        self.assertIsNone(created["expires_at"])
        status, head = self.service.redirect(created["code"])
        self.assertEqual(status, 302)
        self.assertEqual(head.get("location"), "https://example.com/one")

    def test_custom_alias_and_conflict(self):
        alias = "fleet-demo-alias"
        created = self.service.create("https://example.com/alias", alias=alias)
        self.assertEqual(created["code"], alias)
        status, payload, _ = self.service.json("POST", "/links", {"url": "https://example.com/other", "alias": alias})
        self.assertEqual(status, 409)
        self.assertIn("error", payload)

    def test_idempotency_key_reuses_code(self):
        key = "idem-12345"
        first = self.service.create("https://example.com/idem", idempotency_key=key)
        second = self.service.create("https://example.com/idem", idempotency_key=key)
        self.assertEqual(first["code"], second["code"])

    def test_rejects_invalid_url_and_bad_alias(self):
        for payload in ({"url": "notaurl"}, {"url": ""}, {"url": "ftp://example.com/x"},
                        {"url": "https://example.com/x", "alias": "bad alias!"}):
            status, body, _ = self.service.json("POST", "/links", payload)
            self.assertEqual(status, 400, f"payload {payload} should be rejected, got {body}")

    def test_unknown_code_is_404(self):
        status, head = self.service.redirect("definitely-not-here")
        self.assertEqual(status, 404)

    def test_ttl_expiry_returns_410(self):
        created = self.service.create("https://example.com/ttl", ttl_seconds=1)
        self.assertIsNotNone(created["expires_at"])
        self.assertEqual(self.service.redirect(created["code"])[0], 302)
        time.sleep(1.3)
        self.assertEqual(self.service.redirect(created["code"])[0], 410)

    def test_stats_track_clicks(self):
        created = self.service.create("https://example.com/stats")
        for _ in range(3):
            self.service.redirect(created["code"])
        status, payload, _ = self.service.json("GET", f"/links/{created['code']}/stats")
        self.assertEqual(status, 200)
        self.assertEqual(payload["clicks"], 3)
        self.assertEqual(payload["url"], "https://example.com/stats")
        status, _, _ = self.service.json("GET", "/links/nope/stats")
        self.assertEqual(status, 404)

    def test_metrics_endpoint_exposes_counters(self):
        self.service.create("https://example.com/metrics")
        status, payload, _ = self.service.json("GET", "/metrics")
        self.assertEqual(status, 200)
        for key in ("links_created", "redirects", "rate_limited", "not_found"):
            self.assertIn(key, payload)
            self.assertIsInstance(payload[key], (int, float))
        self.assertEqual(payload, json.loads(json.dumps(payload)))

    def test_rate_limit_returns_429_with_retry_after(self):
        with Service(rate=5, burst=5).start() as limited:
            codes = []
            for _ in range(8):
                status, _, head = limited.json("POST", "/links", {"url": "https://example.com/rl"})
                codes.append(status)
                if status == 429:
                    self.assertIn("retry-after", head)
                    break
            self.assertIn(429, codes, f"expected a 429 within 8 requests, got {codes}")


if __name__ == "__main__":
    unittest.main()
