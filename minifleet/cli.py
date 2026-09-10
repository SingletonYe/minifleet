"""Command line interface: the operator's view of the fleet."""

from __future__ import annotations

import argparse
import io
import json
import shlex
import shutil
import subprocess
import sys
import tarfile
import time
from pathlib import Path
from typing import Any

from . import architect, intent as intent_mod, ledger, report as report_mod
from .deploy import Supervisor, metrics_from_report, smoke, soak
from .fleetmetrics import summarize
from .model import Design, IntentSpec, Run, RunStatus, TaskState
from .repair import build_repair_packet
from .scheduler import Scheduler
from .workers import PacketDispatcher, make_dispatcher
from .worktree import GitError, git


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="minifleet", description="intent -> verified production systems")
    sub = parser.add_subparsers(dest="command", required=True)

    p_plan = sub.add_parser("plan", help="compile an intent and lay out the run")
    p_plan.add_argument("--intent", required=True)
    p_plan.add_argument("--runs-dir", default="runs")
    p_plan.add_argument("--dispatcher", default="packet")

    p_status = sub.add_parser("status", help="show run state and next actions")
    p_status.add_argument("--run", required=True)

    p_dispatch = sub.add_parser("dispatch", help="dispatch the current ready wave of tasks")
    p_dispatch.add_argument("--run", required=True)
    p_dispatch.add_argument("--dispatcher", default=None)
    p_dispatch.add_argument("--limit", type=int, default=None)
    p_dispatch.add_argument("--max-parallel", type=int, default=4)

    p_ingest = sub.add_parser("ingest", help="accept a worker's output and run its task gates")
    p_ingest.add_argument("--run", required=True)
    p_ingest.add_argument("--task", required=True)
    p_ingest.add_argument("--worker", default="manual")
    p_ingest.add_argument("--notes", default="")

    p_integrate = sub.add_parser("integrate", help="merge verified tasks onto the integration branch")
    p_integrate.add_argument("--run", required=True)

    p_verify = sub.add_parser("verify", help="run system gates on the integrated tree")
    p_verify.add_argument("--run", required=True)

    p_report = sub.add_parser("report", help="write the run report")
    p_report.add_argument("--run", required=True)
    p_report.add_argument("--no-html", action="store_true")

    p_packet = sub.add_parser("packet", help="print the packet for one task")
    p_packet.add_argument("--run", required=True)
    p_packet.add_argument("--task", required=True)

    p_run = sub.add_parser("run", help="plan, dispatch, integrate and verify in one shot")
    p_run.add_argument("--intent", required=True)
    p_run.add_argument("--runs-dir", default="runs")
    p_run.add_argument("--dispatcher", default="packet")
    p_run.add_argument("--max-parallel", type=int, default=4)

    p_evolve = sub.add_parser("evolve", help="apply a new intent to a completed run")
    p_evolve.add_argument("--run", required=True)
    p_evolve.add_argument("--intent", required=True)

    p_repair = sub.add_parser("repair", help="turn failed tasks into bounded repair packets")
    p_repair.add_argument("--run", required=True)
    p_repair.add_argument("--max-attempts", type=int, default=3)

    p_deploy = sub.add_parser("deploy", help="supervise a produced system and probe it")
    p_deploy.add_argument("--dir", required=True, help="working directory to start the process in")
    p_deploy.add_argument("--cmd", required=True, help="the command that starts the system")
    p_deploy.add_argument("--probe", required=True, help="probe path, e.g. /healthz")
    p_deploy.add_argument("--seconds", type=float, default=0.0, help="soak window in seconds")
    p_deploy.add_argument("--ready-prefix", default="MINIFLEET_READY")
    p_deploy.add_argument("--timeout", type=float, default=30.0, help="seconds to wait for ready")
    p_deploy.add_argument("--emit-metrics", action="store_true",
                          help="print MINIFLEET_METRIC lines so budget gates can enforce them")

    p_compile = sub.add_parser(
        "compile", help="turn a prose intent document into a machine-checkable intent"
    )
    p_compile.add_argument("--text", required=True, help="the Markdown intent document")
    p_compile.add_argument("--out", default=None, help="where to write the compiled intent JSON")
    p_compile.add_argument("--backend", default="rules", choices=["rules", "llm"])

    p_autopilot = sub.add_parser(
        "autopilot", help="drive a run to a verdict: dispatch, verify, repair, repeat"
    )
    p_autopilot.add_argument("--run", required=True)
    p_autopilot.add_argument("--dispatcher", required=True, help="command:<cmd> or http")
    p_autopilot.add_argument("--max-attempts", type=int, default=3)
    p_autopilot.add_argument("--max-rounds", type=int, default=4)
    p_autopilot.add_argument("--max-parallel", type=int, default=4)

    p_metrics = sub.add_parser("fleetmetrics", help="account for a run: attempts, retries, gates, path")
    p_metrics.add_argument("--run", required=True)

    args = parser.parse_args(argv)
    handler = {
        "plan": cmd_plan,
        "status": cmd_status,
        "dispatch": cmd_dispatch,
        "ingest": cmd_ingest,
        "integrate": cmd_integrate,
        "verify": cmd_verify,
        "report": cmd_report,
        "packet": cmd_packet,
        "run": cmd_run,
        "evolve": cmd_evolve,
        "repair": cmd_repair,
        "deploy": cmd_deploy,
        "compile": cmd_compile,
        "autopilot": cmd_autopilot,
        "fleetmetrics": cmd_fleetmetrics,
    }[args.command]
    return handler(args)


# -- commands ------------------------------------------------------------
def cmd_plan(args: argparse.Namespace) -> int:
    spec = intent_mod.load(args.intent)
    raw = Path(args.intent).read_text()
    run, run_dir = ledger.create(args.runs_dir, spec, args.dispatcher, raw_intent=_prose(raw))
    design = architect.design(spec)
    run.set_design(design)

    scheduler = Scheduler(run, run_dir, spec, design)
    baseline = baseline_files(spec, design)
    baseline.update(harness_files(args.intent, spec))
    baseline["INTENT.md"] = f"# Intent as received\n\n{_prose(raw)}\n\n```json\n{raw.strip()}\n```\n"

    brownfield = bool(spec.baseline_from)
    provenance: dict[str, Any] | None = None
    if brownfield:
        source = (Path(args.intent).parent / spec.baseline_from).resolve()
        provenance = adopt_baseline(source, Path(run.repo_dir))
        (run_dir / "baseline.json").write_text(json.dumps(provenance, indent=2, sort_keys=True) + "\n")
        ledger.append(run, run_dir, "baseline.adopted", **provenance)
        origin = provenance["revision"] or "working tree"
        dirty = " (working copy was dirty)" if provenance.get("dirty") else ""
        print(
            f"baseline : brownfield from {source} [{provenance['mode']}] "
            f"{provenance['files']} files @ {origin}{dirty}"
        )
    scheduler.repo.init(
        baseline,
        overwrite=not brownfield,
        fleet_owned=("ACCEPTANCE.md", "INTENT.md", "contracts/", "harness/"),
    )
    scheduler.repo.ensure_branch(run.integration_branch)
    scheduler.refresh()
    scheduler.packets()
    scheduler.persist()
    ledger.append(run, run_dir, "plan.complete", tasks=len(design.tasks), gates=len(design.gates))

    print(f"run      : {run.id}")
    print(f"run dir  : {run_dir}")
    print(f"tasks    : {len(design.tasks)}  gates: {len(design.gates)}  contracts: {len(design.contracts)}")
    if design.repairs:
        print("plan self-repairs:")
        for item in design.repairs:
            print(f"  - {item}")
    print("ready    :", ", ".join(t.id for t in scheduler.ready()) or "(none)")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    run, run_dir = _load(args.run)
    design = run.design_obj
    spec = IntentSpec.from_dict(run.intent)
    print(ledger.summary(run))
    print("")
    print(f"{'task':<18}{'state':<12}{'depends':<14}{'files':<7}scope")
    for task in design.tasks:
        print(
            f"{task.id:<18}{task.state:<12}{','.join(task.depends_on) or '-':<14}"
            f"{len(task.submitted_files):<7}{','.join(task.owns)[:60]}"
        )
    print("")
    print("next actions:")
    for task in design.tasks:
        if task.state in {TaskState.PENDING.value, TaskState.READY.value}:
            print(f"  dispatch {task.id}: see {run_dir}/packets/{task.id}.md")
        elif task.state == TaskState.DISPATCHED.value:
            print(f"  ingest   {task.id}: minifleet ingest --run {run_dir} --task {task.id}")
        elif task.state == TaskState.VERIFIED.value:
            print(f"  merge    {task.id}: minifleet integrate --run {run_dir}")
        elif task.state == TaskState.FAILED.value:
            print(f"  retry    {task.id}: {task.notes or 'gates failed'}")
    if run.verdict:
        print("")
        print("verdict:", run.verdict)
    return 0


def cmd_dispatch(args: argparse.Namespace) -> int:
    run, run_dir = _load(args.run)
    scheduler, spec = _scheduler(run, run_dir)
    dispatcher = make_dispatcher(args.dispatcher or run.dispatcher)
    results = scheduler.dispatch(dispatcher, max_parallel=args.max_parallel, limit=args.limit)
    if not results:
        print("nothing ready to dispatch")
        return 0
    for item in results:
        print(f"{item['task_id']:<18}{item['status']:<12}{item.get('worker', '')}")
        if item.get("notes"):
            print(f"    {item['notes'][:160]}")
    print("")
    print("packets written to", run_dir / "packets")
    if isinstance(dispatcher, PacketDispatcher):
        print("external hand-off mode: run the packet with any agent runtime, then `minifleet ingest`")
    return 0


def cmd_ingest(args: argparse.Namespace) -> int:
    run, run_dir = _load(args.run)
    scheduler, _ = _scheduler(run, run_dir)
    result = scheduler.ingest(args.task, worker=args.worker, notes=args.notes)
    print(json.dumps(result, indent=2)[:4000])
    return 0 if result["status"] == "verified" else 1


def cmd_integrate(args: argparse.Namespace) -> int:
    run, run_dir = _load(args.run)
    scheduler, _ = _scheduler(run, run_dir)
    result = scheduler.integrate()
    print(json.dumps(result, indent=2))
    return 0 if result.get("status") == "ok" else 1


def cmd_verify(args: argparse.Namespace) -> int:
    run, run_dir = _load(args.run)
    scheduler, _ = _scheduler(run, run_dir)
    result = scheduler.verify()
    for item in result["evidence"]:
        metrics = json.dumps(item["metrics"]) if item["metrics"] else ""
        print(f"{item['gate_id']:<28}{item['status']:<8}{item['duration_s']:>7.2f}s  {metrics}")
        if item["status"] != "pass":
            print(f"    {item['detail'][:300]}")
    print("")
    print(result["verdict"])
    return 0 if run.status == RunStatus.PASSED.value else 1


def cmd_report(args: argparse.Namespace) -> int:
    run, run_dir = _load(args.run)
    outputs = report_mod.write(run, run_dir, html_mode=not args.no_html)
    for kind, path in outputs.items():
        print(f"{kind}: {path}")
    return 0


def cmd_packet(args: argparse.Namespace) -> int:
    run, run_dir = _load(args.run)
    path = Path(run_dir) / "packets" / f"{args.task}.md"
    if not path.exists():
        print(f"no packet for {args.task}", file=sys.stderr)
        return 1
    print(path.read_text())
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    code = cmd_plan(args)
    if code:
        return code
    runs = sorted(Path(args.runs_dir).glob("*"), key=lambda p: p.stat().st_mtime)
    run_dir = runs[-1]
    run = Run.load(run_dir)
    scheduler, _ = _scheduler(run, run_dir)
    dispatcher = make_dispatcher(args.dispatcher)
    if isinstance(dispatcher, PacketDispatcher):
        print("packet dispatcher does nothing by itself; use `command:<cmd>` or `http`", file=sys.stderr)
        return 2
    while scheduler.refresh() or scheduler.ready():
        if not scheduler.ready():
            break
        scheduler.dispatch(dispatcher, max_parallel=args.max_parallel)
        for task in list(scheduler.design.tasks):
            if task.state == TaskState.DISPATCHED.value and _worktree_dirty(task):
                scheduler.ingest(task.id, worker=dispatcher.name)
        if all(t.state != TaskState.READY.value for t in scheduler.design.tasks):
            break
    scheduler.integrate()
    scheduler.verify()
    report_mod.write(run, run_dir)
    print(f"\nfinal: {run.status}  {run.verdict}")
    return 0 if run.status == RunStatus.PASSED.value else 1


def cmd_evolve(args: argparse.Namespace) -> int:
    run, run_dir = _load(args.run)
    scheduler, spec = _scheduler(run, run_dir)
    new_spec = intent_mod.load(args.intent)
    plan = architect.design(new_spec)

    old_ids = {t.component_id for t in scheduler.design.tasks}
    new_ids = {c.id for c in new_spec.components}
    added = sorted(new_ids - old_ids)
    removed = sorted(old_ids - new_ids)
    changed = []
    for component in new_spec.components:
        if component.id in old_ids:
            old = next(c for c in scheduler.design.components if c.id == component.id)
            if old.responsibility != component.responsibility or sorted(old.owns) != sorted(component.owns):
                changed.append(component.id)

    delta = {
        "from_intent": run.intent_id,
        "to_intent": new_spec.id,
        "added_components": added,
        "removed_components": removed,
        "changed_components": sorted(changed),
        "new_tasks": [t.id for t in plan.tasks if t.component_id in added],
        "reopened_tasks": [f"T-{c}" for c in changed],
        "regression_suite": [g.id for g in plan.gates if g.scope == "system"],
    }
    (Path(run_dir) / "evolution.json").write_text(json.dumps(delta, indent=2))
    run.status = RunStatus.EVOLVING.value
    run.intent = new_spec.to_dict()
    merged = {t.id: t for t in scheduler.design.tasks}
    plan.tasks = [
        t if t.component_id in added or t.component_id in changed else merged.get(f"T-{t.component_id}", t)
        for t in plan.tasks
    ]
    for task in plan.tasks:
        if task.component_id in changed:
            task.state = TaskState.PENDING.value
    run.set_design(plan)
    scheduler.design = plan
    scheduler.spec = new_spec
    scheduler.refresh()
    scheduler.persist()
    ledger.append(run, run_dir, "evolve.planned", **{k: v for k, v in delta.items() if k != "regression_suite"})
    print(json.dumps(delta, indent=2))
    print("\nreopened/prepared tasks are READY; dispatch them as usual, then integrate and verify.")
    print("previously verified tasks stay merged and keep running as the regression suite.")
    return 0


# -- helpers -------------------------------------------------------------
def cmd_repair(args: argparse.Namespace) -> int:
    """Turn every failed task in a run into a bounded, actionable repair packet."""

    run_dir = Path(args.run)
    record = json.loads((run_dir / "run.json").read_text())

    # A live run has a product repository: repair means reopening the task on a
    # fresh attempt branch, not just writing a note about it.
    repo_dir = record.get("repo_dir") or ""
    if repo_dir and (Path(repo_dir) / ".git").exists():
        run, _ = _load(run_dir)
        scheduler, _ = _scheduler(run, run_dir)
        result = scheduler.repair_failed(max_attempts=args.max_attempts)
        if not result["reopened"] and not result["escalated"]:
            print("no failed tasks")
            for gate in result["unattributed"]:
                print(f"  unattributed failure: {gate}")
            return 0
        print(f"{len(result['reopened'])} task(s) reopened for another attempt")
        for task_id in result["reopened"]:
            print(f"  {task_id}: {run_dir / 'packets' / f'{task_id}.repair.json'}")
        for task_id in result["escalated"]:
            print(f"  {task_id}: ESCALATED (attempt budget exhausted)")
        for gate in result["unattributed"]:
            print(f"  unattributed failure: {gate} (no component claims it)")
        return 0 if not result["escalated"] else 2

    tasks = ((record.get("design") or {}).get("tasks")) or []
    failed = [task for task in tasks if task.get("state") == "failed"]
    if not failed:
        print("no failed tasks")
        return 0
    packets_dir = run_dir / "packets"
    packets_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for task in failed:
        packet = build_repair_packet(
            task,
            _task_evidence(record, task),
            attempt=int(task.get("attempts") or 0),
            max_attempts=args.max_attempts,
        )
        path = packets_dir / f"{task.get('id')}.repair.json"
        path.write_text(json.dumps(packet, indent=2, sort_keys=True))
        written.append(path)
    print(f"{len(written)} repair packet(s) written")
    for path in written:
        print(f"  {path}")
    return 0


def cmd_deploy(args: argparse.Namespace) -> int:
    """Start a produced system, wait for its ready line, probe it, then tear it down."""

    supervisor = Supervisor(
        shlex.split(args.cmd),
        cwd=args.dir,
        ready_prefix=args.ready_prefix,
        timeout=args.timeout,
    )
    report: dict[str, Any] = {"dir": args.dir, "cmd": args.cmd, "probe": args.probe, "ok": False}
    try:
        supervisor.start()
        ready = supervisor.wait_ready()
        report["ready"] = ready
        port = ready.get("port")
        if ready.get("ready") and port:
            url = f"http://127.0.0.1:{port}{args.probe}"
            report["url"] = url
            report["smoke"] = smoke(url)
            ok = bool(report["smoke"]["ok"])
            if args.seconds > 0:
                report["soak"] = soak(url, seconds=args.seconds)
                ok = ok and bool(report["soak"]["ok"])
            report["ok"] = ok
        else:
            report["reason"] = "the system never announced a ready line with a port"
    finally:
        report["exit_code"] = supervisor.stop()
    print(json.dumps(report, indent=2, sort_keys=True))
    if getattr(args, "emit_metrics", False):
        for name, value in metrics_from_report(report).items():
            print("MINIFLEET_METRIC " + json.dumps({"name": name, "value": value}))
    return 0 if report["ok"] else 1


def cmd_compile(args: argparse.Namespace) -> int:
    """Compile a prose intent document into the JSON the fleet executes."""

    from . import compile as compile_mod

    try:
        compilation = compile_mod.compile_path(args.text, backend=args.backend)
    except compile_mod.CompileError as exc:
        print(f"compilation failed: {exc}", file=sys.stderr)
        return 2

    data = compilation.data
    gates = data["gates"]
    print(f"intent   : {data['id']} - {data['title']}")
    print(f"backend  : {compilation.backend}")
    print(f"derived  : {len(data['components'])} component(s), {len(data['acceptance'])} acceptance "
          f"criteria, {len(gates)} gate(s), {len(data['budgets'])} budget(s)")
    print("")
    print("components and write scopes:")
    for component in data["components"]:
        deps = f"  depends on {', '.join(component['depends_on'])}" if component["depends_on"] else ""
        print(f"  {component['id']:<14}{', '.join(component['owns'])}{deps}")
    print("")
    print("gates:")
    for gate in gates:
        detail = gate.get("cmd") or gate.get("metric") or ", ".join(gate.get("paths", []))
        print(f"  {gate['id']:<32}{gate['kind']:<8}{gate['scope']:<8}{str(detail)[:64]}")
    print("")
    print("acceptance criteria:")
    for criterion in data["acceptance"]:
        print(f"  {criterion['id']:<6}[{criterion['kind']:<10}] -> {', '.join(criterion['gates']):<28}"
              f"{criterion['statement'][:60]}")
    if compilation.notes:
        print("")
        print("what the compiler decided for you:")
        for note in compilation.notes:
            print(f"  - {note}")
    if args.out:
        Path(args.out).write_text(compilation.to_json())
        print("")
        print(f"written  : {args.out}")
    return 0


def cmd_autopilot(args: argparse.Namespace) -> int:
    """Drive a run to a verdict: dispatch, ingest, integrate, verify, repair, repeat.

    This is the loop the three v0.2 capabilities exist to close. It stops for one
    of three reasons and says which: the run passed, the attempt budget for every
    failing task is exhausted (escalation), or nothing attributable is left to fix.
    """

    run, run_dir = _load(args.run)
    scheduler, spec = _scheduler(run, run_dir)
    dispatcher = make_dispatcher(args.dispatcher)
    if isinstance(dispatcher, PacketDispatcher):
        print("autopilot needs a dispatcher that can run workers: command:<cmd> or http", file=sys.stderr)
        return 2

    limits = spec.limits or {}
    max_attempts = int(limits.get("max_attempts", args.max_attempts))
    max_rounds = int(limits.get("max_rounds", args.max_rounds))
    wall_budget = limits.get("max_wall_seconds")
    started = time.time()
    exhausted = ""
    history: list[dict[str, Any]] = []
    for round_index in range(1, max_rounds + 1):
        if wall_budget and time.time() - started > wall_budget:
            exhausted = f"wall-clock budget of {wall_budget:g}s is spent"
            print(f"  {exhausted}; stopping")
            break
        print(f"--- round {round_index}: dispatch ---")
        dispatched = scheduler.dispatch(dispatcher, max_parallel=args.max_parallel)
        if not dispatched and scheduler.ready():
            exhausted = "the dispatch budget is spent and work is still ready"
            print(f"  {exhausted}; stopping")
            break
        for item in dispatched:
            if item.get("status") == "submitted":
                outcome = scheduler.ingest(item["task_id"], worker=str(item.get("worker", "")))
                print(f"  {item['task_id']:<18}{outcome['status']:<10}{outcome.get('gates', {})}")
            else:
                task = scheduler.task(item["task_id"])
                task.state = TaskState.FAILED.value
                task.notes = str(item.get("notes", ""))[:400]
                scheduler.persist()
                print(f"  {item['task_id']:<18}{item.get('status')}")

        integration = scheduler.integrate()
        if integration.get("status") != "ok":
            history.append({"round": round_index, "integration": integration})
            print(f"  integration failed: {integration.get('detail') or integration.get('conflicts')}")
            break
        verification = scheduler.verify(max_attempts=max_attempts)
        attribution = verification.get("attribution", {})
        history.append({
            "round": round_index,
            "dispatched": [item["task_id"] for item in dispatched],
            "merged": integration.get("merged", []),
            "verdict": verification["verdict"],
            "reopened": attribution.get("reopened", []),
            "unattributed": attribution.get("unattributed", []),
        })
        print(f"  verify: {verification['verdict']}")
        if scheduler.run.status == RunStatus.PASSED.value:
            break
        repair = scheduler.repair_failed(max_attempts=max_attempts)
        # verify() may already have reopened the failing task, in which case
        # repair_failed() has nothing new to add. What decides whether the loop
        # continues is not the repair report - it is whether any task is
        # dispatchable right now.
        ready = scheduler.ready()
        print(
            f"  repair: reopened={repair['reopened']} escalated={repair['escalated']} "
            f"ready={[task.id for task in ready]}"
        )
        if repair["escalated"] and not ready:
            print("  escalating: the attempt budget is exhausted")
            break
        if not ready:
            print("  nothing dispatchable left; stopping")
            break

    report_mod.write(run, run_dir)
    ledger.append(run, run_dir, "autopilot.finished", status=run.status, verdict=run.verdict,
                  rounds=len(history), budget_exhausted=exhausted,
                  seconds=round(time.time() - started, 3))
    print("")
    print(f"status : {run.status}")
    print(f"verdict: {run.verdict}")
    print(f"rounds : {len(history)}")
    if exhausted:
        print(f"budget : exhausted - {exhausted}")
    print(f"report : {run_dir / 'report.html'}")
    if run.status == RunStatus.PASSED.value:
        return 0
    return 3 if exhausted else 1


def cmd_fleetmetrics(args: argparse.Namespace) -> int:
    """Print one run's fleet-level accounting as JSON."""

    record = json.loads((Path(args.run) / "run.json").read_text())
    print(json.dumps(summarize(record), indent=2, sort_keys=True))
    return 0


def _task_evidence(record: dict[str, Any], task: dict[str, Any]) -> list[dict[str, Any]]:
    """The evidence a failed task is judged on: its own rows, else its gate rows."""

    own = task.get("evidence")
    if own:
        return list(own)
    gate_ids = set(task.get("gate_ids") or [])
    return [
        item
        for item in record.get("evidence") or []
        if item.get("gate_id") in gate_ids
    ]


def _load(run_dir: str) -> tuple[Run, Path]:
    path = Path(run_dir)
    return Run.load(path), path


def _scheduler(run: Run, run_dir: Path) -> tuple[Scheduler, IntentSpec]:
    spec = IntentSpec.from_dict(run.intent)
    design = Design.from_dict(run.design)
    return Scheduler(run, run_dir, spec, design), spec


def _prose(raw: str) -> str:
    """Recover the human sentence from a structured intent file, if present."""

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return raw
    for key in ("intent", "intent_text", "raw_intent", "summary"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return data.get("title", "")


def _worktree_dirty(task) -> bool:
    return bool(task.worktree) and Path(task.worktree).exists()


def baseline_files(spec: IntentSpec, design: Design) -> dict[str, str]:
    """The frozen baseline every worker starts from.

    Contracts, the acceptance statement and the harness are committed *before*
    any worker is dispatched, so no worker can grade its own homework.
    """

    files: dict[str, str] = {}
    files["README.md"] = _readme(spec, design)
    files["ACCEPTANCE.md"] = _acceptance_md(spec, design)
    for contract in design.contracts:
        body = [f"# {contract.id}: {contract.path}", "", contract.summary, ""]
        if contract.exports:
            body += ["## Frozen exports", ""]
            body += [f"- `{name}`: {sig}" for name, sig in contract.exports.items()]
        if getattr(contract, "content", ""):
            body += ["", "## Content", "", contract.content]
        files[f"contracts/{contract.id}.md"] = "\n".join(body) + "\n"
    return files


def harness_files(intent_path: str, spec: IntentSpec) -> dict[str, str]:
    """Copy the frozen acceptance harness into the product baseline."""

    if not spec.harness_dir:
        return {}
    root = (Path(intent_path).parent / spec.harness_dir).resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"harness_dir not found: {root}")
    files: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            rel = path.relative_to(root).as_posix()
            files[f"harness/{rel}"] = path.read_text()
    return files


IGNORED_DIRS = {".git", "__pycache__", "runs", ".pytest_cache", ".mypy_cache", "node_modules", "site"}
IGNORED_SUFFIXES = {".pyc", ".pyo", ".so", ".sqlite", ".db", ".png", ".jpg", ".zip", ".whl"}
MAX_COPY_BYTES = 512 * 1024


def copy_baseline_tree(source: Path, destination: Path) -> int:
    """Adopt an existing codebase as the product tree (brownfield intents)."""

    if not source.is_dir():
        raise FileNotFoundError(f"baseline_from not found: {source}")
    destination.mkdir(parents=True, exist_ok=True)
    count = 0
    for path in sorted(source.rglob("*")):
        rel = path.relative_to(source)
        if any(part in IGNORED_DIRS for part in rel.parts):
            continue
        if path.is_dir():
            continue
        if path.suffix in IGNORED_SUFFIXES or path.stat().st_size > MAX_COPY_BYTES:
            continue
        target = destination / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        count += 1
    return count


def _git_bytes(args: list[str], cwd: Path) -> bytes:
    proc = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True)
    if proc.returncode != 0:
        raise GitError(
            f"git {' '.join(args)} failed in {cwd}:\n{proc.stderr.decode(errors='replace').strip()}"
        )
    return proc.stdout


def source_revision(source: Path) -> dict[str, Any] | None:
    """The committed revision of a git source tree, and whether its working copy is dirty.

    Returns ``None`` for a plain directory, which is the only case where the
    filesystem is the truth. A working copy is not a revision: it can be
    half-written, stale, or shared with another process.
    """

    if not (source / ".git").exists():
        return None
    head = git(["rev-parse", "HEAD"], source, check=False)
    if head.returncode != 0:
        return None
    status = git(["status", "--porcelain"], source, check=False)
    return {"revision": head.stdout.strip(), "dirty": bool(status.stdout.strip())}


def copy_revision_tree(source: Path, destination: Path, revision: str) -> int:
    """Materialise the committed tree of ``revision``, ignoring the working copy."""

    archive = _git_bytes(["archive", "--format=tar", revision], source)
    destination.mkdir(parents=True, exist_ok=True)
    count = 0
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            rel = Path(member.name)
            if any(part in IGNORED_DIRS for part in rel.parts):
                continue
            if rel.suffix in IGNORED_SUFFIXES or member.size > MAX_COPY_BYTES:
                continue
            handle = tar.extractfile(member)
            if handle is None:
                continue
            target = destination / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(handle.read())
            count += 1
    return count


def adopt_baseline(source: Path, destination: Path) -> dict[str, Any]:
    """Adopt an existing codebase as the product tree, pinned to a revision when possible.

    A brownfield run has to be reproducible: it adopts ``HEAD`` of the source
    repository and records that revision, so the product tree is a revision
    rather than whatever happened to be on disk when the planner ran. Only a
    non-git directory falls back to copying the filesystem.
    """

    info = source_revision(source)
    if info is None:
        files = copy_baseline_tree(source, destination)
        return {
            "mode": "filesystem",
            "source": str(source),
            "revision": None,
            "dirty": None,
            "files": files,
        }
    files = copy_revision_tree(source, destination, info["revision"])
    return {
        "mode": "git-revision",
        "source": str(source),
        "revision": info["revision"],
        "dirty": info["dirty"],
        "files": files,
    }


def _readme(spec: IntentSpec, design: Design) -> str:
    lines = [
        f"# {spec.title}",
        "",
        spec.summary,
        "",
        "## Deliverables",
        "",
    ]
    lines += [f"- {d}" for d in spec.deliverables]
    lines += [
        "",
        "## How this repository is verified",
        "",
        "This repository is produced and admitted to production by MiniFleet. "
        "The acceptance harness in `harness/` was frozen before any implementation "
        "work started, and every acceptance criterion in `ACCEPTANCE.md` is tied to a gate.",
        "",
        "## Components",
        "",
    ]
    for component in spec.components:
        lines.append(f"- **{component.id}** — {component.responsibility}")
    return "\n".join(lines) + "\n"


def _acceptance_md(spec: IntentSpec, design: Design) -> str:
    lines = ["# Acceptance criteria (frozen)", "", "| id | kind | statement | gates |", "| --- | --- | --- | --- |"]
    for criterion in spec.acceptance:
        lines.append(
            f"| {criterion.id} | {criterion.kind} | {criterion.statement} | {', '.join(criterion.gates)} |"
        )
    if spec.budgets:
        lines += ["", "## Non-functional budgets", "", "| metric | budget |", "| --- | --- |"]
        lines += [f"| {name} | {value} |" for name, value in spec.budgets.items()]
    lines += ["", "## Gates", "", "| gate | kind | scope | command |", "| --- | --- | --- | --- |"]
    for gate in design.gates:
        lines.append(f"| {gate.id} | {gate.kind} | {gate.scope} | `{gate.cmd or gate.json_path or ','.join(gate.paths)}` |")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
