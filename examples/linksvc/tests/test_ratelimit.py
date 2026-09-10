"""Worker-authored unit tests for linksvc.ratelimit (task T-ratelimit)."""

from __future__ import annotations

import threading
import unittest

from linksvc.ratelimit import TokenBucketLimiter


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class AllowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeClock()
        self.limiter = TokenBucketLimiter(rate=5.0, burst=5, clock=self.clock)

    def test_burst_is_allowed_then_the_next_call_is_refused_with_a_hint(self):
        for _ in range(5):
            self.assertEqual(self.limiter.allow("k"), (True, 0.0))
        allowed, retry_after = self.limiter.allow("k")
        self.assertFalse(allowed)
        self.assertAlmostEqual(retry_after, 0.2, places=6)

    def test_refill_uses_the_clock_rate(self):
        for _ in range(5):
            self.limiter.allow("k")
        self.clock.advance(0.2)          # exactly one token at 5/s
        allowed, _ = self.limiter.allow("k")
        self.assertTrue(allowed, "one token should have refilled after 0.2s")
        self.assertFalse(self.limiter.allow("k")[0])

    def test_tokens_never_exceed_the_burst(self):
        for _ in range(5):
            self.limiter.allow("k")
        self.clock.advance(3600)
        for _ in range(5):
            self.assertTrue(self.limiter.allow("k")[0])
        self.assertFalse(self.limiter.allow("k")[0])

    def test_keys_are_independent(self):
        for _ in range(5):
            self.limiter.allow("a")
        self.assertFalse(self.limiter.allow("a")[0])
        self.assertTrue(self.limiter.allow("b")[0])

    def test_retry_after_does_not_consume_a_token(self):
        self.limiter.allow("k")
        self.assertAlmostEqual(self.limiter.retry_after("k"), 0.0, places=6)
        for _ in range(4):
            self.limiter.allow("k")
        self.assertAlmostEqual(self.limiter.retry_after("k"), 0.2, places=6)
        self.assertAlmostEqual(self.limiter.retry_after("k"), 0.2, places=6)

    def test_unknown_key_has_no_wait(self):
        self.assertEqual(self.limiter.retry_after("never-seen"), 0.0)


class BoundedMemoryTests(unittest.TestCase):
    def test_tracked_keys_stay_bounded_and_evict_least_recently_used(self):
        clock = FakeClock()
        limiter = TokenBucketLimiter(rate=1.0, burst=1, max_keys=3, clock=clock)
        for key in ("a", "b", "c"):
            self.assertTrue(limiter.allow(key)[0])
        self.assertEqual(limiter.tracked_keys(), 3)

        self.assertTrue(limiter.allow("a")[0] is False)   # touch "a", it is full
        self.assertTrue(limiter.allow("d")[0])            # evicts the oldest: "b"
        self.assertEqual(limiter.tracked_keys(), 3)
        self.assertEqual(limiter.retry_after("b"), 0.0, "evicted key starts fresh")

    def test_invalid_configuration_is_rejected(self):
        for kwargs in ({"rate": 0}, {"burst": 0}, {"max_keys": 0}):
            with self.assertRaises(ValueError):
                TokenBucketLimiter(**{**{"rate": 1.0, "burst": 1}, **kwargs})


class ThreadSafetyTests(unittest.TestCase):
    def test_exactly_burst_threads_win_when_they_race(self):
        limiter = TokenBucketLimiter(rate=0.001, burst=25, clock=FakeClock())
        wins: list[bool] = []
        lock = threading.Lock()
        barrier = threading.Barrier(50)

        def contender() -> None:
            barrier.wait()
            allowed, _ = limiter.allow("hot")
            with lock:
                wins.append(allowed)

        threads = [threading.Thread(target=contender) for _ in range(50)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        self.assertEqual(len(wins), 50)
        self.assertEqual(sum(1 for win in wins if win), 25)


if __name__ == "__main__":
    unittest.main()
