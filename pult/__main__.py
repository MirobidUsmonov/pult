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

    def ready() -> None:
        if verbose:
            print("\n".join(appmod.connection_info(cfg)))
        else:
            logging.info("ishga tushdi: %s", appmod.phone_url(cfg))

    # serve() ishlatiladi, chunki tunnel va xabarnoma o'sha yerda
    # ulangan. Bu yerda alohida yozilsa ikkita rejim asta-sekin
    # bir-biridan farq qilib ketardi.
    try:
        await appmod.serve(cfg, stop, on_ready=ready)
    except appmod.FfmpegMissing as exc:
        logging.error("%s", exc)
        return 2
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
    parser.add_argument("--install", action="store_true",
                        help="doimiy joyga o'rnatib, avtomatik ishga tushirishni qo'shish")
    parser.add_argument("--uninstall", action="store_true",
                        help="avtomatik ishga tushirishni o'chirish")
    parser.add_argument("--remote", choices=["off", "cloudflare"],
                        help="tashqi kirish: bitta Wi-Fi chegarasidan chiqish")
    parser.add_argument("--update", metavar="MANBA",
                        help="o'z-o'zini yangilash manbasi: papka, http(s) "
                             "havola yoki \"off\"")
    parser.add_argument("--telegram", metavar="TOKEN",
                        help="Telegram xabarnomasini sozlash (@BotFather bergan token)")
    parser.add_argument("--test-notify", action="store_true",
                        help="Telegram xabarini hozir yuborib ko'rish")
    args = parser.parse_args()

    appmod.setup_logging(console=args.console or args.show or bool(args.telegram))
    cfg = _apply_args(cfgmod.load(), args)

    if args.install or args.uninstall:
        from . import setup as setupmod

        if args.uninstall:
            text = setupmod.uninstall()
        else:
            ok, text = setupmod.install()
            if ok:
                setupmod.launch(setupmod.install_dir() / setupmod.EXE_NAME)
        # Konsoldan ishga tushirilgan bo'lsa matn ko'rinadi, .exe dan
        # bosilgan bo'lsa - oyna. Ikkalasi ham kerak.
        print(text)
        if getattr(sys, "frozen", False):
            setupmod.message(text)
        return 0

    if args.update:
        fresh = cfgmod.load()
        value = args.update.strip()
        if value.lower() in ("off", "yo'q", "none"):
            fresh.update.mode = "off"
            fresh.update.source = ""
            print("O'z-o'zini yangilash o'chirildi.")
        else:
            fresh.update.mode = "url" if value.lower().startswith("http") else "folder"
            fresh.update.source = value
            print(f"Yangilash manbasi: {value}  (rejim: {fresh.update.mode})")
            print()
            print("  Dastur har ishga tushganda shu manbani tekshiradi va")
            print("  fayl boshqacha bo'lsa o'zini almashtirib qayta ishga")
            print(f"  tushadi. Ishlab turganda ham har "
                  f"{fresh.update.check_minutes} daqiqada tekshiradi,")
            print("  lekin faqat hech kim ulanmagan paytda yangilaydi.")
        cfgmod.save(fresh)
        return 0

    if args.remote:
        # Faylga yozamiz, shuning uchun buyruq qatoridagi boshqa
        # vaqtinchalik o'zgarishlar (--port va h.k.) tushib qolmasin
        fresh = cfgmod.load()
        fresh.remote.mode = args.remote
        cfgmod.save(fresh)
        if args.remote == "off":
            print("Tashqi kirish o'chirildi - faqat mahalliy tarmoq.")
        else:
            print("Tashqi kirish yoqildi: cloudflare.")
            print()
            print("  Pult qayta ishga tushganda cloudflared yuklab olinadi")
            print("  (bir marta, ~35 MB) va tashqi manzil ochiladi.")
            print("  Manzil har safar yangi bo'ladi va Telegram xabari")
            print("  bilan keladi - shuning uchun xabarnoma sozlangani")
            print("  ma'qul:  python -m pult --telegram <TOKEN>")
        return 0

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

    # Yig'ilgan .exe birinchi marta ochilganda o'zini o'rnatishni
    # taklif qiladi. Shu tufayli yangi kompyuterga bitta fayldan
    # boshqa hech narsa kerak emas.
    if getattr(sys, "frozen", False):
        from . import setup as setupmod
        from . import update as updatemod

        if setupmod.first_run():
            return 0

        # Yangi versiya bo'lsa o'zini almashtirib qayta ishga tushadi.
        # Server ko'tarilishidan oldin: portni band qilib olib, keyin
        # qayta ishga tushsak yangi nusxa portni ololmay qolardi.
        if asyncio.run(updatemod.apply_if_any(cfg)):
            return 0
        logging.info("versiya: %s; %s",
                     updatemod.stamp(), updatemod.describe(cfg))

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
