"""Tests for the read-only Claude Code adapter.

Uses only synthetic, anonymized fixture data built by
``tests/fixtures/claude_code_projects.py`` — never real transcript data.
"""

from __future__ import annotations

from datetime import datetime, timezone

from claude_code_projects import (
    assistant_event,
    mcp_tool_use,
    native_tool_use,
    skill_tool_use,
    user_event,
    write_transcript,
)

from tomax.adapters import claude_code
from tomax.models import SourceStatus, SupportedAgent
from tomax.time_window import TimeWindow

UTC = timezone.utc

WINDOW = TimeWindow(
    start=datetime(2026, 7, 4, tzinfo=UTC),
    end=datetime(2026, 7, 18, tzinfo=UTC),
)

IN_WINDOW = "2026-07-10T12:00:00.000Z"
BEFORE_WINDOW = "2026-07-01T00:00:00.000Z"
AFTER_WINDOW = "2026-07-19T00:00:00.000Z"


def _project_dir(tmp_path):
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    return projects_dir


def test_collect_returns_source_unavailable_when_projects_dir_is_missing(tmp_path) -> None:
    missing_dir = tmp_path / "does-not-exist"

    [record] = claude_code.collect(missing_dir, WINDOW)

    assert record.agent is SupportedAgent.CLAUDE_CODE
    assert record.source_status is SourceStatus.SOURCE_UNAVAILABLE
    assert record.tokens is None


def test_collect_returns_zero_activity_when_projects_dir_is_empty(tmp_path) -> None:
    projects_dir = _project_dir(tmp_path)

    [record] = claude_code.collect(projects_dir, WINDOW)

    assert record.source_status is SourceStatus.AVAILABLE_WITH_ZERO_ACTIVITY
    assert record.headline_total == 0


def test_collect_excludes_events_outside_the_window(tmp_path) -> None:
    projects_dir = _project_dir(tmp_path)
    write_transcript(
        projects_dir / "proj-a" / "session-1.jsonl",
        [
            assistant_event(
                uuid="uuid-before",
                session_id="session-1",
                timestamp=BEFORE_WINDOW,
                input_tokens=100,
                output_tokens=50,
            ),
            assistant_event(
                uuid="uuid-after",
                session_id="session-1",
                timestamp=AFTER_WINDOW,
                input_tokens=100,
                output_tokens=50,
            ),
        ],
    )

    [record] = claude_code.collect(projects_dir, WINDOW)

    assert record.source_status is SourceStatus.AVAILABLE_WITH_ZERO_ACTIVITY


def test_collect_returns_token_records_for_in_window_assistant_events(tmp_path) -> None:
    projects_dir = _project_dir(tmp_path)
    write_transcript(
        projects_dir / "proj-a" / "session-1.jsonl",
        [
            user_event(uuid="uuid-u1", session_id="session-1", timestamp=IN_WINDOW),
            assistant_event(
                uuid="uuid-a1",
                session_id="session-1",
                timestamp=IN_WINDOW,
                input_tokens=1000,
                output_tokens=200,
                cache_creation_input_tokens=5000,
                cache_read_input_tokens=9000,
            ),
        ],
    )

    records = claude_code.collect(projects_dir, WINDOW)

    [record] = records
    assert record.agent is SupportedAgent.CLAUDE_CODE
    assert record.source_status is SourceStatus.AVAILABLE_WITH_ACTIVITY
    assert record.headline_total == 1200
    assert record.tokens.cache_write_tokens == 5000
    assert record.tokens.cache_read_tokens == 9000


def test_token_records_carry_the_model_from_the_assistant_event(tmp_path) -> None:
    projects_dir = _project_dir(tmp_path)
    write_transcript(
        projects_dir / "proj-a" / "session-1.jsonl",
        [
            assistant_event(
                uuid="uuid-a1",
                session_id="session-1",
                timestamp=IN_WINDOW,
                model="claude-sonnet-5",
                input_tokens=10,
                output_tokens=20,
            ),
        ],
    )

    [record] = claude_code.collect(projects_dir, WINDOW)

    assert record.model == "claude-sonnet-5"


def test_reasoning_tokens_are_always_zero_for_claude_code(tmp_path) -> None:
    projects_dir = _project_dir(tmp_path)
    write_transcript(
        projects_dir / "proj-a" / "session-1.jsonl",
        [
            assistant_event(
                uuid="uuid-a1",
                session_id="session-1",
                timestamp=IN_WINDOW,
                input_tokens=10,
                output_tokens=20,
            ),
        ],
    )

    [record] = claude_code.collect(projects_dir, WINDOW)

    assert record.tokens.reasoning_tokens == 0
    assert record.headline_total == 30


def test_cache_tokens_never_inflate_the_headline_total(tmp_path) -> None:
    projects_dir = _project_dir(tmp_path)
    write_transcript(
        projects_dir / "proj-a" / "session-1.jsonl",
        [
            assistant_event(
                uuid="uuid-a1",
                session_id="session-1",
                timestamp=IN_WINDOW,
                input_tokens=10,
                output_tokens=5,
                cache_creation_input_tokens=999_999,
                cache_read_input_tokens=999_999,
            ),
        ],
    )

    [record] = claude_code.collect(projects_dir, WINDOW)

    assert record.headline_total == 15
    assert record.tokens.cache_write_tokens == 999_999
    assert record.tokens.cache_read_tokens == 999_999


def test_collect_extracts_skill_name_from_skill_tool_use(tmp_path) -> None:
    projects_dir = _project_dir(tmp_path)
    write_transcript(
        projects_dir / "proj-a" / "session-1.jsonl",
        [
            assistant_event(
                uuid="uuid-a1",
                session_id="session-1",
                timestamp=IN_WINDOW,
                input_tokens=10,
                output_tokens=5,
                tool_use=[skill_tool_use("call-1", "synthetic-skill-name")],
            ),
        ],
    )

    records = claude_code.collect(projects_dir, WINDOW)

    [skill_record] = [r for r in records if r.observed_skill_name is not None]
    assert skill_record.observed_skill_name == "synthetic-skill-name"
    assert skill_record.observed_mcp_server_name is None
    assert skill_record.source_status is SourceStatus.AVAILABLE_WITH_ZERO_ACTIVITY
    assert skill_record.headline_total == 0


def test_collect_splits_mcp_server_and_tool_name(tmp_path) -> None:
    projects_dir = _project_dir(tmp_path)
    write_transcript(
        projects_dir / "proj-a" / "session-1.jsonl",
        [
            assistant_event(
                uuid="uuid-a1",
                session_id="session-1",
                timestamp=IN_WINDOW,
                input_tokens=10,
                output_tokens=5,
                tool_use=[mcp_tool_use("call-1", "mcp__synthetic_server__synthetic_tool")],
            ),
        ],
    )

    records = claude_code.collect(projects_dir, WINDOW)

    [mcp_record] = [r for r in records if r.observed_mcp_server_name is not None]
    assert mcp_record.observed_mcp_server_name == "synthetic_server"
    assert mcp_record.observed_mcp_tool_name == "synthetic_tool"
    assert mcp_record.observed_skill_name is None


def test_collect_ignores_native_tool_calls_that_are_not_skill_or_mcp(tmp_path) -> None:
    projects_dir = _project_dir(tmp_path)
    write_transcript(
        projects_dir / "proj-a" / "session-1.jsonl",
        [
            assistant_event(
                uuid="uuid-a1",
                session_id="session-1",
                timestamp=IN_WINDOW,
                input_tokens=10,
                output_tokens=5,
                tool_use=[native_tool_use("call-1", "Bash")],
            ),
        ],
    )

    records = claude_code.collect(projects_dir, WINDOW)

    assert not any(r.observed_skill_name or r.observed_mcp_server_name for r in records)


def test_fingerprint_never_contains_the_raw_session_id_or_uuid(tmp_path) -> None:
    projects_dir = _project_dir(tmp_path)
    real_session_id = "super-secret-real-session-identifier"
    real_uuid = "super-secret-real-event-uuid"
    write_transcript(
        projects_dir / "proj-a" / "session-1.jsonl",
        [
            assistant_event(
                uuid=real_uuid,
                session_id=real_session_id,
                timestamp=IN_WINDOW,
                input_tokens=10,
                output_tokens=5,
                tool_use=[skill_tool_use("call-1", "synthetic-skill")],
            ),
        ],
    )

    records = claude_code.collect(projects_dir, WINDOW)

    assert len(records) == 2
    for record in records:
        assert real_session_id not in record.fingerprint
        assert real_uuid not in record.fingerprint


def test_session_fingerprint_never_contains_the_raw_session_id_and_is_shared(tmp_path) -> None:
    projects_dir = _project_dir(tmp_path)
    real_session_id = "super-secret-real-session-identifier"
    write_transcript(
        projects_dir / "proj-a" / "session-1.jsonl",
        [
            assistant_event(
                uuid="uuid-a1",
                session_id=real_session_id,
                timestamp=IN_WINDOW,
                input_tokens=10,
                output_tokens=5,
                tool_use=[skill_tool_use("call-1", "synthetic-skill")],
            ),
        ],
    )

    records = claude_code.collect(projects_dir, WINDOW)

    assert len(records) == 2
    session_fingerprints = {r.session_fingerprint for r in records}
    assert len(session_fingerprints) == 1
    [session_fingerprint] = session_fingerprints
    assert session_fingerprint is not None
    assert real_session_id not in session_fingerprint


def test_marker_records_have_no_session_fingerprint(tmp_path) -> None:
    missing_dir = tmp_path / "does-not-exist"

    [unavailable_record] = claude_code.collect(missing_dir, WINDOW)

    assert unavailable_record.session_fingerprint is None

    empty_projects_dir = _project_dir(tmp_path)

    [zero_activity_record] = claude_code.collect(empty_projects_dir, WINDOW)

    assert zero_activity_record.session_fingerprint is None


def test_records_never_contain_message_content(tmp_path) -> None:
    projects_dir = _project_dir(tmp_path)
    sensitive_content = "sensitive-prompt-content-should-never-appear"
    write_transcript(
        projects_dir / "proj-a" / "session-1.jsonl",
        [
            user_event(uuid="uuid-u1", session_id="session-1", timestamp=IN_WINDOW, content=sensitive_content),
            assistant_event(
                uuid="uuid-a1",
                session_id="session-1",
                timestamp=IN_WINDOW,
                input_tokens=10,
                output_tokens=5,
                tool_use=[skill_tool_use("call-1", "synthetic-skill")],
            ),
        ],
    )

    records = claude_code.collect(projects_dir, WINDOW)

    for record in records:
        assert sensitive_content not in repr(record)


def test_collect_skips_malformed_json_lines_gracefully(tmp_path) -> None:
    projects_dir = _project_dir(tmp_path)
    transcript_path = projects_dir / "proj-a" / "session-1.jsonl"
    transcript_path.parent.mkdir(parents=True)
    transcript_path.write_text("{not valid json\n" + "\n")

    records = claude_code.collect(projects_dir, WINDOW)

    assert len(records) == 1
    assert records[0].source_status is SourceStatus.AVAILABLE_WITH_ZERO_ACTIVITY


def test_collect_skips_events_with_missing_or_malformed_usage(tmp_path) -> None:
    projects_dir = _project_dir(tmp_path)
    write_transcript(
        projects_dir / "proj-a" / "session-1.jsonl",
        [
            assistant_event(uuid="uuid-a1", session_id="session-1", timestamp=IN_WINDOW, usage=None),
            assistant_event(
                uuid="uuid-a2", session_id="session-1", timestamp=IN_WINDOW, usage={"input_tokens": "not-an-int"}
            ),
        ],
    )

    records = claude_code.collect(projects_dir, WINDOW)

    assert len(records) == 1
    assert records[0].source_status is SourceStatus.AVAILABLE_WITH_ZERO_ACTIVITY


def test_collect_skips_events_with_boolean_token_values(tmp_path) -> None:
    projects_dir = _project_dir(tmp_path)
    write_transcript(
        projects_dir / "proj-a" / "session-1.jsonl",
        [
            assistant_event(
                uuid="uuid-a1",
                session_id="session-1",
                timestamp=IN_WINDOW,
                usage={"input_tokens": True, "output_tokens": 5},
            ),
        ],
    )

    records = claude_code.collect(projects_dir, WINDOW)

    assert len(records) == 1
    assert records[0].source_status is SourceStatus.AVAILABLE_WITH_ZERO_ACTIVITY


def test_collect_survives_invalid_utf8_bytes_in_a_transcript_file(tmp_path) -> None:
    projects_dir = _project_dir(tmp_path)
    transcript_path = projects_dir / "proj-a" / "session-1.jsonl"
    transcript_path.parent.mkdir(parents=True)
    with transcript_path.open("wb") as handle:
        handle.write(b'{"type": "assistant", \xff\xfe invalid bytes here}\n')

    records = claude_code.collect(projects_dir, WINDOW)

    assert len(records) == 1
    assert records[0].source_status is SourceStatus.AVAILABLE_WITH_ZERO_ACTIVITY


def test_collect_reads_across_multiple_project_directories(tmp_path) -> None:
    projects_dir = _project_dir(tmp_path)
    write_transcript(
        projects_dir / "proj-a" / "session-1.jsonl",
        [
            assistant_event(
                uuid="uuid-a1", session_id="session-1", timestamp=IN_WINDOW, input_tokens=10, output_tokens=5
            ),
        ],
    )
    write_transcript(
        projects_dir / "proj-b" / "session-2.jsonl",
        [
            assistant_event(
                uuid="uuid-b1", session_id="session-2", timestamp=IN_WINDOW, input_tokens=20, output_tokens=8
            ),
        ],
    )

    records = claude_code.collect(projects_dir, WINDOW)

    totals = sorted(r.headline_total for r in records)
    assert totals == [15, 28]


def test_collect_is_deterministic_across_repeat_calls(tmp_path) -> None:
    projects_dir = _project_dir(tmp_path)
    write_transcript(
        projects_dir / "proj-a" / "session-1.jsonl",
        [
            assistant_event(
                uuid="uuid-a1", session_id="session-1", timestamp=IN_WINDOW, input_tokens=10, output_tokens=5
            ),
        ],
    )

    first_pass = claude_code.collect(projects_dir, WINDOW)
    second_pass = claude_code.collect(projects_dir, WINDOW)

    assert [r.fingerprint for r in first_pass] == [r.fingerprint for r in second_pass]
