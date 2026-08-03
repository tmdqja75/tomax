"""`tomax dashboard`: build the payload and serve the interactive localhost dashboard."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path

from tomax.commands import collect as collect_command
from tomax.commands.collect import (
    DEFAULT_CLAUDE_CODE_PROJECTS_DIR,
    DEFAULT_CODEX_SESSIONS_DIR,
    DEFAULT_HERMES_STATE_DB,
    AgentCollectionResult,
)
from tomax.config import load_config, resolve_initial_collection_start
from tomax.dashboard.payload import build_payload
from tomax.dashboard.remote import NoRepoTargetError
from tomax.dashboard.server import serve
from tomax.dashboard.ui_build import UIBuildError, resolve_dist_dir
from tomax.privacy import PrivacyPolicy

# The React UI source lives at the repo root under dashboard-ui/.
# commands/dashboard.py -> commands -> tomax -> src -> <repo root>.
UI_DIR = Path(__file__).resolve().parents[3] / "dashboard-ui"


class DashboardError(Exception):
    """A user-facing dashboard failure (surfaced by the CLI as a clean message)."""


def run(
    *,
    ledger_path: Path,
    config_path: Path,
    all_devices: bool,
    port: int,
    open_browser: bool,
    pie_top_n: int,
    lang: str,
    ui_dir: Path,
    force_build: bool,
    today: date,
    now: datetime,
    tmp_stage_dir: Path,
    include_cache_tokens: bool = True,
    claude_code_only_range: bool = False,
    hermes_db: Path = DEFAULT_HERMES_STATE_DB,
    claude_projects_dir: Path = DEFAULT_CLAUDE_CODE_PROJECTS_DIR,
    codex_sessions_dir: Path = DEFAULT_CODEX_SESSIONS_DIR,
    on_collected: Callable[[list[AgentCollectionResult]], None] | None = None,
) -> None:
    """Collect fresh local usage into the ledger, then build the payload, build the UI on demand, and serve until interrupted.

    Collecting first (same as `tomax collect`) means both modes always
    reflect this device's latest activity: plain `dashboard` reads the
    just-updated local ledger, and `--all-devices` merges that freshly
    collected local data with every *other* device's last-published data.
    ``on_collected``, if given, is called with the per-agent collection
    results right away so the CLI can log them before the server starts.
    """
    config = load_config(config_path)
    collect_results = collect_command.collect_all(
        ledger_path=ledger_path,
        hermes_db=hermes_db,
        claude_projects_dir=claude_projects_dir,
        codex_sessions_dir=codex_sessions_dir,
        now=now,
        configured_start=resolve_initial_collection_start(config.initial_collection_start),
        include_cache_tokens=include_cache_tokens,
    )
    if on_collected is not None:
        on_collected(collect_results)
    try:
        data = build_payload(
            ledger_path=ledger_path,
            all_devices=all_devices,
            repo_target=config.repo_target,
            privacy_policy=PrivacyPolicy.from_config(config),
            today=today,
            pie_top_n=pie_top_n,
            bar_chart_threshold_days=config.bar_chart_threshold_days,
            tmp_stage_dir=tmp_stage_dir,
            include_cache_tokens=include_cache_tokens,
            claude_code_only_range=claude_code_only_range,
        )
    except NoRepoTargetError as error:
        raise DashboardError(str(error)) from error

    try:
        dist_dir = resolve_dist_dir(ui_dir, force=force_build)
    except UIBuildError as error:
        raise DashboardError(str(error)) from error

    serve(data, dist_dir=dist_dir, port=port, open_browser=open_browser, lang=lang)
