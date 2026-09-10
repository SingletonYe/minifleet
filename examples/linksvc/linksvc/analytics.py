"""Click analytics: the most-clicked links.

This module reads the link store through the same database the HTTP workers
write to. It opens its own short-lived connection instead of reaching into the
store's internals, so the aggregation runs in SQLite and stays correct while
other threads are redirecting traffic (WAL gives readers a consistent snapshot).
"""

from __future__ import annotations

import sqlite3

from .storage import LinkStats, LinkStore

__all__ = ["InvalidLimit", "DEFAULT_LIMIT", "MAX_LIMIT", "top_links"]

DEFAULT_LIMIT = 10
MAX_LIMIT = 100


class InvalidLimit(ValueError):
    """The requested page size is not an integer inside 1..100."""


def validate_limit(limit: object) -> int:
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise InvalidLimit("limit must be an integer")
    if limit < 1 or limit > MAX_LIMIT:
        raise InvalidLimit(f"limit must be between 1 and {MAX_LIMIT}")
    return limit


def top_links(store: LinkStore, limit: int = DEFAULT_LIMIT) -> list[LinkStats]:
    """Return the most-clicked links, ties broken by the older link first.

    Only links that have been clicked at least once are candidates: a "top
    links" page that leads with links nobody has clicked is not a top links
    page. The ordering is computed by SQLite, not by sorting in Python.
    """

    page_size = validate_limit(limit)
    connection = sqlite3.connect(store.db_path, timeout=30.0)
    connection.row_factory = sqlite3.Row
    try:
        cursor = connection.execute(
            """
            SELECT code, url, clicks, created_at, expires_at
            FROM links
            WHERE clicks > 0
            ORDER BY clicks DESC, created_at ASC, code ASC
            LIMIT ?
            """,
            (page_size,),
        )
        rows = cursor.fetchall()
    finally:
        connection.close()

    return [
        LinkStats(
            code=row["code"],
            url=row["url"],
            clicks=int(row["clicks"]),
            created_at=row["created_at"],
            expires_at=row["expires_at"],
        )
        for row in rows
    ]
