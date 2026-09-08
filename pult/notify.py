"""
Xabarnomalar: kompyuter yonib internetga ulanganda telefonga xabar.

Telegram tanlangani bejiz emas: telefonda allaqachon o'rnatilgan,
xabarlar hamma joyda keladi, va push xabarnomalar uchun alohida
infratuzilma (Firebase va h.k.) qurish shart emas.

Chat raqamini qo'lda topish eng bezovta qiladigan qism bo'lgani uchun
uni dastur o'zi aniqlaydi: siz botga /start yozasiz, dastur getUpdates
orqali ko'radi va saqlab qo'yadi.
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import platform
import time
from datetime import datetime
from urllib.parse import urlparse

import aiohttp

from . import config as cfgmod

log = logging.getLogger("pult.notify")

API = "https://api.telegram.org/bot{token}/{method}"

OYLAR = [
    "yanvar", "fevral", "mart", "aprel", "may", "iyun",
    "iyul", "avgust", "sentabr", "oktabr", "noyabr", "dekabr",
]


def _is_public(url: str) -> bool:
    """Havolani Telegram tugmasiga qo'yish mumkinmi.

    Telegram inline tugmalarida faqat ochiq manzillar ishlaydi: mahalliy
    IP yoki localhost bo'lsa tugma yaratilmaydi, havola matn sifatida
    yuboriladi.
    """
    try:
        host = urlparse(url).hostname or ""
        if not host or host in ("localhost", "127.0.0.1"):
            return False
        try:
            return not ipaddress.ip_address(host).is_private
        except ValueError:
            return "." in host  # domen nomi
    except Exception:
        return False


class Telegram:
    def __init__(self, token: str, chat_id: str = "") -> None:
        self.token = token
        self.chat_id = chat_id

    async def call(self, method: str, **params) -> dict:
        url = API.format(token=self.token, method=method)
        timeout = aiohttp.ClientTimeout(total=params.pop("_timeout", 20))
        async with aiohttp.ClientSession(timeout=timeout) as sess:
            async with sess.post(url, json=params) as r:
                data = await r.json()
        if not data.get("ok"):
            raise RuntimeError(data.get("description", "Telegram xatosi"))
        return data.get("result", {})

    async def me(self) -> dict:
        return await self.call("getMe")

    async def send(self, text: str, button: tuple[str, str] | None = None) -> None:
        params: dict = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if button and _is_public(button[1]):
            params["reply_markup"] = {
                "inline_keyboard": [[{"text": button[0], "url": button[1]}]]
            }
        await self.call("sendMessage", **params)

    async def discover_chat(self, timeout: float = 180) -> str | None:
        """Botga birinchi yozgan odamning chat raqamini qaytaradi.

        Uzun so'rov (long polling) ishlatiladi, shuning uchun kutish
        vaqtida tarmoqqa deyarli murojaat bo'lmaydi.
        """
        offset = 0
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                updates = await self.call(
                    "getUpdates", offset=offset, timeout=25, _timeout=35
                )
            except Exception as exc:
                log.warning("getUpdates: %s", exc)
                await asyncio.sleep(3)
                continue
            for upd in updates:
                offset = max(offset, upd.get("update_id", 0) + 1)
                msg = upd.get("message") or upd.get("channel_post") or {}
                chat = msg.get("chat") or {}
                if chat.get("id"):
                    return str(chat["id"])
        return None


def os_name() -> str:
    """Tizim nomi.

    platform.release() Windows 11 da ham "10" deb qaytaradi - bu Python'ning
    ma'lum kamchiligi. Haqiqiy versiyani qurilish raqamidan aniqlaymiz:
    22000 va undan yuqorisi - Windows 11.
    """
    system = platform.system()
    if system == "Windows":
        try:
            build = int(platform.version().split(".")[2])
            return "Windows 11" if build >= 22000 else "Windows 10"
        except (IndexError, ValueError):
            pass
    return f"{system} {platform.release()}".strip()


def online_message(cfg: cfgmod.Config, url: str) -> str:
    now = datetime.now()
    date = f"{now.day}-{OYLAR[now.month - 1]}"
    lines = [
        f"💻 <b>{cfg.host_name}</b> onlayn",
        f"🕐 {now:%H:%M} · {date}",
        f"🖥 {os_name()}",
        "",
        f"<code>{url}</code>",
    ]
    return "\n".join(lines)


async def wait_online(timeout: float = 0) -> bool:
    """Internet paydo bo'lishini kutadi.

    Kompyuter yonganda dastur tarmoqdan oldin ishga tushishi mumkin,
    shuning uchun darhol xabar yuborishga urinish deyarli doim
    muvaffaqiyatsiz bo'ladi. Telegram serveriga murojaat qilib
    ko'ramiz - baribir bizga kerak bo'ladigan manzil shu.
    """
    delay = 2.0
    started = time.monotonic()
    while True:
        try:
            t = aiohttp.ClientTimeout(total=8)
            async with aiohttp.ClientSession(timeout=t) as sess:
                async with sess.get("https://api.telegram.org") as r:
                    if r.status < 500:
                        return True
        except Exception:
            pass
        if timeout and time.monotonic() - started > timeout:
            return False
        await asyncio.sleep(delay)
        delay = min(delay * 1.6, 60)


async def announce_online(cfg: cfgmod.Config, url: str) -> None:
    """Kompyuter onlayn bo'lganini xabar qiladi. Xato bo'lsa jimgina o'tadi."""
    t = cfg.telegram
    if not (t.enabled and t.on_start and t.bot_token and t.chat_id):
        return
    try:
        await wait_online()
        await Telegram(t.bot_token, t.chat_id).send(
            online_message(cfg, url), button=("Boshqarish", url)
        )
        log.info("Telegram xabari yuborildi")
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        # Xabar yuborilmagani dasturning ishlashiga to'sqinlik qilmasligi kerak
        log.warning("Telegram xabari yuborilmadi: %s", exc)


async def setup_interactive(token: str) -> int:
    """`--telegram <token>` uchun: botni tekshiradi va chat raqamini topadi."""
    tg = Telegram(token)
    try:
        me = await tg.me()
    except Exception as exc:
        print(f"Bot tokeni ishlamadi: {exc}")
        return 1

    username = me.get("username", "?")
    print(f"Bot topildi: @{username}")
    print()
    print(f"  Endi Telegram'da @{username} ni oching va /start yuboring.")
    print("  Kutyapman…")

    chat_id = await tg.discover_chat(timeout=180)
    if not chat_id:
        print("Xabar kelmadi. Qayta urinib ko'ring.")
        return 1

    cfg = cfgmod.load()
    cfg.telegram.enabled = True
    cfg.telegram.bot_token = token
    cfg.telegram.chat_id = chat_id
    cfgmod.save(cfg)

    tg.chat_id = chat_id
    await tg.send(
        f"✅ <b>{cfg.host_name}</b> ulandi.\n"
        "Bundan keyin kompyuter yonganda shu yerga xabar keladi."
    )
    print(f"Tayyor. Chat raqami: {chat_id}")
    print("Sinov xabari yuborildi - Telegram'ni tekshiring.")
    return 0
