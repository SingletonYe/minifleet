# Task packet T-ratelimit - Token bucket limiter

Run: `20260910T112549Z-linksvc-c18a27`   Role: **builder**   Branch: `task/ratelimit`

## Goal

Implement linksvc/ratelimit.py: a thread-safe token bucket with bounded memory that reports a retry-after hint.

## Write scope (may not be exceeded)

- `linksvc/ratelimit.py`
- `tests/test_ratelimit.py`

Any file outside this scope is owned by another worker. Touching it will fail the fleet's ownership check at integration time.

## Frozen contracts

### C-INTERFACES - `contracts/C-INTERFACES.md`

Frozen module boundaries. Workers implement these exactly; they may not renegotiate them.

- `linksvc.ratelimit.TokenBucketLimiter`: TokenBucketLimiter(rate: float, burst: int) with allow(key: str) -> tuple[bool, float] returning (allowed, retry_after_seconds)
- `linksvc.server.serve`: serve(host: str, port: int, db_path: str, rate: float, burst: int) -> int
- `linksvc.storage.Link`: dataclass(code: str, url: str, created_at: float, expires_at: float | None)
- `linksvc.storage.LinkStats`: dataclass(code: str, url: str, clicks: int, created_at: float, expires_at: float | None)
- `linksvc.storage.LinkStore`: LinkStore(db_path: str) with create(url, code=None, ttl_seconds=None, idempotency_key=None) -> Link, get(code) -> Link | None, record_click(code) -> None, stats(code) -> LinkStats | None, count_links() -> int, close() -> None
- `linksvc.storage.errors`: InvalidUrl, InvalidAlias, DuplicateAlias (all subclasses of StorageError)

## Definition of done

- allow() returns (True, 0.0) while tokens remain and (False, retry_after) once exhausted
- tokens refill at the configured rate using a monotonic clock
- the number of tracked keys is bounded (evict least recently used)

## Tests you must write and run

- deterministic tests with an injected clock
- a test proving buckets are evicted

## Acceptance criteria this task feeds

- **A-3** (functional): A token-bucket limiter returns 429 with Retry-After once the burst is exhausted

## System budgets (enforced later, by the fleet)

- `redirect_p99_ms`: 75.0
- `redirect_rps`: 200.0

## Required return

Work only inside the worktree above. When finished, report a JSON object with:

```json
{"task_id": "T-ratelimit", "status": "submitted|failed", "files": ["..."], "commands": ["..."], "notes": "what you did and what you did not do"}
```

Report honestly: a task that is partly done and says so is more useful to the fleet than one that claims success. The fleet verifies every claim itself.