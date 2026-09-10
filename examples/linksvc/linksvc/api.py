"""The HTTP surface: routing, validation, counters.

Everything here is deliberately transport-agnostic: ``Api.handle`` takes a
method, a path, raw body bytes and headers, and returns a status, headers and
body. ``server.py`` does the socket work, which keeps this layer directly
testable and keeps the redirect path short.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from .analytics import DEFAULT_LIMIT, InvalidLimit, top_links
from .ratelimit import TokenBucketLimiter
from .storage import (
    DuplicateAlias,
    InvalidAlias,
    InvalidUrl,
    LinkStore,
    StorageError,
)

MAX_BODY_BYTES = 64 * 1024
ALLOWED_METHODS = ("GET", "POST", "HEAD")


@dataclass
class Counters:
    """Service counters, guarded by the lock the limiter already needs."""

    links_created: int = 0
    redirects: int = 0
    rate_limited: int = 0
    not_found: int = 0
    expired: int = 0
    bad_request: int = 0
    requests: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "links_created": self.links_created,
            "redirects": self.redirects,
            "rate_limited": self.rate_limited,
            "not_found": self.not_found,
            "expired": self.expired,
            "bad_request": self.bad_request,
            "requests": self.requests,
        }


@dataclass
class ServiceState:
    store: LinkStore
    limiter: TokenBucketLimiter
    counters: Counters
    public_base: str = ""
    started_at: float = field(default_factory=time.time)


class Api:
    def __init__(self, state: ServiceState) -> None:
        self.state = state

    # -- entry point -----------------------------------------------------
    def handle(
        self,
        method: str,
        path: str,
        body: bytes = b"",
        headers: dict[str, str] | None = None,
        remote: str = "",
    ) -> tuple[int, dict[str, str], bytes]:
        headers = {k.lower(): v for k, v in (headers or {}).items()}
        self.state.counters.requests += 1
        if method not in ALLOWED_METHODS:
            return self._json(405, {"error": "method not allowed"}, {"allow": ", ".join(ALLOWED_METHODS)})
        try:
            return self._route(method, path, body, headers, remote)
        except json.JSONDecodeError:
            self.state.counters.bad_request += 1
            return self._json(400, {"error": "body must be valid JSON"})
        except (InvalidUrl, InvalidAlias, StorageError) as exc:
            self.state.counters.bad_request += 1
            return self._json(400, {"error": str(exc)})

    # -- routing ---------------------------------------------------------
    def _route(
        self,
        method: str,
        path: str,
        body: bytes,
        headers: dict[str, str],
        remote: str,
    ) -> tuple[int, dict[str, str], bytes]:
        parsed = urlparse(path)
        route = parsed.path
        segments = [unquote(part) for part in route.split("/") if part != ""]

        if segments == ["links", "top"] and method in ("GET", "HEAD"):
            return self._top(parse_qs(parsed.query))

        if len(segments) == 1:
            if segments[0] == "healthz" and method in ("GET", "HEAD"):
                return self._json(200, {"status": "ok", "uptime_s": round(self._uptime(), 3)})
            if segments[0] == "readyz" and method in ("GET", "HEAD"):
                return self._json(200, {"status": "ready", "links": self.state.store.count_links()})
            if segments[0] == "metrics" and method in ("GET", "HEAD"):
                return self._json(200, self.state.counters.as_dict())
            if segments[0] == "links" and method == "POST":
                return self._create(body, remote, headers)
            if method in ("GET", "HEAD"):
                return self._redirect(segments[0])
            return self._json(405, {"error": "method not allowed"}, {"allow": "GET"})

        if len(segments) == 3 and segments[0] == "links" and segments[2] == "stats" and method in ("GET", "HEAD"):
            return self._stats(segments[1])
        return self._not_found()

    # -- handlers --------------------------------------------------------
    def _create(
        self, body: bytes, remote: str, headers: dict[str, str]
    ) -> tuple[int, dict[str, str], bytes]:
        allowed, retry_after = self.state.limiter.allow(remote or "unknown")
        if not allowed:
            self.state.counters.rate_limited += 1
            return self._json(
                429,
                {"error": "rate limit exceeded", "retry_after": round(retry_after, 3)},
                {"retry-after": str(max(1, int(retry_after + 0.999)))},
            )

        if len(body) > MAX_BODY_BYTES:
            self.state.counters.bad_request += 1
            return self._json(413, {"error": "body too large"})

        payload = self._decode(body)
        unknown = set(payload) - {"url", "ttl_seconds", "alias", "idempotency_key", "code"}
        if unknown:
            self.state.counters.bad_request += 1
            return self._json(400, {"error": f"unknown fields: {sorted(unknown)}"})

        alias = payload.get("alias", payload.get("code"))
        ttl = payload.get("ttl_seconds")
        if ttl is not None and not isinstance(ttl, (int, float)):
            self.state.counters.bad_request += 1
            return self._json(400, {"error": "ttl_seconds must be a number"})
        if ttl is not None and float(ttl) <= 0:
            self.state.counters.bad_request += 1
            return self._json(400, {"error": "ttl_seconds must be positive"})
        key = payload.get("idempotency_key")
        if key is not None and not isinstance(key, str):
            self.state.counters.bad_request += 1
            return self._json(400, {"error": "idempotency_key must be a string"})

        try:
            link = self.state.store.create(
                payload.get("url"),
                code=alias,
                ttl_seconds=ttl,
                idempotency_key=key,
            )
        except DuplicateAlias as exc:
            return self._json(409, {"error": str(exc)}) 
        self.state.counters.links_created += 1
        return self._json(201, self._link_payload(link.code, link.url, link.created_at, link.expires_at, headers["host"] if "host" in headers else None))

    def _redirect(self, code: str) -> tuple[int, dict[str, str], bytes]:
        link = self.state.store.get(code)
        if link is None:
            self.state.counters.not_found += 1
            return self._json(404, {"error": "unknown code"})
        if link.expired():
            self.state.counters.expired += 1
            return self._json(410, {"error": "link expired", "code": code})
        self.state.store.record_click(code)
        self.state.counters.redirects += 1
        return 302, {"location": link.url, "cache-control": "no-store"}, b""

    def _stats(self, code: str) -> tuple[int, dict[str, str], bytes]:
        stats = self.state.store.stats(code)
        if stats is None:
            self.state.counters.not_found += 1
            return self._json(404, {"error": "unknown code"})
        return self._json(
            200,
            {
                "code": stats.code,
                "url": stats.url,
                "clicks": stats.clicks,
                "created_at": stats.created_at,
                "expires_at": stats.expires_at,
            },
        )

    def _top(self, query: dict[str, list[str]]) -> tuple[int, dict[str, str], bytes]:
        # The route is reserved: /links/top is the analytics page, never a code.
        # Redirects use the single-segment form (/{code}), so the two cannot
        # collide.
        raw = query.get("limit", [str(DEFAULT_LIMIT)])[0]
        try:
            limit = _parse_limit(raw)
            page = top_links(self.state.store, limit)
        except InvalidLimit as exc:
            self.state.counters.bad_request += 1
            return self._json(400, {"error": str(exc)})
        return self._json(
            200,
            {
                "limit": limit,
                "top": [
                    {
                        "code": row.code,
                        "url": row.url,
                        "clicks": row.clicks,
                        "created_at": row.created_at,
                    }
                    for row in page
                ],
            },
        )

    # -- helpers ---------------------------------------------------------
    def _uptime(self) -> float:
        return time.time() - self.state.started_at

    def _not_found(self) -> tuple[int, dict[str, str], bytes]:
        self.state.counters.not_found += 1
        return self._json(404, {"error": "not found"})

    def _decode(self, body: bytes) -> dict[str, Any]:
        if not body:
            raise StorageError("body must be a JSON object")
        payload = json.loads(body.decode("utf-8"))
        if not isinstance(payload, dict):
            raise StorageError("body must be a JSON object")
        return payload

    def _link_payload(
        self,
        code: str,
        url: str,
        created_at: float,
        expires_at: float | None,
        host: str | None,
    ) -> dict[str, Any]:
        base = self.state.public_base or (f"http://{host}" if host else "")
        return {
            "code": code,
            "short_url": f"{base}/{code}" if base else f"/{code}",
            "url": url,
            "created_at": created_at,
            "expires_at": expires_at,
        }

    @staticmethod
    def _json(
        status: int, payload: dict[str, Any], extra: dict[str, str] | None = None
    ) -> tuple[int, dict[str, str], bytes]:
        body = json.dumps(payload).encode()
        headers = {"content-type": "application/json", "content-length": str(len(body))}
        headers.update(extra or {})
        return status, headers, body


def _parse_limit(raw: str) -> int:
    """Accept only what an operator would type: a base-10 integer."""

    text = raw.strip()
    if not text or not text.isdigit():
        raise InvalidLimit("limit must be an integer between 1 and 100")
    return int(text)
