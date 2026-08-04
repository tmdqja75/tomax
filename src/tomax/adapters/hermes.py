"""Read-only adapter for Hermes Agent's local ``state.db``.

Reads ``sessions``, ``messages``, and ``session_model_usage`` read-only and
normalizes only safe metadata. Never estimates token counts and never
retains prompts, transcripts, raw session IDs, or tool arguments.

Token totals come from ``session_model_usage`` rather than the coarser
``sessions`` aggregate columns: every session with any tokens has full
``session_model_usage`` coverage, and it carries real per-row
``last_seen`` timestamps plus per-model granularity that the session-level
columns don't. ``cache_read_tokens``/``cache_write_tokens`` are read
straight from their own columns on that table.

MCP tool calls are recognized by Hermes's ``mcp__<server>__<tool>`` wire
naming convention (see ``mcp_prefixed_tool_name`` in Hermes's own
``tools/mcp_tool.py``, introduced 2026-06-25). Native/general tool calls
(e.g. shell, file edit) aren't tracked here since
:class:`~tomax.models.NormalizedUsageRecord` has no generic tool-name
field for them.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from tomax.adapters.base import make_fingerprint, split_mcp_tool_name
from tomax.models import NormalizedUsageRecord, SourceStatus, SupportedAgent, TokenUsage
from tomax.time_window import TimeWindow

UTC = timezone.utc


def _epoch_to_utc(value: float) -> datetime:
    return datetime.fromtimestamp(value, tz=UTC)


def _session_fingerprint(session_id: str) -> str:
    return make_fingerprint("hermes", "session", session_id)


def _unavailable_record(window: TimeWindow) -> NormalizedUsageRecord:
    return NormalizedUsageRecord(
        agent=SupportedAgent.HERMES_AGENT,
        occurred_at=window.end_utc,
        fingerprint=make_fingerprint(
            "hermes",
            "source_unavailable",
            window.start_utc.isoformat(),
            window.end_utc.isoformat(),
        ),
        tokens=None,
        source_status=SourceStatus.SOURCE_UNAVAILABLE,
    )


def _zero_activity_record(window: TimeWindow) -> NormalizedUsageRecord:
    return NormalizedUsageRecord(
        agent=SupportedAgent.HERMES_AGENT,
        occurred_at=window.end_utc,
        fingerprint=make_fingerprint(
            "hermes",
            "zero_activity",
            window.start_utc.isoformat(),
            window.end_utc.isoformat(),
        ),
        tokens=TokenUsage(),
        source_status=SourceStatus.AVAILABLE_WITH_ZERO_ACTIVITY,
    )


def _collect_token_records(
    connection: sqlite3.Connection, window: TimeWindow
) -> list[NormalizedUsageRecord]:
    rows = connection.execute(
        """
        SELECT session_id, model, billing_provider, billing_base_url,
               billing_mode, task, input_tokens, output_tokens,
               reasoning_tokens, cache_read_tokens, cache_write_tokens, last_seen
        FROM session_model_usage
        WHERE last_seen >= ? AND last_seen < ?
        """,
        (window.start_utc.timestamp(), window.end_utc.timestamp()),
    ).fetchall()

    records = []
    for row in rows:
        tokens = TokenUsage(
            input_tokens=row["input_tokens"],
            output_tokens=row["output_tokens"],
            reasoning_tokens=row["reasoning_tokens"],
            cache_read_tokens=row["cache_read_tokens"],
            cache_write_tokens=row["cache_write_tokens"],
        )
        status = (
            SourceStatus.AVAILABLE_WITH_ACTIVITY
            if tokens.headline_total > 0
            else SourceStatus.AVAILABLE_WITH_ZERO_ACTIVITY
        )
        records.append(
            NormalizedUsageRecord(
                agent=SupportedAgent.HERMES_AGENT,
                occurred_at=_epoch_to_utc(row["last_seen"]),
                fingerprint=make_fingerprint(
                    "hermes",
                    "session_model_usage",
                    row["session_id"],
                    row["model"],
                    row["billing_provider"],
                    row["billing_base_url"],
                    row["billing_mode"],
                    row["task"],
                ),
                session_fingerprint=_session_fingerprint(row["session_id"]),
                model=row["model"],
                tokens=tokens,
                source_status=status,
            )
        )
    return records


def _skill_names_by_call_id(rows: list[sqlite3.Row]) -> dict[tuple[str, str], str]:
    skill_names: dict[tuple[str, str], str] = {}
    for row in rows:
        if row["role"] != "assistant" or not row["tool_calls"]:
            continue
        try:
            calls = json.loads(row["tool_calls"])
        except (json.JSONDecodeError, TypeError):
            continue
        for call in calls:
            function = call.get("function") or {}
            if function.get("name") != "skill_view":
                continue
            call_id = call.get("id") or call.get("call_id")
            if not call_id:
                continue
            try:
                arguments = json.loads(function.get("arguments") or "{}")
            except (json.JSONDecodeError, TypeError):
                continue
            name = arguments.get("name")
            if isinstance(name, str) and name:
                skill_names[(row["session_id"], call_id)] = name
    return skill_names


def _collect_tool_observation_records(
    connection: sqlite3.Connection, window: TimeWindow
) -> list[NormalizedUsageRecord]:
    # Skill-name attribution is resolved from the assistant message that
    # issued the call, which can carry a different timestamp than the tool
    # response message being recorded. Looking this up across all sessions,
    # rather than only ones with a message inside the window, avoids
    # dropping a call's name just because the two messages straddle the
    # window boundary.
    assistant_rows = connection.execute(
        """
        SELECT session_id, role, tool_calls
        FROM messages
        WHERE role = 'assistant' AND tool_calls IS NOT NULL
        """
    ).fetchall()
    skill_names = _skill_names_by_call_id(assistant_rows)

    rows = connection.execute(
        """
        SELECT id, session_id, role, tool_name, tool_call_id, timestamp
        FROM messages
        WHERE role = 'tool' AND tool_name IS NOT NULL
          AND timestamp >= ? AND timestamp < ?
        ORDER BY session_id, id
        """,
        (window.start_utc.timestamp(), window.end_utc.timestamp()),
    ).fetchall()

    records = []
    for row in rows:
        tool_name = row["tool_name"]

        skill_name: str | None = None
        mcp_server: str | None = None
        mcp_tool: str | None = None

        if tool_name == "skill_view":
            skill_name = skill_names.get((row["session_id"], row["tool_call_id"]))
            if skill_name is None:
                continue
        else:
            split = split_mcp_tool_name(tool_name)
            if split is None:
                continue
            mcp_server, mcp_tool = split

        records.append(
            NormalizedUsageRecord(
                agent=SupportedAgent.HERMES_AGENT,
                occurred_at=_epoch_to_utc(row["timestamp"]),
                fingerprint=make_fingerprint(
                    "hermes", "tool_call", row["session_id"], str(row["id"])
                ),
                session_fingerprint=_session_fingerprint(row["session_id"]),
                tokens=TokenUsage(),
                observed_skill_name=skill_name,
                observed_mcp_server_name=mcp_server,
                observed_mcp_tool_name=mcp_tool,
                source_status=SourceStatus.AVAILABLE_WITH_ZERO_ACTIVITY,
            )
        )
    return records


def collect(db_path: Path, window: TimeWindow) -> list[NormalizedUsageRecord]:
    """Collect normalized Hermes usage records for the given time window.

    Opens ``db_path`` read-only. Returns a single ``source_unavailable``
    record if the database doesn't exist, a single
    ``available_with_zero_activity`` record if nothing falls in the window,
    or one record per observed token usage row / skill / MCP tool call.
    """
    if not db_path.exists():
        return [_unavailable_record(window)]

    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        records = _collect_token_records(connection, window)
        records.extend(_collect_tool_observation_records(connection, window))
    finally:
        connection.close()

    if not records:
        return [_zero_activity_record(window)]
    return records
