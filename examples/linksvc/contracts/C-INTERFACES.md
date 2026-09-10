# C-INTERFACES: contracts/C-INTERFACES.md

Frozen module boundaries. Workers implement these exactly; they may not renegotiate them.

## Frozen exports

- `linksvc.storage.LinkStore`: LinkStore(db_path: str) with create(url, code=None, ttl_seconds=None, idempotency_key=None) -> Link, get(code) -> Link | None, record_click(code) -> None, stats(code) -> LinkStats | None, count_links() -> int, close() -> None
- `linksvc.storage.Link`: dataclass(code: str, url: str, created_at: float, expires_at: float | None)
- `linksvc.storage.LinkStats`: dataclass(code: str, url: str, clicks: int, created_at: float, expires_at: float | None)
- `linksvc.storage.errors`: InvalidUrl, InvalidAlias, DuplicateAlias (all subclasses of StorageError)
- `linksvc.ratelimit.TokenBucketLimiter`: TokenBucketLimiter(rate: float, burst: int) with allow(key: str) -> tuple[bool, float] returning (allowed, retry_after_seconds)
- `linksvc.server.serve`: serve(host: str, port: int, db_path: str, rate: float, burst: int) -> int

## Content

## Required HTTP surface

- `POST /links` body `{url, ttl_seconds?, alias?, idempotency_key?}` -> 201 `{code, short_url, url, created_at, expires_at}`; 400 invalid url/alias; 409 alias taken; 429 when rate limited.
- `GET /{code}` -> 302 `Location: <url>` and records one click; 404 unknown; 410 expired.
- `GET /links/{code}/stats` -> 200 `{code, url, clicks, created_at, expires_at}`; 404 unknown.
- `GET /healthz` -> 200 `{"status": "ok"}`; `GET /readyz` -> 200 `{"status": "ready"}` once the store is open; `GET /metrics` -> 200 with at least `links_created`, `redirects`, `rate_limited`, `not_found` as numbers.

## Required process behaviour

- `python3 -m linksvc --host H --port P --db PATH --rate R --burst B`; port 0 means an ephemeral port.
- On listening, print exactly one line to stdout: `MINIFLEET_READY {"port": <actual port>}`.
- `SIGTERM` and `SIGINT` must drain in-flight requests and leave exit status 0.
- Structured JSON log lines on stderr, one object per request.

## Storage rules

- SQLite with WAL and `synchronous=FULL`; one connection per thread or a serialising lock is acceptable.
- Click counting must be a single atomic `UPDATE ... SET clicks = clicks + 1`.
- `idempotency_key` is unique per key: a repeat returns the existing link instead of creating a second one.
- Expiry is enforced at read time (`expires_at` in the past reads as expired).
