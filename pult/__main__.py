"""
Pult agentini ishga tushirish.

Odatiy holat - treyda, konsolsiz. Nosozlikni izlash uchun --console
bilan terminaldan ishga tushirish mumkin.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys

from . import app as appmod
from . import config as cfgmod


def _apply_args(cfg: cfgmod.Config, args: argparse.Namespace) -> cfgmod.Config:
    if args.port:
        cfg.port = args.port
    if args.bind:
        cfg.bind = args.bind
    if args.no_tls:
        cfg.tls = "off"
    return cfg


async def run_headless(cfg: cfgmod.Config, verbose: bool) -> int:
    stop = asyncio.Event()

    def _signal(*_):
        stop.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _signal)
        except (ValueError, AttributeError):
            pass

    try:
        server = appmod.build_server(cfg)
    except appmod.FfmpegMissing as exc:
        logging.error("%s", exc)
        return 2

    await server.start()
    text = "\n".join(appmod.connection_info(cfg))
    print(text) if verbose else logging.info("ishga tushdi: %s", appmod.phone_url(cfg))

    try:
        await stop.wait()
    finally:
        await server.stop()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="pult", description="Telefondan kompyuterni boshqarish"
    )
    parser.add_argument("--port", type=int, help="tinglash porti (standart 8787)")
    parser.add_argument("--bind", help="tinglash manzili (standart 0.0.0.0)")
    parser.add_argument("--console", action="store_true",
                        help="treysiz, loglar terminalga chiqadi")
    parser.add_argument("--no-tls", action="store_true",
                        help="HTTPS o'rniga oddiy HTTP (faqat tunnel orqasida)")
    parser.add_argument("--show", action="store_true",
                        help="ulanish manzilini ko'rsatib chiqish")
    parser.add_argument("--telegram", metavar="TOKEN",
                        help="Telegram xabarnomasini sozlash (@BotFather bergan token)")
    parser.add_argument("--test-notify", action="store_true",
                        help="Telegram xabarini hozir yuborib ko'rish")
    args = parser.parse_args()

    appmod.setup_logging(console=args.console or args.show or bool(args.telegram))
    cfg = _apply_args(cfgmod.load(), args)

    if args.telegram:
        from . import notify

        return asyncio.run(notify.setup_interactive(args.telegram))

    if args.test_notify:
        from . import notify

        if not cfg.telegram.chat_id:
            print("Telegram sozlanmagan. Avval:  python -m pult --telegram <TOKEN>")
            return 1

        async def _test() -> int:
            tg = notify.Telegram(cfg.telegram.bot_token, cfg.telegram.chat_id)
            url = appmod.phone_url(cfg)
            await tg.send(notify.online_message(cfg, url), button=("Boshqarish", url))
            print("Xabar yuborildi.")
            return 0

        return asyncio.run(_test())

    if args.show:
        print("\n".join(appmod.connection_info(cfg)))
        return 0

    if args.console:
        try:
            return asyncio.run(run_headless(cfg, verbose=True))
        except KeyboardInterrupt:
            return 0

    # Odatiy yo'l: trey. pystray bo'lmasa konsolsiz fon rejimida davom
    # etamiz - dastur baribir ishlashi kerak.
    try:
        from . import tray
    except ImportError:
        logging.warning("pystray topilmadi, trey ikonkasisiz ishlaymiz")
        return asyncio.run(run_headless(cfg, verbose=False))

    return tray.run(cfg)


if __name__ == "__main__":
    raise SystemExit(main())
