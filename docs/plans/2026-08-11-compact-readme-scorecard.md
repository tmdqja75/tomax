# Compact README Scorecard Implementation Plan

> **For Hermes:** Implement task-by-task with test-first changes; do not commit unless requested.

**Goal:** Replace the README's browser screenshot with a privacy-safe, compact 30-day SVG usage scorecard.

**Architecture:** A pure `scorecard.py` renderer receives validated, date-windowed payloads and returns deterministic SVG. Both local render and profile-repo CI call it, so the profile artifact no longer needs React, Node, or Playwright. The interactive dashboard remains unchanged.

**Tech Stack:** Python stdlib SVG text generation, existing aggregation helpers, pytest, Ruff.

**Baseline:** `main` → `feat/compact-readme-scorecard`.

---

### Task 1: Test and add the pure scorecard renderer

**Files:**
- Create: `src/tomax/render/scorecard.py`
- Create: `tests/render/test_scorecard.py`

1. Write failing tests for a 30-day inclusive window, cache-aware headline/agent totals, top-five stable rankings, and an SVG containing the latest static point plus a centered outward-only SMIL ring (`r: 4;9`, opacity `.75;0`).
2. Run `uv run pytest -q tests/render/test_scorecard.py`; expect an import failure.
3. Implement only the renderer and small formatting/layout helpers. Reuse `select_date_range`, `aggregate_records`, `daily_totals`, `agent_effective_total`, and `rank_usage`; do not add dependencies.
4. Re-run the focused test module; expect pass.

### Task 2: Switch local README render to SVG

**Files:**
- Modify: `src/tomax/render/markdown.py`
- Modify: `src/tomax/commands/render.py`
- Modify: `tests/render/test_markdown.py`
- Modify: `tests/render/test_render_command.py`
- Modify: `tests/commands/test_render.py`

1. Write failing tests for `assets/tomax/dashboard.svg`, README reference replacement, and idempotent SVG output.
2. Run the affected tests; expect existing PNG assertions to fail.
3. Replace the local screenshot export path with the pure scorecard renderer and write its SVG atomically through the existing `_write_if_changed` helper. Preserve sanitized daily-record staging and cache-token option behavior.
4. Re-run the affected tests; expect pass.

### Task 3: Switch profile-repo workflow build to SVG

**Files:**
- Modify: `scripts/build_profile_dashboard.py`
- Modify: `src/tomax/templates/github-workflow.yml`
- Modify: `tests/test_workflow_template.py`
- Modify: `tests/scripts/test_build_profile_dashboard.py`

1. Write failing tests proving the profile build writes and references `dashboard.svg`, stays idempotent, keeps invalid-record diagnostics, and the workflow neither installs nor caches browser/UI dependencies.
2. Run the affected tests; expect PNG/browser assumptions to fail.
3. Replace screenshot/UI-build calls with the pure renderer. Rename the CLI option/default and workflow asset/add path to SVG. Preserve validation before rendering.
4. Re-run the affected tests; expect pass.

### Task 4: Update documentation and complete verification

**Files:**
- Modify: `README.md`
- Modify: `AGENTS.md`
- Keep: `docs/superpowers/specs/2026-08-11-compact-readme-scorecard-design.md`

1. Describe the compact 30-day SVG, five-row Skills/MCP lists, static latest marker, and best-effort outward animation.
2. Generate a real local preview with `uv run tomax render --output-dir /tmp/tomax-scorecard-preview` and inspect `assets/tomax/dashboard.svg`.
3. Run `uv run pytest -q`, `uv run ruff check .`, `uv build`, and `git diff --check`.
4. Report exact results and leave the feature branch uncommitted.
