"""Assemble the dashboard data.json from either local ledger data or multi-device data."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from tomax.aggregate import agent_available_date_range, select_date_range, validate_and_partition
from tomax.dashboard.remote import fetch_device_entries
from tomax.ledger.repository import LedgerRepository
from tomax.models import SupportedAgent
from tomax.privacy import PrivacyPolicy
from tomax.public_data import stage_daily_records
from tomax.render.dashboard_data import build_dashboard_data


def _local_entries(
    *, ledger_path: Path, privacy_policy: PrivacyPolicy, tmp_stage_dir: Path
) -> tuple[str, list[tuple[str, dict]]]:
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
    return device_id, [(device_id, payload) for payload in payloads]


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
    claude_code_only_range: bool = False,
) -> dict:
    """Produce the dashboard data.json dict from the chosen data source.

    ``--all-devices`` combines this device's own local ledger (freshest —
    the caller collects into it before calling this) with every *other*
    device's last-published data cloned from the profile repo; this
    device's own remote directory is excluded so its stale published copy
    never shadows the local data.

    ``claude_code_only_range`` trims the payloads to the first-to-last date
    Claude Code was available anywhere in the data, so every chart draws
    only over the span Claude Code actually has data for — days before its
    first observed record or after its last are dropped entirely, from
    every agent, not just Claude Code.
    """
    device_id, local_entries = _local_entries(
        ledger_path=ledger_path,
        privacy_policy=privacy_policy,
        tmp_stage_dir=tmp_stage_dir,
    )
    if all_devices:
        entries = local_entries + fetch_device_entries(repo_target, exclude_device_id=device_id)
    else:
        entries = local_entries
    valid_payloads = validate_and_partition(entries, today=today).valid_payloads
    if claude_code_only_range:
        available = agent_available_date_range(valid_payloads, SupportedAgent.CLAUDE_CODE.value)
        if available is None:
            valid_payloads = []
        else:
            start, end = available
            valid_payloads = select_date_range(valid_payloads, start=start, end=end)
    return build_dashboard_data(
        valid_payloads,
        today=today,
        pie_top_n=pie_top_n,
        bar_chart_threshold_days=bar_chart_threshold_days,
        include_cache_tokens=include_cache_tokens,
    )
