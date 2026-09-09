"""
O'z-o'zini yangilash.

Yangi versiya chiqqanda foydalanuvchi hech narsa qilmasligi kerak:
dastur ishga tushganda manbani tekshiradi, yangisi bo'lsa o'zini
almashtirib qayta ishga tushadi.

Windows ishlab turgan .exe ustiga yozishga ruxsat bermaydi, lekin uning
NOMINI O'ZGARTIRISHGA beradi. Shu tufayli almashtirish o'z-o'zidan
ishlaydi: eski fayl chetga suriladi, yangisi o'sha nom bilan qo'yiladi,
so'ng yangisi ishga tushirilib eskisi chiqadi. Chetga surilgan fayl
keyingi safar o'chiriladi.

Versiya raqami yuritilmaydi - fayllar xesh yig'indisi bo'yicha
solishtiriladi. Yig'ish har safar raqam qo'yishni talab qilmasin:
manbadagi fayl boshqacha bo'lsa, demak u yangi.
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
STAGED = "Pult.exe.yangi"


def running_exe() -> Path | None:
    """Ishlab turgan .exe. Manba kodidan ishga tushirilgan bo'lsa - None."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve()
    return None


def digest(path: Path) -> str:
    """Faylning SHA-256 yig'indisi. O'qib bo'lmasa - bo'sh satr."""
    try:
        h = hashlib.sha256()
        with path.open("rb") as f:
            for block in iter(lambda: f.read(1 << 20), b""):
                h.update(block)
        return h.hexdigest()
    except OSError:
        return ""


# ------------------------------------------------------------ manba

def from_folder(source: str) -> Path | None:
    """Papkadagi Pult.exe. Papka tarmoqdagi umumiy papka ham bo'lishi mumkin."""
    if not source:
        return None
    base = Path(source)
    candidate = base if base.is_file() else base / EXE_NAME
    return candidate if candidate.is_file() else None


async def from_url(source: str, dest: Path) -> Path | None:
    """Manzildan yuklab oladi.

    Yuklab bo'lgach nom beriladi: yarim yuklangan fayl hech qachon
    dastur o'rniga qo'yilmasin.
    """
    if not source:
        return None

    import aiohttp

    tmp = dest.with_suffix(dest.suffix + ".yuklanmoqda")
    try:
        # Umumiy vaqt chegarasi yo'q: fayl 20 MB dan ortiq va sekin
        # tarmoqda uzoq ketadi. Chegara faqat kutishga.
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
        log.warning("yangilanish yuklanmadi: %s: %s", type(exc).__name__, exc)
        tmp.unlink(missing_ok=True)
        return None


async def candidate(cfg) -> Path | None:
    """Manbadagi fayl hozirgisidan boshqacha bo'lsa - uning yo'li."""
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
        log.warning("noma'lum yangilash rejimi: %s", mode)
        return None

    if found is None:
        return None
    if digest(found) == digest(current):
        return None
    return found


# ------------------------------------------------------------ qo'llash

def swap(new: Path, current: Path) -> bool:
    """Yangi faylni dastur o'rniga qo'yadi."""
    old = current.with_name(current.name + ".eski")
    try:
        old.unlink(missing_ok=True)
    except OSError:
        # Oldingi almashtirishdan qolgan va hali band bo'lishi mumkin
        old = current.with_name(f"{current.name}.eski-{os.getpid()}")
    try:
        current.rename(old)
    except OSError as exc:
        log.warning("eski faylni surib bo'lmadi: %s", exc)
        return False
    try:
        shutil.copy2(new, current)
        return True
    except OSError as exc:
        log.error("yangi fayl qo'yilmadi: %s", exc)
        # Eskisini joyiga qaytaramiz - dastursiz qolmaylik
        try:
            old.rename(current)
        except OSError:
            log.error("eski fayl ham qaytarilmadi: %s", old)
        return False


def cleanup(folder: Path) -> None:
    """Oldingi almashtirishdan qolgan fayllar. Endi ular band emas."""
    for stale in list(folder.glob(EXE_NAME + ".eski*")) + list(folder.glob(STAGED)):
        try:
            stale.unlink()
        except OSError:
            pass


def relaunch(exe: Path) -> None:
    try:
        subprocess.Popen([str(exe)], cwd=str(exe.parent), creationflags=NO_WINDOW)
    except Exception:
        log.exception("yangi versiya ishga tushmadi")


async def apply_if_any(cfg, launch: bool = True) -> bool:
    """Yangilanish bo'lsa qo'llaydi.

    launch=True bo'lsa yangi nusxani ham ishga tushiradi - bu faqat
    server hali ko'tarilmagan paytda to'g'ri. Ishlab turgan dasturda
    launch=False qilib, avval eski nusxani to'xtatish kerak: aks holda
    yangi nusxa portni band topib, xato oynasi bilan chiqib ketadi.

    True qaytarsa - dastur almashtirildi.
    """
    current = running_exe()
    if current is None:
        return False
    cleanup(current.parent)

    try:
        new = await candidate(cfg)
    except Exception:
        log.warning("yangilanishni tekshirib bo'lmadi", exc_info=True)
        return False
    if new is None:
        return False

    log.info("yangi versiya topildi: %s", new)
    if not swap(new, current):
        return False
    if launch:
        log.info("yangilandi, qayta ishga tushirilmoqda")
        relaunch(current)
    else:
        log.info("yangilandi - to'xtagach yangi nusxa ishga tushadi")
    return True


async def watch(cfg, busy, on_ready) -> None:
    """Ishlab turganda ham vaqti-vaqti bilan tekshiradi.

    Yangilanish faqat hech kim ulanmagan paytda qo'llanadi: oqim
    o'rtasida uzilib qolish yangilanishdan ko'ra yomonroq.
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
            log.warning("yangilanish tekshiruvi xato berdi", exc_info=True)


def describe(cfg) -> str:
    """Trey menyusi va loglar uchun qisqa holat."""
    mode = (cfg.update.mode or "off").lower()
    if mode in ("off", "", "none"):
        return "yangilanish o'chirilgan"
    return f"yangilanish manbasi: {cfg.update.source or '(ko‘rsatilmagan)'}"


def stamp() -> str:
    """Ishlab turgan faylning belgisi - qaysi yig'ilish ekanini bilish uchun."""
    exe = running_exe()
    if exe is None:
        return "manba kodidan"
    try:
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(exe.stat().st_mtime))
    except OSError:
        when = "?"
    return f"{digest(exe)[:8]} ({when})"
