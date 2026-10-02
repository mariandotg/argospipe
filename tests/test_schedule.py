import plistlib
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from argospipe import schedule as sched
from argospipe.cli import app

cli_runner = CliRunner()
EXE = "/opt/bin/argospipe"


class FakeSystem:
    def __init__(self, crontab: str | None = None) -> None:
        self.crontab = crontab
        self.calls: list[list[str]] = []

    def __call__(
        self, args: list[str], stdin: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append(args)
        if args == ["crontab", "-l"]:
            if self.crontab is None:
                return subprocess.CompletedProcess(args, 1, "", "no crontab")
            return subprocess.CompletedProcess(args, 0, self.crontab, "")
        if args == ["crontab", "-"]:
            self.crontab = stdin or ""
        return subprocess.CompletedProcess(args, 0, "", "")


def test_plist_is_valid_with_args_and_time(tmp_path: Path) -> None:
    data = plistlib.loads(sched.build_launchd_plist(EXE, 7, 30, tmp_path).encode())

    assert data["Label"] == "io.github.mariandotg.argospipe"
    assert data["ProgramArguments"] == [EXE, "run", "--no-open"]
    assert data["StartCalendarInterval"] == {"Hour": 7, "Minute": 30}
    log = str(tmp_path / "schedule.log")
    assert data["StandardOutPath"] == log
    assert data["StandardErrorPath"] == log


def test_plist_accepts_module_fallback(tmp_path: Path) -> None:
    data = plistlib.loads(
        sched.build_launchd_plist(["/py", "-m", "argospipe"], 9, 0, tmp_path).encode()
    )

    assert data["ProgramArguments"] == ["/py", "-m", "argospipe", "run", "--no-open"]


def test_cron_line_format_and_marker(tmp_path: Path) -> None:
    line = sched.build_cron_line(EXE, 9, 5, tmp_path)

    assert (
        line
        == f"5 9 * * * {EXE} run --no-open >> {tmp_path}/schedule.log 2>&1 # argospipe-schedule"
    )
    assert line.endswith("# argospipe-schedule")


def test_install_macos_writes_plist_and_bootstraps(tmp_path: Path) -> None:
    system = FakeSystem()
    agents = tmp_path / "agents"

    plist = sched.install_macos(
        EXE, 9, 0, tmp_path / "logs", agents_dir=agents, run=system, uid=501
    )

    assert plist == agents / f"{sched.LABEL}.plist"
    assert plist.exists()
    assert system.calls == [["launchctl", "bootstrap", "gui/501", str(plist)]]


def test_install_macos_boots_out_existing_first(tmp_path: Path) -> None:
    system = FakeSystem()
    args = dict(agents_dir=tmp_path, run=system, uid=501)
    sched.install_macos(EXE, 9, 0, tmp_path / "logs", **args)  # type: ignore[arg-type]
    system.calls.clear()

    sched.install_macos(EXE, 10, 0, tmp_path / "logs", **args)  # type: ignore[arg-type]

    assert system.calls[0] == ["launchctl", "bootout", f"gui/501/{sched.LABEL}"]
    assert system.calls[1][:3] == ["launchctl", "bootstrap", "gui/501"]


def test_remove_macos_is_idempotent(tmp_path: Path) -> None:
    system = FakeSystem()
    sched.install_macos(EXE, 9, 0, tmp_path / "logs", agents_dir=tmp_path, run=system, uid=501)

    assert sched.remove_macos(agents_dir=tmp_path, run=system, uid=501) is True
    assert not (tmp_path / f"{sched.LABEL}.plist").exists()
    assert sched.remove_macos(agents_dir=tmp_path, run=system, uid=501) is False


def test_install_linux_replaces_marked_line_and_keeps_others(tmp_path: Path) -> None:
    old = "0 1 * * * /old/argospipe run --no-open # argospipe-schedule"
    system = FakeSystem(f"MAILTO=me\n30 2 * * * backup.sh\n{old}\n")

    sched.install_linux(EXE, 9, 0, tmp_path, run=system)

    lines = (system.crontab or "").splitlines()
    assert lines[:2] == ["MAILTO=me", "30 2 * * * backup.sh"]
    assert len(lines) == 3
    assert lines[2].startswith(f"0 9 * * * {EXE}")
    assert "/old/" not in (system.crontab or "")


def test_install_linux_without_existing_crontab(tmp_path: Path) -> None:
    system = FakeSystem(None)

    sched.install_linux(EXE, 9, 0, tmp_path, run=system)

    assert (system.crontab or "").count("# argospipe-schedule") == 1


def test_remove_linux_is_idempotent(tmp_path: Path) -> None:
    system = FakeSystem("30 2 * * * backup.sh\n")
    sched.install_linux(EXE, 9, 0, tmp_path, run=system)

    assert sched.remove_linux(run=system) is True
    assert system.crontab == "30 2 * * * backup.sh\n"
    assert sched.remove_linux(run=system) is False


def test_windows_instructions_mention_time_and_command() -> None:
    text = sched.windows_instructions(EXE, 8, 5)

    assert "Task Scheduler" in text
    assert "08:05" in text
    assert "run --no-open" in text


@pytest.fixture
def cli_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    monkeypatch.setenv("ARGOSPIPE_HOME", str(tmp_path))
    calls: list[str] = []

    def fail(name: str) -> object:
        def inner(*args: object, **kwargs: object) -> object:
            calls.append(name)
            raise AssertionError(f"{name} must not run")

        return inner

    for name in ("install_macos", "remove_macos", "install_linux", "remove_linux"):
        monkeypatch.setattr(sched, name, fail(name))
    return calls


@pytest.mark.parametrize("value", ["25:00", "9", "09:60", "ab:cd", "9:5"])
def test_schedule_rejects_invalid_time(cli_env: list[str], value: str) -> None:
    result = cli_runner.invoke(app, ["schedule", "--at", value])

    assert result.exit_code == 2
    assert cli_env == []


def test_schedule_windows_prints_instructions(
    cli_env: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sched, "current_platform", lambda: "win32")

    result = cli_runner.invoke(app, ["schedule", "--at", "08:15"])

    assert result.exit_code == 0
    assert "Task Scheduler" in result.output
    assert "08:15" in result.output
    assert cli_env == []


def test_schedule_macos_installs_and_reports_logs(
    tmp_path: Path, cli_env: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, object] = {}

    def fake_install(executable: object, hour: int, minute: int, log_dir: Path) -> Path:
        seen.update(hour=hour, minute=minute, log_dir=log_dir)
        return Path("/fake/agent.plist")

    monkeypatch.setattr(sched, "current_platform", lambda: "darwin")
    monkeypatch.setattr(sched, "install_macos", fake_install)

    result = cli_runner.invoke(app, ["schedule", "--at", "7:30"])

    assert result.exit_code == 0
    assert seen == {"hour": 7, "minute": 30, "log_dir": tmp_path}
    assert "/fake/agent.plist" in result.output
    assert str(tmp_path / "schedule.log") in result.output


def test_schedule_linux_remove_reports_nothing_installed(
    cli_env: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sched, "current_platform", lambda: "linux")
    monkeypatch.setattr(sched, "remove_linux", lambda: False)

    result = cli_runner.invoke(app, ["schedule", "--remove"])

    assert result.exit_code == 0
    assert "No schedule installed" in result.output


def test_install_linux_never_wipes_crontab_when_read_fails(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def broken(args: list[str], stdin: str | None = None) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        if args == ["crontab", "-l"]:
            return subprocess.CompletedProcess(args, 1, "", "crontab: permission denied")
        return subprocess.CompletedProcess(args, 0, "", "")

    with pytest.raises(RuntimeError, match="crontab read failed"):
        sched.install_linux(EXE, 9, 0, tmp_path, run=broken)
    assert ["crontab", "-"] not in calls
