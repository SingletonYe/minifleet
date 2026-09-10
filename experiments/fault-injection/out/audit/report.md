# Fleet run report - MiniFleet v0.3: quarantine flaky gates

- Run id: `20260910T181946Z-minifleet-v03-quarantine-b1431f`
- Intent: `minifleet-v03-quarantine`
- Dispatcher: `packet`
- Status: **passed**
- Verdict: pass: all required gates green

## Intent as received

# MiniFleet v0.3: quarantine flaky gates

A gate that red-lights an unlucky run teaches people to ignore red. This intent adds
flaky-gate quarantine to the engine itself: decide from repeated outcomes whether a gate is
flaky, and refuse to decide when the sample is too small. It has to be done without breaking
the engine, the shipped service, or the deployment path - the service answers in well under
50 ms today and must stay there.

## Deliverables

- minifleet/quarantine.py - classify repeated gate outcomes and list the gates worth quarantining
- tests/test_quarantine.py - the worker's own tests

## Acceptance

- [harness] classify() labels an all-pass series, an all-fail series and a mixed series exactly, with a rounded pass_rate and the sample count
- [harness] a sample smaller than min_samples - including an empty one - is insufficient-data with a null pass_rate, never a failure verdict
- [harness] exactly min_samples outcomes are enough to decide: the boundary is inclusive
- [harness] quarantine() returns only flaky gate ids, sorted ascending, and never quarantines insufficient data
- [regression] the pre-existing engine suite still passes on the integrated tree
- [service] the shipped linksvc service still passes its own acceptance harness
- [deploy] the fleet can still deploy a running system, probe it, and stay inside its latency budget

## Constraints

- Python 3.12 standard library only, with one declared exception: the optional PyYAML reader in intent.py that already degrades to a clear error when PyYAML is absent
- harness/ is frozen: workers may read it, never modify it

## Out of scope

- automatically re-running gates to collect samples
- persisting quarantine state between runs

## Budgets

- deploy_ready_seconds <= 10
- deploy_p50_ms <= 50

## Contract

- minifleet.quarantine.classify: classify(outcomes: list[bool], min_samples: int = 3, flake_threshold: float = 0.5) -> {"verdict": str, "pass_rate": float|None, "samples": int, "dominant": str|None}
- minifleet.quarantine.quarantine: quarantine(gates: dict[str, list[bool]], min_samples: int = 3, flake_threshold: float = 0.5) -> list[str]

Insufficient data is not a failure verdict: an empty or too-small sample has pass_rate None and
no dominant side. The boundary is inclusive, so exactly min_samples outcomes decide.

## Service

- dir: examples/linksvc
- harness: harness.test_accept

## Deploy

- dir: examples/linksvc
- cmd: python3 -m linksvc --host 127.0.0.1 --port 0 --db /tmp/minifleet-compiled.db --rate 1000 --burst 1000
- probe: /healthz
- seconds: 3

## Meta

- id: minifleet-v03-quarantine
- baseline_from: ..
- harness_dir: harness-quarantine
- max_attempts: 3
- max_rounds: 4
- max_dispatches: 6
- max_wall_seconds: 900
- stdlib_allow: yaml

## Deliverables

- minifleet/quarantine.py - classify repeated gate outcomes and list the gates worth quarantining
- tests/test_quarantine.py - the worker's own tests

## Design

- Components: 1
- Contracts: 1
- Tasks: 1
- Gates: 9

## Task graph

| task | role | depends on | owns | state | files |
| --- | --- | --- | --- | --- | --- |
| `T-quarantine` Quarantine | builder | - | 2 path(s) | merged | 1 |

## Traceability: intent to evidence

| criterion | verdict | statement | gate | result | observation |
| --- | --- | --- | --- | --- | --- |
| A-1 | verified | classify() labels an all-pass series, an all-fail series and a mixed s | `G-harness` | pass | exit=0 (expected [0]) |
| A-2 | verified | a sample smaller than min_samples - including an empty one - is insuff | `G-harness` | pass | exit=0 (expected [0]) |
| A-3 | verified | exactly min_samples outcomes are enough to decide: the boundary is inc | `G-harness` | pass | exit=0 (expected [0]) |
| A-4 | verified | quarantine() returns only flaky gate ids, sorted ascending, and never  | `G-harness` | pass | exit=0 (expected [0]) |
| A-5 | verified | the pre-existing engine suite still passes on the integrated tree | `G-regression` | pass | exit=0 (expected [0]) |
| A-6 | verified | the shipped linksvc service still passes its own acceptance harness | `G-service` | pass | exit=0 (expected [0]) |
| A-7 | verified | the fleet can still deploy a running system, probe it, and stay inside | `G-deploy` | pass | exit=0 (expected [0]) |
| A-7 | verified | the fleet can still deploy a running system, probe it, and stay inside | `G-budget-deploy-p50-ms` | pass | deploy_p50_ms=1.567 <= 50 -> ok |
| A-7 | verified | the fleet can still deploy a running system, probe it, and stay inside | `G-budget-deploy-ready-seconds` | pass | deploy_ready_seconds=0.062 <= 10 -> ok |

## Gate ledger

| gate | status | duration | metrics | detail |
| --- | --- | --- | --- | --- |
| `G-unit` | pass | 7.29s |  | exit=0 (expected [0]) |
| `G-harness` | fail | 0.05s |  | exit=1 (expected [0]): Traceback (most recent call last): / AssertionError: 'stable-fail' != 'insufficient-data' / AssertionError: 'insuffic |
| `G-service` | pass | 1.66s |  | exit=0 (expected [0]) |
| `G-stdlib-only` | pass | 0.09s |  | exit=0 (expected [0]) |
| `G-regression` | pass | 7.33s |  | exit=0 (expected [0]) |
| `G-deploy` | pass | 3.36s | {"deploy_ok": 1.0, "deploy_ready_seconds": 0.063, "deploy_smoke_ms": 24.8, "deploy_smoke_status": 200.0, "deploy_p50_ms": 1.547, "deploy_max_ms": 1.605, "deploy_samples": 13.0, "deploy_failures": 0.0} | exit=0 (expected [0]) |
| `G-layout` | pass | 0.00s |  | all paths satisfied: ['minifleet/quarantine.py', 'tests/test_quarantine.py'] |
| `G-budget-deploy-ready-seconds` | pass | 0.00s | {"deploy_ready_seconds": 0.063} | deploy_ready_seconds=0.063 <= 10 -> ok |
| `G-budget-deploy-p50-ms` | pass | 0.00s | {"deploy_p50_ms": 1.547} | deploy_p50_ms=1.547 <= 50 -> ok |
| `G-unit` | pass | 7.31s |  | exit=0 (expected [0]) |
| `G-harness` | pass | 0.05s |  | exit=0 (expected [0]) |
| `G-service` | pass | 1.62s |  | exit=0 (expected [0]) |
| `G-stdlib-only` | pass | 0.08s |  | exit=0 (expected [0]) |
| `G-regression` | pass | 7.21s |  | exit=0 (expected [0]) |
| `G-deploy` | pass | 3.35s | {"deploy_ok": 1.0, "deploy_ready_seconds": 0.062, "deploy_smoke_ms": 24.0, "deploy_smoke_status": 200.0, "deploy_p50_ms": 1.567, "deploy_max_ms": 1.8, "deploy_samples": 13.0, "deploy_failures": 0.0} | exit=0 (expected [0]) |
| `G-layout` | pass | 0.00s |  | all paths satisfied: ['minifleet/quarantine.py', 'tests/test_quarantine.py'] |
| `G-budget-deploy-ready-seconds` | pass | 0.00s | {"deploy_ready_seconds": 0.062} | deploy_ready_seconds=0.062 <= 10 -> ok |
| `G-budget-deploy-p50-ms` | pass | 0.00s | {"deploy_p50_ms": 1.567} | deploy_p50_ms=1.567 <= 50 -> ok |

## Not proven

- Every acceptance criterion has passing evidence.

## Risks carried forward

- non-functional criteria are only as trustworthy as the measurement harness

## Decisions taken by the architect

- acceptance criteria are owned by the verification layer, never by the worker that writes the code
- every task has a single writer and a disjoint write scope; concurrency without ownership is a merge queue
- frozen contracts: C-1
