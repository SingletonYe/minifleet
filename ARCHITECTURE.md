# Architecture

## The six stages

1. **Compile.** `intent.py` turns a structured intent into an `IntentSpec`. An intent that
   names a gate that does not exist, that declares a budget without an enforcing gate, or
   that lets two components claim the same write scope is rejected. An unfalsifiable
   intent is a planning problem, not a coding problem.
2. **Design.** `architect.py` freezes the contracts, creates one task per component, and
   reviews its own design: acyclic dependencies, disjoint write scopes, every criterion
   reachable from a gate. Repairable defects (a criterion with no gate, a component with no
   write scope, a dependency cycle, a dangling dependency) are fixed mechanically and the
   repair is recorded in `design.repairs` so a human can audit what changed.
3. **Dispatch.** `scheduler.py` computes the ready wave, and `workers.py` renders one
   self-contained packet per task. Each packet carries the goal, the write scope, the
   frozen contracts, the definition of done, and the tests the worker must write. Packets
   are dispatched in parallel to whatever runtime the dispatcher names.
4. **Integrate.** Every task gets its own branch and worktree from the frozen baseline.
   Before merging, the scheduler computes the changed files per task and refuses to
   continue if two tasks touched the same path. Merges follow dependency order, so the
   integration branch is always a valid partial system.
5. **Verify.** System gates run on the integrated tree. Budget gates run last, after the
   probe gates that measure them. A verdict is `pass` only if every required gate produced
   passing evidence; a metric that was never measured is an error, never a pass.
6. **Observe.** `deploy.py` starts the produced system under supervision, waits for its
   machine-readable ready line, probes it, soaks it for a bounded window and tears it down.
   Verification answers "did the gates pass"; observation answers "does it actually run".

## Adopting an existing codebase

An intent may name `baseline_from`, in which case the run adopts that tree as the product
instead of materialising a fresh one. Adoption is deliberately asymmetric: the fleet owns
`ACCEPTANCE.md`, `INTENT.md`, `contracts/` and `harness/` and always writes them, and it
copies everything else only when the path does not already exist. A worker can therefore
extend a real repository, but no stage of the pipeline can silently overwrite a file the
repository already had - including the regression suite, which becomes part of the run's
definition of done rather than a suggestion.

## Failure is an input, not an ending

`repair.py` turns a failed attempt's evidence into the next attempt's brief: the failing
gates in gate-id order, instructions that name each of them with the gate's own detail, the
task's write scope, what was already submitted, and an attempt budget. Nothing about a
packet is generated from the worker's own claims - the evidence is what gates observed.
When the budget is spent, the packet sets `escalate` and says so, because "retry forever"
is not autonomy. `scheduler.ingest` writes the packet on failure and records
`task.repair_packet` in the ledger; re-dispatching it remains an explicit operator action.

## Evidence model

Every gate run produces an `Evidence` record: status, exit code, duration, captured
metrics, output tail, and the artefacts it wrote. `ledger.traceability()` joins the
acceptance criteria to the tasks that feed them and the gates that judged them, which is
what the report renders. The consequence is a simple rule for the whole system: if a
sentence cannot be traced to evidence, the report marks it as unproven.

`fleetmetrics.py` reads the same records from the other end: how many attempts the run
actually spent, how many of them were retries, how the gates split between pass and not,
and the critical path through `depends_on` - the longest chain of task durations, which is
the wall-clock floor the run could not go below. It is intentionally *not* the sum of all
durations: work that overlapped in time must not be charged twice.

## Concurrency model

Parallelism is safe only because two invariants hold before any worker starts:

* **Isolation** — one git worktree and one branch per task, created from the same baseline
  commit, so a worker cannot see another worker's half-finished state.
* **Ownership** — write scopes are disjoint by construction and enforced twice: at ingest
  (a worker whose commit touches a path outside its scope fails immediately) and before
  merge (overlapping branches abort integration).

Reopening work is the third case the scheduler has to get right, because an evolved intent
reopens tasks whose branch has already been merged. A merged branch is *spent*: checking it
out again would hand the worker the tree from before the merge, without the contracts and
harness files frozen since. `worktree.attempt_branch` therefore starts a fresh attempt
branch from the current baseline whenever the task's previous branch is already an ancestor
of it, and reuses the existing branch only when it holds unmerged work.

The scheduler runs a wave of ready tasks concurrently, then integrates and promotes the
next wave. Task gates run inside the worker's own worktree, so a broken module never
reaches the integration branch; system gates run on the integrated tree, which also
re-runs the worker's own unit tests as a regression suite.

## Where the model is not the product

MiniFleet does not claim the workers are correct. It claims that incorrectness is
*visible*: the write scope check, the frozen acceptance harness and the budget gates exist
precisely to convert a confident claim into a falsifiable one. A run that fails a gate is
a successful run of the verification layer.
