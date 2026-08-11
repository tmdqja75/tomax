# Interactive `init` + README Dashboard Registration — Design

**Date:** 2026-08-06
**Status:** Approved (design), pending implementation plan

## Problem

`tomax render` produces a local-only dashboard preview, and `tomax publish`
only ever pushes this device's sanitized `data/v1/devices/<device-id>/`
partition. Getting the aggregated dashboard image into a GitHub profile
README requires a third, entirely manual step: copying
`templates/github-workflow.yml` into the profile repo by hand as
`.github/workflows/tomax-dashboard.yml`. Nothing in the CLI walks a user
through this, so it is easy to publish data for months without the README
ever reflecting it (this is exactly what happened investigating this issue —
`data/v1/devices/**` was populated back to April, but the profile repo had no
`tomax-dashboard.yml` and no `<!-- tomax:start -->` markers in its README at
all).

Separately, `tomax init --repo OWNER/REPO` is a single required flag with no
interactive fallback, which doesn't leave room for a natural "and would you
also like to set up the dashboard?" follow-up.

## Goal

- Make `tomax init` interactive by default (prompts for repo target and,
  newly, dashboard registration), while keeping every prompt overridable by
  a flag so the command still runs fully non-interactively (scripts, tests,
  `schedule`'s automated invocations).
- When the user opts in, `init` reads the profile repo's real README,
  respects the user's original content, lets them choose where the managed
  dashboard section goes on first install, and installs
  `.github/workflows/tomax-dashboard.yml` — all behind an explicit final
  confirmation before anything is pushed.
- Fix the template-packaging gap that would otherwise make this feature
  non-functional for anyone who installed `tomax` via a built package rather
  than a git checkout.

## Decisions (locked)

- **Lives inside `init`, not a separate command.** `init`'s docstring is
  corrected — it is no longer network-free by default, only when the user
  (interactively or via flag) declines the dashboard step.
- **Opt-in, not automatic.** Dashboard registration only happens on an
  explicit "yes" (prompt or `--dashboard` flag). A "no" (or `--no-dashboard`)
  leaves `init` exactly as network-free as it is today.
- **Detect-and-skip on re-run.** If the profile README already contains
  `<!-- tomax:start -->`/`<!-- tomax:end -->` markers, `init` reports "already
  registered" and does not reposition or duplicate the section, regardless of
  `--insert-line`.
- **Position chosen once, via numbered lines.** The README is printed with a
  line number next to every line; the user picks the line to insert after
  (blank/omitted = end of file, `0` = very top). This only matters for the
  one-time first install — every later update (both `tomax render` locally
  and the profile repo's GitHub Action) replaces content between the existing
  markers in place via the current `update_readme`, so position never drifts
  after this one choice.
- **Explicit final confirmation before any push.** A preview of the exact
  lines being inserted (with surrounding context) and the intended commit
  message is shown; nothing is committed or pushed until the user confirms
  (or passes `--yes`).
- **Every prompt has a matching flag**, so a fully non-interactive run is
  possible:

  | Prompt | Flag | Default when omitted |
  |---|---|---|
  | Repo target | `--repo OWNER/REPO` | prompt |
  | "Add a dashboard?" | `--dashboard` / `--no-dashboard` | prompt |
  | Insertion line | `--insert-line N` (`-1`/omitted = end of file) | prompt (only reached if opted in and no markers exist yet) |
  | Final push confirmation | `--yes` / `-y` | prompt |

  `--insert-line` and `--yes` are simply ignored (no error) if the dashboard
  step is skipped or the README is already registered.
- **Reuse, don't duplicate, existing publish machinery.** The clone/open,
  `gh auth status` gate, and fetch→rebase→push→retry-on-non-fast-forward loop
  are the same ones `tomax publish` already uses — see the refactor below.
- **Targeted refactor: generalize `publish_device_partition`.** Its
  add/commit/fetch/rebase/push/retry body is not specific to a single device
  partition; extracting it lets the dashboard-registration flow reuse the
  exact same never-force-push, retry-bounded logic instead of re-implementing
  it.
- **Targeted fix: package the workflow template.** `templates/github-workflow.yml`
  currently lives at the repo root, which hatchling does not include in the
  built distribution (only `src/tomax/**` ships — confirmed by the existing
  `dashboard/prebuilt_ui/` non-`.py` assets being the only files packaged this
  way today). It moves to `src/tomax/templates/github-workflow.yml` as the
  single source of truth; `README.md`'s manual-copy instructions are updated
  to the new path.

## Architecture

### `src/tomax/render/markdown.py` — new insertion function

```python
def insert_dashboard_section(
    existing_readme: str,
    dashboard_markdown: str,
    *,
    after_line: int | None,  # None = end of file, 0 = very top
) -> str:
```

Splits `existing_readme` into lines, inserts `dashboard_markdown` (already
`MARKER_START`/`MARKER_END`-wrapped, from `render_dashboard_markdown()`)
after the given 1-indexed line number (or at the end when `None`), and
rejoins. Used only for the first-ever install. `update_readme` is unchanged
and keeps handling every subsequent update (replace between existing
markers).

### `src/tomax/publish/git.py` — generalized commit/push

```python
def commit_and_push(
    repo_dir: Path,
    *,
    paths: Sequence[str],
    branch: str,
    commit_message: str,
    on_progress: Callable[[str], None] | None = None,
    max_retries: int = 3,
) -> PublishResult:
```

Extracted from the current `publish_device_partition` body: stages exactly
`paths` (never a whole-tree `git add`), no-ops if nothing staged actually
changed, commits, then fetches/rebases/pushes with the existing bounded retry
and un-stranding logic on rebase conflict. Never force-pushes.
`publish_device_partition` becomes a one-line wrapper:
`commit_and_push(repo_dir, paths=[_device_partition(device_id)], ...)`.
Existing tests for `publish_device_partition` keep passing unchanged; new
tests target `commit_and_push` directly with a multi-path case.

### `src/tomax/commands/init.py` — dashboard registration

New functions, kept separate from the existing local-only `init()`:

```python
def readme_already_registered(readme_text: str) -> bool:
    ...  # True if MARKER_START/MARKER_END both present

def preview_insertion(
    readme_text: str, *, after_line: int | None, dashboard_markdown: str
) -> tuple[str, str]:
    """Returns (updated_readme, diff_preview_text)."""

def register_dashboard(
    *,
    repo_url: str,
    clone_dir: Path,
    branch: str = "main",
    after_line: int | None,
    gh_auth_check: Callable[[], str] | None = None,
    on_progress: Callable[[str], None] | None = None,
) -> DashboardRegistrationResult:  # {status: "already_registered"|"registered"|"skipped", commit_sha: str | None}
```

`register_dashboard` gh-auth-checks, clones/opens the repo, reads
`README.md`, returns early with `status="already_registered"` if markers
exist, otherwise builds the updated README + copies the packaged
`templates/github-workflow.yml` to `.github/workflows/tomax-dashboard.yml` in
the working tree, and calls `commit_and_push(paths=["README.md",
".github/workflows/tomax-dashboard.yml"], commit_message="chore: install
tomax usage dashboard")`.

The interactive line-picker, preview printing, and final confirm live in
`cli.py` (matching the existing convention that only `cli.py` calls
`typer.echo`/prompts — `commands/*.py` stays prompt-free and callback-driven,
same as `on_progress` in `publish`/`render`), calling into
`preview_insertion` to compute what to show before asking the user to
proceed.

### `src/tomax/cli.py` — `init` command rewrite

```python
@app.command()
def init(
    repo: str | None = typer.Option(None, "--repo", ...),
    dashboard: bool | None = typer.Option(None, "--dashboard/--no-dashboard", ...),
    insert_line: int = typer.Option(-1, "--insert-line", ...),  # -1 = end of file
    yes: bool = typer.Option(False, "--yes", "-y", ...),
) -> None:
```

Flow:

1. `repo = repo or typer.prompt(...)`, validate, save config (existing
   `init_command.init`, unchanged).
2. `wants_dashboard = dashboard if dashboard is not None else typer.confirm(...)`.
   If `False` → done.
3. Clone/open the profile repo, read `README.md`.
4. If `readme_already_registered(...)` → echo "already registered", done.
5. Resolve `after_line`: use `--insert-line` if given (and not the `-1`
   sentinel), else print the numbered README and `typer.prompt` for a line
   (blank → end of file).
6. Build the preview via `preview_insertion`, echo it.
7. `if not yes: typer.confirm(...)` before proceeding; abort cleanly (no
   push) on decline.
8. Call `register_dashboard(...)`, echo the result (commit SHA or
   already-registered).

## Data flow

```
tomax init
  │
  ├─ repo target ─────────────► config.json (local only, as today)
  │
  └─ dashboard opt-in?
        │ no  ──► done (no network)
        │ yes
        ▼
  gh auth status ──► clone_or_open(repo_url) ──► read README.md
        │
        ▼
  markers already present? ──yes──► "already registered", done
        │ no
        ▼
  print numbered README ──► user picks insertion line
        │
        ▼
  insert_dashboard_section(...) + copy templates/github-workflow.yml
        │
        ▼
  preview + final confirm ──abort──► done, nothing pushed
        │ confirm
        ▼
  commit_and_push(paths=[README.md, .github/workflows/tomax-dashboard.yml])
        │  (fetch → rebase → push, bounded retry, never force-push)
        ▼
  profile repo now has the workflow installed; next `tomax publish` from
  any device triggers the Action, which renders the aggregated dashboard
  into the same markers going forward.
```

## Error handling

- **Not authenticated** — same `GhAuthError` path `publish` already uses;
  `init` never attempts the dashboard step without a passing `gh auth
  status`.
- **Git failures (clone/fetch/rebase/push)** — same `GitCommandError` path,
  surfaced identically to `publish`'s existing CLI error handling. No new
  error types.
- **Declined at any point** (dashboard opt-out, or the final confirm) — clean
  exit, code 0, no network side effects beyond the read used to build the
  preview (the clone/fetch is read-only until the final confirm).
- **Malformed `--insert-line`** (out of range for the file) — clamp to the
  nearest valid bound (0 or end-of-file) rather than erroring; a bad line
  number is not worth failing the whole command over.

## Testing

- `render/markdown.py`: `insert_dashboard_section` — insert at top, middle,
  end; preserves all surrounding content; combined with `update_readme`,
  a second call after markers exist replaces in place rather than
  re-inserting.
- `publish/git.py`: `commit_and_push` with a multi-path case (README +
  workflow file staged together); existing `publish_device_partition` tests
  continue to pass against the thin wrapper.
- `commands/init.py`: `readme_already_registered` (present/absent/partial
  markers); `preview_insertion` output shape; `register_dashboard` with
  injected `gh_auth_check` and a local bare git repo fixture (no real
  network), covering: no markers → registers, markers present → skip,
  gh-auth failure → no clone attempted.
- `cli.py` / integration: exercise the fully-flagged non-interactive path
  (`--repo ... --dashboard --insert-line N --yes`) end to end against a local
  bare repo fixture; exercise `--no-dashboard` leaves `init` network-free
  (assert no subprocess git calls made for that branch).
- Packaging: a test (or a `uv build` + inspect step documented in the plan)
  confirming `src/tomax/templates/github-workflow.yml` is present in the
  built wheel, guarding against the packaging gap regressing silently.

## Out of scope

- Changing what the GitHub Action itself does once installed (still
  `templates/github-workflow.yml`'s existing render logic, just relocated).
- Repositioning the dashboard section after the first install (locked
  decision: detect-and-skip).
- Any change to `tomax publish`'s own behavior — it still only ever touches
  `data/v1/devices/<device-id>/`.
- Multi-repo or multi-branch dashboard registration in one `init` run.

## Risks

- **Line-number drift** — if the user edits their README between the printed
  preview and confirming, the line they picked could shift. Mitigated by
  doing the read, prompt, and preview all within one `init` invocation
  against one cloned snapshot; no long-lived state to go stale.
- **Refactor blast radius** — `commit_and_push` extraction touches code
  `publish` depends on in production; existing `publish_device_partition`
  tests must keep passing unchanged as a regression guard, not just the new
  tests.
- **Packaging fix could still miss edge cases** (e.g. sdist vs wheel
  differences) — worth an explicit build-and-inspect check in the
  implementation plan rather than assuming parity with `prebuilt_ui/`.
