"""
Tashqi kirish: kompyuterni bitta Wi-Fi chegarasidan chiqarish.

Muammo shundaki, uy routerida port ochish ko'pchilikda ishlamaydi -
operatorlar oq IP bermaydi. Yechim teskari yo'nalishda: kompyuterning
o'zi tashqariga chiqib tunnel ochadi, telefon esa tunnelning ochiq
manziliga ulanadi. Bu har qanday tarmoqda ishlaydi va routerga
tegmaydi.

Hozircha cloudflared'ning "tezkor tunnel" rejimi ishlatiladi: hisob
ham, domen ham kerak emas. Qo'shimcha yutuq - manzil haqiqiy
sertifikatga ega, ya'ni brauzer ogohlantirmaydi va WebCodecs ishlaydi.

Kamchiligi: manzil har ishga tushganda yangi bo'ladi. Shuning uchun u
Telegram xabari bilan yuboriladi - baribir kompyuter yonganda xabar
ketadi, manzil o'sha xabarga qo'shiladi.
"""
from __future__ import annotations

import asyncio
import logging
import platform
import re
import stat
import subprocess
import sys
from pathlib import Path

log = logging.getLogger("pult.tunnel")

# cloudflared tezkor tunnel manzilini shu ko'rinishda chop etadi
URL_RE = re.compile(rb"https://[a-z0-9][a-z0-9-]*\.trycloudflare\.com")

RELEASE = "https://github.com/cloudflare/cloudflared/releases/latest/download"

# Konsol oynasi ochilmasligi kerak: dastur fon rejimida ishlaydi
NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def asset_name() -> str | None:
    """Shu tizim uchun cloudflared fayl nomi."""
    machine = platform.machine().lower()
    if sys.platform == "win32":
        return "cloudflared-windows-386.exe" if machine in ("i386", "x86") \
            else "cloudflared-windows-amd64.exe"
    if sys.platform.startswith("linux"):
        if machine in ("aarch64", "arm64"):
            return "cloudflared-linux-arm64"
        if machine.startswith("arm"):
            return "cloudflared-linux-arm"
        return "cloudflared-linux-amd64"
    # macOS uchun arxiv beriladi, uni ochish kerak - hozircha
    # foydalanuvchi o'zi o'rnatgani sodda: brew install cloudflared
    return None


def find_binary(config_dir: Path, hint: str = "") -> Path | None:
    """cloudflared'ni topadi: sozlamada ko'rsatilgan, yonidagi, PATH'dagi."""
    if hint:
        p = Path(hint)
        if p.is_file():
            return p
        log.warning("sozlamadagi cloudflared topilmadi: %s", p)

    name = asset_name()
    if name:
        local = config_dir / "bin" / name
        if local.is_file():
            return local

    from shutil import which

    found = which("cloudflared")
    return Path(found) if found else None


async def download(config_dir: Path) -> Path | None:
    """cloudflared'ni yuklab oladi.

    Bir marta bo'ladigan ish: fayl sozlamalar papkasiga tushadi va
    keyingi safar shundan olinadi.
    """
    name = asset_name()
    if not name:
        log.warning("bu tizim uchun avtomatik yuklash yo'q - cloudflared'ni "
                    "o'zingiz o'rnating")
        return None

    import aiohttp

    target = config_dir / "bin" / name
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".yuklanmoqda")
    url = f"{RELEASE}/{name}"

    # Fayl ~35 MB. Sekin tarmoqda bu o'n daqiqalab ketadi, shuning
    # uchun umumiy vaqt chegarasi qo'yilmaydi - u ishlab turgan
    # yuklashni ham uzib qo'yardi. Chegara faqat kutishga: ma'lumot
    # kelmay qolsa uziladi.
    timeout = aiohttp.ClientTimeout(total=None, sock_connect=30, sock_read=120)

    # Uzilib qolgan yuklash boshidan boshlanmasin: yarim fayl saqlanadi
    # va davomi so'raladi. Sekin tarmoqda bu farqni bildiradi.
    have = tmp.stat().st_size if tmp.is_file() else 0
    headers = {"Range": f"bytes={have}-"} if have else {}
    log.info("cloudflared yuklanmoqda%s: %s",
             f" ({have / 1048576:.1f} MB dan davom)" if have else "", url)

    try:
        async with aiohttp.ClientSession(timeout=timeout) as sess:
            async with sess.get(url, headers=headers) as r:
                if r.status == 416:
                    # Server "bunday oraliq yo'q" dedi - fayl allaqachon
                    # to'liq bo'lishi mumkin, boshqattan yuklaymiz
                    tmp.unlink(missing_ok=True)
                    raise RuntimeError("yarim fayl yaroqsiz, qayta urinib ko'ring")
                r.raise_for_status()
                resume = r.status == 206
                if not resume:
                    have = 0
                total = int(r.headers.get("Content-Length", 0)) + have
                done = have
                step = done
                with tmp.open("ab" if resume else "wb") as f:
                    async for chunk in r.content.iter_chunked(256 * 1024):
                        f.write(chunk)
                        done += len(chunk)
                        # Belgisiz kutish dastur qotib qolgandek
                        # ko'rinadi - har 5 MB da holatni yozamiz.
                        if done - step >= 5 * 1048576:
                            step = done
                            if total:
                                log.info("cloudflared: %.0f%% (%.1f/%.1f MB)",
                                         done * 100 / total,
                                         done / 1048576, total / 1048576)
                            else:
                                log.info("cloudflared: %.1f MB", done / 1048576)
        # Yozib bo'lgach nom beramiz: yarim yuklangan fayl ishlatilmasin
        tmp.replace(target)
        if sys.platform != "win32":
            target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP)
        log.info("cloudflared tayyor: %s (%.1f MB)",
                 target, target.stat().st_size / 1048576)
        return target
    except Exception as exc:
        # Yarim fayl ataylab saqlanadi: keyingi urinish shundan davom etadi
        log.warning("cloudflared yuklanmadi: %s: %s", type(exc).__name__, exc)
        return None


class Tunnel:
    """Ishlab turgan tunnel. Uzilsa o'zi qayta ko'tariladi."""

    def __init__(self, config_dir: Path, hint: str, local_url: str) -> None:
        self.config_dir = config_dir
        self.hint = hint
        self.binary: Path | None = None
        self.local_url = local_url
        self.url: str = ""
        self.ready = asyncio.Event()
        self._proc: subprocess.Popen | None = None
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="pult-tunnel")

    async def _run(self) -> None:
        delay = 3.0
        while True:
            try:
                await self._once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("tunnel xatosi: %s", exc)
            # Tunnel uzildi. Manzil endi ishlamaydi - uni tozalaymiz,
            # aks holda eski manzil to'g'ridek ko'rinib turardi.
            self.url = ""
            self.ready.clear()
            log.info("tunnel uzildi, %.0f soniyadan keyin qayta urinamiz", delay)
            await asyncio.sleep(delay)
            delay = min(delay * 1.8, 120)

    async def _ensure_binary(self) -> Path:
        """cloudflared'ni topadi yoki yuklab oladi.

        Bu qayta urinish siklining ichida turadi: sekin tarmoqda
        yuklash uzilib qolishi mumkin va u holda keyingi urinish yarim
        fayldan davom etadi. Yuklashni ishga tushishdan oldin qilsak,
        dastur uni kutib turib qolardi.
        """
        if self.binary and self.binary.is_file():
            return self.binary
        found = find_binary(self.config_dir, self.hint)
        if found is None:
            found = await download(self.config_dir)
        if found is None:
            raise RuntimeError("cloudflared topilmadi")
        self.binary = found
        return found

    async def _once(self) -> None:
        binary = await self._ensure_binary()
        cmd = [
            str(binary), "tunnel",
            "--url", self.local_url,
            # Mahalliy server o'z-o'zini imzolagan sertifikat ishlatadi
            "--no-tls-verify",
            "--no-autoupdate",
            # Manzilni shu oqimdan o'qiymiz
            "--loglevel", "info",
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
            creationflags=NO_WINDOW,
        )
        self._proc = proc
        log.info("cloudflared ishga tushdi (pid %s)", proc.pid)
        try:
            assert proc.stderr
            async for line in proc.stderr:
                m = URL_RE.search(line)
                if m and not self.url:
                    self.url = m.group(0).decode("ascii")
                    self.ready.set()
                    log.info("tashqi manzil: %s", self.url)
        finally:
            if proc.returncode is None:
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=5)
                except asyncio.TimeoutError:
                    proc.kill()

    async def wait_url(self, timeout: float = 90) -> str:
        try:
            await asyncio.wait_for(self.ready.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            log.warning("tunnel manzili %.0f soniyada kelmadi", timeout)
        return self.url

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            except Exception:
                log.warning("tunnelni to'xtatishda xato", exc_info=True)
            self._task = None
        proc = self._proc
        if proc and proc.returncode is None:
            try:
                proc.terminate()
            except Exception:
                pass


async def create(cfg, config_dir: Path) -> Tunnel | None:
    """Sozlamaga qarab tunnel ochadi. Yoqilmagan bo'lsa None qaytaradi."""
    mode = (cfg.remote.mode or "off").lower()
    if mode in ("off", "", "none"):
        return None
    if mode != "cloudflare":
        log.warning("noma'lum tashqi kirish rejimi: %s", mode)
        return None

    scheme = "http" if cfg.tls == "off" else "https"
    tunnel = Tunnel(config_dir, cfg.remote.binary, f"{scheme}://127.0.0.1:{cfg.port}")
    tunnel.start()
    return tunnel
