# Fleet run report - A production-grade link shortener

- Run id: `20260910T112549Z-linksvc-c18a27`
- Intent: `linksvc`
- Dispatcher: `packet`
- Status: **passed**
- Verdict: pass: all required gates green

## Intent as received

Build a link shortener that a real team could put behind traffic: short links with optional custom alias and TTL, accurate click counting under concurrency, no data loss if the process is killed, a token-bucket rate limiter, health/readiness/metrics endpoints, structured logs, graceful shutdown, and a redirect path that stays under 75 ms at p99 while sustaining at least 200 req/s. Standard library only - no third-party packages - so the build is hermetic.

## Deliverables

- linksvc/storage.py - durable, concurrency-safe link store on SQLite (WAL, synchronous=FULL)
- linksvc/ratelimit.py - thread-safe token-bucket limiter with bounded memory
- linksvc/server.py + linksvc/api.py - HTTP surface on ThreadingHTTPServer with graceful shutdown
- tests/ - unit tests written by each worker for its own module
- README.md and ACCEPTANCE.md - how to run it and what was proven

## Design

- Components: 3
- Contracts: 1
- Tasks: 3
- Gates: 8

## Task graph

| task | role | depends on | owns | state | files |
| --- | --- | --- | --- | --- | --- |
| `T-storage` Durable link store | builder | - | 2 path(s) | merged | 2 |
| `T-ratelimit` Token bucket limiter | builder | - | 2 path(s) | merged | 2 |
| `T-http` HTTP surface | builder | T-storage, T-ratelimit | 5 path(s) | merged | 5 |

## Traceability: intent to evidence

| criterion | verdict | statement | gate | result | observation |
| --- | --- | --- | --- | --- | --- |
| A-1 | verified | POST /links creates a link, supports a custom alias and an idempotency | `G-acceptance` | pass | exit=0 (expected [0]) |
| A-2 | verified | GET /{code} answers 302 with the original URL, 404 for unknown codes a | `G-acceptance` | pass | exit=0 (expected [0]) |
| A-3 | verified | A token-bucket limiter returns 429 with Retry-After once the burst is  | `G-acceptance` | pass | exit=0 (expected [0]) |
| A-4 | verified | GET /links/{code}/stats reports an exact click count and /metrics expo | `G-acceptance` | pass | exit=0 (expected [0]) |
| A-5 | verified | Parallel redirects never lose a click: 8 threads x 40 redirects must l | `G-concurrency` | pass | exit=0 (expected [0]) |
| A-6 | verified | A SIGKILL during operation loses no link and no click counter after re | `G-durability` | pass | exit=0 (expected [0]) |
| A-7 | verified | SIGTERM produces a clean shutdown with exit status 0 | `G-durability` | pass | exit=0 (expected [0]) |
| A-8 | verified | Redirect latency stays at or below 75 ms at p99 | `G-budget-p99` | pass | redirect_p99_ms=30.641 <= 75 -> ok |
| A-9 | verified | The redirect path sustains at least 200 requests per second | `G-budget-rps` | pass | redirect_rps=896.485 >= 200 -> ok |
| A-10 | verified | The service starts as `python3 -m linksvc` and prints a machine-readab | `G-layout` | pass | all paths satisfied: ['linksvc/__main__.py', 'linksvc/storage.py', 'linksvc/ratelimit.py', 'linksvc/server.py', 'README. |

## Gate ledger

| gate | status | duration | metrics | detail |
| --- | --- | --- | --- | --- |
| `G-unit` | pass | 0.62s |  | exit=0 (expected [0]) |
| `G-unit` | pass | 0.06s |  | exit=0 (expected [0]) |
| `G-unit` | pass | 1.74s |  | exit=0 (expected [0]) |
| `G-acceptance` | pass | 1.64s |  | exit=0 (expected [0]) |
| `G-concurrency` | pass | 0.52s |  | exit=0 (expected [0]) |
| `G-durability` | fail | 0.52s |  | exit=1 (expected [0]) |
| `G-perf` | pass | 1.11s | {"redirect_p50_ms": 5.518, "redirect_p95_ms": 12.029, "redirect_p99_ms": 31.077, "redirect_rps": 892.765, "redirect_samples": 600.0} | exit=0 (expected [0]) |
| `G-layout` | pass | 0.00s |  | all paths satisfied: ['linksvc/__main__.py', 'linksvc/storage.py', 'linksvc/ratelimit.py', 'linksvc/server.py', 'README.md', 'ACCEPTANCE.md' |
| `G-budget-p99` | pass | 0.00s | {"redirect_p99_ms": 31.077} | redirect_p99_ms=31.077 <= 75 -> ok |
| `G-budget-rps` | pass | 0.00s | {"redirect_rps": 892.765} | redirect_rps=892.765 >= 200 -> ok |
| `G-acceptance` | pass | 1.62s |  | exit=0 (expected [0]) |
| `G-concurrency` | pass | 0.53s |  | exit=0 (expected [0]) |
| `G-durability` | pass | 0.60s |  | exit=0 (expected [0]) |
| `G-perf` | pass | 1.09s | {"redirect_p50_ms": 5.202, "redirect_p95_ms": 13.275, "redirect_p99_ms": 30.641, "redirect_rps": 896.485, "redirect_samples": 600.0} | exit=0 (expected [0]) |
| `G-layout` | pass | 0.00s |  | all paths satisfied: ['linksvc/__main__.py', 'linksvc/storage.py', 'linksvc/ratelimit.py', 'linksvc/server.py', 'README.md', 'ACCEPTANCE.md' |
| `G-budget-p99` | pass | 0.00s | {"redirect_p99_ms": 30.641} | redirect_p99_ms=30.641 <= 75 -> ok |
| `G-budget-rps` | pass | 0.00s | {"redirect_rps": 896.485} | redirect_rps=896.485 >= 200 -> ok |

## Not proven

- Every acceptance criterion has passing evidence.

## Risks carried forward

- non-functional criteria are only as trustworthy as the measurement harness

## Decisions taken by the architect

- acceptance criteria are owned by the verification layer, never by the worker that writes the code
- every task has a single writer and a disjoint write scope; concurrency without ownership is a merge queue
- frozen contracts: C-INTERFACES
