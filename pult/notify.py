"""
Notifications: a message to the phone when the computer comes online.

Telegram was not an arbitrary choice: it is already installed on the
phone, messages arrive everywhere, and no separate push infrastructure
(Firebase and friends) has to be built.

Finding the chat id by hand is the most tedious part, so the program
works it out itself: you send /start to the bot, the program sees it
through getUpdates and stores it.
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


def _is_public(url: str) -> bool:
    """Whether the link can go on a Telegram button.

    Telegram inline buttons only accept public addresses: for a local IP
    or localhost no button is made and the link is sent as text.
    """
    try:
        host = urlparse(url).hostname or ""
        if not host or host in ("localhost", "127.0.0.1"):
            return False
        try:
            return not ipaddress.ip_address(host).is_private
        except ValueError:
            return "." in host  # a domain name
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
            raise RuntimeError(data.get("description", "Telegram error"))
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
        """Returns the chat id of the first person to write to the bot.

        Long polling is used, so almost no requests go over the network
        while waiting.
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
    """The system name.

    platform.release() returns "10" even on Windows 11 - a known Python
    quirk. The real version comes from the build number: 22000 and above
    is Windows 11.
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
    lines = [
        f"<b>{cfg.host_name}</b> is online",
        f"{now:%H:%M} · {now:%d %b}",
        os_name(),
        "",
        f"<code>{url}</code>",
    ]
    # The tunnel address is new every time. Without saying so, someone
    # saves the old link and then cannot work out why it stopped working.
    if _is_public(url):
        lines += ["", "Works from any network. The link changes when the "
                      "computer restarts."]
    return "\n".join(lines)


async def wait_online(timeout: float = 0) -> bool:
    """Waits for the internet to appear.

    When the computer boots, the program can start before the network
    does, so sending a message straight away almost always fails. We
    poll Telegram's own server - it is the address we need anyway.
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
    """Announces that the computer is online. Fails quietly."""
    t = cfg.telegram
    if not (t.enabled and t.on_start and t.bot_token and t.chat_id):
        return
    try:
        await wait_online()
        await Telegram(t.bot_token, t.chat_id).send(
            online_message(cfg, url), button=("Open Pult", url)
        )
        log.info("Telegram message sent")
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        # A message that did not go out must not stop the program
        log.warning("Telegram message not sent: %s", exc)


async def setup_interactive(token: str) -> int:
    """For `--telegram <token>`: checks the bot and finds the chat id."""
    tg = Telegram(token)
    try:
        me = await tg.me()
    except Exception as exc:
        print(f"The bot token did not work: {exc}")
        return 1

    username = me.get("username", "?")
    print(f"Bot found: @{username}")
    print()
    print(f"  Now open @{username} in Telegram and send /start.")
    print("  Waiting…")

    chat_id = await tg.discover_chat(timeout=180)
    if not chat_id:
        print("No message arrived. Try again.")
        return 1

    cfg = cfgmod.load()
    cfg.telegram.enabled = True
    cfg.telegram.bot_token = token
    cfg.telegram.chat_id = chat_id
    cfgmod.save(cfg)

    tg.chat_id = chat_id
    await tg.send(
        f"<b>{cfg.host_name}</b> is connected.\n"
        "From now on you will get a message here when it comes online."
    )
    print(f"Done. Chat id: {chat_id}")
    print("A test message was sent - check Telegram.")
    return 0
