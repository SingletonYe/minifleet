"""The front door: prose in, a machine-checkable intent out.

An intent written by hand is an intent nobody writes twice. This module reads a
short Markdown document - the kind of thing a person actually writes - and derives
the parts that are mechanical and easy to get wrong: components and their disjoint
write scopes from the deliverables, gates from the gate library, acceptance
criteria bound to the gate that will judge them, budgets from declared metrics, and
policy gates from the constraints.

Two back ends share one interface:

``rules``  deterministic parsing, no model, fully reproducible
``llm``    the rules result is handed to a model (any OpenAI-compatible endpoint,
           see :class:`HttpRefiner`) to refine, then re-validated

Whatever produced the draft, the result must survive ``intent.validate`` before a
single worker is dispatched. The compiler's assumptions are returned with the
intent and printed, so a human can see what was derived rather than stated.
"""

from __future__ import annotations

import json
import os
import re
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from . import gatelib, intent as intent_mod
from .model import Component, Contract, IntentSpec


class CompileError(ValueError):
    """Raised when a document cannot be turned into a valid intent."""


SECTION_RE = re.compile(r"^##\s+(.+?)\s*$")
TITLE_RE = re.compile(r"^#\s+(.+?)\s*$")
BULLET_RE = re.compile(r"^[-*]\s+(.*)$")
METRIC_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*(<=|>=|<|>|==)\s*(-?\d+(?:\.\d+)?)\s*$")
TAG_RE = re.compile(r"^\[([a-z:-]+)\]\s*(.*)$")
PROSE_METRIC_PATTERNS = (
    (re.compile(r"under\s+(\d+(?:\.\d+)?)\s*(ms|milliseconds|s|seconds)", re.I), "latency"),
    (re.compile(r"at least\s+(\d+(?:\.\d+)?)\s*requests?\s+per\s+second", re.I), "rps"),
)


@dataclass
class Compilation:
    """A compiled intent plus everything the compiler decided for the author."""

    data: dict[str, Any]
    notes: list[str] = field(default_factory=list)
    backend: str = "rules"

    @property
    def spec(self) -> IntentSpec:
        return IntentSpec.from_dict(self.data)

    def to_json(self) -> str:
        return json.dumps(self.data, indent=2) + "\n"


# -- document parsing ----------------------------------------------------
def parse_sections(text: str) -> tuple[str, str, dict[str, list[str]]]:
    """Split a document into title, preamble and ``## section`` lines."""

    title = ""
    preamble: list[str] = []
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for raw in text.splitlines():
        line = raw.rstrip()
        if not title:
            match = TITLE_RE.match(line)
            if match:
                title = match.group(1)
                continue
        match = SECTION_RE.match(line)
        if match:
            current = match.group(1).strip().lower()
            sections.setdefault(current, [])
            continue
        if current is None:
            preamble.append(line)
        else:
            sections[current].append(line)
    return title, "\n".join(preamble).strip(), sections


def bullets(lines: Iterable[str]) -> list[str]:
    out: list[str] = []
    for line in lines:
        match = BULLET_RE.match(line.strip())
        if match:
            out.append(match.group(1).strip())
    return out


def key_values(lines: Iterable[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    for item in bullets(lines):
        key, _, value = item.partition(":")
        if value.strip():
            values[key.strip().lower()] = value.strip()
    return values


# -- derivations ---------------------------------------------------------
def components_from_deliverables(
    deliverables: list[str], notes: list[str]
) -> tuple[list[Component], list[str]]:
    """One component per delivered module, with its tests attached."""

    parsed: list[tuple[str, str, list[str]]] = []
    attached: list[tuple[str, str]] = []
    for item in deliverables:
        path, _, description = item.partition(" - ")
        path = path.strip()
        depends: list[str] = []
        owner = ""
        if "| of:" in description:
            description, _, raw_owner = description.partition("| of:")
            owner = raw_owner.strip()
        if "| depends:" in description:
            description, _, raw_deps = description.partition("| depends:")
            depends = [dep.strip() for dep in raw_deps.split(",") if dep.strip()]
        if owner:
            attached.append((path, owner))
            continue
        parsed.append((path, description.strip(), depends))

    modules = [(path, desc, deps) for path, desc, deps in parsed if not _is_test(path)]
    tests = [path for path, _, _ in parsed if _is_test(path)]

    components: list[Component] = []
    used: dict[str, int] = {}
    for path, description, depends in modules:
        components.append(
            Component(
                id=_component_id(path, used),
                name=_title_from_path(path),
                responsibility=description or f"deliver {path}",
                owns=[path],
                depends_on=depends,
            )
        )
    for test_path in tests:
        owner = _owner_for_test(test_path, components)
        if owner is None:
            components.append(
                Component(
                    id=_component_id(test_path, used),
                    name=_title_from_path(test_path),
                    responsibility=f"deliver {test_path}",
                    owns=[test_path],
                )
            )
            notes.append(f"{test_path} had no module of the same name, so it became its own component")
        else:
            owner.owns.append(test_path)
    for path, component_id in attached:
        target = next((item for item in components if item.id == component_id), None)
        if target is None:
            raise CompileError(f"{path} says '| of: {component_id}', but there is no such component")
        target.owns.append(path)
    if len(components) > 1:
        notes.append(
            f"{len(components)} components derived from the deliverables, each with a disjoint write scope"
        )
    return components, [dep for _, _, deps in parsed for dep in deps]


def _is_test(path: str) -> bool:
    name = Path(path).name
    return path.startswith("tests/") or name.startswith("test_")


def _component_id(path: str, used: dict[str, int]) -> str:
    stem = Path(path).stem
    if stem in used:
        used[stem] += 1
        parent = Path(path).parent.name
        return f"{parent}-{stem}" if parent and parent != "." else f"{stem}-{used[stem]}"
    used[stem] = 1
    return stem


def _title_from_path(path: str) -> str:
    return Path(path).stem.replace("_", " ").capitalize()


def _owner_for_test(test_path: str, components: list[Component]) -> Component | None:
    stem = Path(test_path).stem
    if stem.startswith("test_"):
        stem = stem[len("test_"):]
    for component in components:
        for owned in component.owns:
            if Path(owned).stem == stem:
                return component
    return None


def policy_gates(constraints: list[str], roots: list[str], network_allow: list[str],
                 stdlib_allow: list[str], notes: list[str]) -> list[dict[str, Any]]:
    """Turn constraints phrased in prose into gates that actually check them."""

    gates: list[dict[str, Any]] = []
    joined = " ".join(constraints).lower()
    if roots and ("standard library" in joined or "third-party" in joined or "third party" in joined):
        gates.append(gatelib.stdlib_only(roots, allow=stdlib_allow, project=roots))
        notes.append(f"'standard library only' became an enforced import gate over {', '.join(roots)}")
        if stdlib_allow:
            notes.append(
                "declared exceptions to that gate: " + ", ".join(stdlib_allow)
                + " (an exception belongs in the intent, never in the checker)"
            )
    if "no network" in joined or "offline" in joined:
        gates.append(gatelib.no_network(roots, allow=network_allow))
        notes.append("'no network in library code' became an enforced import gate")
    return gates


def suggest_budgets(text: str) -> dict[str, float]:
    """Read budgets out of prose - as suggestions, never as silent enforcement."""

    suggestions: dict[str, float] = {}
    for pattern, kind in PROSE_METRIC_PATTERNS:
        for match in pattern.finditer(text):
            value = float(match.group(1))
            if kind == "rps":
                suggestions["throughput_rps"] = value
            else:
                unit = (match.group(2) or "").lower()
                if unit.startswith("s"):
                    value *= 1000.0
                suggestions["latency_under_ms"] = value
    return suggestions


def project_roots(components: list[Component]) -> list[str]:
    roots: list[str] = []
    for component in components:
        for owned in component.owns:
            parts = Path(owned).parts
            if parts and parts[0] not in roots and not _is_test(owned):
                roots.append(parts[0])
    return roots


def compile_document(text: str) -> Compilation:
    """The rules back end: deterministic, reproducible, no model involved."""

    notes: list[str] = []
    title, preamble, sections = parse_sections(text)
    if not title:
        raise CompileError("the document needs a '# title' line")
    meta = key_values(sections.get("meta", []))
    deliverables = bullets(sections.get("deliverables", []))
    if not deliverables:
        raise CompileError(
            "the document needs a '## Deliverables' section with at least one '- path - description' bullet"
        )

    components, dependencies = components_from_deliverables(deliverables, notes)
    known = {component.id for component in components}
    for dependency in dependencies:
        if dependency not in known:
            raise CompileError(f"a deliverable depends on {dependency!r}, which is not one of the components")

    # The frozen harness can be declared either as one key in `## Meta` or as the
    # directory of the `## Harness` section that names its modules. Either way the
    # baseline must carry it, or the harness gates have nothing to run against.
    harness_section = key_values(sections.get("harness", []))
    harness_dir = meta.get("harness_dir", "") or harness_section.get("dir", "")
    if harness_dir and not meta.get("harness_dir"):
        notes.append(f"the harness directory {harness_dir!r} came from the '## Harness' section")
    baseline_from = meta.get("baseline_from", "")
    contracts = contracts_from_sections(sections)
    for component in components:
        component.contract_ids = [contract.id for contract in contracts]

    constraints = bullets(sections.get("constraints", []))
    out_of_scope = bullets(sections.get("out of scope", [])) or bullets(sections.get("out-of-scope", []))
    budgets = parse_budgets(sections, notes)
    probes = parse_probes(sections, budgets, notes)
    harness_gates = parse_harness(sections, sorted(known), notes)
    # A `## Harness` section declares the gates *and* the directory they live in.
    # The directory is what `plan` copies into the product baseline, so it has to
    # travel with the gates: without it the run would declare system gates for a
    # module that never reached the repository under construction.
    if not harness_dir and harness_gates:
        harness_dir = key_values(sections.get("harness", [])).get("dir", "")
    deploy = key_values(sections.get("deploy", []))
    service = key_values(sections.get("service", []))
    roots = project_roots(components)

    gates: list[dict[str, Any]] = [gatelib.unit_suite()]
    if harness_gates:
        gates.extend(harness_gates)
    elif harness_dir:
        module = "harness.test_" + harness_dir.split("-")[-1]
        gates.append(gatelib.frozen_harness(module, attributed_to=sorted(known)))
    if service.get("dir"):
        gates.append(gatelib.service_harness(service["dir"], service.get("harness", "harness.test_accept")))
    gates.extend(
        policy_gates(
            constraints,
            roots,
            split_list(meta.get("network_allow")),
            split_list(meta.get("stdlib_allow")),
            notes,
        )
    )
    gates.append(gatelib.regression())
    gates.extend(probes)
    if deploy.get("cmd"):
        gates.append(
            gatelib.deploy_smoke(
                deploy["dir"], deploy["cmd"], deploy.get("probe", "/healthz"),
                float(deploy.get("seconds", 3)),
            )
        )
    for metric, (op, threshold) in budgets.items():
        gates.append(gatelib.budget(metric, op, threshold))
    gate_ids = {gate["id"] for gate in gates}
    gates.append(gatelib.files_present([path for path, _, _ in split_deliverables(deliverables)]))

    acceptance = build_acceptance(sections, gate_ids, harness_dir, notes)
    for component in components:
        component.acceptance_ids = [criterion["id"] for criterion in acceptance]
        component.gate_ids = ["G-unit"]

    suggestions = suggest_budgets(text)
    if suggestions:
        notes.append(
            "prose suggested budgets "
            + ", ".join(f"{name}={value:g}" for name, value in sorted(suggestions.items()))
            + "; declare them under '## Budgets' to have them enforced"
        )

    data: dict[str, Any] = {
        "id": meta.get("id") or _slug(title),
        "title": title,
        "summary": (preamble.split("\n\n")[0] if preamble else title),
        "intent": text,
        "stakeholders": split_list(meta.get("stakeholders")),
        "deliverables": deliverables,
        "constraints": constraints,
        "out_of_scope": out_of_scope,
        "budgets": {name: value for name, (_, value) in budgets.items()},
        "acceptance": acceptance,
        "gates": gates,
        "contracts": [dict(contract.__dict__) for contract in contracts],
        "harness_dir": harness_dir,
        "baseline_from": baseline_from,
        "components": [dict(component.__dict__) for component in components],
        "limits": parse_limits(meta),
        "compile_notes": notes,
    }
    validate_intent(data)
    return Compilation(data=data, notes=notes, backend="rules")


def split_deliverables(deliverables: list[str]) -> list[tuple[str, str, list[str]]]:
    out: list[tuple[str, str, list[str]]] = []
    for item in deliverables:
        path, _, rest = item.partition(" - ")
        out.append((path.strip(), rest.strip(), []))
    return out


def contracts_from_sections(sections: dict[str, list[str]]) -> list[Contract]:
    lines = sections.get("contract", [])
    if not lines:
        return []
    exports: dict[str, str] = {}
    prose: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("- "):
            symbol, _, signature = stripped[2:].partition(":")
            if signature.strip():
                exports[symbol.strip()] = signature.strip()
                continue
        prose.append(line)
    return [
        Contract(
            id="C-1",
            path="contracts/C-1.md",
            summary="frozen interface derived from the intent",
            exports=exports,
            content="\n".join(prose).strip(),
        )
    ]


def parse_budgets(sections: dict[str, list[str]], notes: list[str]) -> dict[str, tuple[str, float]]:
    budgets: dict[str, tuple[str, float]] = {}
    for item in bullets(sections.get("budgets", [])):
        match = METRIC_RE.match(item)
        if not match:
            notes.append(f"budget line ignored (expected '<metric> <op> <number>'): {item}")
            continue
        budgets[match.group(1)] = (match.group(2), float(match.group(3)))
    return budgets


def parse_probes(sections: dict[str, list[str]], budgets: dict[str, tuple[str, float]],
                 notes: list[str]) -> list[dict[str, Any]]:
    probes: list[dict[str, Any]] = []
    for item in bullets(sections.get("probes", [])):
        metrics, _, command = item.partition(":")
        names = [name.strip() for name in metrics.split(",") if name.strip()]
        command = command.strip()
        if not names or not command:
            notes.append(f"probe line ignored (expected '<metric>[, <metric>]: <command>'): {item}")
            continue
        probes.append(gatelib.probe(names[0], command, title="measure " + ", ".join(names)))
    produced = {
        name
        for gate in probes
        for name in gate["title"][len("measure "):].split(", ")
    }
    for metric in budgets:
        if metric.startswith("deploy_") or metric in produced:
            continue
        notes.append(
            f"budget {metric!r} has no probe gate: declare one under '## Probes', or its budget gate "
            "will report an error rather than a pass"
        )
    return probes


def parse_harness(sections: dict[str, list[str]], components: list[str],
                  notes: list[str]) -> list[dict[str, Any]]:
    """One system gate per frozen harness module, attributed to the whole build."""

    values = key_values(sections.get("harness", []))
    directory = values.get("dir", "")
    modules = split_list(values.get("modules")) or split_list(values.get("module"))
    if not directory or not modules:
        return []
    attribution = split_list(values.get("attributed_to")) or components
    gates: list[dict[str, Any]] = []
    for module in modules:
        name = module if module.startswith("test_") else f"test_{module}"
        gates.append(
            gatelib.frozen_harness(
                f"harness.{name}",
                attributed_to=attribution,
            )
        )
        gates[-1]["id"] = "G-harness-" + name[len("test_"):].replace("_", "-")
        gates[-1]["title"] = f"frozen harness: {directory}/{name}"
    notes.append(
        f"the frozen harness became {len(gates)} system gate(s) over {directory}, "
        f"attributed to {', '.join(attribution)}"
    )
    return gates


def build_acceptance(sections: dict[str, list[str]], gate_ids: set[str], harness_dir: str,
                     notes: list[str]) -> list[dict[str, Any]]:
    criteria: list[dict[str, Any]] = []
    harness_gates = sorted(gid for gid in gate_ids if gid == "G-harness" or gid.startswith("G-harness-"))
    tag_to_gate = {
        "regression": "G-regression",
        "unit": "G-unit",
        "policy": "G-stdlib-only",
        "service": "G-service",
        "deploy": "G-deploy",
    }
    untagged = 0
    for index, item in enumerate(bullets(sections.get("acceptance", [])), start=1):
        match = TAG_RE.match(item)
        tag = match.group(1) if match else ""
        statement = (match.group(2) if match else item).strip()
        if tag:
            if tag == "harness":
                gates = list(harness_gates)
            elif tag.startswith("harness:"):
                wanted = "G-harness-" + tag.split(":", 1)[1].strip().replace("_", "-")
                gates = [wanted] if wanted in gate_ids else []
            else:
                gate_id = tag_to_gate.get(tag, "")
                gates = [gate_id] if gate_id in gate_ids else []
            if not gates:
                raise CompileError(
                    f"acceptance [{tag}] does not map to a gate this document produces "
                    f"(it produced {sorted(gate_ids)})"
                )
        elif harness_gates:
            gates = list(harness_gates)
            untagged += 1
        else:
            gates = ["G-regression"]
            untagged += 1
        if gates[0] == "G-deploy":
            gates.extend(sorted(gid for gid in gate_ids if gid.startswith("G-budget-")))
        kind = "nfr" if any(g.startswith("G-budget") for g in gates) or gates[0] == "G-deploy" else "functional"
        criteria.append({"id": f"A-{index}", "statement": statement, "kind": kind, "gates": gates})
    if not criteria:
        raise CompileError(
            "the document needs a '## Acceptance' section: an intent nobody can falsify is not actionable"
        )
    if untagged:
        notes.append(f"{untagged} untagged acceptance criteria were bound to the frozen harness")
    return criteria


def parse_limits(meta: dict[str, str]) -> dict[str, float]:
    limits: dict[str, float] = {}
    for key in ("max_wall_seconds", "max_attempts", "max_dispatches", "max_rounds"):
        if key in meta:
            try:
                limits[key] = float(meta[key])
            except ValueError as exc:
                raise CompileError(f"{key} must be a number, got {meta[key]!r}") from exc
    if limits and "max_attempts" not in limits:
        limits["max_attempts"] = 3.0
    return limits


def split_list(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _slug(title: str) -> str:
    tail = title.split(":", 1)[1] if ":" in title else title
    return re.sub(r"[^a-z0-9]+", "-", tail.strip().lower()).strip("-") or "intent"


def validate_intent(data: dict[str, Any]) -> None:
    try:
        spec = IntentSpec.from_dict(data)
        intent_mod.validate(spec, source="compiled")
    except CompileError:
        raise
    except Exception as exc:  # noqa: BLE001 - surface any failure as a compile error
        raise CompileError(str(exc)) from exc


# -- model back end ------------------------------------------------------
class HttpRefiner:
    """Refine a deterministic draft with any OpenAI-compatible chat endpoint."""

    def __init__(self, base_url: str | None = None, api_key: str | None = None,
                 model: str | None = None) -> None:
        self.base_url = (base_url or os.environ.get("MINIFLEET_LLM_BASE_URL", "")).rstrip("/")
        self.api_key = api_key or os.environ.get("MINIFLEET_LLM_API_KEY", "")
        self.model = model or os.environ.get("MINIFLEET_LLM_MODEL", "gpt-4o-mini")

    def available(self) -> bool:
        return bool(self.base_url and self.api_key)

    def refine(self, draft: dict[str, Any], text: str) -> dict[str, Any]:
        if not self.available():
            raise CompileError(
                "no model endpoint configured: set MINIFLEET_LLM_BASE_URL and MINIFLEET_LLM_API_KEY, "
                "or compile with --backend rules"
            )
        prompt = (
            "You are refining a machine-checkable intent for a software fleet. Reply with JSON only, "
            "using the same keys and the same gate ids as the draft. You may improve titles, "
            "responsibilities and acceptance statements. You may not invent gate ids, remove a gate, "
            "or move a file between components' write scopes.\n\n"
            f"## Intent document\n{text}\n\n## Draft\n{json.dumps(draft, indent=2)}"
        )
        body = json.dumps(
            {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": "Reply with JSON only, no prose."},
                    {"role": "user", "content": prompt},
                ],
                "response_format": {"type": "json_object"},
            }
        ).encode()
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=body,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"},
        )
        with urllib.request.urlopen(request, timeout=300) as response:
            payload = json.loads(response.read())
        refined = json.loads(payload["choices"][0]["message"]["content"])
        validate_intent(refined)
        return refined


def compile_text(text: str, backend: str = "rules", refiner: HttpRefiner | None = None) -> Compilation:
    compilation = compile_document(text)
    if backend == "rules":
        return compilation
    if backend != "llm":
        raise CompileError(f"unknown backend {backend!r}")
    refiner = refiner or HttpRefiner()
    refined = refiner.refine(compilation.data, text)
    notes = list(compilation.notes) + [
        f"refined by {refiner.model}; re-validated against the intent rules before use"
    ]
    refined["compile_notes"] = notes
    return Compilation(data=refined, notes=notes, backend="llm")


def compile_path(path: Path | str, backend: str = "rules") -> Compilation:
    return compile_text(Path(path).read_text(), backend=backend)
