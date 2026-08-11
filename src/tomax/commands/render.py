"""Render a local preview of the profile README scorecard from local ledger data.

Writes a compact SVG scorecard and managed README section entirely locally —
never touching Git or the network. Cross-device aggregation happens only once
records are published and picked up by the profile repository's GitHub Action.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from tomax.aggregate import validate_and_partition
from tomax.ledger.repository import LedgerRepository
from tomax.privacy import PrivacyPolicy
from tomax.public_data import stage_daily_records
from tomax.render.markdown import (
    DASHBOARD_IMAGE_PATH,
    render_dashboard_markdown,
    update_readme,
)
from tomax.render.scorecard import render_scorecard_svg


def _write_if_changed(path: Path, content: str | bytes) -> bool:
    if path.exists() and (
        path.read_bytes() if isinstance(content, bytes) else path.read_text(encoding="utf-8")
    ) == content:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")
    return True


@dataclass(frozen=True, slots=True)
class RenderResult:
    device_id: str
    readme_path: Path
    changed: bool


def render(
    *,
    ledger_path: Path,
    output_dir: Path,
    ui_dir: Path,
    tmp_stage_dir: Path,
    privacy_policy: PrivacyPolicy = PrivacyPolicy(),
    today: date,
    generated_at: str,
    pie_top_n: int = 6,
    bar_chart_threshold_days: int = 15,
    force_build: bool = False,
    include_cache_tokens: bool = True,
    on_progress: Callable[[str], None] | None = None,
) -> RenderResult:
    """Regenerate this device's local dashboard preview. Returns whether anything changed.

    ``on_progress``, if given, is called before each potentially slow ledger
    read or staged-record write.
    """
    progress = on_progress or (lambda _message: None)

    progress(f"reading local ledger at {ledger_path}")
    repository = LedgerRepository.open(ledger_path)
    try:
        device_id = repository.get_or_create_device_id()
        records = repository.list_records()
    finally:
        repository.close()

    progress(f"staging sanitized daily aggregates for device {device_id}")
    device_data_dir = output_dir / "data" / "v1" / "devices" / device_id
    payloads = stage_daily_records(
        device_data_dir, device_id=device_id, records=records, privacy_policy=privacy_policy
    )

    valid_payloads = validate_and_partition(
        [(device_id, payload) for payload in payloads], today=today
    ).valid_payloads
    scorecard_path = output_dir / DASHBOARD_IMAGE_PATH
    svg = render_scorecard_svg(
        valid_payloads, today=today, include_cache_tokens=include_cache_tokens
    )
    changed = _write_if_changed(scorecard_path, svg)

    progress("writing README")
    readme_path = output_dir / "README.md"
    existing_readme = readme_path.read_text(encoding="utf-8") if readme_path.exists() else ""
    updated_readme = update_readme(existing_readme, render_dashboard_markdown())
    changed = _write_if_changed(readme_path, updated_readme) or changed

    return RenderResult(device_id=device_id, readme_path=readme_path, changed=changed)
