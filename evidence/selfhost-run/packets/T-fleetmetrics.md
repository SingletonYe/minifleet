# Task packet T-fleetmetrics - Fleet metrics

Run: `20260910T114536Z-minifleet-v02-40d5be`   Role: **builder**   Branch: `task/fleetmetrics-a2`

## Goal

Implement minifleet/fleetmetrics.py: account for a run - attempts, retries, gate outcomes and the critical path.

## Write scope (may not be exceeded)

- `minifleet/fleetmetrics.py`
- `tests/test_fleetmetrics.py`

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

- summarize returns every documented key for a realistic run record
- the critical path follows depends_on edges and is not the sum of all durations
- an empty run summarises without raising

## Tests you must write and run

- unit tests over synthetic run records, including a branching graph

## Acceptance criteria this task feeds

- **A-5** (functional): Fleet metrics report tasks, merged, failed, attempts, retries, gate outcomes and the critical path through the task graph

## System budgets (enforced later, by the fleet)

- `engine_tests_ms`: 60000.0

## Required return

Work only inside the worktree above. When finished, report a JSON object with:

```json
{"task_id": "T-fleetmetrics", "status": "submitted|failed", "files": ["..."], "commands": ["..."], "notes": "what you did and what you did not do"}
```

Report honestly: a task that is partly done and says so is more useful to the fleet than one that claims success. The fleet verifies every claim itself.