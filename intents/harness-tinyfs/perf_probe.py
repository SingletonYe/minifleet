"""Measure what the filesystem costs, and publish the numbers as fleet metrics."""

from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path

from harness import driver

from tinyfs.fs import FileSystem

FILES = 32
SMALL = 200


def metric(name: str, value: float) -> None:
    print("MINIFLEET_METRIC " + json.dumps({"name": name, "value": round(float(value), 3)}))


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        image = Path(tmp) / "disk.img"
        driver.mkfs(image, blocks=2048)
        fs = FileSystem.mount(str(image))
        try:
            payloads = {}
            started = time.perf_counter()
            for index in range(FILES):
                name = f"big{index}"
                payloads[name] = driver.payload_for(name, driver.MAX_FILE)
                fs.create(name)
                fs.write(name, payloads[name])
            fs.fsync()
            write_seconds = time.perf_counter() - started

            started = time.perf_counter()
            for name, expected in payloads.items():
                if fs.read(name) != expected:
                    print(f"read-back mismatch for {name}", flush=True)
                    return 1
            read_seconds = time.perf_counter() - started

            started = time.perf_counter()
            for index in range(SMALL):
                name = f"s{index}"
                fs.create(name)
                fs.write(name, b"x" * 16)
            fs.fsync()
            create_seconds = time.perf_counter() - started
        finally:
            fs.unmount()

        megabytes = FILES * driver.MAX_FILE / (1024 * 1024)
        metric("fs_write_mbps", megabytes / max(write_seconds, 1e-6))
        metric("fs_read_mbps", megabytes / max(read_seconds, 1e-6))
        metric("fs_create_ops", SMALL / max(create_seconds, 1e-6))
        print(
            f"write {megabytes / max(write_seconds, 1e-6):.2f} MB/s, "
            f"read {megabytes / max(read_seconds, 1e-6):.2f} MB/s, "
            f"create {SMALL / max(create_seconds, 1e-6):.0f} ops/s",
            flush=True,
        )
        report = driver.fsck(image)
        if not report["ok"]:
            print(f"fsck after the benchmark: {report['problems']}", flush=True)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
