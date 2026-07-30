"""SQLite table definitions for the private local usage ledger."""

from __future__ import annotations

import sqlite3

SCHEMA_VERSION = 2

_EVENTS_CACHE_TOKEN_COLUMNS = ("cache_read_tokens", "cache_write_tokens")

_CREATE_EVENTS = """
CREATE TABLE IF NOT EXISTS events (
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

_CREATE_CHECKPOINTS = """
CREATE TABLE IF NOT EXISTS checkpoints (
    agent TEXT PRIMARY KEY,
    last_collected_at TEXT NOT NULL
)
"""

_CREATE_DAILY_AGGREGATES = """
CREATE TABLE IF NOT EXISTS daily_aggregates (
    day TEXT NOT NULL,
    agent TEXT NOT NULL,
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    reasoning_tokens INTEGER NOT NULL,
    session_count INTEGER NOT NULL,
    PRIMARY KEY (day, agent)
)
"""

_CREATE_BACKFILL_PROBES = """
CREATE TABLE IF NOT EXISTS backfill_probes (
    agent TEXT PRIMARY KEY,
    probed_start TEXT NOT NULL
)
"""

_CREATE_DEVICE_IDENTITY = """
CREATE TABLE IF NOT EXISTS device_identity (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    device_id TEXT NOT NULL
)
"""

_CREATE_SCHEMA_MIGRATIONS = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY
)
"""


def _add_missing_events_columns(connection: sqlite3.Connection) -> None:
    """Add ``events`` columns introduced after a DB's original creation.

    ``CREATE TABLE IF NOT EXISTS`` never alters an already-existing table,
    so a ledger created before cache-token tracking was added needs its
    ``events`` table patched in place rather than recreated, to keep every
    already-collected row.
    """
    existing_columns = {row[1] for row in connection.execute("PRAGMA table_info(events)")}
    for column in _EVENTS_CACHE_TOKEN_COLUMNS:
        if column not in existing_columns:
            connection.execute(f"ALTER TABLE events ADD COLUMN {column} INTEGER")


def apply_schema(connection: sqlite3.Connection) -> None:
    """Create the ledger tables if they do not already exist."""
    with connection:
        connection.execute(_CREATE_EVENTS)
        connection.execute(_CREATE_CHECKPOINTS)
        connection.execute(_CREATE_DAILY_AGGREGATES)
        connection.execute(_CREATE_BACKFILL_PROBES)
        connection.execute(_CREATE_DEVICE_IDENTITY)
        connection.execute(_CREATE_SCHEMA_MIGRATIONS)
        _add_missing_events_columns(connection)
        connection.execute(
            "INSERT OR IGNORE INTO schema_migrations (version) VALUES (?)",
            (SCHEMA_VERSION,),
        )
