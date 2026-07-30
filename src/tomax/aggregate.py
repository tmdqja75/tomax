"""Multi-device aggregation and validation of public daily records.

Validates public daily records structurally — schema version, checksum,
date, device identity, non-negative totals, and payload size — before
merging them across every device contributing to one profile repository.
These records may originate from any device pushing to a shared public
repo, so validation is defensive against malformed or hostile input, not
just a re-check of our own trusted output. Rejected records are reported
as diagnostics and excluded from aggregation; nothing is guessed at or
repaired.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, timedelta

from tomax.models import SourceStatus, SupportedAgent
from tomax.public_data import MAX_NAME_ENTRIES_PER_CATEGORY, verify_checksum

SCHEMA_VERSION = 2

MAX_PAYLOAD_BYTES = 100_000
MAX_COUNTER_ENTRIES = MAX_NAME_ENTRIES_PER_CATEGORY + 1  # + the overflow bucket

_AGENT_TOTAL_FIELDS = (
    "input_tokens",
    "output_tokens",
    "reasoning_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "headline_total",
    "session_count",
)

# Codex reports cache_read_tokens as a subset of input_tokens (not additive --
# see adapters/codex.py), unlike Claude Code and Hermes, which report it
# separately from input_tokens. So a cache-inclusive total must skip
# cache_read_tokens for Codex specifically, to avoid double-counting those
# tokens. cache_write_tokens has no such exception (Codex's is always 0).
_AGENTS_WITH_ADDITIVE_CACHE_READ = frozenset(
    {SupportedAgent.CLAUDE_CODE.value, SupportedAgent.HERMES_AGENT.value}
)


def agent_effective_total(
    agent_name: str, agent_data: dict, *, include_cache_tokens: bool = True
) -> int:
    """Return the agent's displayed total: headline_total, plus cache tokens by default.

    ``include_cache_tokens=False`` reproduces the pre-cache-tracking
    behavior (``headline_total`` alone), for callers/flags that want to
    exclude prompt-cache tokens from the displayed total.
    """
    total = agent_data["headline_total"]
    if not include_cache_tokens:
        return total
    if agent_name in _AGENTS_WITH_ADDITIVE_CACHE_READ:
        total += agent_data["cache_read_tokens"]
    total += agent_data["cache_write_tokens"]
    return total
_COUNTER_CATEGORIES = ("skills", "mcp_servers", "mcp_tools")

# Precedence when devices disagree on an agent's status for the same day:
# the most informative status wins.
_STATUS_PRECEDENCE = (
    SourceStatus.AVAILABLE_WITH_ACTIVITY.value,
    SourceStatus.AVAILABLE_WITH_ZERO_ACTIVITY.value,
    SourceStatus.SOURCE_UNAVAILABLE.value,
)


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    """A reason a public record was rejected, for diagnostics."""

    device_id: str
    date: str | None
    reason: str


@dataclass(frozen=True, slots=True)
class PartitionResult:
    """Payloads that passed validation, and diagnostics for ones that didn't."""

    valid_payloads: list[dict]
    issues: list[ValidationIssue]


def _parse_date(value: object) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _is_non_negative_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _payload_byte_size(payload: dict) -> int:
    return len(json.dumps(payload, sort_keys=True).encode("utf-8"))


def validate_record(device_id: str, payload: object, *, today: date) -> ValidationIssue | None:
    """Return a ValidationIssue if the record is invalid, else None.

    ``today`` must be supplied by the caller (never computed internally)
    so validation stays deterministic and testable.
    """
    if not isinstance(payload, dict):
        return ValidationIssue(device_id, None, "payload is not a JSON object")

    date_str = payload.get("date")

    if payload.get("schema_version") != SCHEMA_VERSION:
        return ValidationIssue(device_id, date_str, "unsupported schema_version")

    if _payload_byte_size(payload) > MAX_PAYLOAD_BYTES:
        return ValidationIssue(device_id, date_str, "payload exceeds the allowed size")

    if not verify_checksum(payload):
        return ValidationIssue(device_id, date_str, "checksum does not match record content")

    parsed_date = _parse_date(date_str)
    if parsed_date is None:
        return ValidationIssue(device_id, date_str, "date is missing or not a valid ISO date")
    if parsed_date > today:
        return ValidationIssue(device_id, date_str, "future-dated record")

    if payload.get("device_id") != device_id:
        return ValidationIssue(device_id, date_str, "device_id does not match its partition")

    agents = payload.get("agents")
    if not isinstance(agents, dict):
        return ValidationIssue(device_id, date_str, "missing agents section")
    known_agents = {agent.value for agent in SupportedAgent}
    known_statuses = {status.value for status in SourceStatus}
    for agent_name, agent_data in agents.items():
        if agent_name not in known_agents:
            return ValidationIssue(device_id, date_str, f"unknown agent {agent_name!r}")
        if not isinstance(agent_data, dict):
            return ValidationIssue(device_id, date_str, f"agent {agent_name} is not an object")
        for field_name in _AGENT_TOTAL_FIELDS:
            if not _is_non_negative_int(agent_data.get(field_name)):
                return ValidationIssue(
                    device_id,
                    date_str,
                    f"agent {agent_name} field {field_name} must be a non-negative integer",
                )
        if agent_data.get("source_status") not in known_statuses:
            return ValidationIssue(
                device_id, date_str, f"agent {agent_name} has an invalid source_status"
            )

    for category in _COUNTER_CATEGORIES:
        counters = payload.get(category)
        if not isinstance(counters, dict):
            return ValidationIssue(device_id, date_str, f"missing {category} section")
        if len(counters) > MAX_COUNTER_ENTRIES:
            return ValidationIssue(device_id, date_str, f"{category} exceeds the allowed size")
        for name, count in counters.items():
            if not isinstance(name, str) or not _is_non_negative_int(count):
                return ValidationIssue(
                    device_id, date_str, f"{category} counter {name!r} must be non-negative"
                )

    return None


def validate_and_partition(
    entries: list[tuple[str, object]], *, today: date
) -> PartitionResult:
    """Validate every (device_id, payload) entry, separating valid from invalid."""
    valid_payloads = []
    issues = []
    for device_id, payload in entries:
        issue = validate_record(device_id, payload, today=today)
        if issue is not None:
            issues.append(issue)
        else:
            valid_payloads.append(payload)
    return PartitionResult(valid_payloads=valid_payloads, issues=issues)


def select_date_range(payloads: list[dict], *, start: date, end: date) -> list[dict]:
    """Select payloads whose date falls in [start, end] inclusive."""
    selected = []
    for payload in payloads:
        parsed = _parse_date(payload.get("date"))
        if parsed is not None and start <= parsed <= end:
            selected.append(payload)
    return selected


def rolling_window(payloads: list[dict], *, end: date, days: int = 14) -> list[dict]:
    """Select payloads within the trailing ``days``-day window ending at ``end``."""
    start = end - timedelta(days=days - 1)
    return select_date_range(payloads, start=start, end=end)


def _best_status(current: str | None, candidate: str) -> str:
    if current is None:
        return candidate
    return min((current, candidate), key=_STATUS_PRECEDENCE.index)


def aggregate_records(payloads: list[dict]) -> dict:
    """Sum already-validated daily payloads across every device and day.

    Callers select which payloads to include (e.g. a rolling 14-day window
    or the full lifetime history) before calling this — it has no
    date-range logic of its own.
    """
    agent_totals: dict[str, dict] = {
        agent.value: dict.fromkeys(_AGENT_TOTAL_FIELDS, 0) for agent in SupportedAgent
    }
    status_by_agent_date: dict[str, dict[str, str]] = {
        agent.value: {} for agent in SupportedAgent
    }
    skills: dict[str, int] = {}
    mcp_servers: dict[str, int] = {}
    mcp_tools: dict[str, int] = {}
    devices: set[str] = set()

    for payload in payloads:
        devices.add(payload["device_id"])
        date_str = payload["date"]

        for agent_name, agent_data in payload.get("agents", {}).items():
            if agent_name not in agent_totals:
                continue
            totals = agent_totals[agent_name]
            for field_name in _AGENT_TOTAL_FIELDS:
                totals[field_name] += agent_data[field_name]

            status_by_date = status_by_agent_date[agent_name]
            status_by_date[date_str] = _best_status(
                status_by_date.get(date_str), agent_data["source_status"]
            )

        for name, count in payload.get("skills", {}).items():
            skills[name] = skills.get(name, 0) + count
        for name, count in payload.get("mcp_servers", {}).items():
            mcp_servers[name] = mcp_servers.get(name, 0) + count
        for name, count in payload.get("mcp_tools", {}).items():
            mcp_tools[name] = mcp_tools.get(name, 0) + count

    overall_active_dates: set[str] = set()
    for agent_name, status_by_date in status_by_agent_date.items():
        active_dates = {
            date_str
            for date_str, status in status_by_date.items()
            if status == SourceStatus.AVAILABLE_WITH_ACTIVITY.value
        }
        agent_totals[agent_name]["active_days"] = len(active_dates)
        agent_totals[agent_name]["source_status"] = (
            _overall_status(status_by_date.values())
            if status_by_date
            else SourceStatus.SOURCE_UNAVAILABLE.value
        )
        overall_active_dates |= active_dates

    return {
        "agents": agent_totals,
        "skills": dict(sorted(skills.items())),
        "mcp_servers": dict(sorted(mcp_servers.items())),
        "mcp_tools": dict(sorted(mcp_tools.items())),
        "distinct_devices": len(devices),
        "active_days": len(overall_active_dates),
    }


def _overall_status(statuses: object) -> str:
    best = None
    for status in statuses:
        best = _best_status(best, status)
    return best if best is not None else SourceStatus.SOURCE_UNAVAILABLE.value


def daily_totals(payloads: list[dict], *, include_cache_tokens: bool = True) -> dict[str, int]:
    """Sum each agent's effective total across all agents/devices for each day with data.

    Only dates present in at least one payload appear in the result.
    Callers must treat an absent date as "no data," never as zero — the
    project's core distinction between source_unavailable and confirmed
    zero activity applies to chart data too. Cache tokens are included by
    default (see :func:`agent_effective_total`); pass
    ``include_cache_tokens=False`` to total ``headline_total`` alone.
    """
    totals: dict[str, int] = {}
    for payload in payloads:
        date_str = payload["date"]
        day_total = sum(
            agent_effective_total(agent_name, agent_data, include_cache_tokens=include_cache_tokens)
            for agent_name, agent_data in payload.get("agents", {}).items()
        )
        totals[date_str] = totals.get(date_str, 0) + day_total
    return totals


def daily_agent_totals(
    payloads: list[dict], *, include_cache_tokens: bool = True
) -> dict[str, dict[str, int]]:
    """Sum each agent's effective total per agent for each day with data.

    Agents with zero total for a day are omitted from that day's dict.
    Same no-data-means-absent contract as :func:`daily_totals` for dates,
    and the same cache-token default as :func:`agent_effective_total`.
    """
    totals: dict[str, dict[str, int]] = {}
    for payload in payloads:
        date_str = payload["date"]
        day_totals = totals.setdefault(date_str, {})
        for agent_name, agent_data in payload.get("agents", {}).items():
            total = agent_effective_total(
                agent_name, agent_data, include_cache_tokens=include_cache_tokens
            )
            if total == 0:
                continue
            day_totals[agent_name] = day_totals.get(agent_name, 0) + total
    return totals


def daily_token_totals(
    payloads: list[dict], *, include_cache_tokens: bool = True
) -> dict[str, dict[str, int] | None]:
    """Sum daily input/output/reasoning tokens from observable agent sources.

    A day with public records but no available source is returned as ``None``
    rather than as a fabricated zero.  On days where at least one source was
    available, unavailable sources contribute nothing while the available
    sources' token fields are aggregated across devices.

    By default (``include_cache_tokens=True``), cache-read and cache-write
    tokens are folded into the ``"input"`` bucket, since prompt caching is
    an input-side cost — except Codex's cache-read count, which is already
    inside its ``input_tokens`` (see :func:`agent_effective_total`).
    """
    totals: dict[str, dict[str, int]] = {}
    has_available_source: dict[str, bool] = {}
    for payload in payloads:
        date_str = payload["date"]
        day_totals = totals.setdefault(
            date_str, {"input": 0, "output": 0, "reasoning": 0}
        )
        has_available_source.setdefault(date_str, False)
        for agent_name, agent_data in payload.get("agents", {}).items():
            if agent_data["source_status"] == SourceStatus.SOURCE_UNAVAILABLE.value:
                continue
            has_available_source[date_str] = True
            cache_input = 0
            if include_cache_tokens:
                if agent_name in _AGENTS_WITH_ADDITIVE_CACHE_READ:
                    cache_input += agent_data["cache_read_tokens"]
                cache_input += agent_data["cache_write_tokens"]
            day_totals["input"] += agent_data["input_tokens"] + cache_input
            day_totals["output"] += agent_data["output_tokens"]
            day_totals["reasoning"] += agent_data["reasoning_tokens"]

    return {
        date_str: day_totals if has_available_source[date_str] else None
        for date_str, day_totals in totals.items()
    }


def monthly_totals(payloads: list[dict], *, include_cache_tokens: bool = True) -> dict[str, int]:
    """Sum each agent's effective total across all agents/devices for each YYYY-MM month with data.

    Same no-data-means-absent contract as :func:`daily_totals`, and the same
    cache-token default as :func:`agent_effective_total`.
    """
    totals: dict[str, int] = {}
    for payload in payloads:
        month_key = payload["date"][:7]
        day_total = sum(
            agent_effective_total(agent_name, agent_data, include_cache_tokens=include_cache_tokens)
            for agent_name, agent_data in payload.get("agents", {}).items()
        )
        totals[month_key] = totals.get(month_key, 0) + day_total
    return totals
