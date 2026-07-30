"""Read/write access to the private local usage ledger."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from tomax.ledger.schema import apply_schema
from tomax.models import NormalizedUsageRecord, SourceStatus, SupportedAgent, TokenUsage
from tomax.time_window import normalize_utc

_INSERT_EVENT_SQL = """
INSERT OR IGNORE INTO events (
    fingerprint, agent, occurred_at, session_fingerprint,
    input_tokens, output_tokens, reasoning_tokens,
    cache_read_tokens, cache_write_tokens,
    observed_skill_name, observed_mcp_server_name, observed_mcp_tool_name,
    source_status, schema_version
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

_CACHE_TOKEN_TOTALS_BY_AGENT_SQL = """
SELECT agent, SUM(cache_read_tokens) AS cache_read_tokens,
       SUM(cache_write_tokens) AS cache_write_tokens
FROM events
WHERE source_status != ?
GROUP BY agent
"""

_UPSERT_CHECKPOINT_SQL = """
INSERT INTO checkpoints (agent, last_collected_at) VALUES (?, ?)
ON CONFLICT(agent) DO UPDATE SET last_collected_at = excluded.last_collected_at
"""


def _record_to_row(record: NormalizedUsageRecord) -> tuple:
    tokens = record.tokens
    return (
        record.fingerprint,
        record.agent.value,
        record.occurred_at.isoformat(),
        record.session_fingerprint,
        tokens.input_tokens if tokens is not None else None,
        tokens.output_tokens if tokens is not None else None,
        tokens.reasoning_tokens if tokens is not None else None,
        tokens.cache_read_tokens if tokens is not None else None,
        tokens.cache_write_tokens if tokens is not None else None,
        record.observed_skill_name,
        record.observed_mcp_server_name,
        record.observed_mcp_tool_name,
        record.source_status.value,
        record.schema_version,
    )


def _row_to_record(row: sqlite3.Row) -> NormalizedUsageRecord:
    source_status = SourceStatus(row["source_status"])
    if source_status is SourceStatus.SOURCE_UNAVAILABLE:
        tokens = None
    else:
        tokens = TokenUsage(
            input_tokens=row["input_tokens"],
            output_tokens=row["output_tokens"],
            reasoning_tokens=row["reasoning_tokens"],
            # `or 0` covers rows written before cache-token columns existed.
            cache_read_tokens=row["cache_read_tokens"] or 0,
            cache_write_tokens=row["cache_write_tokens"] or 0,
        )
    return NormalizedUsageRecord(
        agent=SupportedAgent(row["agent"]),
        occurred_at=datetime.fromisoformat(row["occurred_at"]),
        fingerprint=row["fingerprint"],
        session_fingerprint=row["session_fingerprint"],
        tokens=tokens,
        observed_skill_name=row["observed_skill_name"],
        observed_mcp_server_name=row["observed_mcp_server_name"],
        observed_mcp_tool_name=row["observed_mcp_tool_name"],
        source_status=source_status,
        schema_version=row["schema_version"],
    )


class LedgerRepository:
    """Local SQLite-backed store for normalized usage records."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        connection.row_factory = sqlite3.Row
        self._connection = connection
        apply_schema(self._connection)

    @classmethod
    def open(cls, path: Path) -> "LedgerRepository":
        """Open (or create) the ledger database at the given path, read-write."""
        path.parent.mkdir(parents=True, exist_ok=True)
        return cls(sqlite3.connect(path))

    def close(self) -> None:
        self._connection.close()

    def insert_records(self, records: Iterable[NormalizedUsageRecord]) -> int:
        """Insert records, skipping ones whose fingerprint is already stored.

        Returns the number of newly inserted records, so repeat imports of the
        same records are observably idempotent.
        """
        inserted = 0
        with self._connection:
            for record in records:
                cursor = self._connection.execute(_INSERT_EVENT_SQL, _record_to_row(record))
                inserted += cursor.rowcount
        return inserted

    def list_records(
        self, agent: SupportedAgent | None = None
    ) -> list[NormalizedUsageRecord]:
        """Return stored records, optionally filtered to a single agent."""
        if agent is None:
            rows = self._connection.execute(
                "SELECT * FROM events ORDER BY occurred_at"
            ).fetchall()
        else:
            rows = self._connection.execute(
                "SELECT * FROM events WHERE agent = ? ORDER BY occurred_at",
                (agent.value,),
            ).fetchall()
        return [_row_to_record(row) for row in rows]

    def cache_token_totals_by_agent(self) -> dict[SupportedAgent, dict[str, int]]:
        """Sum cache-read and cache-write tokens per agent across the whole ledger.

        Every :class:`~tomax.models.SupportedAgent` is present in the result
        even with zero stored events, so callers never need an existence
        check. ``source_unavailable`` marker rows are excluded since they
        carry no token counts.
        """
        totals = {
            agent: {"cache_read_tokens": 0, "cache_write_tokens": 0} for agent in SupportedAgent
        }
        rows = self._connection.execute(
            _CACHE_TOKEN_TOTALS_BY_AGENT_SQL, (SourceStatus.SOURCE_UNAVAILABLE.value,)
        ).fetchall()
        for row in rows:
            totals[SupportedAgent(row["agent"])] = {
                "cache_read_tokens": row["cache_read_tokens"] or 0,
                "cache_write_tokens": row["cache_write_tokens"] or 0,
            }
        return totals

    def get_checkpoint(self, agent: SupportedAgent) -> datetime | None:
        """Return the last collected instant for an agent, or None if unset."""
        row = self._connection.execute(
            "SELECT last_collected_at FROM checkpoints WHERE agent = ?",
            (agent.value,),
        ).fetchone()
        if row is None:
            return None
        return normalize_utc(datetime.fromisoformat(row["last_collected_at"]))

    def get_earliest_record_at(self, agent: SupportedAgent) -> datetime | None:
        """Return the earliest stored record's timestamp for an agent, or None if it has none."""
        row = self._connection.execute(
            "SELECT MIN(occurred_at) AS earliest FROM events WHERE agent = ?",
            (agent.value,),
        ).fetchone()
        if row is None or row["earliest"] is None:
            return None
        return normalize_utc(datetime.fromisoformat(row["earliest"]))

    def set_checkpoint(self, agent: SupportedAgent, occurred_at: datetime) -> None:
        """Record the latest collected instant for an agent."""
        occurred_at_utc = normalize_utc(occurred_at)
        with self._connection:
            self._connection.execute(
                _UPSERT_CHECKPOINT_SQL, (agent.value, occurred_at_utc.isoformat())
            )

    def get_backfill_probed_start(self, agent: SupportedAgent) -> datetime | None:
        """Return the earliest start already fully backfill-probed for an agent, or None."""
        row = self._connection.execute(
            "SELECT probed_start FROM backfill_probes WHERE agent = ?",
            (agent.value,),
        ).fetchone()
        if row is None:
            return None
        return normalize_utc(datetime.fromisoformat(row["probed_start"]))

    def set_backfill_probed_start(self, agent: SupportedAgent, start: datetime) -> None:
        """Record that the backfill window down to ``start`` has been fully scanned."""
        start_utc = normalize_utc(start)
        with self._connection:
            self._connection.execute(
                "INSERT INTO backfill_probes (agent, probed_start) VALUES (?, ?) "
                "ON CONFLICT(agent) DO UPDATE SET probed_start = excluded.probed_start",
                (agent.value, start_utc.isoformat()),
            )

    def get_or_create_device_id(self) -> str:
        """Return this install's opaque device identifier, creating one if unset."""
        row = self._connection.execute(
            "SELECT device_id FROM device_identity WHERE id = 1"
        ).fetchone()
        if row is not None:
            return row["device_id"]

        device_id = str(uuid4())
        with self._connection:
            self._connection.execute(
                "INSERT INTO device_identity (id, device_id) VALUES (1, ?)",
                (device_id,),
            )
        return device_id
