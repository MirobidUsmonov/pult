"""
Pult'ni bitta .exe qilib yig'adi.

Ishlatish:
    python scripts/build.py                 # oddiy yig'ish
    python scripts/build.py --with-ffmpeg   # ffmpeg'ni ham ichiga qo'shadi
    python scripts/build.py --dir           # bitta fayl emas, papka (tezroq ochiladi)
    python scripts/build.py --setup         # yangi kompyuter uchun o'rnatgich

Natija: dist/Pult.exe  (--setup bilan yana dist/Pult-Setup.exe)

Nega bitta fayl: foydalanuvchiga bitta narsa berish oson va uni istalgan
joyga qo'yib ishga tushirsa bo'ladi. Kamchiligi - har ishga tushganda
vaqtinchalik papkaga ochiladi, shuning uchun ochilishi 2-3 soniya
uzunroq. Fon dasturi uchun bu sezilmaydi.

Nega o'rnatgich alohida: ffmpeg va cloudflared bilan birga fayl 150 MB
ga yaqinlashadi. Agar shu fayl doimiy ishlaydigan dastur bo'lsa,
kompyuter har yonganda 150 MB vaqtinchalik papkaga ochilardi. Shuning
uchun o'rnatgich ichida yengil Pult.exe va yordamchilar alohida turadi:
o'rnatgich ularni joyiga qo'yadi va o'zi keraksiz bo'lib qoladi.
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
    """cloudflared'ni topadi: sozlamalar papkasida yoki PATH'da."""
    sys.path.insert(0, str(ROOT))
    from pult import config as cfgmod
    from pult import tunnel

    path = tunnel.find_binary(cfgmod.config_dir())
    return str(path) if path else None


def build_preset(path: Path) -> bool:
    """Shu kompyuterdagi sozlamalarni yangi kompyuter uchun tayyorlaydi.

    Telegram boti va tunnel rejimi ko'chiriladi - shunda ikkinchi
    kompyuterda hech narsa sozlash kerak bo'lmaydi. Kalit va kompyuter
    raqami ATAYLAB ko'chirilmaydi: har bir kompyuterda o'ziniki
    bo'lishi shart, aks holda telefon ularni farqlay olmaydi.
    """
    sys.path.insert(0, str(ROOT))
    from pult import config as cfgmod

    cfg = cfgmod.load()
    data = {"remote_mode": cfg.remote.mode or "cloudflare"}
    # Yangilash manbasi ham ko'chiriladi. Havola bo'lsa boshqa
    # kompyuterda ham ishlaydi; mahalliy papka bo'lsa u yerda topilmaydi
    # va yangilash jimgina o'tkazib yuboriladi - zarari yo'q.
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
        # Konsol oynasi ochilmasin - dasturning butun mazmuni shunda
        "--windowed",
        "--add-data", f"{ROOT / 'web'}{SEP}web",
        # pystray backend'ni o'zi topa olmaydi
        "--hidden-import", "pystray._win32",
        "--collect-submodules", "aiohttp",
        # Keraksiz og'ir kutubxonalar tortilib qolmasin
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

    print(f"\n{name} yig'ilmoqda…")
    return subprocess.run(cmd, cwd=ROOT).returncode


def size_of(path: Path) -> float:
    if path.is_file():
        return path.stat().st_size / 1048576
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) / 1048576


def main() -> int:
    ap = argparse.ArgumentParser(description="Pult .exe yig'ish")
    ap.add_argument("--with-ffmpeg", action="store_true",
                    help="ffmpeg'ni .exe ichiga qo'shish (~80 MB kattalashadi)")
    ap.add_argument("--setup", action="store_true",
                    help="yangi kompyuter uchun o'rnatgich: ichida ffmpeg, "
                         "cloudflared va shu kompyuterdagi Telegram sozlamalari")
    ap.add_argument("--no-settings", action="store_true",
                    help="--setup bilan: Telegram sozlamalarini ichiga solmaslik")
    ap.add_argument("--dir", action="store_true",
                    help="bitta fayl o'rniga papka (tezroq ochiladi)")
    ap.add_argument("--clean", action="store_true", help="avval build/ va dist/ ni tozalash")
    args = ap.parse_args()

    if args.clean:
        for d in ("build", "dist"):
            shutil.rmtree(ROOT / d, ignore_errors=True)
        print("tozalandi")

    icon = ROOT / "web" / "icons" / "pult.ico"
    if not icon.exists():
        print("ikonka topilmadi, yasayapman…")
        subprocess.run([sys.executable, str(ROOT / "scripts" / "make_icons.py")], check=True)

    entry = ROOT / "scripts" / "entry.py"

    # -- 1-bosqich: dasturning o'zi ------------------------------------
    extra: list[str] = []
    if args.with_ffmpeg and not args.setup:
        path = find_ffmpeg()
        if not path:
            print("ffmpeg topilmadi - usiz yig'aman")
        else:
            extra += ["--add-binary", f"{path}{SEP}."]
            print(f"ffmpeg qo'shilmoqda: {path}")

    rc = pyinstaller("Pult", entry, extra, args.dir)
    if rc != 0:
        return rc

    exe = ROOT / "dist" / ("Pult" if args.dir else "Pult.exe")
    print(f"\nTayyor: {exe}")
    print(f"Hajmi:  {size_of(exe):.1f} MB")

    if not args.setup:
        if not args.with_ffmpeg:
            print()
            print("Eslatma: ffmpeg .exe ichida yo'q. Foydalanuvchida u")
            print("o'rnatilgan bo'lishi kerak, yoki --with-ffmpeg bilan yig'ing.")
        return 0

    # -- 2-bosqich: o'rnatgich ------------------------------------------
    if args.dir:
        print("\n--setup faqat bitta fayl rejimida ishlaydi (--dir siz)")
        return 2

    setup_extra = ["--add-binary", f"{exe}{SEP}."]
    missing = []

    ff = find_ffmpeg()
    if ff:
        setup_extra += ["--add-binary", f"{ff}{SEP}."]
        print(f"ffmpeg qo'shilmoqda: {ff}")
    else:
        missing.append("ffmpeg")

    cf = find_cloudflared()
    if cf:
        setup_extra += ["--add-binary", f"{cf}{SEP}."]
        print(f"cloudflared qo'shilmoqda: {cf}")
    else:
        missing.append("cloudflared")

    if not args.no_settings:
        preset = ROOT / "build" / "preset.json"
        preset.parent.mkdir(parents=True, exist_ok=True)
        has_tg = build_preset(preset)
        setup_extra += ["--add-data", f"{preset}{SEP}."]
        print("sozlamalar qo'shilmoqda"
              + (" (Telegram bilan)" if has_tg else " (Telegram sozlanmagan)"))
        if has_tg:
            print("  DIQQAT: o'rnatgich ichida bot tokeni bo'ladi.")
            print("  Uni faqat o'z kompyuterlaringizga bering.")

    rc = pyinstaller("Pult-Setup", ROOT / "scripts" / "setup_entry.py",
                     setup_extra, onedir=False)
    if rc != 0:
        return rc

    setup_exe = ROOT / "dist" / "Pult-Setup.exe"
    print()
    print(f"O'rnatgich: {setup_exe}")
    print(f"Hajmi:      {size_of(setup_exe):.1f} MB")
    if missing:
        print()
        print("Ichida yo'q: " + ", ".join(missing))
        print("Ular kerak bo'lganda yangi kompyuterda yuklab olinadi -")
        print("sekin internetda bu uzoq davom etishi mumkin.")
    print()
    print("Yangi kompyuterga shu bitta faylni ko'chiring va oching.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
