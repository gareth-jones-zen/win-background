"""Windows notifications (toasts), for tools that run with nobody watching."""

from __future__ import annotations

import sys

from .process import powershell, quote

# Shown as coming from Windows PowerShell, which every Windows machine has registered as an app.
# A tool of our own would need registering (a Start menu shortcut with an AppUserModelID) first.
APP = "{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe"


def notify(title: str, lines: list | str) -> None:
    """Show a notification: a title and up to 600 characters of text.

    Best effort, always: it never raises, because a failed notification must never stop the run
    it's reporting on. Does nothing off Windows."""
    if sys.platform != "win32":
        return
    text = lines if isinstance(lines, str) else "\n".join(str(line) for line in lines)
    script = f"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
$t = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$t.GetElementsByTagName('text').Item(0).AppendChild($t.CreateTextNode({quote(title)})) | Out-Null
$t.GetElementsByTagName('text').Item(1).AppendChild($t.CreateTextNode({quote(text[:600])})) | Out-Null
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier({quote(APP)}).Show([Windows.UI.Notifications.ToastNotification]::new($t))
"""
    try:
        powershell(script, timeout=30)
    except Exception:
        pass
