import os
import plistlib
import shlex
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

LABEL = "io.github.mariandotg.argospipe"
CRON_MARKER = "# argospipe-schedule"
LOG_NAME = "schedule.log"

Runner = Callable[[list[str], str | None], subprocess.CompletedProcess[str]]
Executable = str | Sequence[str]


def default_runner(args: list[str], stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, input=stdin, capture_output=True, text=True, check=False)


def current_platform() -> str:
    return sys.platform


def resolve_executable() -> list[str]:
    found = shutil.which("argospipe")
    if found:
        return [str(Path(found).absolute())]
    return [sys.executable, "-m", "argospipe"]


def launch_agents_dir() -> Path:
    return Path.home() / "Library" / "LaunchAgents"


def _argv(executable: Executable) -> list[str]:
    return [executable] if isinstance(executable, str) else list(executable)


def build_launchd_plist(executable: Executable, hour: int, minute: int, log_dir: Path) -> str:
    log = str(log_dir / LOG_NAME)
    payload = {
        "Label": LABEL,
        "ProgramArguments": [*_argv(executable), "run", "--no-open"],
        "StartCalendarInterval": {"Hour": hour, "Minute": minute},
        "StandardOutPath": log,
        "StandardErrorPath": log,
    }
    return plistlib.dumps(payload).decode("utf-8")


def build_cron_line(executable: Executable, hour: int, minute: int, log_dir: Path) -> str:
    command = shlex.join([*_argv(executable), "run", "--no-open"])
    log = shlex.quote(str(log_dir / LOG_NAME))
    return f"{minute} {hour} * * * {command} >> {log} 2>&1 {CRON_MARKER}"


def windows_instructions(executable: Executable, hour: int, minute: int) -> str:
    command = subprocess.list2cmdline([*_argv(executable), "run", "--no-open"])
    return "\n".join(
        [
            "Automatic install is not supported on Windows. Use Task Scheduler:",
            "1. Open Task Scheduler and choose 'Create Basic Task'.",
            "2. Name it 'argospipe'.",
            f"3. Trigger: Daily, at {hour:02d}:{minute:02d}.",
            "4. Action: 'Start a program'.",
            f"5. Program/script and arguments: {command}",
            "6. Finish. To remove the schedule, delete the task.",
        ]
    )


def install_macos(
    executable: Executable,
    hour: int,
    minute: int,
    log_dir: Path,
    *,
    agents_dir: Path | None = None,
    run: Runner = default_runner,
    uid: int | None = None,
) -> Path:
    plist = (agents_dir or launch_agents_dir()) / f"{LABEL}.plist"
    domain = f"gui/{os.getuid() if uid is None else uid}"
    if plist.exists():
        run(["launchctl", "bootout", f"{domain}/{LABEL}"], None)
    plist.parent.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)
    plist.write_text(build_launchd_plist(executable, hour, minute, log_dir), encoding="utf-8")
    result = run(["launchctl", "bootstrap", domain, str(plist)], None)
    if result.returncode != 0:
        raise RuntimeError(f"launchctl bootstrap failed: {result.stderr.strip()}")
    return plist


def remove_macos(
    *,
    agents_dir: Path | None = None,
    run: Runner = default_runner,
    uid: int | None = None,
) -> bool:
    plist = (agents_dir or launch_agents_dir()) / f"{LABEL}.plist"
    if not plist.exists():
        return False
    domain = f"gui/{os.getuid() if uid is None else uid}"
    run(["launchctl", "bootout", f"{domain}/{LABEL}"], None)
    plist.unlink()
    return True


def _read_crontab(run: Runner) -> list[str]:
    result = run(["crontab", "-l"], None)
    return result.stdout.splitlines() if result.returncode == 0 else []


def _write_crontab(lines: list[str], run: Runner) -> None:
    text = "\n".join(lines) + "\n" if lines else ""
    result = run(["crontab", "-"], text)
    if result.returncode != 0:
        raise RuntimeError(f"crontab write failed: {result.stderr.strip()}")


def _is_marked(line: str) -> bool:
    return line.rstrip().endswith(CRON_MARKER)


def install_linux(
    executable: Executable,
    hour: int,
    minute: int,
    log_dir: Path,
    *,
    run: Runner = default_runner,
) -> str:
    line = build_cron_line(executable, hour, minute, log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    kept = [existing for existing in _read_crontab(run) if not _is_marked(existing)]
    _write_crontab([*kept, line], run)
    return line


def remove_linux(*, run: Runner = default_runner) -> bool:
    lines = _read_crontab(run)
    kept = [line for line in lines if not _is_marked(line)]
    if len(kept) == len(lines):
        return False
    _write_crontab(kept, run)
    return True
