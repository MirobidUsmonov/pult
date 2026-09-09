"""
The entry point for the installer.

This file only installs and exits - it does not start the program
itself. A separate, slim Pult.exe is carried inside and the installer
puts it in place. That way the program that runs all the time does not
contain ffmpeg and cloudflared, and they are not unpacked into a temp
folder every time the computer starts.
"""
import multiprocessing
import os
import sys

if getattr(sys, "frozen", False):
    sys.path.insert(0, os.path.dirname(sys.executable))

from pult import setup as setupmod  # noqa: E402


def main() -> int:
    if not setupmod.ask(
        "Install Pult on this computer?\n\n"
        "\u2022 the program moves into a permanent folder\n"
        "\u2022 it starts itself at logon (no terminal window)\n"
        "\u2022 a QR code opens so the phone can connect\n\n"
        "To remove it later: Pult.exe --uninstall",
        "Install Pult"
    ):
        return 0

    ok, text = setupmod.install()
    if not ok:
        setupmod.message(text, "Pult was not installed", 0x10)
        return 1

    setupmod.message(text + "\n\nStarting it now.")
    setupmod.launch(setupmod.install_dir() / setupmod.EXE_NAME)
    return 0


if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
