"""Run ledger and traceability.

The ledger exists so that a human can answer, for any sentence in the final
report: *which agent wrote this, which gate checked it, and what did the gate
actually observe?* If a statement cannot be traced that way, MiniFleet marks it
as unverified rather than asserting it.
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any, Iterable

from .model import Design, Evidence, IntentSpec, Run, TaskState


def run_id(intent_id: str) -> str:
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    return f"{stamp}-{intent_id}-{uuid.uuid4().hex[:6]}"


def create(runs_dir: Path | str, spec: IntentSpec, dispatcher: str, raw_intent: str = "") -> tuple[Run, Path]:
    rid = run_id(spec.id)
    path = Path(runs_dir) / rid
    (path / "packets").mkdir(parents=True, exist_ok=True)
    (path / "evidence").mkdir(parents=True, exist_ok=True)
    run = Run(
        id=rid,
        intent_id=spec.id,
        dispatcher=dispatcher,
        repo_dir=str(path / "product"),
        intent=spec.to_dict(),
        raw_intent=raw_intent,
    )
    run.log("run.created", intent=spec.id, dispatcher=dispatcher)
    save(run, path)
    return run, path


def save(run: Run, path: Path | str) -> None:
    run.save(path)
    with (Path(path) / "ledger.jsonl").open("a") as handle:
        for event in run.events[-1:]:
            handle.write(json.dumps(event, sort_keys=True) + "\n")


def append(run: Run, path: Path | str, event: str, **fields: Any) -> None:
    entry = run.log(event, **fields)
    with (Path(path) / "ledger.jsonl").open("a") as handle:
        handle.write(json.dumps(entry, sort_keys=True) + "\n")


def design_of(run: Run) -> Design:
    return Design.from_dict(run.design)


def evidence_of(run: Run) -> list[Evidence]:
    return [Evidence.from_dict(item) for item in run.evidence]


def traceability(spec: IntentSpec, design: Design, evidence: Iterable[Evidence]) -> list[dict[str, Any]]:
    """criterion -> tasks -> gates -> observation."""

    by_gate: dict[str, Evidence] = {}
    for item in evidence:
        by_gate[item.gate_id] = item

    rows: list[dict[str, Any]] = []
    for criterion in spec.acceptance:
        owners = [t.id for t in design.tasks if criterion.id in t.acceptance_ids]
        gates = []
        for gate_id in criterion.gates:
            item = by_gate.get(gate_id)
            gates.append(
                {
                    "id": gate_id,
                    "status": item.status if item else "unproven",
                    "detail": item.detail if item else "no evidence recorded",
                }
            )
        statuses = {g["status"] for g in gates}
        if not gates:
            overall = "unproven"
        elif statuses == {"pass"}:
            overall = "verified"
        elif "fail" in statuses:
            overall = "failed"
        else:
            overall = "incomplete"
        rows.append(
            {
                "criterion": criterion.id,
                "statement": criterion.statement,
                "kind": criterion.kind,
                "tasks": owners,
                "gates": gates,
                "status": overall,
            }
        )
    return rows


def summary(run: Run) -> str:
    design = design_of(run)
    states: dict[str, int] = {}
    for task in design.tasks:
        states[task.state] = states.get(task.state, 0) + 1
    parts = ", ".join(f"{k}={v}" for k, v in sorted(states.items()))
    return f"run {run.id}  status={run.status}  tasks[{parts}]  evidence={len(run.evidence)}"


def pending_tasks(run: Run) -> list[str]:
    design = design_of(run)
    return [
        t.id
        for t in design.tasks
        if t.state in {TaskState.PENDING.value, TaskState.READY.value}
    ]
