"""
Telegram sessiyasini qayta tiklash — ikki bosqichda.

BIDO'ning o'z login skripti savollarni interaktiv so'raydi, bu esa
avtomatlashtirishga yaramaydi. Bu skript bosqichlarni ajratadi:
kodni so'rash va kodni kiritish alohida chaqiriladi, shuning uchun
kodni boshqa odam ham (masalan yordamchi) kiritishi mumkin.

    python telegram_relogin.py request +998901234567
    python telegram_relogin.py code 12345
    python telegram_relogin.py password <parol>     # ikki bosqichli himoya bo'lsa
    python telegram_relogin.py check

MUHIM: Telegram kirish kodini SMS bilan emas, Telegram ilovasining
o'zida yuboradi — "Telegram" nomli rasmiy chatga xabar bo'lib keladi.
Boshqa qurilmada kirgan bo'lsangiz doim shunday bo'ladi.

Skript BIDO muhitida ishlashi kerak (Telethon o'sha yerda):
    "C:\\Users\\Windows 11\\mcp\\claude-bot-run\\.venv-telegram\\Scripts\\python.exe"
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
from pathlib import Path

BIDO = Path(r"C:\Users\Windows 11\mcp\claude-bot-run")
SESSION = BIDO / "bido_mcp"
SESSION_FILE = BIDO / "bido_mcp.session"
STATE = BIDO / ".relogin-state.json"
ENV = Path(
    r"C:\Users\Windows 11\Desktop\Antigravity Skills\projects\telegram-agent\.env"
)


def credentials() -> tuple[int, str]:
    values = {}
    for line in ENV.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            values[k.strip()] = v.strip()
    return int(values["API_ID"]), values["API_HASH"]


def session_busy() -> bool:
    """Sessiyani boshqa dastur ushlab turibdimi.

    Bitta sessiyaga ikkita mijoz ulansa Telegram ikkalasini ham bekor
    qiladi - shuning uchun band bo'lsa umuman tegmaymiz.
    """
    if not SESSION_FILE.exists():
        return False
    try:
        con = sqlite3.connect(f"file:{SESSION_FILE}?mode=rw", uri=True, timeout=1)
        con.execute("BEGIN IMMEDIATE")
        con.rollback()
        con.close()
        return False
    except Exception:
        return True


def client():
    from telethon import TelegramClient

    api_id, api_hash = credentials()
    return TelegramClient(str(SESSION), api_id, api_hash)


async def do_request(phone: str) -> int:
    c = client()
    await c.connect()
    try:
        if await c.is_user_authorized():
            me = await c.get_me()
            print(f"Allaqachon kirilgan: {me.first_name} (@{me.username or '-'})")
            return 0
        sent = await c.send_code_request(phone)
        STATE.write_text(json.dumps({
            "phone": phone,
            "hash": sent.phone_code_hash,
        }), encoding="utf-8")
        print("Kod yuborildi.")
        print("Diqqat: kod SMS emas, TELEGRAM ILOVASIGA keladi —")
        print('"Telegram" nomli rasmiy chatni oching.')
        print()
        print("Keyingi qadam:  telegram_relogin.py code <KOD>")
        return 0
    finally:
        await c.disconnect()


async def do_code(code: str) -> int:
    if not STATE.exists():
        print("Avval kod so'ralmagan. Ishga tushiring: request <telefon>")
        return 2
    state = json.loads(STATE.read_text(encoding="utf-8"))
    c = client()
    await c.connect()
    try:
        from telethon.errors import SessionPasswordNeededError

        try:
            await c.sign_in(phone=state["phone"], code=code,
                            phone_code_hash=state["hash"])
        except SessionPasswordNeededError:
            print("Ikki bosqichli himoya yoqilgan.")
            print("Keyingi qadam:  telegram_relogin.py password <PAROL>")
            return 3
        me = await c.get_me()
        STATE.unlink(missing_ok=True)
        print(f"Kirildi: {me.first_name} (@{me.username or '-'}, id={me.id})")
        return 0
    except Exception as exc:
        print(f"Kirib bo'lmadi: {type(exc).__name__}: {exc}")
        return 1
    finally:
        await c.disconnect()


async def do_password(password: str) -> int:
    c = client()
    await c.connect()
    try:
        await c.sign_in(password=password)
        me = await c.get_me()
        STATE.unlink(missing_ok=True)
        print(f"Kirildi: {me.first_name} (@{me.username or '-'}, id={me.id})")
        return 0
    except Exception as exc:
        print(f"Parol qabul qilinmadi: {type(exc).__name__}: {exc}")
        return 1
    finally:
        await c.disconnect()


async def do_check() -> int:
    c = client()
    await c.connect()
    try:
        if await c.is_user_authorized():
            me = await c.get_me()
            print(f"Sessiya tirik: {me.first_name} (@{me.username or '-'})")
            return 0
        print("Sessiya o'lik — qayta kirish kerak")
        return 1
    finally:
        await c.disconnect()


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    try:
        import telethon  # noqa: F401
    except ImportError:
        print("Telethon topilmadi — skript BIDO muhitida ishga tushirilishi kerak")
        return 2

    if session_busy():
        print("Sessiya band (BIDO ishlatyapti). Avval uni to'xtating.")
        return 3

    cmd = sys.argv[1]
    arg = sys.argv[2] if len(sys.argv) > 2 else ""
    if cmd == "request" and arg:
        return asyncio.run(do_request(arg))
    if cmd == "code" and arg:
        return asyncio.run(do_code(arg.strip().replace(" ", "")))
    if cmd == "password" and arg:
        return asyncio.run(do_password(arg))
    if cmd == "check":
        return asyncio.run(do_check())
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
