# Fleet run report - MiniFleet v0.2: autonomy on failure, deployment, fleet metrics

- Run id: `20260910T114536Z-minifleet-v02-40d5be`
- Intent: `minifleet-v02`
- Dispatcher: `packet`
- Status: **passed**
- Verdict: pass: all required gates green

## Intent as received

Continue replicating Fleet by making MiniFleet more autonomous. It must be able to (1) turn a failed task into a repair packet that a worker can act on, bounded by a maximum number of attempts, (2) actually deploy a produced system: start it, wait for its ready line, probe it, soak it for a short window and shut it down, and (3) account for its own runs - attempts, retries, gate outcomes and the critical path through the task graph. All three must be reachable from the CLI. The existing engine behaviour must be preserved: the current regression suite is the floor, not a suggestion.

## Deliverables

- minifleet/repair.py - turn gate evidence into a bounded, actionable repair packet
- minifleet/deploy.py - supervised process launch, ready detection, smoke and soak probes
- minifleet/fleetmetrics.py - attempts, retries, gate outcomes and critical path of a run
- cli wiring so `repair`, `deploy` and `fleetmetrics` are real subcommands
- the existing engine tests, still green

## Design

- Components: 4
- Contracts: 1
- Tasks: 4
- Gates: 7

## Task graph

| task | role | depends on | owns | state | files |
| --- | --- | --- | --- | --- | --- |
| `T-repair` Bounded self-repair | builder | - | 2 path(s) | merged | 2 |
| `T-deploy` Deploy and observe | builder | - | 2 path(s) | merged | 2 |
| `T-fleetmetrics` Fleet metrics | builder | - | 2 path(s) | merged | 2 |
| `T-wiring` CLI and scheduler wiring | builder | T-repair, T-deploy, T-fleetmetrics | 3 path(s) | merged | 3 |

## Traceability: intent to evidence

| criterion | verdict | statement | gate | result | observation |
| --- | --- | --- | --- | --- | --- |
| A-1 | verified | A repair packet lists only the failing gates, in gate-id order, carrie | `G-contract` | pass | exit=0 (expected [0]) |
| A-2 | verified | Repair is bounded: once the attempt budget is exhausted the packet set | `G-contract` | pass | exit=0 (expected [0]) |
| A-3 | verified | A supervisor starts a process, parses its ready line and port, times o | `G-contract` | pass | exit=0 (expected [0]) |
| A-4 | verified | Smoke and soak probes report status, sample count, failure count and l | `G-contract` | pass | exit=0 (expected [0]) |
| A-5 | verified | Fleet metrics report tasks, merged, failed, attempts, retries, gate ou | `G-contract` | pass | exit=0 (expected [0]) |
| A-6 | verified | repair, deploy and fleetmetrics are CLI subcommands; repair writes one | `G-wiring` | pass | exit=0 (expected [0]) |
| A-7 | verified | The pre-existing engine behaviour is unchanged: the regression suite t | `G-regression` | pass | exit=0 (expected [0]) |
| A-8 | verified | The regression suite still finishes within 60 seconds | `G-budget-bench` | pass | engine_tests_ms=6606.4 <= 60000 -> ok |

## Gate ledger

| gate | status | duration | metrics | detail |
| --- | --- | --- | --- | --- |
| `G-unit` | pass | 0.54s |  | exit=0 (expected [0]) |
| `G-unit` | pass | 3.74s |  | exit=0 (expected [0]) |
| `G-unit` | pass | 0.54s |  | exit=0 (expected [0]) |
| `G-unit` | pass | 6.68s |  | exit=0 (expected [0]) |
| `G-contract` | pass | 2.45s |  | exit=0 (expected [0]) |
| `G-wiring` | pass | 0.50s |  | exit=0 (expected [0]) |
| `G-regression` | pass | 6.62s |  | exit=0 (expected [0]) |
| `G-bench` | pass | 6.64s | {"engine_tests_ms": 6606.4} | exit=0 (expected [0]) |
| `G-layout` | pass | 0.00s |  | all paths satisfied: ['minifleet/repair.py', 'minifleet/deploy.py', 'minifleet/fleetmetrics.py', 'ACCEPTANCE.md'] |
| `G-budget-bench` | pass | 0.00s | {"engine_tests_ms": 6606.4} | engine_tests_ms=6606.4 <= 60000 -> ok |

## Not proven

- Every acceptance criterion has passing evidence.

## Risks carried forward

- wide task graph: integration cost grows with the number of writers
- non-functional criteria are only as trustworthy as the measurement harness

## Decisions taken by the architect

- acceptance criteria are owned by the verification layer, never by the worker that writes the code
- every task has a single writer and a disjoint write scope; concurrency without ownership is a merge queue
- frozen contracts: C-V02
