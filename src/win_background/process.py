"""Starting child processes without a console window flashing up."""

from __future__ import annotations

import subprocess
import sys

# CREATE_NO_WINDOW. A tool running under pythonw has no console, so every child it starts would
# otherwise get a console window of its own.
NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def powershell(script: str, timeout: float = 120) -> subprocess.CompletedProcess:
    return subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                          capture_output=True, text=True, timeout=timeout, creationflags=NO_WINDOW)


def quote(text: str) -> str:
    """A PowerShell single-quoted string."""
    return "'" + str(text).replace("'", "''") + "'"
