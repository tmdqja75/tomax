"""Tests for local, network-free profile-repo setup."""

from __future__ import annotations

import subprocess

import pytest

from tomax.commands.init import (
    DashboardRegistrationResult,
    init,
    numbered_readme_lines,
    preview_insertion,
    readme_already_registered,
    register_dashboard,
)
from tomax.config import AppConfig, get_or_create_device_id, load_config, save_config
from tomax.publish.git import clone_or_open
from tomax.render.markdown import MARKER_END, MARKER_START, render_dashboard_markdown


def test_init_sets_repo_target(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    ledger_path = tmp_path / "ledger.sqlite3"

    config = init("tmdqja75/tmdqja75", config_path=config_path, ledger_path=ledger_path)

    assert config.repo_target == "tmdqja75/tmdqja75"
    assert load_config(config_path).repo_target == "tmdqja75/tmdqja75"


def test_init_creates_the_local_ledger_and_a_device_id(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    ledger_path = tmp_path / "ledger.sqlite3"

    init("tmdqja75/tmdqja75", config_path=config_path, ledger_path=ledger_path)

    assert ledger_path.exists()
    device_id = get_or_create_device_id(ledger_path)
    assert len(device_id) == 36


def test_init_preserves_other_existing_config_fields(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    ledger_path = tmp_path / "ledger.sqlite3"
    save_config(
        config_path,
        AppConfig(display_timezone="America/Los_Angeles", privacy_allow=("safe-skill",)),
    )

    config = init("tmdqja75/tmdqja75", config_path=config_path, ledger_path=ledger_path)

    assert config.display_timezone == "America/Los_Angeles"
    assert config.privacy_allow == ("safe-skill",)


def test_init_rejects_a_malformed_repo_target(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    ledger_path = tmp_path / "ledger.sqlite3"

    with pytest.raises(ValueError, match="repo_target"):
        init("not-a-valid-target", config_path=config_path, ledger_path=ledger_path)


def test_init_does_not_write_a_config_file_when_the_repo_target_is_invalid(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    ledger_path = tmp_path / "ledger.sqlite3"

    with pytest.raises(ValueError):
        init("not-a-valid-target", config_path=config_path, ledger_path=ledger_path)

    assert not config_path.exists()


def test_init_run_twice_does_not_change_the_device_id(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    ledger_path = tmp_path / "ledger.sqlite3"

    init("tmdqja75/tmdqja75", config_path=config_path, ledger_path=ledger_path)
    first_device_id = get_or_create_device_id(ledger_path)
    init("tmdqja75/tmdqja75", config_path=config_path, ledger_path=ledger_path)
    second_device_id = get_or_create_device_id(ledger_path)

    assert first_device_id == second_device_id


def test_init_never_writes_a_github_token_or_hostname(tmp_path) -> None:
    import json

    config_path = tmp_path / "config.json"
    ledger_path = tmp_path / "ledger.sqlite3"

    init("tmdqja75/tmdqja75", config_path=config_path, ledger_path=ledger_path)

    serialized = json.dumps(json.loads(config_path.read_text(encoding="utf-8")))
    assert "ghp_" not in serialized
    assert "gho_" not in serialized
    assert "/Users/" not in serialized


# --- dashboard registration --------------------------------------------


def _run(cwd, *args):
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)
    return result.stdout


def _init_bare_origin(tmp_path, *, readme_text="seed\n"):
    seed = tmp_path / "seed"
    seed.mkdir()
    _run(seed, "init", "-b", "main")
    _run(seed, "config", "user.email", "test@example.com")
    _run(seed, "config", "user.name", "Test Author")
    (seed / "README.md").write_text(readme_text, encoding="utf-8")
    _run(seed, "add", "README.md")
    _run(seed, "commit", "-m", "seed")
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "clone", "--bare", str(seed), str(bare)], check=True, capture_output=True)
    return bare


def test_readme_already_registered_is_false_for_plain_readme():
    assert readme_already_registered("# Title\n\nHello\n") is False


def test_readme_already_registered_is_true_once_markers_present():
    text = f"# Title\n\n{MARKER_START}\nold\n{MARKER_END}\n"
    assert readme_already_registered(text) is True


def test_numbered_readme_lines_prefixes_each_line():
    result = numbered_readme_lines("line1\nline2\n")
    assert result == ["1  line1", "2  line2"]


def test_numbered_readme_lines_is_empty_for_empty_readme():
    assert numbered_readme_lines("") == []


def test_preview_insertion_returns_updated_readme_and_a_scoped_preview():
    existing = "line1\nline2\nline3\n"
    section = render_dashboard_markdown()

    updated, preview = preview_insertion(existing, after_line=1, dashboard_markdown=section)

    assert MARKER_START in updated and MARKER_END in updated
    assert "line1" in preview and "line3" in preview
    # The preview must not dump the whole README when it's long.
    assert preview.count("\n") < updated.count("\n")


def test_register_dashboard_writes_readme_and_workflow_then_pushes(tmp_path):
    origin = _init_bare_origin(tmp_path)
    repo_dir = clone_or_open(str(origin), tmp_path / "clone", branch="main")
    _run(repo_dir, "config", "user.email", "test@example.com")
    _run(repo_dir, "config", "user.name", "Test Author")

    result = register_dashboard(repo_dir, branch="main", after_line=None)

    assert result.status == "registered"
    assert result.commit_sha is not None

    verify_dir = tmp_path / "verify"
    clone_or_open(str(origin), verify_dir, branch="main")
    readme = (verify_dir / "README.md").read_text(encoding="utf-8")
    assert MARKER_START in readme and "seed" in readme
    assert (verify_dir / ".github" / "workflows" / "tomax-dashboard.yml").exists()


def test_register_dashboard_skips_when_already_registered(tmp_path):
    existing = f"# Title\n\n{MARKER_START}\nold\n{MARKER_END}\n"
    origin = _init_bare_origin(tmp_path, readme_text=existing)
    repo_dir = clone_or_open(str(origin), tmp_path / "clone", branch="main")
    _run(repo_dir, "config", "user.email", "test@example.com")
    _run(repo_dir, "config", "user.name", "Test Author")

    result = register_dashboard(repo_dir, branch="main", after_line=None)

    assert result == DashboardRegistrationResult(status="already_registered", commit_sha=None)
    assert (repo_dir / ".github" / "workflows" / "tomax-dashboard.yml").exists() is False
