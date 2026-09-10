"""Acceptance for the follow-up intent: top links by clicks.

Written by the verification layer and frozen before the analytics and http
workers are dispatched. It talks to the running service, never to its internals.
"""

from __future__ import annotations

import unittest

from harness.client import Service


class AnalyticsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.service = Service(rate=100000, burst=100000).start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.service.kill()

    def clicks(self, url: str, count: int) -> dict:
        created = self.service.create(url)
        for _ in range(count):
            status, _ = self.service.redirect(created["code"])
            self.assertEqual(status, 302)
        return created

    def top(self, query: str = "") -> tuple[int, dict]:
        status, payload, _ = self.service.json("GET", f"/links/top{query}")
        return status, payload

    def test_top_links_are_ordered_by_clicks(self):
        # Distinct, high click counts so the ordering cannot be an accident of
        # whatever other tests put in this database.
        first = self.clicks("https://example.com/top/first", 30)
        second = self.clicks("https://example.com/top/second", 29)
        third = self.clicks("https://example.com/top/third", 28)

        status, payload = self.top()
        self.assertEqual(status, 200)
        self.assertEqual(payload["limit"], 10)
        codes = [row["code"] for row in payload["top"]]
        self.assertEqual(codes[:3], [first["code"], second["code"], third["code"]])
        self.assertEqual(payload["top"][0]["clicks"], 30)
        self.assertEqual(payload["top"][0]["url"], "https://example.com/top/first")

    def test_limit_is_respected(self):
        for index in range(3):
            self.clicks(f"https://example.com/top/limit/{index}", 4)
        for limit in (1, 2, 3):
            status, payload = self.top(f"?limit={limit}")
            self.assertEqual(status, 200)
            self.assertEqual(len(payload["top"]), limit)
            self.assertEqual(payload["limit"], limit)
            clicks = [row["clicks"] for row in payload["top"]]
            self.assertEqual(clicks, sorted(clicks, reverse=True))

    def test_ties_are_broken_by_creation_time(self):
        older = self.service.create("https://example.com/top/tie-old")
        newer = self.service.create("https://example.com/top/tie-new")
        # Two links with the same click count, but nothing else in this database
        # has exactly 13 clicks.
        for _ in range(13):
            self.service.redirect(older["code"])
            self.service.redirect(newer["code"])

        status, payload = self.top("?limit=2")
        self.assertEqual(status, 200)
        self.assertEqual([row["code"] for row in payload["top"]],
                         [older["code"], newer["code"]])

    def test_bad_limits_are_rejected(self):
        for query in ("?limit=0", "?limit=-3", "?limit=abc", "?limit=101", "?limit=1.5"):
            status, payload, _ = self.service.json("GET", f"/links/top{query}")
            self.assertEqual(status, 400, f"{query} should be rejected, got {status} {payload}")

    def test_repeated_calls_are_stable(self):
        self.clicks("https://example.com/top/stable", 2)
        first = self.top("?limit=5")[1]
        second = self.top("?limit=5")[1]
        self.assertEqual([row["code"] for row in first["top"]],
                         [row["code"] for row in second["top"]])


if __name__ == "__main__":
    unittest.main()
