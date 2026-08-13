"""Tests for the profile-repo GitHub Action template and its SVG builder."""

from __future__ import annotations

import importlib.util
import sys
import tomllib
from datetime import date, datetime, timezone
from pathlib import Path

from tomax.models import NormalizedUsageRecord, SupportedAgent, TokenUsage
from tomax.public_data import build_daily_record, write_daily_record

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_PATH = REPO_ROOT / "src" / "tomax" / "templates" / "github-workflow.yml"
SCRIPT_PATH = REPO_ROOT / "scripts" / "build_profile_dashboard.py"


def _strip_comments(yaml_text: str) -> str:
    lines = []
    for line in yaml_text.splitlines():
        stripped = line.split(" #")[0] if " #" in line else line
        if not stripped.strip().startswith("#"):
            lines.append(stripped)
    return "\n".join(lines)


def _load_build_script():
    spec = importlib.util.spec_from_file_location("build_profile_dashboard", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


build_profile_dashboard = _load_build_script()


def _write_device_record(data_dir: Path, device_id: str, day: date, payload: dict) -> Path:
    path = data_dir / device_id / f"{day.isoformat()}.json"
    write_daily_record(path, payload)
    return path


def _valid_payload(*, device_id: str, day: date) -> dict:
    record = NormalizedUsageRecord(
        agent=SupportedAgent.CLAUDE_CODE,
        occurred_at=datetime(day.year, day.month, day.day, 12, 0, tzinfo=timezone.utc),
        fingerprint=f"fp-{device_id}-{day.isoformat()}",
        session_fingerprint=f"s-{device_id}-{day.isoformat()}",
        tokens=TokenUsage(input_tokens=10, output_tokens=5, reasoning_tokens=1),
    )
    return build_daily_record(device_id=device_id, day=day, records=[record])


def _build(tmp_path: Path, *, data_dir: Path | None = None, today: date = date(2026, 7, 18)):
    return build_profile_dashboard.build(
        data_dir=data_dir or tmp_path / "data" / "v1" / "devices",
        readme_path=tmp_path / "README.md",
        dashboard_svg_path=tmp_path / "assets" / "tomax" / "dashboard.svg",
        today=today,
        generated_at="2026-07-18 00:00 UTC",
    )


def test_workflow_template_is_narrow_and_serialized() -> None:
    text = _strip_comments(WORKFLOW_PATH.read_text(encoding="utf-8"))
    trigger_section = text.split("jobs:")[0]

    assert "on:" in text and "push:" in text and "main" in text
    assert "pull_request" not in text and "workflow_run" not in text
    assert "data/v1/**" in trigger_section
    assert "README.md" not in trigger_section and "assets/tomax" not in trigger_section
    assert "contents: write" in text and "cancel-in-progress: false" in text


def test_workflow_template_defaults_the_collector_ref_to_the_current_release_tag() -> None:
    """The bootstrap default must track a release tag, not a moving branch.

    ``main`` can carry breaking changes (argv/behavior) the instant they
    land; every already-installed user's Action re-fetches ``ref`` on its
    next run and would break immediately (this happened: the scorecard
    rename broke every installed workflow still pinned to 'main' until it
    was pinned by hand). Pinning the packaged template's default to this
    release's own tag means a user only moves to a new tomax behavior when
    they explicitly upgrade and re-run ``tomax init``/``publish`` picks up
    the new template — not the instant upstream ``main`` changes.
    """
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    version = pyproject["project"]["version"]

    assert f"vars.AGENT_USAGE_REF || 'v{version}'" in text


def test_workflow_uses_the_pure_svg_scorecard_without_browser_dependencies() -> None:
    text = _strip_comments(WORKFLOW_PATH.read_text(encoding="utf-8"))

    assert "build_profile_dashboard.py" in text
    assert "--dashboard-svg assets/tomax/dashboard.svg" in text
    assert "git add README.md assets/tomax/dashboard.svg" in text
    assert "Playwright" not in text and "pnpm" not in text and "setup-node" not in text


def test_build_generates_an_svg_readme_and_is_idempotent(tmp_path: Path) -> None:
    data_dir = tmp_path / "data" / "v1" / "devices"
    _write_device_record(data_dir, "device-a", date(2026, 7, 10), _valid_payload(device_id="device-a", day=date(2026, 7, 10)))

    assert _build(tmp_path, data_dir=data_dir) is True
    svg = tmp_path / "assets" / "tomax" / "dashboard.svg"
    readme = (tmp_path / "README.md").read_text(encoding="utf-8")
    assert svg.read_text(encoding="utf-8").startswith("<svg")
    assert "assets/tomax/dashboard.svg" in readme
    assert _build(tmp_path, data_dir=data_dir) is False


def test_build_skips_malformed_and_non_utf8_records_with_diagnostics(tmp_path: Path, capsys) -> None:
    data_dir = tmp_path / "data" / "v1" / "devices"
    _write_device_record(data_dir, "device-a", date(2026, 7, 10), _valid_payload(device_id="device-a", day=date(2026, 7, 10)))
    bad_dir = data_dir / "device-b"
    bad_dir.mkdir(parents=True)
    (bad_dir / "2026-07-10.json").write_text("not valid json{{{", encoding="utf-8")
    (bad_dir / "2026-07-11.json").write_bytes(b"\xff\xfe\x00not utf-8")

    assert _build(tmp_path, data_dir=data_dir) is True
    captured = capsys.readouterr()
    assert "device-b" in captured.err
    assert "not a JSON object" in captured.err


def test_build_never_leaks_device_ids_or_fingerprints_into_the_scorecard(tmp_path: Path) -> None:
    data_dir = tmp_path / "data" / "v1" / "devices"
    device_id = "device-super-secret-id"
    _write_device_record(data_dir, device_id, date(2026, 7, 10), _valid_payload(device_id=device_id, day=date(2026, 7, 10)))

    _build(tmp_path, data_dir=data_dir)
    svg = (tmp_path / "assets" / "tomax" / "dashboard.svg").read_text(encoding="utf-8")
    assert device_id not in svg
    assert "fp-device-super-secret-id" not in svg


def test_build_handles_missing_data_and_rejects_future_records(tmp_path: Path, capsys) -> None:
    assert _build(tmp_path) is True
    assert "## Agent Usage" in (tmp_path / "README.md").read_text(encoding="utf-8")

    data_dir = tmp_path / "future" / "data"
    _write_device_record(data_dir, "device-a", date(2026, 7, 30), _valid_payload(device_id="device-a", day=date(2026, 7, 30)))
    _build(tmp_path / "future-output", data_dir=data_dir)
    assert "future" in capsys.readouterr().err.lower()


def test_main_accepts_svg_arguments_and_exits_zero(tmp_path: Path) -> None:
    data_dir = tmp_path / "data" / "v1" / "devices"
    _write_device_record(data_dir, "device-a", date(2026, 7, 10), _valid_payload(device_id="device-a", day=date(2026, 7, 10)))
    readme = tmp_path / "README.md"
    svg = tmp_path / "assets" / "tomax" / "dashboard.svg"

    exit_code = build_profile_dashboard.main(
        [
            "--data-dir", str(data_dir), "--readme", str(readme), "--dashboard-svg", str(svg),
            "--today", "2026-07-18", "--generated-at", "2026-07-18 00:00 UTC",
        ]
    )

    assert exit_code == 0
    assert readme.exists() and svg.exists()
