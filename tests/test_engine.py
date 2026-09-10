"""Engine tests: the control plane must be trustworthy before it is useful."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from minifleet import architect, intent as intent_mod, ledger, report, worktree as wt
from minifleet.gates import GateRunner, MetricStore, parse_metrics, verdict
from minifleet.model import Evidence, Gate, Task, TaskState
from minifleet.scheduler import Scheduler
from minifleet.workers import DispatchResult, Packet


def _intent(**overrides):
    base = {
        "id": "demo",
        "title": "Demo",
        "summary": "a tiny system used to exercise the engine",
        "deliverables": ["calc module"],
        "constraints": ["stdlib only"],
        "out_of_scope": ["anything else"],
        "budgets": {"unit_ms": 500.0},
        "acceptance": [
            {"id": "A-1", "statement": "calc.add adds", "gates": ["G-accept"]},
            {"id": "A-2", "statement": "the module has tests", "gates": ["G-unit"]},
            {"id": "A-3", "statement": "unit suite stays fast", "gates": ["G-budget"]},
        ],
        "gates": [
            {"id": "G-unit", "title": "unit", "kind": "cmd", "scope": "task",
             "cmd": "python3 -m unittest discover -s tests -v"},
            {"id": "G-accept", "title": "acceptance", "kind": "cmd", "scope": "system",
             "cmd": "python3 -m unittest harness.test_accept -v"},
            {"id": "G-budget", "title": "budget", "kind": "budget", "scope": "system",
             "metric": "unit_ms", "op": "<=", "threshold": 500.0},
            {"id": "G-perf", "title": "measure unit cost", "kind": "cmd", "scope": "system",
             "cmd": "python3 -m harness.perf_probe"},
            {"id": "G-files", "title": "layout", "kind": "files", "scope": "system",
             "paths": ["calc/__init__.py"]},
        ],
        "components": [
            {"id": "calc", "name": "Calculator", "responsibility": "expose add()",
             "owns": ["calc/", "tests/test_calc.py"],
             "acceptance_ids": ["A-1", "A-2"], "gate_ids": ["G-unit"],
             "done_when": ["add returns the sum"], "tests": ["add(2,2)==4"]},
        ],
    }
    base.update(overrides)
    return base


HARNESS = {
    "harness/__init__.py": "",
    "harness/test_accept.py": (
        "import unittest\n"
        "from calc import add\n\n"
        "class Accept(unittest.TestCase):\n"
        "    def test_add(self):\n"
        "        self.assertEqual(add(2, 2), 4)\n"
    ),
    "harness/perf_probe.py": (
        "import json, time\n"
        "from calc import add\n"
        "start = time.perf_counter()\n"
        "for _ in range(2000):\n"
        "    add(1, 2)\n"
        "elapsed = (time.perf_counter() - start) * 1000\n"
        "print('MINIFLEET_METRIC ' + json.dumps({'name': 'unit_ms', 'value': round(elapsed, 3)}))\n"
    ),
}

MODULE_FILES = {
    "calc/__init__.py": "from .core import add\n\n__all__ = ['add']\n",
    "calc/core.py": "def add(a, b):\n    return a + b\n",
    "tests/test_calc.py": (
        "import unittest\n"
        "from calc import add\n\n"
        "class Calc(unittest.TestCase):\n"
        "    def test_add(self):\n"
        "        self.assertEqual(add(1, 2), 3)\n"
    ),
}


class FakeDispatcher:
    """Deterministic stand-in for an agent runtime, used to test the control plane."""

    name = "fake"

    def __init__(self, files_by_task: dict[str, dict[str, str]]):
        self.files_by_task = files_by_task
        self.seen: list[str] = []

    def dispatch(self, packet: Packet, packet_path: Path) -> DispatchResult:
        self.seen.append(packet.task_id)
        files = self.files_by_task.get(packet.task_id, {})
        for rel, content in files.items():
            target = Path(packet.worktree) / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
        return DispatchResult(
            task_id=packet.task_id,
            status="submitted",
            worker="fake",
            files=sorted(files),
        )


class IntentTests(unittest.TestCase):
    def test_unknown_gate_reference_is_fatal(self):
        data = _intent()
        data["acceptance"][0]["gates"] = ["G-missing"]
        with self.assertRaises(intent_mod.IntentError):
            intent_mod.compile_dict(data)

    def test_budget_without_gate_is_rejected_by_helper(self):
        data = _intent(budgets={"unit_ms": 500.0, "orphan_ms": 10.0})
        spec = intent_mod.compile_dict(data)
        with self.assertRaises(intent_mod.IntentError):
            intent_mod.merge_budget_gates(spec)

    def test_overlapping_write_scope_is_fatal(self):
        data = _intent()
        data["components"].append(
            {"id": "other", "name": "Other", "responsibility": "clash",
             "owns": ["calc/__init__.py"]}
        )
        with self.assertRaises(intent_mod.IntentError):
            intent_mod.compile_dict(data)


class ArchitectTests(unittest.TestCase):
    def test_missing_gate_is_repaired_and_recorded(self):
        data = _intent()
        data["acceptance"].append({"id": "A-4", "statement": "no gate was declared", "gates": []})
        spec = intent_mod.compile_dict(data)
        plan = architect.design(spec)
        self.assertTrue(any("A-4" in r for r in plan.repairs))
        self.assertTrue(any(g.id == "G-probe-A-4" for g in plan.gates))

    def test_cycle_is_repaired(self):
        data = _intent()
        data["components"] = [
            {"id": "a", "name": "A", "responsibility": "a", "owns": ["a.py"], "depends_on": ["b"]},
            {"id": "b", "name": "B", "responsibility": "b", "owns": ["b.py"], "depends_on": ["a"]},
        ]
        spec = intent_mod.compile_dict(data)
        plan = architect.design(spec)
        self.assertEqual(architect.find_cycles(plan.tasks), [])
        self.assertTrue(any("cycle" in r for r in plan.repairs))

    def test_batches_respect_dependencies(self):
        tasks = [
            Task(id="T1", title="a", role="builder", goal="a"),
            Task(id="T2", title="b", role="builder", goal="b", depends_on=["T1"]),
            Task(id="T3", title="c", role="builder", goal="c", depends_on=["T1"]),
        ]
        batches = architect.topological_batches(tasks)
        self.assertEqual([[t.id for t in b] for b in batches], [["T1"], ["T2", "T3"]])


class GateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = MetricStore(self.root / "metrics.json")
        self.runner = GateRunner(self.root, self.store, self.root / "evidence")

    def tearDown(self):
        self.tmp.cleanup()

    def test_cmd_gate_captures_metrics(self):
        gate = Gate(
            id="G1", title="emit",
            cmd="python3 -c \"print('MINIFLEET_METRIC {\\\"name\\\": \\\"x\\\", \\\"value\\\": 3}')\"",
        )
        evidence = self.runner.run(gate)
        self.assertEqual(evidence.status, "pass")
        self.assertEqual(evidence.metrics["x"], 3.0)

    def test_budget_gate_fails_when_metric_missing(self):
        gate = Gate(id="G2", title="budget", kind="budget", metric="nope", op="<=", threshold=1)
        evidence = self.runner.run(gate)
        self.assertEqual(evidence.status, "error")

    def test_files_and_json_gates(self):
        (self.root / "artifact.json").write_text(json.dumps({"ok": True}))
        self.assertEqual(self.runner.run(Gate(id="G3", title="f", kind="files", paths=["artifact.json"])).status, "pass")
        self.assertEqual(self.runner.run(Gate(id="G4", title="f", kind="files", paths=["missing.json"])).status, "fail")
        self.assertEqual(
            self.runner.run(Gate(id="G5", title="j", kind="json", paths=["artifact.json"], json_path="ok", json_expected=True)).status,
            "pass",
        )

    def test_metric_parser_ignores_noise(self):
        out = 'noise\nMINIFLEET_METRIC {"name": "a", "value": 1.5}\nMINIFLEET_METRIC not-json\n'
        self.assertEqual(parse_metrics(out), {"a": 1.5})

    def test_verdict_requires_all_required_evidence(self):
        evidence = [Evidence(gate_id="G1", status="pass")]
        self.assertTrue(verdict(evidence, {"G1"}).startswith("pass"))
        self.assertTrue(verdict(evidence, {"G1", "G2"}).startswith("fail"))


class WorktreeTests(unittest.TestCase):
    def test_ownership_conflict_detection(self):
        conflicts = wt.ownership_conflicts({"a": ["x.py", "y.py"], "b": ["y.py", "z.py"]})
        self.assertEqual(conflicts, [("a", "b", ["y.py"])])


class EndToEndTests(unittest.TestCase):
    def test_full_run_passes_and_is_auditable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            intent_path = root / "intent.json"
            intent_path.write_text(json.dumps(_intent(harness_dir="harness"), indent=2))
            harness = root / "harness"
            harness.mkdir()
            for rel, content in HARNESS.items():
                target = root / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content)

            spec = intent_mod.load(intent_path)
            run, run_dir = ledger.create(root / "runs", spec, "fake", raw_intent="build a calculator")
            design = architect.design(spec)
            run.set_design(design)
            scheduler = Scheduler(run, run_dir, spec, design)

            from minifleet.cli import baseline_files, harness_files

            baseline = baseline_files(spec, design)
            baseline.update(harness_files(str(intent_path), spec))
            scheduler.repo.init(baseline)
            scheduler.repo.ensure_branch(run.integration_branch)
            scheduler.refresh()

            dispatcher = FakeDispatcher({"T-calc": MODULE_FILES})
            results = scheduler.dispatch(dispatcher)
            self.assertEqual(results[0]["status"], "submitted")

            outcome = scheduler.ingest("T-calc", worker="fake")
            self.assertEqual(outcome["status"], "verified", outcome)

            integration = scheduler.integrate()
            self.assertEqual(integration["status"], "ok", integration)

            verification = scheduler.verify()
            self.assertTrue(verification["verdict"].startswith("pass"), verification)

            outputs = report.write(run, run_dir)
            markdown = Path(outputs["markdown"]).read_text()
            self.assertIn("Traceability", markdown)
            self.assertIn("Every acceptance criterion has passing evidence", markdown)

            rows = ledger.traceability(spec, design, scheduler.all_evidence())
            self.assertEqual({r["status"] for r in rows}, {"verified"})

    def test_scope_violation_is_caught_before_merge(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            intent_path = root / "intent.json"
            intent_path.write_text(json.dumps(_intent()))
            spec = intent_mod.load(intent_path)
            run, run_dir = ledger.create(root / "runs", spec, "fake")
            design = architect.design(spec)
            run.set_design(design)
            scheduler = Scheduler(run, run_dir, spec, design)
            scheduler.repo.init({})
            scheduler.refresh()
            scheduler.packets()
            task = scheduler.task("T-calc")
            rogue = Path(task.worktree) / "somebody_elses_file.py"
            rogue.write_text("# not mine\n")
            outcome = scheduler.ingest("T-calc")
            self.assertEqual(outcome["status"], "failed")
            self.assertIn("outside its scope", outcome["detail"])
            self.assertEqual(task.state, TaskState.FAILED.value)


if __name__ == "__main__":
    unittest.main()
