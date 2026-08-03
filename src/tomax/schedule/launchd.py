"""Plist generation and launchctl plumbing for the opt-in daily scheduler.

Actually loading/unloading a job with the real ``launchctl`` is gated
behind an injectable ``runner`` (mirroring ``tomax.commands.publish``'s
``gh_auth_check`` pattern), so every function here stays fully testable
without touching the real launchd — configuring the real scheduler is a
separate, explicitly user-authorized action, not something exercised by
this module's own test suite.

launchd only runs one program per job, so "collect, then publish" has to
live in a single shell command rather than two separate scheduled jobs —
this is what makes the generated job equivalent to a user typing
``tomax collect && tomax publish`` by hand.
"""

from __future__ import annotations

import os
import plistlib
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

LABEL = "com.tomax.daily-sync"

CommandRunner = Callable[[list[str]], subprocess.CompletedProcess]


class LaunchctlError(RuntimeError):
    """A launchctl mutation failed, so local scheduler state was not changed."""

    def __init__(self, operation: str) -> None:
        super().__init__(f"launchctl {operation} failed")


def default_runner(args: list[str]) -> subprocess.CompletedProcess:
    """The real runner: shells out to the given command. Overridden in tests."""
    return subprocess.run(args, capture_output=True, text=True, check=False)


def launch_agents_dir() -> Path:
    """Where macOS expects a per-user LaunchAgent plist to live."""
    return Path.home() / "Library" / "LaunchAgents"


def plist_path() -> Path:
    return launch_agents_dir() / f"{LABEL}.plist"


def _parse_time(daily_at: str) -> tuple[int, int]:
    hour_str, sep, minute_str = daily_at.partition(":")
    if not sep or not hour_str.isdigit() or not minute_str.isdigit():
        raise ValueError("daily_at must be in 24-hour HH:MM form")
    hour, minute = int(hour_str), int(minute_str)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("daily_at must be in 24-hour HH:MM form")
    return hour, minute


def build_plist(*, executable: str, daily_at: str, log_dir: Path) -> dict:
    """Build the plist content (as a plain dict) for the daily collect+publish job."""
    hour, minute = _parse_time(daily_at)
    log_dir.mkdir(parents=True, exist_ok=True)
    return {
        "Label": LABEL,
        "ProgramArguments": [
            "/bin/sh",
            "-c",
            '"$1" collect && "$1" publish',
            LABEL,
            executable,
        ],
        "StartCalendarInterval": {"Hour": hour, "Minute": minute},
        "RunAtLoad": False,
        "EnvironmentVariables": {"PATH": os.environ.get("PATH", os.defpath)},
        "StandardOutPath": str(log_dir / "scheduler.log"),
        "StandardErrorPath": str(log_dir / "scheduler.err.log"),
    }


def write_plist(path: Path, plist: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        plistlib.dump(plist, handle)


def read_plist(path: Path) -> dict | None:
    if not path.exists():
        return None
    with path.open("rb") as handle:
        return plistlib.load(handle)


@dataclass(frozen=True, slots=True)
class ScheduleStatus:
    """Whether a schedule is installed, and if so, its configured time and load state."""

    installed: bool
    plist_path: Path
    daily_at: str | None
    loaded: bool


def install(
    *,
    executable: str,
    daily_at: str,
    log_dir: Path,
    runner: CommandRunner = default_runner,
) -> Path:
    """Write the plist and load it into launchd. Returns the plist path."""
    target = plist_path()
    plist = build_plist(executable=executable, daily_at=daily_at, log_dir=log_dir)
    previous_contents = target.read_bytes() if target.exists() else None
    write_plist(target, plist)
    result = runner(["launchctl", "load", "-w", str(target)])
    if result.returncode != 0:
        if previous_contents is None:
            target.unlink(missing_ok=True)
        else:
            target.write_bytes(previous_contents)
        raise LaunchctlError("load")
    return target


def remove(*, runner: CommandRunner = default_runner) -> bool:
    """Unload and delete the plist. Returns True if a plist existed to remove."""
    target = plist_path()
    if not target.exists():
        return False
    result = runner(["launchctl", "unload", "-w", str(target)])
    if result.returncode != 0:
        raise LaunchctlError("unload")
    target.unlink()
    return True


def status(*, runner: CommandRunner = default_runner) -> ScheduleStatus:
    """Report whether a schedule is installed and currently loaded into launchd."""
    target = plist_path()
    plist = read_plist(target)
    if plist is None:
        return ScheduleStatus(installed=False, plist_path=target, daily_at=None, loaded=False)

    interval = plist.get("StartCalendarInterval", {})
    daily_at = None
    if "Hour" in interval and "Minute" in interval:
        daily_at = f"{interval['Hour']:02d}:{interval['Minute']:02d}"

    result = runner(["launchctl", "list", LABEL])
    return ScheduleStatus(
        installed=True, plist_path=target, daily_at=daily_at, loaded=result.returncode == 0
    )
