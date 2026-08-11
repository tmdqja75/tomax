"""Tests for safe git publishing plumbing, against local bare repositories.

Every test operates entirely on temporary local git repositories (a bare
"origin" plus one or more working clones) — never a real GitHub remote —
so this suite never performs any real network or GitHub side effect.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tomax.publish.git import (
    GitCommandError,
    PublishResult,
    clone_or_open,
    commit_and_push,
    publish_device_partition,
)


def _run(cwd: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)
    return result.stdout


def _configure_identity(repo_dir: Path) -> None:
    _run(repo_dir, "config", "user.email", "test@example.com")
    _run(repo_dir, "config", "user.name", "Test Author")


def _init_bare_origin(tmp_path: Path) -> Path:
    seed = tmp_path / "seed"
    seed.mkdir()
    _run(seed, "init", "-b", "main")
    _configure_identity(seed)
    (seed / "README.md").write_text("seed\n", encoding="utf-8")
    _run(seed, "add", "README.md")
    _run(seed, "commit", "-m", "seed")

    bare = tmp_path / "origin.git"
    subprocess.run(
        ["git", "clone", "--bare", str(seed), str(bare)], check=True, capture_output=True
    )
    return bare


def _clone(origin: Path, dest: Path) -> Path:
    repo_dir = clone_or_open(str(origin), dest, branch="main")
    _configure_identity(repo_dir)
    return repo_dir


def _write_device_file(repo_dir: Path, device_id: str, filename: str, content: str = "{}") -> None:
    device_dir = repo_dir / "data" / "v1" / "devices" / device_id
    device_dir.mkdir(parents=True, exist_ok=True)
    (device_dir / filename).write_text(content, encoding="utf-8")


# --- clone_or_open --------------------------------------------------------


def test_clone_or_open_clones_a_fresh_repository(tmp_path) -> None:
    origin = _init_bare_origin(tmp_path)

    repo_dir = clone_or_open(str(origin), tmp_path / "clone", branch="main")

    assert (repo_dir / ".git").is_dir()
    assert (repo_dir / "README.md").exists()


def test_clone_or_open_reuses_an_existing_clone(tmp_path) -> None:
    origin = _init_bare_origin(tmp_path)
    dest = tmp_path / "clone"

    first = clone_or_open(str(origin), dest, branch="main")
    (first / "local-only.txt").write_text("untouched\n", encoding="utf-8")
    second = clone_or_open(str(origin), dest, branch="main")

    assert second == first
    assert (second / "local-only.txt").exists()


# --- publish_device_partition: no-op / basic publish -----------------------


def test_publish_is_a_no_op_when_the_partition_directory_does_not_exist(tmp_path) -> None:
    origin = _init_bare_origin(tmp_path)
    repo_dir = _clone(origin, tmp_path / "clone")

    result = publish_device_partition(
        repo_dir, device_id="device-a", branch="main", commit_message="chore: update"
    )

    assert result == PublishResult(pushed=False, commit_sha=None, attempts=0)


def test_publish_is_a_no_op_when_nothing_changed(tmp_path) -> None:
    origin = _init_bare_origin(tmp_path)
    repo_dir = _clone(origin, tmp_path / "clone")
    _write_device_file(repo_dir, "device-a", "2026-07-10.json")

    first = publish_device_partition(
        repo_dir, device_id="device-a", branch="main", commit_message="chore: update"
    )
    second = publish_device_partition(
        repo_dir, device_id="device-a", branch="main", commit_message="chore: update"
    )

    assert first.pushed is True
    assert second == PublishResult(pushed=False, commit_sha=None, attempts=0)


def test_publish_commits_and_pushes_the_device_partition(tmp_path) -> None:
    origin = _init_bare_origin(tmp_path)
    repo_dir = _clone(origin, tmp_path / "clone")
    _write_device_file(repo_dir, "device-a", "2026-07-10.json", '{"date": "2026-07-10"}')

    result = publish_device_partition(
        repo_dir, device_id="device-a", branch="main", commit_message="chore: update device-a"
    )

    assert result.pushed is True
    assert result.commit_sha is not None
    assert result.attempts == 1

    verify_dir = tmp_path / "verify"
    clone_or_open(str(origin), verify_dir, branch="main")
    assert (verify_dir / "data" / "v1" / "devices" / "device-a" / "2026-07-10.json").exists()


def test_publish_only_stages_the_given_devices_partition(tmp_path) -> None:
    origin = _init_bare_origin(tmp_path)
    repo_dir = _clone(origin, tmp_path / "clone")
    _write_device_file(repo_dir, "device-a", "2026-07-10.json")
    # An unrelated stray file elsewhere in the working tree must never be
    # swept into device-a's commit.
    (repo_dir / "unrelated-scratch-file.txt").write_text("scratch\n", encoding="utf-8")

    publish_device_partition(
        repo_dir, device_id="device-a", branch="main", commit_message="chore: update device-a"
    )

    committed_files = _run(repo_dir, "show", "--name-only", "--pretty=format:", "HEAD").split()
    assert "unrelated-scratch-file.txt" not in committed_files
    assert any("device-a" in path for path in committed_files)


def test_publish_never_touches_another_devices_partition(tmp_path) -> None:
    origin = _init_bare_origin(tmp_path)
    repo_dir = _clone(origin, tmp_path / "clone")
    _write_device_file(repo_dir, "device-a", "2026-07-10.json")
    _write_device_file(repo_dir, "device-b", "2026-07-10.json")

    publish_device_partition(
        repo_dir, device_id="device-a", branch="main", commit_message="chore: update device-a"
    )

    status = _run(repo_dir, "status", "--porcelain")
    assert "device-b" in status  # still untracked/unstaged, never committed by device-a's publish


# --- conflict recovery: fetch + rebase + retry, never force-push -----------


def test_publish_recovers_from_a_concurrent_push_by_another_device(tmp_path) -> None:
    origin = _init_bare_origin(tmp_path)
    clone_a = _clone(origin, tmp_path / "clone-a")
    clone_b = _clone(origin, tmp_path / "clone-b")

    _write_device_file(clone_b, "device-b", "2026-07-10.json")
    concurrent_result = publish_device_partition(
        clone_b, device_id="device-b", branch="main", commit_message="chore: update device-b"
    )
    assert concurrent_result.pushed is True

    _write_device_file(clone_a, "device-a", "2026-07-10.json")
    result = publish_device_partition(
        clone_a, device_id="device-a", branch="main", commit_message="chore: update device-a"
    )

    assert result.pushed is True
    assert result.attempts >= 1

    verify_dir = tmp_path / "verify"
    clone_or_open(str(origin), verify_dir, branch="main")
    assert (verify_dir / "data" / "v1" / "devices" / "device-a" / "2026-07-10.json").exists()
    assert (verify_dir / "data" / "v1" / "devices" / "device-b" / "2026-07-10.json").exists()


def test_publish_never_force_pushes_over_a_concurrent_commit(tmp_path) -> None:
    origin = _init_bare_origin(tmp_path)
    clone_a = _clone(origin, tmp_path / "clone-a")
    clone_b = _clone(origin, tmp_path / "clone-b")

    _write_device_file(clone_b, "device-b", "2026-07-10.json")
    publish_device_partition(
        clone_b, device_id="device-b", branch="main", commit_message="chore: update device-b"
    )
    concurrent_log = _run(clone_b, "log", "--oneline", "main")

    _write_device_file(clone_a, "device-a", "2026-07-10.json")
    publish_device_partition(
        clone_a, device_id="device-a", branch="main", commit_message="chore: update device-a"
    )

    verify_dir = tmp_path / "verify"
    clone_or_open(str(origin), verify_dir, branch="main")
    final_log = _run(verify_dir, "log", "--oneline", "main")
    for line in concurrent_log.strip().splitlines():
        commit_subject = line.split(" ", 1)[1]
        assert commit_subject in final_log


def test_publish_raises_after_exhausting_retries_on_persistent_conflict(tmp_path, monkeypatch) -> None:
    origin = _init_bare_origin(tmp_path)
    repo_dir = _clone(origin, tmp_path / "clone")
    _write_device_file(repo_dir, "device-a", "2026-07-10.json")

    import tomax.publish.git as git_module

    real_run = subprocess.run

    class _RejectedPush:
        returncode = 1
        stderr = "! [rejected] main -> main (non-fast-forward)"
        stdout = ""

    push_attempts = 0

    def _fake_run(args, **kwargs):
        # Only the actual `git push` is faked as perpetually rejected — add,
        # commit, fetch, and rebase must keep running for real, or the retry
        # loop under test would never even reach its push attempts.
        nonlocal push_attempts
        if isinstance(args, list) and "push" in args:
            push_attempts += 1
            return _RejectedPush()
        return real_run(args, **kwargs)

    monkeypatch.setattr(git_module.subprocess, "run", _fake_run)

    with pytest.raises(GitCommandError, match="retries"):
        publish_device_partition(
            repo_dir,
            device_id="device-a",
            branch="main",
            commit_message="chore: update device-a",
            max_retries=2,
        )

    assert push_attempts == 2


def test_publish_returns_the_actual_pushed_commit_sha_after_a_rebase(tmp_path) -> None:
    origin = _init_bare_origin(tmp_path)
    clone_a = _clone(origin, tmp_path / "clone-a")
    clone_b = _clone(origin, tmp_path / "clone-b")

    _write_device_file(clone_b, "device-b", "2026-07-10.json")
    publish_device_partition(
        clone_b, device_id="device-b", branch="main", commit_message="chore: update device-b"
    )

    _write_device_file(clone_a, "device-a", "2026-07-10.json")
    result = publish_device_partition(
        clone_a, device_id="device-a", branch="main", commit_message="chore: update device-a"
    )

    remote_head = _run(clone_a, "ls-remote", "origin", "main").split()[0]
    assert result.commit_sha == remote_head


def test_publish_does_not_silently_report_no_op_after_a_stranded_conflict(tmp_path) -> None:
    origin = _init_bare_origin(tmp_path)
    clone_1 = _clone(origin, tmp_path / "clone-1")
    clone_2 = _clone(origin, tmp_path / "clone-2")

    _write_device_file(clone_1, "device-a", "2026-07-10.json", '{"v": 1}')
    first = publish_device_partition(
        clone_1, device_id="device-a", branch="main", commit_message="chore: update device-a v1"
    )
    assert first.pushed is True

    _write_device_file(clone_2, "device-a", "2026-07-10.json", '{"v": 2}')
    with pytest.raises(GitCommandError):
        publish_device_partition(
            clone_2, device_id="device-a", branch="main", commit_message="chore: update device-a v2"
        )

    # A retry with the same unresolved conflicting content must keep
    # failing loudly, never silently report "nothing to publish" — that
    # would hide a commit that was never actually pushed.
    with pytest.raises(GitCommandError):
        publish_device_partition(
            clone_2,
            device_id="device-a",
            branch="main",
            commit_message="chore: update device-a v2 retry",
        )


def test_publish_rejects_a_non_positive_max_retries_without_committing(tmp_path) -> None:
    origin = _init_bare_origin(tmp_path)
    repo_dir = _clone(origin, tmp_path / "clone")
    _write_device_file(repo_dir, "device-a", "2026-07-10.json")
    head_before = _run(repo_dir, "rev-parse", "HEAD").strip()

    with pytest.raises(ValueError, match="max_retries"):
        publish_device_partition(
            repo_dir,
            device_id="device-a",
            branch="main",
            commit_message="chore: update device-a",
            max_retries=0,
        )

    assert _run(repo_dir, "rev-parse", "HEAD").strip() == head_before


def test_clone_or_open_raises_git_command_error_on_failure(tmp_path) -> None:
    with pytest.raises(GitCommandError):
        clone_or_open("/this/path/does/not/exist.git", tmp_path / "clone", branch="main")


# --- commit_and_push: generalized multi-path commit/push -------------------


def test_commit_and_push_stages_multiple_paths_together(tmp_path) -> None:
    origin = _init_bare_origin(tmp_path)
    repo_dir = _clone(origin, tmp_path / "clone")
    (repo_dir / "README.md").write_text("updated\n", encoding="utf-8")
    workflow_dir = repo_dir / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "tomax-dashboard.yml").write_text("name: x\n", encoding="utf-8")

    result = commit_and_push(
        repo_dir,
        paths=["README.md", ".github/workflows/tomax-dashboard.yml"],
        branch="main",
        commit_message="chore: install tomax usage dashboard",
    )

    assert result.pushed is True
    committed_files = _run(repo_dir, "show", "--name-only", "--pretty=format:", "HEAD").split()
    assert "README.md" in committed_files
    assert ".github/workflows/tomax-dashboard.yml" in committed_files


def test_commit_and_push_is_a_no_op_when_none_of_the_paths_exist(tmp_path) -> None:
    origin = _init_bare_origin(tmp_path)
    repo_dir = _clone(origin, tmp_path / "clone")

    result = commit_and_push(
        repo_dir, paths=["does-not-exist.txt"], branch="main", commit_message="chore: x"
    )

    assert result == PublishResult(pushed=False, commit_sha=None, attempts=0)


def test_publish_device_partition_still_behaves_identically(tmp_path) -> None:
    origin = _init_bare_origin(tmp_path)
    repo_dir = _clone(origin, tmp_path / "clone")
    _write_device_file(repo_dir, "device-a", "2026-07-10.json", '{"date": "2026-07-10"}')

    result = publish_device_partition(
        repo_dir, device_id="device-a", branch="main", commit_message="chore: update device-a"
    )

    assert result.pushed is True
    assert result.commit_sha is not None
