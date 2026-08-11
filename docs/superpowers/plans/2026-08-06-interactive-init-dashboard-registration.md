# Interactive `init` + README Dashboard Registration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `tomax init` interactive (with flag overrides for every prompt) and let it, on opt-in, register the usage dashboard in the profile repo's real README — preserving existing content, letting the user choose the insertion point once, and installing the GitHub Action workflow that keeps the dashboard updated afterward.

**Architecture:** `init` gains a second, opt-in phase reusing `publish`'s existing `gh auth status` gate and `clone_or_open`/fetch-rebase-push machinery (generalized from `publish_device_partition` into a path-agnostic `commit_and_push`). A new `insert_dashboard_section` in `render/markdown.py` handles the one-time positioned insert; every later update (local `render` or the profile repo's Action) keeps using the existing marker-replace `update_readme`. The packaged workflow template moves into `src/tomax/templates/` so it ships with the installed package.

**Tech Stack:** Python 3.11, Typer (CLI prompts/flags), stdlib `subprocess`/`pathlib`, pytest against local bare git repos (no real GitHub network calls in tests).

## Global Constraints

- Every new Python module starts with `from __future__ import annotations`.
- `commands/*.py` stays prompt-free and `typer`-free — only `cli.py` calls `typer.echo`/`typer.prompt`/`typer.confirm`. Command-layer functions communicate via an optional `on_progress: Callable[[str], None] | None` callback, matching the existing `on_collected` (`dashboard.py`) and `on_progress` (`publish.py`, `render.py`) pattern.
- `tomax publish` must keep behaving exactly as before — `commit_and_push` is a refactor extraction, not a behavior change, and existing `publish_device_partition` tests must keep passing unchanged.
- Never force-push. Every commit/push path fetches + rebases + retries bounded, same as today.
- No new dependencies.
- README.md and AGENTS.md get updated in the same change as the feature they document (existing repo convention).

---

### Task 1: Package the workflow template inside `src/tomax/`

**Files:**
- Move: `templates/github-workflow.yml` → `src/tomax/templates/github-workflow.yml`
- Modify: `README.md:214-219`
- Test: `tests/test_workflow_template.py` (existing file — update its path assertions)
- Create: `tests/test_packaged_workflow_template.py`

**Interfaces:**
- Produces: `src/tomax/templates/github-workflow.yml` on disk, resolvable at runtime via `Path(__file__).resolve().parent.parent / "templates" / "github-workflow.yml"` from any module under `src/tomax/commands/` or `src/tomax/` itself. Task 4 depends on this exact path.

- [ ] **Step 1: Check the existing template test's current assertions**

Run: `cat tests/test_workflow_template.py`

Note whatever path it currently asserts against (`templates/github-workflow.yml`) — Step 4 will update it.

- [ ] **Step 2: Move the template file**

```bash
mkdir -p src/tomax/templates
git mv templates/github-workflow.yml src/tomax/templates/github-workflow.yml
rmdir templates 2>/dev/null || true
```

- [ ] **Step 3: Update `README.md`'s install instructions**

Find (around line 214-219):

```markdown
To generate an aggregated profile dashboard, copy
[`templates/github-workflow.yml`](templates/github-workflow.yml) into the
profile repository as `.github/workflows/tomax-dashboard.yml`. The
workflow validates device/day records and updates the managed README section
and chart assets only when data beneath `data/v1/**` changes. Review and enable
that workflow only when you are ready to publish sanitized aggregates.
```

Replace with:

```markdown
To generate an aggregated profile dashboard, run `tomax init` and answer
"yes" when asked to register a usage dashboard — it walks you through
choosing where the dashboard section goes in your README, previews the
change, and installs
[`.github/workflows/tomax-dashboard.yml`](src/tomax/templates/github-workflow.yml)
for you (see `tomax init --help` for the non-interactive flags). The workflow
validates device/day records and updates the managed README section and
dashboard image only when data beneath `data/v1/**` changes.
```

- [ ] **Step 4: Update the existing workflow-template test's path**

In `tests/test_workflow_template.py`, the only reference to the old location is line 17:

```python
WORKFLOW_PATH = REPO_ROOT / "templates" / "github-workflow.yml"
```

Change it to:

```python
WORKFLOW_PATH = REPO_ROOT / "src" / "tomax" / "templates" / "github-workflow.yml"
```

Every other reference in that file goes through the `WORKFLOW_PATH` constant, so no other line needs to change.

- [ ] **Step 5: Run the updated test to confirm it still passes**

Run: `uv run pytest tests/test_workflow_template.py -v`
Expected: PASS

- [ ] **Step 6: Write the packaging regression test**

```python
"""Guards against the workflow template being excluded from the built package.

`tomax init`'s dashboard registration reads this file from inside the
installed package at runtime — a user who installed via pip/uv from a
built wheel has no `templates/` source checkout to fall back on, so if
packaging silently drops this file, dashboard registration breaks for
every non-source install.
"""

from __future__ import annotations

import subprocess
import sys
import zipfile
from pathlib import Path


def test_workflow_template_is_included_in_the_built_wheel(tmp_path) -> None:
    repo_root = Path(__file__).resolve().parent.parent
    out_dir = tmp_path / "dist"
    subprocess.run(
        [sys.executable, "-m", "uv", "build", "--wheel", "-o", str(out_dir)],
        cwd=repo_root,
        check=True,
        capture_output=True,
    )
    wheel_path = next(out_dir.glob("*.whl"))
    with zipfile.ZipFile(wheel_path) as wheel:
        names = wheel.namelist()
    assert "tomax/templates/github-workflow.yml" in names
```

- [ ] **Step 7: Run it to verify it passes**

Run: `uv run pytest tests/test_packaged_workflow_template.py -v`
Expected: PASS (may take several seconds — it runs a real `uv build`)

- [ ] **Step 8: Commit**

```bash
git add src/tomax/templates/github-workflow.yml README.md tests/test_workflow_template.py tests/test_packaged_workflow_template.py
git commit -m "fix: package the dashboard workflow template inside src/tomax

The template lived at the repo root, which hatchling's default src-layout
build never includes — an installed (non-source-checkout) tomax had no
way to read it. Moves it to src/tomax/templates/ alongside the existing
dashboard/prebuilt_ui/ packaged-asset precedent, and adds a build-and-
inspect regression test."
```

---

### Task 2: `render/markdown.py` — positioned first-install insertion

**Files:**
- Modify: `src/tomax/render/markdown.py`
- Test: `tests/render/test_markdown.py`

**Interfaces:**
- Produces: `insert_dashboard_section(existing_readme: str, dashboard_markdown: str, *, after_line: int | None) -> str`. `after_line=None` means end of file; `after_line=0` means the very top; any other value is the 1-indexed line to insert after, clamped into `[0, len(existing_readme.splitlines())]` rather than raising. Task 4 (`preview_insertion`) calls this directly.
- Consumes: nothing new — `MARKER_START`/`MARKER_END` already exist in this file.

- [ ] **Step 1: Write the failing tests**

Add to `tests/render/test_markdown.py`:

```python
from tomax.render.markdown import insert_dashboard_section


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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/render/test_markdown.py -v`
Expected: FAIL with `ImportError: cannot import name 'insert_dashboard_section'`

- [ ] **Step 3: Implement `insert_dashboard_section`**

Add to `src/tomax/render/markdown.py`, after `render_dashboard_markdown`:

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/render/test_markdown.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/tomax/render/markdown.py tests/render/test_markdown.py
git commit -m "feat(render): add positioned first-install README insertion

insert_dashboard_section lets a caller choose where the managed dashboard
section goes on first install; update_readme continues to own every
later in-place replacement."
```

---

### Task 3: `publish/git.py` — generalize commit/push into `commit_and_push`

**Files:**
- Modify: `src/tomax/publish/git.py`
- Test: `tests/publish/test_git.py`

**Interfaces:**
- Produces: `commit_and_push(repo_dir: Path, *, paths: Sequence[str], branch: str, commit_message: str, max_retries: int = 3, on_progress: Callable[[str], None] | None = None) -> PublishResult`. Task 4 depends on this exact signature and on `PublishResult(pushed, commit_sha, attempts)` being unchanged.
- Consumes: nothing new. `publish_device_partition`'s existing signature and behavior are unchanged from the outside.

- [ ] **Step 1: Write the failing test for the generalized function**

Add to `tests/publish/test_git.py`:

```python
from tomax.publish.git import commit_and_push


def test_commit_and_push_stages_multiple_paths_together(tmp_path) -> None:
    origin = _init_bare_origin(tmp_path)
    repo_dir = _clone(origin, tmp_path / "clone")
    (repo_dir / "README.md").write_text("updated\n", encoding="utf-8")
    workflow_dir = repo_dir / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "tomax-dashboard.yml").write_text("name: x\n", encoding="utf-8")

    result = commit_and_push(
        repo_dir,
        paths=["README.md", ".github/workflows/tomax-dashboard.yml"],
        branch="main",
        commit_message="chore: install tomax usage dashboard",
    )

    assert result.pushed is True
    committed_files = _run(repo_dir, "show", "--name-only", "--pretty=format:", "HEAD").split()
    assert "README.md" in committed_files
    assert ".github/workflows/tomax-dashboard.yml" in committed_files


def test_commit_and_push_is_a_no_op_when_none_of_the_paths_exist(tmp_path) -> None:
    origin = _init_bare_origin(tmp_path)
    repo_dir = _clone(origin, tmp_path / "clone")

    result = commit_and_push(
        repo_dir, paths=["does-not-exist.txt"], branch="main", commit_message="chore: x"
    )

    assert result == PublishResult(pushed=False, commit_sha=None, attempts=0)


def test_publish_device_partition_still_behaves_identically(tmp_path) -> None:
    origin = _init_bare_origin(tmp_path)
    repo_dir = _clone(origin, tmp_path / "clone")
    _write_device_file(repo_dir, "device-a", "2026-07-10.json", '{"date": "2026-07-10"}')

    result = publish_device_partition(
        repo_dir, device_id="device-a", branch="main", commit_message="chore: update device-a"
    )

    assert result.pushed is True
    assert result.commit_sha is not None
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `uv run pytest tests/publish/test_git.py -v`
Expected: `commit_and_push` tests FAIL with `ImportError`; the `publish_device_partition` regression test should already PASS (it's testing existing behavior) — confirm it does before refactoring, so any later failure is attributable to the refactor.

- [ ] **Step 3: Extract `commit_and_push` and rewrite `publish_device_partition` as a thin wrapper**

Replace the whole `publish_device_partition` function in `src/tomax/publish/git.py` with:

```python
def commit_and_push(
    repo_dir: Path,
    *,
    paths: Sequence[str],
    branch: str,
    commit_message: str,
    max_retries: int = 3,
    on_progress: Callable[[str], None] | None = None,
) -> PublishResult:
    """Commit and push exactly ``paths``, never force-pushing.

    Returns ``pushed=False`` with no commit if none of ``paths`` exist on
    disk, or none of the ones that do have staged changes. Fetches and
    rebases onto the remote branch before every push attempt; on a
    non-fast-forward rejection (someone else pushed first), re-fetches,
    re-rebases, and retries up to ``max_retries`` times.
    """
    if max_retries < 1:
        raise ValueError("max_retries must be at least 1")
    progress = on_progress or (lambda _message: None)

    existing_paths = [path for path in paths if (repo_dir / path).exists()]
    if not existing_paths:
        # Nothing was staged on disk to begin with — `git add` on a
        # pathspec that matches nothing would otherwise error.
        return PublishResult(pushed=False, commit_sha=None, attempts=0)

    _run("add", "--", *existing_paths, cwd=repo_dir)

    if not any(_has_staged_changes(repo_dir, path) for path in existing_paths):
        return PublishResult(pushed=False, commit_sha=None, attempts=0)

    _run("commit", "-m", commit_message, cwd=repo_dir)

    attempts = 0
    while attempts < max_retries:
        attempts += 1
        progress(f"fetch+rebase onto origin/{branch} (attempt {attempts}/{max_retries})")
        _run("fetch", "origin", branch, cwd=repo_dir)
        try:
            _run("rebase", f"origin/{branch}", cwd=repo_dir)
        except GitCommandError:
            _run("rebase", "--abort", cwd=repo_dir)
            # Un-strand the commit just made: put its content back into the
            # index rather than leaving an unpushed commit that a later
            # call's staged-changes check can no longer see as "changed",
            # which would otherwise make a retry silently report nothing to
            # publish instead of surfacing the unresolved conflict again.
            _run("reset", "--soft", "HEAD~1", cwd=repo_dir)
            raise

        push = subprocess.run(
            ["git", "push", "origin", f"HEAD:{branch}"],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            check=False,
        )
        if push.returncode == 0:
            # Re-derive the SHA post-push: a rebase rewrites the commit
            # whenever origin actually advanced, so a SHA captured before
            # the loop can point at a commit that was never pushed.
            commit_sha = _run("rev-parse", "HEAD", cwd=repo_dir).strip()
            return PublishResult(pushed=True, commit_sha=commit_sha, attempts=attempts)

        if not any(marker in push.stderr for marker in _NON_FAST_FORWARD_MARKERS):
            raise GitCommandError(("push",), push.returncode, push.stderr)

        progress("push rejected (non-fast-forward) — another push landed first, retrying")

    raise GitCommandError(
        ("push",), 1, f"exceeded {max_retries} retries without a fast-forward push"
    )


def publish_device_partition(
    repo_dir: Path,
    *,
    device_id: str,
    branch: str,
    commit_message: str,
    max_retries: int = 3,
    on_progress: Callable[[str], None] | None = None,
) -> PublishResult:
    """Commit and push only ``device_id``'s own data partition, never force-pushing.

    Assumes the caller has already written that device's daily record
    files into ``repo_dir/data/v1/devices/<device_id>/``.
    """
    return commit_and_push(
        repo_dir,
        paths=[_device_partition(device_id)],
        branch=branch,
        commit_message=commit_message,
        max_retries=max_retries,
        on_progress=on_progress,
    )
```

Add `Sequence` to the existing `from collections.abc import Callable` import line (change it to `from collections.abc import Callable, Sequence`).

- [ ] **Step 4: Run the full git test module to verify everything passes**

Run: `uv run pytest tests/publish/test_git.py -v`
Expected: PASS — all previously-passing `publish_device_partition` tests (no-op, basic publish, scoping, conflict recovery, never-force-push, retry exhaustion, SHA-after-rebase) plus the three new `commit_and_push` tests.

- [ ] **Step 5: Run the full test suite to check nothing else broke**

Run: `uv run pytest -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/tomax/publish/git.py tests/publish/test_git.py
git commit -m "refactor(publish): extract commit_and_push from publish_device_partition

The add/commit/fetch/rebase/push/retry body wasn't specific to a single
device partition. publish_device_partition becomes a thin wrapper;
behavior is unchanged (regression-tested), and the dashboard-registration
flow in the next commit reuses commit_and_push directly instead of
re-implementing the retry loop."
```

---

### Task 4: `commands/init.py` — dashboard registration functions

**Files:**
- Modify: `src/tomax/commands/init.py`
- Test: `tests/commands/test_init.py`

**Interfaces:**
- Consumes: `commit_and_push`, `clone_or_open` from `tomax.publish.git`; `render_dashboard_markdown`, `insert_dashboard_section`, `MARKER_START`, `MARKER_END` from `tomax.render.markdown`.
- Produces:
  - `readme_already_registered(readme_text: str) -> bool`
  - `numbered_readme_lines(readme_text: str) -> list[str]`
  - `preview_insertion(readme_text: str, *, after_line: int | None, dashboard_markdown: str) -> tuple[str, str]` (returns `(updated_readme_text, preview_text)`)
  - `DashboardRegistrationResult` — frozen dataclass with `status: str` (`"already_registered"` or `"registered"`) and `commit_sha: str | None`
  - `register_dashboard(repo_dir: Path, *, branch: str = "main", after_line: int | None, on_progress: Callable[[str], None] | None = None) -> DashboardRegistrationResult`
  - `WORKFLOW_RELATIVE_PATH = ".github/workflows/tomax-dashboard.yml"` (module-level constant; Task 5's `cli.py` echoes this path to the user)

  Task 5 (`cli.py`) calls `clone_or_open` and does its own `gh auth` check *before* calling `register_dashboard` — `register_dashboard` takes an already-open `repo_dir` and has no GitHub-auth or cloning concerns of its own, matching `clone_or_open`'s own "no opinion on GitHub authentication" boundary.

- [ ] **Step 1: Write the failing tests**

Create `tests/commands/test_init.py` additions (append to the existing file):

```python
import subprocess

from tomax.commands.init import (
    DashboardRegistrationResult,
    numbered_readme_lines,
    preview_insertion,
    readme_already_registered,
    register_dashboard,
)
from tomax.publish.git import clone_or_open
from tomax.render.markdown import MARKER_END, MARKER_START, render_dashboard_markdown


def _run(cwd, *args):
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)
    return result.stdout


def _init_bare_origin(tmp_path, *, readme_text="seed\n"):
    seed = tmp_path / "seed"
    seed.mkdir()
    _run(seed, "init", "-b", "main")
    _run(seed, "config", "user.email", "test@example.com")
    _run(seed, "config", "user.name", "Test Author")
    (seed / "README.md").write_text(readme_text, encoding="utf-8")
    _run(seed, "add", "README.md")
    _run(seed, "commit", "-m", "seed")
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "clone", "--bare", str(seed), str(bare)], check=True, capture_output=True)
    return bare


def test_readme_already_registered_is_false_for_plain_readme():
    assert readme_already_registered("# Title\n\nHello\n") is False


def test_readme_already_registered_is_true_once_markers_present():
    text = f"# Title\n\n{MARKER_START}\nold\n{MARKER_END}\n"
    assert readme_already_registered(text) is True


def test_numbered_readme_lines_prefixes_each_line():
    result = numbered_readme_lines("line1\nline2\n")
    assert result == ["1  line1", "2  line2"]


def test_numbered_readme_lines_is_empty_for_empty_readme():
    assert numbered_readme_lines("") == []


def test_preview_insertion_returns_updated_readme_and_a_scoped_preview():
    existing = "line1\nline2\nline3\n"
    section = render_dashboard_markdown()

    updated, preview = preview_insertion(existing, after_line=1, dashboard_markdown=section)

    assert MARKER_START in updated and MARKER_END in updated
    assert "line1" in preview and "line3" in preview
    # The preview must not dump the whole README when it's long.
    assert preview.count("\n") < updated.count("\n")


def test_register_dashboard_writes_readme_and_workflow_then_pushes(tmp_path):
    origin = _init_bare_origin(tmp_path)
    repo_dir = clone_or_open(str(origin), tmp_path / "clone", branch="main")
    _run(repo_dir, "config", "user.email", "test@example.com")
    _run(repo_dir, "config", "user.name", "Test Author")

    result = register_dashboard(repo_dir, branch="main", after_line=None)

    assert result.status == "registered"
    assert result.commit_sha is not None

    verify_dir = tmp_path / "verify"
    clone_or_open(str(origin), verify_dir, branch="main")
    readme = (verify_dir / "README.md").read_text(encoding="utf-8")
    assert MARKER_START in readme and "seed" in readme
    assert (verify_dir / ".github" / "workflows" / "tomax-dashboard.yml").exists()


def test_register_dashboard_skips_when_already_registered(tmp_path):
    existing = f"# Title\n\n{MARKER_START}\nold\n{MARKER_END}\n"
    origin = _init_bare_origin(tmp_path, readme_text=existing)
    repo_dir = clone_or_open(str(origin), tmp_path / "clone", branch="main")
    _run(repo_dir, "config", "user.email", "test@example.com")
    _run(repo_dir, "config", "user.name", "Test Author")

    result = register_dashboard(repo_dir, branch="main", after_line=None)

    assert result == DashboardRegistrationResult(status="already_registered", commit_sha=None)
    assert (repo_dir / ".github" / "workflows" / "tomax-dashboard.yml").exists() is False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/commands/test_init.py -v`
Expected: FAIL with `ImportError` for the new names.

- [ ] **Step 3: Implement the new functions in `src/tomax/commands/init.py`**

Replace the whole file with:

```python
"""Local, network-free repo-target setup, plus opt-in dashboard registration.

``init(...)`` itself never touches GitHub or the network. ``register_dashboard``
does — it is only ever called after the caller (``cli.py``) has explicitly
confirmed the user wants it and has already passed a ``gh auth status``
check, the same gate ``tomax publish`` uses.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from tomax.config import AppConfig, get_or_create_device_id, load_config, save_config
from tomax.publish.git import commit_and_push
from tomax.render.markdown import (
    MARKER_END,
    MARKER_START,
    insert_dashboard_section,
    render_dashboard_markdown,
)

WORKFLOW_RELATIVE_PATH = ".github/workflows/tomax-dashboard.yml"
_WORKFLOW_TEMPLATE_PATH = (
    Path(__file__).resolve().parent.parent / "templates" / "github-workflow.yml"
)

_PREVIEW_CONTEXT_LINES = 2


def init(repo: str, *, config_path: Path, ledger_path: Path) -> AppConfig:
    """Set the profile repo target in local config, creating the device id if needed."""
    config = replace(load_config(config_path), repo_target=repo)
    save_config(config_path, config)
    get_or_create_device_id(ledger_path)
    return config


def readme_already_registered(readme_text: str) -> bool:
    """Whether the managed dashboard markers are already present."""
    return MARKER_START in readme_text and MARKER_END in readme_text


def numbered_readme_lines(readme_text: str) -> list[str]:
    """Render each README line prefixed with its 1-indexed line number."""
    lines = readme_text.splitlines()
    width = len(str(len(lines))) if lines else 1
    return [f"{index:>{width}}  {line}" for index, line in enumerate(lines, start=1)]


def preview_insertion(
    readme_text: str, *, after_line: int | None, dashboard_markdown: str
) -> tuple[str, str]:
    """Compute the updated README and a short preview scoped to the insertion.

    Returns ``(updated_readme, preview_text)`` — the preview shows the
    inserted section plus a couple of lines of surrounding context, not the
    whole file, so it stays readable even for a long README.
    """
    updated = insert_dashboard_section(readme_text, dashboard_markdown, after_line=after_line)
    lines = updated.splitlines()
    start = lines.index(MARKER_START)
    end = lines.index(MARKER_END)
    before = lines[max(0, start - _PREVIEW_CONTEXT_LINES) : start]
    after = lines[end + 1 : end + 1 + _PREVIEW_CONTEXT_LINES]
    preview = "\n".join([*before, *lines[start : end + 1], *after])
    return updated, preview


@dataclass(frozen=True, slots=True)
class DashboardRegistrationResult:
    """The outcome of one ``register_dashboard`` call."""

    status: str  # "already_registered" | "registered"
    commit_sha: str | None


def register_dashboard(
    repo_dir: Path,
    *,
    branch: str = "main",
    after_line: int | None,
    on_progress: Callable[[str], None] | None = None,
) -> DashboardRegistrationResult:
    """Write the managed dashboard section + workflow file and push, once.

    Assumes ``repo_dir`` is an already-open clone of the profile repo (see
    ``tomax.publish.git.clone_or_open``) and that the caller has already
    gh-auth-checked. Returns ``status="already_registered"`` and does
    nothing further if the README already has the managed markers.
    """
    progress = on_progress or (lambda _message: None)

    readme_path = repo_dir / "README.md"
    readme_text = readme_path.read_text(encoding="utf-8") if readme_path.exists() else ""
    if readme_already_registered(readme_text):
        return DashboardRegistrationResult(status="already_registered", commit_sha=None)

    progress("writing dashboard section and workflow file")
    updated_readme, _preview = preview_insertion(
        readme_text, after_line=after_line, dashboard_markdown=render_dashboard_markdown()
    )
    readme_path.write_text(updated_readme, encoding="utf-8")

    workflow_path = repo_dir / WORKFLOW_RELATIVE_PATH
    workflow_path.parent.mkdir(parents=True, exist_ok=True)
    workflow_path.write_text(
        _WORKFLOW_TEMPLATE_PATH.read_text(encoding="utf-8"), encoding="utf-8"
    )

    progress("committing and pushing dashboard registration")
    result = commit_and_push(
        repo_dir,
        paths=["README.md", WORKFLOW_RELATIVE_PATH],
        branch=branch,
        commit_message="chore: install tomax usage dashboard",
        on_progress=on_progress,
    )
    return DashboardRegistrationResult(status="registered", commit_sha=result.commit_sha)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/commands/test_init.py -v`
Expected: PASS

- [ ] **Step 5: Run the full test suite**

Run: `uv run pytest -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/tomax/commands/init.py tests/commands/test_init.py
git commit -m "feat(init): add dashboard registration functions

readme_already_registered, numbered_readme_lines, preview_insertion, and
register_dashboard give cli.py everything it needs to walk a user through
registering the usage dashboard in their profile README without
duplicating publish's clone/commit/push machinery."
```

---

### Task 5: `cli.py` — interactive `init` with flag overrides

**Files:**
- Modify: `src/tomax/cli.py`
- Modify: `AGENTS.md:32`
- Modify: `README.md:195-199` (the `init`/`publish` quick-start snippet)
- Test: `tests/test_cli_init.py` (new)

**Interfaces:**
- Consumes: `init_command.init`, `init_command.readme_already_registered`, `init_command.numbered_readme_lines`, `init_command.preview_insertion`, `init_command.register_dashboard`, `init_command.WORKFLOW_RELATIVE_PATH` (Task 4); `check_gh_auth` (already imported indirectly via `publish_command`, but needs a direct import since `init` now calls it too); `clone_or_open` (Task 3, already importable from `tomax.publish.git`, already imported for `GitCommandError` — add `clone_or_open` to that import); `render_dashboard_markdown` from `tomax.render.markdown`.

- [ ] **Step 1: Write the failing CLI-level tests**

Create `tests/test_cli_init.py`:

```python
"""End-to-end tests for the interactive `tomax init` CLI command.

Every test runs against a local bare git "origin" and injects
`check_gh_auth`/prompts via monkeypatching — never a real GitHub remote or
a real gh CLI login.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from typer.testing import CliRunner

import tomax.cli as cli_module
from tomax.cli import app
from tomax.publish.git import clone_or_open
from tomax.render.markdown import MARKER_START

runner = CliRunner()


def _run(cwd: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)
    return result.stdout


def _init_bare_origin(tmp_path: Path) -> Path:
    seed = tmp_path / "seed"
    seed.mkdir()
    _run(seed, "init", "-b", "main")
    _run(seed, "config", "user.email", "test@example.com")
    _run(seed, "config", "user.name", "Test Author")
    (seed / "README.md").write_text("# My Profile\n\nHello.\n", encoding="utf-8")
    _run(seed, "add", "README.md")
    _run(seed, "commit", "-m", "seed")
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "clone", "--bare", str(seed), str(bare)], check=True, capture_output=True)
    return bare


def _patch_env(monkeypatch, tmp_path, *, repo_target="owner/repo", origin=None):
    monkeypatch.setattr(
        cli_module, "config_file_path", lambda: tmp_path / "config.json"
    )
    monkeypatch.setattr(
        cli_module, "ledger_file_path", lambda: tmp_path / "data" / "ledger.sqlite3"
    )
    monkeypatch.setattr(cli_module, "check_gh_auth", lambda: "ok")
    if origin is not None:
        monkeypatch.setattr(
            cli_module, "clone_or_open",
            lambda repo_url, clone_dir, branch: clone_or_open(str(origin), clone_dir, branch=branch),
        )


def test_init_without_dashboard_flag_never_calls_gh_auth(tmp_path, monkeypatch):
    _patch_env(monkeypatch, tmp_path)
    calls = []
    monkeypatch.setattr(cli_module, "check_gh_auth", lambda: calls.append(1))

    result = runner.invoke(app, ["init", "--repo", "owner/repo", "--no-dashboard"])

    assert result.exit_code == 0
    assert calls == []


def test_init_with_dashboard_flags_registers_non_interactively(tmp_path, monkeypatch):
    origin = _init_bare_origin(tmp_path)
    _patch_env(monkeypatch, tmp_path, origin=origin)

    result = runner.invoke(
        app,
        ["init", "--repo", "owner/repo", "--dashboard", "--insert-line", "2", "--yes"],
    )

    assert result.exit_code == 0, result.output
    assert "registered" in result.output

    verify_dir = tmp_path / "verify"
    clone_or_open(str(origin), verify_dir, branch="main")
    readme = (verify_dir / "README.md").read_text(encoding="utf-8")
    assert MARKER_START in readme
    assert (verify_dir / ".github" / "workflows" / "tomax-dashboard.yml").exists()


def test_init_reports_already_registered_without_reprompting(tmp_path, monkeypatch):
    origin = _init_bare_origin(tmp_path)
    seed_clone = clone_or_open(str(origin), tmp_path / "seed-clone", branch="main")
    _run(seed_clone, "config", "user.email", "test@example.com")
    _run(seed_clone, "config", "user.name", "Test Author")
    from tomax.commands.init import register_dashboard

    register_dashboard(seed_clone, branch="main", after_line=None)
    _patch_env(monkeypatch, tmp_path, origin=origin)

    result = runner.invoke(
        app, ["init", "--repo", "owner/repo", "--dashboard", "--yes"]
    )

    assert result.exit_code == 0, result.output
    assert "already registered" in result.output
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_cli_init.py -v`
Expected: FAIL — either an `AttributeError` on `cli_module.check_gh_auth`/`clone_or_open` (not yet imported into `cli.py`) or `init` still requiring `--repo` as a mandatory option without a `--dashboard` flag existing yet.

- [ ] **Step 3: Rewrite the `init` command in `src/tomax/cli.py`**

First, update the import block (around line 25-36) — add `check_gh_auth` to the existing `from tomax.commands.publish import GhAuthError` line, add `clone_or_open` to the existing `from tomax.publish.git import GitCommandError` line, and add a new import for `render_dashboard_markdown`:

```python
from tomax.commands.publish import GhAuthError, check_gh_auth
```

```python
from tomax.publish.git import GitCommandError, clone_or_open
```

Add, near the other `tomax.render`/`tomax.privacy` imports:

```python
from tomax.render.markdown import render_dashboard_markdown
```

Then replace the existing `init` command (currently):

```python
@app.command()
def init(
    repo: str = typer.Option(..., "--repo", help="GitHub profile repo in OWNER/REPO form."),
) -> None:
    """Set the target GitHub profile repository for this install (local only, no network)."""
    config = init_command.init(repo, config_path=config_file_path(), ledger_path=ledger_file_path())
    typer.echo(f"tomax: repo target set to {config.repo_target}")
```

with:

```python
@app.command()
def init(
    repo: str | None = typer.Option(
        None, "--repo", help="GitHub profile repo in OWNER/REPO form. Prompted for if omitted."
    ),
    dashboard: bool | None = typer.Option(
        None,
        "--dashboard/--no-dashboard",
        help="Register the usage dashboard in the profile repo's README. Prompted for if omitted.",
    ),
    insert_line: int = typer.Option(
        -1,
        "--insert-line",
        help="README line to insert the dashboard section after, on first install only "
        "(-1 = prompt interactively, 0 = very top).",
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Skip the final push confirmation for dashboard registration."
    ),
) -> None:
    """Set the profile repo target, and optionally register the usage dashboard in its README."""
    resolved_repo = repo or typer.prompt("GitHub profile repo (OWNER/REPO)")
    config = init_command.init(
        resolved_repo, config_path=config_file_path(), ledger_path=ledger_file_path()
    )
    typer.echo(f"tomax: repo target set to {config.repo_target}")

    wants_dashboard = (
        dashboard
        if dashboard is not None
        else typer.confirm("Add a usage dashboard to this repo's README?")
    )
    if not wants_dashboard:
        return

    clone_dir = ledger_file_path().parent / "profile-repo"
    repo_url = f"https://github.com/{config.repo_target}.git"

    try:
        typer.echo("tomax: checking gh auth status")
        check_gh_auth()
        typer.echo(f"tomax: cloning/opening profile repo at {clone_dir}")
        repo_dir = clone_or_open(repo_url, clone_dir, branch="main")
    except GhAuthError as error:
        typer.echo(f"tomax: gh auth check failed: {error}")
        raise typer.Exit(code=1) from error
    except GitCommandError as error:
        typer.echo(f"tomax: could not open the profile repo: {error}")
        raise typer.Exit(code=1) from error

    readme_path = repo_dir / "README.md"
    readme_text = readme_path.read_text(encoding="utf-8") if readme_path.exists() else ""

    if init_command.readme_already_registered(readme_text):
        typer.echo("tomax: dashboard already registered — nothing to do")
        return

    if insert_line >= 0:
        after_line: int | None = insert_line
    else:
        for line in init_command.numbered_readme_lines(readme_text):
            typer.echo(line)
        chosen = typer.prompt(
            "Insert the dashboard section after which line? (blank = end of file)",
            default=-1,
            show_default=False,
        )
        after_line = None if chosen < 0 else chosen

    _updated_readme, preview = init_command.preview_insertion(
        readme_text, after_line=after_line, dashboard_markdown=render_dashboard_markdown()
    )
    typer.echo("tomax: about to insert:")
    typer.echo(preview)
    typer.echo(
        f"tomax: will commit README.md + {init_command.WORKFLOW_RELATIVE_PATH} and push "
        f"to {config.repo_target}"
    )
    if not yes and not typer.confirm("Proceed?"):
        typer.echo("tomax: dashboard registration cancelled — nothing pushed")
        return

    result = init_command.register_dashboard(
        repo_dir,
        after_line=after_line,
        on_progress=lambda message: typer.echo(f"tomax: {message}"),
    )
    if result.status == "already_registered":
        typer.echo("tomax: dashboard already registered — nothing to do")
    else:
        typer.echo(f"tomax: dashboard registered (commit {result.commit_sha})")
```

- [ ] **Step 4: Run the CLI tests to verify they pass**

Run: `uv run pytest tests/test_cli_init.py -v`
Expected: PASS

- [ ] **Step 5: Run the full test suite**

Run: `uv run pytest -q`
Expected: PASS

- [ ] **Step 6: Run the linter**

Run: `uv run ruff check .`
Expected: All checks passed.

- [ ] **Step 7: Update `AGENTS.md`'s CLI command table**

Find (`AGENTS.md:32`):

```markdown
| `init` | Local-only: record `OWNER/REPO` target, ensure device ID. |
```

Replace with:

```markdown
| `init` | Interactive: record `OWNER/REPO` target, ensure device ID, and optionally register the usage dashboard in the profile repo's README (`gh auth status`-gated, only on explicit opt-in). Every prompt has a flag override (`--repo`, `--dashboard`/`--no-dashboard`, `--insert-line`, `--yes`) for non-interactive use. |
```

- [ ] **Step 8: Update `README.md`'s quick-start snippet**

Find (around line 195-199):

```markdown
uv run tomax init --repo OWNER/PROFILE-REPO
uv run tomax collect
uv run tomax render --output-dir ./tomax-preview
uv run tomax publish
```

Replace with:

```markdown
uv run tomax init
uv run tomax collect
uv run tomax render --output-dir ./tomax-preview
uv run tomax publish
```

`init` prompts for the repo target and, if you'd like, walks you through
registering the usage dashboard in that repo's README — or run it
non-interactively with `tomax init --repo OWNER/PROFILE-REPO --dashboard
--insert-line N --yes` (see `tomax init --help`).
```

- [ ] **Step 9: Commit**

```bash
git add src/tomax/cli.py AGENTS.md README.md tests/test_cli_init.py
git commit -m "feat(cli): make init interactive with flag overrides for every prompt

init now prompts for the repo target and, on opt-in, walks the user
through registering the usage dashboard in their profile README: shows
the README with line numbers, previews the exact insertion, confirms
before pushing, then installs the dashboard GitHub Action workflow.
--repo, --dashboard/--no-dashboard, --insert-line, and --yes let every
step run non-interactively."
```

---

## Self-Review Notes

- **Spec coverage:** interactive repo-target prompt (Task 5), dashboard opt-in prompt + flag (Task 5), README fetch preserving original content (Task 4/5 — read, never truncate/rewrite outside the markers), numbered-line position picker (Task 5), preview + final confirm before push (Task 5), workflow file installation (Task 4), detect-and-skip on re-run (Task 4's `readme_already_registered` + Task 5's early return), `commit_and_push` reuse (Task 3), packaging fix (Task 1) — all covered.
- **Type consistency:** `DashboardRegistrationResult.status` is the string literal `"already_registered"` or `"registered"` everywhere it's checked (Task 4's tests, Task 5's `cli.py`) — no drift. `after_line: int | None` used consistently across `insert_dashboard_section` (Task 2), `preview_insertion`/`register_dashboard` (Task 4), and the CLI option resolution (Task 5).
- **Task independence:** each task's test suite passes on its own before the next task begins; Task 3's refactor is verified against the *existing* `publish_device_partition` tests as a regression gate before any new code depends on it.
