"""Tests for macOS launchd plist generation and install/status/remove plumbing.

Every test runs against a ``launch_agents_dir`` monkeypatched to a tmp_path
and a fake ``runner`` in place of ``subprocess.run`` — this suite never
invokes the real ``launchctl`` or writes to the real
``~/Library/LaunchAgents``, since actually configuring launchd requires
separate, explicit user authorization outside this test suite's scope.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tomax.schedule import launchd


def _fake_runner(returncode: int = 0):
    calls = []

    def runner(args: list[str]) -> subprocess.CompletedProcess:
        calls.append(args)
        return subprocess.CompletedProcess(args, returncode, stdout="", stderr="")

    runner.calls = calls
    return runner


# --- build_plist -------------------------------------------------------


def test_build_plist_sets_the_label_and_calendar_interval(tmp_path: Path) -> None:
    plist = launchd.build_plist(
        executable="/usr/local/bin/tomax", daily_at="09:30", log_dir=tmp_path / "logs"
    )

    assert plist["Label"] == launchd.LABEL
    assert plist["StartCalendarInterval"] == {"Hour": 9, "Minute": 30}


def test_build_plist_runs_collect_then_publish_via_the_given_executable(tmp_path: Path) -> None:
    plist = launchd.build_plist(
        executable="/usr/local/bin/tomax", daily_at="09:00", log_dir=tmp_path / "logs"
    )

    program_arguments = plist["ProgramArguments"]
    assert program_arguments[0] == "/bin/sh"
    assert program_arguments[1] == "-c"
    assert program_arguments[2] == '"$1" collect && "$1" publish'
    assert program_arguments[3] == launchd.LABEL
    assert program_arguments[4] == "/usr/local/bin/tomax"


def test_build_plist_preserves_the_installer_path_for_publish_dependencies(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("PATH", "/opt/homebrew/bin:/usr/bin:/bin")

    plist = launchd.build_plist(
        executable="/usr/local/bin/tomax", daily_at="09:00", log_dir=tmp_path / "logs"
    )

    assert plist["EnvironmentVariables"] == {"PATH": "/opt/homebrew/bin:/usr/bin:/bin"}


def test_build_plist_passes_an_unusual_executable_as_an_argument_not_shell_code(tmp_path: Path) -> None:
    executable = '/Applications/Agent Usage/bin/tomax; echo unsafe'

    plist = launchd.build_plist(executable=executable, daily_at="09:00", log_dir=tmp_path / "logs")

    assert plist["ProgramArguments"][2] == '"$1" collect && "$1" publish'
    assert plist["ProgramArguments"][4] == executable


def test_build_plist_does_not_run_immediately_on_load(tmp_path: Path) -> None:
    plist = launchd.build_plist(
        executable="/usr/local/bin/tomax", daily_at="09:00", log_dir=tmp_path / "logs"
    )

    assert plist["RunAtLoad"] is False


def test_build_plist_points_logs_at_the_given_directory(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"

    plist = launchd.build_plist(
        executable="/usr/local/bin/tomax", daily_at="09:00", log_dir=log_dir
    )

    assert plist["StandardOutPath"] == str(log_dir / "scheduler.log")
    assert plist["StandardErrorPath"] == str(log_dir / "scheduler.err.log")
    assert log_dir.is_dir()


def test_build_plist_rejects_an_out_of_range_time(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="HH:MM"):
        launchd.build_plist(executable="tomax", daily_at="25:00", log_dir=tmp_path / "logs")


# --- write_plist / read_plist round trip --------------------------------


def test_write_and_read_plist_round_trips(tmp_path: Path) -> None:
    target = tmp_path / "test.plist"
    plist = launchd.build_plist(executable="tomax", daily_at="09:00", log_dir=tmp_path / "logs")

    launchd.write_plist(target, plist)
    loaded = launchd.read_plist(target)

    assert loaded == plist


def test_read_plist_returns_none_when_absent(tmp_path: Path) -> None:
    assert launchd.read_plist(tmp_path / "missing.plist") is None


# --- install / remove / status ------------------------------------------


def test_install_writes_the_plist_and_loads_it(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(launchd, "launch_agents_dir", lambda: tmp_path / "LaunchAgents")
    runner = _fake_runner()

    target = launchd.install(
        executable="tomax", daily_at="09:00", log_dir=tmp_path / "logs", runner=runner
    )

    assert target == tmp_path / "LaunchAgents" / f"{launchd.LABEL}.plist"
    assert target.exists()
    assert runner.calls == [["launchctl", "load", "-w", str(target)]]


def test_install_removes_a_new_plist_when_launchctl_load_fails(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(launchd, "launch_agents_dir", lambda: tmp_path / "LaunchAgents")
    target = tmp_path / "LaunchAgents" / f"{launchd.LABEL}.plist"

    with pytest.raises(launchd.LaunchctlError, match="load"):
        launchd.install(
            executable="tomax",
            daily_at="09:00",
            log_dir=tmp_path / "logs",
            runner=_fake_runner(returncode=1),
        )

    assert not target.exists()


def test_remove_unloads_and_deletes_an_existing_plist(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(launchd, "launch_agents_dir", lambda: tmp_path / "LaunchAgents")
    install_runner = _fake_runner()
    target = launchd.install(
        executable="tomax", daily_at="09:00", log_dir=tmp_path / "logs", runner=install_runner
    )
    remove_runner = _fake_runner()

    removed = launchd.remove(runner=remove_runner)

    assert removed is True
    assert not target.exists()
    assert remove_runner.calls == [["launchctl", "unload", "-w", str(target)]]


def test_remove_preserves_the_plist_when_launchctl_unload_fails(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(launchd, "launch_agents_dir", lambda: tmp_path / "LaunchAgents")
    target = launchd.install(
        executable="tomax", daily_at="09:00", log_dir=tmp_path / "logs", runner=_fake_runner()
    )

    with pytest.raises(launchd.LaunchctlError, match="unload"):
        launchd.remove(runner=_fake_runner(returncode=1))

    assert target.exists()


def test_remove_is_a_no_op_when_nothing_is_installed(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(launchd, "launch_agents_dir", lambda: tmp_path / "LaunchAgents")
    runner = _fake_runner()

    removed = launchd.remove(runner=runner)

    assert removed is False
    assert runner.calls == []


def test_status_reports_not_installed_when_no_plist_exists(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(launchd, "launch_agents_dir", lambda: tmp_path / "LaunchAgents")

    result = launchd.status(runner=_fake_runner())

    assert result.installed is False
    assert result.loaded is False
    assert result.daily_at is None


def test_status_reports_installed_and_loaded(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(launchd, "launch_agents_dir", lambda: tmp_path / "LaunchAgents")
    launchd.install(
        executable="tomax", daily_at="14:45", log_dir=tmp_path / "logs", runner=_fake_runner()
    )

    result = launchd.status(runner=_fake_runner(returncode=0))

    assert result.installed is True
    assert result.daily_at == "14:45"
    assert result.loaded is True


def test_status_reports_installed_but_not_loaded(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(launchd, "launch_agents_dir", lambda: tmp_path / "LaunchAgents")
    launchd.install(
        executable="tomax", daily_at="09:00", log_dir=tmp_path / "logs", runner=_fake_runner()
    )

    result = launchd.status(runner=_fake_runner(returncode=1))

    assert result.installed is True
    assert result.loaded is False
