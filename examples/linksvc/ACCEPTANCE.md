# Acceptance criteria (frozen)

| id | kind | statement | gates |
| --- | --- | --- | --- |
| A-1 | functional | POST /links creates a link, supports a custom alias and an idempotency key, and rejects invalid input with 400 / 409 | G-acceptance |
| A-2 | functional | GET /{code} answers 302 with the original URL, 404 for unknown codes and 410 for expired links | G-acceptance |
| A-3 | functional | A token-bucket limiter returns 429 with Retry-After once the burst is exhausted | G-acceptance |
| A-4 | functional | GET /links/{code}/stats reports an exact click count and /metrics exposes the service counters | G-acceptance |
| A-5 | safety | Parallel redirects never lose a click: 8 threads x 40 redirects must land as exactly 320 | G-concurrency |
| A-6 | safety | A SIGKILL during operation loses no link and no click counter after restart on the same database | G-durability |
| A-7 | operability | SIGTERM produces a clean shutdown with exit status 0 | G-durability |
| A-8 | nfr | Redirect latency stays at or below 75 ms at p99 | G-budget-p99 |
| A-9 | nfr | The redirect path sustains at least 200 requests per second | G-budget-rps |
| A-10 | operability | The service starts as `python3 -m linksvc` and prints a machine-readable ready line with its port | G-layout |

## Non-functional budgets

| metric | budget |
| --- | --- |
| redirect_p99_ms | 75.0 |
| redirect_rps | 200.0 |

## Gates

| gate | kind | scope | command |
| --- | --- | --- | --- |
| G-unit | cmd | task | `python3 -m unittest discover -s tests -v` |
| G-acceptance | cmd | system | `python3 -m unittest harness.test_accept -v` |
| G-concurrency | cmd | system | `python3 -m unittest harness.test_concurrency -v` |
| G-durability | cmd | system | `python3 -m unittest harness.test_durability -v` |
| G-perf | cmd | system | `python3 -m harness.perf_probe` |
| G-budget-p99 | budget | system | `` |
| G-budget-rps | budget | system | `` |
| G-layout | files | system | `linksvc/__main__.py,linksvc/storage.py,linksvc/ratelimit.py,linksvc/server.py,README.md,ACCEPTANCE.md` |
