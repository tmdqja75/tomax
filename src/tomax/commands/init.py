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

_PREVIEW_CONTEXT_LINES = 3


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
