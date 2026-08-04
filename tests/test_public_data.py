"""Tests for sanitized daily public-record export."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone

from tomax.models import NormalizedUsageRecord, SourceStatus, SupportedAgent, TokenUsage
from tomax.privacy import PrivacyPolicy
from tomax.public_data import (
    SCHEMA_VERSION,
    build_daily_record,
    stage_daily_records,
    verify_checksum,
    write_daily_record,
)

UTC = timezone.utc
DAY = date(2026, 7, 10)


def _record(
    *,
    agent: SupportedAgent = SupportedAgent.CLAUDE_CODE,
    occurred_at: datetime = datetime(2026, 7, 10, 12, 0, tzinfo=UTC),
    fingerprint: str = "fp-default",
    session_fingerprint: str | None = "session-fp-default",
    model: str | None = None,
    tokens: TokenUsage | None = TokenUsage(input_tokens=10, output_tokens=5, reasoning_tokens=1),
    source_status: SourceStatus = SourceStatus.AVAILABLE_WITH_ACTIVITY,
    observed_skill_name: str | None = None,
    observed_mcp_server_name: str | None = None,
    observed_mcp_tool_name: str | None = None,
) -> NormalizedUsageRecord:
    return NormalizedUsageRecord(
        agent=agent,
        occurred_at=occurred_at,
        fingerprint=fingerprint,
        session_fingerprint=session_fingerprint,
        model=model,
        tokens=tokens,
        observed_skill_name=observed_skill_name,
        observed_mcp_server_name=observed_mcp_server_name,
        observed_mcp_tool_name=observed_mcp_tool_name,
        source_status=source_status,
    )


def test_build_daily_record_includes_schema_version_device_id_and_date() -> None:
    payload = build_daily_record(device_id="device-abc", day=DAY, records=[])

    assert payload["schema_version"] == SCHEMA_VERSION
    assert payload["device_id"] == "device-abc"
    assert payload["date"] == "2026-07-10"


def test_checksum_round_trips_and_verifies() -> None:
    payload = build_daily_record(device_id="device-abc", day=DAY, records=[_record()])

    assert verify_checksum(payload)


def test_checksum_detects_tampering() -> None:
    payload = build_daily_record(device_id="device-abc", day=DAY, records=[_record()])

    tampered = dict(payload)
    tampered["agents"] = dict(tampered["agents"])
    tampered["agents"][SupportedAgent.CLAUDE_CODE.value] = dict(
        tampered["agents"][SupportedAgent.CLAUDE_CODE.value]
    )
    tampered["agents"][SupportedAgent.CLAUDE_CODE.value]["input_tokens"] += 1000

    assert not verify_checksum(tampered)


def test_only_records_matching_the_given_day_are_included() -> None:
    other_day_record = _record(
        occurred_at=datetime(2026, 7, 9, 12, 0, tzinfo=UTC),
        fingerprint="fp-other-day",
        session_fingerprint="session-fp-other-day",
        tokens=TokenUsage(input_tokens=999, output_tokens=999),
    )
    same_day_record = _record(fingerprint="fp-same-day", session_fingerprint="session-fp-same-day")

    payload = build_daily_record(
        device_id="device-abc", day=DAY, records=[other_day_record, same_day_record]
    )

    claude_code = payload["agents"][SupportedAgent.CLAUDE_CODE.value]
    assert claude_code["input_tokens"] == 10
    assert claude_code["output_tokens"] == 5


def test_agent_status_prefers_activity_over_zero_over_unavailable() -> None:
    activity = _record(
        fingerprint="fp-1",
        session_fingerprint="s-1",
        tokens=TokenUsage(input_tokens=5),
        source_status=SourceStatus.AVAILABLE_WITH_ACTIVITY,
    )
    zero = _record(
        fingerprint="fp-2",
        session_fingerprint="s-2",
        tokens=TokenUsage(),
        source_status=SourceStatus.AVAILABLE_WITH_ZERO_ACTIVITY,
    )

    payload = build_daily_record(device_id="device-abc", day=DAY, records=[activity, zero])

    assert (
        payload["agents"][SupportedAgent.CLAUDE_CODE.value]["source_status"]
        == SourceStatus.AVAILABLE_WITH_ACTIVITY.value
    )


def test_agent_with_no_records_that_day_is_reported_unavailable() -> None:
    payload = build_daily_record(device_id="device-abc", day=DAY, records=[])

    for agent in SupportedAgent:
        assert (
            payload["agents"][agent.value]["source_status"]
            == SourceStatus.SOURCE_UNAVAILABLE.value
        )


def test_token_totals_are_summed_correctly_per_agent() -> None:
    records = [
        _record(
            fingerprint="fp-1",
            session_fingerprint="s-1",
            tokens=TokenUsage(input_tokens=10, output_tokens=5, reasoning_tokens=1),
        ),
        _record(
            fingerprint="fp-2",
            session_fingerprint="s-2",
            tokens=TokenUsage(input_tokens=20, output_tokens=8, reasoning_tokens=2),
        ),
    ]

    payload = build_daily_record(device_id="device-abc", day=DAY, records=records)

    claude_code = payload["agents"][SupportedAgent.CLAUDE_CODE.value]
    assert claude_code["input_tokens"] == 30
    assert claude_code["output_tokens"] == 13
    assert claude_code["reasoning_tokens"] == 3
    assert claude_code["headline_total"] == 46


def test_session_count_counts_distinct_session_fingerprints_not_records() -> None:
    records = [
        _record(fingerprint="fp-1", session_fingerprint="same-session"),
        _record(fingerprint="fp-2", session_fingerprint="same-session"),
        _record(fingerprint="fp-3", session_fingerprint="other-session"),
    ]

    payload = build_daily_record(device_id="device-abc", day=DAY, records=records)

    assert payload["agents"][SupportedAgent.CLAUDE_CODE.value]["session_count"] == 2


def test_skill_and_mcp_names_are_sanitized_via_privacy_policy() -> None:
    records = [
        _record(
            fingerprint="fp-1",
            session_fingerprint="s-1",
            observed_skill_name="graphify",
            tokens=TokenUsage(),
            source_status=SourceStatus.AVAILABLE_WITH_ZERO_ACTIVITY,
        ),
        _record(
            fingerprint="fp-2",
            session_fingerprint="s-2",
            observed_skill_name="internal-api-token-dashboard",
            tokens=TokenUsage(),
            source_status=SourceStatus.AVAILABLE_WITH_ZERO_ACTIVITY,
        ),
    ]

    payload = build_daily_record(device_id="device-abc", day=DAY, records=records)

    assert payload["skills"]["graphify"] == 1
    assert payload["skills"]["(hidden)"] == 1
    assert "internal-api-token-dashboard" not in payload["skills"]


def test_user_privacy_overrides_are_applied() -> None:
    policy = PrivacyPolicy(block=frozenset({"graphify"}))
    records = [
        _record(
            fingerprint="fp-1",
            session_fingerprint="s-1",
            observed_skill_name="graphify",
            tokens=TokenUsage(),
            source_status=SourceStatus.AVAILABLE_WITH_ZERO_ACTIVITY,
        ),
    ]

    payload = build_daily_record(
        device_id="device-abc", day=DAY, records=records, privacy_policy=policy
    )

    assert "graphify" not in payload["skills"]
    assert payload["skills"]["(hidden)"] == 1


def test_mcp_server_and_tool_counters_are_tracked() -> None:
    records = [
        _record(
            fingerprint="fp-1",
            session_fingerprint="s-1",
            observed_mcp_server_name="synthetic_server",
            observed_mcp_tool_name="synthetic_tool",
            tokens=TokenUsage(),
            source_status=SourceStatus.AVAILABLE_WITH_ZERO_ACTIVITY,
        ),
    ]

    payload = build_daily_record(device_id="device-abc", day=DAY, records=records)

    assert payload["mcp_servers"]["synthetic_server"] == 1
    assert payload["mcp_tools"]["synthetic_server/synthetic_tool"] == 1


def test_model_token_totals_are_summed_from_headline_total() -> None:
    records = [
        _record(
            fingerprint="fp-1",
            session_fingerprint="s-1",
            model="claude-sonnet-5",
            tokens=TokenUsage(input_tokens=10, output_tokens=5, reasoning_tokens=1),
        ),
        _record(
            fingerprint="fp-2",
            session_fingerprint="s-2",
            model="claude-sonnet-5",
            tokens=TokenUsage(input_tokens=20, output_tokens=8, reasoning_tokens=2),
        ),
        _record(
            fingerprint="fp-3",
            session_fingerprint="s-3",
            model="claude-opus-5",
            tokens=TokenUsage(input_tokens=100, output_tokens=50),
        ),
    ]

    payload = build_daily_record(device_id="device-abc", day=DAY, records=records)

    assert payload["models"]["claude-sonnet-5"] == 46
    assert payload["models"]["claude-opus-5"] == 150


def test_records_without_a_model_are_excluded_from_the_models_counter() -> None:
    payload = build_daily_record(device_id="device-abc", day=DAY, records=[_record(model=None)])

    assert payload["models"] == {}


def test_model_counters_are_capped_with_a_stable_overflow_bucket() -> None:
    records = [
        _record(
            fingerprint=f"fp-{i}",
            session_fingerprint=f"s-{i}",
            model=f"model-{i}",
            tokens=TokenUsage(input_tokens=1),
        )
        for i in range(200)
    ]

    payload = build_daily_record(device_id="device-abc", day=DAY, records=records)

    assert len(payload["models"]) <= 51
    assert "(other)" in payload["models"]


def test_name_counters_are_capped_with_a_stable_overflow_bucket() -> None:
    records = [
        _record(
            fingerprint=f"fp-{i}",
            session_fingerprint=f"s-{i}",
            observed_skill_name=f"skill-{i}",
            tokens=TokenUsage(),
            source_status=SourceStatus.AVAILABLE_WITH_ZERO_ACTIVITY,
        )
        for i in range(200)
    ]

    payload = build_daily_record(device_id="device-abc", day=DAY, records=records)

    assert len(payload["skills"]) <= 51
    assert "(other)" in payload["skills"]


def test_build_daily_record_is_deterministic_regardless_of_input_order() -> None:
    records = [
        _record(fingerprint="fp-1", session_fingerprint="s-1", observed_skill_name="skill-a"),
        _record(fingerprint="fp-2", session_fingerprint="s-2", observed_skill_name="skill-b"),
    ]

    forward = build_daily_record(device_id="device-abc", day=DAY, records=records)
    reversed_order = build_daily_record(
        device_id="device-abc", day=DAY, records=list(reversed(records))
    )

    assert forward == reversed_order
    assert json.dumps(forward, sort_keys=True) == json.dumps(reversed_order, sort_keys=True)


def test_write_daily_record_creates_parent_directories_and_is_idempotent(tmp_path) -> None:
    path = tmp_path / "data" / "v1" / "devices" / "device-abc" / "2026-07-10.json"
    payload = build_daily_record(device_id="device-abc", day=DAY, records=[_record()])

    changed_first = write_daily_record(path, payload)
    content_after_first = path.read_text()
    changed_second = write_daily_record(path, payload)
    content_after_second = path.read_text()

    assert changed_first is True
    assert changed_second is False
    assert content_after_first == content_after_second


def test_private_data_cannot_cross_the_export_boundary() -> None:
    real_fingerprint = "super-secret-real-fingerprint"
    real_session_fingerprint = "super-secret-real-session-fingerprint"
    records = [
        _record(
            fingerprint=real_fingerprint,
            session_fingerprint=real_session_fingerprint,
            observed_skill_name="internal-secret-token-tool",
        ),
    ]

    payload = build_daily_record(device_id="device-abc", day=DAY, records=records)
    serialized = json.dumps(payload)

    assert real_fingerprint not in serialized
    assert real_session_fingerprint not in serialized
    assert "internal-secret-token-tool" not in serialized


def test_stage_daily_records_writes_one_file_per_day_with_data(tmp_path) -> None:
    records = [
        _record(fingerprint="fp-1", session_fingerprint="s-1"),
        _record(
            fingerprint="fp-2",
            session_fingerprint="s-2",
            occurred_at=datetime(2026, 7, 11, 12, 0, tzinfo=UTC),
        ),
    ]
    device_dir = tmp_path / "device-abc"

    payloads = stage_daily_records(device_dir, device_id="device-abc", records=records)

    assert {p["date"] for p in payloads} == {"2026-07-10", "2026-07-11"}
    assert (device_dir / "2026-07-10.json").exists()
    assert (device_dir / "2026-07-11.json").exists()


def test_stage_daily_records_is_idempotent(tmp_path) -> None:
    records = [_record()]
    device_dir = tmp_path / "device-abc"

    stage_daily_records(device_dir, device_id="device-abc", records=records)
    first_content = (device_dir / "2026-07-10.json").read_text(encoding="utf-8")
    stage_daily_records(device_dir, device_id="device-abc", records=records)
    second_content = (device_dir / "2026-07-10.json").read_text(encoding="utf-8")

    assert first_content == second_content


def test_stage_daily_records_returns_empty_list_for_no_records(tmp_path) -> None:
    device_dir = tmp_path / "device-abc"

    payloads = stage_daily_records(device_dir, device_id="device-abc", records=[])

    assert payloads == []
    assert not device_dir.exists()


def test_stage_daily_records_applies_the_privacy_policy(tmp_path) -> None:
    records = [
        _record(
            fingerprint="fp-1",
            session_fingerprint="s-1",
            observed_skill_name="internal-api-token-dashboard",
            tokens=TokenUsage(),
            source_status=SourceStatus.AVAILABLE_WITH_ZERO_ACTIVITY,
        ),
    ]
    device_dir = tmp_path / "device-abc"

    stage_daily_records(device_dir, device_id="device-abc", records=records)

    content = (device_dir / "2026-07-10.json").read_text(encoding="utf-8")
    assert "internal-api-token-dashboard" not in content
    assert "(hidden)" in content
