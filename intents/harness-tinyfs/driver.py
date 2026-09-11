"""Drive tinyfs as a process, and poke at its image from the outside."""

from __future__ import annotations

import json
import os
import signal
import struct
import subprocess
import sys
import tempfile
import time
import zlib
from pathlib import Path

BLOCK = 4096
SUPERBLOCK_BLOCK = 0
JOURNAL_BLOCK = 1
JOURNAL_PAYLOAD_BLOCKS = 2
INODE_TABLE_BLOCK = 4
BITMAP_BLOCK = 5
DATA_START = 8
INODE_COUNT = 64
INODE_SIZE = 64
MAX_FILE = 4 * BLOCK
MAX_NAME = 31
MAGIC = "TINYFS1"
JOURNAL_MAGIC = "TINYFSJ1"
CRASH_ENV = "TINYFS_CRASH_AFTER_WRITES"


def payload_for(name: str, size: int) -> bytes:
    """The frozen deterministic data for a workload entry."""

    if size <= 0:
        return b""
    seed = name.encode()
    return (seed * (size // len(seed) + 1))[:size]


def cli(args: list[str], timeout: float = 120.0, env: dict[str, str] | None = None,
        cwd: str | None = None) -> subprocess.CompletedProcess:
    environment = dict(os.environ)
    environment.pop(CRASH_ENV, None)
    if env:
        environment.update(env)
    return subprocess.run(
        [sys.executable, "-m", "tinyfs", *args],
        capture_output=True, text=True, timeout=timeout, cwd=cwd, env=environment,
    )


def mkfs(image: Path | str, blocks: int = 256) -> subprocess.CompletedProcess:
    return cli(["mkfs", "--image", str(image), "--blocks", str(blocks)])


def put(image: Path | str, name: str, size: int, fsync: bool = False) -> subprocess.CompletedProcess:
    with tempfile.TemporaryDirectory() as tmp:
        data_file = Path(tmp) / "payload"
        data_file.write_bytes(payload_for(name, size))
        args = ["put", "--image", str(image), "--name", name, "--data-file", str(data_file)]
        if fsync:
            args.append("--fsync")
        return cli(args)


def get(image: Path | str, name: str) -> subprocess.CompletedProcess:
    return cli(["get", "--image", str(image), "--name", name])


def rm(image: Path | str, name: str, fsync: bool = False) -> subprocess.CompletedProcess:
    args = ["rm", "--image", str(image), "--name", name]
    if fsync:
        args.append("--fsync")
    return cli(args)


def ls(image: Path | str) -> list[tuple[str, int]]:
    proc = cli(["ls", "--image", str(image)])
    if proc.returncode != 0:
        raise AssertionError(f"ls failed: {proc.returncode} {proc.stderr}")
    entries: list[tuple[str, int]] = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        name, _, size = line.rpartition(" ")
        entries.append((name, int(size)))
    return sorted(entries)


def fsck(image: Path | str) -> dict:
    proc = cli(["fsck", "--image", str(image)])
    body = proc.stdout.strip()
    start = body.find("{")
    payload = json.loads(body[start:]) if start >= 0 else {"ok": False, "problems": [body]}
    payload["exit_code"] = proc.returncode
    return payload


def workload(image: Path | str, ops: list[dict], progress: Path | str,
             crash_after: int | None = None, kill_after: float | None = None,
             timeout: float = 180.0) -> tuple[int, list[dict], str]:
    """Run a scripted workload in its own process, optionally crashing it hard.

    ``progress`` is a real file outside the image: the workload appends one JSON
    line per operation whose durability barrier returned. That is how the harness
    knows exactly which operations the filesystem promised to keep.
    """

    with tempfile.TemporaryDirectory() as tmp:
        script = Path(tmp) / "script.json"
        script.write_text(json.dumps(ops))
        args = [sys.executable, "-m", "tinyfs", "workload", "--image", str(image),
                "--script", str(script), "--progress", str(progress)]
        environment = dict(os.environ)
        environment.pop(CRASH_ENV, None)
        if crash_after is not None:
            environment[CRASH_ENV] = str(crash_after)
        proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, env=environment)
        if kill_after is not None:
            time.sleep(kill_after)
            if proc.poll() is None:
                proc.send_signal(signal.SIGKILL)
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            raise AssertionError("workload did not finish: the crash hook probably never fired")
        progress_path = Path(progress)
        entries = [
            json.loads(line)
            for line in progress_path.read_text().splitlines()
            if line.strip()
        ] if progress_path.exists() else []
        return (proc.returncode or 0), entries, (stderr or stdout)[-800:]


def read_block(image: Path | str, index: int) -> bytes:
    with open(image, "rb") as handle:
        handle.seek(index * BLOCK)
        return handle.read(BLOCK)


def write_block(image: Path | str, index: int, data: bytes) -> None:
    if len(data) > BLOCK:
        raise ValueError("block payload too large")
    with open(image, "r+b") as handle:
        handle.seek(index * BLOCK)
        handle.write(bytes(data).ljust(BLOCK, b"\0"))


def superblock(image: Path | str) -> dict:
    return json.loads(read_block(image, SUPERBLOCK_BLOCK).rstrip(b"\0").decode())


def inode_is_used(image: Path | str, index: int) -> bool:
    return bool(read_block(image, INODE_TABLE_BLOCK)[index * INODE_SIZE])


def bitmap_is_set(image: Path | str, data_index: int) -> bool:
    raw = read_block(image, BITMAP_BLOCK)
    return bool((raw[data_index // 8] >> (data_index % 8)) & 1)


def count_set_bits(image: Path | str, limit: int) -> int:
    raw = read_block(image, BITMAP_BLOCK)
    return sum((raw[index // 8] >> (index % 8)) & 1 for index in range(limit))


def payload_crc(image: Path | str, count: int = JOURNAL_PAYLOAD_BLOCKS) -> int:
    data = b"".join(read_block(image, JOURNAL_BLOCK + 1 + offset) for offset in range(count))
    return zlib.crc32(data)


def pack_inode(*, used: bool, kind: int, name: str, size: int, blocks: list[int],
               mtime: int = 0) -> bytes:
    """Build a raw inode the way the contract describes it (for corruption tests)."""

    name_bytes = name.encode()
    if len(name_bytes) > MAX_NAME:
        raise ValueError("name too long")
    body = bytearray(INODE_SIZE)
    body[0] = 1 if used else 0
    body[1] = kind
    body[2] = len(name_bytes)
    struct.pack_into("<II", body, 4, size, mtime)
    body[16:16 + len(name_bytes)] = name_bytes
    for slot, block in enumerate(blocks[:4]):
        struct.pack_into("<I", body, 48 + slot * 4, block)
    return bytes(body)
