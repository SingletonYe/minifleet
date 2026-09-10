"""Measures the cost of the project's own regression suite and publishes it as a metric."""

from __future__ import annotations

import json
import subprocess
import sys
import time


def main() -> int:
    started = time.perf_counter()
    proc = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests"],
        capture_output=True, text=True,
    )
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    print("MINIFLEET_METRIC " + json.dumps({"name": "engine_tests_ms", "value": round(elapsed_ms, 1)}))
    tail = (proc.stderr or proc.stdout).strip().splitlines()[-1:] or [""]
    print(f"regression suite: {tail[0]} in {elapsed_ms:.0f} ms", flush=True)
    return 0 if proc.returncode == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
