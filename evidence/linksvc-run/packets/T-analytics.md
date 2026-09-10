# Task packet T-analytics - Click analytics

Run: `20260910T112549Z-linksvc-c18a27`   Role: **builder**   Branch: `task/analytics`

## Goal

Implement linksvc/analytics.py: aggregate clicks over the link store and return the most-clicked links with a bounded limit.

## Write scope (may not be exceeded)

- `linksvc/analytics.py`
- `tests/test_analytics.py`

Any file outside this scope is owned by another worker. Touching it will fail the fleet's ownership check at integration time.

## Frozen contracts

### C-INTERFACES - `contracts/C-INTERFACES.md`

Frozen module boundaries. Workers implement these exactly; they may not renegotiate them.

- `linksvc.analytics.top_links`: top_links(store: LinkStore, limit: int = 10) -> list[LinkStats] ordered by clicks desc, then created_at asc
- `linksvc.ratelimit.TokenBucketLimiter`: TokenBucketLimiter(rate: float, burst: int) with allow(key: str) -> tuple[bool, float] returning (allowed, retry_after_seconds)
- `linksvc.server.serve`: serve(host: str, port: int, db_path: str, rate: float, burst: int) -> int
- `linksvc.storage.Link`: dataclass(code: str, url: str, created_at: float, expires_at: float | None)
- `linksvc.storage.LinkStats`: dataclass(code: str, url: str, clicks: int, created_at: float, expires_at: float | None)
- `linksvc.storage.LinkStore`: LinkStore(db_path: str) with create(url, code=None, ttl_seconds=None, idempotency_key=None) -> Link, get(code) -> Link | None, record_click(code) -> None, stats(code) -> LinkStats | None, count_links() -> int, close() -> None
- `linksvc.storage.errors`: InvalidUrl, InvalidAlias, DuplicateAlias (all subclasses of StorageError)

## Definition of done

- top_links returns links ordered by clicks descending, ties broken by created_at ascending
- an out-of-range or non-integer limit raises InvalidLimit rather than returning a wrong page
- the aggregate runs as SQL, not by walking every link in Python

## Tests you must write and run

- unit tests for ordering, ties, limits and an empty store

## Acceptance criteria this task feeds

- **A-11** (functional): GET /links/top?limit=N returns the most-clicked links ordered by clicks (ties broken by creation time), defaults to 10 and rejects a limit outside 1..100 with 400

## System budgets (enforced later, by the fleet)

- `redirect_p99_ms`: 75.0
- `redirect_rps`: 200.0

## Required return

Work only inside the worktree above. When finished, report a JSON object with:

```json
{"task_id": "T-analytics", "status": "submitted|failed", "files": ["..."], "commands": ["..."], "notes": "what you did and what you did not do"}
```

Report honestly: a task that is partly done and says so is more useful to the fleet than one that claims success. The fleet verifies every claim itself.