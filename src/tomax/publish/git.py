"""Git plumbing for publishing this device's own sanitized daily aggregates.

Every publish stages *only* this device's own partition
(``data/v1/devices/<device-id>/``) — never any other device's files, and
never anything outside ``data/v1/devices/`` — so a bug elsewhere in the
working tree can never leak into a commit here. Every push is preceded by
a fetch and rebase onto the remote branch, retried a bounded number of
times on a non-fast-forward rejection (another device published first);
this module never force-pushes under any circumstance.

This module has no opinion on GitHub authentication — see
``tomax.commands.publish``, which gates all of this behind a
``gh auth status`` check before any function here is called.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

_NON_FAST_FORWARD_MARKERS = ("non-fast-forward", "fetch first", "stale info")


class GitCommandError(RuntimeError):
    """A git subprocess exited non-zero."""

    def __init__(self, args: tuple[str, ...], returncode: int, stderr: str) -> None:
        super().__init__(f"git {' '.join(args)} failed ({returncode}): {stderr.strip()}")
        self.git_args = args
        self.returncode = returncode
        self.stderr = stderr


def _run(*args: str, cwd: Path) -> str:
    result = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        raise GitCommandError(args, result.returncode, result.stderr)
    return result.stdout


def clone_or_open(repo_url: str, local_path: Path, *, branch: str) -> Path:
    """Clone ``repo_url`` into ``local_path`` if absent; otherwise reuse the existing clone."""
    if (local_path / ".git").is_dir():
        return local_path
    local_path.parent.mkdir(parents=True, exist_ok=True)
    _run(
        "clone",
        "--branch",
        branch,
        "--single-branch",
        repo_url,
        str(local_path),
        cwd=local_path.parent,
    )
    return local_path


def _device_partition(device_id: str) -> str:
    return f"data/v1/devices/{device_id}"


def _has_staged_changes(repo_dir: Path, partition_path: str) -> bool:
    """Whether ``partition_path`` specifically has staged changes.

    Scoped to just this path (rather than a whole-tree ``git status``) so
    an unrelated untracked or modified file elsewhere in the working copy
    — e.g. another device's partition sitting in the same clone — can
    never be mistaken for "something of ours to commit."
    """
    result = subprocess.run(
        ["git", "diff", "--cached", "--quiet", "--", partition_path],
        cwd=repo_dir,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode not in (0, 1):
        raise GitCommandError(("diff", "--cached", "--quiet"), result.returncode, result.stderr)
    return result.returncode == 1


@dataclass(frozen=True, slots=True)
class PublishResult:
    """The outcome of one ``publish_device_partition`` call."""

    pushed: bool
    commit_sha: str | None
    attempts: int


def commit_and_push(
    repo_dir: Path,
    *,
    paths: Sequence[str],
    branch: str,
    commit_message: str,
    max_retries: int = 3,
    on_progress: Callable[[str], None] | None = None,
) -> PublishResult:
    """Commit and push exactly ``paths``, never force-pushing.

    Returns ``pushed=False`` with no commit if none of ``paths`` exist on
    disk, or none of the ones that do have staged changes. Fetches and
    rebases onto the remote branch before every push attempt; on a
    non-fast-forward rejection (someone else pushed first), re-fetches,
    re-rebases, and retries up to ``max_retries`` times.

    ``on_progress``, if given, is called with a short message before each
    fetch+rebase+push attempt — the loop most likely to take a while or
    need a retry.
    """
    if max_retries < 1:
        raise ValueError("max_retries must be at least 1")
    progress = on_progress or (lambda _message: None)

    existing_paths = [path for path in paths if (repo_dir / path).exists()]
    if not existing_paths:
        # Nothing was staged on disk to begin with (e.g. an empty ledger) —
        # `git add` on a pathspec that matches nothing would otherwise error.
        return PublishResult(pushed=False, commit_sha=None, attempts=0)

    _run("add", "--", *existing_paths, cwd=repo_dir)

    if not any(_has_staged_changes(repo_dir, path) for path in existing_paths):
        return PublishResult(pushed=False, commit_sha=None, attempts=0)

    _run("commit", "-m", commit_message, cwd=repo_dir)

    attempts = 0
    while attempts < max_retries:
        attempts += 1
        progress(f"fetch+rebase onto origin/{branch} (attempt {attempts}/{max_retries})")
        _run("fetch", "origin", branch, cwd=repo_dir)
        try:
            _run("rebase", f"origin/{branch}", cwd=repo_dir)
        except GitCommandError:
            _run("rebase", "--abort", cwd=repo_dir)
            # Un-strand the commit just made: put its content back into the
            # index rather than leaving an unpushed commit that a later
            # call's staged-changes check can no longer see as "changed",
            # which would otherwise make a retry silently report nothing to
            # publish instead of surfacing the unresolved conflict again.
            _run("reset", "--soft", "HEAD~1", cwd=repo_dir)
            raise

        push = subprocess.run(
            ["git", "push", "origin", f"HEAD:{branch}"],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            check=False,
        )
        if push.returncode == 0:
            # Re-derive the SHA post-push: a rebase rewrites the commit
            # whenever origin actually advanced, so a SHA captured before
            # the loop can point at a commit that was never pushed.
            commit_sha = _run("rev-parse", "HEAD", cwd=repo_dir).strip()
            return PublishResult(pushed=True, commit_sha=commit_sha, attempts=attempts)

        if not any(marker in push.stderr for marker in _NON_FAST_FORWARD_MARKERS):
            raise GitCommandError(("push",), push.returncode, push.stderr)

        progress("push rejected (non-fast-forward) — another device published first, retrying")

    raise GitCommandError(
        ("push",), 1, f"exceeded {max_retries} retries without a fast-forward push"
    )


def publish_device_partition(
    repo_dir: Path,
    *,
    device_id: str,
    branch: str,
    commit_message: str,
    max_retries: int = 3,
    on_progress: Callable[[str], None] | None = None,
) -> PublishResult:
    """Commit and push only ``device_id``'s own data partition, never force-pushing.

    Assumes the caller has already written that device's daily record
    files into ``repo_dir/data/v1/devices/<device_id>/``.
    """
    return commit_and_push(
        repo_dir,
        paths=[_device_partition(device_id)],
        branch=branch,
        commit_message=commit_message,
        max_retries=max_retries,
        on_progress=on_progress,
    )


def shallow_clone(repo_url: str, dest: Path, *, branch: str = "main") -> Path:
    """Depth-1 single-branch clone of ``repo_url`` into a fresh ``dest`` directory."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    _run(
        "clone",
        "--depth",
        "1",
        "--branch",
        branch,
        "--single-branch",
        repo_url,
        str(dest),
        cwd=dest.parent,
    )
    return dest
