"""Gate templates.

Hand-writing gates for every intent is how criteria drift from what is actually
enforced. These templates encode the gates that keep being needed, with the
conventions this fleet already uses: a task gate that runs locally inside the
worker's worktree, system gates on the integrated tree, budget gates last, and
attribution on gates that can be traced to a component.
"""

from __future__ import annotations

from typing import Any

DEFAULT_UNIT = "python3 -m unittest discover -s tests -v"
DEFAULT_REGRESSION = "python3 -m unittest discover -s tests"


def unit_suite(cmd: str = DEFAULT_UNIT, timeout: float = 900.0) -> dict[str, Any]:
    """The gate a worker runs on its own worktree before anything is merged."""

    return {
        "id": "G-unit",
        "title": "worker's own tests plus the baseline suite",
        "kind": "cmd",
        "scope": "task",
        "cmd": cmd,
        "timeout": timeout,
    }


def frozen_harness(module: str, attributed_to: list[str], timeout: float = 900.0) -> dict[str, Any]:
    """The acceptance harness, which workers may read and never edit."""

    return {
        "id": "G-harness",
        "title": f"frozen acceptance harness ({module})",
        "kind": "cmd",
        "scope": "system",
        "cmd": f"python3 -m unittest {module} -v",
        "timeout": timeout,
        "attributed_to": list(attributed_to),
    }


def regression(cmd: str = DEFAULT_REGRESSION, timeout: float = 900.0) -> dict[str, Any]:
    return {
        "id": "G-regression",
        "title": "regression suite on the integrated tree",
        "kind": "cmd",
        "scope": "system",
        "cmd": cmd,
        "timeout": timeout,
    }


def service_harness(directory: str, module: str = "harness.test_accept",
                    timeout: float = 900.0) -> dict[str, Any]:
    """A shipped service keeps passing its own harness - brownfield safety net."""

    return {
        "id": "G-service",
        "title": f"{directory} still passes its own acceptance harness",
        "kind": "cmd",
        "scope": "system",
        "cmd": f"cd {directory} && python3 -m unittest {module} -v",
        "timeout": timeout,
    }


def deploy_smoke(directory: str, command: str, probe: str, seconds: float,
                 timeout: float = 900.0) -> dict[str, Any]:
    """Run the fleet's own deploy stage as a gate, publishing its measurements."""

    return {
        "id": "G-deploy",
        "title": f"deploy and observe {directory}",
        "kind": "cmd",
        "scope": "system",
        "cmd": (
            f'python3 -m minifleet deploy --dir {directory} --cmd "{command}" '
            f"--probe {probe} --seconds {seconds} --emit-metrics"
        ),
        "timeout": timeout,
    }


def budget(metric: str, op: str, threshold: float, title: str = "") -> dict[str, Any]:
    return {
        "id": f"G-budget-{metric.replace('_', '-')}",
        "title": title or f"{metric} {op} {threshold:g}",
        "kind": "budget",
        "scope": "system",
        "metric": metric,
        "op": op,
        "threshold": threshold,
    }


def probe(metric: str, command: str, title: str = "", timeout: float = 900.0) -> dict[str, Any]:
    """A command that measures something and prints a MINIFLEET_METRIC line."""

    return {
        "id": f"G-probe-{metric.replace('_', '-')}",
        "title": title or f"measure {metric}",
        "kind": "cmd",
        "scope": "system",
        "cmd": command,
        "timeout": timeout,
    }


def stdlib_only(roots: list[str], allow: list[str] | None = None,
                project: list[str] | None = None) -> dict[str, Any]:
    argv = " ".join(roots)
    extra = ""
    if allow:
        extra += " --allow " + " ".join(allow)
    if project:
        extra += " --project " + " ".join(project)
    return {
        "id": "G-stdlib-only",
        "title": "no third-party imports",
        "kind": "cmd",
        "scope": "system",
        "cmd": f"python3 -m minifleet.checks stdlib-only --roots {argv}{extra}",
    }


def no_network(roots: list[str], allow: list[str] | None = None) -> dict[str, Any]:
    argv = " ".join(roots)
    extra = (" --allow " + " ".join(allow)) if allow else ""
    return {
        "id": "G-no-network",
        "title": "library code does not reach the network",
        "kind": "cmd",
        "scope": "system",
        "cmd": f"python3 -m minifleet.checks no-network --roots {argv}{extra}",
    }


def files_present(paths: list[str], title: str = "deliverables are where the intent says") -> dict[str, Any]:
    return {
        "id": "G-layout",
        "title": title,
        "kind": "files",
        "scope": "system",
        "paths": list(paths),
    }
