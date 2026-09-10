# MiniFleet

MiniFleet turns a human **intent** into a **verified, running system**. It is a small,
dependency-free replica of the idea behind an autonomous engineering fleet: a planner
compiles the intent, an architect freezes interfaces and splits the work into
single-writer tasks, isolated agent workers build those tasks in parallel, and nothing
is admitted to production until machine-checkable gates have produced evidence.

The design rule that shapes everything else: **a worker never grades its own homework**.
The acceptance harness is committed to the repository before the first worker is
dispatched, and the final report can only assert what a gate actually observed.

```
intent.json ──▶ intent compiler ──▶ architect ──▶ task graph ──▶ scheduler
                    (validation)     (self-repair)                    │
                                                                      ▼
                                            isolated git worktree per worker
                                                                      │
                                        task gates ──▶ integrate ──▶ system gates
                                                                      │
                                                                      ▼
                                                    ledger ──▶ report (intent → evidence)
```

## What it actually enforces

| Guarantee | Mechanism |
| --- | --- |
| The intent is falsifiable | every acceptance criterion must name a gate; unknown gate references are fatal errors |
| The plan is executable | dependency cycles, missing write scopes and uncovered criteria are repaired and the repair is recorded |
| Two workers never collide | each task owns a disjoint write scope; the scheduler refuses to merge overlapping branches |
| Work is isolated | one `git worktree` and one branch per task, all created from the frozen baseline |
| Nothing is self-certified | the acceptance harness and the acceptance statement live in the frozen baseline, outside every worker's write scope |
| Claims are auditable | every gate writes an evidence record; the report is generated from the run record and marks unproven criteria explicitly |
| Unmeasured means unverified | a budget gate whose metric was never produced is an **error**, not a pass |

## Quick start

```bash
python3 -m minifleet plan --intent intents/linksvc.json --runs-dir runs
python3 -m minifleet dispatch --run runs/<id>            # writes one packet per ready task
python3 -m minifleet ingest  --run runs/<id> --task T-storage
python3 -m minifleet integrate --run runs/<id>
python3 -m minifleet verify  --run runs/<id>
python3 -m minifleet report  --run runs/<id>
python3 -m minifleet fleetmetrics --run runs/<id>        # what the run cost, gate by gate
python3 -m minifleet repair  --run runs/<id>             # bounded next attempt for failed tasks
python3 -m minifleet deploy --dir <product> --cmd "python3 -m linksvc" --probe /healthz --seconds 5
```

`status` shows the state of every task and the exact next action for each one. The same
pipeline can be driven end to end for dispatchers that run workers themselves:

```bash
python3 -m minifleet run --intent intents/linksvc.json --dispatcher "command:./examples/codex_worker.sh"
```

## Dispatchers

### Brownfield intents

An intent may declare `"baseline_from": "<path>"` instead of starting from an empty repository. The
fleet then adopts that existing tree as the product baseline - it copies the codebase, commits it
untouched, and only then lays out worktrees. Generated artefacts (`ACCEPTANCE.md`, `INTENT.md`,
`contracts/`, `harness/`) are fleet-owned and may overwrite; everything else in the adopted
repository is preserved, which is what lets the fleet work on its own source code.

A dispatcher is how a task packet reaches an agent runtime. The packet is deliberately
self-contained, so workers can be different models, different vendors, or different CLIs.

| Dispatcher | Use |
| --- | --- |
| `packet` (default) | write the packet to disk and hand the run to any external runtime or human |
| `command:<cmd>` | invoke an agent CLI as `<cmd> <packet.md> <result.json>`; see `examples/codex_worker.sh` |
| `http` | single-shot file materialisation through any OpenAI-compatible endpoint (`MINIFLEET_LLM_BASE_URL`, `MINIFLEET_LLM_API_KEY`, `MINIFLEET_LLM_MODEL`) |

## Verification gates

Four kinds, all declarative in the intent:

* `cmd` — run a command in the workspace; the exit code decides. Commands publish metrics
  by printing `MINIFLEET_METRIC {"name": "...", "value": 1.23}`.
* `files` — assert that paths exist, or (`!path`) that they must not.
* `json` — assert a dotted path inside a JSON artefact.
* `budget` — enforce a non-functional budget on a metric captured earlier. Budget gates are
  evaluated last, after every probe has reported.

## Why the demo target is small and strict

`intents/linksvc.json` asks for a link shortener with a deliberately unforgiving
definition of done: exact click counts under eight concurrent writers, no data loss after
`SIGKILL`, `SIGTERM` exiting 0, a p99 redirect latency budget and a throughput budget.
The acceptance harness (`intents/harness-linksvc/`) is written by the verification layer,
not by the workers, and is committed before they start.

## The front door: a prose intent compiles into a checkable spec

An intent written by hand is an intent nobody writes twice. `minifleet compile` reads a short
Markdown document and derives the mechanical parts - components and their disjoint write scopes
from the deliverables, the gate set from the gate library, the binding from each acceptance
criterion to the gate that will judge it, budget gates from declared metrics, and policy gates
such as "standard library only" from the constraints:

```bash
python3 -m minifleet compile --text intents/quarantine.md --out intents/quarantine.compiled.json
python3 -m minifleet plan    --intent intents/quarantine.compiled.json
```

A constraint becomes a gate with teeth: `standard library only` is enforced by an import scan
(`python3 -m minifleet.checks stdlib-only`), and an exception has to be declared in the intent
rather than quietly weakening the check.

Autonomy is bounded by the same document: `max_attempts`, `max_rounds`, `max_dispatches` and
`max_wall_seconds` under `## Meta` are enforced by the scheduler and the autopilot, which stop
and say which budget ran out instead of looping unattended.

## The repair loop, tested with an injected fault

`experiments/fault-injection/` compiles a prose intent, then runs the fleet against the engine's own repository
and injects a defect that no task gate can see: the worker's own tests pass, and the
frozen harness fails. The run then has to detect it, attribute it to the component
that caused it, hand the next attempt an actionable packet, and re-verify.

```bash
python3 experiments/fault-injection/run_experiment.py
```

- rendered result: <https://singletonye.github.io/minifleet/fault-injection.html>
- the run's own traceability report: <https://singletonye.github.io/minifleet/fault-injection-run.html>
- the raw record a sceptic needs: [`experiments/fault-injection/out/audit/`](experiments/fault-injection/out/audit/)

## One real run

[`examples/linksvc/`](examples/linksvc/) is not an illustration: it is the tree the fleet
actually produced and admitted, and [`evidence/linksvc-run/`](evidence/linksvc-run/) is the
record of that run - the frozen packets handed to each worker, the per-gate evidence, the
append-only ledger and the generated report.

The rendered report is published at
<https://singletonye.github.io/minifleet/> (mirror: <https://minifleet-run-report.okou.app>),
and its source is in [`docs/`](docs/).

| what the fleet did | where it is recorded |
| --- | --- |
| intent compiled, three tasks planned, two dispatched in parallel | `evidence/linksvc-run/ledger.jsonl` |
| one self-contained packet per task, each with a disjoint write scope | `evidence/linksvc-run/packets/` |
| worker output committed only after an ownership check | `evidence/linksvc-run/dispatch.json` |
| eight gates judged the integrated tree, budgets last | `evidence/linksvc-run/evidence/` |
| intent -> evidence traceability for all ten criteria | `evidence/linksvc-run/report.md` |

The run is worth reading for the two failures in it, because they are the point:

1. **A gate definition was wrong.** `G-unit` originally ran
   `unittest discover -s tests -t .`, which cannot import a `tests/` directory that
   no task owns. The operator found it while dispatching, corrected the frozen intent
   and re-planned, before any worker output existed to contaminate.
2. **The acceptance harness was wrong.** The first verification pass failed
   `G-durability`, and the harness was at fault: it re-clicked a link after the restart
   and then asserted the pre-crash click count. The assertion was corrected to check
   `clicks == 1` before the new clicks and `clicks == 2` after them. Both verdicts are
   still in the ledger - a corrected harness does not erase the failure it caused.

The final numbers, from `evidence/linksvc-run/report.md`: 320/320 clicks counted under
8 parallel writers, 40/40 links and the click counter intact after `SIGKILL`, `SIGTERM`
exit status 0, redirect p99 `29.9 ms` against a `75 ms` budget, and `928 req/s` against a
`200 req/s` budget.

## The follow-up wave (evolution)

After the system was admitted, a second intent (`intents/linksvc-v2.json`) asked for
something new: `GET /links/top?limit=N`, the most-clicked links. `minifleet evolve` diffed
the intent, reported one added component and one changed component, and reopened exactly
that work - `T-analytics` and `T-http` - while leaving the two verified tasks merged. The
ten original criteria stayed in the run as the regression suite, and the new criterion
`A-11` got its own frozen harness (`intents/harness-linksvc/test_analytics.py`) written
before the analytics worker was dispatched. `evidence/linksvc-run/evolution.json` is the
delta that was computed.

That wave found a third defect, this time in the engine. Dispatching a *reopened* task
reused its old branch, so the worker was handed a tree from before the merge - without
the contracts and harness files frozen since. `worktree.attempt_branch` now detects a
branch that is already contained in the baseline and starts a fresh attempt branch from
the current baseline instead; `tests/test_engine.py` pins the behaviour.

## v0.2: the fleet upgrades itself (brownfield self-hosting)

The third run does not build a new service. It hands the fleet **its own repository** and
asks it for the three capabilities that were still missing, which is the closest thing
here to the product claim: a system that can be pointed at an existing codebase and left
to change it under its own rules.

Two things had to become real for that to work.

**Brownfield baselines.** An intent can name `baseline_from`, and the run adopts that tree
as its product instead of writing a fresh one. Fleet-owned paths (`ACCEPTANCE.md`,
`INTENT.md`, `contracts/`, `harness/`) still overwrite, everything else is left exactly as
it was - the fleet may add to a codebase, but it may not silently clobber it. The new
acceptance harness is frozen into the adopted tree *before* any worker is dispatched, and
the regression suite that shipped with the repository is part of the definition of done.
Adoption is **pinned to a revision**: when the source is a git repository the planner
materialises `HEAD` rather than copying the working copy, and it records the revision and
the file count in `baseline.json` plus a `baseline.adopted` ledger event (a dirty working
copy is recorded, not adopted). Untracked files are not part of a baseline, so commit what
you want the fleet to start from.

**Autonomy on failure, deployment, and self-accounting.** Three modules, all reachable
from the CLI, all judged by gates the workers did not write:

| capability | module | what a gate actually checked |
| --- | --- | --- |
| bounded self-repair | `minifleet/repair.py` | failing gates only, ordered by gate id, instructions that name them, `escalate` set once the attempt budget is spent, inputs unmutated |
| deploy and observe | `minifleet/deploy.py` | a real child process started, its ready line parsed for the port, a timeout that does not hang, `SIGTERM` then `SIGKILL`, smoke and soak probes that report samples, failures and percentiles |
| fleet accounting | `minifleet/fleetmetrics.py` | tasks, merged, failed, attempts, retries, gate outcomes, and the critical path - the longest chain through `depends_on`, not the sum of all durations |

A failed task now leaves a repair packet behind (`packets/<task>.repair.json`, recorded in
the ledger by `task.repair_packet`); `minifleet repair` regenerates those packets from any
run record, including one that is nothing but `run.json` and no git repository at all.

The run is recorded in [`evidence/selfhost-run/`](evidence/selfhost-run/) and its rendered
report is at <https://singletonye.github.io/minifleet/selfhost.html>.

| what the fleet did | where it is recorded |
| --- | --- |
| adopted its own tree as the product, then dispatched three tasks in parallel | `evidence/selfhost-run/ledger.jsonl` |
| each task packet, with its disjoint write scope and the frozen `C-V02` contract | `evidence/selfhost-run/packets/` |
| the fourth task (wiring) dispatched only after the first three merged | `evidence/selfhost-run/dispatch.json` |
| ten gate observations on the integrated tree, budgets last | `evidence/selfhost-run/evidence/` |
| all eight criteria traced to a passing gate | `evidence/selfhost-run/report.md` |

`fleetmetrics` on that run: 4 tasks, 4 merged, 0 failed, 10 gate observations, 0 retries,
critical path 153.7 s, and the regression suite at 6.6 s against a 60 s budget - the
measurement is printed by `harness/bench.py` as `MINIFLEET_METRIC`, so the budget gate
reads the same number the report shows.

**What the post-run audit found.** The run's own gates were green, so the operator re-verified
it from a fresh clone instead of reading the report: the frozen harness (11 contract + 4
wiring tests), the regression suite, the layout and budget gates, and each new capability
driven by hand - `repair` against a run record with no git repository, `deploy` against the
short-link service the *first* run produced, `fleetmetrics` against this run. All of it
reproduced. The audit also found a defect no gate in that run could have caught: the adopted
baseline was **one test short** of the revision its intent pointed at, because adoption
copied whatever was on disk. Adoption now pins `HEAD` and records the revision, and
`tests/test_engine.py` pins the behaviour. The audit record, including the exact commands
and the discrepancy, is in [`evidence/selfhost-run/AUDIT.md`](evidence/selfhost-run/AUDIT.md).

## Repository layout

```
minifleet/           the engine
  intent.py          intent compiler and validation
  architect.py       design, dependency graph, self-repair, design review
  scheduler.py       waves, dispatch, ingestion, integration, verification
  worktree.py        git isolation and ownership conflict detection
  gates.py           gate kinds, metric store, verdicts
  workers.py         packet rendering and dispatcher back ends
  repair.py          gate evidence -> bounded, actionable repair packet
  deploy.py          supervised local execution, smoke and soak probes
  fleetmetrics.py    attempts, retries, gate outcomes, critical path
  ledger.py          run record and intent → evidence traceability
  report.py          Markdown and HTML reports
  cli.py             operator interface
intents/             intents and frozen acceptance harnesses
tests/               engine tests (control-plane correctness)
runs/<id>/           run record, packets, evidence, product repo, worktrees
```

## Limitations, stated plainly

* The engine is a control plane, not a model. Worker quality is the dispatcher's business;
  MiniFleet's job is to make failure visible rather than to hide it.
* Semantic merge conflicts are not auto-resolved. Overlapping ownership is rejected up
  front; a real conflict aborts integration and is reported.
* `evolve` re-plans against a new intent and reopens affected tasks, keeping verified work
  as the regression suite. It does not yet do continuous deployment.
* `deploy` is supervised **local** execution: it starts the artefact, waits for its ready
  line, probes it and tears it down. There is no cloud provider, no multi-host placement
  and no restart-on-crash supervision loop yet.
* Repair is bounded but not yet closed-loop: a failed task gets an actionable packet and,
  once the budget is spent, an escalation - but re-dispatching that packet is still an
  explicit operator decision rather than an automatic retry.

## Tests

```bash
python3 -m unittest discover -s tests
```
