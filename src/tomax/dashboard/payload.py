"""Assemble the dashboard data.json from either local ledger data or multi-device data."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from tomax.aggregate import validate_and_partition
from tomax.dashboard.remote import fetch_device_entries
from tomax.ledger.repository import LedgerRepository
from tomax.privacy import PrivacyPolicy
from tomax.public_data import stage_daily_records
from tomax.render.dashboard_data import build_dashboard_data


def _local_entries(
    *, ledger_path: Path, privacy_policy: PrivacyPolicy, tmp_stage_dir: Path
) -> list[tuple[str, dict]]:
    repository = LedgerRepository.open(ledger_path)
    try:
        device_id = repository.get_or_create_device_id()
        records = repository.list_records()
    finally:
        repository.close()
    device_data_dir = tmp_stage_dir / "data" / "v1" / "devices" / device_id
    payloads = stage_daily_records(
        device_data_dir, device_id=device_id, records=records, privacy_policy=privacy_policy
    )
    return [(device_id, payload) for payload in payloads]


def build_payload(
    *,
    ledger_path: Path,
    all_devices: bool,
    repo_target: str | None,
    privacy_policy: PrivacyPolicy,
    today: date,
    pie_top_n: int,
    bar_chart_threshold_days: int = 15,
    tmp_stage_dir: Path,
    include_cache_tokens: bool = True,
) -> dict:
    """Produce the dashboard data.json dict from the chosen data source."""
    if all_devices:
        entries = fetch_device_entries(repo_target)
    else:
        entries = _local_entries(
            ledger_path=ledger_path,
            privacy_policy=privacy_policy,
            tmp_stage_dir=tmp_stage_dir,
        )
    valid_payloads = validate_and_partition(entries, today=today).valid_payloads
    return build_dashboard_data(
        valid_payloads,
        today=today,
        pie_top_n=pie_top_n,
        bar_chart_threshold_days=bar_chart_threshold_days,
        include_cache_tokens=include_cache_tokens,
    )
