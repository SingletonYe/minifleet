"""The architect: intent -> design -> task graph.

Two things make this more than templating:

1. **Design self-check.** Before any worker is dispatched, the design is
   validated: the dependency graph must be acyclic, write scopes must be
   disjoint, and every acceptance criterion must be reachable from a gate.
2. **Design self-repair.** Violations that can be repaired mechanically
   (missing smoke gate for an uncovered criterion, a component that claims no
   files, a dependency cycle caused by a missing leaf) are repaired and the
   repair is recorded in ``design.repairs`` so the human can audit what changed.
"""

from __future__ import annotations

import dataclasses
from typing import Any, Callable, Iterable

from .model import (
    Contract,
    Design,
    Gate,
    GateKind,
    IntentSpec,
    Task,
    TaskState,
)


class DesignError(ValueError):
    """Raised when the design cannot be repaired into a dispatchable plan."""


ContractFactory = Callable[[IntentSpec], list[Contract]]


def design(spec: IntentSpec, contract_factory: ContractFactory | None = None) -> Design:
    """Produce a validated, dispatchable design for ``spec``."""

    contracts = (
        list(contract_factory(spec))
        if contract_factory
        else (list(spec.contracts) or default_contracts(spec))
    )
    tasks = [_task_for(component, spec) for component in spec.components]
    gates = [dataclasses.replace(g) for g in spec.gate_defs]

    plan = Design(
        intent_id=spec.id,
        components=list(spec.components),
        contracts=contracts,
        tasks=tasks,
        gates=gates,
        risks=_risks(spec),
        decisions=[
            "acceptance criteria are owned by the verification layer, never by the worker that writes the code",
            "every task has a single writer and a disjoint write scope; concurrency without ownership is a merge queue",
            f"frozen contracts: {', '.join(c.id for c in contracts) or 'none'}",
        ],
    )
    repair(plan, spec)
    check(plan, spec)
    return plan


def _gate_dict(raw: dict[str, Any]) -> dict[str, Any]:
    payload = dict(raw)
    payload.setdefault("kind", GateKind.CMD.value)
    return payload


def _task_for(component: Any, spec: IntentSpec) -> Task:
    return Task(
        id=f"T-{component.id}",
        title=component.name,
        role="builder",
        goal=component.responsibility,
        owns=list(component.owns),
        depends_on=[f"T-{dep}" for dep in component.depends_on],
        contract_ids=list(component.contract_ids),
        acceptance_ids=list(component.acceptance_ids),
        done_when=list(component.done_when),
        tests=list(component.tests),
        gate_ids=list(component.gate_ids),
        component_id=component.id,
        branch=f"task/{component.id}",
        state=TaskState.PENDING.value,
    )


def default_contracts(spec: IntentSpec) -> list[Contract]:
    """A minimal contract for intents that do not ship an explicit one."""

    return [
        Contract(
            id="C-README",
            path="README.md",
            summary="frozen statement of what the system promises and how it is verified",
            exports={},
        )
    ]


def repair(plan: Design, spec: IntentSpec) -> None:
    """Mechanically fix the removable classes of design defect."""

    # 1. A criterion with no gate can never be proven: synthesise a probe gate
    #    that at least forces the fleet to produce the artifact under test.
    proven = {gid for c in spec.acceptance for gid in c.gates}
    for criterion in spec.acceptance:
        if not criterion.gates:
            gate_id = f"G-probe-{criterion.id}"
            plan.gates.append(
                Gate(
                    id=gate_id,
                    title=f"probe: {criterion.statement[:60]}",
                    kind=GateKind.FILES.value,
                    paths=["ACCEPTANCE.md"],
                    description="synthesised because the intent declared no gate for this criterion",
                )
            )
            criterion.gates.append(gate_id)
            plan.repairs.append(
                f"criterion {criterion.id} had no gate; added probe gate {gate_id}"
            )
    del proven

    # 2. A component with no write scope still has to produce something
    #    checkable; give it a design note it owns.
    for task in plan.tasks:
        if not task.owns:
            task.owns = [f"docs/{task.component_id or task.id}.md"]
            task.state = TaskState.READY.value
            plan.repairs.append(
                f"task {task.id} claimed no files; assigned write scope {task.owns[0]}"
            )

    # 3. Break dependency cycles by promoting the lowest-id members to leaves.
    cycles = find_cycles(plan.tasks)
    for cycle in cycles:
        victim = sorted(cycle)[0]
        task = plan.task(victim)
        dropped = sorted(set(task.depends_on) & set(cycle))
        task.depends_on = [d for d in task.depends_on if d not in dropped]
        plan.repairs.append(
            f"dependency cycle {sorted(cycle)} broken by dropping {victim} -> {dropped}"
        )

    # 4. Anything a task depends on must exist.
    known = {t.id for t in plan.tasks}
    for task in plan.tasks:
        missing = [d for d in task.depends_on if d not in known]
        if missing:
            task.depends_on = [d for d in task.depends_on if d in known]
            plan.repairs.append(f"task {task.id} referenced unknown dependencies {missing}; dropped")


def check(plan: Design, spec: IntentSpec) -> None:
    """Refuse to dispatch a design that cannot be executed or audited."""

    problems: list[str] = []

    cycles = find_cycles(plan.tasks)
    if cycles:
        problems.append(f"dependency cycles remain: {cycles}")

    owners: dict[str, str] = {}
    for task in plan.tasks:
        for path in task.owns:
            if path in owners and owners[path] != task.id:
                problems.append(f"write scope {path!r} owned by both {owners[path]} and {task.id}")
            owners[path] = task.id

    gate_ids = {g.id for g in plan.gates}
    for task in plan.tasks:
        for gate_id in task.gate_ids:
            if gate_id not in gate_ids:
                problems.append(f"task {task.id} references unknown gate {gate_id!r}")
    for criterion in spec.acceptance:
        if not set(criterion.gates) <= gate_ids:
            problems.append(f"criterion {criterion.id} is not fully covered by gates")

    contract_ids = {c.id for c in plan.contracts}
    for task in plan.tasks:
        missing = set(task.contract_ids) - contract_ids
        if missing:
            problems.append(f"task {task.id} references unknown contracts {sorted(missing)}")

    if not plan.gates:
        problems.append("design declares no gates; nothing would be verified")

    if problems:
        raise DesignError("design review failed:\n  - " + "\n  - ".join(problems))


def find_cycles(tasks: Iterable[Task]) -> list[list[str]]:
    graph = {t.id: list(t.depends_on) for t in tasks}
    cycles: list[list[str]] = []
    colour: dict[str, int] = {}
    stack: list[str] = []

    def visit(node: str) -> None:
        colour[node] = 1
        stack.append(node)
        for nxt in graph.get(node, []):
            if colour.get(nxt, 0) == 1:
                start = stack.index(nxt)
                cycles.append(stack[start:])
            elif colour.get(nxt, 0) == 0:
                visit(nxt)
        stack.pop()
        colour[node] = 2

    for node in graph:
        if colour.get(node, 0) == 0:
            visit(node)
    return cycles


def topological_batches(tasks: Iterable[Task]) -> list[list[Task]]:
    """Group tasks into waves that can run concurrently."""

    remaining = {t.id: t for t in tasks}
    done: set[str] = set()
    batches: list[list[Task]] = []
    while remaining:
        wave = [t for t in remaining.values() if set(t.depends_on) <= done]
        if not wave:
            raise DesignError(
                "no dispatchable task in the remaining set; dependency graph is not a DAG: "
                f"{sorted(remaining)}"
            )
        batches.append(sorted(wave, key=lambda t: t.id))
        done.update(t.id for t in wave)
        for task in wave:
            remaining.pop(task.id)
    return batches


def _risks(spec: IntentSpec) -> list[str]:
    risks: list[str] = []
    if len(spec.components) > 3:
        risks.append("wide task graph: integration cost grows with the number of writers")
    if any(c.kind == "nfr" for c in spec.acceptance):
        risks.append("non-functional criteria are only as trustworthy as the measurement harness")
    if not spec.out_of_scope:
        risks.append("no explicit out-of-scope list: scope creep is unbounded")
    return risks
