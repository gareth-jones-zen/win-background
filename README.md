# win-background

Running Python tools quietly in the background on Windows: start them at logon, tell someone
when something goes wrong, keep to one copy at a time, and keep a log.

Shared by [teams-transcripts](https://github.com/gareth-jones-zen/teams-transcripts),
[teams-channel-notes](https://github.com/gareth-jones-zen/teams-channel-notes) and
[org-directory](https://github.com/gareth-jones-zen/org-directory). Until November 2026 each had
its own copy of all of this, so every fix was made three times. Microsoft Graph sign-in is a
separate library, [m365-graph](https://github.com/gareth-jones-zen/m365-graph).

```python
from win_background import Task, exclusive, key, logger, notify, power, python_command, single_instance

log = logger(r"C:\Users\me\AppData\Local\my-tool\my-tool.log")

lock = single_instance(f"my-tool-watch-{key(config_path)}")   # keep it for the life of the watcher
if lock is None:
    log("another watcher is already running; not starting a second")

with exclusive(f"my-tool-run-{key(config_path)}", timeout=600):   # one sync at a time
    ...

notify("my-tool couldn't sign in", ["At a console, run: my-tool sign-in"])

task = Task("my-tool watch", "Watches for new things (my-tool watch).")
task.enable(*python_command("my_tool", "--config", str(config_path), "watch"), working_dir=config_path.parent)
task.status()    # None, or state, command, last_run, last_result, next_run
task.disable()

with power.SystemRequired("Nightly backup is running"):   # no sleep from idle until the block ends
    ...
```

## Install

```
pip install git+https://github.com/gareth-jones-zen/win-background
```

Or, from a checkout, `pip install -e .` so changes apply at once. Python 3.11+, nothing else.
Everything but the locks and the log does nothing, or refuses, off Windows.

## What it does, and why

Each point came from a tool running unattended on a laptop.

**`Task`: Task Scheduler, not the Startup folder or a service.**
- **It runs as you, only while you're logged on**, so it needs no admin rights and can use your browser profile and sign-in.
- **It catches up a start missed** while the laptop was off or asleep (`StartWhenAvailable`).
- **A watcher (`keep_running=True`, the default) is restarted if it exits with an error**, up to 3 times, 5 minutes apart, **and is never stopped for running too long.** Task Scheduler's default stops a task after 3 days.
- **A job (`keep_running=False`) is stopped after `time_limit`** (30 minutes by default). `daily_at="09:00"` adds a daily trigger; `at_logon` stays on unless you turn it off.
- **`every=timedelta(minutes=15)` repeats a job indefinitely** (0.2.0), for work that has to be picked up without a watcher, such as a queue to drain. A run still going when the next is due finishes, and that next one is skipped. Without `-RepetitionDuration` Windows repeats for ever; `[TimeSpan]::MaxValue`, the usual workaround, is refused by Windows 10 and later.
- **Enabling replaces any existing task of that name**, stopping it first, and **waits for its process to exit** before starting the new one (0.2.1).
- **`task.restart()` is how a watcher picks up new code.** Windows' own stop returns before the process exits, so a stop followed straight away by a start loses the race: the new watcher finds the old one still holding its lock and exits, then the old one exits too, and nothing is running. That happened to three watchers at once on 11 Nov 2026.
- **Run your tool's own executable, never a wrapper.** doc-filer learned both halves in October 2026: a hidden PowerShell window could still be closed, which stopped the watcher, and stopping the task stopped only the wrapper, leaving the watcher running old code. `python_command()` gives `pythonw.exe -m <module> …`, which has no window. In a PyInstaller build it gives the executable itself, or a `<name>-background.exe` beside it if your build makes a windowless twin.
- **`status()` turns Task Scheduler's codes into words**: "never", "running", and your own exit codes through `Task(results={"3": "another watcher was already running"})`.

**`notify`: a Windows notification, best effort.** It never raises, because a failed notification mustn't stop the run it's reporting on. The text is cut to 600 characters. It shows as coming from Windows PowerShell, the one app every machine has registered for notifications. **Notify for errors and for things someone has to act on; success should be silent** (a notification per success trains people to ignore them).

**Locks: `single_instance` for a watcher, `exclusive` for a run.** On Windows they're named mutexes, which Windows releases when the holding process dies, so **a crash can never leave a lock stuck**, as a lock file can. Elsewhere, file locks, which the OS also releases. Name a lock after what it protects: `key(path)` turns a settings file or browser profile path into 16 characters, ignoring case. teams-transcripts keys its watcher lock on the Edge profile, because two watchers on one profile fight over it.

**`power`: keeping Windows awake on purpose, and when it would sleep or lock** (0.3.0).
- **`power.SystemRequired(reason)`** is a named power request (`PowerCreateRequest`, SystemRequired): `hold(True)` keeps the system out of sleep, `hold(False)` or `close()` lets it go, and Windows drops it when the process exits, so a crash can't leave the machine awake. **The reason is what `powercfg /requests` shows**, so anyone can see why. It doesn't fake input: the display still turns off and the screensaver, and its lock, still start. Windows ends it when the lid closes or the power button is pressed, and on battery about five minutes after the sleep timeout.
- **`power.on_mains()`** (True, False, or None if unknown), **`power.idle_seconds()`** (since the last keyboard or mouse input) and **`power.lock_workstation()`** (Windows+L).
- **`power.execution_state()`**: what every process together is asking for, `ES_SYSTEM_REQUIRED` (0x1) and `ES_DISPLAY_REQUIRED` (0x2). A call, a video or a presentation usually holds the display on. Unlike `powercfg /requests`, it needs no admin rights.
- **`power.lock_settings()`** reads everything that decides when an idle session locks, for the current power source: the sleep and display-off timeouts (the active plan, a Group Policy value winning, `powercfg` as a fallback), whether waking needs a sign-in (`DelayLockInterval` on Modern Standby, the "require a password on wakeup" setting elsewhere), the screensaver as it applies (`SystemParametersInfo`, policy included) and the machine inactivity limit. **Read-only: nothing here changes a setting.**
- Used by [claude-keep-awake](https://github.com/gareth-jones-zen/claude-keep-awake). Also useful for any long unattended job on a laptop.

**`logger`: a log that can always be written.** Each line is stamped `yyyy-mm-dd HH:MM:SS`. It also prints the line, except under `pythonw`, which has no console, where printing would raise. A locked or missing log file is skipped rather than stopping the tool. **Log `alive` once an hour from a watcher**, so a silent log means a stuck watcher rather than a quiet day.

`NO_WINDOW` is `CREATE_NO_WINDOW`: pass it as `creationflags` when a background tool starts a child process, or each child gets a console window of its own.

## Tests

```
python -m unittest discover -s tests
```

They take and release real locks across processes, and on Windows check a task that doesn't exist, read the power settings, and set and clear a power request.
