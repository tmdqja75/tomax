"""Publish this device's sanitized daily aggregates to the configured profile repository.

Gated behind ``gh auth status``: this command never stores or manages its
own GitHub credentials, it only shells out to the already-authenticated
``gh``/``git`` toolchain, and refuses to touch the remote at all if that
check fails.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from tomax.ledger.repository import LedgerRepository
from tomax.privacy import PrivacyPolicy
from tomax.public_data import stage_daily_records
from tomax.publish.git import PublishResult, clone_or_open, publish_device_partition


class GhAuthError(RuntimeError):
    """``gh auth status`` did not report an authenticated session."""


def check_gh_auth() -> str:
    """Return ``gh auth status``'s raw output if authenticated; raise otherwise."""
    result = subprocess.run(
        ["gh", "auth", "status"], capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        raise GhAuthError(
            result.stderr.strip() or result.stdout.strip() or "gh auth status failed"
        )
    return result.stdout + result.stderr


@dataclass(frozen=True, slots=True)
class PublishSummary:
    device_id: str
    days_staged: int
    result: PublishResult


def publish(
    *,
    ledger_path: Path,
    repo_url: str,
    clone_dir: Path,
    branch: str = "main",
    privacy_policy: PrivacyPolicy = PrivacyPolicy(),
    today: date,
    gh_auth_check: Callable[[], str] | None = None,
    on_progress: Callable[[str], None] | None = None,
) -> PublishSummary:
    """Stage this device's own daily records and push them to ``repo_url``.

    ``gh_auth_check`` defaults to the module-level ``check_gh_auth`` looked
    up at call time (not baked into the signature as a default-argument
    value), so monkeypatching ``tomax.commands.publish.check_gh_auth``
    — the same pattern used for this project's other default sources/paths
    — actually takes effect for callers, like the CLI, that don't override it.

    ``on_progress``, if given, is called with a short human-readable message
    before each potentially slow step (gh auth check, ledger read, repo
    clone/fetch, staging, commit+push) — the same optional-callback pattern
    ``on_collected`` uses in :mod:`tomax.commands.dashboard`.
    """
    progress = on_progress or (lambda _message: None)

    progress("checking gh auth status")
    (gh_auth_check or check_gh_auth)()

    progress(f"reading local ledger at {ledger_path}")
    repository = LedgerRepository.open(ledger_path)
    try:
        device_id = repository.get_or_create_device_id()
        records = repository.list_records()
    finally:
        repository.close()

    progress(f"cloning/opening profile repo at {clone_dir}")
    repo_dir = clone_or_open(repo_url, clone_dir, branch=branch)

    progress(f"staging sanitized daily aggregates for device {device_id}")
    device_dir = repo_dir / "data" / "v1" / "devices" / device_id
    payloads = stage_daily_records(
        device_dir, device_id=device_id, records=records, privacy_policy=privacy_policy
    )

    progress("committing and pushing device partition")
    result = publish_device_partition(
        repo_dir,
        device_id=device_id,
        branch=branch,
        commit_message=f"chore: update {device_id} daily aggregates ({today.isoformat()})",
        on_progress=on_progress,
    )

    return PublishSummary(device_id=device_id, days_staged=len(payloads), result=result)
