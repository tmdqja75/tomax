from pathlib import Path

import pytest

from tomax.dashboard import ui_build


def test_packaged_prebuilt_dir_finds_shipped_build():
    result = ui_build.packaged_prebuilt_dir()

    assert result is not None
    assert (result / "index.html").is_file()


def test_packaged_prebuilt_dir_none_when_missing(monkeypatch):
    monkeypatch.setattr(
        ui_build.Path, "is_file", lambda self: False
    )

    assert ui_build.packaged_prebuilt_dir() is None


def test_ensure_build_skips_when_fresh(tmp_path):
    (tmp_path / "dist").mkdir()
    (tmp_path / "dist" / "index.html").write_text("x", encoding="utf-8")
    calls = []

    result = ui_build.ensure_build(tmp_path, run=lambda *a, **k: calls.append(a))

    assert result == tmp_path / "dist"
    assert calls == []


def test_ensure_build_runs_install_and_build_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(ui_build, "_package_manager", lambda: ["pnpm"])
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "App.tsx").write_text("x", encoding="utf-8")
    ran = []

    def fake_run(cmd, *, cwd, capture_output, text):
        ran.append(cmd)
        if cmd[-1] == "build":
            dist = Path(cwd) / "dist"
            dist.mkdir(exist_ok=True)
            (dist / "index.html").write_text("y", encoding="utf-8")

        class Result:
            returncode = 0
            stderr = ""

        return Result()

    result = ui_build.ensure_build(tmp_path, run=fake_run)

    assert (result / "index.html").is_file()
    assert ["pnpm", "install"] in ran
    assert ["pnpm", "run", "build"] in ran


def test_ensure_build_raises_on_build_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(ui_build, "_package_manager", lambda: ["npm"])
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "App.tsx").write_text("x", encoding="utf-8")

    def fake_run(cmd, *, cwd, capture_output, text):
        class Result:
            returncode = 1
            stderr = "boom"

        return Result()

    with pytest.raises(ui_build.UIBuildError):
        ui_build.ensure_build(tmp_path, run=fake_run)


def test_resolve_dist_dir_builds_from_source_when_present(tmp_path, monkeypatch):
    ui_dir = tmp_path / "dashboard-ui"
    ui_dir.mkdir()
    monkeypatch.setattr(
        ui_build, "ensure_build", lambda ui_dir, *, force, run=None: ui_dir / "dist"
    )

    assert ui_build.resolve_dist_dir(ui_dir) == ui_dir / "dist"


def test_resolve_dist_dir_falls_back_to_prebuilt_when_source_missing(tmp_path, monkeypatch):
    prebuilt = tmp_path / "prebuilt"
    monkeypatch.setattr(ui_build, "packaged_prebuilt_dir", lambda: prebuilt)

    assert ui_build.resolve_dist_dir(tmp_path / "dashboard-ui-missing") == prebuilt


def test_resolve_dist_dir_raises_when_source_and_prebuilt_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(ui_build, "packaged_prebuilt_dir", lambda: None)

    with pytest.raises(ui_build.UIBuildError):
        ui_build.resolve_dist_dir(tmp_path / "dashboard-ui-missing")


def test_resolve_dist_dir_rejects_force_when_source_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(ui_build, "packaged_prebuilt_dir", lambda: tmp_path / "prebuilt")

    with pytest.raises(ui_build.UIBuildError):
        ui_build.resolve_dist_dir(tmp_path / "dashboard-ui-missing", force=True)
