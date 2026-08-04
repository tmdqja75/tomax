"""Pull local agent usage into the private ledger.

Each agent's collection window runs from its last checkpoint up to
``now``, or — on an agent's first-ever collection — from the configured
initial start (``config.initial_collection_start``, default 2026-07-04,
resolved via ``resolve_initial_collection_start``) up to ``now``.

If the configured start predates an agent's earliest already-collected
record, an additional backfill window covers that gap — appending older
history without moving the forward checkpoint. A per-agent probe marker
(``LedgerRepository.get/set_backfill_probed_start``) records how far a
backfill has already scanned, so once a run confirms there is nothing
earlier than the configured start (including implicitly, via a
first-ever run's forward window already starting there), later runs
with the same configured start don't rescan it. ``now`` must always be
supplied by the caller rather than
computed here, the same explicit-time convention used throughout this
project (see e.g. ``render_dashboard``'s ``today``/``generated_at``), so
collection stays deterministic and testable.

``backfill_all_models``/``backfill_agent_models`` are a separate, narrower
entry point: they patch the ``model`` column into rows that were already
ledgered before model tracking existed, by re-reading full source history
and matching on fingerprint. They never insert new rows or move
checkpoints — a plain re-``collect`` never revisits already-ledgered
fingerprints (``INSERT OR IGNORE``), so this is the only path that fills
``model`` in for historical rows.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from tomax.adapters import claude_code, codex, hermes
from tomax.ledger.repository import LedgerRepository
from tomax.models import NormalizedUsageRecord, SourceStatus, SupportedAgent
from tomax.time_window import DEFAULT_INITIAL_START, TimeWindow

DEFAULT_HERMES_STATE_DB = Path.home() / ".hermes" / "state.db"
DEFAULT_CLAUDE_CODE_PROJECTS_DIR = Path.home() / ".claude" / "projects"
DEFAULT_CODEX_SESSIONS_DIR = Path.home() / ".codex" / "sessions"

AdapterCollect = Callable[[Path, TimeWindow], list[NormalizedUsageRecord]]

ADAPTER_COLLECTORS: tuple[tuple[SupportedAgent, AdapterCollect], ...] = (
    (SupportedAgent.HERMES_AGENT, hermes.collect),
    (SupportedAgent.CLAUDE_CODE, claude_code.collect),
    (SupportedAgent.CODEX, codex.collect),
)

_STATUS_PRECEDENCE = (
    SourceStatus.AVAILABLE_WITH_ACTIVITY,
    SourceStatus.AVAILABLE_WITH_ZERO_ACTIVITY,
    SourceStatus.SOURCE_UNAVAILABLE,
)


@dataclass(frozen=True, slots=True)
class AgentBackfillResult:
    """The outcome of patching ``model`` into one agent's already-ledgered rows."""

    agent: SupportedAgent
    records_scanned: int
    records_backfilled: int


@dataclass(frozen=True, slots=True)
class AgentCollectionResult:
    """The outcome of collecting one agent's source for one run.

    ``status`` is ``None`` when there was nothing new to collect this run
    (the agent's checkpoint had already caught up to ``now``) — distinct
    from the three ``SourceStatus`` values, which describe an
    actually-probed source.
    """

    agent: SupportedAgent
    status: SourceStatus | None
    records_observed: int
    records_inserted: int


def overall_source_status(records: list[NormalizedUsageRecord]) -> SourceStatus:
    """The most-informative status across a batch of collected records."""
    statuses = {record.source_status for record in records}
    for status in _STATUS_PRECEDENCE:
        if status in statuses:
            return status
    return SourceStatus.SOURCE_UNAVAILABLE


def collection_window(
    agent: SupportedAgent,
    repository: LedgerRepository,
    *,
    now: datetime,
    configured_start: datetime = DEFAULT_INITIAL_START,
) -> TimeWindow | None:
    """The forward window to collect for one agent.

    Since the agent's last checkpoint, or from ``configured_start`` through
    ``now`` on an agent's first-ever collection — no fixed cap, so a widened
    ``configured_start`` always reaches all the way to ``now`` on a first
    run. Returns None if there is nothing new to collect (the window would
    be empty).
    """
    checkpoint = repository.get_checkpoint(agent)
    start = checkpoint if checkpoint is not None else configured_start
    end = now

    if start >= end:
        return None
    return TimeWindow(start=start, end=end)


def backfill_window(
    agent: SupportedAgent, repository: LedgerRepository, *, configured_start: datetime
) -> TimeWindow | None:
    """The gap window to backfill when ``configured_start`` predates existing records.

    None if the agent has no records yet (nothing to backfill against — the
    forward window from ``collection_window`` already covers a first run),
    if ``configured_start`` is not earlier than the earliest existing record
    (the gap is already closed), or if a prior run already fully scanned this
    exact ``configured_start`` down to that point (recorded via
    ``get_backfill_probed_start``) — a source's true earliest activity can sit
    later than ``configured_start`` forever, so without this marker the same
    empty window would be rescanned on every run.
    """
    earliest = repository.get_earliest_record_at(agent)
    if earliest is None or configured_start >= earliest:
        return None

    probed_start = repository.get_backfill_probed_start(agent)
    if probed_start is not None and configured_start >= probed_start:
        return None

    return TimeWindow(start=configured_start, end=earliest)


def source_paths_by_agent(
    *, hermes_db: Path, claude_projects_dir: Path, codex_sessions_dir: Path
) -> dict[SupportedAgent, Path]:
    """Map each supported agent to its local source path, in ADAPTER_COLLECTORS order."""
    return {
        SupportedAgent.HERMES_AGENT: hermes_db,
        SupportedAgent.CLAUDE_CODE: claude_projects_dir,
        SupportedAgent.CODEX: codex_sessions_dir,
    }


def _strip_cache_tokens(records: list[NormalizedUsageRecord]) -> list[NormalizedUsageRecord]:
    """Zero each record's cache-read/cache-write tokens before they reach the ledger."""
    return [
        record
        if record.tokens is None
        else replace(
            record, tokens=replace(record.tokens, cache_read_tokens=0, cache_write_tokens=0)
        )
        for record in records
    ]


def collect_agent(
    agent: SupportedAgent,
    adapter_collect: AdapterCollect,
    source_path: Path,
    repository: LedgerRepository,
    *,
    now: datetime,
    dry_run: bool,
    configured_start: datetime = DEFAULT_INITIAL_START,
    include_cache_tokens: bool = True,
) -> AgentCollectionResult:
    """Collect one agent's new usage records — forward and any backfill gap — and persist them.

    ``include_cache_tokens=False`` zeroes cache-read/cache-write tokens
    before they're written to the ledger, for users who don't want prompt-
    cache counts recorded at all.
    """
    is_first_ever_run = repository.get_checkpoint(agent) is None
    forward = collection_window(agent, repository, now=now, configured_start=configured_start)
    backfill = backfill_window(agent, repository, configured_start=configured_start)

    if forward is None and backfill is None:
        return AgentCollectionResult(
            agent=agent, status=None, records_observed=0, records_inserted=0
        )

    records: list[NormalizedUsageRecord] = []
    if forward is not None:
        records.extend(adapter_collect(source_path, forward))
    if backfill is not None:
        records.extend(adapter_collect(source_path, backfill))

    if not include_cache_tokens:
        records = _strip_cache_tokens(records)

    status = overall_source_status(records)

    inserted = 0
    if not dry_run:
        inserted = repository.insert_records(records)
        if forward is not None:
            repository.set_checkpoint(agent, forward.end_utc)
            if is_first_ever_run:
                # The forward window on a first-ever run already started at
                # configured_start, so it fully covers the same span a
                # backfill would otherwise rescan on every later run.
                repository.set_backfill_probed_start(agent, configured_start)
        if backfill is not None:
            repository.set_backfill_probed_start(agent, backfill.start_utc)

    return AgentCollectionResult(
        agent=agent, status=status, records_observed=len(records), records_inserted=inserted
    )


def backfill_agent_models(
    agent: SupportedAgent,
    adapter_collect: AdapterCollect,
    source_path: Path,
    repository: LedgerRepository,
    *,
    now: datetime,
    configured_start: datetime = DEFAULT_INITIAL_START,
) -> AgentBackfillResult:
    """Patch ``model`` into already-ledgered rows that predate model tracking.

    Re-reads the full local source history (``configured_start``..``now``,
    read-only) and matches the returned records back to existing ledger
    rows by fingerprint via :meth:`LedgerRepository.backfill_models`, which
    only fills a still-unset ``model`` column — this never inserts a new
    row and never overwrites any other field, so it's safe to run
    repeatedly (a fully-backfilled ledger just backfills 0 rows).
    """
    window = TimeWindow(start=configured_start, end=now)
    records = adapter_collect(source_path, window)
    models_by_fingerprint = {
        record.fingerprint: record.model for record in records if record.model
    }
    backfilled = repository.backfill_models(models_by_fingerprint)
    return AgentBackfillResult(
        agent=agent, records_scanned=len(records), records_backfilled=backfilled
    )


def backfill_all_models(
    *,
    ledger_path: Path,
    hermes_db: Path,
    claude_projects_dir: Path,
    codex_sessions_dir: Path,
    now: datetime,
    configured_start: datetime = DEFAULT_INITIAL_START,
) -> list[AgentBackfillResult]:
    """Backfill ``model`` into every agent's already-ledgered rows."""
    source_paths = source_paths_by_agent(
        hermes_db=hermes_db,
        claude_projects_dir=claude_projects_dir,
        codex_sessions_dir=codex_sessions_dir,
    )
    repository = LedgerRepository.open(ledger_path)
    try:
        return [
            backfill_agent_models(
                agent,
                adapter_collect,
                source_paths[agent],
                repository,
                now=now,
                configured_start=configured_start,
            )
            for agent, adapter_collect in ADAPTER_COLLECTORS
        ]
    finally:
        repository.close()


def collect_all(
    *,
    ledger_path: Path,
    hermes_db: Path,
    claude_projects_dir: Path,
    codex_sessions_dir: Path,
    now: datetime,
    configured_start: datetime = DEFAULT_INITIAL_START,
    dry_run: bool = False,
    include_cache_tokens: bool = True,
) -> list[AgentCollectionResult]:
    """Collect every supported agent's new usage into the local ledger."""
    source_paths = source_paths_by_agent(
        hermes_db=hermes_db,
        claude_projects_dir=claude_projects_dir,
        codex_sessions_dir=codex_sessions_dir,
    )
    repository = LedgerRepository.open(ledger_path)
    try:
        return [
            collect_agent(
                agent,
                adapter_collect,
                source_paths[agent],
                repository,
                now=now,
                dry_run=dry_run,
                configured_start=configured_start,
                include_cache_tokens=include_cache_tokens,
            )
            for agent, adapter_collect in ADAPTER_COLLECTORS
        ]
    finally:
        repository.close()
