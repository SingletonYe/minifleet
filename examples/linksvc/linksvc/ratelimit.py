"""Thread-safe token bucket limiter with bounded memory.

The limiter has to answer two questions fast, from several threads at once:
is this key allowed right now, and if not, how long until it would be. It also
has to survive a hostile key space, so the number of tracked buckets is capped
and the least recently used bucket is evicted.

The clock is injectable so the refill behaviour is testable without sleeping.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Callable

__all__ = ["TokenBucketLimiter"]

DEFAULT_MAX_KEYS = 4096


class TokenBucketLimiter:
    """``rate`` tokens per second, never more than ``burst`` at once, per key."""

    def __init__(
        self,
        rate: float,
        burst: int,
        max_keys: int = DEFAULT_MAX_KEYS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if rate <= 0:
            raise ValueError("rate must be positive")
        if burst <= 0:
            raise ValueError("burst must be positive")
        if max_keys <= 0:
            raise ValueError("max_keys must be positive")
        self.rate = float(rate)
        self.burst = float(burst)
        self.max_keys = int(max_keys)
        self._clock = clock
        self._lock = threading.Lock()
        self._buckets: OrderedDict[str, tuple[float, float]] = OrderedDict()

    # -- public API ------------------------------------------------------
    def allow(self, key: str) -> tuple[bool, float]:
        """Consume one token.

        Returns ``(True, 0.0)`` when the request may proceed and
        ``(False, retry_after_seconds)`` when it must wait.
        """

        now = self._clock()
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                tokens, last = self.burst, now
            else:
                tokens, last = bucket
                tokens = min(self.burst, tokens + max(0.0, now - last) * self.rate)

            if tokens >= 1.0:
                self._remember(key, tokens - 1.0, now)
                return True, 0.0

            self._remember(key, tokens, now)
            return False, (1.0 - tokens) / self.rate

    def retry_after(self, key: str) -> float:
        """Seconds until this key would have a token, without consuming one."""

        now = self._clock()
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                return 0.0
            tokens, last = bucket
            tokens = min(self.burst, tokens + max(0.0, now - last) * self.rate)
            return 0.0 if tokens >= 1.0 else (1.0 - tokens) / self.rate

    def tracked_keys(self) -> int:
        with self._lock:
            return len(self._buckets)

    def reset(self, key: str | None = None) -> None:
        with self._lock:
            if key is None:
                self._buckets.clear()
            else:
                self._buckets.pop(key, None)

    # -- internals -------------------------------------------------------
    def _remember(self, key: str, tokens: float, now: float) -> None:
        """Store the bucket and evict the least recently used one when full."""

        self._buckets[key] = (tokens, now)
        self._buckets.move_to_end(key)
        while len(self._buckets) > self.max_keys:
            self._buckets.popitem(last=False)
