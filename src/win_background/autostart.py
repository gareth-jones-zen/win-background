"""Starting a tool at logon, or daily, through Windows Task Scheduler.

    task = Task("my-tool watch", "Watches for new things (my-tool watch).")
    exe, args = python_command("my_tool", "--config", str(config), "watch")
    task.enable(exe, args, working_dir=config.parent)          # at every logon, and now
    task.enable(exe, args, daily_at="09:00", keep_running=False) # a daily job, also at logon
    task.status()   # None if there's no task, else state, command, last_run, last_result, next_run
    task.disable()

The task runs as you, only while you're logged on, so it needs no admin rights. It catches up a
start missed while the laptop was off or asleep. A watcher (keep_running) is restarted if it
crashes and never stopped for running too long; a job is stopped after time_limit.

Run the tool's own executable directly, never through a wrapper such as a console window or
`powershell -File`. doc-filer learned both lessons in October 2026: a hidden PowerShell window
could still be closed, which stopped the watcher, and stopping the task stopped only the wrapper,
leaving the watcher running on old code.
"""

from __future__ import annotations

import datetime as dt
import pathlib
import subprocess
import sys

from .process import powershell, quote

NOT_RUN_YET, RUNNING = "267011", "267009"   # SCHED_S_TASK_HAS_NOT_RUN, SCHED_S_TASK_RUNNING


def python_command(module: str, *args: str) -> tuple[str, list]:
    """How to start `python -m module args` with no console window: pythonw.exe when it's there.

    In a PyInstaller build, the executable itself, or a "<name>-background.exe" beside it if the
    build made one (a windowless twin, as teams-transcripts does)."""
    if getattr(sys, "frozen", False):
        exe = pathlib.Path(sys.executable)
        background = exe.with_name(f"{exe.stem}-background{exe.suffix}")
        return str(background if background.exists() else exe), list(args)
    python = pathlib.Path(sys.executable)
    windowless = python.with_name("pythonw.exe")
    return str(windowless if windowless.exists() else python), ["-m", module, *args]


class Task:
    def __init__(self, name: str, description: str = "", results: dict | None = None):
        """results: what the tool's own exit codes mean, for status(), e.g. {"3": "another was running"}."""
        self.name = name
        self.description = description or name
        self.results = results or {}

    def enable(self, exe: str, args: list, working_dir: pathlib.Path | str | None = None, *,
               at_logon: bool = True, daily_at: str | None = None, keep_running: bool = True,
               time_limit: dt.timedelta = dt.timedelta(minutes=30), start_now: bool = True) -> str:
        """Create or replace the task, and start it now unless start_now is False. Returns the command line.

        keep_running: a watcher. Restarted up to 3 times, 5 minutes apart, if it exits with an error,
        and never stopped for time. Otherwise a job, stopped after time_limit."""
        if sys.platform != "win32":
            raise RuntimeError("autostart needs Windows Task Scheduler.")
        if not (at_logon or daily_at):
            raise ValueError("A task needs a trigger: at_logon, daily_at, or both.")
        line = subprocess.list2cmdline(args)
        triggers = []
        if daily_at:
            triggers.append(f"(New-ScheduledTaskTrigger -Daily -At {quote(daily_at)})")
        if at_logon:
            triggers.append("(New-ScheduledTaskTrigger -AtLogOn -User $me)")
        if keep_running:
            limits = "-ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 5)"
        else:
            limits = f"-ExecutionTimeLimit (New-TimeSpan -Seconds {int(time_limit.total_seconds())})"
        folder = str(pathlib.Path(working_dir).resolve() if working_dir else pathlib.Path.home())
        done = powershell(f"""
$ErrorActionPreference = 'Stop'
$me = "$env:USERDOMAIN\\$env:USERNAME"
$action = New-ScheduledTaskAction -Execute {quote(exe)} -Argument {quote(line)} -WorkingDirectory {quote(folder)}
$triggers = @({', '.join(triggers)})
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    {limits} -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId $me -LogonType Interactive -RunLevel Limited
if (Get-ScheduledTask -TaskName {quote(self.name)} -ErrorAction SilentlyContinue) {{ Stop-ScheduledTask -TaskName {quote(self.name)} }}
Register-ScheduledTask -TaskName {quote(self.name)} -Action $action -Trigger $triggers -Settings $settings -Principal $principal `
    -Description {quote(self.description)} -Force | Out-Null
{"Start-ScheduledTask -TaskName " + quote(self.name) if start_now else ""}
""")
        if done.returncode:
            raise RuntimeError(f"Couldn't create the task: {(done.stderr or done.stdout).strip()[:400]}")
        return f"{exe} {line}"

    def disable(self) -> bool:
        """Stop and remove the task. False if there wasn't one."""
        done = powershell(f"""
$task = Get-ScheduledTask -TaskName {quote(self.name)} -ErrorAction SilentlyContinue
if ($task) {{ Stop-ScheduledTask -TaskName {quote(self.name)}; Unregister-ScheduledTask -TaskName {quote(self.name)} -Confirm:$false; 'removed' }}
exit 0
""")
        if done.returncode:
            raise RuntimeError(f"Couldn't remove the task: {(done.stderr or done.stdout).strip()[:400]}")
        return "removed" in done.stdout

    def status(self) -> dict | None:
        """None if there's no task; otherwise state, command, last_run, last_result and next_run."""
        if sys.platform != "win32":
            return None
        done = powershell(f"""
$task = Get-ScheduledTask -TaskName {quote(self.name)} -ErrorAction SilentlyContinue
if ($task) {{
  $info = Get-ScheduledTaskInfo -TaskName {quote(self.name)}
  "state=$($task.State)"
  "command=$($task.Actions[0].Execute) $($task.Actions[0].Arguments)"
  "last_run=$($info.LastRunTime)"
  "last_result=$($info.LastTaskResult)"
  "next_run=$($info.NextRunTime)"
}}
exit 0
""")
        return self.parse_status(done.stdout)

    def parse_status(self, text: str) -> dict | None:
        if not text.strip():
            return None
        info = dict(line.split("=", 1) for line in text.strip().splitlines() if "=" in line)
        result = info.get("last_result")
        if result == NOT_RUN_YET:
            info["last_run"], info["last_result"] = "never", "-"
        elif result == RUNNING:
            info["last_result"] = "running"
        elif result in self.results:
            info["last_result"] = f"{result} ({self.results[result]})"
        return info
