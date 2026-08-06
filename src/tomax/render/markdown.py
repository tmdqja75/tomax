"""Renders the managed README dashboard section as Markdown.

Produces a self-contained Markdown block bounded by stable managed markers,
so an existing README's surrounding content is always preserved on update.
"""

from __future__ import annotations

MARKER_START = "<!-- tomax:start -->"
MARKER_END = "<!-- tomax:end -->"

DASHBOARD_IMAGE_PATH = "assets/tomax/dashboard.png"


def render_dashboard_markdown(*, image_path: str = DASHBOARD_IMAGE_PATH) -> str:
    """Render the managed dashboard section: a single dashboard screenshot."""
    sections = [
        MARKER_START,
        "## Agent Usage",
        "",
        f"![Agent Usage dashboard]({image_path})",
        "",
        MARKER_END,
    ]
    return "\n".join(sections)


def insert_dashboard_section(
    existing_readme: str, dashboard_markdown: str, *, after_line: int | None
) -> str:
    """Insert the managed section at a specific position, for first install only.

    ``after_line`` is the 1-indexed README line to insert after (``0`` = very
    top, ``None`` = end of file). Out-of-range values are clamped rather than
    raising, since a stale line number from a slightly-changed README is not
    worth failing the whole command over. Every subsequent update should go
    through ``update_readme`` instead, which replaces in place between the
    markers this leaves behind.
    """
    lines = existing_readme.splitlines()
    index = len(lines) if after_line is None else max(0, min(after_line, len(lines)))
    before, after = lines[:index], lines[index:]

    parts: list[str] = []
    if before:
        parts.extend(before)
        parts.append("")
    parts.extend(dashboard_markdown.splitlines())
    if after:
        parts.append("")
        parts.extend(after)
    return "\n".join(parts) + "\n"


def update_readme(existing_readme: str, dashboard_markdown: str) -> str:
    """Replace content between the managed markers, preserving everything else.

    If no markers exist yet, appends a new managed section at the end.
    Idempotent: applying the same dashboard content twice leaves the
    README unchanged the second time.
    """
    start_index = existing_readme.find(MARKER_START)
    end_index = existing_readme.find(MARKER_END)
    if start_index == -1 or end_index == -1:
        if existing_readme.strip():
            return existing_readme.rstrip("\n") + "\n\n" + dashboard_markdown + "\n"
        return dashboard_markdown + "\n"

    end_index += len(MARKER_END)
    return existing_readme[:start_index] + dashboard_markdown + existing_readme[end_index:]
