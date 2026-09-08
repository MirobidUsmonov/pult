"""
Windows tizim buyruqlari: qulflash, uxlatish, o'chirish, dastur ochish.

Bularning hammasi sozlamalarda o'chirilishi mumkin (security.allow_commands) -
kimdir dasturni faqat kuzatish uchun ishlatmoqchi bo'lsa.
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
    """Monitorlarni o'chiradi (kompyuter ishlashda davom etadi)."""
    user32.SendMessageW(HWND_BROADCAST, WM_SYSCOMMAND, SC_MONITORPOWER, 2)


def display_on() -> None:
    """Monitorni uyg'otadi.

    SC_MONITORPOWER bilan yoqish ishonchsiz, shuning uchun sichqonchani
    bir piksel qimirlatamiz - bu tizimni har doim uyg'otadi.
    """
    from . import win_input as wi

    x, y = wi.cursor_pos()
    wi.move_to(x + 1, y)
    wi.move_to(x, y)


def sleep() -> None:
    # Ikkinchi argument 0 = uxlash (1 bo'lsa hibernate)
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
    """Dastur yoki faylni ochadi.

    shell=True ataylab: shunda "notepad", "C:\\papka", "https://..." va
    ".mp4" fayli - hammasi bir xil ishlaydi, Windows o'zi mos dasturni
    tanlaydi.
    """
    subprocess.Popen(command, shell=True, creationflags=_NO_WINDOW,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def foreground_window_title() -> str:
    """Hozir faol oynaning sarlavhasi. AI agent uchun foydali kontekst."""
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return ""
    length = user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


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
