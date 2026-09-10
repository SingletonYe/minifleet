"""Domain model for MiniFleet.

The model is deliberately explicit: intent -> design -> task graph -> gates ->
evidence. Anything the fleet claims at the end of a run must be expressible as a
chain of these objects, which is what makes the final report auditable.
"""

from __future__ import annotations

import dataclasses
import json
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Iterable


class TaskState(str, Enum):
    """Lifecycle of one unit of fleet work."""

    PENDING = "pending"          # dependencies not satisfied yet
    READY = "ready"              # can be dispatched now
    DISPATCHED = "dispatched"    # handed to a worker runtime
    SUBMITTED = "submitted"      # worker reports it is done, awaiting task gates
    VERIFIED = "verified"        # task-level gates passed
    FAILED = "failed"            # task gates failed or worker errored
    MERGED = "merged"            # merged into the integration branch


class RunStatus(str, Enum):
    PLANNING = "planning"
    RUNNING = "running"
    INTEGRATING = "integrating"
    VERIFYING = "verifying"
    PASSED = "passed"
    FAILED = "failed"
    EVOLVING = "evolving"


class GateKind(str, Enum):
    CMD = "cmd"          # run a shell command in the integration workspace
    FILES = "files"      # assert that paths exist / are absent
    JSON = "json"        # assert dotted paths inside a JSON artifact
    BUDGET = "budget"    # enforce a non-functional budget on a captured metric


@dataclass
class AcceptanceCriterion:
    """One falsifiable statement of "done" taken from the intent."""

    id: str
    statement: str
    kind: str = "functional"          # functional | nfr | safety | operability
    gates: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AcceptanceCriterion":
        return cls(
            id=data["id"],
            statement=data["statement"],
            kind=data.get("kind", "functional"),
            gates=list(data.get("gates", [])),
        )


@dataclass
class IntentSpec:
    """A compiled, machine-checkable version of a human intent."""

    id: str
    title: str
    summary: str = ""
    stakeholders: list[str] = field(default_factory=list)
    deliverables: list[str] = field(default_factory=list)
    acceptance: list[AcceptanceCriterion] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    budgets: dict[str, float] = field(default_factory=dict)
    out_of_scope: list[str] = field(default_factory=list)
    components: list["Component"] = field(default_factory=list)
    gate_defs: list["Gate"] = field(default_factory=list)
    contracts: list["Contract"] = field(default_factory=list)
    harness_dir: str = ""
    baseline_from: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "IntentSpec":
        return cls(
            id=data["id"],
            title=data["title"],
            summary=data.get("summary", ""),
            stakeholders=list(data.get("stakeholders", [])),
            deliverables=list(data.get("deliverables", [])),
            acceptance=[AcceptanceCriterion.from_dict(c) for c in data.get("acceptance", [])],
            constraints=list(data.get("constraints", [])),
            budgets=dict(data.get("budgets", {})),
            out_of_scope=list(data.get("out_of_scope", [])),
            components=[Component.from_dict(c) for c in data.get("components", [])],
            gate_defs=[Gate.from_dict(g) for g in data.get("gates", [])],
            contracts=[Contract.from_dict(c) for c in data.get("contracts", [])],
            harness_dir=data.get("harness_dir", ""),
            baseline_from=data.get("baseline_from", ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return _clean(dataclasses.asdict(self))


@dataclass
class Contract:
    """A frozen interface the architect owns; workers may not renegotiate it."""

    id: str
    path: str
    summary: str = ""
    exports: dict[str, str] = field(default_factory=dict)
    frozen: bool = True
    content: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Contract":
        return cls(
            id=data["id"],
            path=data["path"],
            summary=data.get("summary", ""),
            exports=dict(data.get("exports", {})),
            frozen=bool(data.get("frozen", True)),
            content=data.get("content", ""),
        )


@dataclass
class Component:
    """A coherent part of the target system, mapped to one owning task."""

    id: str
    name: str
    responsibility: str
    owns: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)
    contract_ids: list[str] = field(default_factory=list)
    acceptance_ids: list[str] = field(default_factory=list)
    tests: list[str] = field(default_factory=list)
    done_when: list[str] = field(default_factory=list)
    gate_ids: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Component":
        return cls(
            id=data["id"],
            name=data["name"],
            responsibility=data.get("responsibility", ""),
            owns=list(data.get("owns", [])),
            depends_on=list(data.get("depends_on", [])),
            contract_ids=list(data.get("contract_ids", [])),
            acceptance_ids=list(data.get("acceptance_ids", [])),
            tests=list(data.get("tests", [])),
            done_when=list(data.get("done_when", [])),
            gate_ids=list(data.get("gate_ids", [])),
        )


@dataclass
class Gate:
    """One machine-checkable verification step."""

    id: str
    title: str
    kind: str = GateKind.CMD.value
    cmd: str = ""
    cwd: str = "."
    timeout: float = 300.0
    required: bool = True
    scope: str = "system"                       # system | task
    expect_exit: list[int] = field(default_factory=lambda: [0])
    metric: str = ""                            # budget gates
    op: str = "<="
    threshold: float = 0.0
    paths: list[str] = field(default_factory=list)
    json_path: str = ""
    json_expected: Any = None
    description: str = ""
    # Which components a failure of this gate implicates. System gates run on the
    # integrated tree, where "who broke it" is not obvious; attribution is what
    # turns a red system gate into a repair packet instead of a human enquiry.
    attributed_to: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Gate":
        known = {f.name for f in dataclasses.fields(cls)}
        unknown = set(data) - known
        if unknown:
            raise ValueError(f"gate {data.get('id')!r} has unknown keys: {sorted(unknown)}")
        payload = dict(data)
        payload.setdefault("title", payload.get("id", "gate"))
        return cls(**payload)


@dataclass
class Task:
    """A unit of work handed to exactly one worker, with a write scope."""

    id: str
    title: str
    role: str
    goal: str
    owns: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)
    contract_ids: list[str] = field(default_factory=list)
    acceptance_ids: list[str] = field(default_factory=list)
    done_when: list[str] = field(default_factory=list)
    tests: list[str] = field(default_factory=list)
    gate_ids: list[str] = field(default_factory=list)
    component_id: str = ""
    state: str = TaskState.PENDING.value
    attempts: int = 0
    branch: str = ""
    worktree: str = ""
    submitted_files: list[str] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    notes: str = ""
    started_at: float | None = None
    finished_at: float | None = None

    @property
    def active(self) -> bool:
        return self.state in {TaskState.READY.value, TaskState.PENDING.value}

    def to_dict(self) -> dict[str, Any]:
        return _clean(dataclasses.asdict(self))


@dataclass
class Design:
    """The architect's output: contracts, tasks, gates and known risks."""

    intent_id: str
    components: list[Component] = field(default_factory=list)
    contracts: list[Contract] = field(default_factory=list)
    tasks: list[Task] = field(default_factory=list)
    gates: list[Gate] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    decisions: list[str] = field(default_factory=list)
    repairs: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Design":
        return cls(
            intent_id=data["intent_id"],
            components=[Component.from_dict(c) for c in data.get("components", [])],
            contracts=[Contract.from_dict(c) for c in data.get("contracts", [])],
            tasks=[Task(**{k: v for k, v in t.items() if k in {f.name for f in dataclasses.fields(Task)}})
                   for t in data.get("tasks", [])],
            gates=[Gate.from_dict(g) for g in data.get("gates", [])],
            risks=list(data.get("risks", [])),
            decisions=list(data.get("decisions", [])),
            repairs=list(data.get("repairs", [])),
        )

    def to_dict(self) -> dict[str, Any]:
        return _clean(dataclasses.asdict(self))

    def task(self, task_id: str) -> Task:
        for task in self.tasks:
            if task.id == task_id:
                return task
        raise KeyError(task_id)


@dataclass
class Evidence:
    """The only currency of trust in MiniFleet."""

    gate_id: str
    status: str                       # pass | fail | error | skip
    detail: str = ""
    exit_code: int | None = None
    duration_s: float = 0.0
    metrics: dict[str, float] = field(default_factory=dict)
    artifacts: list[str] = field(default_factory=list)
    output_tail: str = ""
    task_id: str = ""
    recorded_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return _clean(dataclasses.asdict(self))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Evidence":
        return cls(**{k: v for k, v in data.items() if k in {f.name for f in dataclasses.fields(cls)}})


@dataclass
class Run:
    """Everything the fleet knows about one execution."""

    id: str
    intent_id: str
    status: str = RunStatus.PLANNING.value
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    repo_dir: str = ""
    integration_branch: str = "integration"
    dispatcher: str = "packet"
    intent: dict[str, Any] = field(default_factory=dict)
    design: dict[str, Any] = field(default_factory=dict)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    raw_intent: str = ""
    verdict: str = ""
    attributed_verdict: str = ""

    # -- persistence -----------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return _clean(dataclasses.asdict(self))

    @classmethod
    def load(cls, run_dir: Path | str) -> "Run":
        path = Path(run_dir) / "run.json"
        data = json.loads(path.read_text())
        known = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})

    def save(self, run_dir: Path | str) -> None:
        self.updated_at = time.time()
        path = Path(run_dir) / "run.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.to_dict(), indent=2, sort_keys=True))
        tmp.replace(path)

    @property
    def design_obj(self) -> Design:
        return Design.from_dict(self.design)

    def set_design(self, design: Design) -> None:
        self.design = design.to_dict()

    def log(self, event: str, **fields: Any) -> dict[str, Any]:
        entry = {"t": time.time(), "event": event, **fields}
        self.events.append(entry)
        return entry


def _clean(value: Any) -> Any:
    """Recursively convert enums/tuples into JSON-friendly values."""

    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_clean(v) for v in value]
    return value


def scopes_overlap(left: str, right: str) -> bool:
    """True when two write scopes could touch the same file.

    Scopes are either directory prefixes (``calc/``), globs (``calc/*.py``) or
    exact paths. Overlap is checked in both directions because a glob on either
    side can swallow the other.
    """

    import fnmatch

    a, b = left.rstrip("/"), right.rstrip("/")
    if a == b:
        return True
    if a.startswith(b + "/") or b.startswith(a + "/"):
        return True
    return fnmatch.fnmatch(a, right) or fnmatch.fnmatch(b, left)


def iter_task_ids(tasks: Iterable[Task]) -> list[str]:
    return [t.id for t in tasks]
