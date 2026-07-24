"""Command-line interface for tomax."""

from __future__ import annotations

import sys
import tempfile
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import typer

from tomax.commands import collect as collect_command
from tomax.commands import dashboard as dashboard_command
from tomax.commands import doctor as doctor_command
from tomax.commands import init as init_command
from tomax.commands import publish as publish_command
from tomax.commands import render as render_command
from tomax.commands import schedule as schedule_command
from tomax.commands.collect import (
    DEFAULT_CLAUDE_CODE_PROJECTS_DIR,
    DEFAULT_CODEX_SESSIONS_DIR,
    DEFAULT_HERMES_STATE_DB,
)
from tomax.commands.publish import GhAuthError
from tomax.dashboard.ui_build import UIBuildError
from tomax.config import (
    config_file_path,
    data_dir,
    ledger_file_path,
    load_config,
    resolve_initial_collection_start,
    save_config,
)
from tomax.privacy import PrivacyPolicy
from tomax.publish.git import GitCommandError
from tomax.schedule.launchd import LaunchctlError

app = typer.Typer(
    help="Collect privacy-conscious local agent usage summaries.",
    no_args_is_help=True,
)
schedule_app = typer.Typer(help="Manage the opt-in local macOS daily scheduler.", no_args_is_help=True)
app.add_typer(schedule_app, name="schedule")

config_app = typer.Typer(help="Manage local tomax configuration.", no_args_is_help=True)
app.add_typer(config_app, name="config")


@app.callback()
def command_group() -> None:
    """Commands for collecting and reviewing agent usage."""


@app.command()
def init(
    repo: str = typer.Option(..., "--repo", help="GitHub profile repo in OWNER/REPO form."),
) -> None:
    """Set the target GitHub profile repository for this install (local only, no network)."""
    config = init_command.init(repo, config_path=config_file_path(), ledger_path=ledger_file_path())
    typer.echo(f"tomax: repo target set to {config.repo_target}")


@app.command()
def doctor() -> None:
    """Show local configuration and per-agent source health."""
    now = datetime.now(timezone.utc)
    report = doctor_command.run_doctor(
        config_path=config_file_path(),
        ledger_path=ledger_file_path(),
        hermes_db=DEFAULT_HERMES_STATE_DB,
        claude_projects_dir=DEFAULT_CLAUDE_CODE_PROJECTS_DIR,
        codex_sessions_dir=DEFAULT_CODEX_SESSIONS_DIR,
        now=now,
    )
    typer.echo(f"device id: {report.device_id}")
    typer.echo(f"repo target: {report.repo_target or '(not set)'}")
    typer.echo(f"display timezone: {report.display_timezone}")
    typer.echo(f"initial collection start: {report.initial_collection_start or 'default (2026-07-04)'}")
    typer.echo(f"bar chart threshold: {report.bar_chart_threshold_days} day(s)")
    for source in report.sources:
        status = source.status.value if source.status is not None else "up to date"
        typer.echo(f"  {source.agent.value}: {status}")


@config_app.command("start-date")
def config_start_date(
    date: str | None = typer.Option(
        None, "--date", help="Custom initial collection start date, in YYYY-MM-DD form."
    ),
    all_history: bool = typer.Option(
        False, "--all", help="Collect all available local history (unbounded start)."
    ),
) -> None:
    """Set how far back the first-ever collection for each agent should reach."""
    if date is not None and all_history:
        raise typer.BadParameter("pass either --date or --all, not both")
    if date is None and not all_history:
        raise typer.BadParameter("pass either --date or --all")

    value = "ALL" if all_history else date
    try:
        config = replace(load_config(config_file_path()), initial_collection_start=value)
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error

    save_config(config_file_path(), config)
    typer.echo(f"tomax: initial collection start set to {value}")


@config_app.command("bar-chart-threshold")
def config_bar_chart_threshold(
    days: int = typer.Option(
        ..., "--days", help="Day span above which the token usage chart renders as bars."
    ),
) -> None:
    """Set the day-span threshold above which the token usage chart renders as bars."""
    if days < 1:
        raise typer.BadParameter("--days must be a positive integer")

    try:
        config = replace(load_config(config_file_path()), bar_chart_threshold_days=days)
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error

    save_config(config_file_path(), config)
    typer.echo(f"tomax: bar chart threshold set to {days} day(s)")


@app.command()
def collect(
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Preview what would be collected without writing to the ledger."
    ),
) -> None:
    """Pull new local agent usage into the private ledger."""
    now = datetime.now(timezone.utc)
    config = load_config(config_file_path())
    configured_start = resolve_initial_collection_start(config.initial_collection_start)
    results = collect_command.collect_all(
        ledger_path=ledger_file_path(),
        hermes_db=DEFAULT_HERMES_STATE_DB,
        claude_projects_dir=DEFAULT_CLAUDE_CODE_PROJECTS_DIR,
        codex_sessions_dir=DEFAULT_CODEX_SESSIONS_DIR,
        now=now,
        configured_start=configured_start,
        dry_run=dry_run,
    )
    for result in results:
        status = result.status.value if result.status is not None else "up to date"
        typer.echo(
            f"  {result.agent.value}: {status} "
            f"(observed {result.records_observed}, inserted {result.records_inserted})"
        )
    if dry_run:
        typer.echo("tomax: dry run, nothing written")


@app.command()
def render(
    output_dir: Path | None = typer.Option(
        None, "--output-dir", help="Where to write the local dashboard preview."
    ),
    pie_top_n: int = typer.Option(
        6,
        "--pie-top-n",
        help="Max Skills/MCP slices to show before bucketing the rest into 'Other'.",
    ),
    rebuild: bool = typer.Option(
        False, "--rebuild", help="Force a fresh UI build even if the cached build looks current."
    ),
) -> None:
    """Render a local preview of the dashboard from this device's own collected data."""
    if pie_top_n < 1:
        raise typer.BadParameter("--pie-top-n must be at least 1")
    now = datetime.now(timezone.utc)
    config = load_config(config_file_path())
    resolved_output_dir = output_dir or (ledger_file_path().parent / "preview")
    with tempfile.TemporaryDirectory(prefix="tomax-render-") as tmp:
        try:
            result = render_command.render(
                ledger_path=ledger_file_path(),
                output_dir=resolved_output_dir,
                ui_dir=dashboard_command.UI_DIR,
                tmp_stage_dir=Path(tmp),
                privacy_policy=PrivacyPolicy.from_config(config),
                today=now.date(),
                generated_at=now.strftime("%Y-%m-%d %H:%M UTC"),
                pie_top_n=pie_top_n,
                bar_chart_threshold_days=config.bar_chart_threshold_days,
                force_build=rebuild,
            )
        except UIBuildError as error:
            typer.echo(f"tomax: {error}")
            raise typer.Exit(code=1) from error
    typer.echo(f"tomax: preview written to {result.readme_path}")
    typer.echo(
        "tomax: dashboard changed" if result.changed else "tomax: dashboard unchanged"
    )


@app.command()
def dashboard(
    all_devices: bool = typer.Option(
        False, "--all-devices", help="Aggregate multi-device data cloned from the profile repo."
    ),
    port: int = typer.Option(8000, "--port", help="Localhost port to serve on."),
    no_open: bool = typer.Option(False, "--no-open", help="Do not open a browser automatically."),
    rebuild: bool = typer.Option(
        False, "--rebuild", help="Force a fresh UI build even if the cached build looks current."
    ),
    pie_top_n: int = typer.Option(
        6, "--pie-top-n", help="Max Skills/MCP pie slices before bucketing the rest into 'Other'."
    ),
    lang: str = typer.Option(
        "en", "--lang", help="Dashboard UI language: 'en' (default) or 'ko'."
    ),
) -> None:
    """Serve an interactive localhost usage dashboard (local data, or --all-devices)."""
    if pie_top_n < 1:
        raise typer.BadParameter("--pie-top-n must be at least 1")
    if lang not in ("en", "ko"):
        raise typer.BadParameter("--lang must be 'en' or 'ko'")
    now = datetime.now(timezone.utc)
    with tempfile.TemporaryDirectory(prefix="tomax-dash-") as tmp:
        try:
            dashboard_command.run(
                ledger_path=ledger_file_path(),
                config_path=config_file_path(),
                all_devices=all_devices,
                port=port,
                open_browser=not no_open,
                pie_top_n=pie_top_n,
                lang=lang,
                ui_dir=dashboard_command.UI_DIR,
                force_build=rebuild,
                today=now.date(),
                tmp_stage_dir=Path(tmp),
            )
        except dashboard_command.DashboardError as error:
            typer.echo(f"tomax: {error}")
            raise typer.Exit(code=1) from error


@app.command()
def publish(
    branch: str = typer.Option("main", "--branch", help="Target branch on the profile repo."),
    clone_dir: Path | None = typer.Option(
        None, "--clone-dir", help="Local working copy of the profile repository to use."
    ),
) -> None:
    """Publish this device's own sanitized daily aggregates to the profile repository."""
    config = load_config(config_file_path())
    if config.repo_target is None:
        typer.echo(
            "tomax: no repo target set — run `tomax init --repo OWNER/REPO` first"
        )
        raise typer.Exit(code=1)

    now = datetime.now(timezone.utc)
    resolved_clone_dir = clone_dir or (ledger_file_path().parent / "profile-repo")
    repo_url = f"https://github.com/{config.repo_target}.git"

    try:
        summary = publish_command.publish(
            ledger_path=ledger_file_path(),
            repo_url=repo_url,
            clone_dir=resolved_clone_dir,
            branch=branch,
            privacy_policy=PrivacyPolicy.from_config(config),
            today=now.date(),
        )
    except GhAuthError as error:
        typer.echo(f"tomax: gh auth check failed: {error}")
        raise typer.Exit(code=1) from error
    except GitCommandError as error:
        typer.echo(f"tomax: publish failed: {error}")
        raise typer.Exit(code=1) from error

    typer.echo(
        f"tomax: {summary.days_staged} day(s) of local history for device "
        f"{summary.device_id}"
    )
    if summary.result.pushed:
        typer.echo(f"tomax: published (commit {summary.result.commit_sha})")
    else:
        typer.echo("tomax: nothing new to publish")


@schedule_app.command("install")
def schedule_install(
    daily_at: str = typer.Option(..., "--daily-at", help="Local 24-hour time in HH:MM form."),
) -> None:
    """Install a daily local job that runs collect, then publish."""
    try:
        result = schedule_command.install(
            config_path=config_file_path(),
            daily_at=daily_at,
            executable=str(Path(sys.argv[0]).resolve()),
            log_dir=data_dir() / "logs",
        )
    except (LaunchctlError, ValueError) as error:
        typer.echo(f"tomax: schedule install failed: {error}")
        raise typer.Exit(code=1) from error

    typer.echo(f"tomax: daily schedule installed for {result.daily_at}")


@schedule_app.command("status")
def schedule_status() -> None:
    """Show whether the local daily job is installed and loaded."""
    result = schedule_command.status()
    if not result.installed:
        typer.echo("tomax: daily schedule is not installed")
        return

    load_state = "loaded" if result.loaded else "not loaded"
    typer.echo(f"tomax: daily schedule installed for {result.daily_at} ({load_state})")


@schedule_app.command("remove")
def schedule_remove() -> None:
    """Unload and remove the local daily job."""
    try:
        removed = schedule_command.remove(config_path=config_file_path())
    except LaunchctlError as error:
        typer.echo(f"tomax: schedule removal failed: {error}")
        raise typer.Exit(code=1) from error

    if removed:
        typer.echo("tomax: daily schedule removed")
    else:
        typer.echo("tomax: daily schedule is not installed")


def main() -> None:
    """Run the command-line application."""
    app()
