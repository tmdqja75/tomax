"""Privacy-safe normalized usage records shared by local source adapters."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from tomax.time_window import normalize_utc


SCHEMA_VERSION = 1


class SupportedAgent(str, Enum):
    """Agent products supported in the first release."""

    HERMES_AGENT = "hermes_agent"
    CLAUDE_CODE = "claude_code"
    CODEX = "codex"


class SourceStatus(str, Enum):
    """Whether a source was readable and whether it exposed activity."""

    AVAILABLE_WITH_ACTIVITY = "available_with_activity"
    AVAILABLE_WITH_ZERO_ACTIVITY = "available_with_zero_activity"
    SOURCE_UNAVAILABLE = "source_unavailable"


@dataclass(frozen=True, slots=True)
class TokenUsage:
    """Token counts for a single usage observation.

    ``cache_read_tokens`` and ``cache_write_tokens`` are tracked separately
    from :attr:`headline_total` (``input + output + reasoning``): prompt
    caching isn't consistently additive with ``input_tokens`` across
    sources (Claude Code and Hermes report it separately from
    ``input_tokens``; Codex reports it as a subset of ``input_tokens``), so
    this low-level record keeps both raw components rather than picking one
    combined number here. Display-time code that wants a single
    cache-inclusive total, provider-aware, uses
    :func:`tomax.aggregate.agent_effective_total`.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    def __post_init__(self) -> None:
        for field_name in (
            "input_tokens",
            "output_tokens",
            "reasoning_tokens",
            "cache_read_tokens",
            "cache_write_tokens",
        ):
            value = getattr(self, field_name)
            if type(value) is not int or value < 0:
                raise ValueError(f"{field_name} must be a non-negative integer")

    @property
    def headline_total(self) -> int:
        """Return exactly input + output + reasoning tokens."""
        return self.input_tokens + self.output_tokens + self.reasoning_tokens


@dataclass(frozen=True, slots=True)
class NormalizedUsageRecord:
    """A privacy-safe, source-independent local usage observation.

    This record deliberately retains only opaque identifiers and observed names.
    It has no fields for prompts, source paths, command arguments, or content.

    ``session_fingerprint`` is a separate opaque hash of just the source's
    real session identifier (never the identifier itself), shared by every
    record produced from the same session. It exists purely so aggregation
    can count distinct sessions per agent/day without ever recovering or
    storing the raw session ID. It's None for synthetic per-window marker
    records (source unavailable / zero activity) that don't correspond to
    any real session.
    """

    agent: SupportedAgent
    occurred_at: datetime
    fingerprint: str
    tokens: TokenUsage | None
    session_fingerprint: str | None = None
    model: str | None = None
    observed_skill_name: str | None = None
    observed_mcp_server_name: str | None = None
    observed_mcp_tool_name: str | None = None
    source_status: SourceStatus = SourceStatus.AVAILABLE_WITH_ACTIVITY
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.agent, SupportedAgent):
            raise ValueError("agent must be a supported SupportedAgent value")
        if not isinstance(self.source_status, SourceStatus):
            raise ValueError("source_status must be a supported SourceStatus value")
        if not isinstance(self.fingerprint, str) or not self.fingerprint.strip():
            raise ValueError("fingerprint cannot be empty")
        if not isinstance(self.schema_version, int) or self.schema_version < 1:
            raise ValueError("schema_version must be a positive integer")

        object.__setattr__(self, "occurred_at", normalize_utc(self.occurred_at))
        self._validate_source_status()

    @property
    def headline_total(self) -> int | None:
        """Return the headline total, or None when the source was unavailable."""
        if self.tokens is None:
            return None
        return self.tokens.headline_total

    def _validate_source_status(self) -> None:
        if self.source_status is SourceStatus.SOURCE_UNAVAILABLE:
            if self.tokens is not None:
                raise ValueError("source_status source_unavailable requires tokens=None")
            return

        if self.tokens is None:
            raise ValueError("source_status requires observed token usage")

        headline_total = self.tokens.headline_total
        if self.source_status is SourceStatus.AVAILABLE_WITH_ACTIVITY:
            if headline_total == 0:
                raise ValueError(
                    "source_status available_with_activity requires token activity"
                )
            return

        if self.source_status is SourceStatus.AVAILABLE_WITH_ZERO_ACTIVITY:
            if headline_total != 0:
                raise ValueError(
                    "source_status available_with_zero_activity requires zero tokens"
                )
            return

        raise ValueError("source_status must be a supported SourceStatus value")
