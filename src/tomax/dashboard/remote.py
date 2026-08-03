"""Fetch multi-device published records by shallow-cloning the profile repo.

Used by ``tomax dashboard --all-devices``. Clones the configured
profile repository into a temporary directory, reads every device's public
daily records under ``data/v1/devices/**``, and returns them as
``(device_id, payload)`` entries for ``validate_and_partition``. The clone is
always removed afterward.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from tomax.publish.git import shallow_clone

_DEVICES_SUBPATH = Path("data") / "v1" / "devices"


class NoRepoTargetError(Exception):
    """Raised when --all-devices is used but no profile repo target is configured."""


def _read_entries(devices_root: Path, *, exclude_device_id: str | None) -> list[tuple[str, dict]]:
    entries: list[tuple[str, dict]] = []
    if not devices_root.is_dir():
        return entries
    for device_dir in sorted(devices_root.iterdir()):
        if not device_dir.is_dir() or device_dir.name == exclude_device_id:
            continue
        device_id = device_dir.name
        for json_path in sorted(device_dir.glob("*.json")):
            payload = json.loads(json_path.read_text(encoding="utf-8"))
            entries.append((device_id, payload))
    return entries


def fetch_device_entries(
    repo_target: str | None, *, branch: str = "main", exclude_device_id: str | None = None
) -> list[tuple[str, dict]]:
    """Shallow-clone the profile repo and return every other device's daily records.

    ``exclude_device_id`` skips this device's own remote directory — the
    caller merges its local ledger data in separately, which is fresher than
    whatever it last published.
    """
    if not repo_target:
        raise NoRepoTargetError(
            "no repo target set — run `tomax init --repo OWNER/REPO` first"
        )
    repo_url = f"https://github.com/{repo_target}.git"
    with tempfile.TemporaryDirectory(prefix="tomax-dash-") as tmp:
        clone_dir = Path(tmp) / "profile-repo"
        shallow_clone(repo_url, clone_dir, branch=branch)
        return _read_entries(clone_dir / _DEVICES_SUBPATH, exclude_device_id=exclude_device_id)
