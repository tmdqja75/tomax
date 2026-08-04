"""Tests for the private local usage ledger repository."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from tomax.ledger.repository import LedgerRepository
from tomax.models import NormalizedUsageRecord, SourceStatus, SupportedAgent, TokenUsage


UTC = timezone.utc


@pytest.fixture
def repository(tmp_path):
    repo = LedgerRepository.open(tmp_path / "ledger.sqlite3")
    yield repo
    repo.close()


def _record(
    fingerprint: str,
    *,
    agent: SupportedAgent = SupportedAgent.CLAUDE_CODE,
    occurred_at: datetime = datetime(2026, 7, 5, 12, 0, tzinfo=UTC),
    session_fingerprint: str | None = None,
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


def test_open_creates_missing_parent_directories(tmp_path) -> None:
    nested_path = tmp_path / "nested" / "subdir" / "ledger.sqlite3"

    repo = LedgerRepository.open(nested_path)
    try:
        assert nested_path.exists()
    finally:
        repo.close()


def test_schema_creates_expected_ledger_tables(tmp_path) -> None:
    repo = LedgerRepository.open(tmp_path / "ledger.sqlite3")
    try:
        table_names = {
            row[0]
            for row in repo._connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        assert {
            "events",
            "checkpoints",
            "daily_aggregates",
            "device_identity",
            "schema_migrations",
        } <= table_names
    finally:
        repo.close()


def test_opening_a_legacy_db_without_cache_columns_adds_them_in_place(tmp_path) -> None:
    db_path = tmp_path / "legacy.sqlite3"
    legacy_connection = sqlite3.connect(db_path)
    try:
        legacy_connection.execute(
            """
            CREATE TABLE events (
                fingerprint TEXT PRIMARY KEY,
                agent TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                session_fingerprint TEXT,
                input_tokens INTEGER,
                output_tokens INTEGER,
                reasoning_tokens INTEGER,
                observed_skill_name TEXT,
                observed_mcp_server_name TEXT,
                observed_mcp_tool_name TEXT,
                source_status TEXT NOT NULL,
                schema_version INTEGER NOT NULL
            )
            """
        )
        legacy_connection.execute(
            """
            INSERT INTO events (
                fingerprint, agent, occurred_at, input_tokens, output_tokens,
                reasoning_tokens, source_status, schema_version
            ) VALUES ('fp-legacy', 'codex', ?, 10, 5, 0, 'available_with_activity', 1)
            """,
            (datetime(2026, 7, 5, tzinfo=UTC).isoformat(),),
        )
        legacy_connection.commit()
    finally:
        legacy_connection.close()

    repo = LedgerRepository.open(db_path)
    try:
        columns = {row[1] for row in repo._connection.execute("PRAGMA table_info(events)")}
        assert {"cache_read_tokens", "cache_write_tokens"} <= columns

        [record] = repo.list_records()
        assert record.fingerprint == "fp-legacy"
        assert record.tokens.cache_read_tokens == 0
        assert record.tokens.cache_write_tokens == 0
        assert record.headline_total == 15
    finally:
        repo.close()


def test_insert_and_list_round_trips_a_normalized_record(repository) -> None:
    record = _record(
        "fingerprint-one",
        session_fingerprint="opaque-session-hash",
        observed_skill_name="safe-skill",
        observed_mcp_server_name="local-server",
        observed_mcp_tool_name="safe-tool",
    )

    inserted = repository.insert_records([record])

    assert inserted == 1
    stored = repository.list_records()
    assert stored == [record]
    assert stored[0].session_fingerprint == "opaque-session-hash"


def test_insert_and_list_round_trips_the_model(repository) -> None:
    record = _record("fingerprint-model", model="claude-sonnet-5")

    repository.insert_records([record])

    [stored] = repository.list_records()
    assert stored.model == "claude-sonnet-5"


def test_opening_a_legacy_db_without_the_model_column_adds_it_in_place(tmp_path) -> None:
    db_path = tmp_path / "legacy.sqlite3"
    legacy_connection = sqlite3.connect(db_path)
    try:
        legacy_connection.execute(
            """
            CREATE TABLE events (
                fingerprint TEXT PRIMARY KEY,
                agent TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                session_fingerprint TEXT,
                input_tokens INTEGER,
                output_tokens INTEGER,
                reasoning_tokens INTEGER,
                cache_read_tokens INTEGER,
                cache_write_tokens INTEGER,
                observed_skill_name TEXT,
                observed_mcp_server_name TEXT,
                observed_mcp_tool_name TEXT,
                source_status TEXT NOT NULL,
                schema_version INTEGER NOT NULL
            )
            """
        )
        legacy_connection.execute(
            """
            INSERT INTO events (
                fingerprint, agent, occurred_at, input_tokens, output_tokens,
                reasoning_tokens, source_status, schema_version
            ) VALUES ('fp-legacy', 'codex', ?, 10, 5, 0, 'available_with_activity', 1)
            """,
            (datetime(2026, 7, 5, tzinfo=UTC).isoformat(),),
        )
        legacy_connection.commit()
    finally:
        legacy_connection.close()

    repo = LedgerRepository.open(db_path)
    try:
        columns = {row[1] for row in repo._connection.execute("PRAGMA table_info(events)")}
        assert "model" in columns

        [record] = repo.list_records()
        assert record.model is None
    finally:
        repo.close()


def test_backfill_models_fills_only_rows_missing_a_model(repository) -> None:
    repository.insert_records(
        [
            _record("fp-no-model"),
            _record("fp-has-model", model="already-set"),
        ]
    )

    backfilled = repository.backfill_models(
        {"fp-no-model": "claude-sonnet-5", "fp-has-model": "should-not-overwrite"}
    )

    stored = {r.fingerprint: r.model for r in repository.list_records()}
    assert backfilled == 1
    assert stored["fp-no-model"] == "claude-sonnet-5"
    assert stored["fp-has-model"] == "already-set"


def test_backfill_models_ignores_unknown_fingerprints(repository) -> None:
    backfilled = repository.backfill_models({"fp-does-not-exist": "claude-sonnet-5"})

    assert backfilled == 0


def test_insert_and_list_round_trips_cache_tokens(repository) -> None:
    record = _record(
        "fingerprint-cache",
        tokens=TokenUsage(
            input_tokens=10,
            output_tokens=5,
            reasoning_tokens=1,
            cache_read_tokens=5000,
            cache_write_tokens=500,
        ),
    )

    repository.insert_records([record])

    [stored] = repository.list_records()
    assert stored.tokens.cache_read_tokens == 5000
    assert stored.tokens.cache_write_tokens == 500


def test_session_fingerprint_round_trips_as_none_when_unset(repository) -> None:
    record = _record("fingerprint-no-session")

    repository.insert_records([record])

    [stored] = repository.list_records()
    assert stored.session_fingerprint is None


def test_insert_preserves_source_unavailable_with_none_tokens(repository) -> None:
    record = _record(
        "fingerprint-unavailable",
        tokens=None,
        source_status=SourceStatus.SOURCE_UNAVAILABLE,
    )

    repository.insert_records([record])

    [stored] = repository.list_records()
    assert stored.source_status is SourceStatus.SOURCE_UNAVAILABLE
    assert stored.tokens is None


def test_insert_preserves_zero_activity_tokens(repository) -> None:
    record = _record(
        "fingerprint-zero",
        tokens=TokenUsage(),
        source_status=SourceStatus.AVAILABLE_WITH_ZERO_ACTIVITY,
    )

    repository.insert_records([record])

    [stored] = repository.list_records()
    assert stored.source_status is SourceStatus.AVAILABLE_WITH_ZERO_ACTIVITY
    assert stored.headline_total == 0


def test_duplicate_fingerprint_is_rejected_on_repeat_import(repository) -> None:
    record = _record("duplicate-fingerprint")

    first_pass = repository.insert_records([record])
    second_pass = repository.insert_records([record])

    assert first_pass == 1
    assert second_pass == 0
    assert len(repository.list_records()) == 1


def test_repeat_import_leaves_totals_unchanged(repository) -> None:
    records = [
        _record("fp-1", tokens=TokenUsage(input_tokens=10, output_tokens=5, reasoning_tokens=0)),
        _record("fp-2", tokens=TokenUsage(input_tokens=3, output_tokens=2, reasoning_tokens=1)),
    ]

    repository.insert_records(records)
    totals_after_first_import = sum(r.headline_total for r in repository.list_records())

    repository.insert_records(records)
    totals_after_repeat_import = sum(r.headline_total for r in repository.list_records())

    assert totals_after_first_import == 21
    assert totals_after_repeat_import == totals_after_first_import


def test_list_records_can_filter_by_agent(repository) -> None:
    repository.insert_records(
        [
            _record("fp-claude", agent=SupportedAgent.CLAUDE_CODE),
            _record("fp-codex", agent=SupportedAgent.CODEX),
        ]
    )

    claude_only = repository.list_records(agent=SupportedAgent.CLAUDE_CODE)

    assert [r.fingerprint for r in claude_only] == ["fp-claude"]


def test_checkpoint_round_trips_and_defaults_to_none(repository) -> None:
    assert repository.get_checkpoint(SupportedAgent.CODEX) is None

    repository.set_checkpoint(
        SupportedAgent.CODEX, datetime(2026, 7, 10, 8, 30, tzinfo=UTC)
    )

    assert repository.get_checkpoint(SupportedAgent.CODEX) == datetime(
        2026, 7, 10, 8, 30, tzinfo=UTC
    )


def test_checkpoint_update_overwrites_previous_value(repository) -> None:
    repository.set_checkpoint(
        SupportedAgent.HERMES_AGENT, datetime(2026, 7, 5, tzinfo=UTC)
    )
    repository.set_checkpoint(
        SupportedAgent.HERMES_AGENT, datetime(2026, 7, 12, tzinfo=UTC)
    )

    assert repository.get_checkpoint(SupportedAgent.HERMES_AGENT) == datetime(
        2026, 7, 12, tzinfo=UTC
    )


def test_checkpoint_is_independent_per_agent(repository) -> None:
    repository.set_checkpoint(
        SupportedAgent.CODEX, datetime(2026, 7, 6, tzinfo=UTC)
    )

    assert repository.get_checkpoint(SupportedAgent.HERMES_AGENT) is None


def test_get_earliest_record_at_returns_none_when_agent_has_no_records(tmp_path) -> None:
    repo = LedgerRepository.open(tmp_path / "ledger.sqlite3")
    try:
        assert repo.get_earliest_record_at(SupportedAgent.CLAUDE_CODE) is None
    finally:
        repo.close()


def test_get_earliest_record_at_returns_the_minimum_occurred_at_for_that_agent(tmp_path) -> None:
    repo = LedgerRepository.open(tmp_path / "ledger.sqlite3")
    try:
        repo.insert_records(
            [
                _record("a", occurred_at=datetime(2026, 7, 10, tzinfo=UTC)),
                _record("b", occurred_at=datetime(2026, 7, 5, tzinfo=UTC)),
                _record("c", occurred_at=datetime(2026, 7, 20, tzinfo=UTC)),
                _record(
                    "d",
                    agent=SupportedAgent.CODEX,
                    occurred_at=datetime(2026, 7, 1, tzinfo=UTC),
                ),
            ]
        )
        earliest = repo.get_earliest_record_at(SupportedAgent.CLAUDE_CODE)
    finally:
        repo.close()

    assert earliest == datetime(2026, 7, 5, tzinfo=UTC)


def test_get_backfill_probed_start_returns_none_when_never_set(tmp_path) -> None:
    repo = LedgerRepository.open(tmp_path / "ledger.sqlite3")
    try:
        assert repo.get_backfill_probed_start(SupportedAgent.CLAUDE_CODE) is None
    finally:
        repo.close()


def test_set_backfill_probed_start_persists_and_overwrites_per_agent(tmp_path) -> None:
    repo = LedgerRepository.open(tmp_path / "ledger.sqlite3")
    try:
        repo.set_backfill_probed_start(SupportedAgent.CLAUDE_CODE, datetime(2026, 7, 4, tzinfo=UTC))
        repo.set_backfill_probed_start(SupportedAgent.CODEX, datetime(2026, 1, 1, tzinfo=UTC))
        repo.set_backfill_probed_start(SupportedAgent.CLAUDE_CODE, datetime(2026, 1, 1, tzinfo=UTC))

        claude_probe = repo.get_backfill_probed_start(SupportedAgent.CLAUDE_CODE)
        codex_probe = repo.get_backfill_probed_start(SupportedAgent.CODEX)
    finally:
        repo.close()

    assert claude_probe == datetime(2026, 1, 1, tzinfo=UTC)
    assert codex_probe == datetime(2026, 1, 1, tzinfo=UTC)


def test_cache_token_totals_by_agent_sums_per_agent(repository) -> None:
    repository.insert_records(
        [
            _record(
                "fp-1",
                agent=SupportedAgent.CLAUDE_CODE,
                tokens=TokenUsage(input_tokens=1, cache_read_tokens=100, cache_write_tokens=10),
            ),
            _record(
                "fp-2",
                agent=SupportedAgent.CLAUDE_CODE,
                tokens=TokenUsage(input_tokens=1, cache_read_tokens=50, cache_write_tokens=5),
            ),
            _record(
                "fp-3",
                agent=SupportedAgent.CODEX,
                tokens=TokenUsage(input_tokens=1, cache_read_tokens=7, cache_write_tokens=0),
            ),
        ]
    )

    totals = repository.cache_token_totals_by_agent()

    assert totals[SupportedAgent.CLAUDE_CODE] == {"cache_read_tokens": 150, "cache_write_tokens": 15}
    assert totals[SupportedAgent.CODEX] == {"cache_read_tokens": 7, "cache_write_tokens": 0}
    assert totals[SupportedAgent.HERMES_AGENT] == {"cache_read_tokens": 0, "cache_write_tokens": 0}


def test_cache_token_totals_by_agent_ignores_source_unavailable_markers(repository) -> None:
    repository.insert_records(
        [
            _record(
                "fp-unavailable",
                agent=SupportedAgent.HERMES_AGENT,
                tokens=None,
                source_status=SourceStatus.SOURCE_UNAVAILABLE,
            ),
        ]
    )

    totals = repository.cache_token_totals_by_agent()

    assert totals[SupportedAgent.HERMES_AGENT] == {"cache_read_tokens": 0, "cache_write_tokens": 0}


def test_cache_token_totals_by_agent_returns_zero_for_empty_ledger(tmp_path) -> None:
    repo = LedgerRepository.open(tmp_path / "ledger.sqlite3")
    try:
        totals = repo.cache_token_totals_by_agent()
    finally:
        repo.close()

    assert totals == {
        SupportedAgent.HERMES_AGENT: {"cache_read_tokens": 0, "cache_write_tokens": 0},
        SupportedAgent.CLAUDE_CODE: {"cache_read_tokens": 0, "cache_write_tokens": 0},
        SupportedAgent.CODEX: {"cache_read_tokens": 0, "cache_write_tokens": 0},
    }
