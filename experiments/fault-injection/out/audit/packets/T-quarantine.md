# Task packet T-quarantine - Flaky-gate quarantine

Run: `20260910T134913Z-minifleet-v03-quarantine-d94998`   Role: **builder**   Branch: `task/quarantine-a2`

## Goal

Implement minifleet/quarantine.py: classify repeated gate outcomes and list the gates worth quarantining.

## Write scope (may not be exceeded)

- `minifleet/quarantine.py`
- `tests/test_quarantine.py`

Any file outside this scope is owned by another worker. Touching it will fail the fleet's ownership check at integration time.

## Frozen contracts

### C-V03 - `contracts/C-V03.md`

Frozen interface for flaky-gate quarantine.

- `minifleet.quarantine.classify`: classify(outcomes: list[bool], min_samples: int = 3, flake_threshold: float = 0.5) -> {'verdict': str, 'pass_rate': float|None, 'samples': int, 'dominant': str|None}
- `minifleet.quarantine.quarantine`: quarantine(gates: dict[str, list[bool]], min_samples: int = 3, flake_threshold: float = 0.5) -> list[str]

## Definition of done

- classify() returns exactly the four documented keys
- insufficient data - including the empty sample - is never given a verdict
- min_samples is an inclusive boundary
- quarantine() returns sorted flaky gate ids only

## Tests you must write and run

- unit tests for stable, flaky and insufficient-data cases

## Acceptance criteria this task feeds

- **A-1** (functional): classify() labels all-pass as stable-pass, all-fail as stable-fail and mixed outcomes as flaky, with an exact pass_rate and sample count
- **A-2** (safety): A sample smaller than min_samples - including an empty one - is insufficient-data with a null pass_rate, never a verdict
- **A-3** (functional): Exactly min_samples outcomes are enough to decide; the boundary is inclusive
- **A-4** (functional): quarantine() returns only flaky gates, sorted, and never quarantines insufficient data

## System budgets (enforced later, by the fleet)

- `deploy_p50_ms`: 50.0
- `deploy_ready_seconds`: 10.0

## Required return

Work only inside the worktree above. When finished, report a JSON object with:

```json
{"task_id": "T-quarantine", "status": "submitted|failed", "files": ["..."], "commands": ["..."], "notes": "what you did and what you did not do"}
```

Report honestly: a task that is partly done and says so is more useful to the fleet than one that claims success. The fleet verifies every claim itself.