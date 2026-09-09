"""
Opening a URL as a standalone window on the computer.

Pult's interface is a web page, but on the computer it should look like
a program, not a website. Opened in an ordinary browser it comes with an
address bar, bookmarks and other tabs, which is confusing and leaves the
impression of having been dumped on some website.

Chromium-based browsers have an "--app=" flag: the page opens in its own
window with no address bar and sits in the taskbar as an independent
application. Edge ships with Windows, so this works almost everywhere.
If no browser is found we fall back to the default one - looking plainer
is better than not working.
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

# Order is deliberate: Edge is always present on Windows
RELATIVE = [
    r"Microsoft\Edge\Application\msedge.exe",
    r"Google\Chrome\Application\chrome.exe",
    r"BraveSoftware\Brave-Browser\Application\brave.exe",
    r"Chromium\Application\chrome.exe",
]


def find_browser() -> Path | None:
    """Finds a Chromium-based browser."""
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
    """Opens the URL in a standalone window. False if it could not."""
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
        log.warning("could not open window: %s", exe, exc_info=True)
        return False


def open_url(url: str, size: tuple[int, int] | None = None) -> None:
    """Tries a standalone window, otherwise the default browser."""
    if not open_app(url, size):
        webbrowser.open(url)
