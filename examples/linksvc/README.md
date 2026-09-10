# A production-grade link shortener

A dependency-free link shortener that is safe to run in production: durable under crash, correct under concurrency, rate limited, observable, and fast.

## Deliverables

- linksvc/storage.py - durable, concurrency-safe link store on SQLite (WAL, synchronous=FULL)
- linksvc/ratelimit.py - thread-safe token-bucket limiter with bounded memory
- linksvc/server.py + linksvc/api.py - HTTP surface on ThreadingHTTPServer with graceful shutdown
- tests/ - unit tests written by each worker for its own module
- README.md and ACCEPTANCE.md - how to run it and what was proven

## How this repository is verified

This repository is produced and admitted to production by MiniFleet. The acceptance harness in `harness/` was frozen before any implementation work started, and every acceptance criterion in `ACCEPTANCE.md` is tied to a gate.

## Components

- **storage** — Implement linksvc/storage.py: the SQLite-backed store with exact click counting and crash durability.
- **ratelimit** — Implement linksvc/ratelimit.py: a thread-safe token bucket with bounded memory that reports a retry-after hint.
- **http** — Implement linksvc/__main__.py, linksvc/api.py, linksvc/server.py and linksvc/__init__.py: the HTTP API, routing, validation, ready line, logging and graceful shutdown.
