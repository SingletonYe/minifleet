"""``python3 -m linksvc`` entry point."""

from __future__ import annotations

import argparse
import sys

from .server import serve


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="linksvc", description="a durable link shortener")
    parser.add_argument("--host", default="127.0.0.1", help="interface to bind")
    parser.add_argument("--port", type=int, default=8080, help="port to bind (0 = ephemeral)")
    parser.add_argument("--db", default="links.db", help="SQLite database path")
    parser.add_argument("--rate", type=float, default=50.0, help="write tokens per second")
    parser.add_argument("--burst", type=int, default=100, help="maximum write burst")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not (0 <= args.port <= 65535):
        print("port must be between 0 and 65535", file=sys.stderr)
        return 2
    return serve(args.host, args.port, args.db, args.rate, args.burst)


if __name__ == "__main__":
    raise SystemExit(main())
