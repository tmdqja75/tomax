"""Tests for the read-only Codex adapter.

Uses only synthetic, anonymized fixture data built by
``tests/fixtures/codex_sessions.py`` — never real session data. The
non-negotiable rule this adapter must satisfy: cumulative token_count
snapshots become monotonic per-session deltas, never summed cumulative
totals.
"""

from __future__ import annotations

from datetime import datetime, timezone

from codex_sessions import (
    function_call_event,
    token_count_event,
    turn_context_event,
    write_rollout,
)

from tomax.adapters import codex
from tomax.models import SourceStatus, SupportedAgent
from tomax.time_window import TimeWindow

UTC = timezone.utc

WINDOW = TimeWindow(
    start=datetime(2026, 7, 4, tzinfo=UTC),
    end=datetime(2026, 7, 18, tzinfo=UTC),
)

IN_WINDOW_1 = "2026-07-10T10:00:00.000Z"
IN_WINDOW_2 = "2026-07-10T10:05:00.000Z"
IN_WINDOW_3 = "2026-07-10T10:10:00.000Z"
BEFORE_WINDOW = "2026-07-01T00:00:00.000Z"
AFTER_WINDOW = "2026-07-19T00:00:00.000Z"


def _sessions_dir(tmp_path):
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    return sessions_dir


def test_collect_returns_source_unavailable_when_sessions_dir_is_missing(tmp_path) -> None:
    missing_dir = tmp_path / "does-not-exist"

    [record] = codex.collect(missing_dir, WINDOW)

    assert record.agent is SupportedAgent.CODEX
    assert record.source_status is SourceStatus.SOURCE_UNAVAILABLE
    assert record.tokens is None


def test_collect_returns_zero_activity_when_sessions_dir_is_empty(tmp_path) -> None:
    sessions_dir = _sessions_dir(tmp_path)

    [record] = codex.collect(sessions_dir, WINDOW)

    assert record.source_status is SourceStatus.AVAILABLE_WITH_ZERO_ACTIVITY
    assert record.headline_total == 0


def test_collect_excludes_events_outside_the_window(tmp_path) -> None:
    sessions_dir = _sessions_dir(tmp_path)
    write_rollout(
        sessions_dir / "2026" / "07" / "01" / "rollout-x.jsonl",
        "session-1",
        [
            token_count_event(BEFORE_WINDOW, total_input=100, total_output=50),
        ],
    )

    [record] = codex.collect(sessions_dir, WINDOW)

    assert record.source_status is SourceStatus.AVAILABLE_WITH_ZERO_ACTIVITY


def test_first_snapshot_in_a_session_is_its_own_delta(tmp_path) -> None:
    sessions_dir = _sessions_dir(tmp_path)
    write_rollout(
        sessions_dir / "2026" / "07" / "10" / "rollout-x.jsonl",
        "session-1",
        [
            token_count_event(IN_WINDOW_1, total_input=3081, total_output=72, total_reasoning=10),
        ],
    )

    [record] = codex.collect(sessions_dir, WINDOW)

    assert record.tokens.input_tokens == 3081
    assert record.tokens.output_tokens == 72
    assert record.tokens.reasoning_tokens == 10
    assert record.source_status is SourceStatus.AVAILABLE_WITH_ACTIVITY


def test_token_records_carry_the_model_from_a_preceding_turn_context_event(tmp_path) -> None:
    sessions_dir = _sessions_dir(tmp_path)
    write_rollout(
        sessions_dir / "2026" / "07" / "10" / "rollout-x.jsonl",
        "session-1",
        [
            turn_context_event(IN_WINDOW_1, model="gpt-5.5"),
            token_count_event(IN_WINDOW_1, total_input=100, total_output=50),
        ],
    )

    [record] = codex.collect(sessions_dir, WINDOW)

    assert record.model == "gpt-5.5"


def test_a_mid_session_model_switch_splits_attribution_across_deltas(tmp_path) -> None:
    sessions_dir = _sessions_dir(tmp_path)
    write_rollout(
        sessions_dir / "2026" / "07" / "10" / "rollout-x.jsonl",
        "session-1",
        [
            turn_context_event(IN_WINDOW_1, model="gpt-5.5"),
            token_count_event(IN_WINDOW_1, total_input=100, total_output=50),
            turn_context_event(IN_WINDOW_2, model="gpt-5.5-mini"),
            token_count_event(IN_WINDOW_2, total_input=150, total_output=80),
        ],
    )

    records = codex.collect(sessions_dir, WINDOW)

    by_model = {r.model: r.tokens.input_tokens for r in records}
    assert by_model == {"gpt-5.5": 100, "gpt-5.5-mini": 50}


def test_collect_computes_deltas_between_snapshots_never_sums_cumulative_totals(tmp_path) -> None:
    sessions_dir = _sessions_dir(tmp_path)
    write_rollout(
        sessions_dir / "2026" / "07" / "10" / "rollout-x.jsonl",
        "session-1",
        [
            token_count_event(IN_WINDOW_1, total_input=3081, total_output=72),
            token_count_event(IN_WINDOW_2, total_input=6265, total_output=177),
            token_count_event(IN_WINDOW_3, total_input=9564, total_output=227),
        ],
    )

    records = codex.collect(sessions_dir, WINDOW)

    deltas = sorted((r.tokens.input_tokens, r.tokens.output_tokens) for r in records)
    # 3081-0, 6265-3081=3184, 9564-6265=3299 -- never the raw cumulative values themselves
    assert deltas == [(3081, 72), (3184, 105), (3299, 50)]
    assert sum(r.tokens.input_tokens for r in records) == 9564
    assert sum(r.tokens.output_tokens for r in records) == 227


def test_collect_skips_duplicate_snapshot_with_no_new_tokens(tmp_path) -> None:
    sessions_dir = _sessions_dir(tmp_path)
    write_rollout(
        sessions_dir / "2026" / "07" / "10" / "rollout-x.jsonl",
        "session-1",
        [
            token_count_event(IN_WINDOW_1, total_input=3081, total_output=72),
            token_count_event(IN_WINDOW_2, total_input=3081, total_output=72),  # duplicate, no new usage
        ],
    )

    records = codex.collect(sessions_dir, WINDOW)

    assert len(records) == 1
    assert records[0].tokens.input_tokens == 3081


def test_collect_treats_a_counter_reset_as_a_fresh_delta_not_a_negative_one(tmp_path) -> None:
    sessions_dir = _sessions_dir(tmp_path)
    write_rollout(
        sessions_dir / "2026" / "07" / "10" / "rollout-x.jsonl",
        "session-1",
        [
            token_count_event(IN_WINDOW_1, total_input=50_000, total_output=2_000),
            # counter resets (e.g. context compaction) to a much smaller cumulative value
            token_count_event(IN_WINDOW_2, total_input=500, total_output=40),
        ],
    )

    records = codex.collect(sessions_dir, WINDOW)

    deltas = sorted((r.tokens.input_tokens, r.tokens.output_tokens) for r in records)
    assert deltas == [(500, 40), (50_000, 2_000)]
    assert all(r.tokens.input_tokens >= 0 and r.tokens.output_tokens >= 0 for r in records)


def test_collect_treats_an_asymmetric_dimension_drop_as_a_reset(tmp_path) -> None:
    sessions_dir = _sessions_dir(tmp_path)
    write_rollout(
        sessions_dir / "2026" / "07" / "10" / "rollout-x.jsonl",
        "session-1",
        [
            # input drops while output rises enough that the aggregate sum
            # still increases -- must still be detected as a reset, or the
            # per-dimension delta would go negative for input_tokens.
            token_count_event(IN_WINDOW_1, total_input=1000, total_output=500),
            token_count_event(IN_WINDOW_2, total_input=200, total_output=2000),
        ],
    )

    records = codex.collect(sessions_dir, WINDOW)

    for record in records:
        assert record.tokens.input_tokens >= 0
        assert record.tokens.output_tokens >= 0
    deltas = sorted((r.tokens.input_tokens, r.tokens.output_tokens) for r in records)
    assert deltas == [(200, 2000), (1000, 500)]


def test_collect_tracks_cumulative_state_across_the_window_boundary(tmp_path) -> None:
    sessions_dir = _sessions_dir(tmp_path)
    write_rollout(
        sessions_dir / "2026" / "07" / "10" / "rollout-x.jsonl",
        "session-1",
        [
            token_count_event(BEFORE_WINDOW, total_input=1000, total_output=100),
            token_count_event(IN_WINDOW_1, total_input=1300, total_output=150),
        ],
    )

    [record] = codex.collect(sessions_dir, WINDOW)

    # delta must be relative to the pre-window snapshot (300, 50), not the
    # raw in-window cumulative value (1300, 150) treated as a fresh delta.
    assert record.tokens.input_tokens == 300
    assert record.tokens.output_tokens == 50


def test_reasoning_tokens_are_tracked_separately_for_codex(tmp_path) -> None:
    sessions_dir = _sessions_dir(tmp_path)
    write_rollout(
        sessions_dir / "2026" / "07" / "10" / "rollout-x.jsonl",
        "session-1",
        [
            token_count_event(IN_WINDOW_1, total_input=10, total_output=5, total_reasoning=3),
        ],
    )

    [record] = codex.collect(sessions_dir, WINDOW)

    assert record.tokens.reasoning_tokens == 3
    assert record.headline_total == 18


def test_cache_read_tokens_are_diffed_like_other_dimensions_for_codex(tmp_path) -> None:
    sessions_dir = _sessions_dir(tmp_path)
    write_rollout(
        sessions_dir / "2026" / "07" / "10" / "rollout-x.jsonl",
        "session-1",
        [
            token_count_event(IN_WINDOW_1, total_input=3081, total_output=72, total_cached=2000),
            token_count_event(IN_WINDOW_2, total_input=6265, total_output=177, total_cached=4608),
        ],
    )

    records = codex.collect(sessions_dir, WINDOW)

    cache_deltas = sorted(r.tokens.cache_read_tokens for r in records)
    assert cache_deltas == [2000, 2608]


def test_cache_write_tokens_are_always_zero_for_codex(tmp_path) -> None:
    sessions_dir = _sessions_dir(tmp_path)
    write_rollout(
        sessions_dir / "2026" / "07" / "10" / "rollout-x.jsonl",
        "session-1",
        [
            token_count_event(IN_WINDOW_1, total_input=10, total_output=5, total_cached=4608),
        ],
    )

    [record] = codex.collect(sessions_dir, WINDOW)

    assert record.tokens.cache_write_tokens == 0
    assert record.tokens.cache_read_tokens == 4608
    assert record.headline_total == 15


def test_collect_splits_mcp_server_and_tool_name(tmp_path) -> None:
    sessions_dir = _sessions_dir(tmp_path)
    write_rollout(
        sessions_dir / "2026" / "07" / "10" / "rollout-x.jsonl",
        "session-1",
        [
            token_count_event(IN_WINDOW_1, total_input=10, total_output=5),
            function_call_event(
                IN_WINDOW_1, name="mcp__synthetic_server__synthetic_tool", call_id="call-1"
            ),
        ],
    )

    records = codex.collect(sessions_dir, WINDOW)

    [mcp_record] = [r for r in records if r.observed_mcp_server_name is not None]
    assert mcp_record.observed_mcp_server_name == "synthetic_server"
    assert mcp_record.observed_mcp_tool_name == "synthetic_tool"
    assert mcp_record.source_status is SourceStatus.AVAILABLE_WITH_ZERO_ACTIVITY
    assert mcp_record.headline_total == 0


def test_collect_ignores_native_tool_calls_that_are_not_mcp(tmp_path) -> None:
    sessions_dir = _sessions_dir(tmp_path)
    write_rollout(
        sessions_dir / "2026" / "07" / "10" / "rollout-x.jsonl",
        "session-1",
        [
            token_count_event(IN_WINDOW_1, total_input=10, total_output=5),
            function_call_event(IN_WINDOW_1, name="shell", call_id="call-1"),
            function_call_event(IN_WINDOW_1, name="apply_patch", call_id="call-2"),
        ],
    )

    records = codex.collect(sessions_dir, WINDOW)

    assert not any(r.observed_mcp_server_name for r in records)


def test_collect_does_not_invent_a_skill_convention_for_codex(tmp_path) -> None:
    sessions_dir = _sessions_dir(tmp_path)
    write_rollout(
        sessions_dir / "2026" / "07" / "10" / "rollout-x.jsonl",
        "session-1",
        [
            token_count_event(IN_WINDOW_1, total_input=10, total_output=5),
            function_call_event(IN_WINDOW_1, name="skill_view", call_id="call-1"),
        ],
    )

    records = codex.collect(sessions_dir, WINDOW)

    assert not any(r.observed_skill_name for r in records)


def test_fingerprint_never_contains_the_raw_session_id(tmp_path) -> None:
    sessions_dir = _sessions_dir(tmp_path)
    real_session_id = "super-secret-real-session-identifier"
    write_rollout(
        sessions_dir / "2026" / "07" / "10" / "rollout-x.jsonl",
        real_session_id,
        [
            token_count_event(IN_WINDOW_1, total_input=10, total_output=5),
            function_call_event(
                IN_WINDOW_1, name="mcp__synthetic_server__synthetic_tool", call_id="call-1"
            ),
        ],
    )

    records = codex.collect(sessions_dir, WINDOW)

    assert len(records) == 2
    for record in records:
        assert real_session_id not in record.fingerprint


def test_session_fingerprint_never_contains_the_raw_session_id_and_is_shared(tmp_path) -> None:
    sessions_dir = _sessions_dir(tmp_path)
    real_session_id = "super-secret-real-session-identifier"
    write_rollout(
        sessions_dir / "2026" / "07" / "10" / "rollout-x.jsonl",
        real_session_id,
        [
            token_count_event(IN_WINDOW_1, total_input=10, total_output=5),
            function_call_event(
                IN_WINDOW_1, name="mcp__synthetic_server__synthetic_tool", call_id="call-1"
            ),
        ],
    )

    records = codex.collect(sessions_dir, WINDOW)

    assert len(records) == 2
    session_fingerprints = {r.session_fingerprint for r in records}
    assert len(session_fingerprints) == 1
    [session_fingerprint] = session_fingerprints
    assert session_fingerprint is not None
    assert real_session_id not in session_fingerprint


def test_marker_records_have_no_session_fingerprint(tmp_path) -> None:
    missing_dir = tmp_path / "does-not-exist"

    [unavailable_record] = codex.collect(missing_dir, WINDOW)

    assert unavailable_record.session_fingerprint is None

    empty_sessions_dir = _sessions_dir(tmp_path)

    [zero_activity_record] = codex.collect(empty_sessions_dir, WINDOW)

    assert zero_activity_record.session_fingerprint is None


def test_records_never_contain_raw_tool_arguments(tmp_path) -> None:
    sessions_dir = _sessions_dir(tmp_path)
    write_rollout(
        sessions_dir / "2026" / "07" / "10" / "rollout-x.jsonl",
        "session-1",
        [
            token_count_event(IN_WINDOW_1, total_input=10, total_output=5),
            function_call_event(
                IN_WINDOW_1, name="mcp__synthetic_server__synthetic_tool", call_id="call-1"
            ),
        ],
    )

    records = codex.collect(sessions_dir, WINDOW)

    for record in records:
        assert "{}" not in repr(record)
        assert "arguments" not in repr(record)


def test_collect_skips_malformed_json_lines_gracefully(tmp_path) -> None:
    sessions_dir = _sessions_dir(tmp_path)
    rollout_path = sessions_dir / "2026" / "07" / "10" / "rollout-x.jsonl"
    rollout_path.parent.mkdir(parents=True)
    rollout_path.write_text("{not valid json\n" + "\n")

    records = codex.collect(sessions_dir, WINDOW)

    assert len(records) == 1
    assert records[0].source_status is SourceStatus.AVAILABLE_WITH_ZERO_ACTIVITY


def test_collect_is_deterministic_across_repeat_calls(tmp_path) -> None:
    sessions_dir = _sessions_dir(tmp_path)
    write_rollout(
        sessions_dir / "2026" / "07" / "10" / "rollout-x.jsonl",
        "session-1",
        [
            token_count_event(IN_WINDOW_1, total_input=10, total_output=5),
            token_count_event(IN_WINDOW_2, total_input=20, total_output=8),
        ],
    )

    first_pass = codex.collect(sessions_dir, WINDOW)
    second_pass = codex.collect(sessions_dir, WINDOW)

    assert [r.fingerprint for r in first_pass] == [r.fingerprint for r in second_pass]
