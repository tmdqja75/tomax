"""Tests for privacy-safe normalized usage models."""

from __future__ import annotations

from dataclasses import fields
from datetime import datetime, timedelta, timezone

import pytest

from tomax.models import (
    NormalizedUsageRecord,
    SourceStatus,
    SupportedAgent,
    TokenUsage,
)


UTC = timezone.utc


def test_supported_agents_are_limited_to_the_first_release() -> None:
    assert {agent.value for agent in SupportedAgent} == {
        "hermes_agent",
        "claude_code",
        "codex",
    }


def test_normalized_record_rejects_an_unsupported_agent() -> None:
    with pytest.raises(ValueError, match="agent"):
        NormalizedUsageRecord(
            agent="another-agent",  # type: ignore[arg-type]
            occurred_at=datetime(2026, 7, 4, tzinfo=UTC),
            fingerprint="unsupported-agent",
            tokens=TokenUsage(input_tokens=1),
            source_status=SourceStatus.AVAILABLE_WITH_ACTIVITY,
        )


def test_token_usage_headline_excludes_cache_tokens() -> None:
    usage = TokenUsage(
        input_tokens=11,
        output_tokens=7,
        reasoning_tokens=3,
        cache_read_tokens=999_999,
        cache_write_tokens=999_999,
    )

    assert usage.headline_total == 21
    assert usage.cache_read_tokens == 999_999
    assert usage.cache_write_tokens == 999_999


@pytest.mark.parametrize(
    "field_name",
    ["input_tokens", "output_tokens", "reasoning_tokens", "cache_read_tokens", "cache_write_tokens"],
)
def test_token_usage_rejects_negative_fields(field_name: str) -> None:
    values = {
        "input_tokens": 1,
        "output_tokens": 2,
        "reasoning_tokens": 3,
        "cache_read_tokens": 4,
        "cache_write_tokens": 5,
    }
    values[field_name] = -1

    with pytest.raises(ValueError, match=field_name):
        TokenUsage(**values)


@pytest.mark.parametrize(
    "field_name",
    ["input_tokens", "output_tokens", "reasoning_tokens", "cache_read_tokens", "cache_write_tokens"],
)
@pytest.mark.parametrize("invalid_value", [1.5, True, False])
def test_token_usage_rejects_non_integer_fields(
    field_name: str, invalid_value: object
) -> None:
    values: dict[str, object] = {
        "input_tokens": 1,
        "output_tokens": 2,
        "reasoning_tokens": 3,
        "cache_read_tokens": 4,
        "cache_write_tokens": 5,
    }
    values[field_name] = invalid_value

    with pytest.raises(ValueError, match=f"{field_name} must be a non-negative integer"):
        TokenUsage(**values)  # type: ignore[arg-type]


def test_normalized_record_keeps_only_safe_metadata_and_normalizes_utc() -> None:
    record = NormalizedUsageRecord(
        agent=SupportedAgent.HERMES_AGENT,
        occurred_at=datetime(2026, 7, 4, 9, 30, tzinfo=timezone(timedelta(hours=9))),
        fingerprint="local-event-fingerprint",
        tokens=TokenUsage(input_tokens=10, output_tokens=20, reasoning_tokens=30),
        observed_skill_name="safe-skill",
        observed_mcp_server_name="local-server",
        observed_mcp_tool_name="safe-tool",
        source_status=SourceStatus.AVAILABLE_WITH_ACTIVITY,
    )

    assert record.occurred_at == datetime(2026, 7, 4, 0, 30, tzinfo=UTC)
    assert record.headline_total == 60
    assert record.schema_version == 1
    assert {field.name for field in fields(NormalizedUsageRecord)} == {
        "agent",
        "occurred_at",
        "fingerprint",
        "session_fingerprint",
        "model",
        "tokens",
        "observed_skill_name",
        "observed_mcp_server_name",
        "observed_mcp_tool_name",
        "source_status",
        "schema_version",
    }
    protected_names = {"prompt", "path", "arguments"}
    assert not protected_names & {field.name for field in fields(NormalizedUsageRecord)}


def test_session_fingerprint_defaults_to_none_and_can_be_set() -> None:
    without_session = NormalizedUsageRecord(
        agent=SupportedAgent.CODEX,
        occurred_at=datetime(2026, 7, 4, tzinfo=UTC),
        fingerprint="marker-fingerprint",
        tokens=None,
        source_status=SourceStatus.SOURCE_UNAVAILABLE,
    )
    with_session = NormalizedUsageRecord(
        agent=SupportedAgent.CODEX,
        occurred_at=datetime(2026, 7, 4, tzinfo=UTC),
        fingerprint="event-fingerprint",
        session_fingerprint="opaque-session-hash",
        tokens=TokenUsage(input_tokens=1),
        source_status=SourceStatus.AVAILABLE_WITH_ACTIVITY,
    )

    assert without_session.session_fingerprint is None
    assert with_session.session_fingerprint == "opaque-session-hash"


@pytest.mark.parametrize("fingerprint", ["", "   "])
def test_normalized_record_rejects_empty_fingerprint(fingerprint: str) -> None:
    with pytest.raises(ValueError, match="fingerprint"):
        NormalizedUsageRecord(
            agent=SupportedAgent.CODEX,
            occurred_at=datetime(2026, 7, 4, tzinfo=UTC),
            fingerprint=fingerprint,
            tokens=TokenUsage(),
            source_status=SourceStatus.AVAILABLE_WITH_ZERO_ACTIVITY,
        )


def test_source_statuses_distinguish_unavailable_from_observed_zero_activity() -> None:
    zero_activity = NormalizedUsageRecord(
        agent=SupportedAgent.CLAUDE_CODE,
        occurred_at=datetime(2026, 7, 4, tzinfo=UTC),
        fingerprint="zero-activity",
        tokens=TokenUsage(),
        source_status=SourceStatus.AVAILABLE_WITH_ZERO_ACTIVITY,
    )
    unavailable = NormalizedUsageRecord(
        agent=SupportedAgent.CLAUDE_CODE,
        occurred_at=datetime(2026, 7, 4, tzinfo=UTC),
        fingerprint="unavailable",
        tokens=None,
        source_status=SourceStatus.SOURCE_UNAVAILABLE,
    )

    assert zero_activity.source_status is SourceStatus.AVAILABLE_WITH_ZERO_ACTIVITY
    assert zero_activity.headline_total == 0
    assert unavailable.source_status is SourceStatus.SOURCE_UNAVAILABLE
    assert unavailable.tokens is None
    assert unavailable.headline_total is None


@pytest.mark.parametrize(
    ("status", "tokens"),
    [
        (SourceStatus.AVAILABLE_WITH_ACTIVITY, TokenUsage()),
        (SourceStatus.AVAILABLE_WITH_ZERO_ACTIVITY, TokenUsage(input_tokens=1)),
        (SourceStatus.SOURCE_UNAVAILABLE, TokenUsage()),
    ],
)
def test_source_status_requires_a_matching_token_state(
    status: SourceStatus, tokens: TokenUsage | None
) -> None:
    with pytest.raises(ValueError, match="source_status"):
        NormalizedUsageRecord(
            agent=SupportedAgent.CODEX,
            occurred_at=datetime(2026, 7, 4, tzinfo=UTC),
            fingerprint="status-validation",
            tokens=tokens,
            source_status=status,
        )


def test_normalized_record_rejects_naive_occurrence_timestamp() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        NormalizedUsageRecord(
            agent=SupportedAgent.CODEX,
            occurred_at=datetime(2026, 7, 4),
            fingerprint="naive-time",
            tokens=TokenUsage(input_tokens=1),
            source_status=SourceStatus.AVAILABLE_WITH_ACTIVITY,
        )
