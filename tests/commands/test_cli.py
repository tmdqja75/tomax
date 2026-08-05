"""Tests for CLI command wiring."""

from __future__ import annotations

import re

from typer.testing import CliRunner

import tomax.cli as cli_module
from tomax.cli import app
from tomax.commands.collect import AgentCollectionResult
from tomax.models import SourceStatus, SupportedAgent

runner = CliRunner()

_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(text: str) -> str:
    return _ANSI_ESCAPE.sub("", text)


def _patch_local_paths(monkeypatch, tmp_path):
    monkeypatch.setattr(cli_module, "config_file_path", lambda: tmp_path / "config.json")
    monkeypatch.setattr(cli_module, "ledger_file_path", lambda: tmp_path / "ledger.sqlite3")
    monkeypatch.setattr(cli_module, "data_dir", lambda: tmp_path / "data")


def _patch_missing_sources(monkeypatch, tmp_path):
    monkeypatch.setattr(cli_module, "DEFAULT_HERMES_STATE_DB", tmp_path / "missing-hermes.db")
    monkeypatch.setattr(
        cli_module, "DEFAULT_CLAUDE_CODE_PROJECTS_DIR", tmp_path / "missing-claude"
    )
    monkeypatch.setattr(cli_module, "DEFAULT_CODEX_SESSIONS_DIR", tmp_path / "missing-codex")


def test_help_lists_all_commands() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    for name in ("init", "doctor", "collect", "render", "publish", "schedule"):
        assert name in result.stdout


def test_schedule_help_lists_install_status_and_remove() -> None:
    result = runner.invoke(app, ["schedule", "--help"])

    assert result.exit_code == 0
    for name in ("install", "status", "remove"):
        assert name in result.stdout


def test_schedule_install_wires_local_paths_and_reports_the_time(tmp_path, monkeypatch) -> None:
    from tomax.commands.schedule import ScheduleInstallResult

    _patch_local_paths(monkeypatch, tmp_path)
    captured = {}

    def _fake_install(**kwargs):
        captured.update(kwargs)
        return ScheduleInstallResult(plist_path=tmp_path / "schedule.plist", daily_at="09:00")

    monkeypatch.setattr(cli_module.schedule_command, "install", _fake_install)

    result = runner.invoke(app, ["schedule", "install", "--daily-at", "09:00"])

    assert result.exit_code == 0
    assert captured["config_path"] == tmp_path / "config.json"
    assert captured["log_dir"] == tmp_path / "data" / "logs"
    assert "09:00" in result.stdout


def test_schedule_status_reports_not_installed_without_printing_a_local_path(tmp_path, monkeypatch) -> None:
    from tomax.schedule.launchd import ScheduleStatus

    _patch_local_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(
        cli_module.schedule_command,
        "status",
        lambda: ScheduleStatus(False, tmp_path / "schedule.plist", None, False),
    )

    result = runner.invoke(app, ["schedule", "status"])

    assert result.exit_code == 0
    assert "not installed" in result.stdout.lower()
    assert str(tmp_path) not in result.stdout


def test_schedule_remove_reports_when_nothing_was_installed(tmp_path, monkeypatch) -> None:
    _patch_local_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(cli_module.schedule_command, "remove", lambda **kwargs: False)

    result = runner.invoke(app, ["schedule", "remove"])

    assert result.exit_code == 0
    assert "not installed" in result.stdout.lower()


def test_init_command_sets_repo_target(tmp_path, monkeypatch) -> None:
    _patch_local_paths(monkeypatch, tmp_path)

    result = runner.invoke(app, ["init", "--repo", "tmdqja75/tmdqja75"])

    assert result.exit_code == 0
    assert "tmdqja75/tmdqja75" in result.stdout


def test_init_command_rejects_a_malformed_repo_target(tmp_path, monkeypatch) -> None:
    _patch_local_paths(monkeypatch, tmp_path)

    result = runner.invoke(app, ["init", "--repo", "not-valid"])

    assert result.exit_code != 0


def test_doctor_command_reports_unavailable_sources_with_no_real_sources_present(
    tmp_path, monkeypatch
) -> None:
    _patch_local_paths(monkeypatch, tmp_path)
    _patch_missing_sources(monkeypatch, tmp_path)

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 0
    assert "source_unavailable" in result.stdout
    assert "device id" in result.stdout


def test_collect_dry_run_reports_and_writes_nothing(tmp_path, monkeypatch) -> None:
    _patch_local_paths(monkeypatch, tmp_path)
    _patch_missing_sources(monkeypatch, tmp_path)

    result = runner.invoke(app, ["collect", "--dry-run"])

    assert result.exit_code == 0
    assert "dry run" in result.stdout.lower()
    ledger_path = tmp_path / "ledger.sqlite3"
    if ledger_path.exists():
        from tomax.ledger.repository import LedgerRepository

        repository = LedgerRepository.open(ledger_path)
        try:
            assert repository.list_records() == []
        finally:
            repository.close()


def test_collect_backfill_model_reports_scanned_and_backfilled(tmp_path, monkeypatch) -> None:
    _patch_local_paths(monkeypatch, tmp_path)
    _patch_missing_sources(monkeypatch, tmp_path)

    result = runner.invoke(app, ["collect", "--backfill-model"])

    assert result.exit_code == 0
    # a missing source's collect() still returns one source_unavailable
    # marker record, so "scanned" is 1 even with nothing to backfill.
    for agent in ("hermes_agent", "claude_code", "codex"):
        assert f"{agent}: scanned 1, backfilled 0" in result.stdout


def test_collect_backfill_model_rejects_dry_run(tmp_path, monkeypatch) -> None:
    _patch_local_paths(monkeypatch, tmp_path)
    _patch_missing_sources(monkeypatch, tmp_path)

    result = runner.invoke(app, ["collect", "--backfill-model", "--dry-run"])

    assert result.exit_code != 0


def test_collect_then_render_produces_a_local_preview(tmp_path, monkeypatch) -> None:
    _patch_local_paths(monkeypatch, tmp_path)
    _patch_missing_sources(monkeypatch, tmp_path)

    collect_result = runner.invoke(app, ["collect"])
    assert collect_result.exit_code == 0

    output_dir = tmp_path / "preview"
    render_result = runner.invoke(app, ["render", "--output-dir", str(output_dir)])

    assert render_result.exit_code == 0
    assert (output_dir / "README.md").exists()


def test_render_accepts_a_custom_pie_top_n(tmp_path, monkeypatch) -> None:
    _patch_local_paths(monkeypatch, tmp_path)
    _patch_missing_sources(monkeypatch, tmp_path)

    runner.invoke(app, ["collect"])
    output_dir = tmp_path / "preview"

    result = runner.invoke(app, ["render", "--output-dir", str(output_dir), "--pie-top-n", "3"])

    assert result.exit_code == 0
    assert (output_dir / "README.md").exists()


def test_render_rejects_a_pie_top_n_below_one(tmp_path, monkeypatch) -> None:
    _patch_local_paths(monkeypatch, tmp_path)
    _patch_missing_sources(monkeypatch, tmp_path)

    output_dir = tmp_path / "preview"
    result = runner.invoke(app, ["render", "--output-dir", str(output_dir), "--pie-top-n", "0"])

    assert result.exit_code != 0
    assert "pie-top-n" in _strip_ansi(result.output).lower()


def test_dashboard_rejects_an_invalid_lang(tmp_path, monkeypatch) -> None:
    _patch_local_paths(monkeypatch, tmp_path)
    _patch_missing_sources(monkeypatch, tmp_path)

    result = runner.invoke(app, ["dashboard", "--lang", "fr", "--no-open"])

    assert result.exit_code != 0
    assert "lang" in _strip_ansi(result.output).lower()


def test_dashboard_includes_cache_tokens_by_default(tmp_path, monkeypatch) -> None:
    _patch_local_paths(monkeypatch, tmp_path)
    _patch_missing_sources(monkeypatch, tmp_path)
    captured = {}

    def _fake_run(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(cli_module.dashboard_command, "run", _fake_run)

    result = runner.invoke(app, ["dashboard", "--no-open"])

    assert result.exit_code == 0
    assert captured["include_cache_tokens"] is True


def test_dashboard_claude_code_only_range_flag_is_threaded_through(tmp_path, monkeypatch) -> None:
    _patch_local_paths(monkeypatch, tmp_path)
    _patch_missing_sources(monkeypatch, tmp_path)
    captured = {}

    def _fake_run(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(cli_module.dashboard_command, "run", _fake_run)

    result = runner.invoke(app, ["dashboard", "--no-open", "--claude-code-only-range"])

    assert result.exit_code == 0
    assert captured["claude_code_only_range"] is True


def test_dashboard_claude_code_only_range_defaults_to_false(tmp_path, monkeypatch) -> None:
    _patch_local_paths(monkeypatch, tmp_path)
    _patch_missing_sources(monkeypatch, tmp_path)
    captured = {}

    def _fake_run(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(cli_module.dashboard_command, "run", _fake_run)

    result = runner.invoke(app, ["dashboard", "--no-open"])

    assert result.exit_code == 0
    assert captured["claude_code_only_range"] is False


def test_dashboard_logs_per_agent_collection_results(tmp_path, monkeypatch) -> None:
    _patch_local_paths(monkeypatch, tmp_path)
    _patch_missing_sources(monkeypatch, tmp_path)

    def _fake_run(**kwargs):
        kwargs["on_collected"](
            [
                AgentCollectionResult(
                    agent=SupportedAgent.CLAUDE_CODE,
                    status=SourceStatus.AVAILABLE_WITH_ACTIVITY,
                    records_observed=5,
                    records_inserted=3,
                ),
                AgentCollectionResult(
                    agent=SupportedAgent.CODEX,
                    status=SourceStatus.SOURCE_UNAVAILABLE,
                    records_observed=0,
                    records_inserted=0,
                ),
            ]
        )

    monkeypatch.setattr(cli_module.dashboard_command, "run", _fake_run)

    result = runner.invoke(app, ["dashboard", "--no-open"])

    assert result.exit_code == 0
    assert "claude_code: available_with_activity (observed 5, inserted 3)" in result.stdout
    assert "codex: source_unavailable (observed 0, inserted 0)" in result.stdout


def test_dashboard_exclude_cache_tokens_flag_is_threaded_through(tmp_path, monkeypatch) -> None:
    _patch_local_paths(monkeypatch, tmp_path)
    _patch_missing_sources(monkeypatch, tmp_path)
    captured = {}

    def _fake_run(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(cli_module.dashboard_command, "run", _fake_run)

    result = runner.invoke(app, ["dashboard", "--no-open", "--exclude-cache-tokens"])

    assert result.exit_code == 0
    assert captured["include_cache_tokens"] is False


def test_render_exclude_cache_tokens_flag_is_threaded_through(tmp_path, monkeypatch) -> None:
    _patch_local_paths(monkeypatch, tmp_path)
    _patch_missing_sources(monkeypatch, tmp_path)
    captured = {}

    def _fake_render(**kwargs):
        captured.update(kwargs)
        from tomax.commands.render import RenderResult

        return RenderResult(device_id="dev", readme_path=tmp_path / "README.md", changed=False)

    monkeypatch.setattr(cli_module.render_command, "render", _fake_render)

    output_dir = tmp_path / "preview"
    result = runner.invoke(
        app, ["render", "--output-dir", str(output_dir), "--exclude-cache-tokens"]
    )

    assert result.exit_code == 0
    assert captured["include_cache_tokens"] is False


def test_publish_command_requires_a_repo_target(tmp_path, monkeypatch) -> None:
    _patch_local_paths(monkeypatch, tmp_path)

    result = runner.invoke(app, ["publish"])

    assert result.exit_code != 0
    assert "init" in result.stdout.lower()


def test_publish_command_reports_a_clear_error_when_gh_auth_fails(tmp_path, monkeypatch) -> None:
    from tomax.config import AppConfig, save_config

    _patch_local_paths(monkeypatch, tmp_path)
    save_config(tmp_path / "config.json", AppConfig(repo_target="tmdqja75/tmdqja75"))

    def _fake_run(args, **kwargs):
        class _Result:
            returncode = 1
            stdout = ""
            stderr = "not logged in"

        return _Result()

    import tomax.commands.publish as publish_module

    monkeypatch.setattr(publish_module.subprocess, "run", _fake_run)

    result = runner.invoke(app, ["publish", "--clone-dir", str(tmp_path / "clone")])

    assert result.exit_code != 0
    assert "gh auth" in result.stdout.lower()
    assert not (tmp_path / "clone").exists()


def test_publish_command_reports_a_clear_error_when_git_operations_fail(
    tmp_path, monkeypatch
) -> None:
    from tomax.config import AppConfig, save_config
    from tomax.publish.git import GitCommandError

    _patch_local_paths(monkeypatch, tmp_path)
    save_config(tmp_path / "config.json", AppConfig(repo_target="tmdqja75/tmdqja75"))

    def _fake_publish(**kwargs):
        raise GitCommandError(("push",), 1, "! [rejected] main -> main (non-fast-forward)")

    monkeypatch.setattr(cli_module.publish_command, "publish", _fake_publish)

    result = runner.invoke(app, ["publish"])

    assert result.exit_code != 0
    assert "publish failed" in result.stdout.lower()
    assert not isinstance(result.exception, GitCommandError)


def test_config_start_date_requires_exactly_one_of_date_or_all(tmp_path, monkeypatch) -> None:
    _patch_local_paths(monkeypatch, tmp_path)

    result = runner.invoke(app, ["config", "start-date"])

    assert result.exit_code != 0


def test_config_start_date_rejects_both_date_and_all(tmp_path, monkeypatch) -> None:
    _patch_local_paths(monkeypatch, tmp_path)

    result = runner.invoke(app, ["config", "start-date", "--date", "2026-01-01", "--all"])

    assert result.exit_code != 0


def test_config_start_date_persists_a_custom_date(tmp_path, monkeypatch) -> None:
    import json

    _patch_local_paths(monkeypatch, tmp_path)

    result = runner.invoke(app, ["config", "start-date", "--date", "2026-01-01"])

    assert result.exit_code == 0
    config_path = tmp_path / "config.json"
    assert json.loads(config_path.read_text())["initial_collection_start"] == "2026-01-01"


def test_config_start_date_persists_all(tmp_path, monkeypatch) -> None:
    import json

    _patch_local_paths(monkeypatch, tmp_path)

    result = runner.invoke(app, ["config", "start-date", "--all"])

    assert result.exit_code == 0
    config_path = tmp_path / "config.json"
    assert json.loads(config_path.read_text())["initial_collection_start"] == "ALL"


def test_config_start_date_rejects_a_malformed_date(tmp_path, monkeypatch) -> None:
    _patch_local_paths(monkeypatch, tmp_path)

    result = runner.invoke(app, ["config", "start-date", "--date", "not-a-date"])

    assert result.exit_code != 0
    assert not (tmp_path / "config.json").exists()


def test_config_bar_chart_threshold_persists_a_custom_value(tmp_path, monkeypatch) -> None:
    import json

    _patch_local_paths(monkeypatch, tmp_path)

    result = runner.invoke(app, ["config", "bar-chart-threshold", "--days", "5"])

    assert result.exit_code == 0
    config_path = tmp_path / "config.json"
    assert json.loads(config_path.read_text())["bar_chart_threshold_days"] == 5


def test_config_bar_chart_threshold_rejects_a_non_positive_value(tmp_path, monkeypatch) -> None:
    _patch_local_paths(monkeypatch, tmp_path)

    result = runner.invoke(app, ["config", "bar-chart-threshold", "--days", "0"])

    assert result.exit_code != 0
    assert not (tmp_path / "config.json").exists()


def test_collect_uses_the_configured_start_date(tmp_path, monkeypatch) -> None:
    _patch_local_paths(monkeypatch, tmp_path)
    _patch_missing_sources(monkeypatch, tmp_path)

    runner.invoke(app, ["config", "start-date", "--all"])
    captured = {}
    original_collect_all = cli_module.collect_command.collect_all

    def _capturing_collect_all(**kwargs):
        captured.update(kwargs)
        return original_collect_all(**kwargs)

    monkeypatch.setattr(cli_module.collect_command, "collect_all", _capturing_collect_all)

    result = runner.invoke(app, ["collect", "--dry-run"])

    assert result.exit_code == 0
    from tomax.time_window import EPOCH_START

    assert captured["configured_start"] == EPOCH_START


def test_collect_includes_cache_tokens_by_default(tmp_path, monkeypatch) -> None:
    _patch_local_paths(monkeypatch, tmp_path)
    _patch_missing_sources(monkeypatch, tmp_path)
    captured = {}
    original_collect_all = cli_module.collect_command.collect_all

    def _capturing_collect_all(**kwargs):
        captured.update(kwargs)
        return original_collect_all(**kwargs)

    monkeypatch.setattr(cli_module.collect_command, "collect_all", _capturing_collect_all)

    result = runner.invoke(app, ["collect", "--dry-run"])

    assert result.exit_code == 0
    assert captured["include_cache_tokens"] is True


def test_collect_exclude_cache_tokens_flag_disables_cache_tracking(tmp_path, monkeypatch) -> None:
    _patch_local_paths(monkeypatch, tmp_path)
    _patch_missing_sources(monkeypatch, tmp_path)
    captured = {}
    original_collect_all = cli_module.collect_command.collect_all

    def _capturing_collect_all(**kwargs):
        captured.update(kwargs)
        return original_collect_all(**kwargs)

    monkeypatch.setattr(cli_module.collect_command, "collect_all", _capturing_collect_all)

    result = runner.invoke(app, ["collect", "--dry-run", "--exclude-cache-tokens"])

    assert result.exit_code == 0
    assert captured["include_cache_tokens"] is False


def test_publish_command_resolves_repo_url_from_config_and_reports_the_result(
    tmp_path, monkeypatch
) -> None:
    from tomax.commands.publish import PublishSummary
    from tomax.config import AppConfig, save_config
    from tomax.publish.git import PublishResult

    _patch_local_paths(monkeypatch, tmp_path)
    save_config(tmp_path / "config.json", AppConfig(repo_target="tmdqja75/tmdqja75"))

    captured = {}

    def _fake_publish(**kwargs):
        captured.update(kwargs)
        return PublishSummary(
            device_id="device-x",
            days_staged=2,
            result=PublishResult(pushed=True, commit_sha="abc123", attempts=1),
        )

    monkeypatch.setattr(cli_module.publish_command, "publish", _fake_publish)

    result = runner.invoke(app, ["publish"])

    assert result.exit_code == 0
    assert captured["repo_url"] == "https://github.com/tmdqja75/tmdqja75.git"
    assert "device-x" in result.stdout
    assert "abc123" in result.stdout
