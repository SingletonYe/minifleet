#!/usr/bin/env python3
"""Independent audit of a tinyfs build.

The frozen harness is the admission gate: nothing enters production without it.
This script is the layer *above* the gate, and it exists because a passing gate is
a claim, and a claim is worth exactly what an independent attempt to break it is
worth. It asks four questions the run itself cannot ask:

1. Is the build reproducible?  A clean `git archive` export of the integration
   branch must carry the whole system, judges included, and pass the same gates.

2. Is the judge still the judge?  The bytes of `harness/**` in the product must be
   the bytes that were frozen before the first worker was dispatched.

3. Does the artifact break under *more* pressure than the gate applied?  The gate
   kills the writer at 20 block-write boundaries; this sweeps 120, adds a crash
   during journal replay, and drives a long randomised workload across the process
   boundary against a shadow model.

4. Is fsck actually read-only and actually strict?  The same corruption matrix the
   contract lists, with a byte hash taken before and after each check.

    python3 experiments/tinyfs/audit.py                    # audit the newest build
    python3 experiments/tinyfs/audit.py --tree /path       # audit a specific tree
    python3 experiments/tinyfs/audit.py --sweep 200        # push harder
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
HARNESS_SRC = REPO / "intents" / "harness-tinyfs"
sys.path.insert(0, str(HARNESS_SRC))

from driver import (  # noqa: E402  (the frozen driver, not a copy of it)
    BITMAP_BLOCK,
    BLOCK,
    DATA_START,
    INODE_TABLE_BLOCK,
    JOURNAL_BLOCK,
    MAX_FILE,
    SUPERBLOCK_BLOCK,
    cli,
    fsck,
    mkfs,
    pack_inode,
    payload_for,
    read_block,
    superblock,
    workload,
    write_block,
)

OPS = [
    {"op": "put", "name": "f0", "size": 4096, "fsync": True},
    {"op": "put", "name": "f1", "size": 9000, "fsync": True},
    {"op": "put", "name": "f2", "size": 1, "fsync": True},
    {"op": "put", "name": "f3", "size": 16384, "fsync": True},
    {"op": "put", "name": "dirty", "size": 8192, "fsync": False},
]

GATES = [
    ("harness: api", "python3 -m unittest harness.test_api -v"),
    ("harness: differential", "python3 -m unittest harness.test_differential -v"),
    ("harness: crash", "python3 -m unittest harness.test_crash -v"),
    ("harness: fsck", "python3 -m unittest harness.test_fsck -v"),
    ("regression suite", "python3 -m unittest discover -s tests"),
    ("stdlib-only policy", "python3 -m minifleet.checks stdlib-only --roots tinyfs --project tinyfs"),
    ("perf probe", "python3 -m harness.perf_probe"),
]


# -- helpers -------------------------------------------------------------
def newest_tree() -> Path:
    trees = sorted((REPO / "runs" / "tinyfs").glob("*/product"), key=lambda p: p.stat().st_mtime)
    if not trees:
        raise SystemExit("no build found under runs/tinyfs/*/product")
    return trees[-1]


def sha_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tree_digest(root: Path, pattern: str = "*") -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): sha_of(path)
        for path in sorted(root.rglob(pattern))
        if path.is_file() and "__pycache__" not in path.parts
    }


def sh(cmd: str, cwd: Path, timeout: int = 2400) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, shell=True, cwd=str(cwd), capture_output=True, text=True, timeout=timeout)


def gate(name: str, cmd: str, tree: Path) -> dict:
    proc = sh(cmd, tree)
    lines = [line for line in (proc.stdout + proc.stderr).splitlines() if line.strip()]
    return {"gate": name, "ok": proc.returncode == 0, "detail": lines[-1][:160] if lines else ""}


def export_branch(tree: Path, destination: Path) -> None:
    """A clean export of the integration branch: no worktrees, no caches, no run records."""

    destination.mkdir(parents=True, exist_ok=True)
    tarball = destination.parent / "export.tar"
    with open(tarball, "wb") as handle:
        proc = subprocess.run(["git", "archive", "--format=tar", "HEAD"],
                              cwd=str(tree), stdout=handle, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        raise SystemExit(f"git archive failed: {proc.stderr[:400]!r}")
    proc = subprocess.run(["tar", "-xf", str(tarball), "-C", str(destination)],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        raise SystemExit(f"tar failed: {proc.stderr[:400]}")
    tarball.unlink()


# -- the four questions --------------------------------------------------
def check_reproducible(tree: Path) -> tuple[list[dict], Path]:
    """Export the branch and re-run every gate on the export."""

    scratch = Path(tempfile.mkdtemp(prefix="tinyfs-export-"))
    export = scratch / "tree"
    export_branch(tree, export)
    rows = [gate(name, cmd, export) for name, cmd in GATES]
    shipped = {"gate": "clean export is self-contained",
               "ok": all(row["ok"] for row in rows),
               "detail": f"{sum(row['ok'] for row in rows)}/{len(rows)} gates pass on a git-archive export"}
    return [shipped, *rows], export


def check_judge_untouched(tree: Path) -> dict:
    frozen = tree_digest(HARNESS_SRC)
    shipped = tree_digest(tree / "harness")
    missing = sorted(set(frozen) - set(shipped))
    changed = sorted(rel for rel in set(frozen) & set(shipped) if frozen[rel] != shipped[rel])
    extra = sorted(set(shipped) - set(frozen))
    ok = not missing and not changed and not extra
    return {
        "gate": "frozen harness shipped byte-identical",
        "ok": ok,
        "detail": (
            f"{len(frozen)} harness files match"
            + (f"; changed: {changed}" if changed else "")
            + (f"; missing: {missing}" if missing else "")
            + (f"; added to the judge: {extra}" if extra else "")
        ),
    }


def check_scope(tree: Path, intent: Path) -> dict:
    """The build may only have written the paths the intent declared."""

    spec = json.loads(intent.read_text())
    declared = {path for component in spec["components"] for path in component["owns"]}
    frozen = {"ACCEPTANCE.md", "INTENT.md", "README.md", "contracts/C-1.md"}
    changed = {
        line.split("\t", 1)[1].strip()
        for line in sh("git diff --name-status $(git rev-list --max-parents=0 HEAD) HEAD", tree).stdout.splitlines()
        if line.strip() and "\t" in line
    }
    outside = sorted(
        path for path in changed
        if path not in declared
        and path not in frozen
        and not path.startswith("harness/")
        and not path.endswith((".pyc", ".pyo"))
        and "__pycache__" not in path
    )
    missing = sorted(declared - changed)
    return {
        "gate": "every written path was declared",
        "ok": not outside and not missing,
        "detail": (
            f"{len(changed)} path(s) changed, all inside the declared write scopes"
            if not outside and not missing
            else f"outside the plan: {outside}; never delivered: {missing}"
        ),
    }


def crash_sweep(highest: int) -> dict:
    """More power-loss points than the gate injects, plus a fault during replay."""

    failures: list[str] = []
    crashed = 0
    replayed = 0
    for point in range(1, highest + 1):
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / "disk.img"
            progress = Path(tmp) / "progress.log"
            if mkfs(image, blocks=64).returncode != 0:
                failures.append(f"k={point}: mkfs failed")
                continue
            code, entries, tail = workload(image, OPS, progress, crash_after=point)
            crashed += 1 if code == 42 else 0
            replay(image)
            if point % 3 == 0:
                # A second fault, this time during journal replay: mounting once is
                # what applies a committed record, so crashing there is the nastiest
                # window the format has.
                second, _, _ = workload(image, [], Path(tmp) / "unused.log",
                                        crash_after=point % 5 + 1)
                replayed += 1
                if second not in (0, 42):
                    failures.append(f"k={point}: replay crashed with {second}")
            report = fsck(image)
            if not report["ok"]:
                failures.append(f"k={point}: inconsistent after the crash: {report['problems'][:2]}")
                continue
            for entry in entries:
                if entry.get("op") != "put":
                    continue
                if read_back(image, entry["name"]) != payload_for(entry["name"], entry["size"]):
                    failures.append(f"k={point}: {entry['name']} was fsynced and did not survive")
    return {
        "gate": f"crash sweep k=1..{highest} (+{replayed} double faults)",
        "ok": not failures and crashed > 0,
        "detail": f"{crashed}/{highest} crash(es) injected, {len(failures)} failure(s)"
                  + (f" e.g. {failures[0]}" if failures else ""),
        "failures": failures[:8],
    }


def replay(image: Path) -> None:
    """Mounting is what applies a committed journal record; then unmount."""

    script = Path(image).parent / "replay.json"
    script.write_text("[]")
    workload(image, [], script)


def read_back(image: Path, name: str) -> bytes:
    """Read a file back as raw bytes, through a fresh process."""

    proc = subprocess.run(
        [sys.executable, "-m", "tinyfs", "get", "--image", str(image), "--name", name],
        capture_output=True,
    )
    if proc.returncode != 0:
        return b"<missing>"
    return proc.stdout


def model_across_the_process_boundary(steps: int = 120) -> dict:
    """A long random script through the CLI, compared with a shadow model.

    The gate's differential test runs in-process; this one never imports tinyfs.
    Every operation is a fresh process, remounts included, and the payload is
    compared byte for byte on the way out.
    """

    names = ["a", "bb", "ccc", "file-1", "file-2", "log", "tmp", "zed"]
    rng = random.Random(4242)
    with tempfile.TemporaryDirectory() as tmp:
        image = Path(tmp) / "disk.img"
        if mkfs(image, blocks=256).returncode != 0:
            return {"gate": "model over the CLI", "ok": False, "detail": "mkfs failed"}
        model: dict[str, bytes] = {}
        for step in range(steps):
            name = rng.choice(names)
            action = rng.choices(["put", "get", "rm", "ls"], weights=[5, 3, 2, 2])[0]
            if action == "put":
                size = rng.choice([0, 1, 512, 4096, 4097, 9000, MAX_FILE])
                data = rng.randbytes(size)
                outcome = put_bytes(image, name, data, fsync=rng.random() < 0.4)
                if outcome != 0:
                    return {"gate": "model over the CLI", "ok": False,
                            "detail": f"step {step}: put {name!r} ({size}B) exited {outcome}"}
                model[name] = data
            elif action == "get":
                got = read_back(image, name)
                expected = model.get(name)
                if expected is None:
                    continue
                if got != expected:
                    return {"gate": "model over the CLI", "ok": False,
                            "detail": f"step {step}: {name!r} came back wrong from a fresh process"}
            elif action == "rm":
                if name in model:
                    proc = cli(["rm", "--image", str(image), "--name", name, "--fsync"])
                    if proc.returncode != 0:
                        return {"gate": "model over the CLI", "ok": False,
                                "detail": f"step {step}: rm {name!r} exited {proc.returncode}"}
                    model.pop(name)
            else:
                listing = cli(["ls", "--image", str(image)])
                parsed = sorted(
                    (line.rsplit(" ", 1)[0], int(line.rsplit(" ", 1)[1]))
                    for line in listing.stdout.splitlines() if line.strip()
                )
                expected = sorted((key, len(value)) for key, value in model.items())
                if parsed != expected:
                    return {"gate": "model over the CLI", "ok": False,
                            "detail": f"step {step}: listing {parsed} != model {expected}"}
        report = fsck(image)
        size_ok = image.stat().st_size == 256 * BLOCK
        return {
            "gate": f"model over the CLI ({steps} processes)",
            "ok": report["ok"] and size_ok,
            "detail": f"fsck ok={report['ok']}, image is exactly {256 * BLOCK} bytes: {size_ok}",
        }


def put_bytes(image: Path, name: str, data: bytes, fsync: bool) -> int:
    with tempfile.TemporaryDirectory() as tmp:
        blob = Path(tmp) / "blob"
        blob.write_bytes(data)
        args = ["put", "--image", str(image), "--name", name, "--data-file", str(blob)]
        if fsync:
            args.append("--fsync")
        return cli(args).returncode


# -- the corruption matrix, taken from the contract ----------------------
def corruption_matrix() -> dict:
    """Every corruption the contract says fsck must reject, plus the read-only promise."""

    cases = [
        ("bad superblock magic", _bad_magic),
        ("image truncated", _truncate),
        ("inode name longer than 31 bytes", _overlong_name),
        ("unknown inode kind", _unknown_kind),
        ("size above the maximum", _oversized),
        ("block pointer outside the data area", _pointer_outside),
        ("two inodes sharing a block", _shared_block),
        ("block count disagreeing with the size", _size_without_blocks),
        ("bitmap bit set for a block nobody references", _orphan_bit),
        ("bitmap bit set outside the data area", _bitmap_outside),
        ("committed journal whose crc does not match", _bad_journal),
    ]
    accepted: list[str] = []
    modified: list[str] = []
    rejected = 0
    for name, mutate in cases:
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / "disk.img"
            mkfs(image, blocks=64)
            healthy(image)
            mutate(image)
            before = sha_of(image)
            report = fsck(image)
            if report["ok"]:
                accepted.append(name)
            elif not report["problems"]:
                accepted.append(f"{name} (failed without saying why)")
            else:
                rejected += 1
            if sha_of(image) != before:
                modified.append(name)
    return {
        "gate": f"corruption matrix ({len(cases)} cases)",
        "ok": not accepted and not modified,
        "detail": f"{rejected}/{len(cases)} rejected"
                  + (f"; accepted: {accepted}" if accepted else "")
                  + (f"; fsck modified the image for: {modified}" if modified else ""),
    }


def healthy(image: Path) -> None:
    put_bytes(image, "keep", payload_for("keep", 9000), fsync=True)
    put_bytes(image, "other", payload_for("other", 4096), fsync=True)


def _bad_magic(image: Path) -> None:
    raw = read_block(image, SUPERBLOCK_BLOCK)
    write_block(image, SUPERBLOCK_BLOCK, raw.replace(b"TINYFS1", b"NOTFS1"))


def _truncate(image: Path) -> None:
    with open(image, "r+b") as handle:
        handle.truncate(BLOCK * 32)


def _overlong_name(image: Path) -> None:
    table = bytearray(read_block(image, INODE_TABLE_BLOCK))
    table[2] = 40
    write_block(image, INODE_TABLE_BLOCK, bytes(table))


def _unknown_kind(image: Path) -> None:
    table = bytearray(read_block(image, INODE_TABLE_BLOCK))
    table[1] = 7
    write_block(image, INODE_TABLE_BLOCK, bytes(table))


def _oversized(image: Path) -> None:
    table = bytearray(read_block(image, INODE_TABLE_BLOCK))
    table[4:8] = (MAX_FILE + 4096).to_bytes(4, "little")
    write_block(image, INODE_TABLE_BLOCK, bytes(table))


def _pointer_outside(image: Path) -> None:
    table = read_block(image, INODE_TABLE_BLOCK)
    ghost = pack_inode(used=True, kind=1, name="ghost", size=BLOCK, blocks=[10 ** 6])
    write_block(image, INODE_TABLE_BLOCK, _put_inode(table, _free_slot(table), ghost))


def _shared_block(image: Path) -> None:
    table = bytearray(read_block(image, INODE_TABLE_BLOCK))
    first = int.from_bytes(table[48:52], "little") or int.from_bytes(table[64 + 48:64 + 52], "little")
    twin = pack_inode(used=True, kind=1, name="twin", size=BLOCK, blocks=[first])
    write_block(image, INODE_TABLE_BLOCK, _put_inode(bytes(table), _free_slot(bytes(table)), twin))


def _size_without_blocks(image: Path) -> None:
    table = read_block(image, INODE_TABLE_BLOCK)
    liar = pack_inode(used=True, kind=1, name="liar", size=9000, blocks=[])
    write_block(image, INODE_TABLE_BLOCK, _put_inode(table, _free_slot(table), liar))


def _orphan_bit(image: Path) -> None:
    geometry = superblock(image)
    index = geometry["data_blocks"] - 1
    raw = bytearray(read_block(image, BITMAP_BLOCK))
    raw[index // 8] |= 1 << (index % 8)
    write_block(image, BITMAP_BLOCK, bytes(raw))


def _bitmap_outside(image: Path) -> None:
    geometry = superblock(image)
    raw = bytearray(read_block(image, BITMAP_BLOCK))
    bit = geometry["data_blocks"] + 4
    if bit // 8 < BLOCK:
        raw[bit // 8] |= 1 << (bit % 8)
    else:
        raw[-1] |= 0x80
    write_block(image, BITMAP_BLOCK, bytes(raw))


def _bad_journal(image: Path) -> None:
    write_block(image, JOURNAL_BLOCK,
                b'{"magic": "TINYFSJ1", "seq": 9, "committed": true, "crc": 424242, "payload_blocks": [4, 5]}')


def _free_slot(table: bytes) -> int:
    for index in range(64):
        if not table[index * 64]:
            return index
    raise AssertionError("no free inode slot")


def _put_inode(table: bytes, slot: int, raw: bytes) -> bytes:
    body = bytearray(table)
    body[slot * 64:(slot + 1) * 64] = raw
    return bytes(body)


# -- invariants the format promises --------------------------------------
def structural_invariants() -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        image = Path(tmp) / "disk.img"
        progress = Path(tmp) / "progress.log"
        mkfs(image, blocks=64)
        workload(image, OPS, progress)
        geometry = superblock(image)
        problems = []
        if image.stat().st_size != 64 * BLOCK:
            problems.append(f"image is {image.stat().st_size} bytes, expected {64 * BLOCK}")
        for reserved in (6, 7):
            if read_block(image, reserved).strip(b"\0"):
                problems.append(f"reserved block {reserved} is not zero")
        bitmap = read_block(image, BITMAP_BLOCK)
        outside = sum(
            (bitmap[bit // 8] >> (bit % 8)) & 1
            for bit in range(geometry["data_blocks"], BLOCK * 8)
        )
        if outside:
            problems.append(f"{outside} bitmap bit(s) set outside the data area")
        report = fsck(image)
        if not report["ok"]:
            problems.append(f"fsck: {report['problems'][:2]}")
        return {
            "gate": "on-disk invariants after a workload",
            "ok": not problems,
            "detail": "; ".join(problems) if problems else
                      f"size, reserved blocks, bitmap bounds and fsck all hold (data_start={DATA_START})",
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tree", default="")
    parser.add_argument("--intent", default=str(REPO / "intents" / "tinyfs.compiled.json"))
    parser.add_argument("--sweep", type=int, default=120)
    parser.add_argument("--keep-export", action="store_true")
    args = parser.parse_args()

    tree = Path(args.tree).resolve() if args.tree else newest_tree()
    print(f"auditing {tree}\n")

    rows, export = check_reproducible(tree)
    # Everything below talks to the *export*, through the process boundary, so the
    # audit never accidentally verifies the worktrees the fleet was building in.
    os.chdir(export)
    sys.path.insert(0, str(export))

    rows.append(check_judge_untouched(tree))
    rows.append(check_scope(tree, Path(args.intent)))
    rows.append(structural_invariants())
    rows.append(model_across_the_process_boundary())
    rows.append(crash_sweep(highest=args.sweep))
    rows.append(corruption_matrix())

    width = max(len(row["gate"]) for row in rows)
    for row in rows:
        print(f"  {'ok  ' if row['ok'] else 'FAIL'}  {row['gate']:<{width}}  {row['detail'][:120]}")
    failures = [row for row in rows if not row["ok"]]
    print()
    print(f"{len(rows) - len(failures)}/{len(rows)} checks passed")

    out = REPO / "experiments" / "tinyfs" / "audit.json"
    out.write_text(json.dumps({"tree": str(tree), "rows": rows}, indent=2))
    print(f"written {out}")
    if not args.keep_export:
        shutil.rmtree(export.parent, ignore_errors=True)
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
