"""Intent compilation: human language -> machine-checkable specification.

MiniFleet refuses to start work on an intent it cannot falsify. Compilation
therefore ends with a validation pass that requires:

  * every acceptance criterion to name at least one gate,
  * every named gate to exist,
  * every budget to be a number with a threshold gate.

An intent that fails validation is a planning problem, not a coding problem.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Any

from .model import Gate, IntentSpec, scopes_overlap


class IntentError(ValueError):
    """Raised when an intent cannot be compiled into a checkable spec."""


def load(path: Path | str) -> IntentSpec:
    """Load an intent from JSON, TOML or (if available) YAML."""

    path = Path(path)
    suffix = path.suffix.lower()
    text = path.read_text()
    if suffix == ".json":
        data = json.loads(text)
    elif suffix == ".toml":
        data = tomllib.loads(text)
    elif suffix in {".yaml", ".yml"}:
        try:
            import yaml  # type: ignore
        except ModuleNotFoundError as exc:  # pragma: no cover - optional path
            raise IntentError(
                "YAML intents need PyYAML; use .json or .toml to stay dependency-free"
            ) from exc
        data = yaml.safe_load(text)
    else:
        raise IntentError(f"unsupported intent format: {path.suffix!r}")
    if not isinstance(data, dict):
        raise IntentError(f"{path}: root of the intent must be an object")
    return compile_dict(data, source=str(path), raw=text)


def compile_dict(data: dict[str, Any], source: str = "<inline>", raw: str = "") -> IntentSpec:
    spec = IntentSpec.from_dict(data)
    validate(spec, source=source)
    return spec


def validate(spec: IntentSpec, source: str = "<inline>") -> None:
    """Fail fast on intents that no amount of engineering could verify."""

    problems: list[str] = []

    if not spec.id:
        problems.append("intent.id is required")
    if not spec.deliverables:
        problems.append("intent.deliverables must not be empty")
    if not spec.acceptance:
        problems.append("intent.acceptance must not be empty; an unfalsifiable intent is not actionable")
    if not spec.components:
        problems.append("intent.components must not be empty; the architect needs a decomposition")

    gate_ids = {g.id for g in spec.gate_defs}
    if len(gate_ids) != len(spec.gate_defs):
        problems.append("duplicate gate ids in intent.gates")

    seen_criteria: set[str] = set()
    for criterion in spec.acceptance:
        if criterion.id in seen_criteria:
            problems.append(f"duplicate acceptance id: {criterion.id}")
        seen_criteria.add(criterion.id)
        for gate_id in criterion.gates:
            if gate_id not in gate_ids:
                problems.append(f"acceptance {criterion.id} references unknown gate {gate_id!r}")
        # A criterion without a gate is not fatal here: the architect synthesises
        # a probe gate and records the repair. Unknown gate references are fatal.

    for budget, value in spec.budgets.items():
        if not isinstance(value, (int, float)):
            problems.append(f"budget {budget!r} must be numeric")
    allowed_limits = {"max_wall_seconds", "max_attempts", "max_dispatches", "max_rounds"}
    for name, value in spec.limits.items():
        if name not in allowed_limits:
            problems.append(f"unknown limit {name!r}; supported: {sorted(allowed_limits)}")
        elif not isinstance(value, (int, float)) or value <= 0:
            problems.append(f"limit {name!r} must be a positive number")
    budget_names = set(spec.budgets)
    for gate in spec.gate_defs:
        if gate.kind == "budget":
            if gate.metric not in budget_names:
                problems.append(
                    f"budget gate {gate.id} measures {gate.metric!r} which is not declared in intent.budgets"
                )
            elif spec.budgets[gate.metric] != gate.threshold:
                problems.append(
                    f"budget gate {gate.id} threshold {gate.threshold} disagrees with "
                    f"intent.budgets[{gate.metric!r}]={spec.budgets[gate.metric]}"
                )
        if gate.kind == "cmd" and not gate.cmd:
            problems.append(f"gate {gate.id} is a cmd gate without a command")
        if gate.kind == "files" and not gate.paths:
            problems.append(f"gate {gate.id} is a files gate without paths")
        if gate.kind == "json" and not gate.json_path:
            problems.append(f"gate {gate.id} is a json gate without json_path")

    component_ids = {c.id for c in spec.components}
    if len(component_ids) != len(spec.components):
        problems.append("duplicate component ids in intent.components")
    for component in spec.components:
        for dep in component.depends_on:
            if dep not in component_ids:
                problems.append(f"component {component.id} depends on unknown component {dep!r}")
        for criterion_id in component.acceptance_ids:
            if criterion_id not in seen_criteria:
                problems.append(
                    f"component {component.id} claims unknown acceptance criterion {criterion_id!r}"
                )
        for gate_id in component.gate_ids:
            if gate_id not in gate_ids:
                problems.append(f"component {component.id} references unknown gate {gate_id!r}")

    owns_seen: dict[str, str] = {}
    for component in spec.components:
        for path in component.owns:
            for seen_path, owner in owns_seen.items():
                if owner != component.id and scopes_overlap(path, seen_path):
                    problems.append(
                        f"write scope {path!r} overlaps {seen_path!r} claimed by {owner}; "
                        "overlapping ownership is the main source of merge conflict in a fleet"
                    )
            owns_seen[path] = component.id

    if problems:
        raise IntentError(
            f"intent {source} failed validation:\n  - " + "\n  - ".join(problems)
        )


def merge_budget_gates(spec: IntentSpec) -> IntentSpec:
    """Helper used by tests: report which budgets lack an enforcing gate."""

    enforced = {g.metric for g in spec.gate_defs if g.kind == "budget"}
    missing = [name for name in spec.budgets if name not in enforced]
    if missing:
        raise IntentError(f"budgets without an enforcing gate: {missing}")
    return spec


Gate  # re-exported for type checkers reading this module
