"""Threading HTTP server, structured logs and graceful shutdown."""

from __future__ import annotations

import json
import signal
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .api import Api, Counters, ServiceState
from .ratelimit import TokenBucketLimiter
from .storage import LinkStore

READY_PREFIX = "MINIFLEET_READY"


class LinkService(ThreadingHTTPServer):
    """A ThreadingHTTPServer that knows how to drain instead of dropping."""

    daemon_threads = False          # in-flight requests must finish
    block_on_close = True
    allow_reuse_address = True

    def __init__(self, address, handler, api: Api) -> None:
        self.api = api
        self._stopping = threading.Event()
        super().__init__(address, handler)
        self.port = int(self.server_address[1])
        self.api.state.public_base = f"http://{address[0]}:{self.port}"

    def stop(self) -> None:
        if not self._stopping.is_set():
            self._stopping.set()
            threading.Thread(target=self.shutdown, daemon=True).start()

    def serve_until_stopped(self) -> None:
        self.serve_forever(poll_interval=0.1)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "linksvc/1.0"
    sys_version = ""

    def log_message(self, fmt: str, *args: object) -> None:  # noqa: A003
        """Silence the default stderr logging; we emit structured JSON instead."""

    # -- dispatch --------------------------------------------------------
    def _handle(self, method: str) -> None:
        started = time.perf_counter()
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        body = self.rfile.read(length) if length > 0 else b""
        headers = {k.lower(): v for k, v in self.headers.items()}
        status, out_headers, payload = self.server.api.handle(
            method, self.path, body, headers, self.client_address[0]
        )
        try:
            self.send_response(status)
            for key, value in out_headers.items():
                self.send_header(key, value)
            if method == "HEAD" or status == 302:
                self.send_header("content-length", out_headers.get("content-length", "0"))
            self.end_headers()
            if method != "HEAD" and status != 302 and payload:
                self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError):
            status = status if isinstance(status, int) else 500
        self._log(method, status, started)

    def _log(self, method: str, status: int, started: float) -> None:
        record = {
            "ts": round(time.time(), 6),
            "level": "info",
            "method": method,
            "path": self.path,
            "status": status,
            "duration_ms": round((time.perf_counter() - started) * 1000.0, 3),
            "remote": self.client_address[0],
        }
        print(json.dumps(record, sort_keys=True), file=sys.stderr, flush=True)

    def do_GET(self) -> None:      # noqa: N802
        self._handle("GET")

    def do_HEAD(self) -> None:     # noqa: N802
        self._handle("HEAD")

    def do_POST(self) -> None:     # noqa: N802
        self._handle("POST")

    def do_PUT(self) -> None:      # noqa: N802
        self._handle("PUT")

    def do_PATCH(self) -> None:    # noqa: N802
        self._handle("PATCH")

    def do_DELETE(self) -> None:   # noqa: N802
        self._handle("DELETE")


def build_server(
    host: str, port: int, db_path: str, rate: float, burst: int, max_keys: int = 4096
) -> LinkService:
    """Bind the socket and return a server that is not serving yet."""

    state = ServiceState(
        store=LinkStore(db_path),
        limiter=TokenBucketLimiter(rate=rate, burst=burst, max_keys=max_keys),
        counters=Counters(),
    )
    return LinkService((host, port), Handler, Api(state))


def serve(host: str, port: int, db_path: str, rate: float, burst: int) -> int:
    """Run until SIGTERM/SIGINT; returns the process exit status."""

    server = build_server(host, port, db_path, rate, burst)
    actual_port = server.port
    print(f"{READY_PREFIX} {json.dumps({'port': actual_port})}", flush=True)
    print(
        json.dumps(
            {
                "ts": round(time.time(), 6),
                "level": "info",
                "event": "listening",
                "host": host,
                "port": actual_port,
                "db": db_path,
                "rate": rate,
                "burst": burst,
            },
            sort_keys=True,
        ),
        file=sys.stderr,
        flush=True,
    )

    def shutdown(signum: int, _frame: object) -> None:
        print(
            json.dumps(
                {"ts": round(time.time(), 6), "level": "info", "event": "signal", "signal": signum},
                sort_keys=True,
            ),
            file=sys.stderr,
            flush=True,
        )
        server.stop()

    installed = False
    if threading.current_thread() is threading.main_thread():
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, shutdown)
        installed = True

    try:
        server.serve_until_stopped()      # drains in-flight requests on stop
    finally:
        server.server_close()             # joins worker threads (block_on_close)
        server.api.state.store.close()
        if installed:
            for sig in (signal.SIGTERM, signal.SIGINT):
                signal.signal(sig, signal.SIG_DFL)
    return 0
