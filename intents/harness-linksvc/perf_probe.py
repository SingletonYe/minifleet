"""Measures redirect latency and throughput and publishes them as fleet metrics."""

from __future__ import annotations

import json
import statistics
import threading
import time

from harness.client import Service

CODES = 50
THREADS = 6
PER_THREAD = 100


def metric(name: str, value: float) -> None:
    print("MINIFLEET_METRIC " + json.dumps({"name": name, "value": round(float(value), 3)}))


def main() -> int:
    with Service(rate=100000, burst=100000).start() as service:
        codes = [service.create(f"https://example.com/perf/{i}")["code"] for i in range(CODES)]
        for index in range(120):                      # warm up
            service.redirect(codes[index % CODES])

        latencies: list[float] = []
        errors: list[str] = []
        lock = threading.Lock()

        def worker(seed: int) -> None:
            local: list[float] = []
            for index in range(PER_THREAD):
                started = time.perf_counter()
                status, _ = service.redirect(codes[(seed + index) % CODES])
                local.append((time.perf_counter() - started) * 1000.0)
                if status != 302:
                    with lock:
                        errors.append(f"status {status}")
            with lock:
                latencies.extend(local)

        threads = [threading.Thread(target=worker, args=(i * 7,)) for i in range(THREADS)]
        started = time.perf_counter()
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        elapsed = time.perf_counter() - started

        if errors:
            print(f"probe observed errors: {errors[:5]}", flush=True)
            return 1

        latencies.sort()

        def pct(p: float) -> float:
            if not latencies:
                return 0.0
            index = min(len(latencies) - 1, int(round((p / 100.0) * (len(latencies) - 1))))
            return latencies[index]

        metric("redirect_p50_ms", pct(50))
        metric("redirect_p95_ms", pct(95))
        metric("redirect_p99_ms", pct(99))
        metric("redirect_rps", len(latencies) / elapsed)
        metric("redirect_samples", len(latencies))
        print(f"median={statistics.median(latencies):.3f}ms p99={pct(99):.3f}ms "
              f"rps={len(latencies) / elapsed:.1f}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
