# Compact GitHub README Scorecard

## Goal

Replace the profile README's tall interactive-dashboard screenshot with a compact, native SVG scorecard built from the same sanitized aggregates. It advertises practical multi-agent coding activity in a readable static GitHub README image.

## Scope

- Use a rolling UTC window of the latest 30 calendar days, ending on the render date.
- Render one SVG at `assets/tomax/dashboard.svg` and point the managed README block at it.
- Keep the interactive localhost dashboard unchanged.
- Keep the existing public-data validation and privacy boundary unchanged: the scorecard consumes only validated daily payloads, so it never sees device IDs, fingerprints, raw events, or unsanitized names.

## Scorecard

The SVG contains:

1. Header: `Agent Usage`, inclusive 30-day UTC range, and a count of agents with non-zero token totals.
2. Headline: provider-aware cache-inclusive total token count.
3. Trend: a 30-point daily effective-token line, with absent data rendered as a gap rather than zero.
4. Activity summary: active days, total tokens, and agents used.
5. Agent mix: ranked bars for the three supported agents.
6. Two ranked mini-bar lists: top five Skills and top five MCP servers, each with total calls and distinct-name count.

The scorecard height grows only to accommodate five entries per list; no models, pie charts, calendar heatmap, date picker, or React UI is duplicated.

## SVG animation

The most recent present trend point will have a visible static dot plus a small declarative SMIL ring that expands only outward from the dot, then restarts. The static dot is the accessibility and compatibility fallback. SVG scripts are never emitted. The pulse is best-effort in GitHub's image pipeline; the scorecard remains accurate and complete if GitHub proxies or suppresses animation.

## Data and rendering flow

1. Reuse `select_date_range` to limit validated payloads to `[today - 29 days, today]` before all aggregation.
2. Add a small, pure Python SVG renderer under `src/tomax/render/` that accepts the windowed payloads and produces deterministic SVG text.
3. Replace the Playwright screenshot path for the README artifact in both local `tomax render` and `scripts/build_profile_dashboard.py`.
4. Update the workflow template to pass, add, and commit `assets/tomax/dashboard.svg`; remove Node, pnpm, and Playwright setup/caching from that profile-repo workflow because the compact artifact has no browser dependency.
5. Preserve the full interactive dashboard and `export_dashboard_png` for `tomax dashboard` / local detail work; this change only swaps the README artifact.

## Verification

- Test window filtering: older valid payloads do not affect the SVG's totals, trend, agent bars, or top-five lists.
- Test SVG content: it is XML/SVG, contains the static latest-point circle and SMIL animation, and contains exactly the five highest-ranked sanitized Skills and MCP servers in stable rank order.
- Test local render and profile workflow build: each writes `dashboard.svg`, updates README to reference it, remains idempotent, and retains malformed-record diagnostics.
- Run the affected test modules, `uv run pytest -q`, `uv run ruff check .`, and generate a local SVG from the private ledger for visual inspection.

## Documentation

Update `README.md` and `AGENTS.md` to describe the compact 30-day SVG scorecard, top-five list treatment, and static fallback for the pulse.
