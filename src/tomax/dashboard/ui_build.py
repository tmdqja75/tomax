"""Build the React dashboard UI on demand and return the servable dist directory.

Called each time ``tomax dashboard`` runs. The build is cached: if
``dist/index.html`` is newer than every UI source file, it is reused as-is.
Otherwise the UI is rebuilt with pnpm (or npm). No build artifact is ever
committed — ``dist/`` is a local, gitignored cache.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

_ROOT_SOURCES = ("package.json", "vite.config.ts", "index.html")


class UIBuildError(Exception):
    """A user-facing failure while building the dashboard UI."""


def _package_manager() -> list[str]:
    for manager in ("pnpm", "npm"):
        if shutil.which(manager):
            return [manager]
    raise UIBuildError(
        "Node package manager not found — install Node.js and pnpm (or npm) "
        "to build the dashboard UI"
    )


def _source_files(ui_dir: Path) -> Iterator[Path]:
    src = ui_dir / "src"
    if src.is_dir():
        yield from (path for path in src.rglob("*") if path.is_file())
    for name in _ROOT_SOURCES:
        candidate = ui_dir / name
        if candidate.is_file():
            yield candidate


def _is_stale(ui_dir: Path, dist_dir: Path) -> bool:
    index = dist_dir / "index.html"
    if not index.is_file():
        return True
    built_at = index.stat().st_mtime
    return any(source.stat().st_mtime > built_at for source in _source_files(ui_dir))


def _run_step(run, command: list[str], cwd: Path) -> None:
    result = run(command, cwd=str(cwd), capture_output=True, text=True)
    if result.returncode != 0:
        raise UIBuildError(f"`{' '.join(command)}` failed:\n{result.stderr}")


def packaged_prebuilt_dir() -> Path | None:
    """Return the dashboard UI shipped inside the installed package, if present.

    Ships a pre-built ``dist/`` alongside the Python package so ``tomax
    dashboard`` works when installed as a tool (pip/pipx/uv tool), where the
    ``dashboard-ui/`` source checkout isn't available to build from.
    """
    candidate = Path(__file__).resolve().parent / "prebuilt_ui"
    return candidate if (candidate / "index.html").is_file() else None


def ensure_build(ui_dir: Path, *, force: bool = False, run=subprocess.run) -> Path:
    """Ensure the UI is built and return its dist directory, rebuilding only if needed."""
    dist_dir = ui_dir / "dist"
    if not force and not _is_stale(ui_dir, dist_dir):
        return dist_dir
    package_manager = _package_manager()
    if not (ui_dir / "node_modules").is_dir():
        _run_step(run, [*package_manager, "install"], ui_dir)
    _run_step(run, [*package_manager, "run", "build"], ui_dir)
    if not (dist_dir / "index.html").is_file():
        raise UIBuildError("dashboard UI build did not produce dist/index.html")
    return dist_dir
