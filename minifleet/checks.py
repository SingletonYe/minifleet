"""Policy checks that run as gates.

These are the checks every repository claims to satisfy and few actually verify.
They are deliberately standalone: a gate runs them as a command, so the same
check can be used from an intent, a CI job, or a human's shell.

    python3 -m minifleet.checks stdlib-only --roots minifleet
    python3 -m minifleet.checks no-network --roots minifleet --allow minifleet/deploy.py
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

NETWORK_MODULES = {"socket", "ssl", "ftplib", "smtplib", "telnetlib", "http", "urllib",
                   "requests", "httpx", "aiohttp", "xmlrpc"}


def python_files(roots: list[str]) -> list[Path]:
    found: list[Path] = []
    for root in roots:
        path = Path(root)
        if path.is_file() and path.suffix == ".py":
            found.append(path)
        elif path.is_dir():
            found.extend(sorted(p for p in path.rglob("*.py") if "__pycache__" not in p.parts))
    return found


def imported_modules(path: Path) -> set[str]:
    """Top-level module names imported by a file (relative imports excluded)."""

    tree = ast.parse(path.read_text(), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                names.add(node.module.split(".")[0])
    return names


def stdlib_only(roots: list[str], allow: list[str], project: list[str]) -> list[str]:
    """Imports that are neither stdlib nor part of the project the fleet is building."""

    permitted = set(sys.stdlib_module_names) | set(allow) | set(project)
    violations: list[str] = []
    for path in python_files(roots):
        for name in sorted(imported_modules(path)):
            if name not in permitted:
                violations.append(f"{path}: imports {name!r}, which is not in the standard library")
    return violations


def no_network(roots: list[str], allow: list[str]) -> list[str]:
    allowed = {Path(item).resolve() for item in allow}
    violations: list[str] = []
    for path in python_files(roots):
        if path.resolve() in allowed:
            continue
        for name in sorted(imported_modules(path)):
            if name in NETWORK_MODULES:
                violations.append(f"{path}: imports {name!r}; library code must not touch the network")
    return violations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m minifleet.checks")
    sub = parser.add_subparsers(dest="check", required=True)

    p_stdlib = sub.add_parser("stdlib-only", help="reject every third-party import")
    p_stdlib.add_argument("--roots", nargs="+", required=True)
    p_stdlib.add_argument("--allow", nargs="*", default=[])
    p_stdlib.add_argument("--project", nargs="*", default=[])

    p_net = sub.add_parser("no-network", help="reject network imports outside an allowlist")
    p_net.add_argument("--roots", nargs="+", required=True)
    p_net.add_argument("--allow", nargs="*", default=[])

    args = parser.parse_args(argv)
    if args.check == "stdlib-only":
        violations = stdlib_only(args.roots, args.allow, args.project)
        label = "stdlib-only"
    else:
        violations = no_network(args.roots, args.allow)
        label = "no-network"

    if violations:
        print(f"{label}: {len(violations)} violation(s)")
        for item in violations:
            print(f"  - {item}")
        return 1
    print(f"{label}: clean ({len(python_files(args.roots))} file(s) checked)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
