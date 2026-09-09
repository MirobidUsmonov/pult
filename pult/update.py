"""
Updating itself.

When a new version appears the user should have to do nothing: on
startup the program checks the source and, if it is newer, replaces
itself and restarts.

Windows will not let a running .exe be overwritten, but it will let it
be RENAMED. That is what makes the swap work: the old file is moved
aside, the new one takes its name, the new one is launched and the old
one exits. The file that was moved aside is deleted on the next run.

There are no version numbers - files are compared by hash. A build
should not have to remember to bump anything: if the file at the source
differs, that is the new one.
"""
from __future__ import annotations

import hashlib
import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

log = logging.getLogger("pult.update")

NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

EXE_NAME = "Pult.exe"
STAGED = "Pult.exe.new"


def running_exe() -> Path | None:
    """The running .exe. None when started from source."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve()
    return None


def digest(path: Path) -> str:
    """The file's SHA-256. Empty string if it cannot be read."""
    try:
        h = hashlib.sha256()
        with path.open("rb") as f:
            for block in iter(lambda: f.read(1 << 20), b""):
                h.update(block)
        return h.hexdigest()
    except OSError:
        return ""


# ------------------------------------------------------------ source

def from_folder(source: str) -> Path | None:
    """Pult.exe in a folder, which may also be a network share."""
    if not source:
        return None
    base = Path(source)
    candidate = base if base.is_file() else base / EXE_NAME
    return candidate if candidate.is_file() else None


async def from_url(source: str, dest: Path) -> Path | None:
    """Downloads from an address.

    The final name is only given once the download finishes, so a
    half-downloaded file is never put in the program's place.
    """
    if not source:
        return None

    import aiohttp

    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        # No overall timeout: the file is over 20 MB and takes a long
        # time on a slow link. The limit only applies to waiting.
        timeout = aiohttp.ClientTimeout(total=None, sock_connect=30, sock_read=120)
        async with aiohttp.ClientSession(timeout=timeout) as sess:
            async with sess.get(source) as r:
                r.raise_for_status()
                with tmp.open("wb") as f:
                    async for chunk in r.content.iter_chunked(256 * 1024):
                        f.write(chunk)
        tmp.replace(dest)
        return dest
    except Exception as exc:
        log.warning("update download failed: %s: %s", type(exc).__name__, exc)
        tmp.unlink(missing_ok=True)
        return None


async def candidate(cfg) -> Path | None:
    """Path to the source file when it differs from the current one."""
    current = running_exe()
    if current is None:
        return None

    mode = (cfg.update.mode or "off").lower()
    if mode in ("off", "", "none"):
        return None

    if mode == "folder":
        found = from_folder(cfg.update.source)
    elif mode == "url":
        found = await from_url(cfg.update.source, current.parent / STAGED)
    else:
        log.warning("unknown update mode: %s", mode)
        return None

    if found is None:
        return None
    if digest(found) == digest(current):
        return None
    return found


# ------------------------------------------------------------ applying

def swap(new: Path, current: Path) -> bool:
    """Puts the new file in the program's place."""
    old = current.with_name(current.name + ".old")
    try:
        old.unlink(missing_ok=True)
    except OSError:
        # Left over from an earlier swap and possibly still in use
        old = current.with_name(f"{current.name}.old-{os.getpid()}")
    try:
        current.rename(old)
    except OSError as exc:
        log.warning("could not move the old file aside: %s", exc)
        return False
    try:
        shutil.copy2(new, current)
        return True
    except OSError as exc:
        log.error("could not put the new file in place: %s", exc)
        # Put the old one back - never leave the machine without an agent
        try:
            old.rename(current)
        except OSError:
            log.error("the old file could not be restored either: %s", old)
        return False


def cleanup(folder: Path) -> None:
    """Files left over from an earlier swap. They are free by now.

    ".eski" is the name earlier builds used; it stays in the list so an
    upgrade from one of those does not leave a stray 20 MB file behind.
    """
    patterns = [EXE_NAME + ".old*", EXE_NAME + ".eski*", STAGED, "Pult.exe.yangi"]
    for pattern in patterns:
        for stale in folder.glob(pattern):
            try:
                stale.unlink()
            except OSError:
                pass


def clean_env() -> dict:
    """The environment with PyInstaller's internals stripped out.

    A one-file build passes its state through environment variables
    (_PYI_..., _MEIPASS2). When the program launches itself from the
    inside, those are inherited and the new copy believes it is already
    a child process. It then checks its parent and fails with "parent
    process has different executable" - especially after an update,
    because the parent's file has been moved aside. So they are removed.
    """
    env = dict(os.environ)
    for key in list(env):
        if key.startswith("_PYI_") or key in ("_MEIPASS2", "_MEIPASS"):
            env.pop(key, None)
    return env


def relaunch(exe: Path) -> None:
    try:
        subprocess.Popen([str(exe)], cwd=str(exe.parent),
                         creationflags=NO_WINDOW, env=clean_env())
    except Exception:
        log.exception("the new version did not start")


async def apply_if_any(cfg, launch: bool = True) -> bool:
    """Applies an update when there is one.

    With launch=True the new copy is started as well, which is only
    correct before the server is up. In a running program use
    launch=False and stop the old copy first: otherwise the new copy
    finds the port taken and exits with an error dialog.

    Returns True when the program was replaced.
    """
    current = running_exe()
    if current is None:
        return False
    cleanup(current.parent)

    try:
        new = await candidate(cfg)
    except Exception:
        log.warning("could not check for updates", exc_info=True)
        return False
    if new is None:
        return False

    log.info("new version found: %s", new)
    if not swap(new, current):
        return False
    if launch:
        log.info("updated, restarting")
        relaunch(current)
    else:
        log.info("updated - the new copy starts once this one stops")
    return True


async def watch(cfg, busy, on_ready) -> None:
    """Also checks periodically while running.

    An update is only applied when nobody is connected: being cut off
    mid-stream is worse than waiting for the update.
    """
    import asyncio

    minutes = max(int(cfg.update.check_minutes or 0), 1)
    while True:
        await asyncio.sleep(minutes * 60)
        if busy():
            continue
        try:
            if await apply_if_any(cfg, launch=False):
                on_ready()
                return
        except asyncio.CancelledError:
            raise
        except Exception:
            log.warning("the update check failed", exc_info=True)


def describe(cfg) -> str:
    """A short status line for the tray menu and the log."""
    mode = (cfg.update.mode or "off").lower()
    if mode in ("off", "", "none"):
        return "updates disabled"
    return f"update source: {cfg.update.source or '(not set)'}"


def stamp() -> str:
    """A marker for the running file, to tell builds apart."""
    exe = running_exe()
    if exe is None:
        return "from source"
    try:
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(exe.stat().st_mtime))
    except OSError:
        when = "?"
    return f"{digest(exe)[:8]} ({when})"
