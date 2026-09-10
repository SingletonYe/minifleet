"""linksvc - a dependency-free, production-grade link shortener.

Run it with::

    python3 -m linksvc --host 127.0.0.1 --port 8080 --db links.db

The process prints ``MINIFLEET_READY {"port": <port>}`` on stdout once it is
listening, serves structured JSON logs on stderr, and shuts down cleanly on
SIGTERM or SIGINT.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "1.0.0"
