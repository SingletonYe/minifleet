# Task packet T-wiring - CLI and scheduler wiring

Run: `20260910T114536Z-minifleet-v02-40d5be`   Role: **builder**   Branch: `task/wiring`

## Goal

Wire the three new modules into the CLI (repair, deploy, fleetmetrics subcommands) and into the scheduler, without changing existing behaviour.

## Write scope (may not be exceeded)

- `minifleet/cli.py`
- `minifleet/scheduler.py`
- `tests/test_wiring.py`

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

- python3 -m minifleet --help lists repair, deploy and fleetmetrics
- repair writes packets/<task>.repair.json for each failed task and prints 'no failed tasks' otherwise
- deploy starts a command, waits for its ready line, probes it, prints JSON and always tears it down
- fleetmetrics prints the summarize() JSON
- every pre-existing subcommand and its behaviour is preserved

## Tests you must write and run

- a CLI-level test that drives the fixture run directory through the new subcommands

## Acceptance criteria this task feeds

- **A-6** (functional): repair, deploy and fleetmetrics are CLI subcommands; repair writes one repair packet per failed task and reports cleanly when nothing failed
- **A-7** (safety): The pre-existing engine behaviour is unchanged: the regression suite that shipped with the baseline still passes on the integrated tree

## System budgets (enforced later, by the fleet)

- `engine_tests_ms`: 60000.0

## Required return

Work only inside the worktree above. When finished, report a JSON object with:

```json
{"task_id": "T-wiring", "status": "submitted|failed", "files": ["..."], "commands": ["..."], "notes": "what you did and what you did not do"}
```

Report honestly: a task that is partly done and says so is more useful to the fleet than one that claims success. The fleet verifies every claim itself.