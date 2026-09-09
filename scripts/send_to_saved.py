"""
Faylni Telegram'dagi "Saqlangan xabarlar"ga yuboradi.

BIDO yordamchisining Telegram sessiyasidan foydalanadi. U akkaunt
nomidan ishlagani uchun Saqlangan xabarlarga yoza oladi - oddiy bot
buni qila olmaydi, chunki Saqlanganlar foydalanuvchining o'z chati.

Bu skript BIDO'ning o'z Python muhitida ishlashi kerak (Telethon o'sha
yerda). Yig'ish skripti uni o'zi to'g'ri interpretator bilan chaqiradi.

Ishlatish:
    python send_to_saved.py <fayl> [izoh]
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

BIDO = Path(r"C:\Users\Windows 11\mcp\claude-bot-run")
SESSION = BIDO / "bido_mcp.session"
# Kalitlar BIDO'ning o'z .env faylida emas, telegram-agent loyihasida
ENV = Path(
    r"C:\Users\Windows 11\Desktop\Antigravity Skills\projects\telegram-agent\.env"
)


def session_busy() -> bool:
    """Sessiyani boshqa dastur ushlab turibdimi.

    Bu tekshiruv juda muhim: bitta Telegram sessiyasini ikkita mijoz bir
    vaqtda ishlatsa, Telegram AuthKeyDuplicatedError beradi va IKKALA
    sessiyani ham bekor qiladi. BIDO'ning o'z kodida bu haqda
    ogohlantirish bor - ular allaqachon bir marta shu sababdan
    sessiyalarini yo'qotishgan. Shuning uchun band bo'lsa umuman
    ulanmaymiz.
    """
    import sqlite3

    try:
        con = sqlite3.connect(f"file:{SESSION}?mode=rw", uri=True, timeout=1)
        con.execute("BEGIN IMMEDIATE")
        con.rollback()
        con.close()
        return False
    except Exception:
        return True


def credentials() -> tuple[int, str]:
    """API kalitlarini BIDO sozlamalaridan oladi."""
    values = {}
    for line in ENV.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            values[k.strip()] = v.strip()
    if "API_ID" not in values or "API_HASH" not in values:
        raise SystemExit("BIDO sozlamalarida API_ID/API_HASH topilmadi")
    return int(values["API_ID"]), values["API_HASH"]


async def send(path: Path, caption: str) -> int:
    try:
        from telethon import TelegramClient
    except ImportError:
        print("Telethon topilmadi - skript BIDO muhitida ishga tushirilishi kerak")
        return 2

    if session_busy():
        print("Telegram sessiyasi band (BIDO ishlatyapti). Yubormadim - "
              "bir vaqtda ikkita ulanish sessiyani bekor qilib yuborardi.")
        return 3

    api_id, api_hash = credentials()
    client = TelegramClient(str(SESSION), api_id, api_hash)
    try:
        await client.connect()
    except Exception as exc:
        print(f"Telegram'ga ulanib bo'lmadi: {type(exc).__name__}: {exc}")
        return 1

    try:
        if not await client.is_user_authorized():
            print("Telegram sessiyasi eskirgan. BIDO'da telegram_login.py ni "
                  "qayta ishga tushirish kerak (telefon + kod).")
            return 1
        # "me" - foydalanuvchining o'zi, ya'ni Saqlangan xabarlar
        await client.send_file("me", str(path), caption=caption[:1024],
                               force_document=True)
        size = path.stat().st_size / 1024
        print(f"Saqlangan xabarlarga yuborildi: {path.name} ({size:.0f} KB)")
        return 0
    except Exception as exc:
        print(f"Yuborilmadi: {type(exc).__name__}: {exc}")
        return 1
    finally:
        await client.disconnect()


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    path = Path(sys.argv[1])
    if not path.is_file():
        print(f"Fayl topilmadi: {path}")
        return 2
    caption = sys.argv[2] if len(sys.argv) > 2 else path.name
    return asyncio.run(send(path, caption))


if __name__ == "__main__":
    sys.exit(main())
