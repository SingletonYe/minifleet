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
```

`status` shows the state of every task and the exact next action for each one. The same
pipeline can be driven end to end for dispatchers that run workers themselves:

```bash
python3 -m minifleet run --intent intents/linksvc.json --dispatcher "command:./examples/codex_worker.sh"
```

## Dispatchers

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

## Repository layout

```
minifleet/           the engine
  intent.py          intent compiler and validation
  architect.py       design, dependency graph, self-repair, design review
  scheduler.py       waves, dispatch, ingestion, integration, verification
  worktree.py        git isolation and ownership conflict detection
  gates.py           gate kinds, metric store, verdicts
  workers.py         packet rendering and dispatcher back ends
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

## Tests

```bash
python3 -m unittest discover -s tests
```
