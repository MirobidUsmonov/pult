"""
Windows system commands: lock, sleep, shut down, launch a program.

All of these can be switched off in the settings
(security.allow_commands) for anyone who wants viewing only.
"""
from __future__ import annotations

import ctypes
import os
import subprocess
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)

HWND_BROADCAST = 0xFFFF
WM_SYSCOMMAND = 0x0112
SC_MONITORPOWER = 0xF170

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _run(args: list[str]) -> None:
    subprocess.Popen(args, creationflags=_NO_WINDOW,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def lock() -> None:
    user32.LockWorkStation()


def display_off() -> None:
    """Turns the monitors off (the computer keeps running)."""
    user32.SendMessageW(HWND_BROADCAST, WM_SYSCOMMAND, SC_MONITORPOWER, 2)


def display_on() -> None:
    """Wakes the monitor.

    Turning it back on with SC_MONITORPOWER is unreliable, so we nudge
    the mouse by one pixel instead - that always wakes the system.
    """
    from . import win_input as wi

    x, y = wi.cursor_pos()
    wi.move_to(x + 1, y)
    wi.move_to(x, y)


def sleep() -> None:
    # Second argument 0 = sleep (1 would be hibernate)
    _run(["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"])


def hibernate() -> None:
    _run(["shutdown", "/h"])


def shutdown(delay: int = 0) -> None:
    _run(["shutdown", "/s", "/t", str(delay)])


def reboot(delay: int = 0) -> None:
    _run(["shutdown", "/r", "/t", str(delay)])


def logoff() -> None:
    _run(["shutdown", "/l"])


def cancel_shutdown() -> None:
    _run(["shutdown", "/a"])


def run_program(command: str) -> None:
    """Opens a program or a file.

    shell=True is deliberate: it makes "notepad", "C:\\folder",
    "https://..." and an ".mp4" file all work the same way, with Windows
    picking the right program for each.
    """
    subprocess.Popen(command, shell=True, creationflags=_NO_WINDOW,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def foreground_window_title() -> str:
    """Title of the window in the foreground. Useful context for an AI agent."""
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return ""
    length = user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


class _ENTRY(ctypes.Structure):
    """PROCESSENTRY32W - only the fields up to the name are needed."""
    _fields_ = [
        ("dwSize", ctypes.c_ulong),
        ("cntUsage", ctypes.c_ulong),
        ("th32ProcessID", ctypes.c_ulong),
        ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
        ("th32ModuleID", ctypes.c_ulong),
        ("cntThreads", ctypes.c_ulong),
        ("th32ParentProcessID", ctypes.c_ulong),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", ctypes.c_ulong),
        ("szExeFile", ctypes.c_wchar * 260),
    ]


def first_running(names: list[str]) -> str:
    """The first of these executables that is running, or "".

    A plain snapshot of the process table rather than WMI or an extra
    package: this is asked every few seconds while someone is watching,
    and WMI would cost far more than the question is worth.

    Only the executable name is compared, so nothing here depends on
    where a program was installed.
    """
    if not names:
        return ""
    wanted = {n.strip().lower() for n in names if n and n.strip()}
    if not wanted:
        return ""

    TH32CS_SNAPPROCESS = 0x00000002
    INVALID = ctypes.c_void_p(-1).value

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    kernel32.Process32FirstW.argtypes = [ctypes.c_void_p, ctypes.POINTER(_ENTRY)]
    kernel32.Process32NextW.argtypes = [ctypes.c_void_p, ctypes.POINTER(_ENTRY)]
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]

    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snap or snap == INVALID:
        return ""
    try:
        entry = _ENTRY()
        entry.dwSize = ctypes.sizeof(_ENTRY)
        if not kernel32.Process32FirstW(snap, ctypes.byref(entry)):
            return ""
        while True:
            if entry.szExeFile.lower() in wanted:
                return entry.szExeFile
            if not kernel32.Process32NextW(snap, ctypes.byref(entry)):
                return ""
    finally:
        kernel32.CloseHandle(snap)


COMMANDS = {
    "lock": lock,
    "sleep": sleep,
    "hibernate": hibernate,
    "shutdown": shutdown,
    "reboot": reboot,
    "logoff": logoff,
    "cancel_shutdown": cancel_shutdown,
    "display_off": display_off,
    "display_on": display_on,
}
