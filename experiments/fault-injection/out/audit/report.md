# Fleet run report - MiniFleet v0.3: quarantine flaky gates

- Run id: `20260910T134913Z-minifleet-v03-quarantine-d94998`
- Intent: `minifleet-v03-quarantine`
- Dispatcher: `packet`
- Status: **passed**
- Verdict: pass: all required gates green

## Intent as received

A gate that red-lights an unlucky run teaches people to ignore red, so the fleet needs to tell a flaky gate from a broken one. Add minifleet/quarantine.py: classify a series of gate outcomes as insufficient-data, stable-pass, stable-fail or flaky, and list the gates worth quarantining. Refuse to decide on a sample smaller than the minimum. Do this without breaking the engine, the shipped service, or the deployment path.

## Deliverables

- minifleet/quarantine.py - classify repeated gate outcomes and list flaky gates
- tests/test_quarantine.py - the worker's own tests
- the existing engine, service and deployment gates, still green

## Design

- Components: 1
- Contracts: 1
- Tasks: 1
- Gates: 8

## Task graph

| task | role | depends on | owns | state | files |
| --- | --- | --- | --- | --- | --- |
| `T-quarantine` Flaky-gate quarantine | builder | - | 2 path(s) | merged | 1 |

## Traceability: intent to evidence

| criterion | verdict | statement | gate | result | observation |
| --- | --- | --- | --- | --- | --- |
| A-1 | verified | classify() labels all-pass as stable-pass, all-fail as stable-fail and | `G-quarantine` | pass | exit=0 (expected [0]) |
| A-2 | verified | A sample smaller than min_samples - including an empty one - is insuff | `G-quarantine` | pass | exit=0 (expected [0]) |
| A-3 | verified | Exactly min_samples outcomes are enough to decide; the boundary is inc | `G-quarantine` | pass | exit=0 (expected [0]) |
| A-4 | verified | quarantine() returns only flaky gates, sorted, and never quarantines i | `G-quarantine` | pass | exit=0 (expected [0]) |
| A-5 | verified | The pre-existing engine regression suite still passes on the integrate | `G-regression` | pass | exit=0 (expected [0]) |
| A-6 | verified | The shipped linksvc service still passes its own acceptance harness | `G-service` | pass | exit=0 (expected [0]) |
| A-7 | verified | The fleet can still deploy a running system and probe it: ready under  | `G-deploy` | pass | exit=0 (expected [0]) |
| A-7 | verified | The fleet can still deploy a running system and probe it: ready under  | `G-budget-ready` | pass | deploy_ready_seconds=0.068 <= 10 -> ok |
| A-7 | verified | The fleet can still deploy a running system and probe it: ready under  | `G-budget-p50` | pass | deploy_p50_ms=1.731 <= 50 -> ok |

## Gate ledger

| gate | status | duration | metrics | detail |
| --- | --- | --- | --- | --- |
| `G-unit` | pass | 6.82s |  | exit=0 (expected [0]) |
| `G-quarantine` | fail | 0.05s |  | exit=1 (expected [0]): Traceback (most recent call last): / AssertionError: 'stable-fail' != 'insufficient-data' / AssertionError: 'insuffic |
| `G-regression` | pass | 7.35s |  | exit=0 (expected [0]) |
| `G-service` | pass | 1.61s |  | exit=0 (expected [0]) |
| `G-deploy` | pass | 3.36s | {"deploy_ok": 1.0, "deploy_ready_seconds": 0.068, "deploy_smoke_ms": 23.9, "deploy_smoke_status": 200.0, "deploy_p50_ms": 1.704, "deploy_max_ms": 2.022, "deploy_samples": 13.0, "deploy_failures": 0.0} | exit=0 (expected [0]) |
| `G-layout` | pass | 0.00s |  | all paths satisfied: ['minifleet/quarantine.py', 'ACCEPTANCE.md'] |
| `G-budget-ready` | pass | 0.00s | {"deploy_ready_seconds": 0.068} | deploy_ready_seconds=0.068 <= 10 -> ok |
| `G-budget-p50` | pass | 0.00s | {"deploy_p50_ms": 1.704} | deploy_p50_ms=1.704 <= 50 -> ok |
| `G-unit` | pass | 7.33s |  | exit=0 (expected [0]) |
| `G-quarantine` | pass | 0.05s |  | exit=0 (expected [0]) |
| `G-regression` | pass | 7.36s |  | exit=0 (expected [0]) |
| `G-service` | pass | 1.62s |  | exit=0 (expected [0]) |
| `G-deploy` | pass | 3.37s | {"deploy_ok": 1.0, "deploy_ready_seconds": 0.068, "deploy_smoke_ms": 23.8, "deploy_smoke_status": 200.0, "deploy_p50_ms": 1.731, "deploy_max_ms": 1.91, "deploy_samples": 13.0, "deploy_failures": 0.0} | exit=0 (expected [0]) |
| `G-layout` | pass | 0.00s |  | all paths satisfied: ['minifleet/quarantine.py', 'ACCEPTANCE.md'] |
| `G-budget-ready` | pass | 0.00s | {"deploy_ready_seconds": 0.068} | deploy_ready_seconds=0.068 <= 10 -> ok |
| `G-budget-p50` | pass | 0.00s | {"deploy_p50_ms": 1.731} | deploy_p50_ms=1.731 <= 50 -> ok |

## Not proven

- Every acceptance criterion has passing evidence.

## Risks carried forward

- none recorded

## Decisions taken by the architect

- acceptance criteria are owned by the verification layer, never by the worker that writes the code
- every task has a single writer and a disjoint write scope; concurrency without ownership is a merge queue
- frozen contracts: C-V03
