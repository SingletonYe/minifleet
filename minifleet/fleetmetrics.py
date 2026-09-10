"""Fleet-level accounting: what a run cost, and where the time actually went.

A per-task view answers "did task X pass". The operator needs the other
question: how many attempts did the fleet burn, which gates failed, and which
chain of dependent tasks set the wall-clock floor for the run.
"""

from __future__ import annotations

from typing import Any


def _duration(task: dict[str, Any]) -> float:
    started = task.get("started_at")
    finished = task.get("finished_at")
    if started is None or finished is None:
        return 0.0
    try:
        return max(0.0, float(finished) - float(started))
    except (TypeError, ValueError):
        return 0.0


def critical_path_seconds(tasks: list[dict[str, Any]]) -> float:
    """Longest chain of task durations following ``depends_on`` edges.

    This is deliberately *not* the sum of all durations: parallel work that
    overlaps in time must not be charged twice.
    """

    by_id = {str(task.get("id", "")): task for task in tasks}
    memo: dict[str, float] = {}

    def longest(task_id: str, seen: frozenset[str]) -> float:
        if task_id in memo:
            return memo[task_id]
        if task_id in seen:  # a dependency cycle must not hang the report
            return 0.0
        task = by_id.get(task_id)
        if task is None:
            return 0.0
        parents = [
            longest(str(dep), seen | {task_id})
            for dep in (task.get("depends_on") or [])
            if str(dep) in by_id
        ]
        total = _duration(task) + (max(parents) if parents else 0.0)
        memo[task_id] = total
        return total

    return round(max((longest(task_id, frozenset()) for task_id in by_id), default=0.0), 3)


def summarize(run: dict[str, Any]) -> dict[str, Any]:
    """Account for one run record (a decoded ``run.json``)."""

    design = run.get("design") or {}
    tasks = list(design.get("tasks") or [])
    evidence = list(run.get("evidence") or [])

    merged = sum(1 for task in tasks if task.get("state") == "merged")
    failed = sum(1 for task in tasks if task.get("state") == "failed")
    attempts_total = sum(int(task.get("attempts") or 0) for task in tasks)
    retries = sum(max(int(task.get("attempts") or 0) - 1, 0) for task in tasks)

    gates_passed = sum(1 for item in evidence if item.get("status") == "pass")
    gates_total = len(evidence)

    slowest: dict[str, Any] | None = None
    for task in tasks:
        seconds = round(_duration(task), 3)
        if slowest is None or seconds > slowest["seconds"]:
            slowest = {"id": str(task.get("id", "")), "seconds": seconds}

    verdict = str(run.get("verdict") or run.get("status") or "")
    return {
        "tasks": len(tasks),
        "merged": merged,
        "failed": failed,
        "attempts_total": attempts_total,
        "retries": retries,
        "gates_total": gates_total,
        "gates_passed": gates_passed,
        "gates_failed": gates_total - gates_passed,
        "critical_path_seconds": critical_path_seconds(tasks),
        "slowest_task": slowest,
        "verdict": verdict,
    }
