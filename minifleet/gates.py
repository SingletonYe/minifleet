"""Verification gates: the only mechanism through which work is admitted.

A gate is a small, declarative, reproducible check. Four kinds are supported:

``cmd``     run a command in the integration workspace; exit code decides
``files``   assert that paths exist (or, with a ``!`` prefix, do not exist)
``json``    assert a dotted path inside a JSON artifact equals a value
``budget``  enforce a non-functional budget on a metric captured earlier

Commands may publish measurements by printing one JSON object per line prefixed
with ``MINIFLEET_METRIC``. Metrics are accumulated per run, which is what lets a
budget gate later fail the run when a performance target was missed. A missing
metric is deliberately an *error*, not a pass: unmeasured is unverified.
"""

from __future__ import annotations

import fnmatch
import json
import subprocess
import time
from pathlib import Path
from typing import Any, Iterable

from .model import Evidence, Gate

METRIC_PREFIX = "MINIFLEET_METRIC"
OUTPUT_TAIL_CHARS = 4000
DIAGNOSIS_CHARS = 260


def diagnose(output: str, limit: int = DIAGNOSIS_CHARS) -> str:
    """The part of a failing command's output a worker can act on.

    A gate that only says ``exit=1`` turns every repair into a scavenger hunt
    through the ledger. The failing gate's own output is the most useful thing the
    fleet can hand to the next attempt, so it is lifted into the evidence detail -
    and from there into the repair packet and the report.
    """

    markers = ("AssertionError", "Error", "error:", "FAILED", "Traceback", "not ok")
    interesting: list[str] = []
    for line in output.splitlines():
        stripped = line.strip()
        if stripped and any(marker in stripped for marker in markers):
            interesting.append(stripped)
    if not interesting:
        lines = [line.strip() for line in output.splitlines() if line.strip()]
        interesting = lines[-2:]
    seen: list[str] = []
    for line in interesting:
        if line not in seen:
            seen.append(line)
    return " | ".join(seen)[:limit]


class MetricStore:
    """Metrics published by gates during a single run."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.values: dict[str, float] = {}
        if self.path.exists():
            try:
                self.values = dict(json.loads(self.path.read_text()))
            except json.JSONDecodeError:
                self.values = {}

    def update(self, metrics: dict[str, float]) -> None:
        self.values.update(metrics)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.values, indent=2, sort_keys=True))

    def get(self, name: str) -> float | None:
        return self.values.get(name)


def parse_metrics(output: str) -> dict[str, float]:
    metrics: dict[str, float] = {}
    for line in output.splitlines():
        line = line.strip()
        if not line.startswith(METRIC_PREFIX):
            continue
        payload = line[len(METRIC_PREFIX):].strip()
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and isinstance(data.get("name"), str):
            try:
                metrics[data["name"]] = float(data["value"])
            except (KeyError, TypeError, ValueError):
                continue
    return metrics


def _compare(value: float, op: str, threshold: float) -> bool:
    return {
        "<=": value <= threshold,
        "<": value < threshold,
        ">=": value >= threshold,
        ">": value > threshold,
        "==": value == threshold,
    }.get(op, False)


class GateRunner:
    """Executes gates and turns them into evidence."""

    def __init__(
        self,
        workspace: Path | str,
        metrics: MetricStore,
        artifacts_dir: Path | str,
        env: dict[str, str] | None = None,
    ) -> None:
        self.workspace = Path(workspace)
        self.metrics = metrics
        self.artifacts_dir = Path(artifacts_dir)
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        self.env = env or {}

    def run(self, gate: Gate, task_id: str = "") -> Evidence:
        started = time.time()
        try:
            handler = {
                "cmd": self._cmd,
                "files": self._files,
                "json": self._json,
                "budget": self._budget,
            }.get(gate.kind)
            if handler is None:
                return Evidence(
                    gate_id=gate.id,
                    status="error",
                    detail=f"unknown gate kind {gate.kind!r}",
                    task_id=task_id,
                    duration_s=time.time() - started,
                )
            return handler(gate, task_id, started)
        except Exception as exc:  # noqa: BLE001 - evidence must never crash a run
            return Evidence(
                gate_id=gate.id,
                status="error",
                detail=f"{type(exc).__name__}: {exc}",
                task_id=task_id,
                duration_s=time.time() - started,
            )

    # -- gate kinds ------------------------------------------------------
    def _cmd(self, gate: Gate, task_id: str, started: float) -> Evidence:
        import os

        env = {**os.environ, **self.env}
        cwd = (self.workspace / gate.cwd).resolve()
        proc = subprocess.run(
            gate.cmd,
            shell=True,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=gate.timeout,
            env=env,
        )
        output = proc.stdout + ("\n" + proc.stderr if proc.stderr else "")
        metrics = parse_metrics(output)
        if metrics:
            self.metrics.update(metrics)
        status = "pass" if proc.returncode in gate.expect_exit else "fail"
        detail = f"exit={proc.returncode} (expected {gate.expect_exit})"
        if status == "fail":
            diagnosis = diagnose(output)
            if diagnosis:
                detail = f"{detail}: {diagnosis}"
        evidence = Evidence(
            gate_id=gate.id,
            status=status,
            detail=detail,
            exit_code=proc.returncode,
            duration_s=time.time() - started,
            metrics=metrics,
            output_tail=output[-OUTPUT_TAIL_CHARS:],
            task_id=task_id,
        )
        self._persist(evidence, gate)
        return evidence

    def _files(self, gate: Gate, task_id: str, started: float) -> Evidence:
        missing: list[str] = []
        unexpected: list[str] = []
        for pattern in gate.paths:
            must_be_absent = pattern.startswith("!")
            clean = pattern.lstrip("!")
            found = sorted(p for p in self.workspace.glob(clean))
            if must_be_absent and found:
                unexpected.extend(str(p.relative_to(self.workspace)) for p in found)
            elif not must_be_absent and not found:
                missing.append(clean)
        ok = not missing and not unexpected
        detail_parts = []
        if missing:
            detail_parts.append(f"missing: {missing}")
        if unexpected:
            detail_parts.append(f"present but forbidden: {unexpected}")
        return Evidence(
            gate_id=gate.id,
            status="pass" if ok else "fail",
            detail="; ".join(detail_parts) or f"all paths satisfied: {gate.paths}",
            duration_s=time.time() - started,
            task_id=task_id,
        )

    def _json(self, gate: Gate, task_id: str, started: float) -> Evidence:
        target = self.workspace / (gate.paths[0] if gate.paths else gate.json_path)
        if not target.exists():
            return Evidence(
                gate_id=gate.id,
                status="fail",
                detail=f"json artifact {target} does not exist",
                duration_s=time.time() - started,
                task_id=task_id,
            )
        data: Any = json.loads(target.read_text())
        for part in gate.json_path.split("."):
            if isinstance(data, dict) and part in data:
                data = data[part]
            elif isinstance(data, list) and part.isdigit() and int(part) < len(data):
                data = data[int(part)]
            else:
                return Evidence(
                    gate_id=gate.id,
                    status="fail",
                    detail=f"{gate.json_path} not found in {target.name}",
                    duration_s=time.time() - started,
                    task_id=task_id,
                )
        ok = data == gate.json_expected
        return Evidence(
            gate_id=gate.id,
            status="pass" if ok else "fail",
            detail=f"{gate.json_path}={data!r} expected {gate.json_expected!r}",
            duration_s=time.time() - started,
            task_id=task_id,
        )

    def _budget(self, gate: Gate, task_id: str, started: float) -> Evidence:
        value = self.metrics.get(gate.metric)
        if value is None:
            return Evidence(
                gate_id=gate.id,
                status="error",
                detail=(
                    f"metric {gate.metric!r} was never produced by any gate; "
                    "an unmeasured non-functional claim cannot be accepted"
                ),
                duration_s=time.time() - started,
                task_id=task_id,
            )
        ok = _compare(value, gate.op, gate.threshold)
        return Evidence(
            gate_id=gate.id,
            status="pass" if ok else "fail",
            detail=f"{gate.metric}={value:g} {gate.op} {gate.threshold:g} -> {'ok' if ok else 'missed'}",
            duration_s=time.time() - started,
            metrics={gate.metric: value},
            task_id=task_id,
        )

    def _persist(self, evidence: Evidence, gate: Gate) -> None:
        record = {"gate": gate.id, "title": gate.title, "cmd": gate.cmd, "evidence": evidence.to_dict()}
        path = self.artifacts_dir / f"{gate.id}.json"
        path.write_text(json.dumps(record, indent=2, sort_keys=True))
        evidence.artifacts.append(str(path))


def verdict(evidence: Iterable[Evidence], required: set[str]) -> str:
    """pass only if every required gate produced passing evidence."""

    by_gate = {e.gate_id: e for e in evidence}
    missing = required - set(by_gate)
    if missing:
        return f"fail: no evidence for required gates {sorted(missing)}"
    bad = [
        f"{e.gate_id}={e.status}"
        for gate_id, e in sorted(by_gate.items())
        if gate_id in required and e.status != "pass"
    ]
    if bad:
        return "fail: " + ", ".join(bad)
    return "pass: all required gates green"


def glob_any(patterns: Iterable[str], names: Iterable[str]) -> bool:
    return any(fnmatch.fnmatch(name, pattern) for pattern in patterns for name in names)
