# Architecture

## The five stages

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

## Evidence model

Every gate run produces an `Evidence` record: status, exit code, duration, captured
metrics, output tail, and the artefacts it wrote. `ledger.traceability()` joins the
acceptance criteria to the tasks that feed them and the gates that judged them, which is
what the report renders. The consequence is a simple rule for the whole system: if a
sentence cannot be traced to evidence, the report marks it as unproven.

## Concurrency model

Parallelism is safe only because two invariants hold before any worker starts:

* **Isolation** — one git worktree and one branch per task, created from the same baseline
  commit, so a worker cannot see another worker's half-finished state.
* **Ownership** — write scopes are disjoint by construction and enforced twice: at ingest
  (a worker whose commit touches a path outside its scope fails immediately) and before
  merge (overlapping branches abort integration).

The scheduler runs a wave of ready tasks concurrently, then integrates and promotes the
next wave. Task gates run inside the worker's own worktree, so a broken module never
reaches the integration branch; system gates run on the integrated tree, which also
re-runs the worker's own unit tests as a regression suite.

## Where the model is not the product

MiniFleet does not claim the workers are correct. It claims that incorrectness is
*visible*: the write scope check, the frozen acceptance harness and the budget gates exist
precisely to convert a confident claim into a falsifiable one. A run that fails a gate is
a successful run of the verification layer.
