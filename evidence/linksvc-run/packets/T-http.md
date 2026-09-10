# Task packet T-http - HTTP surface

Run: `20260910T112549Z-linksvc-c18a27`   Role: **builder**   Branch: `task/http`

## Goal

Implement linksvc/__main__.py, linksvc/api.py, linksvc/server.py and linksvc/__init__.py: the HTTP API, routing, validation, ready line, logging and graceful shutdown.

## Write scope (may not be exceeded)

- `linksvc/__init__.py`
- `linksvc/api.py`
- `linksvc/server.py`
- `linksvc/__main__.py`
- `tests/test_api.py`

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

- every route in the contract answers with the specified status code and body
- the process prints the MINIFLEET_READY line with the bound port
- SIGTERM drains and exits 0
- each request emits one JSON log line on stderr

## Tests you must write and run

- an in-process test that starts the server on port 0 and drives the full API

## Acceptance criteria this task feeds

- **A-1** (functional): POST /links creates a link, supports a custom alias and an idempotency key, and rejects invalid input with 400 / 409
- **A-2** (functional): GET /{code} answers 302 with the original URL, 404 for unknown codes and 410 for expired links
- **A-4** (functional): GET /links/{code}/stats reports an exact click count and /metrics exposes the service counters
- **A-7** (operability): SIGTERM produces a clean shutdown with exit status 0
- **A-10** (operability): The service starts as `python3 -m linksvc` and prints a machine-readable ready line with its port

## System budgets (enforced later, by the fleet)

- `redirect_p99_ms`: 75.0
- `redirect_rps`: 200.0

## Required return

Work only inside the worktree above. When finished, report a JSON object with:

```json
{"task_id": "T-http", "status": "submitted|failed", "files": ["..."], "commands": ["..."], "notes": "what you did and what you did not do"}
```

Report honestly: a task that is partly done and says so is more useful to the fleet than one that claims success. The fleet verifies every claim itself.