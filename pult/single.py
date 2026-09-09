"""
Bitta nusxa qulfi.

Bir vaqtda ikkita agent ishlasa hammasi buziladi: ular port uchun
kurashadi, ekranni ikkalasi oladi va o'z-o'zini yangilashda bir-birining
faylini almashtirib yuboradi. Amalda shu ko'rilgan - qayta ishga
tushirishlardan keyin to'rtta nusxa yig'ilib qolgan, eskisi portni
ushlab turgan, fayli esa allaqachon boshqasiga almashtirilgani uchun
u modullarini o'qiy olmay 500 xato bergan.

Windows'da nomlangan muteks ishlatiladi: jarayon tugashi bilan tizim
uni o'zi bo'shatadi, ya'ni dastur qulab tushsa ham qulf osilib
qolmaydi. Fayl qulfida bunday kafolat yo'q.

Kutish vaqti bor, chunki yangilanishdan keyin eski nusxa chiqib
ulgurmagan bo'lishi mumkin: yangisi uni bir oz kutib turadi.
"""
from __future__ import annotations

import logging
import sys
import time

log = logging.getLogger("pult.single")

NAME = "Local\\PultAgentYagona"

_handle = None


def acquire(timeout: float = 0.0) -> bool:
    """Qulfni oladi. Boshqa nusxa ushlab tursa - False.

    Qulf jarayon tugaguncha saqlanadi; ataylab bo'shatish kerak emas.
    """
    global _handle
    if sys.platform != "win32":
        # Boshqa tizimlarda portning o'zi qulf vazifasini bajaradi:
        # ikkinchi nusxa baribir ko'tarila olmaydi
        return True

    import ctypes
    from ctypes import wintypes

    ERROR_ALREADY_EXISTS = 183
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]

    end = time.monotonic() + max(timeout, 0.0)
    while True:
        handle = kernel32.CreateMutexW(None, True, NAME)
        if handle and ctypes.get_last_error() != ERROR_ALREADY_EXISTS:
            _handle = handle
            return True
        if handle:
            kernel32.CloseHandle(handle)
        if time.monotonic() >= end:
            return False
        # Eski nusxa chiqishini kutamiz - yangilanishdan keyin u bir
        # necha soniya jonli qolishi mumkin
        time.sleep(0.5)
