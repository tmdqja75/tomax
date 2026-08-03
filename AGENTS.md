# AGENTS.md

Guidance for AI agents and contributors working in this repository.

## What this project is

`tomax` is a macOS-first Python CLI that builds privacy-conscious, local
usage summaries for **Hermes Agent**, **Claude Code**, and **Codex**. It reads
each supported local source read-only, normalizes only aggregation metadata, and
keeps a private SQLite ledger. Optional publishing writes sanitized per-device,
per-UTC-day aggregates to a GitHub profile repository.

See [README.md](README.md) for the user-facing guide.

## Environment and commands

- Python `>=3.11`. Dependency and task runner: [uv](https://docs.astral.sh/uv/).
- Run tests: `uv run pytest -q`
- Lint: `uv run ruff check .`
- Build: `uv build`
- CLI help: `uv run tomax --help`

### CLI commands

| Command | Purpose |
| --- | --- |
| `doctor` | Read-only diagnostics; no ledger writes, no checkpoint advance. |
| `collect` | Read sources and persist normalized records (`--dry-run` to preview). |
| `render` | Write a fully local dashboard preview screenshot (no network). |
| `dashboard` | Serve an interactive localhost chart dashboard (see below); supports `--lang en\|ko`. |
| `publish` | Stage and push this device's sanitized aggregates (opt-in, `gh` auth). |
| `init` | Local-only: record `OWNER/REPO` target, ensure device ID. |
| `schedule` | Install/remove a `launchd` daily collect+publish job; preserves the installer `PATH` for `gh`/`git`. |

## Repository layout

- `src/tomax/` — package source.
  - `aggregate.py` — validation, rolling windows, daily/token totals, record aggregation.
  - `render/` — dashboard backends. `export.py` (screenshot via Playwright/Chromium),
    `dashboard_data.py` (interactive `data.json` builder), `_counters.py` (shared rank/bucket helpers, backend-neutral).
    `markdown.py` (assemble README with embedded screenshot).
  - `dashboard/` — interactive dashboard: `payload.py` (assemble data.json from local
    or multi-device), `remote.py` (shallow-clone multi-device fetch), `server.py`
    (stdlib loopback HTTP server; injects `window.__LANG__` into served
    `index.html` for `--lang`), `ui_build.py` (on-demand UI build with caching).
  - `commands/` — one module per CLI command; `cli.py` wires them with Typer.
  - `ledger/`, `publish/`, `privacy.py`, `public_data.py`, `config.py`, `models.py`.
    `ledger/repository.py` also tracks cache-read/cache-write tokens per
    event (`LedgerRepository.cache_token_totals_by_agent()`) and the public
    per-day record carries per-agent `cache_read_tokens`/`cache_write_tokens`
    alongside `headline_total` (`input + output + reasoning`, never
    including cache). `aggregate.agent_effective_total` computes the
    cache-inclusive display total, skipping Codex's `cache_read_tokens`
    since Codex reports it as a subset of `input_tokens` rather than
    additional to it (unlike Claude Code/Hermes). `collect`,
    `dashboard`, and `render` all include cache tokens by default and
    accept `--exclude-cache-tokens` to opt out (`collect` stops recording
    them at all; `dashboard`/`render` just stop displaying them).
- `dashboard-ui/` — React + Vite + TypeScript source for the interactive dashboard.
  Tailwind v4 + shadcn wiring (`components.json`, `src/index.css`, `src/lib/utils.ts`)
  plus the [bklit](https://ui.bklit.com) chart registry (visx + `motion` +
  `@number-flow/react`).
  - `src/components/charts/**` — vendored bklit components (shadcn `add @bklit/*`).
    Generated; treat as third-party. Chart colors read `--chart-*` tokens from
    `src/index.css`.
  - `src/charts/` — thin leaf wrappers that feed `data.json` into bklit:
    `TokenArea`/`TokenBar` (Area/stacked-Bar chart + tooltip, switched by
    `TokenChart` on `data.tokensChartType`), `DateRangeFilter` (native date
    inputs, clamped to the loaded token date range, filters the Total Token
    Usage chart client-side — `App.tsx` owns the `{from, to}` state), `AgentRing`
    (RingChart + Legend), `UsageDonut` (PieChart, Skills + MCP), plus the custom
    `CalendarHeatmap` and the `names.ts` label/palette map.
  - `src/i18n.ts` — translation lookup and locale-aware number/date formatting,
    driven by `window.__LANG__` (`en` or `ko`).
  Built on demand by `dashboard/ui_build.py`; `node_modules/` and `dist/` are gitignored.
- `tests/` — mirrors the package (`tests/render/`, `tests/dashboard/`, `tests/commands/`).
  Test dirs use no `__init__.py`.
- `docs/` — privacy boundary, multi-device rules, troubleshooting, plans/specs.

## Conventions

- Whenever a new function/feature is added or a bug is fixed, update
  README.md and AGENTS.md to reflect it in the same change.
- Every new Python module starts with `from __future__ import annotations`.
- Match the surrounding code's style, naming, and comment density.
- TDD: write a failing test first, then implement. Commit per logical unit.
- The interactive dashboard server binds `127.0.0.1` only — never `0.0.0.0`.
- Multi-device data is fetched only by shallow `git clone` of the profile repo —
  never the GitHub API.
- `source_unavailable` is never treated as zero activity.

## Dashboard UI constraints (hard)

- Page background is exactly `#090A0B`; each chart block background is exactly
  `#0E0F13`.
- **No colored or gradient backgrounds / gradient blocks anywhere.** The only
  permitted gradients are those internal to a chart component's own default
  rendering — keep those intact.
- `data.json` contract keys: `window {start,end}`, `tokens [{date,input,output,
  reasoning}]`, `tokensChartType ("bar"|"area")`, `agents [{agent,tokens}]`,
  `skills [{name,count}]`, `mcp [{name,count}]`,
  `heatmap [{date,tokens,byAgent [{agent,tokens}]}]`.
- Skills/MCP pies bucket beyond `--pie-top-n` (default 6) into one `Other` entry.
- `tokensChartType` is computed server-side in `render/dashboard_data.py` from
  the window span vs. `AppConfig.bar_chart_threshold_days` (default 15, set via
  `tomax config bar-chart-threshold --days N`) — `"bar"` above the
  threshold, `"area"` at or below it.
- bklit gradients (Area fill, ring/pie glows) are the permitted chart-internal
  exception — do not add page/block CSS gradients.

### Adding more bklit components

`pnpm dlx shadcn@latest add @bklit/<name> --yes` (registry configured in
`components.json`). Node 22 + pnpm 11; `esbuild` is allow-listed in
`pnpm-workspace.yaml`. The rtk hook mangles `curl` stdout — use `rtk proxy curl`
or curl to a file when inspecting registry JSON. Verify with `pnpm build` and
`npx tsc --noEmit` (tsconfig targets ES2022 for bklit's `Array.at`).
