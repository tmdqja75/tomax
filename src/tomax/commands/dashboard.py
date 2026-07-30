"""`tomax dashboard`: build the payload and serve the interactive localhost dashboard."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from tomax.config import load_config
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
    tmp_stage_dir: Path,
    include_cache_tokens: bool = True,
) -> None:
    """Build the payload, build the UI on demand, and serve until interrupted."""
    config = load_config(config_path)
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
        )
    except NoRepoTargetError as error:
        raise DashboardError(str(error)) from error

    try:
        dist_dir = resolve_dist_dir(ui_dir, force=force_build)
    except UIBuildError as error:
        raise DashboardError(str(error)) from error

    serve(data, dist_dir=dist_dir, port=port, open_browser=open_browser, lang=lang)
