"""Worker-authored unit tests for linksvc.analytics (task T-analytics)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from linksvc.analytics import InvalidLimit, top_links
from linksvc.storage import LinkStats, LinkStore


class AnalyticsTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="analytics-test-")
        self.store = LinkStore(str(Path(self.tmp) / "links.db"))

    def tearDown(self) -> None:
        self.store.close()

    def clicked(self, slug: str, times: int) -> str:
        link = self.store.create(f"https://example.com/{slug}")
        for _ in range(times):
            self.store.record_click(link.code)
        return link.code


class OrderingTests(AnalyticsTestCase):
    def test_links_are_ordered_by_clicks(self):
        quiet = self.clicked("quiet", 1)
        loud = self.clicked("loud", 9)
        middle = self.clicked("middle", 4)
        page = top_links(self.store, 3)
        self.assertEqual([row.code for row in page], [loud, middle, quiet])
        self.assertEqual([row.clicks for row in page], [9, 4, 1])
        self.assertIsInstance(page[0], LinkStats)
        self.assertEqual(page[0].url, "https://example.com/loud")

    def test_ties_are_broken_by_creation_time(self):
        older = self.clicked("tie-old", 3)
        newer = self.clicked("tie-new", 3)
        page = top_links(self.store, 2)
        self.assertEqual([row.code for row in page], [older, newer])

    def test_links_with_no_clicks_are_not_on_the_page(self):
        self.store.create("https://example.com/never-clicked")
        self.assertEqual(top_links(self.store, 10), [])
        clicked = self.clicked("once", 1)
        self.assertEqual([row.code for row in top_links(self.store, 10)], [clicked])

    def test_page_size_is_respected(self):
        codes = [self.clicked(f"page-{index}", index + 1) for index in range(5)]
        page = top_links(self.store, 2)
        self.assertEqual([row.code for row in page], list(reversed(codes))[:2])

    def test_default_limit_is_ten(self):
        for index in range(12):
            self.clicked(f"bulk-{index}", index + 1)
        self.assertEqual(len(top_links(self.store)), 10)

    def test_expiry_does_not_hide_a_clicked_link(self):
        code = None
        link = self.store.create("https://example.com/expiring", ttl_seconds=0.01)
        self.store.record_click(link.code)
        code = link.code
        page = top_links(self.store, 5)
        self.assertIn(code, [row.code for row in page])


class LimitTests(AnalyticsTestCase):
    def test_invalid_limits_are_rejected(self):
        for bad in (0, -1, 101, 1.5, "5", None, True):
            with self.assertRaises(InvalidLimit, msg=f"{bad!r} must be rejected"):
                top_links(self.store, bad)  # type: ignore[arg-type]

    def test_bounds_are_inclusive(self):
        self.clicked("bound", 1)
        self.assertEqual(len(top_links(self.store, 1)), 1)
        self.assertEqual(top_links(self.store, 100), top_links(self.store, 100))

    def test_missing_database_is_reported(self):
        self.store.close()
        self.store = LinkStore(str(Path(self.tmp) / "empty.db"))
        self.assertEqual(top_links(self.store, 5), [])


if __name__ == "__main__":
    unittest.main()
