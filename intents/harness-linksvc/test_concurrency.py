"""Concurrency acceptance: no click may be lost under parallel redirects."""

from __future__ import annotations

import threading
import unittest

from harness.client import Service

THREADS = 8
PER_THREAD = 40


class ConcurrencyTest(unittest.TestCase):
    def test_no_lost_clicks(self):
        with Service(rate=100000, burst=100000).start() as service:
            created = service.create("https://example.com/hot")
            code = created["code"]
            errors: list[str] = []

            def hammer() -> None:
                for _ in range(PER_THREAD):
                    status, _ = service.redirect(code)
                    if status != 302:
                        errors.append(f"unexpected status {status}")

            workers = [threading.Thread(target=hammer) for _ in range(THREADS)]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join(timeout=60)

            self.assertEqual(errors, [])
            status, payload, _ = service.json("GET", f"/links/{code}/stats")
            self.assertEqual(status, 200)
            self.assertEqual(payload["clicks"], THREADS * PER_THREAD)


if __name__ == "__main__":
    unittest.main()
