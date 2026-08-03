# tomax

`tomax` is a macOS-first Python CLI that builds privacy-conscious,
local usage summaries for **Hermes Agent**, **Claude Code**, and **Codex**.

It reads each supported local source in read-only mode, normalizes only the
metadata needed for aggregation, and keeps a private SQLite ledger on the
machine. Optional publishing writes sanitized per-device, per-UTC-day
aggregates to a GitHub profile repository.

## Requirements

- macOS
- Python 3.11 or newer
- [uv](https://docs.astral.sh/uv/)

Run the commands below from a checkout of this repository.

```sh
uv sync --dev --locked
uv run tomax --help
```

## Quick start: collect and preview locally

Start with the read-only diagnostic command. It creates or reuses this
installation's opaque device ID, reports the configured profile repository (if
any), and checks source health without adding ledger records or advancing
collection checkpoints.

```sh
uv run tomax doctor
```

Preview collection before persisting anything, then run the real collection.
The dry run observes the same inputs but does not insert records or advance
checkpoints.

```sh
uv run tomax collect --dry-run
uv run tomax collect
```

`collect` records prompt cache-read/cache-write token counts in the ledger
by default; pass `--exclude-cache-tokens` to skip recording them at all
(see [Prompt cache token tracking](#prompt-cache-token-tracking)).

Render a fully local dashboard preview after collecting. This command does not
use Git or the network; it writes a preview README, sanitized daily records,
and a screenshot of the dashboard to the chosen directory.

```sh
uv run tomax render --output-dir ./tomax-preview
open ./tomax-preview/README.md
```

Omit `--output-dir` to write the preview alongside the private ledger.

## Interactive localhost dashboard

`tomax dashboard` serves an interactive chart dashboard on
`127.0.0.1` (loopback only) from your local ledger data. The React UI is built
**on demand** the first time you run the command and cached under
`dashboard-ui/dist/`; it is rebuilt only when the UI source changes.

```sh
uv run tomax dashboard
```

The command always collects fresh local usage first (the same collection
`tomax collect` does), so the ledger is up to date before the payload is
built. Without `--all-devices`, the dashboard reflects that just-updated
local ledger. With `--all-devices`, it merges the just-collected local data
with every *other* device's last-published data (shallow-cloned from the
configured profile repository, never the GitHub API) — this device's own
data always comes from the fresh local ledger rather than its possibly-stale
published copy.

This builds the payload, compiles the UI if needed, starts the server, and
opens your browser. Press Ctrl-C to stop.

Requirements: Node.js and pnpm (or npm) must be installed to build the UI. The
build output is a local, gitignored artifact — no bundle is committed.

The charts are [bklit](https://ui.bklit.com) components (built on visx +
`motion` + `@number-flow/react`) and are interactive:

- **Total token usage** — hover shows an animated date ticker plus a tooltip
  with Input / Output / Reasoning counts. Renders as an area chart by default;
  switches to stacked bars once the collected span exceeds
  `bar_chart_threshold_days` (default 15) so long ranges stay readable.
- **Usage by agent** — a ring chart with a side legend; hovering either the ring
  or a legend row syncs the two and shows that agent's value in the ring center
  (large totals are compacted, e.g. `20.9M`).
- **Skills / MCP** — pie charts where hovering pushes a slice out from center and
  animates its value; the idle center shows the total.

Flags:

- `--all-devices` — merge this device's freshly collected local data with
  every other device's data, shallow-cloned from the configured profile
  repository (never the GitHub API).
- `--port` — localhost port to serve on (default `8000`).
- `--no-open` — do not open a browser automatically.
- `--rebuild` — force a fresh UI build even if the cached build looks current.
- `--pie-top-n` — max Skills/MCP pie slices before bucketing the rest into
  `Other` (default `6`).
- `--lang` — dashboard UI language: `en` (default) or `ko`. Localizes chart
  titles, legends, tooltips, number formatting, and date formatting.
- `--exclude-cache-tokens` — exclude prompt cache-read/cache-write tokens
  from the displayed totals (see [Prompt cache token
  tracking](#prompt-cache-token-tracking)). Included by default.
- `--claude-code-only-range` — trim every chart to the first-to-last date
  Claude Code has data for, dropping other agents' data outside that span
  too (not just Claude Code's). Off by default.

`tomax render` accepts the same `--exclude-cache-tokens` flag for its
screenshot/README preview.

The dashboard renders five blocks: total token usage over the rolling window,
usage by agent (ring chart), Skills and MCP usage pies, and a calendar activity
heatmap. It fetches `/data.json` from the local server when the page opens.

## What the collector reads

The first release supports these local sources:

| Agent | Local source | Accounting behavior |
| --- | --- | --- |
| Hermes Agent | `~/.hermes/state.db` | Reads usage rows (including per-row `cache_read_tokens`/`cache_write_tokens`) plus observed skill and MCP calls. |
| Claude Code | `~/.claude/projects/*/*.jsonl` | Reads assistant usage, including `cache_creation_input_tokens`/`cache_read_input_tokens`, and observed Skill/MCP calls. Claude Code's reasoning counter is `0` because its source payload does not expose a separate reasoning field. |
| Codex | `~/.codex/sessions/**/rollout-*.jsonl` | Converts cumulative `token_count` snapshots (including `cached_input_tokens`) into per-session deltas, including safe handling for counter resets; reads observed MCP calls. Codex has no cache-write signal, so its cache-write count is always `0`. |

The collector does not infer usage when a source is missing or malformed. Each
agent reports one of the following statuses:

- `available_with_activity`
- `available_with_zero_activity`
- `source_unavailable`

`source_unavailable` is deliberately different from zero activity and is not
treated as a zero-token estimate.

### Prompt cache token tracking

Every source above also reports cache-read and cache-write token counts.
`collect` records them in the private ledger by default (`--exclude-cache-tokens`
skips recording them at all), and `dashboard`/`render` include them in the
displayed totals by default (`--exclude-cache-tokens` there falls back to
`input + output + reasoning` only).

The inclusion is provider-aware to avoid double-counting: Claude Code and
Hermes report cache-read/cache-write tokens as additional to `input_tokens`,
so both are added on top; Codex reports its cache-read count as a *subset*
of `input_tokens` (it's already counted there), so only Codex's
(always-zero) cache-write count is added. See
`aggregate.agent_effective_total` for the exact rule.

The raw, per-agent `headline_total` (`input + output + reasoning`, never
including cache tokens) and the raw `cache_read_tokens`/`cache_write_tokens`
counts are always both present in the private ledger and the published
per-day JSON records — the flags above only control which figure a given
command *displays* or *records*, not what's retained.

### Collection windows and repeat runs

On a source's first collection, the current bootstrap window is the half-open
UTC interval `[2026-07-04T00:00:00Z, 2026-07-18T00:00:00Z)`. This avoids an
unbounded historical scan. Later collections resume from that source's saved
checkpoint, and opaque fingerprints prevent duplicate normalized records from
being counted twice.

## Publish sanitized aggregates (optional)

Publishing is opt-in. It requires an existing GitHub profile repository and an
authenticated GitHub CLI session; `tomax` does not store or manage a
GitHub token.

```sh
gh auth status
uv run tomax init --repo OWNER/PROFILE-REPO
uv run tomax collect
uv run tomax render --output-dir ./tomax-preview
uv run tomax publish
```

`init` is local-only: it records the `OWNER/REPO` target and ensures the local
device ID exists. `publish` then stages only this installation's sanitized
files under:

```text
data/v1/devices/<opaque-device-id>/<YYYY-MM-DD>.json
```

It clones or reuses a local checkout of the profile repository, fetches and
rebases before pushing, retries bounded non-fast-forward races, and never
force-pushes. Use `--branch` to select a target branch or `--clone-dir` to use
an explicit local checkout.

To generate an aggregated profile dashboard, copy
[`templates/github-workflow.yml`](templates/github-workflow.yml) into the
profile repository as `.github/workflows/tomax-dashboard.yml`. The
workflow validates device/day records and updates the managed README section
and chart assets only when data beneath `data/v1/**` changes. Review and enable
that workflow only when you are ready to publish sanitized aggregates.

## Optional daily macOS schedule

The scheduler installs a local `launchd` job that runs `collect` and then
`publish` at the requested local time. It is not enabled by default.

```sh
uv run tomax schedule install --daily-at 09:00
uv run tomax schedule status
uv run tomax schedule remove
```

Install a schedule only after `init`, GitHub authentication, and a manual
publish have been verified, because scheduled runs can publish this device's
sanitized daily aggregates. The installed job preserves the `PATH` available
during installation so Homebrew-provided `gh` and `git` remain available to
`launchd`.

## Privacy boundary

The project is designed to process only data a user explicitly makes available
on their own machine. It does not transmit session content, raw identifiers,
credentials, or other sensitive personal data. Opt-in publishing writes only
sanitized device/day aggregates.

The local collector is macOS-first. Profile-dashboard rendering happens in a
separate GitHub Actions workflow from sanitized device/day aggregates only.

See [docs/privacy.md](docs/privacy.md) for the exact local/public data boundary,
[docs/multi-device.md](docs/multi-device.md) for shared-profile workflow and
device partition rules, and [docs/troubleshooting.md](docs/troubleshooting.md)
for source-status, Codex-accounting, publishing, and scheduler troubleshooting.

## Development

```sh
uv sync --dev --locked
uv run pytest -q
uv run ruff check .
uv build
uv run tomax --help
```
