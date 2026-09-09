"""
The tray icon - the program's console-free face.

This is how Pult normally starts: no terminal window, driven from a
small icon next to the clock. The server runs on its own thread and the
icon on the main one, which is what a Windows tray icon requires.
"""
from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
import threading
from pathlib import Path

from . import app as appmod
from . import config as cfgmod
from . import window

log = logging.getLogger("pult.tray")


def _icon_image():
    from PIL import Image

    for name in ("favicon.png", "icon-192.png"):
        path = _web_icons() / name
        if path.is_file():
            return Image.open(path).convert("RGBA")
    # The program must run even when the icon file is missing
    return Image.new("RGBA", (64, 64), (77, 163, 255, 255))


def _web_icons() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", ".")) / "web" / "icons"
    return Path(__file__).resolve().parents[1] / "web" / "icons"


def _open_path(path: Path) -> None:
    try:
        if sys.platform == "win32":
            os.startfile(str(path))  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except Exception:
        log.exception("could not open: %s", path)


def _copy(text: str) -> None:
    """Puts text on the clipboard (without an external library)."""
    if sys.platform != "win32":
        return
    import ctypes
    from ctypes import wintypes

    CF_UNICODETEXT = 13
    GMEM_MOVEABLE = 0x0002

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalLock.restype = ctypes.c_void_p

    data = text.encode("utf-16-le") + b"\x00\x00"
    handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
    ptr = kernel32.GlobalLock(handle)
    ctypes.memmove(ptr, data, len(data))
    kernel32.GlobalUnlock(handle)
    if user32.OpenClipboard(None):
        try:
            user32.EmptyClipboard()
            user32.SetClipboardData(CF_UNICODETEXT, handle)
        finally:
            user32.CloseClipboard()


class TrayApp:
    def __init__(self, cfg: cfgmod.Config) -> None:
        self.cfg = cfg
        self.loop: asyncio.AbstractEventLoop | None = None
        self.stop_event: asyncio.Event | None = None
        self.ready = threading.Event()
        self.error: BaseException | None = None
        self.icon = None
        # When an update has been applied, the file to start after
        # quitting. Started before the server stops, the new copy would
        # find the port taken.
        self.relaunch: Path | None = None

    # -- the server thread -------------------------------------------------

    def _run_server(self) -> None:
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.stop_event = asyncio.Event()
        try:
            self.loop.run_until_complete(self._serve())
        except BaseException as exc:  # noqa: BLE001 - pass the error to the icon
            self.error = exc
            log.exception("the server stopped")
            self.ready.set()
        finally:
            try:
                self.loop.close()
            except Exception:
                pass

    async def _serve(self) -> None:
        server = appmod.build_server(self.cfg)
        await server.start()
        # The icon appears as soon as the server is up: waiting for the
        # tunnel can take several seconds, and during that time the
        # program looked like it had not started.
        self.ready.set()
        tun = await appmod.start_remote(self.cfg)
        tracker = asyncio.create_task(appmod.track_public_url(self.cfg, tun)) if tun else None
        notifier = appmod.start_notifier(self.cfg, tun)
        updater = self._start_updater(server)
        log.info("Pult is running in the tray - %s", appmod.phone_url(self.cfg))
        try:
            assert self.stop_event
            await self.stop_event.wait()
        finally:
            for task in (notifier, tracker, updater):
                if task:
                    task.cancel()
            if tun:
                await tun.stop()
            await server.stop()

    def _start_updater(self, server) -> asyncio.Task | None:
        """Watches for updates while running.

        An update is only applied while nobody is connected - dropping
        out mid-stream is worse than staying on the old version. Once
        the new copy starts, this one leaves the tray.
        """
        if not getattr(sys, "frozen", False):
            return None
        from . import update as updatemod

        if (self.cfg.update.mode or "off").lower() in ("off", "", "none"):
            return None
        def done() -> None:
            self.relaunch = updatemod.running_exe()
            self.quit()

        return asyncio.create_task(
            updatemod.watch(
                self.cfg,
                busy=lambda: bool(server.ctx.sessions),
                on_ready=done,
            ),
            name="pult-update",
        )

    # -- the menu ----------------------------------------------------------

    def _menu(self):
        import pystray

        def item(label, fn, **kw):
            return pystray.MenuItem(label, lambda *_: fn(), **kw)

        return pystray.Menu(
            pystray.MenuItem(self.cfg.host_name, None, enabled=False),
            pystray.Menu.SEPARATOR,
            # The main window, opened by clicking the icon - this is
            # where the phone appears. The default action used to be the
            # QR page, and people could not find where to see the phone.
            item("Pult window", self.open_viewer, default=True),
            item("Connect a phone (QR)", self.open_pair),
            item("Copy the link", self.copy_link),
            pystray.Menu.SEPARATOR,
            item("Update now", self.update_now),
            item("Logs", lambda: _open_path(cfgmod.log_path())),
            item("Settings folder", lambda: _open_path(cfgmod.config_dir())),
            pystray.Menu.SEPARATOR,
            item("Quit", self.quit),
        )

    def open_pair(self) -> None:
        window.open_url(appmod.pair_url(self.cfg), size=(560, 780))

    def open_viewer(self) -> None:
        """Opens the phone screen in its own window on the computer."""
        window.open_url(appmod.viewer_url(self.cfg), size=(980, 720))

    def update_now(self) -> None:
        """Checks for an update at once, without waiting for its turn."""
        from . import update as updatemod

        if not getattr(sys, "frozen", False):
            self.notify("Updating only works in a built program")
            return
        if (self.cfg.update.mode or "off").lower() in ("off", "", "none"):
            self.notify("Updating is not set up",
                        "Set it up:  Pult.exe --update <folder or link>")
            return
        if not self.loop:
            return

        async def run() -> None:
            if await updatemod.apply_if_any(self.cfg, launch=False):
                self.relaunch = updatemod.running_exe()
                self.quit()
            else:
                self.notify("No update available", updatemod.stamp())

        asyncio.run_coroutine_threadsafe(run(), self.loop)

    def copy_link(self) -> None:
        url = appmod.phone_url(self.cfg)
        _copy(url)
        # Saying which link was copied matters: a local link will not
        # open from another network, and it is better to know that in
        # advance.
        if self.cfg.public_url:
            self.notify("Remote link copied", url)
        elif self.cfg.remote.mode != "off":
            self.notify("Local link copied",
                        f"The remote address is not ready yet. {url}")
        else:
            self.notify("Link copied (this Wi-Fi only)", url)

    def notify(self, title: str, message: str = "") -> None:
        try:
            if self.icon and self.icon.HAS_NOTIFICATION:
                self.icon.notify(message or title, title)
        except Exception:
            pass

    def quit(self) -> None:
        if self.loop and self.stop_event:
            self.loop.call_soon_threadsafe(self.stop_event.set)
        if self.icon:
            self.icon.stop()

    # -- startup -----------------------------------------------------------

    def run(self) -> int:
        import pystray

        thread = threading.Thread(target=self._run_server, name="pult-server", daemon=True)
        thread.start()
        # Wait for the server: on an error we show no icon at all
        self.ready.wait(timeout=30)
        if self.error is not None:
            self._show_error(str(self.error))
            return 1

        self.icon = pystray.Icon(
            "pult", _icon_image(),
            f"Pult - {self.cfg.host_name}",
            menu=self._menu(),
        )
        self.icon.run()

        # The server has stopped and the port is free - now the new copy
        # can be started
        if self.relaunch is not None:
            thread.join(timeout=10)
            from . import update as updatemod

            log.info("starting the updated copy")
            updatemod.relaunch(self.relaunch)
        return 0

    def _show_error(self, message: str) -> None:
        """There is no console, so errors are shown in a message box."""
        log.error("did not start: %s", message)
        if sys.platform == "win32":
            import ctypes

            ctypes.WinDLL("user32").MessageBoxW(
                None,
                f"{message}\n\nDetails: {cfgmod.log_path()}",
                "Pult did not start",
                0x10,  # MB_ICONERROR
            )


def run(cfg: cfgmod.Config) -> int:
    return TrayApp(cfg).run()
