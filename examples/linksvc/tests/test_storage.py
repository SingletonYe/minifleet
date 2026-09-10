"""Worker-authored unit tests for linksvc.storage (task T-storage)."""

from __future__ import annotations

import os
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path

from linksvc.storage import (
    DuplicateAlias,
    InvalidAlias,
    InvalidUrl,
    LinkStore,
)


class StoreTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="storage-test-")
        self.db = str(Path(self.tmp) / "links.db")
        self.store = LinkStore(self.db)

    def tearDown(self) -> None:
        self.store.close()


class CreateTests(StoreTestCase):
    def test_create_generates_a_code_and_reads_back(self):
        link = self.store.create("https://example.com/a")
        self.assertTrue(link.code)
        self.assertIsNone(link.expires_at)
        again = self.store.get(link.code)
        self.assertIsNotNone(again)
        self.assertEqual(again.url, "https://example.com/a")
        self.assertEqual(self.store.count_links(), 1)

    def test_custom_alias_is_honoured_and_conflicts_are_rejected(self):
        link = self.store.create("https://example.com/b", code="my-alias")
        self.assertEqual(link.code, "my-alias")
        with self.assertRaises(DuplicateAlias):
            self.store.create("https://example.com/c", code="my-alias")
        self.assertEqual(self.store.count_links(), 1)

    def test_invalid_urls_and_aliases_are_rejected(self):
        for bad in ("", "notaurl", "ftp://example.com/x", "https://", None, "x" * 3000):
            with self.assertRaises(InvalidUrl, msg=f"{bad!r} should be rejected"):
                self.store.create(bad)  # type: ignore[arg-type]
        for bad_alias in ("bad alias!", "", "x" * 65, "sl/ash"):
            with self.assertRaises(InvalidAlias, msg=f"{bad_alias!r} should be rejected"):
                self.store.create("https://example.com/d", code=bad_alias)

    def test_idempotency_key_never_creates_a_second_link(self):
        first = self.store.create("https://example.com/idem", idempotency_key="k-1")
        second = self.store.create("https://example.com/other", idempotency_key="k-1")
        self.assertEqual(first.code, second.code)
        self.assertEqual(second.url, "https://example.com/idem")
        self.assertEqual(self.store.count_links(), 1)

    def test_ttl_sets_expiry_and_expired_links_are_still_readable(self):
        link = self.store.create("https://example.com/ttl", ttl_seconds=0.25)
        self.assertIsNotNone(link.expires_at)
        self.assertFalse(link.expired())
        time.sleep(0.35)
        replayed = self.store.get(link.code)
        self.assertIsNotNone(replayed, "an expired link is expired, not missing")
        self.assertTrue(replayed.expired())
        self.assertIsNone(self.store.get("missing-code"))

    def test_stats_report_an_exact_click_count(self):
        link = self.store.create("https://example.com/stats")
        for _ in range(5):
            self.store.record_click(link.code)
        stats = self.store.stats(link.code)
        self.assertIsNotNone(stats)
        self.assertEqual(stats.clicks, 5)
        self.assertEqual(stats.url, "https://example.com/stats")
        self.assertIsNone(self.store.stats("missing-code"))


class ConcurrencyTests(StoreTestCase):
    def test_parallel_clicks_are_never_lost(self):
        link = self.store.create("https://example.com/hot")
        threads_count, per_thread = 8, 40
        barrier = threading.Barrier(threads_count)

        def hammer() -> None:
            barrier.wait()
            for _ in range(per_thread):
                self.store.record_click(link.code)

        threads = [threading.Thread(target=hammer) for _ in range(threads_count)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)
        self.assertEqual(self.store.stats(link.code).clicks, threads_count * per_thread)

    def test_writes_from_a_second_connection_are_visible(self):
        link = self.store.create("https://example.com/shared")
        self.store.record_click(link.code)
        self.store.close()
        self.store = LinkStore(self.db)
        self.assertEqual(self.store.stats(link.code).clicks, 1)


class DurabilityTests(StoreTestCase):
    def test_committed_rows_survive_an_abrupt_process_death(self):
        link = self.store.create("https://example.com/durable")
        self.store.record_click(link.code)
        # Simulate SIGKILL: drop the connection without close(), no flush hook.
        self.store._conn.close()  # noqa: SLF001 - the test owns the handle

        reopened = LinkStore(self.db)
        try:
            self.assertEqual(reopened.stats(link.code).clicks, 1)
            self.assertEqual(reopened.get(link.code).url, "https://example.com/durable")
        finally:
            reopened.close()
        self.store = LinkStore(self.db)

    def test_database_uses_wal_and_full_synchronous(self):
        conn = sqlite3.connect(self.db)
        try:
            self.assertEqual(str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower(), "wal")
        finally:
            conn.close()
        # synchronous is per-connection, so ask the store's own handle.
        self.assertEqual(int(self.store._conn.execute("PRAGMA synchronous").fetchone()[0]), 2)
        self.assertTrue(os.path.exists(self.db))


if __name__ == "__main__":
    unittest.main()
