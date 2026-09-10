"""The scheduler: waves of parallel work, then integration, then verification.

Ordering rules that MiniFleet enforces:

* a task becomes READY only when every dependency is MERGED,
* task-scope gates run inside the worker's own worktree, so a broken module
  never reaches the integration branch,
* system-scope gates run only on the integrated tree,
* merging is a separate, auditable step and refuses to run when two tasks
  touched the same path.
"""

from __future__ import annotations

import fnmatch
import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from . import architect, gates as gates_mod, ledger, repair as repair_mod, worktree as wt
from .model import Design, Evidence, Gate, IntentSpec, Run, RunStatus, Task, TaskState
from .workers import Dispatcher, Packet, build_packet


@dataclass
class Scheduler:
    run: Run
    run_dir: Path
    spec: IntentSpec
    design: Design

    # -- derived state ---------------------------------------------------
    @property
    def product_dir(self) -> Path:
        return Path(self.run.repo_dir)

    @property
    def repo(self) -> wt.Repo:
        return wt.Repo(self.product_dir)

    @property
    def metrics(self) -> gates_mod.MetricStore:
        return gates_mod.MetricStore(self.run_dir / "evidence" / "metrics.json")

    def task(self, task_id: str) -> Task:
        return self.design.task(task_id)

    def gate(self, gate_id: str) -> Gate:
        for gate in self.design.gates:
            if gate.id == gate_id:
                return gate
        raise KeyError(gate_id)

    def gate_or_none(self, gate_id: str) -> Gate | None:
        for gate in self.design.gates:
            if gate.id == gate_id:
                return gate
        return None

    def task_gates(self, task: Task) -> list[Gate]:
        return [g for g in self.design.gates if g.id in task.gate_ids]

    def system_gates(self) -> list[Gate]:
        return [g for g in self.design.gates if g.scope == "system"]

    # -- state machine ---------------------------------------------------
    def refresh(self) -> list[Task]:
        """Promote PENDING tasks whose dependencies are all merged."""

        merged = {t.id for t in self.design.tasks if t.state == TaskState.MERGED.value}
        promoted: list[Task] = []
        for task in self.design.tasks:
            if task.state != TaskState.PENDING.value:
                continue
            if set(task.depends_on) <= merged:
                task.state = TaskState.READY.value
                promoted.append(task)
        if promoted:
            ledger.append(self.run, self.run_dir, "tasks.ready", tasks=[t.id for t in promoted])
            self.persist()
        return promoted

    def ready(self) -> list[Task]:
        return [t for t in self.design.tasks if t.state == TaskState.READY.value]

    def persist(self) -> None:
        self.run.set_design(self.design)
        self.run.save(self.run_dir)

    # -- dispatch --------------------------------------------------------
    def packets(self, tasks: Iterable[Task] | None = None) -> list[tuple[Task, Packet, Path]]:
        if tasks is None:
            self.refresh()
            tasks = self.ready()
        tasks = list(tasks)
        base = self.repo.head()
        out: list[tuple[Task, Packet, Path]] = []
        for task in tasks:
            worktree_path = (self.run_dir / "worktrees" / task.component_id).resolve()
            # A reopened task must start from the current baseline: its old
            # branch is already merged, and the baseline may carry contracts and
            # harness files frozen after that merge. Reusing the merged branch
            # would silently hand the worker a stale tree.
            task.branch = self.repo.attempt_branch(task.component_id, task.branch, base)
            self.repo.add_worktree(worktree_path, task.branch, base)
            task.worktree = str(worktree_path)
            packet = build_packet(
                run_id=self.run.id,
                task=task,
                design=self.design,
                spec=self.spec,
                product_dir=self.product_dir,
                worktree=worktree_path,
                integration_branch=self.run.integration_branch,
                base_commit=base,
            )
            path = self.run_dir / "packets" / f"{task.id}.md"
            path.write_text(packet.to_markdown())
            path.with_suffix(".json").write_text(
                json.dumps(packet.to_dict(), indent=2, sort_keys=True)
            )
            out.append((task, packet, path))
        self.persist()
        return out

    def dispatch(
        self, dispatcher: Dispatcher, max_parallel: int = 4, limit: int | None = None
    ) -> list[dict[str, Any]]:
        """Dispatch the current ready wave; returns per-task results."""

        prepared = self.packets()
        if limit:
            prepared = prepared[:limit]
        if not prepared:
            return []

        for task, _, _ in prepared:
            task.state = TaskState.DISPATCHED.value
            task.started_at = task.started_at or time.time()
        self.persist()
        ledger.append(
            self.run, self.run_dir, "tasks.dispatched",
            dispatcher=dispatcher.name, tasks=[t.id for t, _, _ in prepared],
        )

        results: list[dict[str, Any]] = []

        def one(item: tuple[Task, Packet, Path]) -> dict[str, Any]:
            task, packet, path = item
            result = dispatcher.dispatch(packet, path)
            return result.to_dict()

        with ThreadPoolExecutor(max_workers=max(1, max_parallel)) as pool:
            for payload in pool.map(one, prepared):
                results.append(payload)
        (self.run_dir / "dispatch.json").write_text(
            json.dumps(results, indent=2, sort_keys=True)
        )
        return results

    # -- submission and task-level verification --------------------------
    def ingest(
        self, task_id: str, worker: str = "manual", notes: str = "", files: list[str] | None = None
    ) -> dict[str, Any]:
        """Accept a worker's output, commit it, and run that task's gates."""

        task = self.task(task_id)
        if not task.worktree:
            task.worktree = str((self.run_dir / "worktrees" / task.component_id).resolve())
        task.attempts += 1
        changed = files if files is not None else self.repo.changed_files(task.worktree, "HEAD")
        outside = [p for p in changed if not _within(p, task.owns)]
        if outside:
            task.state = TaskState.FAILED.value
            task.notes = f"worker wrote outside its scope: {outside}"
            self.persist()
            ledger.append(self.run, self.run_dir, "task.scope_violation", task=task_id, files=outside)
            return {"task_id": task_id, "status": "failed", "detail": task.notes}

        task.submitted_files = sorted(changed)
        sha = self.repo.commit_all(task.worktree, f"{task.id}: {task.title}")
        task.state = TaskState.SUBMITTED.value
        ledger.append(
            self.run, self.run_dir, "task.submitted",
            task=task_id, worker=worker, files=task.submitted_files, commit=sha, notes=notes,
        )

        runner = gates_mod.GateRunner(task.worktree, self.metrics, self.run_dir / "evidence")
        evidence = [runner.run(gate, task_id=task.id) for gate in self.task_gates(task)]
        task.evidence = [e.to_dict() for e in evidence]
        self.run.evidence.extend(e.to_dict() for e in evidence)
        passed = all(e.status == "pass" for e in evidence)
        task.state = TaskState.VERIFIED.value if passed else TaskState.FAILED.value
        task.finished_at = time.time()
        self.persist()
        ledger.append(
            self.run, self.run_dir, "task.verified" if passed else "task.failed",
            task=task_id,
            gates={e.gate_id: e.status for e in evidence},
        )
        if not passed:
            # A failure is only useful if it becomes the next attempt's brief.
            # The packet is written next to the task packet and recorded in the
            # ledger; dispatching it is still a separate, explicit decision.
            brief = self.write_repair_packet(task_id)
            ledger.append(
                self.run, self.run_dir, "task.repair_packet",
                task=task_id, attempt=task.attempts, brief=str(brief),
                escalate=bool(json.loads(brief.read_text())["escalate"]),
            )
        return {
            "task_id": task_id,
            "status": "verified" if passed else "failed",
            "files": task.submitted_files,
            "gates": {e.gate_id: e.status for e in evidence},
            "evidence": [e.to_dict() for e in evidence],
        }

    # -- repair ----------------------------------------------------------
    def write_repair_packet(self, task_id: str, max_attempts: int = 3) -> Path:
        """Record what the next attempt of a failed task must fix, and its budget."""

        task = self.task(task_id)
        packet = repair_mod.build_repair_packet(
            task.to_dict(), list(task.evidence), attempt=task.attempts, max_attempts=max_attempts
        )
        path = self.run_dir / "packets" / f"{task.id}.repair.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(packet, indent=2, sort_keys=True))
        return path

    def repair_failed(self, max_attempts: int = 3) -> dict[str, Any]:
        """Reopen every task that is currently failing, bounded by the attempt budget.

        Two failure shapes are covered: a task whose own gates failed, and a
        system gate that runs on the integrated tree and declares which component
        it implicates. The second is what makes a code-level defect repairable -
        without attribution a red system gate can only be reported, not fixed.
        """

        reopened: list[str] = []
        escalated: list[str] = []
        for task in self.design.tasks:
            if task.state != TaskState.FAILED.value:
                continue
            if task.worktree:
                self.repo.remove_worktree(task.worktree)
                task.worktree = ""
            brief = self.write_repair_packet(task.id, max_attempts=max_attempts)
            packet = json.loads(brief.read_text())
            if packet.get("escalate"):
                escalated.append(task.id)
                ledger.append(
                    self.run, self.run_dir, "task.escalated",
                    task=task.id, attempt=task.attempts, brief=str(brief),
                )
                continue
            task.state = TaskState.PENDING.value
            reopened.append(task.id)
            ledger.append(
                self.run, self.run_dir, "task.reopened",
                task=task.id, attempt=task.attempts, brief=str(brief),
            )

        attribution = {"reopened": [], "escalated": [], "unattributed": []}
        needs_attribution = bool(self.run.verdict) and not self.run.verdict.startswith("pass")
        if needs_attribution:
            latest: dict[str, dict[str, Any]] = {}
            for row in self.run.evidence:
                latest[str(row.get("gate_id"))] = row
            failing = [
                Evidence.from_dict(row) for row in latest.values() if row.get("status") != "pass"
            ]
            if failing:
                attribution = self.attribute_failures(failing, required=None, max_attempts=max_attempts)

        self.refresh()
        self.persist()
        return {
            "reopened": sorted(set(reopened) | set(attribution["reopened"])),
            "escalated": sorted(set(escalated) | set(attribution["escalated"])),
            "unattributed": attribution["unattributed"],
        }

    # -- integration -----------------------------------------------------
    def integrate(self) -> dict[str, Any]:
        """Merge verified tasks in dependency order onto the integration branch."""

        self.run.status = RunStatus.INTEGRATING.value
        repo = self.repo
        repo.ensure_branch(self.run.integration_branch)
        base = repo.head()

        branches: dict[str, list[str]] = {}
        for task in self.design.tasks:
            if task.state == TaskState.VERIFIED.value:
                branches[task.branch] = repo.changed_files(task.worktree, base)
        conflicts = wt.ownership_conflicts(branches)
        if conflicts:
            self.run.verdict = f"fail: ownership conflicts {conflicts}"
            self.persist()
            ledger.append(self.run, self.run_dir, "integrate.conflict", conflicts=conflicts)
            return {"status": "failed", "conflicts": conflicts}

        merged: list[str] = []
        try:
            repo.checkout(self.run.integration_branch)
            for wave in architect.topological_batches(self.design.tasks):
                for task in wave:
                    if task.state != TaskState.VERIFIED.value:
                        continue
                    repo.merge(task.branch, f"{task.id}: {task.title}")
                    task.state = TaskState.MERGED.value
                    merged.append(task.id)
                    ledger.append(self.run, self.run_dir, "task.merged", task=task.id, branch=task.branch)
        except Exception as exc:  # noqa: BLE001
            self.run.status = RunStatus.FAILED.value
            self.run.verdict = f"fail: integration error: {exc}"
            self.persist()
            ledger.append(self.run, self.run_dir, "integrate.error", error=str(exc))
            return {"status": "failed", "detail": str(exc), "merged": merged}

        self.refresh()
        self.persist()
        return {"status": "ok", "merged": merged, "head": repo.head()}

    # -- system verification ---------------------------------------------
    def verify(self, max_attempts: int = 3) -> dict[str, Any]:
        self.run.status = RunStatus.VERIFYING.value
        self.persist()
        runner = gates_mod.GateRunner(self.product_dir, self.metrics, self.run_dir / "evidence")
        # Measurement must precede judgement: budget gates are evaluated last so
        # that any metric produced by a probe gate is already in the store.
        system_gates = sorted(self.system_gates(), key=lambda g: 1 if g.kind == "budget" else 0)
        evidence = [runner.run(gate) for gate in system_gates]
        self.run.evidence.extend(e.to_dict() for e in evidence)
        required = {g.id for g in system_gates if g.required}
        result = gates_mod.verdict(evidence, required)
        self.run.verdict = result
        self.run.status = RunStatus.PASSED.value if result.startswith("pass") else RunStatus.FAILED.value
        attribution = (
            self.attribute_failures(evidence, required=required, max_attempts=max_attempts)
            if self.run.status == RunStatus.FAILED.value
            else {"reopened": [], "unattributed": [], "escalated": []}
        )
        self.persist()
        ledger.append(
            self.run, self.run_dir, "system.verified", verdict=result,
            gates={e.gate_id: e.status for e in evidence},
            reopened=attribution["reopened"],
            unattributed=attribution["unattributed"],
        )

        # Retire worker sandboxes only once the integrated tree is proven.
        if self.run.status == RunStatus.PASSED.value:
            for task in self.design.tasks:
                if task.worktree:
                    self.repo.remove_worktree(task.worktree)
            self.persist()
        return {
            "verdict": result,
            "evidence": [e.to_dict() for e in evidence],
            "attribution": attribution,
        }

    def task_for_component(self, component_id: str) -> Task | None:
        for task in self.design.tasks:
            if task.component_id == component_id:
                return task
        return None

    def attribute_failures(
        self,
        evidence: Iterable[Evidence],
        required: set[str] | None = None,
        max_attempts: int = 3,
    ) -> dict[str, Any]:
        """Turn red system gates into the next attempt of the task that owns them.

        A gate may declare ``attributed_to`` (component ids). Every failed required
        gate that carries attribution reopens its component's task on a fresh
        attempt branch, carrying the real gate output as the repair brief. A failed
        gate with no attribution is reported as unattributed: the fleet refuses to
        guess who owns it, and says so rather than retrying blindly.
        """

        reopened: list[str] = []
        escalated: list[str] = []
        unattributed: list[str] = []
        for item in evidence:
            if item.status == "pass":
                continue
            if required is not None and item.gate_id not in required:
                continue
            gate = self.gate_or_none(item.gate_id)
            if gate is None:
                # Evidence for a gate the design does not declare cannot be
                # attributed to anyone; report it instead of guessing.
                unattributed.append(item.gate_id)
                continue
            targets = list(gate.attributed_to)
            if not targets:
                unattributed.append(item.gate_id)
                continue
            for component_id in targets:
                task = self.task_for_component(component_id)
                if task is None:
                    unattributed.append(f"{item.gate_id}->{component_id}")
                    continue
                task.evidence = [
                    row for row in task.evidence if row.get("gate_id") != item.gate_id
                ] + [item.to_dict()]
                task.state = TaskState.FAILED.value
                task.notes = f"system gate {item.gate_id} failed: {item.detail[:200]}"
                if task.worktree:
                    self.repo.remove_worktree(task.worktree)
                    task.worktree = ""
                brief = self.write_repair_packet(task.id, max_attempts=max_attempts)
                packet = json.loads(brief.read_text())
                if packet.get("escalate"):
                    escalated.append(task.id)
                else:
                    task.state = TaskState.PENDING.value
                    reopened.append(task.id)
                ledger.append(
                    self.run, self.run_dir, "system.attributed",
                    gate=item.gate_id, task=task.id, attempt=task.attempts,
                    escalate=bool(packet.get("escalate")), brief=str(brief),
                )
        if unattributed:
            ledger.append(
                self.run, self.run_dir, "system.unattributed", gates=sorted(set(unattributed)),
            )
        self.refresh()
        self.persist()
        return {"reopened": sorted(set(reopened)), "escalated": sorted(set(escalated)),
                "unattributed": sorted(set(unattributed))}

    def all_evidence(self) -> list[Evidence]:
        return [Evidence.from_dict(item) for item in self.run.evidence]


def _within(path: str, patterns: list[str]) -> bool:
    if not patterns:
        return False
    for pattern in patterns:
        if fnmatch.fnmatch(path, pattern):
            return True
        if pattern.endswith("/") and path.startswith(pattern):
            return True
        if path.startswith(pattern.rstrip("/") + "/"):
            return True
    return False
