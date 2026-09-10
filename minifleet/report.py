"""Run reports: the human-facing account of what the fleet actually proved.

The report is generated from the run record only. Nothing in it is asserted
without a gate and an evidence record, and the "not proven" section is
deliberately prominent.
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

from . import ledger
from .model import Design, IntentSpec, Run, TaskState

CSS = """
:root { --bg:#0b0f14; --panel:#131a23; --line:#22303d; --ink:#e8eef5; --muted:#8ba0b4;
        --ok:#3ddc97; --bad:#ff6b6b; --warn:#ffc857; --info:#57b8ff; }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--ink);
       font:15px/1.6 ui-sans-serif,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif; }
header { padding:40px 28px 24px; border-bottom:1px solid var(--line);
         background:linear-gradient(180deg,#101823,#0b0f14); }
h1 { margin:0 0 6px; font-size:26px; letter-spacing:-.01em; }
h2 { margin:36px 0 12px; font-size:18px; }
h3 { margin:22px 0 8px; font-size:13px; color:var(--muted); text-transform:uppercase; letter-spacing:.08em; }
.muted { color:var(--muted); }
main { padding:24px 28px 64px; max-width:1180px; }
.cards { display:grid; grid-template-columns:repeat(auto-fit,minmax(170px,1fr)); gap:12px; margin-top:18px; }
.card { background:var(--panel); border:1px solid var(--line); border-radius:12px; padding:14px 16px; }
.card .k { color:var(--muted); font-size:11.5px; text-transform:uppercase; letter-spacing:.08em; }
.card .v { font-size:22px; font-weight:600; margin-top:4px; }
table { width:100%; border-collapse:collapse; background:var(--panel);
        border:1px solid var(--line); border-radius:12px; overflow:hidden; }
th,td { text-align:left; padding:10px 12px; border-bottom:1px solid var(--line); vertical-align:top; font-size:13.5px; }
th { background:#0f1620; color:var(--muted); font-weight:600; font-size:11.5px; text-transform:uppercase; letter-spacing:.06em; }
tr:last-child td { border-bottom:none; }
code { font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:12.5px; color:#cfe3ff; }
.pill { display:inline-block; padding:1px 8px; border-radius:999px; font-size:11.5px;
        border:1px solid var(--line); background:#0f1620; }
.pill.pass,.pill.verified,.pill.merged { color:var(--ok); border-color:#1d5c43; background:#0f2620; }
.pill.fail,.pill.failed,.pill.error { color:var(--bad); border-color:#5c1d1d; background:#260f0f; }
.pill.dispatched,.pill.submitted,.pill.incomplete,.pill.unproven { color:var(--warn); border-color:#5c4a1d; background:#26200f; }
.pill.ready,.pill.pending { color:var(--info); border-color:#1d3c5c; background:#0f1c26; }
ul { margin:8px 0; padding-left:20px; }
footer { padding:18px 28px 40px; color:var(--muted); font-size:12.5px; border-top:1px solid var(--line); }
"""


def markdown(run: Run) -> str:
    spec = IntentSpec.from_dict(run.intent)
    design = Design.from_dict(run.design)
    rows = ledger.traceability(spec, design, ledger.evidence_of(run))
    lines: list[str] = [
        f"# Fleet run report - {spec.title}",
        "",
        f"- Run id: `{run.id}`",
        f"- Intent: `{spec.id}`",
        f"- Dispatcher: `{run.dispatcher}`",
        f"- Status: **{run.status}**",
        f"- Verdict: {run.verdict or 'not yet verified'}",
        "",
        "## Intent as received",
        "",
        run.raw_intent.strip() or spec.summary,
        "",
        "## Deliverables",
        "",
    ]
    lines += [f"- {d}" for d in spec.deliverables]
    lines += [
        "",
        "## Design",
        "",
        f"- Components: {len(design.components)}",
        f"- Contracts: {len(design.contracts)}",
        f"- Tasks: {len(design.tasks)}",
        f"- Gates: {len(design.gates)}",
        "",
    ]
    if design.repairs:
        lines += ["### Plan self-repairs", ""] + [f"- {r}" for r in design.repairs] + [""]
    lines += [
        "## Task graph",
        "",
        "| task | role | depends on | owns | state | files |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for task in design.tasks:
        lines.append(
            f"| `{task.id}` {task.title} | {task.role} | {', '.join(task.depends_on) or '-'} | "
            f"{len(task.owns)} path(s) | {task.state} | {len(task.submitted_files)} |"
        )
    lines += [
        "",
        "## Traceability: intent to evidence",
        "",
        "| criterion | verdict | statement | gate | result | observation |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        for gate in row["gates"]:
            detail = gate["detail"].replace("|", "/")[:120]
            statement = row["statement"][:70].replace("|", "/")
            lines.append(
                f"| {row['criterion']} | {row['status']} | {statement} | "
                f"`{gate['id']}` | {gate['status']} | {detail} |"
            )
    lines += [
        "",
        "## Gate ledger",
        "",
        "| gate | status | duration | metrics | detail |",
        "| --- | --- | --- | --- | --- |",
    ]
    for item in ledger.evidence_of(run):
        metrics = json.dumps(item.metrics) if item.metrics else ""
        lines.append(
            f"| `{item.gate_id}` | {item.status} | {item.duration_s:.2f}s | {metrics} | "
            f"{item.detail.replace('|', '/')[:140]} |"
        )
    unproven = [row for row in rows if row["status"] != "verified"]
    lines += ["", "## Not proven", ""]
    if unproven:
        lines += [f"- **{row['criterion']}** ({row['status']}): {row['statement']}" for row in unproven]
    else:
        lines.append("- Every acceptance criterion has passing evidence.")
    lines += ["", "## Risks carried forward", ""]
    lines += [f"- {r}" for r in design.risks] or ["- none recorded"]
    lines += ["", "## Decisions taken by the architect", ""]
    lines += [f"- {d}" for d in design.decisions] or ["- none recorded"]
    return "\n".join(lines) + "\n"


def html_report(run: Run) -> str:
    spec = IntentSpec.from_dict(run.intent)
    design = Design.from_dict(run.design)
    rows = ledger.traceability(spec, design, ledger.evidence_of(run))
    evidence = ledger.evidence_of(run)
    verified = sum(1 for r in rows if r["status"] == "verified")
    gates_pass = sum(1 for e in evidence if e.status == "pass")

    def esc(value: Any) -> str:
        return html.escape(str(value))

    task_rows = "\n".join(
        f"<tr><td><code>{esc(t.id)}</code><br><span class='muted'>{esc(t.title)}</span></td>"
        f"<td>{esc(', '.join(t.depends_on) or '-')}</td>"
        f"<td><code>{esc(', '.join(t.owns) or '-')}</code></td>"
        f"<td><span class='pill {esc(_status_class(t.state))}'>{esc(t.state)}</span></td>"
        f"<td>{len(t.submitted_files)}</td></tr>"
        for t in design.tasks
    )
    trace_rows = "\n".join(
        "<tr>"
        f"<td><code>{esc(r['criterion'])}</code><br>"
        f"<span class='pill {esc(_status_class(r['status']))}'>{esc(r['status'])}</span></td>"
        f"<td>{esc(r['statement'])}</td>"
        f"<td>{esc(', '.join(r['tasks']) or '-')}</td>"
        "<td>"
        + "<br>".join(
            f"<code>{esc(g['id'])}</code> "
            f"<span class='pill {esc(_status_class(g['status']))}'>{esc(g['status'])}</span>"
            f"<br><span class='muted'>{esc(g['detail'][:150])}</span>"
            for g in r["gates"]
        )
        + "</td></tr>"
        for r in rows
    )
    gate_rows = "\n".join(
        f"<tr><td><code>{esc(e.gate_id)}</code></td>"
        f"<td><span class='pill {esc(_status_class(e.status))}'>{esc(e.status)}</span></td>"
        f"<td>{e.duration_s:.2f}s</td>"
        f"<td><code>{esc(json.dumps(e.metrics) if e.metrics else '')}</code></td>"
        f"<td class='muted'>{esc(e.detail[:200])}</td></tr>"
        for e in evidence
    )
    unproven = [r for r in rows if r["status"] != "verified"]
    unproven_html = "".join(
        f"<li><strong>{esc(r['criterion'])}</strong> "
        f"<span class='pill {esc(_status_class(r['status']))}'>{esc(r['status'])}</span> "
        f"- {esc(r['statement'])}</li>"
        for r in unproven
    ) or "<li>Every acceptance criterion carries passing evidence from a gate.</li>"
    repairs_html = "".join(f"<li>{esc(r)}</li>" for r in design.repairs) or "<li>No repairs were needed.</li>"
    risks_html = "".join(f"<li>{esc(r)}</li>" for r in design.risks) or "<li>None recorded.</li>"
    decisions_html = "".join(f"<li>{esc(d)}</li>" for d in design.decisions) or "<li>None recorded.</li>"
    deliverables_html = "".join(f"<li>{esc(d)}</li>" for d in spec.deliverables)
    constraints_html = "".join(f"<li>{esc(c)}</li>" for c in spec.constraints) or "<li>None declared.</li>"
    out_of_scope_html = "".join(f"<li>{esc(c)}</li>" for c in spec.out_of_scope) or "<li>Not declared.</li>"

    return (
        "<!doctype html>\n<html lang='en'><head><meta charset='utf-8'>\n"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>\n"
        f"<title>MiniFleet run report - {esc(spec.title)}</title>\n"
        f"<style>{CSS}</style></head>\n<body>\n<header>\n"
        "  <h1>MiniFleet run report</h1>\n"
        f"  <div class='muted'>{esc(spec.title)} &middot; intent <code>{esc(spec.id)}</code> "
        f"&middot; run <code>{esc(run.id)}</code></div>\n"
        "  <div class='cards'>\n"
        f"    <div class='card'><div class='k'>Status</div><div class='v'>{esc(run.status)}</div></div>\n"
        f"    <div class='card'><div class='k'>Criteria verified</div><div class='v'>{verified}/{len(rows)}</div></div>\n"
        f"    <div class='card'><div class='k'>Gates passed</div><div class='v'>{gates_pass}/{len(evidence)}</div></div>\n"
        f"    <div class='card'><div class='k'>Tasks</div><div class='v'>{len(design.tasks)}</div></div>\n"
        f"    <div class='card'><div class='k'>Plan repairs</div><div class='v'>{len(design.repairs)}</div></div>\n"
        "  </div>\n</header>\n<main>\n"
        "  <h2>1. Intent as received</h2>\n"
        f"  <p>{esc(run.raw_intent.strip() or spec.summary)}</p>\n"
        f"  <h3>Deliverables</h3><ul>{deliverables_html}</ul>\n"
        f"  <h3>Constraints</h3><ul>{constraints_html}</ul>\n"
        f"  <h3>Out of scope</h3><ul>{out_of_scope_html}</ul>\n"
        "  <h2>2. Design and task graph</h2>\n"
        f"  <p class='muted'>The architect froze {len(design.contracts)} contract(s) and split the work into "
        f"{len(design.tasks)} single-writer tasks before any worker was dispatched.</p>\n"
        f"  {_dag_svg(design)}\n"
        "  <table><thead><tr><th>Task</th><th>Depends on</th><th>Write scope</th><th>State</th><th>Files</th>"
        f"</tr></thead>\n  <tbody>{task_rows}</tbody></table>\n"
        f"  <h3>Plan self-repairs</h3><ul>{repairs_html}</ul>\n"
        "  <h2>3. Traceability: intent to evidence</h2>\n"
        "  <p class='muted'>Every acceptance criterion is tied to the gate that decided its fate. "
        "Nothing in this report is asserted without a row here.</p>\n"
        "  <table><thead><tr><th>Criterion</th><th>Statement</th><th>Owning tasks</th>"
        f"<th>Gate and observation</th></tr></thead>\n  <tbody>{trace_rows}</tbody></table>\n"
        "  <h2>4. Gate ledger</h2>\n"
        f"  <table><thead><tr><th>Gate</th><th>Status</th><th>Duration</th><th>Metrics</th><th>Detail</th>"
        f"</tr></thead>\n  <tbody>{gate_rows}</tbody></table>\n"
        f"  <h2>5. Not proven</h2>\n  <ul>{unproven_html}</ul>\n"
        "  <h2>6. Risks and architect decisions</h2>\n"
        f"  <h3>Risks carried forward</h3><ul>{risks_html}</ul>\n"
        f"  <h3>Decisions</h3><ul>{decisions_html}</ul>\n"
        "</main>\n<footer>Generated by MiniFleet from the run ledger - no claim in this report exists "
        "without gate evidence.</footer>\n</body></html>\n"
    )


def write(run: Run, run_dir: Path | str, html_mode: bool = True) -> dict[str, str]:
    run_dir = Path(run_dir)
    md_path = run_dir / "report.md"
    md_path.write_text(markdown(run))
    outputs = {"markdown": str(md_path)}
    if html_mode:
        html_path = run_dir / "report.html"
        html_path.write_text(html_report(run))
        outputs["html"] = str(html_path)
    return outputs


def _status_class(status: str) -> str:
    return {
        "verified": "pass",
        "pass": "pass",
        "merged": "pass",
        "failed": "fail",
        "fail": "fail",
        "error": "fail",
        "incomplete": "dispatched",
        "unproven": "dispatched",
    }.get(status, "")


def _dag_svg(design: Design, width: int = 1080, row_height: int = 78) -> str:
    """A layered dependency graph; layers are dependency depth."""

    depth: dict[str, int] = {}

    def compute(task_id: str, seen: set[str]) -> int:
        if task_id in depth:
            return depth[task_id]
        if task_id in seen:
            return 0
        task = design.task(task_id)
        value = 0 if not task.depends_on else 1 + max(compute(d, seen | {task_id}) for d in task.depends_on)
        depth[task_id] = value
        return value

    for task in design.tasks:
        compute(task.id, set())
    layers: dict[int, list[str]] = {}
    for task_id, level in depth.items():
        layers.setdefault(level, []).append(task_id)
    max_level = max(layers) if layers else 0
    height = (max_level + 1) * row_height + 20
    nodes: dict[str, tuple[float, float]] = {}
    boxes: list[str] = []
    edges: list[str] = []
    for level in sorted(layers):
        members = sorted(layers[level])
        slot = width / (len(members) + 1)
        for index, task_id in enumerate(members, start=1):
            x = slot * index
            y = 30 + level * row_height
            nodes[task_id] = (x, y)
            task = design.task(task_id)
            colour = {
                TaskState.MERGED.value: "#3ddc97",
                TaskState.VERIFIED.value: "#3ddc97",
                TaskState.FAILED.value: "#ff6b6b",
                TaskState.DISPATCHED.value: "#ffc857",
                TaskState.SUBMITTED.value: "#ffc857",
            }.get(task.state, "#57b8ff")
            boxes.append(
                f"<g><rect x='{x - 99:.0f}' y='{y - 24:.0f}' width='198' height='48' rx='10' "
                f"fill='#131a23' stroke='{colour}' stroke-width='1.5'/>"
                f"<text x='{x:.0f}' y='{y - 4:.0f}' text-anchor='middle' fill='#e8eef5' "
                f"font-family='ui-sans-serif' font-size='12.5'>{html.escape(task.id)}</text>"
                f"<text x='{x:.0f}' y='{y + 13:.0f}' text-anchor='middle' fill='#8ba0b4' "
                f"font-family='ui-sans-serif' font-size='11'>{html.escape(task.title[:28])}</text></g>"
            )
    for task in design.tasks:
        for dep in task.depends_on:
            if dep in nodes and task.id in nodes:
                x1, y1 = nodes[dep]
                x2, y2 = nodes[task.id]
                edges.append(
                    f"<path d='M{x1:.0f} {y1 + 24:.0f} C {x1:.0f} {y1 + 46:.0f}, "
                    f"{x2:.0f} {y2 - 46:.0f}, {x2:.0f} {y2 - 24:.0f}' stroke='#22303d' "
                    "fill='none' stroke-width='1.5'/>"
                )
    return (
        f"<svg viewBox='0 0 {width} {height}' width='100%' height='{height}' role='img' "
        "aria-label='task dependency graph'>" + "".join(edges) + "".join(boxes) + "</svg>"
    )
