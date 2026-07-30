"""Tests for the read-only Hermes Agent adapter.

Uses only synthetic, anonymized fixture data built by
``tests/fixtures/hermes_state.py`` — never real session data.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from hermes_state import build_hermes_state_db

from tomax.adapters import hermes
from tomax.models import SourceStatus, SupportedAgent
from tomax.time_window import TimeWindow

UTC = timezone.utc

WINDOW = TimeWindow(
    start=datetime(2026, 7, 4, tzinfo=UTC),
    end=datetime(2026, 7, 18, tzinfo=UTC),
)

IN_WINDOW = datetime(2026, 7, 10, 12, 0, tzinfo=UTC).timestamp()
BEFORE_WINDOW = datetime(2026, 7, 1, tzinfo=UTC).timestamp()
AFTER_WINDOW = datetime(2026, 7, 19, tzinfo=UTC).timestamp()


def _skill_view_tool_calls(call_id: str, skill_name: str) -> str:
    return json.dumps(
        [
            {
                "id": call_id,
                "call_id": call_id,
                "response_item_id": "resp-synthetic",
                "type": "function_call",
                "function": {
                    "name": "skill_view",
                    "arguments": json.dumps({"name": skill_name}),
                },
            }
        ]
    )


def test_collect_returns_source_unavailable_when_db_is_missing(tmp_path) -> None:
    missing_path = tmp_path / "does-not-exist.db"

    [record] = hermes.collect(missing_path, WINDOW)

    assert record.agent is SupportedAgent.HERMES_AGENT
    assert record.source_status is SourceStatus.SOURCE_UNAVAILABLE
    assert record.tokens is None


def test_collect_returns_zero_activity_when_no_sessions_in_window(tmp_path) -> None:
    db_path = tmp_path / "state.db"
    build_hermes_state_db(db_path)

    [record] = hermes.collect(db_path, WINDOW)

    assert record.source_status is SourceStatus.AVAILABLE_WITH_ZERO_ACTIVITY
    assert record.headline_total == 0


def test_collect_excludes_sessions_outside_the_window(tmp_path) -> None:
    db_path = tmp_path / "state.db"
    build_hermes_state_db(
        db_path,
        sessions=[
            {"id": "s-before", "source": "cli", "started_at": BEFORE_WINDOW},
            {"id": "s-after", "source": "cli", "started_at": AFTER_WINDOW},
        ],
        session_model_usage=[
            {
                "session_id": "s-before",
                "model": "synthetic-model",
                "input_tokens": 100,
                "output_tokens": 50,
                "last_seen": BEFORE_WINDOW,
            },
            {
                "session_id": "s-after",
                "model": "synthetic-model",
                "input_tokens": 100,
                "output_tokens": 50,
                "last_seen": AFTER_WINDOW,
            },
        ],
    )

    [record] = hermes.collect(db_path, WINDOW)

    assert record.source_status is SourceStatus.AVAILABLE_WITH_ZERO_ACTIVITY


def test_collect_aggregates_token_totals_from_session_model_usage(tmp_path) -> None:
    db_path = tmp_path / "state.db"
    build_hermes_state_db(
        db_path,
        sessions=[{"id": "s-1", "source": "cli", "started_at": IN_WINDOW}],
        session_model_usage=[
            {
                "session_id": "s-1",
                "model": "synthetic-model-a",
                "input_tokens": 1000,
                "output_tokens": 200,
                "reasoning_tokens": 30,
                "cache_read_tokens": 5000,
                "cache_write_tokens": 500,
                "last_seen": IN_WINDOW,
            },
            {
                "session_id": "s-1",
                "model": "synthetic-model-b",
                "input_tokens": 10,
                "output_tokens": 5,
                "reasoning_tokens": 0,
                "last_seen": IN_WINDOW,
            },
        ],
    )

    records = hermes.collect(db_path, WINDOW)

    assert len(records) == 2
    assert all(r.agent is SupportedAgent.HERMES_AGENT for r in records)
    assert all(r.source_status is SourceStatus.AVAILABLE_WITH_ACTIVITY for r in records)
    totals = sorted(r.headline_total for r in records)
    assert totals == [15, 1230]
    cache_totals = sorted((r.tokens.cache_read_tokens, r.tokens.cache_write_tokens) for r in records)
    assert cache_totals == [(0, 0), (5000, 500)]


def test_cache_tokens_never_inflate_the_headline_total(tmp_path) -> None:
    db_path = tmp_path / "state.db"
    build_hermes_state_db(
        db_path,
        sessions=[{"id": "s-1", "source": "cli", "started_at": IN_WINDOW}],
        session_model_usage=[
            {
                "session_id": "s-1",
                "model": "synthetic-model",
                "input_tokens": 10,
                "output_tokens": 5,
                "reasoning_tokens": 1,
                "cache_read_tokens": 999_999,
                "cache_write_tokens": 999_999,
                "last_seen": IN_WINDOW,
            },
        ],
    )

    [record] = hermes.collect(db_path, WINDOW)

    assert record.headline_total == 16
    assert record.tokens.cache_read_tokens == 999_999
    assert record.tokens.cache_write_tokens == 999_999


def test_collect_extracts_skill_name_from_skill_view_call(tmp_path) -> None:
    db_path = tmp_path / "state.db"
    build_hermes_state_db(
        db_path,
        sessions=[{"id": "s-1", "source": "cli", "started_at": IN_WINDOW}],
        messages=[
            {
                "id": 1,
                "session_id": "s-1",
                "role": "assistant",
                "tool_calls": _skill_view_tool_calls("call-1", "synthetic-skill-name"),
                "timestamp": IN_WINDOW,
            },
            {
                "id": 2,
                "session_id": "s-1",
                "role": "tool",
                "tool_name": "skill_view",
                "tool_call_id": "call-1",
                "timestamp": IN_WINDOW,
            },
        ],
    )

    records = hermes.collect(db_path, WINDOW)

    [skill_record] = [r for r in records if r.observed_skill_name is not None]
    assert skill_record.observed_skill_name == "synthetic-skill-name"
    assert skill_record.observed_mcp_server_name is None
    assert skill_record.source_status is SourceStatus.AVAILABLE_WITH_ZERO_ACTIVITY
    assert skill_record.headline_total == 0


def test_collect_splits_mcp_server_and_tool_name(tmp_path) -> None:
    db_path = tmp_path / "state.db"
    build_hermes_state_db(
        db_path,
        sessions=[{"id": "s-1", "source": "cli", "started_at": IN_WINDOW}],
        messages=[
            {
                "id": 1,
                "session_id": "s-1",
                "role": "tool",
                "tool_name": "mcp__synthetic_server__synthetic_tool",
                "tool_call_id": "call-mcp-1",
                "timestamp": IN_WINDOW,
            },
        ],
    )

    records = hermes.collect(db_path, WINDOW)

    [mcp_record] = [r for r in records if r.observed_mcp_server_name is not None]
    assert mcp_record.observed_mcp_server_name == "synthetic_server"
    assert mcp_record.observed_mcp_tool_name == "synthetic_tool"
    assert mcp_record.observed_skill_name is None
    assert mcp_record.source_status is SourceStatus.AVAILABLE_WITH_ZERO_ACTIVITY


def test_collect_skips_malformed_mcp_tool_name_without_full_delimiter(tmp_path) -> None:
    db_path = tmp_path / "state.db"
    build_hermes_state_db(
        db_path,
        sessions=[{"id": "s-1", "source": "cli", "started_at": IN_WINDOW}],
        messages=[
            {
                "id": 1,
                "session_id": "s-1",
                "role": "tool",
                "tool_name": "mcp__incomplete",
                "tool_call_id": "call-1",
                "timestamp": IN_WINDOW,
            },
        ],
    )

    records = hermes.collect(db_path, WINDOW)

    assert not any(r.observed_mcp_server_name for r in records)
    assert not any(r.observed_mcp_tool_name for r in records)


def test_collect_ignores_native_tool_calls_that_are_not_skill_or_mcp(tmp_path) -> None:
    db_path = tmp_path / "state.db"
    build_hermes_state_db(
        db_path,
        sessions=[{"id": "s-1", "source": "cli", "started_at": IN_WINDOW}],
        messages=[
            {
                "id": 1,
                "session_id": "s-1",
                "role": "tool",
                "tool_name": "bash",
                "tool_call_id": "call-1",
                "timestamp": IN_WINDOW,
            },
        ],
    )

    records = hermes.collect(db_path, WINDOW)

    assert not any(r.observed_skill_name or r.observed_mcp_server_name for r in records)
    assert all(r.source_status is SourceStatus.AVAILABLE_WITH_ZERO_ACTIVITY for r in records)


def test_fingerprint_never_contains_the_raw_session_id(tmp_path) -> None:
    db_path = tmp_path / "state.db"
    real_session_id = "super-secret-real-session-identifier"
    build_hermes_state_db(
        db_path,
        sessions=[{"id": real_session_id, "source": "cli", "started_at": IN_WINDOW}],
        session_model_usage=[
            {
                "session_id": real_session_id,
                "model": "synthetic-model",
                "input_tokens": 10,
                "output_tokens": 5,
                "last_seen": IN_WINDOW,
            },
        ],
        messages=[
            {
                "id": 1,
                "session_id": real_session_id,
                "role": "tool",
                "tool_name": "mcp__synthetic_server__synthetic_tool",
                "tool_call_id": "call-1",
                "timestamp": IN_WINDOW,
            },
        ],
    )

    records = hermes.collect(db_path, WINDOW)

    assert len(records) == 2
    for record in records:
        assert real_session_id not in record.fingerprint


def test_session_fingerprint_never_contains_the_raw_session_id_and_is_shared(tmp_path) -> None:
    db_path = tmp_path / "state.db"
    real_session_id = "super-secret-real-session-identifier"
    build_hermes_state_db(
        db_path,
        sessions=[{"id": real_session_id, "source": "cli", "started_at": IN_WINDOW}],
        session_model_usage=[
            {
                "session_id": real_session_id,
                "model": "synthetic-model",
                "input_tokens": 10,
                "output_tokens": 5,
                "last_seen": IN_WINDOW,
            },
        ],
        messages=[
            {
                "id": 1,
                "session_id": real_session_id,
                "role": "tool",
                "tool_name": "mcp__synthetic_server__synthetic_tool",
                "tool_call_id": "call-1",
                "timestamp": IN_WINDOW,
            },
        ],
    )

    records = hermes.collect(db_path, WINDOW)

    assert len(records) == 2
    session_fingerprints = {r.session_fingerprint for r in records}
    assert len(session_fingerprints) == 1
    [session_fingerprint] = session_fingerprints
    assert session_fingerprint is not None
    assert real_session_id not in session_fingerprint


def test_marker_records_have_no_session_fingerprint(tmp_path) -> None:
    missing_path = tmp_path / "does-not-exist.db"

    [unavailable_record] = hermes.collect(missing_path, WINDOW)

    assert unavailable_record.session_fingerprint is None

    empty_db_path = tmp_path / "state.db"
    build_hermes_state_db(empty_db_path)

    [zero_activity_record] = hermes.collect(empty_db_path, WINDOW)

    assert zero_activity_record.session_fingerprint is None


def test_records_never_contain_message_content_or_tool_arguments(tmp_path) -> None:
    db_path = tmp_path / "state.db"
    sensitive_content = "sensitive-prompt-content-should-never-appear"
    build_hermes_state_db(
        db_path,
        sessions=[{"id": "s-1", "source": "cli", "started_at": IN_WINDOW}],
        messages=[
            {
                "id": 1,
                "session_id": "s-1",
                "role": "user",
                "content": sensitive_content,
                "timestamp": IN_WINDOW,
            },
            {
                "id": 2,
                "session_id": "s-1",
                "role": "assistant",
                "tool_calls": _skill_view_tool_calls("call-1", "synthetic-skill"),
                "timestamp": IN_WINDOW,
            },
            {
                "id": 3,
                "session_id": "s-1",
                "role": "tool",
                "tool_name": "skill_view",
                "tool_call_id": "call-1",
                "content": sensitive_content,
                "timestamp": IN_WINDOW,
            },
        ],
    )

    records = hermes.collect(db_path, WINDOW)

    for record in records:
        assert sensitive_content not in repr(record)


def test_collect_includes_usage_observed_in_window_from_a_session_started_earlier(
    tmp_path,
) -> None:
    db_path = tmp_path / "state.db"
    build_hermes_state_db(
        db_path,
        sessions=[{"id": "s-long-running", "source": "cli", "started_at": BEFORE_WINDOW}],
        session_model_usage=[
            {
                "session_id": "s-long-running",
                "model": "synthetic-model",
                "input_tokens": 10,
                "output_tokens": 5,
                "last_seen": IN_WINDOW,
            },
        ],
    )

    [record] = hermes.collect(db_path, WINDOW)

    assert record.source_status is SourceStatus.AVAILABLE_WITH_ACTIVITY
    assert record.headline_total == 15


def test_collect_excludes_usage_last_seen_outside_window_even_if_session_started_inside(
    tmp_path,
) -> None:
    db_path = tmp_path / "state.db"
    build_hermes_state_db(
        db_path,
        sessions=[{"id": "s-1", "source": "cli", "started_at": IN_WINDOW}],
        session_model_usage=[
            {
                "session_id": "s-1",
                "model": "synthetic-model",
                "input_tokens": 10,
                "output_tokens": 5,
                "last_seen": AFTER_WINDOW,
            },
        ],
    )

    [record] = hermes.collect(db_path, WINDOW)

    assert record.source_status is SourceStatus.AVAILABLE_WITH_ZERO_ACTIVITY


def test_collect_attributes_skill_name_even_when_the_assistant_call_predates_the_window(
    tmp_path,
) -> None:
    db_path = tmp_path / "state.db"
    build_hermes_state_db(
        db_path,
        sessions=[{"id": "s-1", "source": "cli", "started_at": BEFORE_WINDOW}],
        messages=[
            {
                "id": 1,
                "session_id": "s-1",
                "role": "assistant",
                "tool_calls": _skill_view_tool_calls("call-1", "synthetic-skill-name"),
                "timestamp": BEFORE_WINDOW,
            },
            {
                "id": 2,
                "session_id": "s-1",
                "role": "tool",
                "tool_name": "skill_view",
                "tool_call_id": "call-1",
                "timestamp": IN_WINDOW,
            },
        ],
    )

    records = hermes.collect(db_path, WINDOW)

    [skill_record] = [r for r in records if r.observed_skill_name is not None]
    assert skill_record.observed_skill_name == "synthetic-skill-name"


def test_collect_skips_gracefully_on_malformed_tool_calls_json(tmp_path) -> None:
    db_path = tmp_path / "state.db"
    build_hermes_state_db(
        db_path,
        sessions=[{"id": "s-1", "source": "cli", "started_at": IN_WINDOW}],
        messages=[
            {
                "id": 1,
                "session_id": "s-1",
                "role": "assistant",
                "tool_calls": "{not valid json",
                "timestamp": IN_WINDOW,
            },
            {
                "id": 2,
                "session_id": "s-1",
                "role": "tool",
                "tool_name": "skill_view",
                "tool_call_id": "call-1",
                "timestamp": IN_WINDOW,
            },
        ],
    )

    records = hermes.collect(db_path, WINDOW)

    assert not any(r.observed_skill_name for r in records)


def test_collect_is_deterministic_across_repeat_calls(tmp_path) -> None:
    db_path = tmp_path / "state.db"
    build_hermes_state_db(
        db_path,
        sessions=[{"id": "s-1", "source": "cli", "started_at": IN_WINDOW}],
        session_model_usage=[
            {
                "session_id": "s-1",
                "model": "synthetic-model",
                "input_tokens": 10,
                "output_tokens": 5,
                "last_seen": IN_WINDOW,
            },
        ],
    )

    first_pass = hermes.collect(db_path, WINDOW)
    second_pass = hermes.collect(db_path, WINDOW)

    assert [r.fingerprint for r in first_pass] == [r.fingerprint for r in second_pass]
