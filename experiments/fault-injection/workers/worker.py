#!/usr/bin/env python3
"""A deterministic fleet worker that injects a real defect on its first attempt.

MiniFleet calls a command dispatcher as ``<cmd> <packet.md> <result.json>``.
This worker decides what to do from one thing only: whether a repair packet
already exists for its task.

* no repair packet -> attempt 1: copy the defective variant in, together with the
  worker's own (weak) tests. The task gate passes; the frozen harness is expected
  to fail.
* repair packet present -> attempt 2: read it, report which gates failed and why,
  and copy the correct variant in.

The defect is not in the engine. It is the kind of defect a worker really
produces, and the experiment's claim is that the fleet detects it, attributes it
and repairs it without a human reading the diff.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
VARIANTS = HERE.parent / "variants"


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: worker.py <packet.md> <result.json>", file=sys.stderr)
        return 2

    packet_path = Path(argv[1])
    result_path = Path(argv[2])
    packet = json.loads(packet_path.with_suffix(".json").read_text())
    task_id = packet["task"]["id"]
    worktree = Path(packet["worktree"])
    run_dir = Path(packet["product_dir"]).parent
    repair_path = run_dir / "packets" / f"{task_id}.repair.json"

    repair = json.loads(repair_path.read_text()) if repair_path.exists() else None
    source = "quarantine_correct.py" if repair else "quarantine_faulty.py"

    if repair:
        failing = ", ".join(row["gate_id"] for row in repair.get("failing_gates", []))
        notes = (
            f"attempt 2: read {repair_path.name}; the fleet attributed "
            f"{repair.get('reason', 'a failure')} to this task; fixing {failing}"
        )
    else:
        notes = "attempt 1: implementation and tests written; no repair packet existed"

    written: list[str] = []
    for target_rel, source_name in (
        ("minifleet/quarantine.py", source),
        ("tests/test_quarantine.py", "test_quarantine_weak.py"),
    ):
        target = worktree / target_rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(VARIANTS / source_name, target)
        written.append(target_rel)

    result_path.write_text(
        json.dumps(
            {
                "task_id": task_id,
                "status": "submitted",
                "files": sorted(written),
                "commands": ["python3 -m unittest discover -s tests -v"],
                "attempt_kind": "repair" if repair else "first",
                "notes": notes,
            },
            indent=2,
            sort_keys=True,
        )
    )
    print(f"[worker] {task_id}: {notes}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
