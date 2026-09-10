# Post-run audit - MiniFleet v0.2

Written by the fleet operator after the run, not by the agents that did the work. The rule
the run itself enforces applies to the run: **a result is only worth what an independent
re-execution of it is worth.** Nothing below is taken from `report.md`.

## 1. Rebuild and re-grade the integrated tree

The run's product tree was cloned from its `integration` branch into a fresh directory, and
the gates were re-executed there rather than read out of the ledger.

```
git clone --branch integration <run>/product /tmp/verify/repo
python3 -m unittest harness.test_contract   # 11 tests, OK
python3 -m unittest harness.test_wiring     #  4 tests, OK
python3 -m unittest discover -s tests       # 48 tests, OK
python3 -m harness.bench                    # MINIFLEET_METRIC engine_tests_ms=6736.0 (budget 60000)
```

The frozen harness was also compared byte-for-byte against the baseline commit it was frozen
from: `test_contract.py`, `test_wiring.py`, `bench.py` and `__init__.py` are identical, so the
criteria could not have been edited to fit the result.

The same gates were then re-run on the landed `main` commit (`ab7ff45`), where the suite is
49 tests: all green, and the new CI job `self-hosting` rebuilds that tree from scratch on
every push.

## 2. Drive the three new capabilities by hand

Unit tests can pass while the CLI path is broken, so each capability was also used for real.

| capability | what was run | what came back |
| --- | --- | --- |
| `repair` | `python3 -m minifleet repair --run <copy of harness/fixtures/failing-run>` - a directory with a `run.json` and no git repository | one packet for `T-e`, `failing_gates=[G-unit]`, `scope=[e.py]`, `escalate=false` at attempt 2/3, inputs untouched |
| `deploy` | `python3 -m minifleet deploy --dir examples/linksvc --cmd "python3 -m linksvc --port 0 --db ..." --probe /healthz --seconds 3` | a real child process, ready line parsed (`port=36461`), smoke `200`, soak 13 samples / 0 failures, `p50_ms=1.75`, teardown exit code 0, no stray process |
| `fleetmetrics` | `python3 -m minifleet fleetmetrics --run <this run>` | 4 tasks, 4 merged, 0 failed, 10 gate observations, 0 retries, critical path 153.677 s, verdict `pass: all required gates green` |

The deploy probe is the point of the exercise: the artefact it supervised is the short-link
service that the *first* fleet run produced from an intent, months-to-minutes earlier, so the
chain intent -> built system -> running system -> observed system is closed end to end.

## 3. The defect the run could not see

The run reported that it had "adopted its own tree". The audit compared the adopted baseline
with the repository it was adopted from, file by file, by git blob hash.

```
Product baseline (commit 812061c)          Repository baseline (commit fbf1538)
tests/test_engine.py   334 lines           tests/test_engine.py   365 lines
                       17 test methods                             18 test methods
```

Every other path is byte-identical, including the files the fleet owns and rewrites
(`ACCEPTANCE.md`, `INTENT.md`, `contracts/C-V02.md`, `harness/`). The single difference is
`test_brownfield_baseline_is_adopted_without_clobbering`, the regression test for the very
capability this run depended on: it is present in the repository and absent from the tree the
run adopted.

Consequences, stated honestly:

- The run's "regression floor" was one test weaker than the repository's real suite. Nothing
  broke - the full suite passes on the landed tree - but the run's claim to have preserved
  the existing behaviour was enforced against an incomplete copy of it.
- `baseline_from` read the **working copy**, not a revision. A working copy can be dirty,
  half-written, or shared with another process; on this machine it evidently was, and the
  fleet adopted the drift silently because nothing recorded which revision it had adopted.

The fix is not to trust the working copy less carefully, it is to stop treating a working
copy as the baseline at all: `adopt_baseline()` now materialises `HEAD` through `git archive`
when the source is a repository (falling back to the filesystem only for a plain directory),
records `{mode, source, revision, dirty, files}` in `baseline.json` and in a
`baseline.adopted` ledger event, and refuses to hide the day the working copy diverges.
`WorktreeTests.test_brownfield_adoption_pins_a_revision_instead_of_the_working_copy` reproduces
the original failure: a committed `app.py`, a dirty edit to it, and an untracked scratch file
- the adopted tree is the revision, not the disk.

## What is still not proven

- The v0.2 run itself was not repeated on the fixed adoption path. The fix changes how a
  baseline is *chosen*, not how tasks are built, so it was verified by its own test, by the
  full engine suite, and by a fresh brownfield `plan` against this repository, which now
  adopts revision `ab7ff45` and says so.
- The dirty working copy that caused the drift was never identified. The audit narrows it to
  "something modified `tests/test_engine.py` in the shared checkout between the brownfield
  commit and the run's planning step" and stops there rather than guessing.
- `deploy` supervision is local-process supervision. It proves the artefact starts, answers
  and stops; it says nothing about a container runtime, a scheduler, or a cloud provider.
