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
