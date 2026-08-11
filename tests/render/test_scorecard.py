from __future__ import annotations

from datetime import date
from xml.etree import ElementTree

from tomax.render.scorecard import render_scorecard_svg


def _agent(*, tokens: int = 0) -> dict:
    return {
        "input_tokens": tokens,
        "output_tokens": 0,
        "reasoning_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "headline_total": tokens,
        "session_count": 1 if tokens else 0,
        "source_status": "available_with_activity" if tokens else "source_unavailable",
    }


def _payload(day: str, *, tokens: int, skills: dict[str, int], mcp: dict[str, int]) -> dict:
    return {
        "device_id": "device-a",
        "date": day,
        "agents": {
            "claude_code": _agent(tokens=tokens),
            "codex": _agent(),
            "hermes_agent": _agent(),
        },
        "skills": skills,
        "mcp_servers": mcp,
    }


def test_scorecard_limits_all_sections_to_the_last_30_days_and_keeps_top_five() -> None:
    svg = render_scorecard_svg(
        [
            _payload(
                "2026-07-01",
                tokens=999,
                skills={"old-skill": 999},
                mcp={"old-mcp": 999},
            ),
            _payload(
                "2026-07-02",
                tokens=100,
                skills={f"skill-{index}": 10 - index for index in range(6)},
                mcp={f"mcp-{index}": 10 - index for index in range(6)},
            ),
            _payload("2026-07-31", tokens=50, skills={}, mcp={}),
        ],
        today=date(2026, 7, 31),
    )

    assert "Jul 2 – Jul 31, 2026" in svg
    assert "old-skill" not in svg and "old-mcp" not in svg
    for index in range(5):
        assert f"skill-{index}" in svg
        assert f"mcp-{index}" in svg
    assert "skill-5" not in svg and "mcp-5" not in svg


def test_scorecard_has_a_small_centered_outward_only_latest_point_pulse() -> None:
    svg = render_scorecard_svg(
        [_payload("2026-07-31", tokens=50, skills={}, mcp={})], today=date(2026, 7, 31)
    )

    root = ElementTree.fromstring(svg)
    namespace = {"svg": "http://www.w3.org/2000/svg"}
    circles = root.findall("svg:circle", namespace)
    animated = next(circle for circle in circles if circle.find("svg:animate", namespace) is not None)
    static = next(circle for circle in circles if circle is not animated and circle.get("fill") == "#7CA7FF")

    assert (animated.get("cx"), animated.get("cy")) == (static.get("cx"), static.get("cy"))
    assert animated.find("svg:animate[@attributeName='r']", namespace).get("values") == "4;9"
    assert animated.find("svg:animate[@attributeName='opacity']", namespace).get("values") == ".75;0"


def test_scorecard_ranks_agent_bars_by_total_tokens() -> None:
    payload = _payload("2026-07-31", tokens=30, skills={}, mcp={})
    payload["agents"]["hermes_agent"] = _agent(tokens=20)

    svg = render_scorecard_svg([payload], today=date(2026, 7, 31))

    assert svg.index("Claude Code") < svg.index("Hermes Agent")
