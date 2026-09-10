# Intent as received

Build a link shortener that a real team could put behind traffic: short links with optional custom alias and TTL, accurate click counting under concurrency, no data loss if the process is killed, a token-bucket rate limiter, health/readiness/metrics endpoints, structured logs, graceful shutdown, and a redirect path that stays under 75 ms at p99 while sustaining at least 200 req/s. Standard library only - no third-party packages - so the build is hermetic.

```json
{
  "id": "linksvc",
  "title": "A production-grade link shortener",
  "summary": "A dependency-free link shortener that is safe to run in production: durable under crash, correct under concurrency, rate limited, observable, and fast.",
  "intent": "Build a link shortener that a real team could put behind traffic: short links with optional custom alias and TTL, accurate click counting under concurrency, no data loss if the process is killed, a token-bucket rate limiter, health/readiness/metrics endpoints, structured logs, graceful shutdown, and a redirect path that stays under 75 ms at p99 while sustaining at least 200 req/s. Standard library only - no third-party packages - so the build is hermetic.",
  "stakeholders": [
    "platform team",
    "on-call engineer"
  ],
  "deliverables": [
    "linksvc/storage.py - durable, concurrency-safe link store on SQLite (WAL, synchronous=FULL)",
    "linksvc/ratelimit.py - thread-safe token-bucket limiter with bounded memory",
    "linksvc/server.py + linksvc/api.py - HTTP surface on ThreadingHTTPServer with graceful shutdown",
    "tests/ - unit tests written by each worker for its own module",
    "README.md and ACCEPTANCE.md - how to run it and what was proven"
  ],
  "constraints": [
    "Python 3.12 standard library only",
    "no data loss on SIGKILL (durability is proven by restarting on the same database)",
    "every counter mutation must be a single atomic statement",
    "the acceptance harness in harness/ is frozen: workers may read it, never modify it"
  ],
  "out_of_scope": [
    "authentication and multi-tenancy",
    "TLS termination (delegated to a reverse proxy)",
    "analytics beyond click counts",
    "custom alias reservations across restarts of a different process"
  ],
  "budgets": {
    "redirect_p99_ms": 75.0,
    "redirect_rps": 200.0
  },
  "acceptance": [
    {
      "id": "A-1",
      "kind": "functional",
      "statement": "POST /links creates a link, supports a custom alias and an idempotency key, and rejects invalid input with 400 / 409",
      "gates": [
        "G-acceptance"
      ]
    },
    {
      "id": "A-2",
      "kind": "functional",
      "statement": "GET /{code} answers 302 with the original URL, 404 for unknown codes and 410 for expired links",
      "gates": [
        "G-acceptance"
      ]
    },
    {
      "id": "A-3",
      "kind": "functional",
      "statement": "A token-bucket limiter returns 429 with Retry-After once the burst is exhausted",
      "gates": [
        "G-acceptance"
      ]
    },
    {
      "id": "A-4",
      "kind": "functional",
      "statement": "GET /links/{code}/stats reports an exact click count and /metrics exposes the service counters",
      "gates": [
        "G-acceptance"
      ]
    },
    {
      "id": "A-5",
      "kind": "safety",
      "statement": "Parallel redirects never lose a click: 8 threads x 40 redirects must land as exactly 320",
      "gates": [
        "G-concurrency"
      ]
    },
    {
      "id": "A-6",
      "kind": "safety",
      "statement": "A SIGKILL during operation loses no link and no click counter after restart on the same database",
      "gates": [
        "G-durability"
      ]
    },
    {
      "id": "A-7",
      "kind": "operability",
      "statement": "SIGTERM produces a clean shutdown with exit status 0",
      "gates": [
        "G-durability"
      ]
    },
    {
      "id": "A-8",
      "kind": "nfr",
      "statement": "Redirect latency stays at or below 75 ms at p99",
      "gates": [
        "G-budget-p99"
      ]
    },
    {
      "id": "A-9",
      "kind": "nfr",
      "statement": "The redirect path sustains at least 200 requests per second",
      "gates": [
        "G-budget-rps"
      ]
    },
    {
      "id": "A-10",
      "kind": "operability",
      "statement": "The service starts as `python3 -m linksvc` and prints a machine-readable ready line with its port",
      "gates": [
        "G-layout"
      ]
    }
  ],
  "gates": [
    {
      "id": "G-unit",
      "title": "worker's own unit tests",
      "kind": "cmd",
      "scope": "task",
      "cmd": "python3 -m unittest discover -s tests -v",
      "timeout": 600
    },
    {
      "id": "G-acceptance",
      "title": "HTTP contract conformance",
      "kind": "cmd",
      "scope": "system",
      "cmd": "python3 -m unittest harness.test_accept -v",
      "timeout": 900
    },
    {
      "id": "G-concurrency",
      "title": "no lost clicks under concurrency",
      "kind": "cmd",
      "scope": "system",
      "cmd": "python3 -m unittest harness.test_concurrency -v",
      "timeout": 900
    },
    {
      "id": "G-durability",
      "title": "crash durability and graceful shutdown",
      "kind": "cmd",
      "scope": "system",
      "cmd": "python3 -m unittest harness.test_durability -v",
      "timeout": 900
    },
    {
      "id": "G-perf",
      "title": "measure the redirect path",
      "kind": "cmd",
      "scope": "system",
      "cmd": "python3 -m harness.perf_probe",
      "timeout": 900
    },
    {
      "id": "G-budget-p99",
      "title": "p99 latency budget",
      "kind": "budget",
      "scope": "system",
      "metric": "redirect_p99_ms",
      "op": "<=",
      "threshold": 75.0
    },
    {
      "id": "G-budget-rps",
      "title": "throughput budget",
      "kind": "budget",
      "scope": "system",
      "metric": "redirect_rps",
      "op": ">=",
      "threshold": 200.0
    },
    {
      "id": "G-layout",
      "title": "deliverable layout",
      "kind": "files",
      "scope": "system",
      "paths": [
        "linksvc/__main__.py",
        "linksvc/storage.py",
        "linksvc/ratelimit.py",
        "linksvc/server.py",
        "README.md",
        "ACCEPTANCE.md"
      ]
    }
  ],
  "contracts": [
    {
      "id": "C-INTERFACES",
      "path": "contracts/C-INTERFACES.md",
      "summary": "Frozen module boundaries. Workers implement these exactly; they may not renegotiate them.",
      "exports": {
        "linksvc.storage.LinkStore": "LinkStore(db_path: str) with create(url, code=None, ttl_seconds=None, idempotency_key=None) -> Link, get(code) -> Link | None, record_click(code) -> None, stats(code) -> LinkStats | None, count_links() -> int, close() -> None",
        "linksvc.storage.Link": "dataclass(code: str, url: str, created_at: float, expires_at: float | None)",
        "linksvc.storage.LinkStats": "dataclass(code: str, url: str, clicks: int, created_at: float, expires_at: float | None)",
        "linksvc.storage.errors": "InvalidUrl, InvalidAlias, DuplicateAlias (all subclasses of StorageError)",
        "linksvc.ratelimit.TokenBucketLimiter": "TokenBucketLimiter(rate: float, burst: int) with allow(key: str) -> tuple[bool, float] returning (allowed, retry_after_seconds)",
        "linksvc.server.serve": "serve(host: str, port: int, db_path: str, rate: float, burst: int) -> int"
      },
      "content": "## Required HTTP surface\n\n- `POST /links` body `{url, ttl_seconds?, alias?, idempotency_key?}` -> 201 `{code, short_url, url, created_at, expires_at}`; 400 invalid url/alias; 409 alias taken; 429 when rate limited.\n- `GET /{code}` -> 302 `Location: <url>` and records one click; 404 unknown; 410 expired.\n- `GET /links/{code}/stats` -> 200 `{code, url, clicks, created_at, expires_at}`; 404 unknown.\n- `GET /healthz` -> 200 `{\"status\": \"ok\"}`; `GET /readyz` -> 200 `{\"status\": \"ready\"}` once the store is open; `GET /metrics` -> 200 with at least `links_created`, `redirects`, `rate_limited`, `not_found` as numbers.\n\n## Required process behaviour\n\n- `python3 -m linksvc --host H --port P --db PATH --rate R --burst B`; port 0 means an ephemeral port.\n- On listening, print exactly one line to stdout: `MINIFLEET_READY {\"port\": <actual port>}`.\n- `SIGTERM` and `SIGINT` must drain in-flight requests and leave exit status 0.\n- Structured JSON log lines on stderr, one object per request.\n\n## Storage rules\n\n- SQLite with WAL and `synchronous=FULL`; one connection per thread or a serialising lock is acceptable.\n- Click counting must be a single atomic `UPDATE ... SET clicks = clicks + 1`.\n- `idempotency_key` is unique per key: a repeat returns the existing link instead of creating a second one.\n- Expiry is enforced at read time (`expires_at` in the past reads as expired)."
    }
  ],
  "harness_dir": "harness-linksvc",
  "components": [
    {
      "id": "storage",
      "name": "Durable link store",
      "responsibility": "Implement linksvc/storage.py: the SQLite-backed store with exact click counting and crash durability.",
      "owns": [
        "linksvc/storage.py",
        "tests/test_storage.py"
      ],
      "contract_ids": [
        "C-INTERFACES"
      ],
      "acceptance_ids": [
        "A-1",
        "A-4",
        "A-5",
        "A-6"
      ],
      "gate_ids": [
        "G-unit"
      ],
      "done_when": [
        "LinkStore.create/get/record_click/stats/count_links behave as specified",
        "an idempotency key never creates a second link",
        "expired links are reported as expired rather than missing",
        "100 parallel record_click calls on one code add exactly 100 clicks"
      ],
      "tests": [
        "unit tests for create/alias/ttl/idempotency",
        "a threaded test proving no clicks are lost"
      ]
    },
    {
      "id": "ratelimit",
      "name": "Token bucket limiter",
      "responsibility": "Implement linksvc/ratelimit.py: a thread-safe token bucket with bounded memory that reports a retry-after hint.",
      "owns": [
        "linksvc/ratelimit.py",
        "tests/test_ratelimit.py"
      ],
      "contract_ids": [
        "C-INTERFACES"
      ],
      "acceptance_ids": [
        "A-3"
      ],
      "gate_ids": [
        "G-unit"
      ],
      "done_when": [
        "allow() returns (True, 0.0) while tokens remain and (False, retry_after) once exhausted",
        "tokens refill at the configured rate using a monotonic clock",
        "the number of tracked keys is bounded (evict least recently used)"
      ],
      "tests": [
        "deterministic tests with an injected clock",
        "a test proving buckets are evicted"
      ]
    },
    {
      "id": "http",
      "name": "HTTP surface",
      "responsibility": "Implement linksvc/__main__.py, linksvc/api.py, linksvc/server.py and linksvc/__init__.py: the HTTP API, routing, validation, ready line, logging and graceful shutdown.",
      "owns": [
        "linksvc/__init__.py",
        "linksvc/api.py",
        "linksvc/server.py",
        "linksvc/__main__.py",
        "tests/test_api.py"
      ],
      "depends_on": [
        "storage",
        "ratelimit"
      ],
      "contract_ids": [
        "C-INTERFACES"
      ],
      "acceptance_ids": [
        "A-1",
        "A-2",
        "A-4",
        "A-7",
        "A-10"
      ],
      "gate_ids": [
        "G-unit"
      ],
      "done_when": [
        "every route in the contract answers with the specified status code and body",
        "the process prints the MINIFLEET_READY line with the bound port",
        "SIGTERM drains and exits 0",
        "each request emits one JSON log line on stderr"
      ],
      "tests": [
        "an in-process test that starts the server on port 0 and drives the full API"
      ]
    }
  ]
}
```
