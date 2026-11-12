"""Running Python tools quietly in the background on Windows.

    from win_background import NO_WINDOW, Task, exclusive, logger, notify, python_command, single_instance
    from win_background import power     # power requests, idle time, and when Windows would sleep or lock

Shared by teams-transcripts, teams-channel-notes and org-directory, which each had their own copy
of all of this until November 2026, so a fix had to be made three times.
"""

from . import power
from .autostart import Task, python_command
from .locks import Lock, exclusive, key, single_instance
from .log import logger
from .notify import notify
from .process import NO_WINDOW

__version__ = "0.3.0"
__all__ = ["NO_WINDOW", "Lock", "Task", "exclusive", "key", "logger", "notify", "power", "python_command", "single_instance"]
