"""Read-only adapter for Claude Code's local project transcripts.

Reads ``<projects_dir>/*/*.jsonl`` read-only and normalizes only safe
metadata. If ``projects_dir`` itself doesn't exist, the source is reported
unavailable rather than zero: per-turn token usage genuinely cannot be
recovered from a lesser source (e.g. a bare command-history log), so we
never estimate it from one.

Claude Code's Anthropic-backed usage payload has no separate
reasoning-token field — extended-thinking tokens are counted inside
``output_tokens`` — so ``reasoning_tokens`` is always 0 here, unlike Codex,
which reports them separately.

Skill and MCP tool calls are recognized from ``tool_use`` content blocks:
``name == "Skill"`` carries the skill name in ``input.skill``; MCP tools
use the ``mcp__<server>__<tool>`` convention shared with Hermes and Codex.
Native/general tool calls aren't tracked, matching the Hermes adapter,
since NormalizedUsageRecord has no generic tool-name field for them.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

from tomax.adapters.base import make_fingerprint, split_mcp_tool_name
from tomax.models import NormalizedUsageRecord, SourceStatus, SupportedAgent, TokenUsage
from tomax.time_window import TimeWindow


def _session_fingerprint(session_id: str) -> str | None:
    return make_fingerprint("claude_code", "session", session_id) if session_id else None


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _unavailable_record(window: TimeWindow) -> NormalizedUsageRecord:
    return NormalizedUsageRecord(
        agent=SupportedAgent.CLAUDE_CODE,
        occurred_at=window.end_utc,
        fingerprint=make_fingerprint(
            "claude_code",
            "source_unavailable",
            window.start_utc.isoformat(),
            window.end_utc.isoformat(),
        ),
        tokens=None,
        source_status=SourceStatus.SOURCE_UNAVAILABLE,
    )


def _zero_activity_record(window: TimeWindow) -> NormalizedUsageRecord:
    return NormalizedUsageRecord(
        agent=SupportedAgent.CLAUDE_CODE,
        occurred_at=window.end_utc,
        fingerprint=make_fingerprint(
            "claude_code",
            "zero_activity",
            window.start_utc.isoformat(),
            window.end_utc.isoformat(),
        ),
        tokens=TokenUsage(),
        source_status=SourceStatus.AVAILABLE_WITH_ZERO_ACTIVITY,
    )


def _iter_transcript_files(projects_dir: Path) -> Iterator[Path]:
    yield from sorted(projects_dir.glob("*/*.jsonl"))


def _iter_events(transcript_path: Path) -> Iterator[dict]:
    try:
        handle = transcript_path.open("r", encoding="utf-8", errors="replace")
    except OSError:
        return
    try:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                yield event
    finally:
        handle.close()


def _token_record_from_assistant_event(
    event: dict, window: TimeWindow
) -> NormalizedUsageRecord | None:
    occurred_at = _parse_timestamp(event.get("timestamp"))
    if occurred_at is None or not window.contains(occurred_at):
        return None

    usage = event.get("message", {}).get("usage")
    if not isinstance(usage, dict):
        return None

    input_tokens = usage.get("input_tokens")
    output_tokens = usage.get("output_tokens")
    if not isinstance(input_tokens, int) or not isinstance(output_tokens, int):
        return None
    if isinstance(input_tokens, bool) or isinstance(output_tokens, bool):
        return None

    cache_write_tokens = usage.get("cache_creation_input_tokens", 0)
    cache_read_tokens = usage.get("cache_read_input_tokens", 0)
    if not isinstance(cache_write_tokens, int) or isinstance(cache_write_tokens, bool):
        cache_write_tokens = 0
    if not isinstance(cache_read_tokens, int) or isinstance(cache_read_tokens, bool):
        cache_read_tokens = 0

    tokens = TokenUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_write_tokens=cache_write_tokens,
        cache_read_tokens=cache_read_tokens,
    )
    status = (
        SourceStatus.AVAILABLE_WITH_ACTIVITY
        if tokens.headline_total > 0
        else SourceStatus.AVAILABLE_WITH_ZERO_ACTIVITY
    )
    session_id = event.get("sessionId") or event.get("session_id") or ""
    uuid = event.get("uuid") or ""
    return NormalizedUsageRecord(
        agent=SupportedAgent.CLAUDE_CODE,
        occurred_at=occurred_at,
        fingerprint=make_fingerprint("claude_code", "assistant_usage", session_id, uuid),
        session_fingerprint=_session_fingerprint(session_id),
        tokens=tokens,
        source_status=status,
    )


def _tool_observation_records_from_assistant_event(
    event: dict, window: TimeWindow
) -> list[NormalizedUsageRecord]:
    occurred_at = _parse_timestamp(event.get("timestamp"))
    if occurred_at is None or not window.contains(occurred_at):
        return []

    content = event.get("message", {}).get("content")
    if not isinstance(content, list):
        return []

    session_id = event.get("sessionId") or event.get("session_id") or ""
    records = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            continue
        tool_name = block.get("name")
        tool_use_id = block.get("id")
        if not tool_name or not tool_use_id:
            continue

        skill_name: str | None = None
        mcp_server: str | None = None
        mcp_tool: str | None = None

        if tool_name == "Skill":
            skill_name = (block.get("input") or {}).get("skill")
            if not isinstance(skill_name, str) or not skill_name:
                continue
        else:
            split = split_mcp_tool_name(tool_name)
            if split is None:
                continue
            mcp_server, mcp_tool = split

        records.append(
            NormalizedUsageRecord(
                agent=SupportedAgent.CLAUDE_CODE,
                occurred_at=occurred_at,
                fingerprint=make_fingerprint(
                    "claude_code", "tool_call", session_id, tool_use_id
                ),
                session_fingerprint=_session_fingerprint(session_id),
                tokens=TokenUsage(),
                observed_skill_name=skill_name,
                observed_mcp_server_name=mcp_server,
                observed_mcp_tool_name=mcp_tool,
                source_status=SourceStatus.AVAILABLE_WITH_ZERO_ACTIVITY,
            )
        )
    return records


def collect(projects_dir: Path, window: TimeWindow) -> list[NormalizedUsageRecord]:
    """Collect normalized Claude Code usage records for the given window.

    Returns a single ``source_unavailable`` record if ``projects_dir``
    doesn't exist, a single ``available_with_zero_activity`` record if
    nothing falls in the window, or one record per observed token usage
    turn / skill / MCP tool call.
    """
    if not projects_dir.exists() or not projects_dir.is_dir():
        return [_unavailable_record(window)]

    records: list[NormalizedUsageRecord] = []
    for transcript_path in _iter_transcript_files(projects_dir):
        for event in _iter_events(transcript_path):
            if event.get("type") != "assistant":
                continue
            token_record = _token_record_from_assistant_event(event, window)
            if token_record is not None:
                records.append(token_record)
            records.extend(_tool_observation_records_from_assistant_event(event, window))

    if not records:
        return [_zero_activity_record(window)]
    return records
