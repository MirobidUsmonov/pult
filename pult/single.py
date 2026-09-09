"""
Single-instance lock.

Two agents running at once breaks everything: they fight over the port,
both grab the screen, and during a self-update they swap each other's
executable. This is not hypothetical — after a few restarts four copies
had piled up, the oldest still holding the port while its own file had
already been replaced, so it could no longer read its own modules and
answered every request with a 500.

Windows named mutexes are used because the system releases them when the
process ends, so a crash cannot leave the lock stuck. A lock file gives
no such guarantee.

There is a wait, because right after an update the old copy may not have
exited yet: the new one gives it a moment.
"""
from __future__ import annotations

import logging
import sys
import time

log = logging.getLogger("pult.single")

NAME = "Local\\PultAgentSingle"

_handle = None


def acquire(timeout: float = 0.0) -> bool:
    """Takes the lock. Returns False if another copy holds it.

    The lock lives until the process ends; releasing it explicitly is
    not necessary.
    """
    global _handle
    if sys.platform != "win32":
        # Elsewhere the port itself acts as the lock: a second copy
        # cannot bind it anyway
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
        # Wait for the old copy to exit - after an update it can stay
        # alive for a few more seconds
        time.sleep(0.5)
