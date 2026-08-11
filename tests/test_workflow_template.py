"""Tests for the profile-repo GitHub Action template and its build script.

``templates/github-workflow.yml`` is meant to be copied into a profile
repository (e.g. ``tmdqja75/tmdqja75``), not this collector repo, so it is
checked here as a static template: trigger scope, permissions, and loop
prevention are asserted directly against its text. ``scripts/build_profile_dashboard.py``
is the script that workflow invokes; it is exercised directly against a
temporary directory standing in for a checked-out profile repo.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import date
from pathlib import Path

from tomax.public_data import write_daily_record

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_PATH = REPO_ROOT / "src" / "tomax" / "templates" / "github-workflow.yml"
SCRIPT_PATH = REPO_ROOT / "scripts" / "build_profile_dashboard.py"


def _strip_comments(yaml_text: str) -> str:
    """Drop full-line and trailing '#' comments so assertions check live YAML, not prose."""
    lines = []
    for line in yaml_text.splitlines():
        stripped = line.split(" #")[0] if " #" in line else line
        if stripped.strip().startswith("#"):
            continue
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
    from datetime import datetime, timezone

    from tomax.models import NormalizedUsageRecord, SupportedAgent, TokenUsage
    from tomax.public_data import build_daily_record

    record = NormalizedUsageRecord(
        agent=SupportedAgent.CLAUDE_CODE,
        occurred_at=datetime(day.year, day.month, day.day, 12, 0, tzinfo=timezone.utc),
        fingerprint=f"fp-{device_id}-{day.isoformat()}",
        session_fingerprint=f"s-{device_id}-{day.isoformat()}",
        tokens=TokenUsage(input_tokens=10, output_tokens=5, reasoning_tokens=1),
    )
    return build_daily_record(device_id=device_id, day=day, records=[record])


# --- templates/github-workflow.yml -----------------------------------------


def test_workflow_template_exists() -> None:
    assert WORKFLOW_PATH.is_file()


def test_workflow_triggers_only_on_push_to_default_branch() -> None:
    text = _strip_comments(WORKFLOW_PATH.read_text(encoding="utf-8"))

    assert "pull_request" not in text
    assert "workflow_run" not in text
    assert "on:" in text
    assert "push:" in text
    assert "branches:" in text
    assert "main" in text


def test_workflow_triggers_only_on_data_partition_changes() -> None:
    text = _strip_comments(WORKFLOW_PATH.read_text(encoding="utf-8"))
    trigger_section = text.split("jobs:")[0]

    assert "paths:" in trigger_section
    assert "data/v1/**" in trigger_section
    # The generated dashboard files must never appear in the trigger paths,
    # or the workflow would loop on its own commits.
    assert "README.md" not in trigger_section
    assert "assets/tomax" not in trigger_section


def test_workflow_has_write_permissions_and_serialized_concurrency() -> None:
    text = _strip_comments(WORKFLOW_PATH.read_text(encoding="utf-8"))

    assert "permissions:" in text
    assert "contents: write" in text
    assert "concurrency:" in text
    assert "cancel-in-progress: false" in text


def test_workflow_invokes_the_build_script() -> None:
    text = _strip_comments(WORKFLOW_PATH.read_text(encoding="utf-8"))

    assert "build_profile_dashboard.py" in text


def test_workflow_only_commits_when_something_changed() -> None:
    text = _strip_comments(WORKFLOW_PATH.read_text(encoding="utf-8"))

    assert "git status --porcelain" in text or "git diff --quiet" in text


# --- scripts/build_profile_dashboard.py -------------------------------------


def _fake_screenshot(payload, output_path, **kwargs) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(b"\x89PNG\r\n\x1a\nFAKE")


def _mock_screenshot_pipeline(monkeypatch) -> None:
    monkeypatch.setattr(build_profile_dashboard, "screenshot_payload", _fake_screenshot)
    monkeypatch.setattr(build_profile_dashboard, "ensure_build", lambda ui_dir, force=False: ui_dir)


def test_build_generates_readme_and_dashboard_png(tmp_path: Path, monkeypatch) -> None:
    _mock_screenshot_pipeline(monkeypatch)
    data_dir = tmp_path / "data" / "v1" / "devices"
    _write_device_record(data_dir, "device-a", date(2026, 7, 10), _valid_payload(device_id="device-a", day=date(2026, 7, 10)))
    readme_path = tmp_path / "README.md"
    readme_path.write_text("# My Profile\n\nIntro text.\n", encoding="utf-8")
    dashboard_png_path = tmp_path / "assets" / "tomax" / "dashboard.png"
    ui_dir = tmp_path / "dashboard-ui"

    changed = build_profile_dashboard.build(
        data_dir=data_dir,
        readme_path=readme_path,
        dashboard_png_path=dashboard_png_path,
        ui_dir=ui_dir,
        today=date(2026, 7, 18),
        generated_at="2026-07-18 00:00 UTC",
    )

    assert changed is True
    assert "# My Profile" in readme_path.read_text(encoding="utf-8")
    assert "tomax:start" in readme_path.read_text(encoding="utf-8")
    png_signature = b"\x89PNG\r\n\x1a\n"
    assert dashboard_png_path.read_bytes().startswith(png_signature)


def test_build_is_idempotent_on_unchanged_input(tmp_path: Path, monkeypatch) -> None:
    _mock_screenshot_pipeline(monkeypatch)
    data_dir = tmp_path / "data" / "v1" / "devices"
    _write_device_record(data_dir, "device-a", date(2026, 7, 10), _valid_payload(device_id="device-a", day=date(2026, 7, 10)))
    readme_path = tmp_path / "README.md"
    dashboard_png_path = tmp_path / "assets" / "tomax" / "dashboard.png"
    ui_dir = tmp_path / "dashboard-ui"

    kwargs = dict(
        data_dir=data_dir,
        readme_path=readme_path,
        dashboard_png_path=dashboard_png_path,
        ui_dir=ui_dir,
        today=date(2026, 7, 18),
        generated_at="2026-07-18 00:00 UTC",
    )
    first = build_profile_dashboard.build(**kwargs)
    second = build_profile_dashboard.build(**kwargs)

    assert first is True
    assert second is False


def test_build_skips_malformed_records_and_reports_diagnostics(tmp_path: Path, capsys, monkeypatch) -> None:
    _mock_screenshot_pipeline(monkeypatch)
    data_dir = tmp_path / "data" / "v1" / "devices"
    _write_device_record(data_dir, "device-a", date(2026, 7, 10), _valid_payload(device_id="device-a", day=date(2026, 7, 10)))
    bad_dir = data_dir / "device-b"
    bad_dir.mkdir(parents=True, exist_ok=True)
    (bad_dir / "2026-07-10.json").write_text("not valid json{{{", encoding="utf-8")
    readme_path = tmp_path / "README.md"
    dashboard_png_path = tmp_path / "assets" / "tomax" / "dashboard.png"
    ui_dir = tmp_path / "dashboard-ui"

    changed = build_profile_dashboard.build(
        data_dir=data_dir,
        readme_path=readme_path,
        dashboard_png_path=dashboard_png_path,
        ui_dir=ui_dir,
        today=date(2026, 7, 18),
        generated_at="2026-07-18 00:00 UTC",
    )

    assert changed is True
    assert "device-b" not in readme_path.read_text(encoding="utf-8")
    captured = capsys.readouterr()
    assert "device-b" in captured.err
    assert "not a JSON object" in captured.err


def test_build_skips_a_non_utf8_record_file_without_crashing(tmp_path: Path, capsys, monkeypatch) -> None:
    _mock_screenshot_pipeline(monkeypatch)
    data_dir = tmp_path / "data" / "v1" / "devices"
    _write_device_record(data_dir, "device-a", date(2026, 7, 10), _valid_payload(device_id="device-a", day=date(2026, 7, 10)))
    bad_dir = data_dir / "device-b"
    bad_dir.mkdir(parents=True, exist_ok=True)
    (bad_dir / "2026-07-10.json").write_bytes(b"\xff\xfe\x00not utf-8")
    readme_path = tmp_path / "README.md"
    dashboard_png_path = tmp_path / "assets" / "tomax" / "dashboard.png"
    ui_dir = tmp_path / "dashboard-ui"

    changed = build_profile_dashboard.build(
        data_dir=data_dir,
        readme_path=readme_path,
        dashboard_png_path=dashboard_png_path,
        ui_dir=ui_dir,
        today=date(2026, 7, 18),
        generated_at="2026-07-18 00:00 UTC",
    )

    assert changed is True
    captured = capsys.readouterr()
    assert "device-b" in captured.err
    assert "not a JSON object" in captured.err


def test_build_never_leaks_device_ids_or_fingerprints_into_readme(tmp_path: Path, monkeypatch) -> None:
    _mock_screenshot_pipeline(monkeypatch)
    data_dir = tmp_path / "data" / "v1" / "devices"
    _write_device_record(
        data_dir, "device-super-secret-id", date(2026, 7, 10),
        _valid_payload(device_id="device-super-secret-id", day=date(2026, 7, 10)),
    )
    readme_path = tmp_path / "README.md"
    dashboard_png_path = tmp_path / "assets" / "tomax" / "dashboard.png"
    ui_dir = tmp_path / "dashboard-ui"

    build_profile_dashboard.build(
        data_dir=data_dir,
        readme_path=readme_path,
        dashboard_png_path=dashboard_png_path,
        ui_dir=ui_dir,
        today=date(2026, 7, 18),
        generated_at="2026-07-18 00:00 UTC",
    )

    assert "device-super-secret-id" not in readme_path.read_text(encoding="utf-8")


def test_build_handles_missing_data_dir_without_crashing(tmp_path: Path, monkeypatch) -> None:
    _mock_screenshot_pipeline(monkeypatch)
    data_dir = tmp_path / "data" / "v1" / "devices"  # never created
    readme_path = tmp_path / "README.md"
    dashboard_png_path = tmp_path / "assets" / "tomax" / "dashboard.png"
    ui_dir = tmp_path / "dashboard-ui"

    changed = build_profile_dashboard.build(
        data_dir=data_dir,
        readme_path=readme_path,
        dashboard_png_path=dashboard_png_path,
        ui_dir=ui_dir,
        today=date(2026, 7, 18),
        generated_at="2026-07-18 00:00 UTC",
    )

    assert changed is True
    assert "## Agent Usage" in readme_path.read_text(encoding="utf-8")


def test_build_rejects_future_dated_records_without_crashing(tmp_path: Path, capsys, monkeypatch) -> None:
    _mock_screenshot_pipeline(monkeypatch)
    data_dir = tmp_path / "data" / "v1" / "devices"
    _write_device_record(
        data_dir, "device-a", date(2026, 7, 30),
        _valid_payload(device_id="device-a", day=date(2026, 7, 30)),
    )
    readme_path = tmp_path / "README.md"
    dashboard_png_path = tmp_path / "assets" / "tomax" / "dashboard.png"
    ui_dir = tmp_path / "dashboard-ui"

    build_profile_dashboard.build(
        data_dir=data_dir,
        readme_path=readme_path,
        dashboard_png_path=dashboard_png_path,
        ui_dir=ui_dir,
        today=date(2026, 7, 18),
        generated_at="2026-07-18 00:00 UTC",
    )

    captured = capsys.readouterr()
    assert "future" in captured.err.lower()


def test_main_accepts_cli_arguments_and_exits_zero(tmp_path: Path, monkeypatch) -> None:
    _mock_screenshot_pipeline(monkeypatch)
    data_dir = tmp_path / "data" / "v1" / "devices"
    _write_device_record(data_dir, "device-a", date(2026, 7, 10), _valid_payload(device_id="device-a", day=date(2026, 7, 10)))
    readme_path = tmp_path / "README.md"
    dashboard_png_path = tmp_path / "assets" / "tomax" / "dashboard.png"
    ui_dir = tmp_path / "dashboard-ui"

    exit_code = build_profile_dashboard.main(
        [
            "--data-dir",
            str(data_dir),
            "--readme",
            str(readme_path),
            "--dashboard-png",
            str(dashboard_png_path),
            "--ui-dir",
            str(ui_dir),
            "--today",
            "2026-07-18",
            "--generated-at",
            "2026-07-18 00:00 UTC",
        ]
    )

    assert exit_code == 0
    assert readme_path.exists()
    assert dashboard_png_path.exists()
