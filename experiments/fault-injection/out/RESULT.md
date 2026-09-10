# Injected-fault experiment: does the repair loop actually close?

Run `20260910T181946Z-minifleet-v03-quarantine-b1431f` for intent `minifleet-v03-quarantine`, executed by `minifleet autopilot` against the engine's own repository.

## 0. The intent was prose, not JSON

The intent for this run was written as a Markdown document and compiled by `minifleet compile`, which derived the components and their write scopes, the gate set, the budgets and the acceptance-to-gate binding. What it decided on the author's behalf is listed here rather than left implicit:


## 1. The injected defect

The worker wrote `minifleet/quarantine.py` with two defects that its own tests do not cover: an empty sample is reported as `stable-fail` instead of `insufficient-data`, and the insufficient-data guard is `<=` instead of `<`, so exactly `min_samples` outcomes are refused a verdict.

The task gate (`python3 -m unittest discover -s tests -v`) passed anyway - the worker's tests only used 4-5 sample series. That is the point: the defect is invisible to whoever wrote it.

## 2. What the fleet saw on the first attempt

- `G-harness` -> **fail**: exit=1 (expected [0]): Traceback (most recent call last): | AssertionError: 'stable-fail' != 'insufficient-data' | AssertionError: 'insufficient-data' != 'flaky' | AssertionError: {'verdict': 'insufficient-data', 'pass_rate': None, 's[24 chars]None} != {'verdict': 'stable-fail', 'pa
  - AssertionError: 'stable-fail' != 'insufficient-data'

Round 1 verdict: `fail: G-harness=fail`

## 3. What the fleet did about it

- gate `G-harness` attributed to task `T-quarantine` (attempt 1, escalate=False)

The repair packet the fleet handed to the next attempt:

```json
{
  "attempt": 1,
  "max_attempts": 3,
  "escalate": false,
  "reason": "attempt 1/3 failed 1 gate(s): G-harness",
  "failing_gates": [
    {
      "detail": "exit=1 (expected [0]): Traceback (most recent call last): | AssertionError: 'stable-fail' != 'insufficient-data' | AssertionError: 'insufficient-data' != 'flaky' | AssertionError: {'verdict': 'insufficient-data', 'pass_rate': None, 's[24 chars]None} != {'verdict': 'stable-fail', 'pa",
      "gate_id": "G-harness",
      "status": "fail"
    }
  ],
  "instructions": [
    "Fix gate G-harness (status fail): exit=1 (expected [0]): Traceback (most recent call last): | AssertionError: 'stable-fail' != 'insufficient-data' | AssertionError: 'insufficient-data' != 'flaky' | AssertionError: {'verdict': 'insufficient-data', 'pass_rate': None, 's[24 chars]None} != {'verdict': 'stable-fail', 'pa",
    "Re-run gate G-harness locally and confirm it passes."
  ]
}
```

## 4. The second attempt

The worker read `packets/T-quarantine.repair.json`, fixed the module and resubmitted. Final verdict: **pass: all required gates green** (round verdicts: ['fail: G-harness=fail', 'pass: all required gates green']).

## 5. Deployment, observed by the fleet itself

- ready in 0.065s on port 34097
- smoke `200`, soak 17 samples / 0 failures, p50 1.611 ms, max 1.906 ms
- budget gates: ready <= 10s and p50 <= 50ms both enforced as system gates

## 6. Fleet accounting

```json
{
  "attempts_total": 2,
  "critical_path_seconds": 27.335,
  "failed": 0,
  "gates_failed": 1,
  "gates_passed": 17,
  "gates_total": 18,
  "merged": 1,
  "retries": 1,
  "slowest_task": {
    "id": "T-quarantine",
    "seconds": 27.335
  },
  "tasks": 1,
  "verdict": "pass: all required gates green"
}
```

## 7. Reproduce

```bash
python3 experiments/fault-injection/run_experiment.py
```

## 8. What this proves, and what it does not

- It proves the loop closes: a defect invisible to its author's tests was caught by a gate the worker could not edit, attributed to the component that caused it, turned into an actionable packet, and fixed on a second attempt that the same gate re-verified.
- It does not prove the worker 'understood' anything: the second attempt in this experiment is a deterministic script. What is under test is the engine - detection, attribution, bounded retry and re-verification.
- An unattributed red gate is deliberately *not* retried. The fleet reports it and stops, because retrying what nobody owns is how a fleet burns a night.
