"""Builds synthetic, anonymized Codex rollout-log fixtures.

Mirrors the subset of the real ``rollout-*.jsonl`` line schema that the
Codex adapter reads. All values passed in by tests are synthetic; nothing
here originates from real session data.
"""

from __future__ import annotations

import json
from pathlib import Path


def write_rollout(path: Path, session_id: str, events: list[dict]) -> None:
    """Write a synthetic Codex rollout JSONL file, session_meta first."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(session_meta_event(session_id, events[0]["timestamp"])) + "\n")
        for event in events:
            handle.write(json.dumps(event) + "\n")


def session_meta_event(session_id: str, timestamp: str) -> dict:
    return {
        "timestamp": timestamp,
        "type": "session_meta",
        "payload": {
            "id": session_id,
            "timestamp": timestamp,
            "cwd": "/synthetic/path",
            "originator": "codex_cli",
            "cli_version": "0.0.0-synthetic",
        },
    }


def token_count_event(
    timestamp: str,
    *,
    total_input: int,
    total_cached: int = 0,
    total_output: int,
    total_reasoning: int = 0,
) -> dict:
    total = {
        "input_tokens": total_input,
        "cached_input_tokens": total_cached,
        "output_tokens": total_output,
        "reasoning_output_tokens": total_reasoning,
        "total_tokens": total_input + total_output + total_reasoning,
    }
    return {
        "timestamp": timestamp,
        "type": "event_msg",
        "payload": {
            "type": "token_count",
            "info": {
                "total_token_usage": total,
                "last_token_usage": dict(total),
                "model_context_window": 200_000,
            },
            "rate_limits": None,
        },
    }


def turn_context_event(timestamp: str, *, model: str) -> dict:
    return {
        "timestamp": timestamp,
        "type": "turn_context",
        "payload": {
            "model": model,
        },
    }


def function_call_event(timestamp: str, *, name: str, call_id: str) -> dict:
    return {
        "timestamp": timestamp,
        "type": "response_item",
        "payload": {
            "type": "function_call",
            "name": name,
            "arguments": "{}",
            "call_id": call_id,
        },
    }
