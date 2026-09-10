"""Operability acceptance: crash durability and graceful shutdown."""

from __future__ import annotations

import signal
import tempfile
import unittest
from pathlib import Path

from harness.client import Service

LINKS = 40


class DurabilityTest(unittest.TestCase):
    def test_survives_sigkill_and_reopens_same_database(self):
        db = str(Path(tempfile.mkdtemp(prefix="linksvc-durable-")) / "links.db")
        service = Service(db=db, rate=100000, burst=100000).start()
        codes = []
        for index in range(LINKS):
            created = service.create(f"https://example.com/{index}")
            codes.append(created["code"])
        for code in codes[:10]:
            service.redirect(code)
        service.proc.kill()          # SIGKILL: no clean shutdown, no flush callback
        service.proc.wait(timeout=10)

        reopened = Service(db=db, rate=100000, burst=100000).start()
        try:
            # The click recorded before the crash must already be visible.
            status, payload, _ = reopened.json("GET", f"/links/{codes[0]}/stats")
            self.assertEqual(status, 200)
            self.assertEqual(payload["clicks"], 1, "click counter lost after crash")
            for index, code in enumerate(codes):
                status, head = reopened.redirect(code)
                self.assertEqual(status, 302, f"link {index} lost after crash")
                self.assertEqual(head.get("location"), f"https://example.com/{index}")
            # ... and clicks recorded after the restart must still be counted.
            status, payload, _ = reopened.json("GET", f"/links/{codes[0]}/stats")
            self.assertEqual(status, 200)
            self.assertEqual(payload["clicks"], 2, "click recorded after restart was not counted")
            fresh = reopened.create("https://example.com/after-crash")
            self.assertTrue(fresh["code"])
        finally:
            reopened.kill()

    def test_sigterm_shuts_down_cleanly(self):
        service = Service().start()
        service.create("https://example.com/graceful")
        code = service.stop(signal.SIGTERM, timeout=10)
        self.assertEqual(code, 0, "SIGTERM must produce a clean exit status")


if __name__ == "__main__":
    unittest.main()
