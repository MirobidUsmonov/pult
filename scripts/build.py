"""
Builds Pult into a single .exe.

Usage:
    python scripts/build.py                 # a plain build
    python scripts/build.py --with-ffmpeg   # bundle ffmpeg as well
    python scripts/build.py --dir           # a folder instead of one file (starts faster)
    python scripts/build.py --setup         # an installer for a new computer

Result: dist/Pult.exe  (with --setup, dist/Pult-Setup.exe too)

Why one file: handing someone a single thing is easy, and they can drop
it anywhere and run it. The downside is that it unpacks into a temp
folder on every start, so starting takes 2-3 seconds longer. For a
background program that is not noticeable.

Why the installer is separate: with ffmpeg and cloudflared inside, the
file approaches 150 MB. If that file were the program that runs all the
time, 150 MB would be unpacked into a temp folder every time the
computer starts. So the installer carries a slim Pult.exe and the
helpers separately: it puts them in place and then has no further use.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEP = ";" if os.name == "nt" else ":"


def find_ffmpeg() -> str | None:
    sys.path.insert(0, str(ROOT))
    from pult.platform import ffmpeg as ff

    return ff.find_ffmpeg()


def find_cloudflared() -> str | None:
    """Finds cloudflared, in the settings folder or on PATH."""
    sys.path.insert(0, str(ROOT))
    from pult import config as cfgmod
    from pult import tunnel

    path = tunnel.find_binary(cfgmod.config_dir())
    return str(path) if path else None


def build_preset(path: Path) -> bool:
    """Prepares this computer's settings for a new computer.

    The Telegram bot and the tunnel mode are carried over, so nothing
    has to be configured on the second computer. The key and the
    computer id are DELIBERATELY not carried over: each computer must
    have its own, otherwise the phone cannot tell them apart.
    """
    sys.path.insert(0, str(ROOT))
    from pult import config as cfgmod

    cfg = cfgmod.load()
    data = {"remote_mode": cfg.remote.mode or "cloudflare"}
    # The update source is carried over too. A link works on another
    # computer as well; a local folder will not be found there and the
    # update is skipped quietly - no harm done.
    if (cfg.update.mode or "off").lower() not in ("off", "", "none"):
        data["update"] = {
            "mode": cfg.update.mode,
            "source": cfg.update.source,
            "check_minutes": cfg.update.check_minutes,
        }
    if cfg.telegram.bot_token and cfg.telegram.chat_id:
        data["telegram"] = {
            "bot_token": cfg.telegram.bot_token,
            "chat_id": cfg.telegram.chat_id,
        }
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return "telegram" in data


def pyinstaller(name: str, entry: Path, extra: list[str], onedir: bool) -> int:
    icon = ROOT / "web" / "icons" / "pult.ico"
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--name", name,
        "--icon", str(icon),
        # No console window - that is the whole point of the program
        "--windowed",
        "--add-data", f"{ROOT / 'web'}{SEP}web",
        # pystray cannot find its backend by itself
        "--hidden-import", "pystray._win32",
        "--collect-submodules", "aiohttp",
        # Keep heavy libraries we do not need out of the build
        "--exclude-module", "tkinter",
        "--exclude-module", "matplotlib",
        "--exclude-module", "numpy",
        "--exclude-module", "pytest",
        "--distpath", str(ROOT / "dist"),
        "--workpath", str(ROOT / "build" / name),
        "--specpath", str(ROOT / "build"),
    ]
    cmd.append("--onedir" if onedir else "--onefile")
    cmd += extra
    cmd.append(str(entry))

    print(f"\nBuilding {name}\u2026")
    return subprocess.run(cmd, cwd=ROOT).returncode


def size_of(path: Path) -> float:
    if path.is_file():
        return path.stat().st_size / 1048576
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) / 1048576


def main() -> int:
    ap = argparse.ArgumentParser(description="Build the Pult .exe")
    ap.add_argument("--with-ffmpeg", action="store_true",
                    help="bundle ffmpeg into the .exe (adds ~80 MB)")
    ap.add_argument("--setup", action="store_true",
                    help="an installer for a new computer: ffmpeg, "
                         "cloudflared and this computer's Telegram settings")
    ap.add_argument("--no-settings", action="store_true",
                    help="with --setup: leave the Telegram settings out")
    ap.add_argument("--dir", action="store_true",
                    help="a folder instead of one file (starts faster)")
    ap.add_argument("--clean", action="store_true", help="clear build/ and dist/ first")
    args = ap.parse_args()

    if args.clean:
        for d in ("build", "dist"):
            shutil.rmtree(ROOT / d, ignore_errors=True)
        print("cleaned")

    icon = ROOT / "web" / "icons" / "pult.ico"
    if not icon.exists():
        print("no icon found, generating one\u2026")
        subprocess.run([sys.executable, str(ROOT / "scripts" / "make_icons.py")], check=True)

    entry = ROOT / "scripts" / "entry.py"

    # -- step 1: the program itself ------------------------------------
    extra: list[str] = []
    if args.with_ffmpeg and not args.setup:
        path = find_ffmpeg()
        if not path:
            print("ffmpeg not found - building without it")
        else:
            extra += ["--add-binary", f"{path}{SEP}."]
            print(f"bundling ffmpeg: {path}")

    rc = pyinstaller("Pult", entry, extra, args.dir)
    if rc != 0:
        return rc

    exe = ROOT / "dist" / ("Pult" if args.dir else "Pult.exe")
    print(f"\nReady: {exe}")
    print(f"Size:  {size_of(exe):.1f} MB")

    if not args.setup:
        if not args.with_ffmpeg:
            print()
            print("Note: ffmpeg is not inside the .exe. The user has to")
            print("have it installed, or build with --with-ffmpeg.")
        return 0

    # -- step 2: the installer ------------------------------------------
    if args.dir:
        print("\n--setup only works in one-file mode (without --dir)")
        return 2

    setup_extra = ["--add-binary", f"{exe}{SEP}."]
    missing = []

    ff = find_ffmpeg()
    if ff:
        setup_extra += ["--add-binary", f"{ff}{SEP}."]
        print(f"bundling ffmpeg: {ff}")
    else:
        missing.append("ffmpeg")

    cf = find_cloudflared()
    if cf:
        setup_extra += ["--add-binary", f"{cf}{SEP}."]
        print(f"bundling cloudflared: {cf}")
    else:
        missing.append("cloudflared")

    if not args.no_settings:
        preset = ROOT / "build" / "preset.json"
        preset.parent.mkdir(parents=True, exist_ok=True)
        has_tg = build_preset(preset)
        setup_extra += ["--add-data", f"{preset}{SEP}."]
        print("bundling settings"
              + (" (with Telegram)" if has_tg else " (Telegram not set up)"))
        if has_tg:
            print("  CAREFUL: the installer will contain the bot token.")
            print("  Only hand it to your own computers.")

    rc = pyinstaller("Pult-Setup", ROOT / "scripts" / "setup_entry.py",
                     setup_extra, onedir=False)
    if rc != 0:
        return rc

    setup_exe = ROOT / "dist" / "Pult-Setup.exe"
    print()
    print(f"Installer: {setup_exe}")
    print(f"Size:      {size_of(setup_exe):.1f} MB")
    if missing:
        print()
        print("Not bundled: " + ", ".join(missing))
        print("They are downloaded on the new computer when needed -")
        print("on a slow connection that can take a while.")
    print()
    print("Copy this one file to the new computer and open it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
