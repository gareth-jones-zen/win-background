"""Keeping Windows awake on purpose, and reading when Windows would sleep or lock.

    from win_background import power

    request = power.SystemRequired("Nightly backup is running")   # shows in `powercfg /requests`
    request.hold(True)          # the system won't sleep from idle (the screen still turns off)
    ...
    request.hold(False)         # or request.close(); Windows also drops it when the process exits

    power.on_mains()            # True, False, or None if Windows can't tell
    power.idle_seconds()        # since the last keyboard or mouse input, in this session
    power.lock_workstation()    # what Windows+L does
    power.execution_state()     # what every process is asking for: SYSTEM_REQUIRED, DISPLAY_REQUIRED
    power.lock_settings()       # the sleep timeout, sign-in on wake, the screensaver and the inactivity limit

A power request is the documented way for a program to say "I'm busy, don't sleep": backup agents and
media players use it. It doesn't fake input, so the screensaver, and its lock, still run. Windows ends it
when the lid closes, the power button is pressed or Start > Sleep is chosen, and on battery about five
minutes after the sleep timeout. Nothing here needs admin rights, and nothing here changes a setting.
"""

from __future__ import annotations

import ctypes
import re
import subprocess
import sys
import uuid
from dataclasses import dataclass

from .process import NO_WINDOW

WINDOWS = sys.platform == "win32"

# Power setting GUIDs (powercfg /aliases).
SUB_SLEEP = "238c9fa8-0aad-41ed-83f4-97be242c8f20"
STANDBYIDLE = "29f6c1db-86da-48c5-9fdb-f2b67b1f44da"           # sleep after
SUB_VIDEO = "7516b95f-f776-4464-8c53-06167f40cc99"
VIDEOIDLE = "3c0bc021-c8a8-4e07-a973-6b14cbcb2b7e"             # turn off the display after
SUB_NONE = "fea3413e-7e05-4911-9a71-700331f1c294"
CONSOLELOCK = "0e796bdb-100d-47d6-a2d5-f7d2daa51f51"           # require a password on wakeup
POWER_POLICY_KEY = r"SOFTWARE\Policies\Microsoft\Power\PowerSettings"
DESKTOP = r"Control Panel\Desktop"
DESKTOP_POLICY = r"Software\Policies\Microsoft\Windows\Control Panel\Desktop"
SYSTEM_POLICY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System"
NEVER = 0xFFFFFFFF                                               # DelayLockInterval "Never"

ES_SYSTEM_REQUIRED, ES_DISPLAY_REQUIRED, ES_AWAYMODE_REQUIRED = 0x1, 0x2, 0x40


if WINDOWS:
    from ctypes import wintypes as wt

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _u32 = ctypes.WinDLL("user32", use_last_error=True)
    _pp = ctypes.WinDLL("powrprof", use_last_error=True)

    class _REASON_CONTEXT(ctypes.Structure):
        # Version, Flags, then a union whose largest member (the "Detailed" form) is 24 bytes on x64.
        _fields_ = [("Version", wt.ULONG), ("Flags", wt.DWORD), ("SimpleReasonString", wt.LPWSTR),
                    ("_rest", ctypes.c_byte * 16)]

    _k32.PowerCreateRequest.restype = wt.HANDLE
    _k32.PowerCreateRequest.argtypes = [ctypes.POINTER(_REASON_CONTEXT)]
    for _fn in (_k32.PowerSetRequest, _k32.PowerClearRequest):
        _fn.argtypes, _fn.restype = [wt.HANDLE, ctypes.c_int], wt.BOOL
    _k32.CloseHandle.argtypes = [wt.HANDLE]
    _k32.GetTickCount.restype = wt.DWORD

    class _SYSTEM_POWER_STATUS(ctypes.Structure):
        _fields_ = [("ACLineStatus", ctypes.c_ubyte), ("BatteryFlag", ctypes.c_ubyte),
                    ("BatteryLifePercent", ctypes.c_ubyte), ("SystemStatusFlag", ctypes.c_ubyte),
                    ("BatteryLifeTime", wt.DWORD), ("BatteryFullLifeTime", wt.DWORD)]

    class _LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", wt.UINT), ("dwTime", wt.DWORD)]

    class _GUID(ctypes.Structure):
        _fields_ = [("Data1", wt.DWORD), ("Data2", wt.WORD), ("Data3", wt.WORD), ("Data4", ctypes.c_ubyte * 8)]

        @classmethod
        def of(cls, text: str) -> "_GUID":
            u = uuid.UUID(text)
            g = cls()
            g.Data1, g.Data2, g.Data3 = u.fields[0], u.fields[1], u.fields[2]
            g.Data4[:] = u.bytes[8:]
            return g

    class _SYSTEM_POWER_CAPABILITIES(ctypes.Structure):
        _fields_ = ([(name, ctypes.c_ubyte) for name in (
            "PowerButtonPresent SleepButtonPresent LidPresent SystemS1 SystemS2 SystemS3 SystemS4 SystemS5 "
            "HiberFilePresent FullWake VideoDimPresent ApmPresent UpsPresent ThermalControl ProcessorThrottle "
            "ProcessorMinThrottle ProcessorMaxThrottle FastSystemS4 Hiberboot WakeAlarmPresent AoAc DiskSpinDown "
            "HiberFileType AoAcConnectivitySupported").split()]
            + [("spare3", ctypes.c_ubyte * 6), ("SystemBatteriesPresent", ctypes.c_ubyte),
               ("BatteriesAreShortTerm", ctypes.c_ubyte), ("BatteryScale", wt.ULONG * 6), ("_states", wt.ULONG * 5)])


def _need_windows(what: str) -> None:
    if not WINDOWS:
        raise RuntimeError(f"{what} needs Windows.")


class SystemRequired:
    """A named "keep the system awake" power request (PowerCreateRequest, PowerRequestSystemRequired).

    The reason is what `powercfg /requests` shows, so anyone can see why the machine stayed awake. Windows
    drops the request when the process exits, so a crash can't leave the machine awake. It keeps the system
    out of sleep only: the display still turns off and the screensaver still starts."""

    KIND = 1   # PowerRequestSystemRequired

    def __init__(self, reason: str):
        _need_windows("A power request")
        self.reason = reason
        self._ctx = _REASON_CONTEXT(0, 0x1, reason)   # POWER_REQUEST_CONTEXT_VERSION, _SIMPLE_STRING
        self.handle = _k32.PowerCreateRequest(ctypes.byref(self._ctx))
        if not self.handle or self.handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        self.held = False

    def hold(self, on: bool = True) -> None:
        """Set (True) or clear (False) the request. Does nothing if it's already that way."""
        if on == self.held:
            return
        call = _k32.PowerSetRequest if on else _k32.PowerClearRequest
        if not call(self.handle, self.KIND):
            raise ctypes.WinError(ctypes.get_last_error())
        self.held = on

    def close(self) -> None:
        if self.handle:
            if self.held:
                _k32.PowerClearRequest(self.handle, self.KIND)
                self.held = False
            _k32.CloseHandle(self.handle)
            self.handle = None

    def __enter__(self):
        self.hold(True)
        return self

    def __exit__(self, *exc):
        self.close()


def on_mains() -> bool | None:
    """True on mains power, False on battery, None if Windows can't tell (or off Windows)."""
    if not WINDOWS:
        return None
    status = _SYSTEM_POWER_STATUS()
    if not _k32.GetSystemPowerStatus(ctypes.byref(status)) or status.ACLineStatus == 255:
        return None
    return status.ACLineStatus == 1


def idle_seconds() -> float:
    """Seconds since the last keyboard or mouse input in this session. Programs being busy don't count,
    which is why a machine running a long job looks idle to Windows."""
    _need_windows("Idle time")
    info = _LASTINPUTINFO(ctypes.sizeof(_LASTINPUTINFO), 0)
    if not _u32.GetLastInputInfo(ctypes.byref(info)):
        raise ctypes.WinError(ctypes.get_last_error())
    return ((_k32.GetTickCount() - info.dwTime) & 0xFFFFFFFF) / 1000


def lock_workstation() -> bool:
    """Lock the session, as Windows+L does. False if Windows refused (or off Windows)."""
    return bool(WINDOWS and _u32.LockWorkStation())


def execution_state() -> int:
    """What all processes together are asking of the power manager: ES_SYSTEM_REQUIRED (0x1) and
    ES_DISPLAY_REQUIRED (0x2), from power requests or SetThreadExecutionState. A video, a call or a
    presentation usually holds DISPLAY_REQUIRED, and then Windows neither sleeps nor starts the
    screensaver. Needs no admin rights, unlike `powercfg /requests`. 0 off Windows."""
    if not WINDOWS:
        return 0
    value = wt.ULONG()
    if _pp.CallNtPowerInformation(16, None, 0, ctypes.byref(value), ctypes.sizeof(value)):   # SystemExecutionState
        return 0
    return value.value


def modern_standby() -> bool | None:
    """True on a Modern Standby (S0 low power idle) machine, where "Require sign-in" is DelayLockInterval
    rather than the "require a password on wakeup" power setting. None if unknown."""
    if not WINDOWS:
        return None
    caps = _SYSTEM_POWER_CAPABILITIES()
    if not _pp.GetPwrCapabilities(ctypes.byref(caps)):
        return None
    return bool(caps.AoAc)


# ---- Reading settings -------------------------------------------------------------------------------

def _registry(root_name: str, path: str, name: str):
    """A registry value, or None if the key or value is missing or unreadable."""
    if not WINDOWS:
        return None
    import winreg
    root = {"HKCU": winreg.HKEY_CURRENT_USER, "HKLM": winreg.HKEY_LOCAL_MACHINE}[root_name]
    try:
        with winreg.OpenKey(root, path) as handle:
            return winreg.QueryValueEx(handle, name)[0]
    except OSError:
        return None


def _number(value) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _scheme_value(subgroup: str, setting: str, ac: bool) -> tuple[int | None, str]:
    """A power setting for one power source: a Group Policy override if there is one, else the active plan
    (PowerReadACValueIndex / PowerReadDCValueIndex), else `powercfg /query`. Returns (value, source)."""
    side = "AC" if ac else "DC"
    policy = _registry("HKLM", rf"{POWER_POLICY_KEY}\{setting}", f"{side}SettingIndex")
    if policy is not None:
        return int(policy), "Group Policy"
    if WINDOWS:
        scheme = ctypes.POINTER(_GUID)()
        if _pp.PowerGetActiveScheme(None, ctypes.byref(scheme)) == 0:
            try:
                value = wt.DWORD()
                read = _pp.PowerReadACValueIndex if ac else _pp.PowerReadDCValueIndex
                if read(None, scheme, ctypes.byref(_GUID.of(subgroup)), ctypes.byref(_GUID.of(setting)),
                        ctypes.byref(value)) == 0:
                    return value.value, "power plan"
            finally:
                _k32.LocalFree(scheme)
        return _powercfg(subgroup, setting, ac), "powercfg"
    return None, "unknown"


def _powercfg(subgroup: str, setting: str, ac: bool) -> int | None:
    """The fallback: `powercfg /query`. Its labels are translated, so take the last two hex numbers (AC, DC)."""
    try:
        done = subprocess.run(["powercfg", "/query", "SCHEME_CURRENT", subgroup, setting], capture_output=True,
                              text=True, timeout=15, creationflags=NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return parse_powercfg(done.stdout, ac)


def parse_powercfg(text: str, ac: bool) -> int | None:
    found = re.findall(r":\s*0x([0-9a-fA-F]+)\s*$", text, re.MULTILINE)
    if len(found) < 2:
        return None
    return int(found[-2 if ac else -1], 16)


def sleep_timeout(ac: bool | None = None) -> tuple[int | None, str]:
    """Seconds of idle before Windows sleeps, for mains (ac=True), battery (False) or whichever it's on now.
    0 means never. Returns (seconds, where it came from)."""
    return _scheme_value(SUB_SLEEP, STANDBYIDLE, _side(ac))


def display_timeout(ac: bool | None = None) -> tuple[int | None, str]:
    """Seconds of idle before the display turns off. 0 means never."""
    return _scheme_value(SUB_VIDEO, VIDEOIDLE, _side(ac))


def _side(ac: bool | None) -> bool:
    return ac if ac is not None else on_mains() is not False


def sign_in_on_wake(ac: bool | None = None) -> tuple[int | None, str]:
    """Whether waking from sleep needs a sign-in: (seconds asleep before it does, source). 0 means every time,
    None means never.

    Settings > Accounts > Sign-in options > "If you've been away, when should Windows require you to sign
    in again?" On Modern Standby that's DelayLockInterval in HKCU\\Control Panel\\Desktop (0 every time,
    60/180/300/900 seconds, 0xFFFFFFFF never; missing is taken as every time). On S3 machines it's the
    "require a password on wakeup" power setting (CONSOLELOCK: 1 yes, 0 no). A Group Policy value for that
    setting wins on either."""
    side = _side(ac)
    policy = _registry("HKLM", rf"{POWER_POLICY_KEY}\{CONSOLELOCK}", f"{'AC' if side else 'DC'}SettingIndex")
    if policy is not None:
        return (0 if int(policy) else None), "Group Policy (require a password on wakeup)"
    if modern_standby() is not False:
        delay = _registry("HKCU", DESKTOP, "DelayLockInterval")
        if delay is None:
            return 0, "DelayLockInterval not set (every time)"
        delay = int(delay) & 0xFFFFFFFF
        return (None if delay == NEVER else delay), "DelayLockInterval"
    value, source = _scheme_value(SUB_NONE, CONSOLELOCK, side)
    return (None if value == 0 else 0), f"require a password on wakeup ({source})"


@dataclass
class Screensaver:
    active: bool
    secure: bool            # "On resume, display logon screen"
    timeout: int            # seconds
    program: str | None     # SCRNSAVE.EXE; with none chosen, no screensaver runs
    policy: bool            # any of these set by Group Policy

    @property
    def locks(self) -> bool:
        return self.active and self.secure and bool(self.program) and self.timeout > 0


def screensaver() -> Screensaver:
    """The screensaver as it applies now. SystemParametersInfo gives the effective active, timeout and
    secure values, Group Policy included; the program comes from the policy key, then the user's own."""
    _need_windows("The screensaver settings")
    values = {}
    for name, code in (("active", 0x10), ("timeout", 0x0E), ("secure", 0x76)):
        value = wt.UINT()
        _u32.SystemParametersInfoW(code, 0, ctypes.byref(value), 0)
        values[name] = value.value
    policy_values = {name: _registry("HKCU", DESKTOP_POLICY, name)
                     for name in ("ScreenSaveActive", "ScreenSaverIsSecure", "ScreenSaveTimeOut", "SCRNSAVE.EXE")}
    program = policy_values["SCRNSAVE.EXE"] or _registry("HKCU", DESKTOP, "SCRNSAVE.EXE")
    return Screensaver(bool(values["active"]), bool(values["secure"]), int(values["timeout"]), program or None,
                       any(v is not None for v in policy_values.values()))


def inactivity_limit() -> int | None:
    """"Interactive logon: Machine inactivity limit" (InactivityTimeoutSecs): Windows locks after this many
    seconds of idle, whatever else is set. None if not set."""
    value = _number(_registry("HKLM", SYSTEM_POLICY, "InactivityTimeoutSecs"))
    return value or None


@dataclass
class LockSettings:
    """Everything that decides when an idle Windows session locks, for one power source."""
    on_mains: bool
    sleep_after: int | None            # seconds; 0 never; None unreadable
    sleep_source: str
    display_off_after: int | None
    sign_in_after_sleep: int | None    # seconds asleep before sign-in is needed; 0 every time; None never
    sign_in_source: str
    screensaver: Screensaver | None
    inactivity_limit: int | None
    modern_standby: bool | None = None


def lock_settings(ac: bool | None = None) -> LockSettings:
    """Read every setting that decides when an idle session locks. Read-only; changes nothing."""
    _need_windows("Lock settings")
    side = _side(ac)
    sleep, sleep_source = sleep_timeout(side)
    display, _ = display_timeout(side)
    sign_in, sign_in_source = sign_in_on_wake(side)
    try:
        saver = screensaver()
    except OSError:
        saver = None
    return LockSettings(side, sleep, sleep_source, display, sign_in, sign_in_source, saver, inactivity_limit(),
                        modern_standby())
