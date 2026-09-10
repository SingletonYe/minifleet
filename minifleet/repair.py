"""Bounded self-repair: turn gate evidence into a packet a worker can act on.

The fleet's rule is that a failure must produce *action*, not a retry by
default. ``build_repair_packet`` is the only sanctioned way to turn a failed
attempt into the next one: it is bounded by an attempt budget, it names the
exact gates that failed, and it never mutates the records it was given.
"""

from __future__ import annotations

from typing import Any


def failing_gates(evidence: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Every non-passing evidence row, ordered by gate id.

    A gate that never produced evidence is not silently treated as a pass:
    callers see exactly the rows they handed in, minus the passing ones.
    """

    rows: list[dict[str, str]] = []
    for item in evidence:
        status = str(item.get("status", "") or "")
        if status == "pass":
            continue
        rows.append(
            {
                "gate_id": str(item.get("gate_id", "") or ""),
                "status": status or "unknown",
                "detail": str(item.get("detail", "") or ""),
            }
        )
    return sorted(rows, key=lambda row: row["gate_id"])


def _instructions(
    failures: list[dict[str, str]], attempt: int, max_attempts: int, escalate: bool
) -> list[str]:
    if not failures:
        return [
            "No failing gate was recorded for this attempt.",
            "Re-run the task gates before resubmitting so the fleet can judge the attempt.",
        ]
    instructions: list[str] = []
    for row in failures:
        detail = row["detail"] or "no detail was recorded by the gate"
        instructions.append(f"Fix gate {row['gate_id']} (status {row['status']}): {detail}")
        instructions.append(f"Re-run gate {row['gate_id']} locally and confirm it passes.")
    if escalate:
        instructions.append(
            f"Attempt budget exhausted ({attempt}/{max_attempts}); "
            "stop retrying and escalate this task to the fleet operator."
        )
    return instructions


def _reason(
    failures: list[dict[str, str]], attempt: int, max_attempts: int, escalate: bool
) -> str:
    if not failures:
        return f"attempt {attempt}/{max_attempts} recorded no failing gate"
    names = ", ".join(row["gate_id"] for row in failures)
    if escalate:
        return (
            f"attempt {attempt}/{max_attempts} exhausted with {len(failures)} failing "
            f"gate(s): {names}"
        )
    return f"attempt {attempt}/{max_attempts} failed {len(failures)} gate(s): {names}"


def build_repair_packet(
    task: dict[str, Any],
    evidence: list[dict[str, Any]],
    attempt: int,
    max_attempts: int = 3,
) -> dict[str, Any]:
    """Build the next attempt's brief from the previous attempt's evidence."""

    failures = failing_gates(list(evidence))
    escalate = attempt >= max_attempts
    return {
        "task_id": str(task.get("id", "") or ""),
        "attempt": attempt,
        "max_attempts": max_attempts,
        "escalate": escalate,
        "reason": _reason(failures, attempt, max_attempts, escalate),
        "failing_gates": failures,
        "instructions": _instructions(failures, attempt, max_attempts, escalate),
        "scope": list(task.get("owns", []) or []),
        "carry_forward": list(task.get("submitted_files", []) or []),
    }


def render_repair_markdown(packet: dict[str, Any]) -> str:
    """A human-readable form of the same packet, for the operator's log."""

    lines = [
        f"# Repair packet {packet['task_id']} (attempt {packet['attempt']}/{packet['max_attempts']})",
        "",
        packet["reason"],
        "",
        "## Failing gates",
        "",
    ]
    lines += [
        f"- **{row['gate_id']}** ({row['status']}): {row['detail'] or '(no detail)'}"
        for row in packet["failing_gates"]
    ] or ["- none recorded"]
    lines += ["", "## Instructions", ""]
    lines += [f"{index}. {text}" for index, text in enumerate(packet["instructions"], start=1)]
    lines += ["", "## Write scope", ""]
    lines += [f"- `{path}`" for path in packet["scope"]] or ["- none"]
    if packet["carry_forward"]:
        lines += ["", "## Carry forward (already submitted)", ""]
        lines += [f"- `{path}`" for path in packet["carry_forward"]]
    if packet["escalate"]:
        lines += ["", "**Escalation required: the attempt budget is exhausted.**"]
    return "\n".join(lines) + "\n"
