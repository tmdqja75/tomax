from datetime import date

from tomax.render.dashboard_data import build_dashboard_data

_STATUS = "available_with_activity"


def _agent(
    input_=0,
    output=0,
    reasoning=0,
    headline=0,
    sessions=0,
    status=_STATUS,
    cache_read=0,
    cache_write=0,
):
    return {
        "input_tokens": input_,
        "output_tokens": output,
        "reasoning_tokens": reasoning,
        "cache_read_tokens": cache_read,
        "cache_write_tokens": cache_write,
        "headline_total": headline,
        "session_count": sessions,
        "source_status": status,
    }


def _payload(day, *, device="dev1", claude=None, codex=None, skills=None, mcp=None):
    return {
        "schema_version": 2,
        "device_id": device,
        "date": day,
        "agents": {
            "claude_code": claude or _agent(status="source_unavailable"),
            "codex": codex or _agent(status="source_unavailable"),
            "hermes_agent": _agent(status="source_unavailable"),
        },
        "skills": skills or {},
        "mcp_servers": mcp or {},
        "mcp_tools": {},
    }


def test_build_dashboard_data_shapes_every_section():
    payloads = [
        _payload(
            "2026-07-10",
            claude=_agent(input_=100, output=50, reasoning=0, headline=150),
            skills={"brainstorming": 3, "tdd": 2, "graphify": 1},
            mcp={"gmail": 4, "calendar": 1},
        ),
        _payload(
            "2026-07-11",
            codex=_agent(input_=10, output=5, reasoning=2, headline=17),
        ),
    ]

    data = build_dashboard_data(payloads, today=date(2026, 7, 11), window_days=14, pie_top_n=2)

    assert data["window"] == {"start": "2026-07-10", "end": "2026-07-11"}
    assert data["tokens"] == [
        {"date": "2026-07-10", "input": 100, "output": 50, "reasoning": 0, "cache": 0},
        {"date": "2026-07-11", "input": 10, "output": 5, "reasoning": 2, "cache": 0},
    ]
    assert {"agent": "claude_code", "tokens": 150} in data["agents"]
    assert {"agent": "codex", "tokens": 17} in data["agents"]
    # pie_top_n=2 keeps top 2 skills and folds the rest into "Other"
    assert data["skills"] == [
        {"name": "brainstorming", "count": 3},
        {"name": "tdd", "count": 2},
        {"name": "Other", "count": 1},
    ]
    assert data["mcp"] == [{"name": "gmail", "count": 4}, {"name": "calendar", "count": 1}]
    assert data["heatmap"] == [
        {
            "date": "2026-07-10",
            "tokens": 150,
            "byAgent": [{"agent": "claude_code", "tokens": 150}],
            "bySkill": [
                {"name": "brainstorming", "count": 3},
                {"name": "tdd", "count": 2},
                {"name": "graphify", "count": 1},
            ],
            "byMcp": [
                {"name": "gmail", "count": 4},
                {"name": "calendar", "count": 1},
            ],
        },
        {
            "date": "2026-07-11",
            "tokens": 17,
            "byAgent": [{"agent": "codex", "tokens": 17}],
            "bySkill": [],
            "byMcp": [],
        },
    ]
    assert data["pieTopN"] == 2


def test_build_dashboard_data_tokens_are_not_truncated_past_window_days():
    payloads = [
        _payload("2026-06-01", claude=_agent(input_=1, output=1, reasoning=0, headline=2)),
        _payload("2026-07-11", claude=_agent(input_=2, output=2, reasoning=0, headline=4)),
    ]

    data = build_dashboard_data(payloads, today=date(2026, 7, 11), window_days=14)

    assert data["window"] == {"start": "2026-06-01", "end": "2026-07-11"}
    assert [entry["date"] for entry in data["tokens"]] == ["2026-06-01", "2026-07-11"]


def test_build_dashboard_data_empty_uses_window_fallback():
    data = build_dashboard_data([], today=date(2026, 7, 18), window_days=14)
    assert data["window"] == {"start": "2026-07-05", "end": "2026-07-18"}
    assert data["tokens"] == []
    assert data["skills"] == []
    assert data["mcp"] == []
    assert data["heatmap"] == []
    assert all(entry["tokens"] == 0 for entry in data["agents"])


def test_tokens_chart_type_is_area_when_span_equals_the_threshold():
    data = build_dashboard_data(
        [], today=date(2026, 7, 15), window_days=5, bar_chart_threshold_days=5
    )
    assert data["window"] == {"start": "2026-07-11", "end": "2026-07-15"}
    assert data["tokensChartType"] == "area"


def test_tokens_chart_type_is_bar_when_span_exceeds_the_threshold():
    data = build_dashboard_data(
        [], today=date(2026, 7, 16), window_days=6, bar_chart_threshold_days=5
    )
    assert data["window"] == {"start": "2026-07-11", "end": "2026-07-16"}
    assert data["tokensChartType"] == "bar"


def test_cache_tokens_are_a_separate_bucket_in_the_tokens_chart_by_default():
    payloads = [
        _payload(
            "2026-07-10",
            claude=_agent(
                input_=100, output=50, reasoning=0, headline=150, cache_read=20, cache_write=5
            ),
        ),
    ]

    data = build_dashboard_data(payloads, today=date(2026, 7, 10), window_days=14)

    assert data["tokens"] == [
        {"date": "2026-07-10", "input": 100, "output": 50, "reasoning": 0, "cache": 25}
    ]
    # the ring/heatmap totals still fold cache into one per-agent number
    assert {"agent": "claude_code", "tokens": 175} in data["agents"]
    assert data["heatmap"][0]["tokens"] == 175


def test_exclude_cache_tokens_zeroes_the_cache_bucket_and_reverts_totals():
    payloads = [
        _payload(
            "2026-07-10",
            claude=_agent(
                input_=100, output=50, reasoning=0, headline=150, cache_read=20, cache_write=5
            ),
        ),
    ]

    data = build_dashboard_data(
        payloads, today=date(2026, 7, 10), window_days=14, include_cache_tokens=False
    )

    assert data["tokens"] == [
        {"date": "2026-07-10", "input": 100, "output": 50, "reasoning": 0, "cache": 0}
    ]
    assert {"agent": "claude_code", "tokens": 150} in data["agents"]


def test_codex_cache_read_is_split_out_of_input_into_the_cache_bucket():
    # Codex's input_tokens (100) already includes its cache_read_tokens (20)
    # as a subset, so the chart's "input" bucket must show the non-cached
    # remainder (80) while "cache" shows the 20 -- not double-count it in
    # "input" while dropping it from "cache" entirely.
    payloads = [
        _payload(
            "2026-07-10",
            codex=_agent(
                input_=100, output=50, reasoning=0, headline=150, cache_read=20, cache_write=0
            ),
        ),
    ]

    data = build_dashboard_data(payloads, today=date(2026, 7, 10), window_days=14)

    assert data["tokens"] == [
        {"date": "2026-07-10", "input": 80, "output": 50, "reasoning": 0, "cache": 20}
    ]
    # headline_total (150) already has the cache read baked in, so the
    # ring-chart total is unaffected by the input/cache split above.
    assert {"agent": "codex", "tokens": 150} in data["agents"]
