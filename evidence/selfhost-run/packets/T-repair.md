# Task packet T-repair - Bounded self-repair

Run: `20260910T114536Z-minifleet-v02-40d5be`   Role: **builder**   Branch: `task/repair-a2`

## Goal

Implement minifleet/repair.py, turning gate evidence into an actionable, bounded repair packet.

## Write scope (may not be exceeded)

- `minifleet/repair.py`
- `tests/test_repair.py`

Any file outside this scope is owned by another worker. Touching it will fail the fleet's ownership check at integration time.

## Frozen contracts

### C-V02 - `contracts/C-V02.md`

Frozen interfaces for the three v0.2 modules and the CLI surface that exposes them.

- `minifleet cli`: python3 -m minifleet repair --run DIR writes packets/<task>.repair.json for every failed task and prints 'no failed tasks' when there are none; python3 -m minifleet deploy --dir DIR --cmd CMD --probe PATH [--seconds N] starts the process, waits for its ready line, smokes and soaks the probe path, prints a JSON report and exits non-zero when the probe fails; python3 -m minifleet fleetmetrics --run DIR prints the summarize() JSON on stdout
- `minifleet.deploy.Supervisor`: Supervisor(argv: list[str], cwd: str, ready_prefix: str = 'MINIFLEET_READY', timeout: float = 30.0) with .start() -> Supervisor, .wait_ready() -> {'ready': bool, 'port': int|None, 'seconds': float, 'stdout_tail': str}, .stop(timeout: float = 10.0) -> int, .running -> bool
- `minifleet.deploy.smoke`: smoke(url: str, timeout: float = 5.0) -> {'status': int, 'ok': bool, 'seconds': float}
- `minifleet.deploy.soak`: soak(url: str, seconds: float, interval: float = 0.25, timeout: float = 5.0) -> {'samples': int, 'failures': int, 'ok': bool, 'p50_ms': float, 'max_ms': float}
- `minifleet.fleetmetrics.summarize`: summarize(run: dict) -> {'tasks': int, 'merged': int, 'failed': int, 'attempts_total': int, 'retries': int, 'gates_total': int, 'gates_passed': int, 'gates_failed': int, 'critical_path_seconds': float, 'slowest_task': {'id': str, 'seconds': float} | None, 'verdict': str}
- `minifleet.repair.build_repair_packet`: build_repair_packet(task: dict, evidence: list[dict], attempt: int, max_attempts: int = 3) -> dict with keys task_id, attempt, max_attempts, escalate, reason, failing_gates[{gate_id,status,detail}], instructions[list[str]], scope[list[str]], carry_forward[list[str]]

## Definition of done

- build_repair_packet matches the frozen signature and returns every documented key
- failing gates are ordered by gate_id and exclude every passing gate
- instructions name each failing gate id
- the attempt budget is respected: escalate is set once it is exhausted
- inputs are not mutated

## Tests you must write and run

- unit tests covering ordering, escalation, empty evidence and non-mutation

## Acceptance criteria this task feeds

- **A-1** (functional): A repair packet lists only the failing gates, in gate-id order, carries the task write scope and never mutates its inputs
- **A-2** (safety): Repair is bounded: once the attempt budget is exhausted the packet sets escalate instead of proposing another attempt

## System budgets (enforced later, by the fleet)

- `engine_tests_ms`: 60000.0

## Required return

Work only inside the worktree above. When finished, report a JSON object with:

```json
{"task_id": "T-repair", "status": "submitted|failed", "files": ["..."], "commands": ["..."], "notes": "what you did and what you did not do"}
```

Report honestly: a task that is partly done and says so is more useful to the fleet than one that claims success. The fleet verifies every claim itself.