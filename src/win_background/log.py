"""A log file that a background tool can always write to."""

from __future__ import annotations

import datetime as dt
import pathlib
from typing import Callable


def logger(path: pathlib.Path | str | None, echo: bool = True, stamp_echo: bool = True) -> Callable[[str], None]:
    """A log(text) function: appends "yyyy-mm-dd HH:MM:SS  text" to the file, and prints it too.

    Never raises. Under pythonw there's no console, so printing fails, and a log that can't be
    written (a locked or missing folder) mustn't stop the tool. stamp_echo=False prints the text
    without the time, for commands run by hand."""
    file = pathlib.Path(path) if path else None

    def log(text: str) -> None:
        line = f"{dt.datetime.now():%Y-%m-%d %H:%M:%S}  {text}"
        if echo:
            try:
                print(line if stamp_echo else text, flush=True)
            except Exception:
                pass
        if file is not None:
            try:
                file.parent.mkdir(parents=True, exist_ok=True)
                with open(file, "a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
            except OSError:
                pass

    return log
