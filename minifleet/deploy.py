"""Deploy and observe: supervised local execution plus smoke and soak probes.

"Production" for MiniFleet means the artefact actually runs, announces where it
listens, answers a probe, and survives a short soak window - and that the
process is always torn down, including when the ready line never arrives.
"""

from __future__ import annotations

import json
import os
import queue
import signal
import statistics
import subprocess
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from typing import Any, Sequence


class Supervisor:
    """Run a process, wait for its ready line, and never leak the child."""

    def __init__(
        self,
        argv: Sequence[str],
        cwd: str,
        ready_prefix: str = "MINIFLEET_READY",
        timeout: float = 30.0,
    ) -> None:
        self.argv = [str(part) for part in argv]
        self.cwd = str(cwd)
        self.ready_prefix = ready_prefix
        self.timeout = float(timeout)
        self._proc: subprocess.Popen[str] | None = None
        self._lines: queue.Queue[str | None] = queue.Queue()
        self._tail: deque[str] = deque(maxlen=200)
        self._reader: threading.Thread | None = None
        self._started_at = 0.0
        self.ready: dict[str, Any] | None = None

    # -- lifecycle -------------------------------------------------------
    def start(self) -> "Supervisor":
        env = dict(os.environ)
        env["PYTHONUNBUFFERED"] = "1"
        self._started_at = time.monotonic()
        self._proc = subprocess.Popen(
            self.argv,
            cwd=self.cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=env,
        )
        self._reader = threading.Thread(target=self._pump, daemon=True)
        self._reader.start()
        return self

    def _pump(self) -> None:
        stream = self._proc.stdout if self._proc else None
        if stream is not None:
            for raw in stream:
                line = raw.rstrip("\n")
                self._tail.append(line)
                self._lines.put(line)
        self._lines.put(None)

    def wait_ready(self) -> dict[str, Any]:
        """Read stdout until the ready line arrives or the timeout expires."""

        deadline = time.monotonic() + self.timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return self._ready_result(False, None)
            try:
                line = self._lines.get(timeout=remaining)
            except queue.Empty:
                return self._ready_result(False, None)
            if line is None:
                # The child exited without ever announcing a port.
                return self._ready_result(False, None)
            if line.startswith(self.ready_prefix):
                return self._ready_result(True, self._parse_port(line))
        # unreachable

    def _parse_port(self, line: str) -> int | None:
        payload = line[len(self.ready_prefix) :].strip()
        if not payload:
            return None
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            return None
        if isinstance(data, dict):
            port = data.get("port")
            try:
                return int(port) if port is not None else None
            except (TypeError, ValueError):
                return None
        return None

    def _ready_result(self, ready: bool, port: int | None) -> dict[str, Any]:
        result = {
            "ready": ready,
            "port": port,
            "seconds": round(time.monotonic() - self._started_at, 3),
            "stdout_tail": "\n".join(self._tail),
        }
        self.ready = result
        return result

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def stop(self, timeout: float = 10.0) -> int:
        """Terminate the child, escalate to SIGKILL, and return its exit code."""

        proc = self._proc
        if proc is None:
            return 0
        if proc.poll() is None:
            try:
                proc.send_signal(signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                try:
                    proc.wait(timeout=5.0)
                except subprocess.TimeoutExpired:
                    return -9
        if proc.stdout is not None:
            try:
                proc.stdout.close()
            except Exception:  # noqa: BLE001 - teardown must not raise
                pass
        return int(proc.returncode if proc.returncode is not None else -1)

    def __enter__(self) -> "Supervisor":
        return self.start()

    def __exit__(self, *_exc: object) -> None:
        self.stop()


def _get(url: str, timeout: float) -> tuple[int, bool, float]:
    started = time.perf_counter()
    status = 0
    ok = False
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310
            response.read()
            status = int(getattr(response, "status", 0) or 0)
            ok = 200 <= status < 300
    except urllib.error.HTTPError as exc:
        status = int(exc.code)
        ok = False
        try:
            exc.read()
        except Exception:  # noqa: BLE001
            pass
    except Exception:  # noqa: BLE001 - a dead endpoint is data, not a crash
        status = 0
        ok = False
    return status, ok, time.perf_counter() - started


def smoke(url: str, timeout: float = 5.0) -> dict[str, Any]:
    """One GET: did the artefact answer, and how fast?"""

    status, ok, seconds = _get(url, timeout)
    return {"status": status, "ok": ok, "seconds": round(seconds, 4)}


def soak(
    url: str, seconds: float, interval: float = 0.25, timeout: float = 5.0
) -> dict[str, Any]:
    """Repeat one probe for a bounded window and report failures and latency."""

    started = time.perf_counter()
    samples = 0
    failures = 0
    timings: list[float] = []
    while True:
        _, ok, elapsed = _get(url, timeout)
        samples += 1
        timings.append(elapsed * 1000.0)
        if not ok:
            failures += 1
        run_for = time.perf_counter() - started
        if run_for >= seconds:
            break
        time.sleep(min(interval, max(0.0, seconds - run_for)))
    ordered = sorted(timings)
    p50 = statistics.median(ordered) if ordered else 0.0
    return {
        "samples": samples,
        "failures": failures,
        "ok": samples > 0 and failures == 0,
        "p50_ms": round(float(p50), 3),
        "max_ms": round(max(ordered) if ordered else 0.0, 3),
    }
