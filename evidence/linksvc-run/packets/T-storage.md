# Task packet T-storage - Durable link store

Run: `20260910T112549Z-linksvc-c18a27`   Role: **builder**   Branch: `task/storage`

## Goal

Implement linksvc/storage.py: the SQLite-backed store with exact click counting and crash durability.

## Write scope (may not be exceeded)

- `linksvc/storage.py`
- `tests/test_storage.py`

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

- LinkStore.create/get/record_click/stats/count_links behave as specified
- an idempotency key never creates a second link
- expired links are reported as expired rather than missing
- 100 parallel record_click calls on one code add exactly 100 clicks

## Tests you must write and run

- unit tests for create/alias/ttl/idempotency
- a threaded test proving no clicks are lost

## Acceptance criteria this task feeds

- **A-1** (functional): POST /links creates a link, supports a custom alias and an idempotency key, and rejects invalid input with 400 / 409
- **A-4** (functional): GET /links/{code}/stats reports an exact click count and /metrics exposes the service counters
- **A-5** (safety): Parallel redirects never lose a click: 8 threads x 40 redirects must land as exactly 320
- **A-6** (safety): A SIGKILL during operation loses no link and no click counter after restart on the same database

## System budgets (enforced later, by the fleet)

- `redirect_p99_ms`: 75.0
- `redirect_rps`: 200.0

## Required return

Work only inside the worktree above. When finished, report a JSON object with:

```json
{"task_id": "T-storage", "status": "submitted|failed", "files": ["..."], "commands": ["..."], "notes": "what you did and what you did not do"}
```

Report honestly: a task that is partly done and says so is more useful to the fleet than one that claims success. The fleet verifies every claim itself.