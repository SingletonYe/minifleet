"""Test client for linksvc: starts the real service and talks raw HTTP to it."""

from __future__ import annotations

import http.client
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

READY_PREFIX = "MINIFLEET_READY"


class ServiceError(RuntimeError):
    pass


class Service:
    """A running `python3 -m linksvc` process on an ephemeral port."""

    def __init__(self, db: str | None = None, rate: float = 1000.0, burst: int = 1000,
                 args: list[str] | None = None) -> None:
        self.db = db or str(Path(tempfile.mkdtemp(prefix="linksvc-")) / "links.db")
        self.rate = rate
        self.burst = burst
        self.args = list(args or [])
        self.proc: subprocess.Popen | None = None
        self.port = 0
        self.stderr_lines: list[str] = []

    # -- lifecycle -------------------------------------------------------
    def start(self, timeout: float = 25.0) -> "Service":
        cmd = [
            sys.executable, "-m", "linksvc",
            "--host", "127.0.0.1",
            "--port", "0",
            "--db", self.db,
            "--rate", str(self.rate),
            "--burst", str(self.burst),
            *self.args,
        ]
        self.proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            cwd=os.getcwd(), bufsize=1,
        )
        deadline = time.time() + timeout
        assert self.proc.stdout is not None
        while time.time() < deadline:
            line = self.proc.stdout.readline()
            if not line:
                if self.proc.poll() is not None:
                    raise ServiceError(
                        f"linksvc exited with {self.proc.returncode} before becoming ready: "
                        f"{(self.proc.stderr.read() if self.proc.stderr else '')[-800:]}"
                    )
                continue
            if line.startswith(READY_PREFIX):
                payload = json.loads(line[len(READY_PREFIX):].strip() or "{}")
                self.port = int(payload["port"])
                return self
        raise ServiceError("linksvc did not print a ready line in time")

    def stop(self, sig: int = signal.SIGTERM, timeout: float = 10.0) -> int:
        assert self.proc is not None
        self.proc.send_signal(sig)
        try:
            return self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            raise ServiceError(f"linksvc ignored {signal.Signals(sig).name} for {timeout}s")

    def kill(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait(timeout=10)

    def __enter__(self) -> "Service":
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.kill()

    # -- requests --------------------------------------------------------
    def request(self, method: str, path: str, body: dict | None = None,
                headers: dict[str, str] | None = None) -> tuple[int, dict[str, str], bytes]:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        payload = None if body is None else json.dumps(body).encode()
        head = {"Content-Type": "application/json", **(headers or {})}
        try:
            conn.request(method, path, body=payload, headers=head)
            response = conn.getresponse()
            data = response.read()
            return response.status, {k.lower(): v for k, v in response.getheaders()}, data
        finally:
            conn.close()

    def json(self, method: str, path: str, body: dict | None = None,
             headers: dict[str, str] | None = None) -> tuple[int, dict, dict[str, str]]:
        status, head, data = self.request(method, path, body, headers)
        try:
            payload = json.loads(data or b"{}")
        except json.JSONDecodeError:
            payload = {"_raw": data.decode(errors="replace")}
        return status, payload, head

    # -- convenience -----------------------------------------------------
    def create(self, url: str = "https://example.com/a", **extra) -> dict:
        status, payload, _ = self.json("POST", "/links", {"url": url, **extra})
        if status != 201:
            raise ServiceError(f"create failed: {status} {payload}")
        return payload

    def redirect(self, code: str, headers: dict[str, str] | None = None) -> tuple[int, dict[str, str]]:
        status, head, _ = self.request("GET", f"/{code}", headers=headers)
        return status, head
