"""Durable, concurrency-safe link store on SQLite.

Design notes that the acceptance criteria depend on:

* The database runs in WAL mode with ``synchronous=FULL``, so a committed
  transaction survives ``SIGKILL`` of the whole process.
* Every counter mutation is one atomic ``UPDATE ... SET clicks = clicks + 1``
  statement. Read-modify-write in Python would lose clicks under parallel
  redirects.
* Expiry is enforced at read time. ``get`` returns the link even when it has
  expired, together with ``expires_at``, so the HTTP layer can answer 410
  (expired) instead of 404 (unknown). ``Link.expired`` does the comparison.
"""

from __future__ import annotations

import re
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass
from urllib.parse import urlparse

__all__ = [
    "Link",
    "LinkStats",
    "LinkStore",
    "StorageError",
    "InvalidUrl",
    "InvalidAlias",
    "DuplicateAlias",
]

ALPHABET = "23456789abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ"
CODE_LENGTH = 8
ALIAS_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
MAX_URL_LENGTH = 2048


class StorageError(Exception):
    """Base class for every rejection the store makes."""


class InvalidUrl(StorageError):
    """The URL is not an absolute http(s) URL we are willing to shorten."""


class InvalidAlias(StorageError):
    """The requested alias is not a legal short code."""


class DuplicateAlias(StorageError):
    """The requested alias is already taken (and not by this idempotency key)."""


@dataclass(frozen=True)
class Link:
    code: str
    url: str
    created_at: float
    expires_at: float | None

    def expired(self, now: float | None = None) -> bool:
        if self.expires_at is None:
            return False
        return self.expires_at <= (time.time() if now is None else now)


@dataclass(frozen=True)
class LinkStats:
    code: str
    url: str
    clicks: int
    created_at: float
    expires_at: float | None


def normalize_url(url: object) -> str:
    """Validate and canonicalise an inbound URL, or raise InvalidUrl."""

    if not isinstance(url, str):
        raise InvalidUrl("url must be a string")
    candidate = url.strip()
    if not candidate or len(candidate) > MAX_URL_LENGTH:
        raise InvalidUrl("url must be a non-empty string under 2048 characters")
    parsed = urlparse(candidate)
    if parsed.scheme not in {"http", "https"}:
        raise InvalidUrl("only http and https urls are supported")
    if not parsed.netloc:
        raise InvalidUrl("url must include a host")
    return candidate


def normalize_alias(alias: object) -> str:
    if not isinstance(alias, str) or not ALIAS_RE.match(alias):
        raise InvalidAlias("alias must match ^[A-Za-z0-9_-]{1,64}$")
    return alias


class LinkStore:
    """A single SQLite database, guarded by one lock, safe for many threads.

    One connection plus a serialising lock is the simplest arrangement that is
    still exactly correct: SQLite serialises writers anyway, and the lock keeps
    interleaved Python statements from interleaving transactions.
    """

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False, timeout=30.0)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=FULL")
            self._conn.execute("PRAGMA busy_timeout=30000")
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS links (
                    code            TEXT PRIMARY KEY,
                    url             TEXT NOT NULL,
                    created_at      REAL NOT NULL,
                    expires_at      REAL,
                    clicks          INTEGER NOT NULL DEFAULT 0,
                    idempotency_key TEXT UNIQUE
                );
                CREATE INDEX IF NOT EXISTS links_created_at ON links (created_at);
                """
            )
            self._conn.commit()

    # -- internals -------------------------------------------------------
    def _row(self, code: str) -> sqlite3.Row | None:
        cursor = self._conn.execute(
            "SELECT code, url, created_at, expires_at, clicks FROM links WHERE code = ?",
            (code,),
        )
        return cursor.fetchone()

    def _by_idempotency_key(self, key: str) -> sqlite3.Row | None:
        cursor = self._conn.execute(
            "SELECT code, url, created_at, expires_at, clicks FROM links WHERE idempotency_key = ?",
            (key,),
        )
        return cursor.fetchone()

    @staticmethod
    def _link(row: sqlite3.Row) -> Link:
        return Link(
            code=row["code"],
            url=row["url"],
            created_at=row["created_at"],
            expires_at=row["expires_at"],
        )

    def _new_code(self) -> str:
        for _ in range(32):
            code = "".join(secrets.choice(ALPHABET) for _ in range(CODE_LENGTH))
            if self._row(code) is None:
                return code
        raise StorageError("could not allocate a unique code")

    # -- writes ----------------------------------------------------------
    def create(
        self,
        url: str,
        code: str | None = None,
        ttl_seconds: float | None = None,
        idempotency_key: str | None = None,
    ) -> Link:
        clean_url = normalize_url(url)
        if code is not None:
            clean_alias = normalize_alias(code)
        else:
            clean_alias = None
        if ttl_seconds is not None:
            ttl = float(ttl_seconds)
            if ttl <= 0 or ttl != ttl or ttl in (float("inf"),):
                raise InvalidUrl("ttl_seconds must be a positive number")
        else:
            ttl = None
        if idempotency_key is not None:
            if not isinstance(idempotency_key, str) or not idempotency_key:
                raise StorageError("idempotency_key must be a non-empty string")

        with self._lock:
            if idempotency_key is not None:
                existing = self._by_idempotency_key(idempotency_key)
                if existing is not None:
                    return self._link(existing)

            now = time.time()
            expires_at = None if ttl is None else now + ttl
            target_code = clean_alias or self._new_code()
            try:
                self._conn.execute(
                    "INSERT INTO links (code, url, created_at, expires_at, clicks, idempotency_key)"
                    " VALUES (?, ?, ?, ?, 0, ?)",
                    (target_code, clean_url, now, expires_at, idempotency_key),
                )
                self._conn.commit()
            except sqlite3.IntegrityError as exc:
                self._conn.rollback()
                if idempotency_key is not None:
                    existing = self._by_idempotency_key(idempotency_key)
                    if existing is not None:
                        return self._link(existing)
                raise DuplicateAlias(f"alias {target_code!r} is already taken") from exc
        return Link(code=target_code, url=clean_url, created_at=now, expires_at=expires_at)

    def record_click(self, code: str) -> None:
        """Count one redirect with a single atomic statement."""

        with self._lock:
            self._conn.execute(
                "UPDATE links SET clicks = clicks + 1 WHERE code = ?", (code,)
            )
            self._conn.commit()

    # -- reads -----------------------------------------------------------
    def get(self, code: str) -> Link | None:
        """Return the link, expired or not, or None when the code is unknown."""

        if not isinstance(code, str) or not code:
            return None
        with self._lock:
            row = self._row(code)
        return None if row is None else self._link(row)

    def stats(self, code: str) -> LinkStats | None:
        if not isinstance(code, str) or not code:
            return None
        with self._lock:
            row = self._row(code)
        if row is None:
            return None
        return LinkStats(
            code=row["code"],
            url=row["url"],
            clicks=int(row["clicks"]),
            created_at=row["created_at"],
            expires_at=row["expires_at"],
        )

    def count_links(self) -> int:
        with self._lock:
            cursor = self._conn.execute("SELECT COUNT(*) AS n FROM links")
            return int(cursor.fetchone()["n"])

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.commit()
            finally:
                self._conn.close()

    def __enter__(self) -> "LinkStore":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
