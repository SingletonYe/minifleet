"""Git-backed isolation for fleet workers.

Concurrency is only safe when two guarantees hold: every worker has its own
filesystem view (a git worktree) and no two workers may write the same path
(ownership). MiniFleet enforces the second guarantee explicitly, and detects it
before merging rather than during a conflict resolution.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


class GitError(RuntimeError):
    pass


# Bytecode and tool caches are produced by *running* the gate commands, not by
# a worker's edits. They are excluded from the ownership check and from worker
# commits; a scope violation is about source files, not about a .pyc.
CACHE_PATHSPECS = [
    ":(exclude)**/__pycache__/**",
    ":(exclude)**/*.pyc",
    ":(exclude)**/.pytest_cache/**",
    ":(exclude)**/.mypy_cache/**",
]


def is_cache_path(path: str) -> bool:
    parts = path.replace("\\", "/").split("/")
    if "__pycache__" in parts or ".pytest_cache" in parts or ".mypy_cache" in parts:
        return True
    return path.endswith((".pyc", ".pyo"))


def git(args: list[str], cwd: Path | str, check: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        env=_env(),
    )
    if check and proc.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed in {cwd}:\n{proc.stderr.strip()}")
    return proc


def _env() -> dict[str, str]:
    import os

    env = dict(os.environ)
    env.setdefault("GIT_AUTHOR_NAME", "MiniFleet")
    env.setdefault("GIT_AUTHOR_EMAIL", "fleet@minifleet.local")
    env.setdefault("GIT_COMMITTER_NAME", "MiniFleet")
    env.setdefault("GIT_COMMITTER_EMAIL", "fleet@minifleet.local")
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    return env


@dataclass
class Repo:
    """The product repository under construction."""

    path: Path

    def __post_init__(self) -> None:
        self.path = Path(self.path)

    # -- lifecycle -------------------------------------------------------
    def init(self, baseline: dict[str, str], branch: str = "main") -> str:
        self.path.mkdir(parents=True, exist_ok=True)
        if not (self.path / ".git").exists():
            git(["init", "-q", "-b", branch], self.path)
            git(["config", "user.name", "MiniFleet"], self.path)
            git(["config", "user.email", "fleet@minifleet.local"], self.path)
        for rel, content in baseline.items():
            target = self.path / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
        git(["add", "-A"], self.path)
        proc = git(
            ["commit", "-q", "--allow-empty", "-m",
             "baseline: frozen contracts and acceptance harness"],
            self.path,
            check=False,
        )
        if proc.returncode != 0 and "nothing to commit" not in proc.stdout + proc.stderr:
            raise GitError(proc.stderr)
        return self.head()

    def head(self, rev: str = "HEAD") -> str:
        return git(["rev-parse", rev], self.path).stdout.strip()

    def current_branch(self) -> str:
        return git(["rev-parse", "--abbrev-ref", "HEAD"], self.path).stdout.strip()

    def ensure_branch(self, branch: str, base: str = "HEAD") -> None:
        exists = git(["rev-parse", "--verify", "-q", branch], self.path, check=False)
        if exists.returncode != 0:
            git(["branch", branch, base], self.path)

    def checkout(self, branch: str) -> None:
        git(["checkout", "-q", branch], self.path)

    def branch_exists(self, branch: str) -> bool:
        return git(["rev-parse", "--verify", "-q", branch], self.path, check=False).returncode == 0

    def is_ancestor(self, ancestor: str, descendant: str) -> bool:
        """True when ``ancestor`` is already contained in ``descendant``."""

        proc = git(
            ["merge-base", "--is-ancestor", ancestor, descendant], self.path, check=False
        )
        return proc.returncode == 0

    def attempt_branch(self, component_id: str, current: str, base: str) -> str:
        """Pick the branch a new attempt should use.

        A branch whose tip is already contained in the baseline has been
        merged; checking it out again would hand the worker a tree from before
        the merge (missing contracts and harness files frozen since). Such a
        branch is spent, so the attempt gets a fresh branch from the baseline.
        An unmerged branch is live work and is reused.
        """

        if not self.branch_exists(current):
            return current
        if not self.is_ancestor(current, base):
            return current
        attempt = 2
        while self.branch_exists(f"task/{component_id}-a{attempt}"):
            attempt += 1
        return f"task/{component_id}-a{attempt}"

    # -- worker sandboxes ------------------------------------------------
    def add_worktree(self, path: Path | str, branch: str, base: str = "HEAD") -> Path:
        path = Path(path).resolve()
        if (path / ".git").exists():
            current = git(
                ["rev-parse", "--abbrev-ref", "HEAD"], path, check=False
            ).stdout.strip()
            if current == branch:
                return path
            # The sandbox exists but is checked out on another branch: it is a
            # leftover from an earlier attempt and must not be reused.
            self.remove_worktree(path)
        elif path.exists():
            for child in sorted(path.rglob("*"), reverse=True):
                if child.is_dir() and not child.is_symlink():
                    child.rmdir()
                else:
                    child.unlink()
            path.rmdir()
        path.parent.mkdir(parents=True, exist_ok=True)
        exists = git(["rev-parse", "--verify", "-q", branch], self.path, check=False)
        if exists.returncode == 0:
            git(["worktree", "add", "-q", str(path), branch], self.path)
        else:
            git(["worktree", "add", "-q", "-b", branch, str(path), base], self.path)
        return path

    def remove_worktree(self, path: Path | str) -> None:
        git(["worktree", "remove", "--force", str(path)], self.path, check=False)

    def commit_all(self, worktree: Path | str, message: str) -> str | None:
        git(["add", "-A", "--", ".", *CACHE_PATHSPECS], worktree)
        diff = git(["diff", "--cached", "--quiet"], worktree, check=False)
        if diff.returncode == 0:
            return None
        git(["commit", "-q", "-m", message], worktree)
        return git(["rev-parse", "HEAD"], worktree).stdout.strip()

    # -- inspection ------------------------------------------------------
    def changed_files(self, worktree: Path | str, base: str) -> list[str]:
        tracked = git(["diff", "--name-only", base], worktree).stdout.split()
        status = git(["status", "--porcelain", "-uall"], worktree).stdout.splitlines()
        untracked: list[str] = []
        for line in status:
            if not line.startswith("??"):
                continue
            path = line[3:]
            untracked.append(path.split(" -> ")[-1].strip())
        changed = {p for p in set(tracked) | set(untracked) if not is_cache_path(p)}
        return sorted(changed)

    def diffstat(self, worktree: Path | str, base: str) -> str:
        return git(["diff", "--stat", base], worktree).stdout.strip()

    def merge(self, branch: str, message: str) -> str:
        proc = git(["merge", "--no-ff", "-q", branch, "-m", message], self.path, check=False)
        if proc.returncode != 0:
            git(["merge", "--abort"], self.path, check=False)
            raise GitError(
                f"merge of {branch} failed; the fleet does not auto-resolve semantic conflicts:\n"
                f"{proc.stdout.strip()}\n{proc.stderr.strip()}"
            )
        return self.head()


def ownership_conflicts(branches: dict[str, list[str]]) -> list[tuple[str, str, list[str]]]:
    """Report paths touched by more than one task branch."""

    seen: dict[str, str] = {}
    conflicts: dict[tuple[str, str], list[str]] = {}
    for branch, files in branches.items():
        for path in files:
            if path in seen and seen[path] != branch:
                key = tuple(sorted((seen[path], branch)))
                conflicts.setdefault(key, []).append(path)
            seen.setdefault(path, branch)
    return [(a, b, sorted(set(paths))) for (a, b), paths in conflicts.items()]
