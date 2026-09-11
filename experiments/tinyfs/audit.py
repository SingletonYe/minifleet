#!/usr/bin/env python3
"""Independent audit of a tinyfs build.

The frozen harness is the admission gate. This script is the layer above it: it
re-runs those gates from a clean export, then pushes harder than they do - a much
longer sweep of power-loss points, and a corruption matrix - because a gate that
passes is a claim, and a claim is worth what an independent attempt to break it
is worth.

    python3 experiments/tinyfs/audit.py                 # audit the newest build
    python3 experiments/tinyfs/audit.py --tree /path    # audit a specific tree
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "intents" / "harness-tinyfs"))

from driver import (  # noqa: E402  (harness driver, imported from the frozen source)
    BLOCK, BITMAP_BLOCK, INODE_TABLE_BLOCK, JOURNAL_BLOCK, SUPERBLOCK_BLOCK,
    count_set_bits, fsck, mkfs, pack_inode, payload_for, read_block, superblock,
    workload, write_block,
)

OPS = [
    {"op": "put", "name": "f0", "size": 4096, "fsync": True},
    {"op": "put", "name": "f1", "size": 9000, "fsync": True},
    {"op": "put", "name": "f2", "size": 1, "fsync": True},
    {"op": "put", "name": "f3", "size": 16384, "fsync": True},
    {"op": "put", "name": "dirty", "size": 8192, "fsync": False},
]

GATES = [
    ("api", "python3 -m unittest harness.test_api -v"),
    ("differential", "python3 -m unittest harness.test_differential -v"),
    ("crash", "python3 -m unittest harness.test_crash -v"),
    ("fsck", "python3 -m unittest harness.test_fsck -v"),
    ("regression", "python3 -m unittest discover -s tests"),
    ("policy", "python3 -m minifleet.checks stdlib-only --roots tinyfs --project tinyfs"),
    ("perf", "python3 -m harness.perf_probe"),
]


def newest_tree() -> Path:
    runs = sorted((REPO / "runs" / "tinyfs").glob("*/product"), key=lambda p: p.stat().st_mtime)
    if not runs:
        raise SystemExit("no build found under runs/tinyfs/*/product")
    return runs[-1]


def run(cmd: str, cwd: Path, timeout: int = 1800) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, shell=True, cwd=str(cwd), capture_output=True, text=True, timeout=timeout)


def gate(name: str, cmd: str, tree: Path) -> dict:
    proc = run(cmd, tree)
    tail = [line for line in (proc.stdout + proc.stderr).splitlines() if line.strip()][-1:]
    return {"gate": name, "ok": proc.returncode == 0, "detail": tail[0] if tail else ""}


def crash_sweep(tree: Path, highest: int = 80) -> dict:
    """Far more power-loss points than the frozen harness injects."""

    import os

    os.chdir(tree)
    from tinyfs.fs import FileSystem

    crashed = 0
    checked = 0
    failures: list[str] = []
    for point in range(1, highest + 1):
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / "disk.img"
            progress = Path(tmp) / "progress.log"
            if mkfs(image, blocks=64).returncode != 0:
                failures.append(f"k={point}: mkfs failed")
                continue
            code, entries, tail = workload(image, OPS, progress, crash_after=point)
            crashed += 1 if code == 42 else 0
            fs = FileSystem.mount(str(image))
            fs.unmount()
            report = fsck(image)
            checked += 1
            if not report["ok"]:
                failures.append(f"k={point}: inconsistent after crash: {report['problems'][:2]}")
                continue
            fs = FileSystem.mount(str(image))
            try:
                for entry in entries:
                    if entry.get("op") != "put":
                        continue
                    if fs.read(entry["name"]) != payload_for(entry["name"], entry["size"]):
                        failures.append(f"k={point}: {entry['name']} was fsynced and did not survive")
            finally:
                fs.unmount()
    return {
        "gate": f"crash sweep (k=1..{highest})",
        "ok": not failures and crashed > 0,
        "detail": f"{crashed} crash(es) injected, {checked} images checked, {len(failures)} failure(s)"
                  + (f" e.g. {failures[0]}" if failures else ""),
    }


def corruption_matrix(tree: Path) -> dict:
    """Corrupt a healthy image in ways the frozen harness does not, and demand rejection."""

    import os

    os.chdir(tree)
    from tinyfs.fs import FileSystem

    corruptions: list[tuple[str, callable]] = [
        ("swap two inode names", lambda image: _swap_names(image)),
        ("point an inode past the image", lambda image: _pointer_outside(image)),
        ("mark a used block free in the bitmap", lambda image: _clear_bitmap_bit(image)),
        ("set a bitmap bit for a free block", lambda image: _set_random_bitmap_bit(image)),
        ("committed journal with a wrong crc", lambda image: _bad_journal(image)),
        ("inode claiming more blocks than its size", lambda image: _size_mismatch(image)),
    ]
    rejected = 0
    problems: list[str] = []
    for name, mutate in corruptions:
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / "disk.img"
            if mkfs(image, blocks=64).returncode != 0:
                problems.append(f"{name}: mkfs failed")
                continue
            fs = FileSystem.mount(str(image))
            fs.create("keep")
            fs.write("keep", payload_for("keep", 9000))
            fs.fsync()
            fs.unmount()
            mutate(image)
            report = fsck(image)
            if report["ok"]:
                problems.append(f"{name}: fsck accepted the corruption")
            else:
                rejected += 1
    return {
        "gate": "corruption matrix",
        "ok": not problems,
        "detail": f"{rejected}/{len(corruptions)} rejected" + (f"; {problems[0]}" if problems else ""),
    }


def _free_slot(table: bytes) -> int:
    for index in range(64):
        if not table[index * 64]:
            return index
    raise AssertionError("no free inode slot")


def _put_inode(table: bytes, slot: int, raw: bytes) -> bytes:
    body = bytearray(table)
    body[slot * 64:(slot + 1) * 64] = raw
    return bytes(body)


def _swap_names(image: Path) -> None:
    table = bytearray(read_block(image, INODE_TABLE_BLOCK))
    table[16], table[64 + 16] = table[64 + 16], table[16]
    write_block(image, INODE_TABLE_BLOCK, bytes(table))


def _pointer_outside(image: Path) -> None:
    table = read_block(image, INODE_TABLE_BLOCK)
    ghost = pack_inode(used=True, kind=1, name="ghost", size=BLOCK, blocks=[10 ** 6])
    write_block(image, INODE_TABLE_BLOCK, _put_inode(table, _free_slot(table), ghost))


def _clear_bitmap_bit(image: Path) -> None:
    raw = bytearray(read_block(image, BITMAP_BLOCK))
    for index, byte in enumerate(raw):
        for bit in range(8):
            if byte >> bit & 1:
                raw[index] &= ~(1 << bit)
                write_block(image, BITMAP_BLOCK, bytes(raw))
                return
    raise AssertionError("bitmap was empty")


def _set_random_bitmap_bit(image: Path) -> None:
    geometry = superblock(image)
    index = geometry["data_blocks"] - 2
    raw = bytearray(read_block(image, BITMAP_BLOCK))
    raw[index // 8] |= 1 << (index % 8)
    write_block(image, BITMAP_BLOCK, bytes(raw))


def _bad_journal(image: Path) -> None:
    write_block(
        image, JOURNAL_BLOCK,
        b'{"magic": "TINYFSJ1", "seq": 9, "committed": true, "crc": 424242, '
        b'"payload_blocks": [4, 5]}',
    )


def _size_mismatch(image: Path) -> None:
    table = bytearray(read_block(image, INODE_TABLE_BLOCK))
    table[4:8] = (9000).to_bytes(4, "little")     # inode 0 claims more than its blocks
    write_block(image, INODE_TABLE_BLOCK, bytes(table))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tree", default="")
    parser.add_argument("--sweep", type=int, default=80)
    args = parser.parse_args()
    tree = Path(args.tree).resolve() if args.tree else newest_tree()
    print(f"auditing {tree}\n")

    rows = [gate(name, cmd, tree) for name, cmd in GATES]
    rows.append(crash_sweep(tree, highest=args.sweep))
    rows.append(corruption_matrix(tree))

    width = max(len(row["gate"]) for row in rows)
    for row in rows:
        mark = "ok  " if row["ok"] else "FAIL"
        print(f"  {mark}  {row['gate']:<{width}}  {row['detail'][:110]}")
    failures = [row for row in rows if not row["ok"]]
    print("")
    print(f"{len(rows) - len(failures)}/{len(rows)} checks passed")
    out = REPO / "experiments" / "tinyfs" / "audit.json"
    out.write_text(json.dumps({"tree": str(tree), "rows": rows}, indent=2))
    print(f"written {out}")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
