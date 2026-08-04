"""Build the chart-ready ``data.json`` payload consumed by the interactive dashboard UI.

Pure and I/O-free: takes already-validated daily payloads from
``validate_and_partition(...).valid_payloads`` and reshapes them into the exact
JSON contract the React charts expect.
"""

from __future__ import annotations

from datetime import date, timedelta

from tomax.aggregate import (
    agent_effective_total,
    aggregate_records,
    daily_agent_totals,
    daily_counter_totals,
    daily_token_totals,
    daily_totals,
)
from tomax.models import SupportedAgent
from tomax.render._counters import bucket_top_n, rank_usage


def _pie(counters: dict[str, int], pie_top_n: int) -> list[dict]:
    return [
        {"name": name, "count": count}
        for name, count in bucket_top_n(rank_usage(counters), pie_top_n)
    ]


def build_dashboard_data(
    valid_payloads: list[dict],
    *,
    today: date,
    window_days: int = 14,
    pie_top_n: int = 6,
    bar_chart_threshold_days: int = 15,
    include_cache_tokens: bool = True,
) -> dict:
    """Reshape validated daily payloads into the dashboard's data.json contract.

    ``include_cache_tokens`` (default ``True``) folds prompt-cache tokens
    into the displayed totals — see ``aggregate.agent_effective_total``.
    """
    token_by_date = daily_token_totals(valid_payloads, include_cache_tokens=include_cache_tokens)

    tokens = [
        {
            "date": day,
            "input": totals["input"],
            "output": totals["output"],
            "reasoning": totals["reasoning"],
            "cache": totals["cache"],
        }
        for day, totals in sorted(token_by_date.items())
        if totals is not None
    ]

    if tokens:
        window = {"start": tokens[0]["date"], "end": tokens[-1]["date"]}
    else:
        start = today - timedelta(days=window_days - 1)
        window = {"start": start.isoformat(), "end": today.isoformat()}

    span_days = (date.fromisoformat(window["end"]) - date.fromisoformat(window["start"])).days + 1
    tokens_chart_type = "bar" if span_days > bar_chart_threshold_days else "area"

    aggregated = aggregate_records(valid_payloads)
    agents = [
        {
            "agent": agent.value,
            "tokens": agent_effective_total(
                agent.value,
                aggregated["agents"][agent.value],
                include_cache_tokens=include_cache_tokens,
            ),
        }
        for agent in SupportedAgent
    ]

    agent_totals_by_day = daily_agent_totals(valid_payloads, include_cache_tokens=include_cache_tokens)
    skills_by_day = daily_counter_totals(valid_payloads, "skills")
    mcp_by_day = daily_counter_totals(valid_payloads, "mcp_servers")
    models_by_day = daily_counter_totals(valid_payloads, "models")
    heatmap = [
        {
            "date": day,
            "tokens": total,
            "byAgent": [
                {"agent": agent_name, "tokens": agent_total}
                for agent_name, agent_total in sorted(
                    agent_totals_by_day.get(day, {}).items(), key=lambda kv: -kv[1]
                )
            ],
            "bySkill": [
                {"name": name, "count": count} for name, count in rank_usage(skills_by_day.get(day, {}))
            ],
            "byMcp": [
                {"name": name, "count": count} for name, count in rank_usage(mcp_by_day.get(day, {}))
            ],
            "byModel": [
                {"name": name, "count": count}
                for name, count in rank_usage(models_by_day.get(day, {}))
            ],
        }
        for day, total in sorted(
            daily_totals(valid_payloads, include_cache_tokens=include_cache_tokens).items()
        )
    ]

    return {
        "window": window,
        "tokens": tokens,
        "tokensChartType": tokens_chart_type,
        "agents": agents,
        "skills": _pie(aggregated["skills"], pie_top_n),
        "mcp": _pie(aggregated["mcp_servers"], pie_top_n),
        "models": _pie(aggregated["models"], pie_top_n),
        "heatmap": heatmap,
        "pieTopN": pie_top_n,
    }
