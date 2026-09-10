# Task packet T-quarantine - Quarantine

Run: `20260910T181946Z-minifleet-v03-quarantine-b1431f`   Role: **builder**   Branch: `task/quarantine-a2`

## Goal

classify repeated gate outcomes and list the gates worth quarantining

## Write scope (may not be exceeded)

- `minifleet/quarantine.py`
- `tests/test_quarantine.py`

Any file outside this scope is owned by another worker. Touching it will fail the fleet's ownership check at integration time.

## Frozen contracts

### C-1 - `contracts/C-1.md`

frozen interface derived from the intent

- `minifleet.quarantine.classify`: classify(outcomes: list[bool], min_samples: int = 3, flake_threshold: float = 0.5) -> {"verdict": str, "pass_rate": float|None, "samples": int, "dominant": str|None}
- `minifleet.quarantine.quarantine`: quarantine(gates: dict[str, list[bool]], min_samples: int = 3, flake_threshold: float = 0.5) -> list[str]

## Definition of done

- (unspecified)

## Tests you must write and run

- (unspecified)

## Acceptance criteria this task feeds

- **A-1** (functional): classify() labels an all-pass series, an all-fail series and a mixed series exactly, with a rounded pass_rate and the sample count
- **A-2** (functional): a sample smaller than min_samples - including an empty one - is insufficient-data with a null pass_rate, never a failure verdict
- **A-3** (functional): exactly min_samples outcomes are enough to decide: the boundary is inclusive
- **A-4** (functional): quarantine() returns only flaky gate ids, sorted ascending, and never quarantines insufficient data
- **A-5** (functional): the pre-existing engine suite still passes on the integrated tree
- **A-6** (functional): the shipped linksvc service still passes its own acceptance harness
- **A-7** (nfr): the fleet can still deploy a running system, probe it, and stay inside its latency budget

## System budgets (enforced later, by the fleet)

- `deploy_p50_ms`: 50.0
- `deploy_ready_seconds`: 10.0

## Required return

Work only inside the worktree above. When finished, report a JSON object with:

```json
{"task_id": "T-quarantine", "status": "submitted|failed", "files": ["..."], "commands": ["..."], "notes": "what you did and what you did not do"}
```

Report honestly: a task that is partly done and says so is more useful to the fleet than one that claims success. The fleet verifies every claim itself.