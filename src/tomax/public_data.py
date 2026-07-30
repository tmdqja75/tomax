"""Sanitized daily public-record export.

Aggregates a device's normalized usage records for a single UTC calendar
day into the public JSON record published at
``data/v1/devices/<device-id>/<YYYY-MM-DD>.json``. Contains only schema
metadata, a checksum, safe per-agent token/session totals and status, and
sanitized skill/MCP counters — never raw events, fingerprints, real
session identity, or anything :mod:`tomax.privacy` would hide.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, timezone
from pathlib import Path

from tomax.models import NormalizedUsageRecord, SourceStatus, SupportedAgent
from tomax.privacy import PrivacyPolicy

SCHEMA_VERSION = 2

MAX_NAME_ENTRIES_PER_CATEGORY = 50
OVERFLOW_BUCKET = "(other)"

UTC = timezone.utc

# Precedence when an agent has multiple records for the same day: the most
# informative status wins, since a later successful collection run should
# supersede an earlier failed one for the same day.
_STATUS_PRECEDENCE = (
    SourceStatus.AVAILABLE_WITH_ACTIVITY,
    SourceStatus.AVAILABLE_WITH_ZERO_ACTIVITY,
)


def _record_day(record: NormalizedUsageRecord) -> date:
    return record.occurred_at.astimezone(UTC).date()


def _agent_status_for_day(records: list[NormalizedUsageRecord]) -> SourceStatus:
    statuses = {record.source_status for record in records}
    for status in _STATUS_PRECEDENCE:
        if status in statuses:
            return status
    return SourceStatus.SOURCE_UNAVAILABLE


def _cap_counter(counts: dict[str, int]) -> dict[str, int]:
    """Keep the top entries by count, rolling any remainder into a stable bucket."""
    if len(counts) <= MAX_NAME_ENTRIES_PER_CATEGORY:
        return dict(sorted(counts.items()))

    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    kept = dict(ranked[:MAX_NAME_ENTRIES_PER_CATEGORY])
    overflow_total = sum(count for _, count in ranked[MAX_NAME_ENTRIES_PER_CATEGORY:])
    if overflow_total:
        kept[OVERFLOW_BUCKET] = kept.get(OVERFLOW_BUCKET, 0) + overflow_total
    return dict(sorted(kept.items()))


def _checksum(payload_without_checksum: dict) -> str:
    canonical = json.dumps(payload_without_checksum, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def verify_checksum(payload: dict) -> bool:
    """Return whether a loaded public record's checksum matches its content."""
    if "checksum" not in payload:
        return False
    without_checksum = {key: value for key, value in payload.items() if key != "checksum"}
    return _checksum(without_checksum) == payload["checksum"]


def build_daily_record(
    *,
    device_id: str,
    day: date,
    records: list[NormalizedUsageRecord],
    privacy_policy: PrivacyPolicy | None = None,
) -> dict:
    """Aggregate one device's records for one UTC day into a public dict.

    Only records whose ``occurred_at`` falls on ``day`` (UTC calendar
    date) are considered; records for other days are ignored, so callers
    may pass a mixed-day record list safely.
    """
    policy = privacy_policy or PrivacyPolicy()
    day_records = [record for record in records if _record_day(record) == day]

    agents: dict[str, dict] = {}
    skills: dict[str, int] = {}
    mcp_servers: dict[str, int] = {}
    mcp_tools: dict[str, int] = {}

    for agent in SupportedAgent:
        agent_records = [record for record in day_records if record.agent is agent]

        input_tokens = sum(
            record.tokens.input_tokens for record in agent_records if record.tokens is not None
        )
        output_tokens = sum(
            record.tokens.output_tokens for record in agent_records if record.tokens is not None
        )
        reasoning_tokens = sum(
            record.tokens.reasoning_tokens
            for record in agent_records
            if record.tokens is not None
        )
        cache_read_tokens = sum(
            record.tokens.cache_read_tokens
            for record in agent_records
            if record.tokens is not None
        )
        cache_write_tokens = sum(
            record.tokens.cache_write_tokens
            for record in agent_records
            if record.tokens is not None
        )
        session_count = len(
            {
                record.session_fingerprint
                for record in agent_records
                if record.session_fingerprint is not None
            }
        )

        agents[agent.value] = {
            "source_status": _agent_status_for_day(agent_records).value,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "reasoning_tokens": reasoning_tokens,
            "cache_read_tokens": cache_read_tokens,
            "cache_write_tokens": cache_write_tokens,
            "headline_total": input_tokens + output_tokens + reasoning_tokens,
            "session_count": session_count,
        }

        for record in agent_records:
            if record.observed_skill_name:
                name = policy.sanitize(record.observed_skill_name)
                skills[name] = skills.get(name, 0) + 1
            if record.observed_mcp_server_name:
                server_name = policy.sanitize(record.observed_mcp_server_name)
                mcp_servers[server_name] = mcp_servers.get(server_name, 0) + 1
                if record.observed_mcp_tool_name:
                    tool_name = policy.sanitize(record.observed_mcp_tool_name)
                    key = f"{server_name}/{tool_name}"
                    mcp_tools[key] = mcp_tools.get(key, 0) + 1

    payload = {
        "schema_version": SCHEMA_VERSION,
        "device_id": device_id,
        "date": day.isoformat(),
        "agents": agents,
        "skills": _cap_counter(skills),
        "mcp_servers": _cap_counter(mcp_servers),
        "mcp_tools": _cap_counter(mcp_tools),
    }
    payload["checksum"] = _checksum(payload)
    return payload


def write_daily_record(path: Path, payload: dict) -> bool:
    """Write a daily record to disk. Returns True only if content changed."""
    serialized = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if path.exists() and path.read_text(encoding="utf-8") == serialized:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(serialized, encoding="utf-8")
    return True


def stage_daily_records(
    device_dir: Path,
    *,
    device_id: str,
    records: list[NormalizedUsageRecord],
    privacy_policy: PrivacyPolicy | None = None,
) -> list[dict]:
    """Build and write one device's public daily records, one file per day with data.

    Shared by the local dashboard preview and the publish command, which
    both need to turn a device's full ledger history into the same
    ``<device_dir>/<YYYY-MM-DD>.json`` layout the profile repository
    expects. Returns the written payloads in day order, so callers can
    validate or aggregate them without re-reading from disk.
    """
    days = sorted({_record_day(record) for record in records})
    payloads = []
    for day in days:
        payload = build_daily_record(
            device_id=device_id, day=day, records=records, privacy_policy=privacy_policy
        )
        write_daily_record(device_dir / f"{day.isoformat()}.json", payload)
        payloads.append(payload)
    return payloads
