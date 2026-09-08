"""
Pult'ni bitta .exe qilib yig'adi.

Ishlatish:
    python scripts/build.py                 # oddiy yig'ish
    python scripts/build.py --with-ffmpeg   # ffmpeg'ni ham ichiga qo'shadi
    python scripts/build.py --dir           # bitta fayl emas, papka (tezroq ochiladi)

Natija: dist/Pult.exe

Nega bitta fayl: foydalanuvchiga bitta narsa berish oson va uni istalgan
joyga qo'yib ishga tushirsa bo'ladi. Kamchiligi - har ishga tushganda
vaqtinchalik papkaga ochiladi, shuning uchun ochilishi 2-3 soniya
uzunroq. Fon dasturi uchun bu sezilmaydi.
"""
from __future__ import annotations

import argparse
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


def main() -> int:
    ap = argparse.ArgumentParser(description="Pult .exe yig'ish")
    ap.add_argument("--with-ffmpeg", action="store_true",
                    help="ffmpeg'ni .exe ichiga qo'shish (~80 MB kattalashadi)")
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

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--name", "Pult",
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
        "--workpath", str(ROOT / "build"),
        "--specpath", str(ROOT / "build"),
    ]
    cmd.append("--onedir" if args.dir else "--onefile")

    if args.with_ffmpeg:
        path = find_ffmpeg()
        if not path:
            print("ffmpeg topilmadi - usiz yig'aman")
        else:
            cmd += ["--add-binary", f"{path}{SEP}."]
            print(f"ffmpeg qo'shilmoqda: {path}")

    cmd.append(str(ROOT / "scripts" / "entry.py"))

    print("PyInstaller ishga tushmoqda…")
    print(" ".join(cmd))
    print()
    result = subprocess.run(cmd, cwd=ROOT)
    if result.returncode != 0:
        return result.returncode

    exe = ROOT / "dist" / ("Pult" if args.dir else "Pult.exe")
    if exe.exists():
        size = exe.stat().st_size if exe.is_file() else sum(
            f.stat().st_size for f in exe.rglob("*") if f.is_file()
        )
        print()
        print(f"Tayyor: {exe}")
        print(f"Hajmi:  {size / 1024 / 1024:.1f} MB")
        if not args.with_ffmpeg:
            print()
            print("Eslatma: ffmpeg .exe ichida yo'q. Foydalanuvchida u")
            print("o'rnatilgan bo'lishi kerak, yoki --with-ffmpeg bilan yig'ing.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
