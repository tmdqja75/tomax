"""End-to-end tests for the interactive `tomax init` CLI command.

Every test runs against a local bare git "origin" and injects
`check_gh_auth`/prompts via monkeypatching — never a real GitHub remote or
a real gh CLI login.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from typer.testing import CliRunner

import tomax.cli as cli_module
from tomax.cli import app
from tomax.publish.git import clone_or_open
from tomax.render.markdown import MARKER_START

runner = CliRunner()


def _run(cwd: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)
    return result.stdout


def _init_bare_origin(tmp_path: Path) -> Path:
    seed = tmp_path / "seed"
    seed.mkdir()
    _run(seed, "init", "-b", "main")
    _run(seed, "config", "user.email", "test@example.com")
    _run(seed, "config", "user.name", "Test Author")
    (seed / "README.md").write_text("# My Profile\n\nHello.\n", encoding="utf-8")
    _run(seed, "add", "README.md")
    _run(seed, "commit", "-m", "seed")
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "clone", "--bare", str(seed), str(bare)], check=True, capture_output=True)
    return bare


def _patch_env(monkeypatch, tmp_path, *, repo_target="owner/repo", origin=None):
    monkeypatch.setattr(
        cli_module, "config_file_path", lambda: tmp_path / "config.json"
    )
    monkeypatch.setattr(
        cli_module, "ledger_file_path", lambda: tmp_path / "data" / "ledger.sqlite3"
    )
    monkeypatch.setattr(cli_module, "check_gh_auth", lambda: "ok")
    if origin is not None:
        monkeypatch.setattr(
            cli_module, "clone_or_open",
            lambda repo_url, clone_dir, branch: clone_or_open(str(origin), clone_dir, branch=branch),
        )


def test_init_without_dashboard_flag_never_calls_gh_auth(tmp_path, monkeypatch):
    _patch_env(monkeypatch, tmp_path)
    calls = []
    monkeypatch.setattr(cli_module, "check_gh_auth", lambda: calls.append(1))

    result = runner.invoke(app, ["init", "--repo", "owner/repo", "--no-dashboard"])

    assert result.exit_code == 0
    assert calls == []


def test_init_with_dashboard_flags_registers_non_interactively(tmp_path, monkeypatch):
    origin = _init_bare_origin(tmp_path)
    _patch_env(monkeypatch, tmp_path, origin=origin)

    result = runner.invoke(
        app,
        ["init", "--repo", "owner/repo", "--dashboard", "--insert-line", "2", "--yes"],
    )

    assert result.exit_code == 0, result.output
    assert "registered" in result.output

    verify_dir = tmp_path / "verify"
    clone_or_open(str(origin), verify_dir, branch="main")
    readme = (verify_dir / "README.md").read_text(encoding="utf-8")
    assert MARKER_START in readme
    assert (verify_dir / ".github" / "workflows" / "tomax-dashboard.yml").exists()


def test_init_reports_already_registered_without_reprompting(tmp_path, monkeypatch):
    origin = _init_bare_origin(tmp_path)
    seed_clone = clone_or_open(str(origin), tmp_path / "seed-clone", branch="main")
    _run(seed_clone, "config", "user.email", "test@example.com")
    _run(seed_clone, "config", "user.name", "Test Author")
    from tomax.commands.init import register_dashboard

    register_dashboard(seed_clone, branch="main", after_line=None)
    _patch_env(monkeypatch, tmp_path, origin=origin)

    result = runner.invoke(
        app, ["init", "--repo", "owner/repo", "--dashboard", "--yes"]
    )

    assert result.exit_code == 0, result.output
    assert "already registered" in result.output
