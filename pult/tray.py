"""
Trey ikonkasi - dasturning konsolsiz ko'rinishi.

Bu Pult'ning odatiy ishga tushish usuli: terminal oynasi ochilmaydi,
soat yonidagi kichik ikonka bilan boshqariladi. Server alohida oqimda
ishlaydi, ikonka esa asosiy oqimda - Windows'da trey ikonkasi shunday
talab qiladi.
"""
from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

from . import app as appmod
from . import config as cfgmod

log = logging.getLogger("pult.tray")


def _icon_image():
    from PIL import Image

    for name in ("favicon.png", "icon-192.png"):
        path = _web_icons() / name
        if path.is_file():
            return Image.open(path).convert("RGBA")
    # Ikonka fayli topilmasa ham dastur ishlashi kerak
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
        log.exception("ochib bo'lmadi: %s", path)


def _copy(text: str) -> None:
    """Matnni almashish buferiga qo'yadi (tashqi kutubxonasiz)."""
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

    # -- server oqimi ------------------------------------------------------

    def _run_server(self) -> None:
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.stop_event = asyncio.Event()
        try:
            self.loop.run_until_complete(self._serve())
        except BaseException as exc:  # noqa: BLE001 - xatoni ikonkaga uzatamiz
            self.error = exc
            log.exception("server to'xtadi")
            self.ready.set()
        finally:
            try:
                self.loop.close()
            except Exception:
                pass

    async def _serve(self) -> None:
        server = appmod.build_server(self.cfg)
        await server.start()
        self.ready.set()
        notifier = appmod.start_notifier(self.cfg)
        log.info("Pult treyda ishlayapti - %s", appmod.phone_url(self.cfg))
        try:
            assert self.stop_event
            await self.stop_event.wait()
        finally:
            if notifier:
                notifier.cancel()
            await server.stop()

    # -- menyu -------------------------------------------------------------

    def _menu(self):
        import pystray

        def item(label, fn, **kw):
            return pystray.MenuItem(label, lambda *_: fn(), **kw)

        return pystray.Menu(
            pystray.MenuItem(self.cfg.host_name, None, enabled=False),
            pystray.Menu.SEPARATOR,
            item("Telefonni ulash (QR)", self.open_pair, default=True),
            item("Telefon ekranini ko'rish", self.open_viewer),
            item("Havolani nusxalash", self.copy_link),
            pystray.Menu.SEPARATOR,
            item("Loglar", lambda: _open_path(cfgmod.log_path())),
            item("Sozlamalar papkasi", lambda: _open_path(cfgmod.config_dir())),
            pystray.Menu.SEPARATOR,
            item("Chiqish", self.quit),
        )

    def open_pair(self) -> None:
        webbrowser.open(appmod.pair_url(self.cfg))

    def open_viewer(self) -> None:
        """Telefon ekranini kompyuter brauzerida ochadi."""
        webbrowser.open(appmod.viewer_url(self.cfg))

    def copy_link(self) -> None:
        url = appmod.phone_url(self.cfg)
        _copy(url)
        self.notify("Havola nusxalandi", url)

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

    # -- ishga tushirish ---------------------------------------------------

    def run(self) -> int:
        import pystray

        thread = threading.Thread(target=self._run_server, name="pult-server", daemon=True)
        thread.start()
        # Server ko'tarilishini kutamiz: xato bo'lsa ikonka ko'rsatmaymiz
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
        return 0

    def _show_error(self, message: str) -> None:
        """Konsol yo'q bo'lgani uchun xatoni oyna bilan ko'rsatamiz."""
        log.error("ishga tushmadi: %s", message)
        if sys.platform == "win32":
            import ctypes

            ctypes.WinDLL("user32").MessageBoxW(
                None,
                f"{message}\n\nBatafsil: {cfgmod.log_path()}",
                "Pult ishga tushmadi",
                0x10,  # MB_ICONERROR
            )


def run(cfg: cfgmod.Config) -> int:
    return TrayApp(cfg).run()
