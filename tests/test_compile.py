"""The front door: prose must compile into something the fleet can actually execute."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from minifleet import architect, checks, cli, compile as compile_mod, intent as intent_mod

DOCUMENT = """# Swarm: a webhook relay

Relay webhooks with retries and a dead-letter queue. The service must answer in under
50 ms and sustain at least 200 requests per second.

## Deliverables

- relay/store.py - durable delivery state
- relay/worker.py - retry loop and backoff | depends: store
- tests/test_store.py - store tests
- tests/test_worker.py - worker tests

## Acceptance

- [harness] a delivery survives a restart
- [regression] the engine suite still passes
- [policy] no third-party import sneaks in

## Constraints

- Python 3.12 standard library only

## Out of scope

- multi-region failover

## Budgets

- relay_p99_ms <= 250

## Probes

- relay_p99_ms: python3 harness/perf_probe.py

## Meta

- id: swarm-relay
- harness_dir: harness-relay
- max_attempts: 2
- max_dispatches: 8
"""


class ParsingTest(unittest.TestCase):
    def test_document_compiles_into_a_dispatchable_design(self):
        compilation = compile_mod.compile_document(DOCUMENT)
        data = compilation.data
        self.assertEqual(data["id"], "swarm-relay")
        self.assertEqual(sorted(c["id"] for c in data["components"]), ["store", "worker"])
        scopes = {c["id"]: c["owns"] for c in data["components"]}
        self.assertEqual(scopes["store"], ["relay/store.py", "tests/test_store.py"])
        self.assertEqual(scopes["worker"], ["relay/worker.py", "tests/test_worker.py"])
        self.assertEqual(
            next(c for c in data["components"] if c["id"] == "worker")["depends_on"], ["store"]
        )

        # The design review must accept what the compiler produced.
        design = architect.design(compilation.spec)
        self.assertEqual(design.repairs, [])
        self.assertEqual(len(design.tasks), 2)

    def test_gates_are_derived_from_the_document(self):
        data = compile_mod.compile_document(DOCUMENT).data
        ids = {gate["id"] for gate in data["gates"]}
        self.assertEqual(
            ids,
            {
                "G-unit",
                "G-harness",
                "G-stdlib-only",
                "G-regression",
                "G-probe-relay-p99-ms",
                "G-budget-relay-p99-ms",
                "G-layout",
            },
        )
        harness = next(gate for gate in data["gates"] if gate["id"] == "G-harness")
        self.assertEqual(harness["scope"], "system")
        self.assertEqual(harness["attributed_to"], ["store", "worker"])
        unit = next(gate for gate in data["gates"] if gate["id"] == "G-unit")
        self.assertEqual(unit["scope"], "task")

    def test_acceptance_tags_bind_criteria_to_gates(self):
        data = compile_mod.compile_document(DOCUMENT).data
        mapping = {c["id"]: c["gates"] for c in data["acceptance"]}
        self.assertEqual(mapping["A-1"], ["G-harness"])
        self.assertEqual(mapping["A-2"], ["G-regression"])
        self.assertEqual(mapping["A-3"], ["G-stdlib-only"])

    def test_a_harness_section_carries_its_directory_into_the_baseline(self):
        # The `## Harness` section names the gates *and* the directory they live in.
        # The directory is what `plan` copies into the product baseline, so declaring
        # the gates without it would ship gates for a module that never arrives.
        document = DOCUMENT.replace("- harness_dir: harness-relay\n", "")
        document = document.replace(
            "## Meta",
            "## Harness\n\n- dir: harness-relay\n- modules: test_accept, test_crash\n\n## Meta",
        )
        data = compile_mod.compile_document(document).data
        ids = {gate["id"] for gate in data["gates"]}
        self.assertIn("G-harness-accept", ids)
        self.assertIn("G-harness-crash", ids)
        self.assertEqual(data["harness_dir"], "harness-relay")
        self.assertEqual(intent_mod.IntentSpec.from_dict(data).harness_dir, "harness-relay")

    def test_unknown_tag_is_a_compile_error(self):
        document = DOCUMENT.replace("[policy]", "[telepathy]")
        with self.assertRaises(compile_mod.CompileError):
            compile_mod.compile_document(document)

    def test_prose_budgets_are_suggested_not_silently_enforced(self):
        compilation = compile_mod.compile_document(DOCUMENT)
        self.assertIn("relay_p99_ms", compilation.data["budgets"])
        self.assertNotIn("throughput_rps", compilation.data["budgets"])
        self.assertTrue(any("prose suggested budgets" in note for note in compilation.notes))
        self.assertTrue(
            any("throughput_rps" in note or "latency_under_ms" in note for note in compilation.notes)
        )

    def test_a_budget_without_a_probe_is_called_out(self):
        document = DOCUMENT.replace("## Probes\n\n- relay_p99_ms: python3 harness/perf_probe.py\n\n", "")
        compilation = compile_mod.compile_document(document)
        self.assertTrue(any("relay_p99_ms" in note and "probe" in note for note in compilation.notes))

    def test_limits_are_parsed_and_validated(self):
        data = compile_mod.compile_document(DOCUMENT).data
        self.assertEqual(data["limits"], {"max_attempts": 2.0, "max_dispatches": 8.0})
        broken = dict(data, limits={"max_unicorns": 3.0})
        with self.assertRaises(intent_mod.IntentError):
            intent_mod.validate(intent_mod.IntentSpec.from_dict(broken))

    def test_missing_sections_are_refused(self):
        with self.assertRaises(compile_mod.CompileError):
            compile_mod.compile_document("# Nothing\n\nno sections at all\n")
        with self.assertRaises(compile_mod.CompileError):
            compile_mod.compile_document(DOCUMENT.replace("## Acceptance", "## Vibes"))

    def test_a_dependency_on_an_unknown_component_is_refused(self):
        document = DOCUMENT.replace("| depends: store", "| depends: datalake")
        with self.assertRaises(compile_mod.CompileError):
            compile_mod.compile_document(document)

    def test_llm_backend_without_a_model_is_an_explicit_failure(self):
        refiner = compile_mod.HttpRefiner(base_url="", api_key="")
        self.assertFalse(refiner.available())
        with self.assertRaises(compile_mod.CompileError):
            compile_mod.compile_text(DOCUMENT, backend="llm", refiner=refiner)

    def test_llm_backend_revalidates_what_it_was_given(self):
        class BadRefiner(compile_mod.HttpRefiner):
            def available(self) -> bool:
                return True

            def refine(self, draft, text):  # noqa: ANN001
                broken = dict(draft)
                broken["acceptance"] = []          # an intent nobody can falsify
                compile_mod.validate_intent(broken)
                return broken

        with self.assertRaises(compile_mod.CompileError):
            compile_mod.compile_text(DOCUMENT, backend="llm", refiner=BadRefiner())


class HarnessSectionTest(unittest.TestCase):
    """A `## Harness` section is the single place the frozen harness is declared."""

    SECTIONED = DOCUMENT.replace(
        "## Meta\n\n- id: swarm-relay\n- harness_dir: harness-relay\n",
        "## Harness\n\n- dir: harness-relay\n- modules: test_accept, test_restart\n\n"
        "## Meta\n\n- id: swarm-relay\n",
    )

    def test_the_section_directory_becomes_the_harness_dir(self):
        compilation = compile_mod.compile_document(self.SECTIONED)
        self.assertEqual(compilation.data["harness_dir"], "harness-relay")
        ids = {gate["id"] for gate in compilation.data["gates"]}
        self.assertTrue({"G-harness-accept", "G-harness-restart"} <= ids)
        self.assertTrue(any("## Harness" in note for note in compilation.notes))

    def test_the_meta_key_still_wins_when_both_are_present(self):
        both = self.SECTIONED.replace(
            "- id: swarm-relay\n", "- id: swarm-relay\n- harness_dir: elsewhere\n"
        )
        self.assertEqual(compile_mod.compile_document(both).data["harness_dir"], "elsewhere")

    def test_the_frozen_harness_is_copied_into_the_baseline(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            frozen = root / "harness-relay"
            frozen.mkdir()
            (frozen / "test_accept.py").write_text("# frozen by the harness owner\n")
            intent_path = root / "intent.md"
            intent_path.write_text(self.SECTIONED)
            spec = intent_mod.IntentSpec.from_dict(
                compile_mod.compile_document(self.SECTIONED).data
            )
            files = cli.harness_files(str(intent_path), spec)
            self.assertEqual(
                files, {"harness/test_accept.py": "# frozen by the harness owner\n"}
            )


class PolicyCheckTest(unittest.TestCase):
    def test_stdlib_only_flags_a_third_party_import(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "pkg"
            root.mkdir()
            (root / "clean.py").write_text("import json\nfrom pathlib import Path\n")
            (root / "dirty.py").write_text("import requests\n")
            violations = checks.stdlib_only([str(root)], allow=[], project=["pkg"])
            self.assertEqual(len(violations), 1)
            self.assertIn("requests", violations[0])

    def test_stdlib_only_accepts_the_projects_own_package(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "pkg"
            root.mkdir()
            (root / "mod.py").write_text("from pkg import other\nimport json\n")
            self.assertEqual(checks.stdlib_only([str(root)], allow=[], project=["pkg"]), [])

    def test_no_network_honours_the_allowlist(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "pkg"
            root.mkdir()
            probe = root / "probe.py"
            probe.write_text("import urllib.request\n")
            self.assertEqual(len(checks.no_network([str(root)], allow=[])), 1)
            self.assertEqual(checks.no_network([str(root)], allow=[str(probe)]), [])


if __name__ == "__main__":
    unittest.main()
