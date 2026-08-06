# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

**Read [AGENTS.md](AGENTS.md) first** — it is the primary agent-facing guide (project purpose, environment, CLI command table, full repository layout, conventions, and hard dashboard UI constraints). This file adds only what AGENTS.md doesn't already cover.

## Commands

```sh
uv sync --dev --locked        # install deps (locked)
uv run pytest -q              # run all tests
uv run pytest tests/render/test_markdown.py -q          # single test file
uv run pytest tests/render/test_markdown.py::test_foo   # single test
uv run ruff check .           # lint
uv build                      # build distribution
uv run tomax --help           # CLI entry point
```

Dashboard UI (inside `dashboard-ui/`, Node 22 + pnpm):

```sh
pnpm build          # production build (also triggered on-demand by `tomax dashboard`)
npx tsc --noEmit     # typecheck (tsconfig targets ES2022 for bklit's Array.at)
```

CI (`.github/workflows/ci.yml`) runs, in order: `uv sync --dev --locked`, `pytest -q`, `ruff check .`, `uv build`, `tomax --help`.

## Architecture notes beyond AGENTS.md

- **Adapters** (`src/tomax/adapters/`) — one module per source (`claude_code.py`, `codex.py`, `hermes.py`) sharing a `base.py` interface. Each adapter is read-only against its local source and reports one of `available_with_activity` / `available_with_zero_activity` / `source_unavailable`; never fabricate usage for a missing/malformed source.
- **Collection flow**: `commands/collect.py` drives adapters → normalizes records → `ledger/repository.py` (SQLite) persists them, keyed by opaque per-record fingerprints so re-running collection never double-counts. `ledger/schema.py` defines the schema.
- **Render vs. dashboard**: `render/` produces the fully local, no-network screenshot+README preview (Playwright/Chromium via `render/export.py`); `dashboard/` serves the interactive localhost React app (`dashboard/server.py`, loopback-only). Both consume the same aggregation logic in `aggregate.py` but build their payload independently (`render/dashboard_data.py` vs `dashboard/payload.py`).
- **Multi-device aggregation** (`dashboard/remote.py`) shallow-clones the profile repo — never calls the GitHub API.
- **Publish** (`publish/git.py`) stages only the current device's sanitized `data/v1/devices/<device-id>/<date>.json`, rebases before pushing, retries bounded non-fast-forward races, never force-pushes.
- **Dashboard UI** (`dashboard-ui/`) is a separate pnpm project built on demand and cached in `dashboard-ui/dist/` (gitignored, not committed). `src/components/charts/**` is vendored/generated bklit output — treat as third-party, don't hand-edit; add new components via `pnpm dlx shadcn@latest add @bklit/<name>`. Leaf wrappers in `src/charts/` are the actual integration point with `data.json`.
- Tests mirror the package under `tests/` with no `__init__.py` files; fixtures for adapter sources live in `tests/fixtures/`.

## Conventions not to violate

See AGENTS.md's "Conventions" and "Dashboard UI constraints (hard)" sections — notably: every new module starts with `from __future__ import annotations`, README.md/AGENTS.md get updated in the same change as any new feature/fix, TDD (failing test first), the dashboard server binds `127.0.0.1` only, and `source_unavailable` must never be treated as zero activity.
