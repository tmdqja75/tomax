from datetime import date

import pytest

from tomax.commands import dashboard as dashboard_command
from tomax.dashboard.remote import NoRepoTargetError
from tomax.dashboard.ui_build import UIBuildError


def test_run_builds_payload_and_serves(monkeypatch, tmp_path):
    calls = {}

    monkeypatch.setattr(
        dashboard_command, "build_payload", lambda **kwargs: {"served": kwargs["all_devices"]}
    )
    monkeypatch.setattr(
        dashboard_command, "resolve_dist_dir", lambda ui_dir, *, force: ui_dir / "dist"
    )

    def fake_serve(data, *, dist_dir, port, open_browser, lang):
        calls["data"] = data
        calls["dist_dir"] = dist_dir
        calls["port"] = port
        calls["open_browser"] = open_browser
        calls["lang"] = lang

    monkeypatch.setattr(dashboard_command, "serve", fake_serve)

    ui_dir = tmp_path / "dashboard-ui"
    ui_dir.mkdir()

    dashboard_command.run(
        ledger_path=tmp_path / "ledger.sqlite3",
        config_path=tmp_path / "config.json",
        all_devices=True,
        port=8123,
        open_browser=False,
        pie_top_n=6,
        lang="ko",
        ui_dir=ui_dir,
        force_build=False,
        today=date(2026, 7, 18),
        tmp_stage_dir=tmp_path / "stage",
    )

    assert calls["data"] == {"served": True}
    assert calls["dist_dir"] == ui_dir / "dist"
    assert calls["port"] == 8123
    assert calls["open_browser"] is False
    assert calls["lang"] == "ko"


def test_run_wraps_ui_build_error_as_dashboard_error(monkeypatch, tmp_path):
    monkeypatch.setattr(dashboard_command, "build_payload", lambda **kwargs: {"ok": True})

    def boom(ui_dir, *, force):
        raise UIBuildError("dashboard UI source not found — run from a repository checkout")

    monkeypatch.setattr(dashboard_command, "resolve_dist_dir", boom)
    monkeypatch.setattr(dashboard_command, "serve", lambda *a, **k: None)

    with pytest.raises(dashboard_command.DashboardError):
        dashboard_command.run(
            ledger_path=tmp_path / "ledger.sqlite3",
            config_path=tmp_path / "config.json",
            all_devices=True,
            port=8000,
            open_browser=False,
            pie_top_n=6,
            lang="ko",
            ui_dir=tmp_path / "dashboard-ui-missing",
            force_build=False,
            today=date(2026, 7, 18),
            tmp_stage_dir=tmp_path / "stage",
        )


def test_run_reports_missing_repo_target(monkeypatch, tmp_path):
    def boom(**kwargs):
        raise NoRepoTargetError("no repo target set")

    monkeypatch.setattr(dashboard_command, "build_payload", boom)
    monkeypatch.setattr(dashboard_command, "resolve_dist_dir", lambda ui_dir, *, force: ui_dir)
    monkeypatch.setattr(dashboard_command, "serve", lambda *a, **k: None)

    ui_dir = tmp_path / "dashboard-ui"
    ui_dir.mkdir()

    with pytest.raises(dashboard_command.DashboardError):
        dashboard_command.run(
            ledger_path=tmp_path / "ledger.sqlite3",
            config_path=tmp_path / "config.json",
            all_devices=True,
            port=8000,
            open_browser=False,
            pie_top_n=6,
            lang="ko",
            ui_dir=ui_dir,
            force_build=False,
            today=date(2026, 7, 18),
            tmp_stage_dir=tmp_path / "stage",
        )
