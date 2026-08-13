"""Build the profile README dashboard from public per-device aggregates.

Run inside the profile repository's GitHub Action (see
``templates/github-workflow.yml``): reads every device's daily JSON
records under ``data/v1/devices/``, validates them defensively — they may
originate from any device pushing to the shared public repo, so this is
not just a re-check of trusted local output — and regenerates the managed
README section plus its screenshot-generated PNG dashboard asset. A
malformed or invalid record is skipped and reported as a diagnostic on
stderr rather than aborting the whole build, so one bad device partition
never blocks everyone else's dashboard. Diagnostics only ever include the
device id, date, and rejection reason already carried by
``ValidationIssue`` — never record content.

Not part of the installable ``tomax`` package: it is meant to be
checked out from this repo and invoked directly in the profile repo's CI,
per the workflow template.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from tomax.aggregate import validate_and_partition
from tomax.render.markdown import (
    DASHBOARD_IMAGE_PATH,
    render_dashboard_markdown,
    update_readme,
)
from tomax.render.scorecard import render_scorecard_svg

DEFAULT_DATA_DIR = Path("data/v1/devices")
DEFAULT_README = Path("README.md")
DEFAULT_DASHBOARD_SVG = Path(DASHBOARD_IMAGE_PATH)


def _load_entries(data_dir: Path) -> list[tuple[str, object]]:
    """Load every ``<device_id>/<date>.json`` file under ``data_dir``.

    A file that fails to read or parse becomes an entry with payload
    ``None``, so ``validate_and_partition`` rejects it with a diagnostic
    like any other malformed record instead of the whole build crashing.
    """
    entries: list[tuple[str, object]] = []
    if not data_dir.is_dir():
        return entries
    for device_dir in sorted(p for p in data_dir.iterdir() if p.is_dir()):
        device_id = device_dir.name
        for record_path in sorted(device_dir.glob("*.json")):
            try:
                payload = json.loads(record_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                payload = None
            entries.append((device_id, payload))
    return entries


def _write_if_changed(path: Path, content: str | bytes) -> bool:
    if path.exists() and (
        path.read_bytes() if isinstance(content, bytes) else path.read_text(encoding="utf-8")
    ) == content:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")
    return True


def _readme_relative_path(path: Path, *, readme_path: Path) -> str:
    try:
        return path.relative_to(readme_path.parent).as_posix()
    except ValueError:
        return path.as_posix()


def build(
    *,
    data_dir: Path,
    readme_path: Path,
    dashboard_svg_path: Path,
    today: date,
    generated_at: str,
) -> bool:
    """Regenerate the README and compact SVG scorecard. Returns True if anything changed.

    Reads the cross-device records locally from ``data_dir`` (the profile repo
    checkout already contains them — no network), then renders the compact
    static scorecard directly with no browser or UI build.
    """
    entries = _load_entries(data_dir)
    partition = validate_and_partition(entries, today=today)
    for issue in partition.issues:
        print(
            f"tomax: skipping invalid record "
            f"device={issue.device_id} date={issue.date} reason={issue.reason}",
            file=sys.stderr,
        )
    svg = render_scorecard_svg(partition.valid_payloads, today=today)
    changed = _write_if_changed(dashboard_svg_path, svg)

    image_ref = _readme_relative_path(dashboard_svg_path, readme_path=readme_path)
    existing_readme = readme_path.read_text(encoding="utf-8") if readme_path.exists() else ""
    updated = update_readme(existing_readme, render_dashboard_markdown(image_path=image_ref))
    changed = _write_if_changed(readme_path, updated) or changed
    return changed


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--readme", type=Path, default=DEFAULT_README)
    parser.add_argument("--dashboard-svg", type=Path, default=DEFAULT_DASHBOARD_SVG)
    parser.add_argument("--today", type=date.fromisoformat, default=None)
    parser.add_argument("--generated-at", type=str, default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    now = datetime.now(timezone.utc)
    today = args.today or now.date()
    generated_at = args.generated_at or now.strftime("%Y-%m-%d %H:%M UTC")
    changed = build(
        data_dir=args.data_dir,
        readme_path=args.readme,
        dashboard_svg_path=args.dashboard_svg,
        today=today,
        generated_at=generated_at,

    )
    print("tomax: dashboard changed" if changed else "tomax: dashboard unchanged")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
