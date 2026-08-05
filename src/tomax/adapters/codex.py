"""Read-only adapter for Codex's local session rollout logs.

Reads ``<sessions_dir>/**/rollout-*.jsonl`` read-only and normalizes only
safe metadata. Each ``token_count`` event's ``info.total_token_usage`` is a
*cumulative* per-session snapshot, not a per-turn delta. This adapter always
derives the delta itself by diffing consecutive snapshots — it never sums
raw cumulative values, and never assumes the snapshot only grows: if a
snapshot's total is lower than the previous one (e.g. after a context-reset
event), that is treated as a counter reset and the new snapshot is counted
as its own delta from zero, rather than producing a negative delta.

Unlike Claude Code, Codex's usage payload does report a separate
``reasoning_output_tokens`` field, so Codex's ``reasoning_tokens`` is real,
not always zero. It also reports ``cached_input_tokens`` (a subset of
``input_tokens``, diffed the same way), mapped to ``cache_read_tokens``, and
``cache_write_input_tokens`` (diffed the same way), mapped to
``cache_write_tokens``. Older rollout files predate the
``cache_write_input_tokens`` field, so it defaults to 0 when absent rather
than rejecting the event.

The model name isn't on the ``token_count`` event itself; it's on separate
``turn_context`` events (``payload.model``) that precede the turns they
apply to. This adapter tracks the most recently seen ``turn_context``
model as it walks a session's events in order and stamps it onto each
token-delta record, so a mid-session model switch attributes deltas to
the correct model on each side of the switch.

MCP tool calls are recognized by the same ``mcp__<server>__<tool>``
convention used by Hermes and Claude Code. Codex has no confirmed
skill-invocation convention of its own (skills are a Claude
Code/Hermes-specific concept) — this adapter deliberately does not guess
one; only MCP calls are tracked. Native/general tool calls (shell,
apply_patch, etc.) aren't tracked either, since NormalizedUsageRecord has
no generic tool-name field for them.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

from tomax.adapters.base import make_fingerprint, split_mcp_tool_name
from tomax.models import NormalizedUsageRecord, SourceStatus, SupportedAgent, TokenUsage
from tomax.time_window import TimeWindow

_TOOL_CALL_PAYLOAD_TYPES = ("function_call", "custom_tool_call")


def _session_fingerprint(session_id: str) -> str | None:
    return make_fingerprint("codex", "session", session_id) if session_id else None


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
        agent=SupportedAgent.CODEX,
        occurred_at=window.end_utc,
        fingerprint=make_fingerprint(
            "codex",
            "source_unavailable",
            window.start_utc.isoformat(),
            window.end_utc.isoformat(),
        ),
        tokens=None,
        source_status=SourceStatus.SOURCE_UNAVAILABLE,
    )


def _zero_activity_record(window: TimeWindow) -> NormalizedUsageRecord:
    return NormalizedUsageRecord(
        agent=SupportedAgent.CODEX,
        occurred_at=window.end_utc,
        fingerprint=make_fingerprint(
            "codex",
            "zero_activity",
            window.start_utc.isoformat(),
            window.end_utc.isoformat(),
        ),
        tokens=TokenUsage(),
        source_status=SourceStatus.AVAILABLE_WITH_ZERO_ACTIVITY,
    )


def _iter_rollout_files(sessions_dir: Path) -> Iterator[Path]:
    yield from sorted(sessions_dir.glob("**/rollout-*.jsonl"))


def _iter_events(rollout_path: Path) -> Iterator[dict]:
    try:
        handle = rollout_path.open("r", encoding="utf-8", errors="replace")
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


def _session_id_from_events(events: list[dict]) -> str:
    for event in events:
        if event.get("type") == "session_meta":
            session_id = event.get("payload", {}).get("id")
            if isinstance(session_id, str):
                return session_id
    return ""


def _extract_total_usage(info: object) -> tuple[int, int, int, int, int] | None:
    if not isinstance(info, dict):
        return None
    total = info.get("total_token_usage")
    if not isinstance(total, dict):
        return None
    input_tokens = total.get("input_tokens")
    output_tokens = total.get("output_tokens")
    reasoning_tokens = total.get("reasoning_output_tokens")
    # cached_input_tokens is a subset of input_tokens, not additive to it (unlike
    # Claude Code's cache_read_input_tokens) -- tracked here purely as a separate
    # cache-read counter, never folded into input_tokens or the headline total.
    cached_input_tokens = total.get("cached_input_tokens")
    # cache_write_input_tokens is absent from rollout files predating it; default
    # to 0 rather than rejecting the whole event over a missing optional field.
    cache_write_input_tokens = total.get("cache_write_input_tokens", 0)
    values = (
        input_tokens,
        output_tokens,
        reasoning_tokens,
        cached_input_tokens,
        cache_write_input_tokens,
    )
    if any(not isinstance(v, int) or isinstance(v, bool) for v in values):
        return None
    return input_tokens, output_tokens, reasoning_tokens, cached_input_tokens, cache_write_input_tokens


def _model_from_turn_context(event: dict) -> str | None:
    model = event.get("payload", {}).get("model")
    return model if isinstance(model, str) and model else None


def _token_records_for_session(
    events: list[dict], window: TimeWindow, session_id: str
) -> list[NormalizedUsageRecord]:
    records = []
    previous_total: tuple[int, int, int, int, int] | None = None
    event_index = 0
    current_model: str | None = None

    for event in events:
        if event.get("type") == "turn_context":
            current_model = _model_from_turn_context(event) or current_model
            continue
        if event.get("type") != "event_msg":
            continue
        payload = event.get("payload", {})
        if payload.get("type") != "token_count":
            continue

        current_total = _extract_total_usage(payload.get("info"))
        if current_total is None:
            continue

        event_index += 1

        if previous_total is None or any(
            current < previous for current, previous in zip(current_total, previous_total)
        ):
            # No prior snapshot, or at least one dimension dropped below its
            # previous value: the counter reset (e.g. a context-compaction
            # event). Count the new snapshot as its own delta from zero
            # rather than risk a negative per-dimension delta — comparing
            # only the aggregate sum could miss a reset where one dimension
            # drops while another rises enough to mask it.
            delta = current_total
        else:
            delta = tuple(c - p for c, p in zip(current_total, previous_total))

        previous_total = current_total

        if delta == (0, 0, 0, 0, 0):
            continue

        occurred_at = _parse_timestamp(event.get("timestamp"))
        if occurred_at is None or not window.contains(occurred_at):
            continue

        tokens = TokenUsage(
            input_tokens=delta[0],
            output_tokens=delta[1],
            reasoning_tokens=delta[2],
            cache_read_tokens=delta[3],
            cache_write_tokens=delta[4],
        )
        status = (
            SourceStatus.AVAILABLE_WITH_ACTIVITY
            if tokens.headline_total > 0
            else SourceStatus.AVAILABLE_WITH_ZERO_ACTIVITY
        )
        records.append(
            NormalizedUsageRecord(
                agent=SupportedAgent.CODEX,
                occurred_at=occurred_at,
                fingerprint=make_fingerprint(
                    "codex", "token_count", session_id, str(event_index)
                ),
                session_fingerprint=_session_fingerprint(session_id),
                model=current_model,
                tokens=tokens,
                source_status=status,
            )
        )
    return records


def _tool_observation_records_for_session(
    events: list[dict], window: TimeWindow, session_id: str
) -> list[NormalizedUsageRecord]:
    records = []
    for event in events:
        if event.get("type") != "response_item":
            continue
        payload = event.get("payload", {})
        if payload.get("type") not in _TOOL_CALL_PAYLOAD_TYPES:
            continue

        tool_name = payload.get("name")
        call_id = payload.get("call_id")
        if not tool_name or not call_id:
            continue

        split = split_mcp_tool_name(tool_name)
        if split is None:
            continue
        mcp_server, mcp_tool = split

        occurred_at = _parse_timestamp(event.get("timestamp"))
        if occurred_at is None or not window.contains(occurred_at):
            continue

        records.append(
            NormalizedUsageRecord(
                agent=SupportedAgent.CODEX,
                occurred_at=occurred_at,
                fingerprint=make_fingerprint("codex", "tool_call", session_id, call_id),
                session_fingerprint=_session_fingerprint(session_id),
                tokens=TokenUsage(),
                observed_mcp_server_name=mcp_server,
                observed_mcp_tool_name=mcp_tool,
                source_status=SourceStatus.AVAILABLE_WITH_ZERO_ACTIVITY,
            )
        )
    return records


def collect(sessions_dir: Path, window: TimeWindow) -> list[NormalizedUsageRecord]:
    """Collect normalized Codex usage records for the given window.

    Returns a single ``source_unavailable`` record if ``sessions_dir``
    doesn't exist, a single ``available_with_zero_activity`` record if
    nothing falls in the window, or one record per computed per-session
    token delta / MCP tool call.
    """
    if not sessions_dir.exists() or not sessions_dir.is_dir():
        return [_unavailable_record(window)]

    records: list[NormalizedUsageRecord] = []
    for rollout_path in _iter_rollout_files(sessions_dir):
        events = list(_iter_events(rollout_path))
        if not events:
            continue
        session_id = _session_id_from_events(events)
        records.extend(_token_records_for_session(events, window, session_id))
        records.extend(_tool_observation_records_for_session(events, window, session_id))

    if not records:
        return [_zero_activity_record(window)]
    return records
