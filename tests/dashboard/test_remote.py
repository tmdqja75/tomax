import json
from pathlib import Path

import pytest

from tomax.dashboard import remote


def test_fetch_device_entries_reads_cloned_device_payloads(monkeypatch, tmp_path):
    def fake_shallow_clone(repo_url, dest, *, branch="main"):
        devices = Path(dest) / "data" / "v1" / "devices" / "devA"
        devices.mkdir(parents=True)
        (devices / "2026-07-10.json").write_text(json.dumps({"date": "2026-07-10"}))
        (devices / "latest.json").write_text(json.dumps({"date": "2026-07-11"}))
        return Path(dest)

    monkeypatch.setattr(remote, "shallow_clone", fake_shallow_clone)

    entries = remote.fetch_device_entries("owner/repo")

    assert ("devA", {"date": "2026-07-10"}) in entries
    assert ("devA", {"date": "2026-07-11"}) in entries


def test_fetch_device_entries_requires_repo_target():
    with pytest.raises(remote.NoRepoTargetError):
        remote.fetch_device_entries(None)


def test_fetch_device_entries_excludes_given_device_id(monkeypatch, tmp_path):
    def fake_shallow_clone(repo_url, dest, *, branch="main"):
        devices = Path(dest) / "data" / "v1" / "devices"
        (devices / "devA").mkdir(parents=True)
        (devices / "devA" / "2026-07-10.json").write_text(json.dumps({"date": "2026-07-10"}))
        (devices / "devB").mkdir(parents=True)
        (devices / "devB" / "2026-07-10.json").write_text(json.dumps({"date": "2026-07-10"}))
        return Path(dest)

    monkeypatch.setattr(remote, "shallow_clone", fake_shallow_clone)

    entries = remote.fetch_device_entries("owner/repo", exclude_device_id="devA")

    assert [device_id for device_id, _ in entries] == ["devB"]
