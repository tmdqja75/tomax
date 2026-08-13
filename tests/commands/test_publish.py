"""Tests for the publish command: the gh-auth gate and end-to-end staging + push.

Git operations run against a local bare "origin" repository, never a real
GitHub remote. ``gh auth status`` is always faked via dependency injection
(or a monkeypatched subprocess) so these tests never depend on this
machine's real GitHub CLI login state.
"""

from __future__ import annotations

import subprocess
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

import tomax.commands.init as init_module
import tomax.commands.publish as publish_module
from tomax.commands.init import WORKFLOW_RELATIVE_PATH
from tomax.commands.publish import GhAuthError, check_gh_auth, publish
from tomax.ledger.repository import LedgerRepository
from tomax.models import NormalizedUsageRecord, SupportedAgent, TokenUsage
from tomax.publish.git import clone_or_open

UTC = timezone.utc
TODAY = date(2026, 7, 18)


def _run(cwd: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)
    return result.stdout


def _init_bare_origin(tmp_path: Path) -> Path:
    seed = tmp_path / "seed"
    seed.mkdir()
    _run(seed, "init", "-b", "main")
    _run(seed, "config", "user.email", "test@example.com")
    _run(seed, "config", "user.name", "Test Author")
    (seed / "README.md").write_text("seed\n", encoding="utf-8")
    _run(seed, "add", "README.md")
    _run(seed, "commit", "-m", "seed")

    bare = tmp_path / "origin.git"
    subprocess.run(
        ["git", "clone", "--bare", str(seed), str(bare)], check=True, capture_output=True
    )
    return bare


def _insert_record(ledger_path: Path) -> None:
    record = NormalizedUsageRecord(
        agent=SupportedAgent.CLAUDE_CODE,
        occurred_at=datetime(2026, 7, 10, 12, 0, tzinfo=UTC),
        fingerprint="fp-1",
        session_fingerprint="s-1",
        tokens=TokenUsage(input_tokens=10, output_tokens=5),
    )
    repository = LedgerRepository.open(ledger_path)
    try:
        repository.insert_records([record])
    finally:
        repository.close()


def _noop_gh_auth() -> str:
    return "ok"


# --- check_gh_auth ---------------------------------------------------------


def test_check_gh_auth_raises_when_not_authenticated(monkeypatch) -> None:
    def _fake_run(args, **kwargs):
        class _Result:
            returncode = 1
            stdout = ""
            stderr = "You are not logged into any GitHub hosts."

        return _Result()

    monkeypatch.setattr(publish_module.subprocess, "run", _fake_run)

    with pytest.raises(GhAuthError):
        check_gh_auth()


def test_check_gh_auth_returns_output_when_authenticated(monkeypatch) -> None:
    def _fake_run(args, **kwargs):
        class _Result:
            returncode = 0
            stdout = "Logged in to github.com account example-user"
            stderr = ""

        return _Result()

    monkeypatch.setattr(publish_module.subprocess, "run", _fake_run)

    output = check_gh_auth()

    assert "example-user" in output


# --- publish: gh-auth gate ---------------------------------------------------


def test_publish_never_touches_git_when_gh_auth_check_fails(tmp_path) -> None:
    ledger_path = tmp_path / "ledger.sqlite3"
    _insert_record(ledger_path)

    def _failing_gh_auth() -> str:
        raise GhAuthError("not logged in")

    with pytest.raises(GhAuthError):
        publish(
            ledger_path=ledger_path,
            repo_url="/this/path/does/not/exist.git",
            clone_dir=tmp_path / "clone",
            today=TODAY,
            gh_auth_check=_failing_gh_auth,
        )

    assert not (tmp_path / "clone").exists()


# --- publish: end-to-end staging + push against a local origin -------------


def test_publish_stages_and_pushes_this_devices_records(tmp_path) -> None:
    origin = _init_bare_origin(tmp_path)
    ledger_path = tmp_path / "ledger.sqlite3"
    _insert_record(ledger_path)

    summary = publish(
        ledger_path=ledger_path,
        repo_url=str(origin),
        clone_dir=tmp_path / "clone",
        today=TODAY,
        gh_auth_check=_noop_gh_auth,
    )

    assert summary.days_staged == 1
    assert summary.result.pushed is True

    verify_dir = tmp_path / "verify"
    clone_or_open(str(origin), verify_dir, branch="main")
    staged = verify_dir / "data" / "v1" / "devices" / summary.device_id / "2026-07-10.json"
    assert staged.exists()


def test_publish_is_a_no_op_for_an_empty_ledger(tmp_path) -> None:
    origin = _init_bare_origin(tmp_path)
    ledger_path = tmp_path / "ledger.sqlite3"

    summary = publish(
        ledger_path=ledger_path,
        repo_url=str(origin),
        clone_dir=tmp_path / "clone",
        today=TODAY,
        gh_auth_check=_noop_gh_auth,
    )

    assert summary.days_staged == 0
    assert summary.result.pushed is False


def test_publish_reuses_an_existing_clone_on_a_second_run(tmp_path) -> None:
    origin = _init_bare_origin(tmp_path)
    ledger_path = tmp_path / "ledger.sqlite3"
    _insert_record(ledger_path)
    clone_dir = tmp_path / "clone"

    first = publish(
        ledger_path=ledger_path,
        repo_url=str(origin),
        clone_dir=clone_dir,
        today=TODAY,
        gh_auth_check=_noop_gh_auth,
    )
    second = publish(
        ledger_path=ledger_path,
        repo_url=str(origin),
        clone_dir=clone_dir,
        today=TODAY,
        gh_auth_check=_noop_gh_auth,
    )

    assert first.result.pushed is True
    assert second.result.pushed is False  # nothing changed since the first publish


# --- publish: dashboard workflow sync ---------------------------------------


def test_publish_refreshes_a_stale_dashboard_workflow_file(tmp_path) -> None:
    origin = _init_bare_origin(tmp_path)
    seed_clone = clone_or_open(str(origin), tmp_path / "seed-clone", branch="main")
    _run(seed_clone, "config", "user.email", "test@example.com")
    _run(seed_clone, "config", "user.name", "Test Author")
    workflow_path = seed_clone / WORKFLOW_RELATIVE_PATH
    workflow_path.parent.mkdir(parents=True)
    workflow_path.write_text("name: stale\n", encoding="utf-8")
    _run(seed_clone, "add", WORKFLOW_RELATIVE_PATH)
    _run(seed_clone, "commit", "-m", "seed stale workflow")
    _run(seed_clone, "push", "origin", "main")

    ledger_path = tmp_path / "ledger.sqlite3"
    _insert_record(ledger_path)

    summary = publish(
        ledger_path=ledger_path,
        repo_url=str(origin),
        clone_dir=tmp_path / "clone",
        today=TODAY,
        gh_auth_check=_noop_gh_auth,
    )

    assert summary.result.pushed is True

    verify_dir = tmp_path / "verify"
    clone_or_open(str(origin), verify_dir, branch="main")
    current_template = init_module._WORKFLOW_TEMPLATE_PATH.read_text(encoding="utf-8")
    assert (verify_dir / WORKFLOW_RELATIVE_PATH).read_text(encoding="utf-8") == current_template


def test_publish_never_installs_a_workflow_file_that_was_never_registered(tmp_path) -> None:
    origin = _init_bare_origin(tmp_path)
    ledger_path = tmp_path / "ledger.sqlite3"
    _insert_record(ledger_path)

    publish(
        ledger_path=ledger_path,
        repo_url=str(origin),
        clone_dir=tmp_path / "clone",
        today=TODAY,
        gh_auth_check=_noop_gh_auth,
    )

    verify_dir = tmp_path / "verify"
    clone_or_open(str(origin), verify_dir, branch="main")
    assert (verify_dir / WORKFLOW_RELATIVE_PATH).exists() is False
