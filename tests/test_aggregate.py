"""Tests for multi-device aggregation and validation of public daily records."""

from __future__ import annotations

import hashlib
import json
from datetime import date, timedelta

from tomax.aggregate import (
    MAX_PAYLOAD_BYTES,
    agent_available_date_range,
    aggregate_records,
    daily_agent_totals,
    daily_counter_totals,
    daily_token_totals,
    daily_totals,
    monthly_totals,
    rolling_window,
    validate_and_partition,
    validate_record,
)
from tomax.models import SourceStatus, SupportedAgent
from tomax.public_data import build_daily_record

TODAY = date(2026, 7, 18)


def _recompute_checksum(payload: dict) -> str:
    """Mirror public_data's checksum formula for tests that mutate a payload.

    Simulates a self-consistent-but-invalid payload (as a bad actor or a
    buggy producer might create), so a test can exercise one specific
    validator without checksum validation masking it first.
    """
    without_checksum = {key: value for key, value in payload.items() if key != "checksum"}
    canonical = json.dumps(without_checksum, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _payload(
    *,
    device_id: str = "device-a",
    day: date = date(2026, 7, 10),
    input_tokens: int = 10,
    output_tokens: int = 5,
    reasoning_tokens: int = 1,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    session_count: int = 1,
    status: SourceStatus = SourceStatus.AVAILABLE_WITH_ACTIVITY,
    skills: dict | None = None,
    model: str | None = None,
    agent: SupportedAgent = SupportedAgent.CLAUDE_CODE,
) -> dict:
    from tomax.models import NormalizedUsageRecord, TokenUsage
    from datetime import datetime, timezone

    record = NormalizedUsageRecord(
        agent=agent,
        occurred_at=datetime(day.year, day.month, day.day, 12, 0, tzinfo=timezone.utc),
        fingerprint=f"fp-{device_id}-{day.isoformat()}",
        session_fingerprint=f"session-{device_id}-{day.isoformat()}",
        model=model,
        tokens=TokenUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            reasoning_tokens=reasoning_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_write_tokens=cache_write_tokens,
        )
        if status is not SourceStatus.SOURCE_UNAVAILABLE
        else None,
        observed_skill_name=next(iter(skills), None) if skills else None,
        source_status=status,
    )
    return build_daily_record(device_id=device_id, day=day, records=[record])


def test_validate_record_accepts_a_well_formed_payload() -> None:
    payload = _payload()

    assert validate_record("device-a", payload, today=TODAY) is None


def test_validate_record_rejects_wrong_schema_version() -> None:
    payload = _payload()
    payload = dict(payload)
    payload["schema_version"] = 999

    issue = validate_record("device-a", payload, today=TODAY)

    assert issue is not None
    assert "schema_version" in issue.reason


def test_validate_record_rejects_checksum_mismatch() -> None:
    payload = dict(_payload())
    payload["agents"] = dict(payload["agents"])
    payload["agents"][SupportedAgent.CLAUDE_CODE.value] = dict(
        payload["agents"][SupportedAgent.CLAUDE_CODE.value]
    )
    payload["agents"][SupportedAgent.CLAUDE_CODE.value]["input_tokens"] += 500

    issue = validate_record("device-a", payload, today=TODAY)

    assert issue is not None
    assert "checksum" in issue.reason


def test_validate_record_rejects_missing_or_malformed_date() -> None:
    payload = dict(_payload())
    payload["date"] = "not-a-date"
    payload["checksum"] = _recompute_checksum(payload)

    issue = validate_record("device-a", payload, today=TODAY)

    assert issue is not None
    assert "date" in issue.reason


def test_validate_record_rejects_a_future_dated_record() -> None:
    payload = _payload(day=date(2026, 7, 20))

    issue = validate_record("device-a", payload, today=TODAY)

    assert issue is not None
    assert "future" in issue.reason


def test_validate_record_rejects_device_id_mismatch() -> None:
    payload = _payload(device_id="device-a")

    issue = validate_record("device-b", payload, today=TODAY)

    assert issue is not None
    assert "device_id" in issue.reason


def test_validate_record_rejects_an_unknown_agent_name() -> None:
    payload = dict(_payload())
    payload["agents"] = dict(payload["agents"])
    payload["agents"]["unknown_agent"] = payload["agents"][SupportedAgent.CLAUDE_CODE.value]
    payload["checksum"] = _recompute_checksum(payload)

    issue = validate_record("device-a", payload, today=TODAY)

    assert issue is not None
    assert "agent" in issue.reason


def test_validate_record_rejects_negative_totals() -> None:
    payload = dict(_payload())
    payload["agents"] = dict(payload["agents"])
    payload["agents"][SupportedAgent.CLAUDE_CODE.value] = dict(
        payload["agents"][SupportedAgent.CLAUDE_CODE.value]
    )
    payload["agents"][SupportedAgent.CLAUDE_CODE.value]["input_tokens"] = -1
    payload["checksum"] = _recompute_checksum(payload)

    issue = validate_record("device-a", payload, today=TODAY)

    assert issue is not None
    assert "non-negative" in issue.reason


def test_validate_record_rejects_an_oversized_payload() -> None:
    payload = dict(_payload())
    payload["skills"] = dict(payload["skills"])
    for i in range(1000):
        payload["skills"][f"padding-name-{i}-{'x' * 200}"] = 1

    issue = validate_record("device-a", payload, today=TODAY)

    assert issue is not None
    assert "size" in issue.reason.lower()


def test_max_payload_bytes_is_a_sane_bound() -> None:
    assert 1_000 < MAX_PAYLOAD_BYTES <= 1_000_000


def test_validate_and_partition_separates_valid_from_invalid_with_diagnostics() -> None:
    valid_payload = _payload(device_id="device-a")
    invalid_payload = dict(_payload(device_id="device-b"))
    invalid_payload["schema_version"] = 999

    result = validate_and_partition(
        [("device-a", valid_payload), ("device-b", invalid_payload)], today=TODAY
    )

    assert result.valid_payloads == [valid_payload]
    assert len(result.issues) == 1
    assert result.issues[0].device_id == "device-b"


def test_aggregate_records_sums_token_totals_across_devices() -> None:
    payloads = [
        _payload(device_id="device-a", input_tokens=10, output_tokens=5, reasoning_tokens=1),
        _payload(device_id="device-b", input_tokens=20, output_tokens=8, reasoning_tokens=2),
    ]

    summary = aggregate_records(payloads)

    claude_code = summary["agents"][SupportedAgent.CLAUDE_CODE.value]
    assert claude_code["input_tokens"] == 30
    assert claude_code["output_tokens"] == 13
    assert claude_code["reasoning_tokens"] == 3
    assert claude_code["headline_total"] == 46


def test_aggregate_records_counts_distinct_devices() -> None:
    payloads = [
        _payload(device_id="device-a"),
        _payload(device_id="device-b"),
        _payload(device_id="device-a", day=date(2026, 7, 11)),
    ]

    summary = aggregate_records(payloads)

    assert summary["distinct_devices"] == 2


def test_aggregate_records_deduplicates_active_days_across_devices_for_the_same_agent() -> None:
    same_day = date(2026, 7, 10)
    payloads = [
        _payload(device_id="device-a", day=same_day),
        _payload(device_id="device-b", day=same_day),
    ]

    summary = aggregate_records(payloads)

    assert summary["agents"][SupportedAgent.CLAUDE_CODE.value]["active_days"] == 1
    assert summary["active_days"] == 1


def test_aggregate_records_picks_best_status_across_devices_for_the_same_agent_day() -> None:
    same_day = date(2026, 7, 10)
    payloads = [
        _payload(
            device_id="device-a",
            day=same_day,
            status=SourceStatus.SOURCE_UNAVAILABLE,
            input_tokens=0,
            output_tokens=0,
            reasoning_tokens=0,
        ),
        _payload(device_id="device-b", day=same_day, status=SourceStatus.AVAILABLE_WITH_ACTIVITY),
    ]

    summary = aggregate_records(payloads)

    assert (
        summary["agents"][SupportedAgent.CLAUDE_CODE.value]["source_status"]
        == SourceStatus.AVAILABLE_WITH_ACTIVITY.value
    )


def test_aggregate_records_sums_skill_and_mcp_counters_across_devices() -> None:
    payloads = [
        _payload(device_id="device-a", skills={"graphify": 1}),
        _payload(device_id="device-b", skills={"graphify": 1}),
    ]

    summary = aggregate_records(payloads)

    assert summary["skills"]["graphify"] == 2


def test_aggregate_records_sums_model_counters_across_devices() -> None:
    payloads = [
        _payload(device_id="device-a", model="claude-sonnet-5", input_tokens=10, output_tokens=5),
        _payload(device_id="device-b", model="claude-sonnet-5", input_tokens=20, output_tokens=8),
    ]

    summary = aggregate_records(payloads)

    assert summary["models"]["claude-sonnet-5"] == 45


def test_daily_counter_totals_sums_the_models_field_per_day() -> None:
    payloads = [
        _payload(device_id="device-a", day=date(2026, 7, 10), model="claude-sonnet-5"),
        _payload(device_id="device-b", day=date(2026, 7, 10), model="claude-sonnet-5"),
        _payload(device_id="device-a", day=date(2026, 7, 11), model="claude-opus-5"),
    ]

    totals = daily_counter_totals(payloads, "models")

    assert totals["2026-07-10"]["claude-sonnet-5"] == 32
    assert totals["2026-07-11"]["claude-opus-5"] == 16


def test_rolling_window_selects_only_the_last_n_days() -> None:
    payloads = [
        _payload(device_id="device-a", day=TODAY - timedelta(days=1)),
        _payload(device_id="device-a", day=TODAY - timedelta(days=13)),
        _payload(device_id="device-a", day=TODAY - timedelta(days=14)),
        _payload(device_id="device-a", day=TODAY - timedelta(days=30)),
    ]

    selected = rolling_window(payloads, end=TODAY, days=14)

    selected_dates = {payload["date"] for payload in selected}
    assert (TODAY - timedelta(days=1)).isoformat() in selected_dates
    assert (TODAY - timedelta(days=13)).isoformat() in selected_dates
    assert (TODAY - timedelta(days=14)).isoformat() not in selected_dates
    assert (TODAY - timedelta(days=30)).isoformat() not in selected_dates


def test_agent_available_date_range_finds_first_and_last_available_day() -> None:
    payloads = [
        _payload(day=date(2026, 7, 1), agent=SupportedAgent.CODEX),
        _payload(
            day=date(2026, 7, 5),
            agent=SupportedAgent.CLAUDE_CODE,
            status=SourceStatus.AVAILABLE_WITH_ACTIVITY,
        ),
        _payload(
            day=date(2026, 7, 10),
            agent=SupportedAgent.CLAUDE_CODE,
            status=SourceStatus.AVAILABLE_WITH_ZERO_ACTIVITY,
            input_tokens=0,
            output_tokens=0,
            reasoning_tokens=0,
        ),
        _payload(day=date(2026, 7, 15), agent=SupportedAgent.CODEX),
    ]

    result = agent_available_date_range(payloads, SupportedAgent.CLAUDE_CODE.value)

    assert result == (date(2026, 7, 5), date(2026, 7, 10))


def test_agent_available_date_range_returns_none_when_never_available() -> None:
    payloads = [_payload(day=date(2026, 7, 1), agent=SupportedAgent.CODEX)]

    assert agent_available_date_range(payloads, SupportedAgent.CLAUDE_CODE.value) is None


def test_lifetime_aggregation_includes_all_valid_days() -> None:
    payloads = [
        _payload(device_id="device-a", day=date(2026, 6, 1)),
        _payload(device_id="device-a", day=date(2026, 7, 15)),
    ]

    summary = aggregate_records(payloads)

    assert summary["agents"][SupportedAgent.CLAUDE_CODE.value]["active_days"] == 2


def test_aggregate_records_handles_an_empty_payload_list() -> None:
    summary = aggregate_records([])

    assert summary["distinct_devices"] == 0
    assert summary["active_days"] == 0
    for agent in SupportedAgent:
        agent_summary = summary["agents"][agent.value]
        assert agent_summary["source_status"] == SourceStatus.SOURCE_UNAVAILABLE.value
        assert agent_summary["input_tokens"] == 0
        assert agent_summary["active_days"] == 0


def test_a_maximally_capped_daily_record_still_passes_validation() -> None:
    from tomax.models import NormalizedUsageRecord, TokenUsage
    from datetime import datetime, timezone

    records = [
        NormalizedUsageRecord(
            agent=SupportedAgent.CLAUDE_CODE,
            occurred_at=datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc),
            fingerprint=f"fp-{i}",
            session_fingerprint=f"s-{i}",
            tokens=TokenUsage(),
            observed_skill_name=f"skill-{i}",
            source_status=SourceStatus.AVAILABLE_WITH_ZERO_ACTIVITY,
        )
        for i in range(200)
    ]
    payload = build_daily_record(device_id="device-a", day=date(2026, 7, 10), records=records)

    issue = validate_record("device-a", payload, today=TODAY)

    assert issue is None


def test_daily_totals_sums_headline_total_across_agents_and_devices() -> None:
    payloads = [
        _payload(
            device_id="device-a",
            day=date(2026, 7, 10),
            input_tokens=10,
            output_tokens=5,
            reasoning_tokens=0,
        ),
        _payload(
            device_id="device-b",
            day=date(2026, 7, 10),
            input_tokens=20,
            output_tokens=8,
            reasoning_tokens=0,
        ),
        _payload(
            device_id="device-a",
            day=date(2026, 7, 11),
            input_tokens=1,
            output_tokens=1,
            reasoning_tokens=0,
        ),
    ]

    totals = daily_totals(payloads)

    assert totals["2026-07-10"] == 15 + 28
    assert totals["2026-07-11"] == 2


def test_daily_totals_excluding_cache_subtracts_codex_cache_read_from_headline() -> None:
    # Codex's headline_total (input + output + reasoning) already has its
    # cache_read_tokens baked into input_tokens, unlike Claude Code/Hermes
    # where cache reads are reported separately. Excluding cache tokens must
    # therefore subtract them back out for Codex, not leave the total
    # unchanged -- otherwise --exclude-cache-tokens is a no-op for Codex.
    payloads = [
        _payload(
            device_id="device-a",
            day=date(2026, 7, 10),
            input_tokens=110,
            output_tokens=5,
            reasoning_tokens=1,
            cache_read_tokens=100,
            agent=SupportedAgent.CODEX,
        ),
    ]

    with_cache = daily_totals(payloads, include_cache_tokens=True)
    without_cache = daily_totals(payloads, include_cache_tokens=False)

    assert with_cache["2026-07-10"] == 116
    assert without_cache["2026-07-10"] == 16


def test_daily_agent_totals_groups_per_agent_and_sums_devices() -> None:
    payloads = [
        _payload(
            device_id="device-a",
            day=date(2026, 7, 10),
            agent=SupportedAgent.CLAUDE_CODE,
            input_tokens=10,
            output_tokens=5,
            reasoning_tokens=0,
        ),
        _payload(
            device_id="device-b",
            day=date(2026, 7, 10),
            agent=SupportedAgent.CLAUDE_CODE,
            input_tokens=20,
            output_tokens=8,
            reasoning_tokens=0,
        ),
        _payload(
            device_id="device-a",
            day=date(2026, 7, 10),
            agent=SupportedAgent.CODEX,
            input_tokens=1,
            output_tokens=1,
            reasoning_tokens=0,
        ),
    ]

    totals = daily_agent_totals(payloads)

    assert totals["2026-07-10"] == {"claude_code": 15 + 28, "codex": 2}


def test_daily_agent_totals_omits_zero_agents() -> None:
    payloads = [
        _payload(
            device_id="device-a",
            day=date(2026, 7, 10),
            agent=SupportedAgent.CLAUDE_CODE,
            input_tokens=0,
            output_tokens=0,
            reasoning_tokens=0,
            status=SourceStatus.AVAILABLE_WITH_ZERO_ACTIVITY,
        ),
    ]

    totals = daily_agent_totals(payloads)

    assert totals["2026-07-10"] == {}


def test_daily_totals_omits_days_with_no_payload_at_all() -> None:
    payloads = [_payload(device_id="device-a", day=date(2026, 7, 10))]

    totals = daily_totals(payloads)

    assert "2026-07-11" not in totals
    assert len(totals) == 1


def test_daily_token_totals_groups_token_types_and_preserves_unknown_days() -> None:
    payloads = [
        _payload(
            device_id="device-a",
            day=date(2026, 7, 10),
            input_tokens=10,
            output_tokens=5,
            reasoning_tokens=1,
        ),
        _payload(
            device_id="device-b",
            day=date(2026, 7, 10),
            agent=SupportedAgent.HERMES_AGENT,
            input_tokens=20,
            output_tokens=4,
            reasoning_tokens=2,
        ),
        _payload(
            device_id="device-a",
            day=date(2026, 7, 11),
            status=SourceStatus.SOURCE_UNAVAILABLE,
        ),
    ]

    totals = daily_token_totals(payloads)

    assert totals["2026-07-10"] == {"input": 30, "output": 9, "reasoning": 3, "cache": 0}
    assert totals["2026-07-11"] is None


def test_daily_token_totals_keeps_cache_tokens_in_their_own_bucket() -> None:
    payloads = [
        _payload(
            device_id="device-a",
            input_tokens=10,
            output_tokens=5,
            reasoning_tokens=1,
            cache_read_tokens=100,
            cache_write_tokens=20,
            agent=SupportedAgent.CLAUDE_CODE,
        ),
    ]

    totals = daily_token_totals(payloads)

    assert totals["2026-07-10"] == {"input": 10, "output": 5, "reasoning": 1, "cache": 120}


def test_daily_token_totals_splits_codex_cache_read_out_of_input() -> None:
    # Codex's input_tokens already includes cache_read_tokens as a subset (110
    # total input, 100 of which were cache reads) -- the cache bucket must
    # pull that 100 back out of "input" rather than showing it in both places
    # or dropping it from the chart entirely.
    payloads = [
        _payload(
            device_id="device-a",
            input_tokens=110,
            output_tokens=5,
            reasoning_tokens=1,
            cache_read_tokens=100,
            cache_write_tokens=0,
            agent=SupportedAgent.CODEX,
        ),
    ]

    totals = daily_token_totals(payloads)

    assert totals["2026-07-10"] == {"input": 10, "output": 5, "reasoning": 1, "cache": 100}


def test_daily_token_totals_cache_bucket_is_zero_when_excluded() -> None:
    payloads = [
        _payload(
            device_id="device-a",
            input_tokens=10,
            output_tokens=5,
            reasoning_tokens=1,
            cache_read_tokens=100,
            cache_write_tokens=20,
            agent=SupportedAgent.CLAUDE_CODE,
        ),
    ]

    totals = daily_token_totals(payloads, include_cache_tokens=False)

    assert totals["2026-07-10"] == {"input": 10, "output": 5, "reasoning": 1, "cache": 0}


def test_monthly_totals_sums_headline_total_by_calendar_month() -> None:
    payloads = [
        _payload(
            device_id="device-a",
            day=date(2026, 6, 1),
            input_tokens=10,
            output_tokens=0,
            reasoning_tokens=0,
        ),
        _payload(
            device_id="device-a",
            day=date(2026, 6, 15),
            input_tokens=5,
            output_tokens=0,
            reasoning_tokens=0,
        ),
        _payload(
            device_id="device-a",
            day=date(2026, 7, 1),
            input_tokens=7,
            output_tokens=0,
            reasoning_tokens=0,
        ),
    ]

    totals = monthly_totals(payloads)

    assert totals["2026-06"] == 15
    assert totals["2026-07"] == 7
