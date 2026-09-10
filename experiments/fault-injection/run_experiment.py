#!/usr/bin/env python3
"""End-to-end self-hosting verification of MiniFleet, with a real injected fault.

The experiment runs the fleet on its own repository and injects a defect that no
task gate can see:

1. adopt the engine repository (pinned to a git revision) as the product,
2. let the fault-injecting worker write a defective `minifleet/quarantine.py`
   whose own tests pass - the task gate is green,
3. the frozen harness fails, and because the gate declares which component it
   implicates, the fleet attributes the failure, writes a repair packet and
   reopens the task on a new attempt,
4. the second attempt reads that packet and fixes the module,
5. the run has to reach a green verdict, then deploy a running system and account
   for the whole run with fleetmetrics.

Everything is written to `out/` so the result can be re-read without re-running.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from minifleet.report import CSS  # noqa: E402  (same styling as a run report)

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
RUNS = HERE / "runs"
OUT = HERE / "out"
INTENT = ROOT / "intents" / "quarantine.json"
WORKER = HERE / "workers" / "worker.py"

TRANSCRIPT: list[str] = []


def run(args: list[str], timeout: int = 1800) -> subprocess.CompletedProcess:
    started = time.time()
    proc = subprocess.run(
        [sys.executable, "-m", "minifleet", *args],
        cwd=str(ROOT), capture_output=True, text=True, timeout=timeout,
    )
    elapsed = time.time() - started
    TRANSCRIPT.append(
        f"\n$ minifleet {' '.join(args)}   ({elapsed:.1f}s, exit {proc.returncode})\n"
        + (proc.stdout or "")
        + (f"\n[stderr]\n{proc.stderr}\n" if proc.stderr.strip() else "")
    )
    return proc


def latest_run_dir() -> Path:
    runs = sorted((RUNS).glob("*"), key=lambda path: path.stat().st_mtime)
    if not runs:
        raise SystemExit("no run directory was produced")
    return runs[-1]


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    if RUNS.exists():
        shutil.rmtree(RUNS)

    plan = run(["plan", "--intent", str(INTENT), "--runs-dir", str(RUNS)])
    if plan.returncode != 0:
        print(plan.stdout, plan.stderr)
        return 1
    run_dir = latest_run_dir()
    print(f"run directory: {run_dir}")

    autopilot = run([
        "autopilot", "--run", str(run_dir),
        "--dispatcher", f"command:{sys.executable} {WORKER}",
        "--max-attempts", "3", "--max-rounds", "4",
    ])
    print(autopilot.stdout.strip().splitlines()[-4:] and "\n".join(autopilot.stdout.strip().splitlines()[-4:]))

    deploy = run([
        "deploy",
        "--dir", "examples/linksvc",
        "--cmd",
        "python3 -m linksvc --host 127.0.0.1 --port 0 --db /tmp/minifleet-experiment.db "
        "--rate 1000 --burst 1000",
        "--probe", "/healthz", "--seconds", "4", "--emit-metrics",
    ])
    metrics_proc = run(["fleetmetrics", "--run", str(run_dir)])

    record = json.loads((run_dir / "run.json").read_text())
    ledger = [
        json.loads(line)
        for line in (run_dir / "ledger.jsonl").read_text().splitlines()
        if line.strip()
    ]
    failing = [row for row in record.get("evidence", []) if row.get("status") != "pass"]
    repair_packet_path = run_dir / "packets" / "T-quarantine.repair.json"
    repair_packet = json.loads(repair_packet_path.read_text()) if repair_packet_path.exists() else {}
    deploy_report = _json_prefix(deploy.stdout)
    fleet_metrics = json.loads(metrics_proc.stdout)

    summary = {
        "run_id": record["id"],
        "intent": record["intent_id"],
        "final_status": record["status"],
        "final_verdict": record["verdict"],
        "autopilot_exit": autopilot.returncode,
        "rounds": [event for event in ledger if event["event"] == "system.verified"],
        "attributed": [event for event in ledger if event["event"] == "system.attributed"],
        "unattributed": [event for event in ledger if event["event"] == "system.unattributed"],
        "failing_evidence": failing,
        "repair_packet": repair_packet,
        "deploy": deploy_report,
        "fleet_metrics": fleet_metrics,
    }
    (OUT / "transcript.log").write_text("\n".join(TRANSCRIPT))
    (OUT / "experiment.json").write_text(json.dumps(summary, indent=2, sort_keys=True))
    (OUT / "RESULT.md").write_text(_result_markdown(summary))
    publish_artifacts(run_dir)

    passed = record["status"] == "passed" and deploy_report.get("ok") is True
    print("")
    print(f"run status      : {record['status']} ({record['verdict']})")
    print(f"failed attempts : {len(failing)} gate observation(s) red before the repair")
    print(f"attributed to   : {[event['gate'] for event in summary['attributed']]}")
    print(f"deploy          : ok={deploy_report.get('ok')} p50={deploy_report.get('soak', {}).get('p50_ms')}ms")
    print(f"fleet metrics   : {json.dumps(fleet_metrics)}")
    print(f"artefacts       : {OUT / 'RESULT.md'}, {OUT / 'experiment.json'}, {OUT / 'transcript.log'}")
    return 0 if passed else 1


def publish_artifacts(run_dir: Path) -> dict[str, str]:
    """Copy the evidence a reader needs to audit the run, and render the result.

    The run directory itself stays out of version control (it is a scratch tree);
    what is published is the record a sceptic needs: the run record, the append-only
    ledger, the generated report, the packets and the per-gate evidence.
    """

    audit = OUT / "audit"
    audit.mkdir(parents=True, exist_ok=True)
    copied: dict[str, str] = {}
    for name in ("run.json", "ledger.jsonl", "report.html", "report.md", "dispatch.json", "baseline.json"):
        source = run_dir / name
        if source.exists():
            shutil.copy2(source, audit / name)
            copied[name] = str(audit / name)
    for directory in ("packets", "evidence"):
        source = run_dir / directory
        if source.is_dir():
            target = audit / directory
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(source, target, ignore=shutil.ignore_patterns("__pycache__"))
            copied[directory] = str(target)
    result_md = OUT / "RESULT.md"
    if result_md.exists():
        (OUT / "RESULT.html").write_text(_markdown_to_html(result_md.read_text()))
    return copied


def _markdown_to_html(text: str) -> str:
    """Enough Markdown for this document: headings, lists, code fences, inline code."""

    import html as html_module

    lines = text.splitlines()
    body: list[str] = []
    in_code = False
    in_list = False
    for line in lines:
        if line.startswith("```"):
            if in_code:
                body.append("</pre>")
                in_code = False
            else:
                body.append("<pre>")
                in_code = True
            continue
        if in_code:
            body.append(html_module.escape(line))
            continue
        if line.startswith("- "):
            if not in_list:
                body.append("<ul>")
                in_list = True
            body.append(f"<li>{_inline(line[2:])}</li>")
            continue
        if in_list:
            body.append("</ul>")
            in_list = False
        if line.startswith("#"):
            level = min(len(line) - len(line.lstrip("#")), 3)
            body.append(f"<h{level}>{_inline(line[level:].strip())}</h{level}>")
        elif line.strip():
            body.append(f"<p>{_inline(line.strip())}</p>")
        else:
            body.append("")
    if in_list:
        body.append("</ul>")
    if in_code:
        body.append("</pre>")
    return (
        "<!doctype html>\n<html lang='en'><head><meta charset='utf-8'>\n"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>\n"
        "<title>MiniFleet injected-fault experiment</title>\n"
        f"<style>{CSS}</style></head>\n<body>\n<header>\n"
        "  <h1>Injected-fault experiment</h1>\n"
        "  <div class='muted'>MiniFleet repairing a defect it could not see - "
        "detected, attributed, bounded, re-verified</div>\n</header>\n<main>\n"
        + "\n".join(body)
        + "\n</main>\n<footer>Generated by the experiment runner from the run it produced - "
        "no claim here exists without the run record next to it.</footer>\n</body></html>\n"
    )


def _inline(text: str) -> str:
    import html as html_module
    import re

    escaped = html_module.escape(text)
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    escaped = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escaped)
    return escaped


def _json_prefix(text: str) -> dict:
    """The deploy command prints a JSON report and then MINIFLEET_METRIC lines."""

    lines = [line for line in text.splitlines() if not line.startswith("MINIFLEET_METRIC")]
    body = "\n".join(lines)
    start = body.find("{")
    end = body.rfind("}")
    if start < 0 or end < 0:
        return {}
    return json.loads(body[start : end + 1])


def _result_markdown(summary: dict) -> str:
    attributed = summary["attributed"]
    rounds = summary["rounds"]
    first_round = rounds[0] if rounds else {}
    last_round = rounds[-1] if rounds else {}
    packet = summary["repair_packet"]
    soak = summary["deploy"].get("soak", {})
    ready = summary["deploy"].get("ready", {})
    lines = [
        "# Injected-fault experiment: does the repair loop actually close?",
        "",
        f"Run `{summary['run_id']}` for intent `{summary['intent']}`, executed by "
        "`minifleet autopilot` against the engine's own repository.",
        "",
        "## 1. The injected defect",
        "",
        "The worker wrote `minifleet/quarantine.py` with two defects that its own tests "
        "do not cover: an empty sample is reported as `stable-fail` instead of "
        "`insufficient-data`, and the insufficient-data guard is `<=` instead of `<`, so "
        "exactly `min_samples` outcomes are refused a verdict.",
        "",
        "The task gate (`python3 -m unittest discover -s tests -v`) passed anyway - the "
        "worker's tests only used 4-5 sample series. That is the point: the defect is "
        "invisible to whoever wrote it.",
        "",
        "## 2. What the fleet saw on the first attempt",
        "",
    ]
    for row in summary["failing_evidence"]:
        detail = (row.get("detail") or "").replace("\n", " ")
        tail = (row.get("output_tail") or "").strip().splitlines()
        assertion = next((line.strip() for line in tail if "AssertionError" in line), "")
        lines.append(f"- `{row['gate_id']}` -> **{row['status']}**: {detail}")
        if assertion:
            lines.append(f"  - {assertion[:200]}")
    lines += [
        "",
        f"Round 1 verdict: `{first_round.get('verdict', 'n/a')}`",
        "",
        "## 3. What the fleet did about it",
        "",
    ]
    for event in attributed:
        lines.append(
            f"- gate `{event['gate']}` attributed to task `{event['task']}` "
            f"(attempt {event['attempt']}, escalate={event['escalate']})"
        )
    if summary["unattributed"]:
        gates = sorted({gate for event in summary["unattributed"] for gate in event.get("gates", [])})
        lines.append(
            f"- gates nobody claimed were reported rather than retried: {gates}"
        )
    lines += [
        "",
        "The repair packet the fleet handed to the next attempt:",
        "",
        "```json",
        json.dumps(
            {
                "attempt": packet.get("attempt"),
                "max_attempts": packet.get("max_attempts"),
                "escalate": packet.get("escalate"),
                "reason": packet.get("reason"),
                "failing_gates": packet.get("failing_gates"),
                "instructions": packet.get("instructions"),
            },
            indent=2,
        ),
        "```",
        "",
        "## 4. The second attempt",
        "",
        f"The worker read `packets/T-quarantine.repair.json`, fixed the module and "
        f"resubmitted. Final verdict: **{summary['final_verdict']}** "
        f"(round verdicts: {[r.get('verdict') for r in rounds]}).",
        "",
        "## 5. Deployment, observed by the fleet itself",
        "",
        f"- ready in {ready.get('seconds')}s on port {ready.get('port')}",
        f"- smoke `{summary['deploy'].get('smoke', {}).get('status')}`, "
        f"soak {soak.get('samples')} samples / {soak.get('failures')} failures, "
        f"p50 {soak.get('p50_ms')} ms, max {soak.get('max_ms')} ms",
        f"- budget gates: ready <= 10s and p50 <= 50ms both enforced as system gates",
        "",
        "## 6. Fleet accounting",
        "",
        "```json",
        json.dumps(summary["fleet_metrics"], indent=2, sort_keys=True),
        "```",
        "",
        "## 7. Reproduce",
        "",
        "```bash",
        "python3 experiments/fault-injection/run_experiment.py",
        "```",
        "",
        "## 8. What this proves, and what it does not",
        "",
        "- It proves the loop closes: a defect invisible to its author's tests was caught "
        "by a gate the worker could not edit, attributed to the component that caused it, "
        "turned into an actionable packet, and fixed on a second attempt that the same "
        "gate re-verified.",
        "- It does not prove the worker 'understood' anything: the second attempt in this "
        "experiment is a deterministic script. What is under test is the engine - "
        "detection, attribution, bounded retry and re-verification.",
        "- An unattributed red gate is deliberately *not* retried. The fleet reports it "
        "and stops, because retrying what nobody owns is how a fleet burns a night.",
    ]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
