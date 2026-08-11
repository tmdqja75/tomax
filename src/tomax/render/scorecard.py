"""Render the compact, static profile README usage scorecard as SVG."""

from __future__ import annotations

from datetime import date, timedelta
from html import escape

from tomax.aggregate import agent_effective_total, aggregate_records, daily_token_totals, select_date_range
from tomax.models import SupportedAgent
from tomax.render._counters import rank_usage

_WINDOW_DAYS = 30
_TOP_N = 5
_WIDTH = 960
_HEIGHT = 740
_CHART_LEFT = 36
_CHART_RIGHT = 616
_CHART_TOP = 176
_CHART_BOTTOM = 272

_AGENT_LABELS = {
    SupportedAgent.CLAUDE_CODE.value: "Claude Code",
    SupportedAgent.HERMES_AGENT.value: "Hermes Agent",
    SupportedAgent.CODEX.value: "Codex",
}
_AGENT_COLORS = {
    SupportedAgent.CLAUDE_CODE.value: "#AA8DFF",
    SupportedAgent.HERMES_AGENT.value: "#5CD69B",
    SupportedAgent.CODEX.value: "#7CA7FF",
}


def _format_tokens(value: int) -> str:
    if value >= 1_000_000_000:
        return f"{value / 1_000_000_000:.2f}B"
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}K"
    return str(value)


def _date_label(value: date) -> str:
    return f"{value:%b} {value.day}"


def _window_payloads(payloads: list[dict], *, today: date) -> tuple[list[dict], date]:
    start = today - timedelta(days=_WINDOW_DAYS - 1)
    return select_date_range(payloads, start=start, end=today), start


def _trend_paths(
    daily: dict[str, dict[str, int] | None], *, start: date, today: date
) -> tuple[str, tuple[float, float] | None]:
    values: list[tuple[float, int | None]] = []
    for offset in range(_WINDOW_DAYS):
        day = start + timedelta(days=offset)
        bucket = daily.get(day.isoformat())
        value = sum(bucket.values()) if bucket is not None else None
        x = _CHART_LEFT + (_CHART_RIGHT - _CHART_LEFT) * offset / (_WINDOW_DAYS - 1)
        values.append((x, value))

    observed = [value for _, value in values if value is not None]
    if not observed:
        return "", None
    maximum = max(observed) or 1
    commands: list[str] = []
    last_point: tuple[float, float] | None = None
    previous_missing = True
    for x, value in values:
        if value is None:
            previous_missing = True
            continue
        y = _CHART_BOTTOM - (_CHART_BOTTOM - _CHART_TOP) * value / maximum
        commands.append(f"{'M' if previous_missing else 'L'} {x:.1f} {y:.1f}")
        previous_missing = False
        last_point = (x, y)
    return " ".join(commands), last_point


def _counter_rows(counters: dict[str, int], *, color: str, title: str) -> str:
    ranked = rank_usage(counters)[:_TOP_N]
    total = sum(counters.values())
    noun = "skills" if title == "TOP SKILLS" else "servers"
    lines = [
        f'<text x="668" y="{{title_y}}" class="label">{title}</text>',
        f'<text x="668" y="{{subtitle_y}}" class="meta">{total} calls · {len(counters)} {noun}</text>',
    ]
    if not ranked:
        lines.append('<text x="668" y="{row_y}" class="meta">No observed usage</text>')
        return "".join(lines)

    maximum = ranked[0][1]
    for index, (name, count) in enumerate(ranked):
        row_y = 168 + index * 39
        width = 238 * count / maximum
        lines.extend(
            [
                f'<text x="668" y="{{offset_y_plus_{row_y}}}" class="body">{escape(name)}</text>',
                f'<text x="906" y="{{offset_y_plus_{row_y}}}" text-anchor="end" class="count">{count}</text>',
                f'<rect x="668" y="{{offset_y_plus_{row_y + 8}}}" width="238" height="5" rx="3" fill="#252936"/>',
                f'<rect x="668" y="{{offset_y_plus_{row_y + 8}}}" width="{width:.1f}" height="5" rx="3" fill="{color}"/>',
            ]
        )
    return "".join(lines)


def render_scorecard_svg(
    payloads: list[dict], *, today: date, include_cache_tokens: bool = True
) -> str:
    """Return a deterministic, 30-day compact README scorecard SVG.

    ``payloads`` must already be validated public daily records. Missing source
    data remains a gap in the trend rather than becoming an invented zero.
    """
    windowed, start = _window_payloads(payloads, today=today)
    aggregated = aggregate_records(windowed)
    agent_totals = {
        agent.value: agent_effective_total(
            agent.value,
            aggregated["agents"][agent.value],
            include_cache_tokens=include_cache_tokens,
        )
        for agent in SupportedAgent
    }
    total_tokens = sum(agent_totals.values())
    active_days = aggregated["active_days"]
    active_agents = sum(value > 0 for value in agent_totals.values())
    trend_path, latest_point = _trend_paths(
        daily_token_totals(windowed, include_cache_tokens=include_cache_tokens),
        start=start,
        today=today,
    )
    skills = _counter_rows(aggregated["skills"], color="#AA8DFF", title="TOP SKILLS")
    mcp = _counter_rows(aggregated["mcp_servers"], color="#5CD69B", title="TOP MCP")

    trend = (
        f'<path d="{trend_path}" fill="none" stroke="#7CA7FF" stroke-width="4" '
        'stroke-linejoin="round" stroke-linecap="round"/>'
        if trend_path
        else ""
    )
    pulse = ""
    if latest_point is not None:
        x, y = latest_point
        pulse = (
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" fill="#7CA7FF"/>'
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" fill="none" stroke="#7CA7FF" '
            'stroke-width="1.5"><animate attributeName="r" values="4;9" dur="1.6s" '
            'repeatCount="indefinite"/><animate attributeName="opacity" values=".75;0" '
            'dur="1.6s" repeatCount="indefinite"/></circle>'
        )

    agent_rows: list[str] = []
    maximum_agent = max(agent_totals.values()) or 1
    for index, (agent_name, value) in enumerate(rank_usage(agent_totals)):
        y = 428 + index * 44
        width = 564 * value / maximum_agent
        agent_rows.append(
            f'<text x="36" y="{y}" class="body">{_AGENT_LABELS[agent_name]}</text>'
            f'<text x="600" y="{y}" text-anchor="end" class="count">{_format_tokens(value)}</text>'
            f'<rect x="36" y="{y + 9}" width="564" height="7" rx="4" fill="#252936"/>'
            f'<rect x="36" y="{y + 9}" width="{width:.1f}" height="7" rx="4" '
            f'fill="{_AGENT_COLORS[agent_name]}"/>'
        )

    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="{_WIDTH}" height="{_HEIGHT}" viewBox="0 0 {_WIDTH} {_HEIGHT}" role="img" aria-labelledby="title desc">
  <title id="title">Agent Usage — {_date_label(start)} to {_date_label(today)}, {today.year}</title>
  <desc id="desc">30-day coding-agent usage scorecard with daily token trend, agent distribution, five top Skills, and five top MCP servers.</desc>
  <rect width="960" height="740" rx="18" fill="#0E0F13"/>
  <style>.title{{font:700 22px system-ui,sans-serif;fill:#F5F7FB}}.meta{{font:12px system-ui,sans-serif;fill:#8C91A1}}.label{{font:600 11px system-ui,sans-serif;letter-spacing:1.1px;fill:#8C91A1}}.total{{font:800 48px system-ui,sans-serif;fill:#F5F7FB}}.small{{font:600 13px system-ui,sans-serif;fill:#F5F7FB}}.body{{font:13px system-ui,sans-serif;fill:#D7D9E0}}.count{{font:600 12px system-ui,sans-serif;fill:#8C91A1}}.rule{{stroke:#252734;stroke-width:1}}.grid{{stroke:#1A1C24;stroke-width:1}}</style>
  <text x="36" y="48" class="title">Agent Usage</text><text x="36" y="69" class="meta">{_date_label(start)} – {_date_label(today)}, {today.year} · refreshed daily</text>
  <rect x="810" y="32" width="114" height="28" rx="14" fill="#11261C" stroke="#24533D"/><text x="867" y="50" text-anchor="middle" class="meta">{active_agents} coding agents</text>
  <text x="36" y="111" class="label">30-DAY TOKEN USAGE</text><text x="36" y="158" class="total">{_format_tokens(total_tokens)}</text><text x="210" y="158" class="meta">tokens</text>
  <line x1="36" y1="190" x2="616" y2="190" class="grid"/><line x1="36" y1="230" x2="616" y2="230" class="grid"/><line x1="36" y1="270" x2="616" y2="270" class="grid"/>{trend}{pulse}
  <text x="36" y="292" class="meta">{_date_label(start)}</text><text x="616" y="292" text-anchor="end" class="meta">{_date_label(today)}</text>
  <text x="36" y="337" class="small">{active_days} / {_WINDOW_DAYS}</text><text x="36" y="355" class="meta">active days</text><text x="180" y="337" class="small">{_format_tokens(total_tokens)}</text><text x="180" y="355" class="meta">total tokens</text><text x="310" y="337" class="small">{active_agents}</text><text x="310" y="355" class="meta">agents used</text>
  <text x="36" y="402" class="label">AGENT MIX</text>{''.join(agent_rows)}
  <rect x="650" y="94" width="274" height="248" rx="10" fill="#090A0B" stroke="#252734"/>{skills.format(title_y=121, subtitle_y=139, offset_y_plus_168=168, offset_y_plus_176=176, offset_y_plus_207=207, offset_y_plus_215=215, offset_y_plus_246=246, offset_y_plus_254=254, offset_y_plus_285=285, offset_y_plus_293=293, offset_y_plus_324=324, offset_y_plus_332=332, row_y=168)}
  <rect x="650" y="368" width="274" height="248" rx="10" fill="#090A0B" stroke="#252734"/>{mcp.format(title_y=395, subtitle_y=413, offset_y_plus_168=442, offset_y_plus_176=450, offset_y_plus_207=481, offset_y_plus_215=489, offset_y_plus_246=520, offset_y_plus_254=528, offset_y_plus_285=559, offset_y_plus_293=567, offset_y_plus_324=598, offset_y_plus_332=606, row_y=442)}
  <line x1="36" y1="660" x2="924" y2="660" class="rule"/><text x="36" y="690" class="meta">Includes prompt-cache tokens using provider-aware accounting.</text>
</svg>'''
    return svg
