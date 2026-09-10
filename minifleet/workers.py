"""Worker front-ends: how a task packet reaches an agent runtime.

MiniFleet separates *what must be built and how it will be judged* (the packet)
from *who builds it* (the dispatcher). Four dispatchers ship here:

``packet``   write the packet to disk and hand off to any external runtime
``command``  invoke any agent CLI with the packet path and read back a result
``http``     single-shot materialisation through an OpenAI-compatible endpoint
``manual``   block until a human or orchestrator marks the task as submitted

The packet is written to be self-contained on purpose: a worker never needs the
rest of the fleet's context to do its job, which is what allows workers to be
different models, different vendors, or different agent CLIs.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from .model import Contract, Design, IntentSpec, Task


@dataclass
class Packet:
    """Everything a worker is allowed to know, and everything it must return."""

    run_id: str
    task: dict[str, Any]
    contracts: list[dict[str, Any]]
    acceptance: list[dict[str, Any]]
    budgets: dict[str, float]
    product_dir: str
    worktree: str
    base_commit: str
    integration_branch: str

    def to_dict(self) -> dict[str, Any]:
        return json.loads(json.dumps(self.__dict__, sort_keys=True))

    @property
    def task_id(self) -> str:
        return self.task["id"]

    def to_markdown(self) -> str:
        task = self.task
        lines = [
            f"# Task packet {task['id']} - {task['title']}",
            "",
            f"Run: `{self.run_id}`   Role: **{task['role']}**   Branch: `{task['branch']}`",
            "",
            "## Goal",
            "",
            task["goal"],
            "",
            "## Write scope (may not be exceeded)",
            "",
        ]
        lines += [f"- `{p}`" for p in task["owns"]] or ["- (none)"]
        lines += [
            "",
            "Any file outside this scope is owned by another worker. Touching it will "
            "fail the fleet's ownership check at integration time.",
            "",
            "## Frozen contracts",
            "",
        ]
        for contract in self.contracts:
            lines.append(f"### {contract['id']} - `{contract['path']}`")
            lines.append("")
            if contract.get("summary"):
                lines.append(contract["summary"])
                lines.append("")
            for symbol, signature in (contract.get("exports") or {}).items():
                lines.append(f"- `{symbol}`: {signature}")
            lines.append("")
        lines += ["## Definition of done", ""]
        lines += [f"- {item}" for item in task.get("done_when", [])] or ["- (unspecified)"]
        lines += ["", "## Tests you must write and run", ""]
        lines += [f"- {item}" for item in task.get("tests", [])] or ["- (unspecified)"]
        lines += ["", "## Acceptance criteria this task feeds", ""]
        for criterion in self.acceptance:
            if criterion["id"] in task.get("acceptance_ids", []):
                lines.append(f"- **{criterion['id']}** ({criterion['kind']}): {criterion['statement']}")
        if self.budgets:
            lines += ["", "## System budgets (enforced later, by the fleet)", ""]
            lines += [f"- `{name}`: {value}" for name, value in self.budgets.items()]
        lines += [
            "",
            "## Required return",
            "",
            "Work only inside the worktree above. When finished, report a JSON object with:",
            "",
            "```json",
            '{"task_id": "%s", "status": "submitted|failed", "files": ["..."], '
            '"commands": ["..."], "notes": "what you did and what you did not do"}'
            % task["id"],
            "```",
            "",
            "Report honestly: a task that is partly done and says so is more useful to the "
            "fleet than one that claims success. The fleet verifies every claim itself.",
        ]
        return "\n".join(lines)


@dataclass
class DispatchResult:
    task_id: str
    status: str
    worker: str = ""
    files: list[str] = field(default_factory=list)
    notes: str = ""
    commands: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return json.loads(json.dumps(self.__dict__, sort_keys=True))


class Dispatcher(Protocol):
    name: str

    def dispatch(self, packet: Packet, packet_path: Path) -> DispatchResult:
        ...


def build_packet(
    run_id: str,
    task: Task,
    design: Design,
    spec: IntentSpec,
    product_dir: Path,
    worktree: Path,
    integration_branch: str,
    base_commit: str,
) -> Packet:
    contracts = [
        c.to_dict() if hasattr(c, "to_dict") else _contract_dict(c)
        for c in design.contracts
        if c.id in task.contract_ids or not task.contract_ids
    ]
    return Packet(
        run_id=run_id,
        task=task.to_dict(),
        contracts=contracts,
        acceptance=[_criterion_dict(c) for c in spec.acceptance],
        budgets=dict(spec.budgets),
        product_dir=str(product_dir),
        worktree=str(worktree),
        base_commit=base_commit,
        integration_branch=integration_branch,
    )


def _contract_dict(contract: Contract) -> dict[str, Any]:
    return {
        "id": contract.id,
        "path": contract.path,
        "summary": contract.summary,
        "exports": contract.exports,
        "frozen": contract.frozen,
    }


def _criterion_dict(criterion: Any) -> dict[str, Any]:
    return {
        "id": criterion.id,
        "statement": criterion.statement,
        "kind": criterion.kind,
        "gates": list(criterion.gates),
    }


class PacketDispatcher:
    """Hand-off mode: the packet is the deliverable of the dispatch step."""

    name = "packet"

    def dispatch(self, packet: Packet, packet_path: Path) -> DispatchResult:
        return DispatchResult(
            task_id=packet.task_id,
            status="dispatched",
            worker="external-runtime",
            notes=f"packet written to {packet_path}; awaiting an external agent runtime",
        )


class CommandDispatcher:
    """Invoke any agent CLI: `<cmd> <packet_path> <result_path>`.

    This is the integration point for Codex, Claude Code, or an in-house agent.
    The contract with the worker is only the packet and the result JSON.
    """

    name = "command"

    def __init__(self, command: str, timeout: float = 3600.0) -> None:
        self.command = command
        self.timeout = timeout

    def dispatch(self, packet: Packet, packet_path: Path) -> DispatchResult:
        result_path = packet_path.with_suffix(".result.json")
        argv = shlex.split(self.command) + [str(packet_path), str(result_path)]
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=self.timeout)
        if result_path.exists():
            payload = json.loads(result_path.read_text())
            return _result_from_payload(packet, payload, worker=self.command)
        return DispatchResult(
            task_id=packet.task_id,
            status="failed" if proc.returncode else "dispatched",
            worker=self.command,
            notes=(proc.stdout + proc.stderr)[-2000:],
            finished_at=time.time(),
        )


class HttpDispatcher:
    """Single-shot materialisation through an OpenAI-compatible chat endpoint.

    Configure with ``MINIFLEET_LLM_BASE_URL``, ``MINIFLEET_LLM_API_KEY`` and
    ``MINIFLEET_LLM_MODEL``. The worker is asked for the complete file contents;
    anything the model omits stays absent and will fail the gates, which is the
    intended failure mode.
    """

    name = "http"

    def __init__(self, base_url: str | None = None, api_key: str | None = None, model: str | None = None):
        self.base_url = (base_url or os.environ.get("MINIFLEET_LLM_BASE_URL", "")).rstrip("/")
        self.api_key = api_key or os.environ.get("MINIFLEET_LLM_API_KEY", "")
        self.model = model or os.environ.get("MINIFLEET_LLM_MODEL", "gpt-4o-mini")

    def available(self) -> bool:
        return bool(self.base_url and self.api_key)

    def dispatch(self, packet: Packet, packet_path: Path) -> DispatchResult:
        started = time.time()
        if not self.available():
            return DispatchResult(
                task_id=packet.task_id,
                status="failed",
                worker="http",
                notes="MINIFLEET_LLM_BASE_URL / MINIFLEET_LLM_API_KEY are not configured",
                finished_at=time.time(),
            )
        body = json.dumps(
            {
                "model": self.model,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You are a fleet worker. Reply with JSON only: "
                            '{"files": {"relative/path": "full file content"}, "notes": "..."}'
                        ),
                    },
                    {"role": "user", "content": packet.to_markdown()},
                ],
                "response_format": {"type": "json_object"},
            }
        ).encode()
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=600) as response:
                payload = json.loads(response.read())
            content = payload["choices"][0]["message"]["content"]
            files = json.loads(content).get("files", {})
        except Exception as exc:  # noqa: BLE001
            return DispatchResult(
                task_id=packet.task_id,
                status="failed",
                worker=f"http:{self.model}",
                notes=f"{type(exc).__name__}: {exc}",
                started_at=started,
                finished_at=time.time(),
            )
        worktree = Path(packet.worktree)
        written: list[str] = []
        for rel, content in files.items():
            target = worktree / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
            written.append(rel)
        return DispatchResult(
            task_id=packet.task_id,
            status="submitted" if written else "failed",
            worker=f"http:{self.model}",
            files=sorted(written),
            notes=f"materialised {len(written)} files",
            started_at=started,
            finished_at=time.time(),
        )


def _result_from_payload(packet: Packet, payload: dict[str, Any], worker: str) -> DispatchResult:
    return DispatchResult(
        task_id=packet.task_id,
        status=str(payload.get("status", "submitted")),
        worker=worker,
        files=list(payload.get("files", [])),
        notes=str(payload.get("notes", "")),
        commands=list(payload.get("commands", [])),
        raw=payload,
        finished_at=time.time(),
    )


def make_dispatcher(spec: str) -> Dispatcher:
    """``packet`` | ``command:<cmd>`` | ``http``."""

    if spec in {"packet", "manual"}:
        return PacketDispatcher()
    if spec == "http":
        return HttpDispatcher()
    if spec.startswith("command:"):
        return CommandDispatcher(spec.split(":", 1)[1])
    raise ValueError(f"unknown dispatcher spec {spec!r}")
