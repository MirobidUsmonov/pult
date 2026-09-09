"""
Havolani kompyuterda alohida oyna qilib ochish.

Pult'ning interfeysi veb-sahifa, lekin kompyuterda u sayt emas, dastur
bo'lib ko'rinishi kerak. Oddiy brauzerda ochilsa manzil qatori,
xatcho'plar va boshqa varaqlar ko'rinadi - bu foydalanuvchini
chalkashtiradi va "meni saytga otvordi" degan taassurot qoldiradi.

Chromium'ga asoslangan brauzerlarda "--app=" bayrog'i bor: sahifa
manzil qatorisiz, alohida oynada ochiladi va vazifalar panelida
mustaqil dastur bo'lib turadi. Edge Windows'da doim bor, shuning uchun
bu deyarli har joyda ishlaydi. Topilmasa oddiy brauzerga qaytamiz -
imkoniyat yo'qolgandan ko'ra chiroyi kamroq bo'lgani yaxshi.
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import webbrowser
from pathlib import Path

log = logging.getLogger("pult.window")

NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

# Tartib ataylab: Edge Windows'da doim bor
RELATIVE = [
    r"Microsoft\Edge\Application\msedge.exe",
    r"Google\Chrome\Application\chrome.exe",
    r"BraveSoftware\Brave-Browser\Application\brave.exe",
    r"Chromium\Application\chrome.exe",
]


def find_browser() -> Path | None:
    """Chromium'ga asoslangan brauzerni topadi."""
    if sys.platform != "win32":
        from shutil import which

        for name in ("google-chrome", "chromium", "chromium-browser", "microsoft-edge"):
            found = which(name)
            if found:
                return Path(found)
        return None

    roots = [
        os.environ.get("PROGRAMFILES"),
        os.environ.get("PROGRAMFILES(X86)"),
        os.environ.get("LOCALAPPDATA"),
    ]
    for root in roots:
        if not root:
            continue
        for rel in RELATIVE:
            path = Path(root) / rel
            if path.is_file():
                return path
    return None


def open_app(url: str, size: tuple[int, int] | None = None) -> bool:
    """Havolani alohida oynada ochadi. Uddalamasa False."""
    exe = find_browser()
    if exe is None:
        return False
    args = [str(exe), f"--app={url}"]
    if size:
        args.append(f"--window-size={size[0]},{size[1]}")
    try:
        subprocess.Popen(args, creationflags=NO_WINDOW)
        return True
    except Exception:
        log.warning("oyna ochilmadi: %s", exe, exc_info=True)
        return False


def open_url(url: str, size: tuple[int, int] | None = None) -> None:
    """Alohida oynada ochishga urinadi, bo'lmasa odatiy brauzerda."""
    if not open_app(url, size):
        webbrowser.open(url)
