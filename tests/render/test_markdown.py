from tomax.render.markdown import (
    DASHBOARD_IMAGE_PATH,
    MARKER_END,
    MARKER_START,
    insert_dashboard_section,
    render_dashboard_markdown,
    update_readme,
)


def test_section_contains_single_screenshot_reference():
    md = render_dashboard_markdown()
    assert MARKER_START in md and MARKER_END in md
    assert f"]({DASHBOARD_IMAGE_PATH})" in md
    # No leftover per-chart images.
    for stale in ("token-activity-14d.png", "agent-share.png", "skills.png", "mcp.png"):
        assert stale not in md


def test_section_uses_custom_image_path():
    md = render_dashboard_markdown(image_path="x/y/dash.png")
    assert "](x/y/dash.png)" in md


def test_update_readme_replaces_between_markers_and_is_idempotent():
    existing = "# Title\n\nintro\n\n<!-- tomax:start -->\nOLD\n<!-- tomax:end -->\n\nfooter\n"
    section = render_dashboard_markdown()
    once = update_readme(existing, section)
    twice = update_readme(once, section)
    assert once == twice
    assert "# Title" in once and "footer" in once
    assert "OLD" not in once


def test_insert_dashboard_section_at_end_when_after_line_is_none():
    existing = "# Title\n\nintro\n"
    section = render_dashboard_markdown()

    result = insert_dashboard_section(existing, section, after_line=None)

    assert result.startswith("# Title\n\nintro\n\n" + section)


def test_insert_dashboard_section_at_top_when_after_line_is_zero():
    existing = "# Title\n\nintro\n"
    section = render_dashboard_markdown()

    result = insert_dashboard_section(existing, section, after_line=0)

    assert result.startswith(section)
    assert result.rstrip("\n").endswith("intro")


def test_insert_dashboard_section_in_the_middle():
    existing = "line1\nline2\nline3\nline4\n"
    section = render_dashboard_markdown()

    result = insert_dashboard_section(existing, section, after_line=2)
    lines = result.splitlines()

    assert lines[0] == "line1"
    assert lines[1] == "line2"
    assert MARKER_START in result
    assert result.rstrip("\n").endswith("line4")
    assert lines.index(MARKER_START) > lines.index("line2")
    assert lines.index("line3") > lines.index(MARKER_END)


def test_insert_dashboard_section_clamps_an_out_of_range_after_line():
    existing = "line1\nline2\n"
    section = render_dashboard_markdown()

    too_high = insert_dashboard_section(existing, section, after_line=999)
    too_low = insert_dashboard_section(existing, section, after_line=-5)

    assert too_high.rstrip("\n").endswith(MARKER_END)
    assert too_low.startswith(MARKER_START)


def test_insert_dashboard_section_then_update_readme_replaces_in_place():
    existing = "line1\nline2\nline3\n"
    section = render_dashboard_markdown()

    first_install = insert_dashboard_section(existing, section, after_line=1)
    second_update = update_readme(first_install, render_dashboard_markdown(image_path="new.png"))

    assert second_update.count(MARKER_START) == 1
    assert "new.png" in second_update
    assert second_update.splitlines()[0] == "line1"
    assert second_update.rstrip("\n").endswith("line3")
