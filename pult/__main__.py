"""
Starting the Pult agent.

The normal case is the tray, with no console. For troubleshooting it can
be started from a terminal with --console.
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
            logging.info("started: %s", appmod.phone_url(cfg))

    # serve() is used because the tunnel and the notifier are wired up
    # there. Repeating that here would let the two modes drift apart.
    try:
        await appmod.serve(cfg, stop, on_ready=ready)
    except appmod.FfmpegMissing as exc:
        logging.error("%s", exc)
        return 2
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="pult", description="Control your computer from your phone"
    )
    parser.add_argument("--port", type=int, help="port to listen on (default 8787)")
    parser.add_argument("--bind", help="address to listen on (default 0.0.0.0)")
    parser.add_argument("--console", action="store_true",
                        help="no tray, logs go to the terminal")
    parser.add_argument("--no-tls", action="store_true",
                        help="plain HTTP instead of HTTPS (only behind a tunnel)")
    parser.add_argument("--show", action="store_true",
                        help="print the connection details and exit")
    parser.add_argument("--install", action="store_true",
                        help="install to a permanent location and start at logon")
    parser.add_argument("--uninstall", action="store_true",
                        help="remove the logon task")
    parser.add_argument("--remote", choices=["off", "cloudflare"],
                        help="remote access: reach the computer from any network")
    parser.add_argument("--update", metavar="SOURCE",
                        help="self-update source: a folder, an http(s) link "
                             "or \"off\"")
    parser.add_argument("--telegram", metavar="TOKEN",
                        help="set up Telegram notifications (token from @BotFather)")
    parser.add_argument("--test-notify", action="store_true",
                        help="send a Telegram message right now")
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
        # Started from a console the text is visible; double-clicked as
        # an .exe it is not, so a dialog is needed too.
        print(text)
        if getattr(sys, "frozen", False):
            setupmod.message(text)
        return 0

    if args.update:
        fresh = cfgmod.load()
        value = args.update.strip()
        if value.lower() in ("off", "none"):
            fresh.update.mode = "off"
            fresh.update.source = ""
            print("Self-update turned off.")
        else:
            fresh.update.mode = "url" if value.lower().startswith("http") else "folder"
            fresh.update.source = value
            print(f"Update source: {value}  (mode: {fresh.update.mode})")
            print()
            print("  On every start the program checks this source and, if")
            print("  the file differs, replaces itself and restarts. While")
            print(f"  running it also checks every "
                  f"{fresh.update.check_minutes} minutes, but only")
            print("  applies an update when nobody is connected.")
        cfgmod.save(fresh)
        return 0

    if args.remote:
        # Written to the file, so temporary command-line changes
        # (--port and friends) do not get saved along with it
        fresh = cfgmod.load()
        fresh.remote.mode = args.remote
        cfgmod.save(fresh)
        if args.remote == "off":
            print("Remote access turned off - local network only.")
        else:
            print("Remote access enabled: cloudflare.")
            print()
            print("  On the next start Pult downloads cloudflared once")
            print("  (~50 MB) and opens a public address.")
            print("  That address is new every time and arrives by Telegram,")
            print("  so setting notifications up is worth it:")
            print("      python -m pult --telegram <TOKEN>")
        return 0

    if args.telegram:
        from . import notify

        return asyncio.run(notify.setup_interactive(args.telegram))

    if args.test_notify:
        from . import notify

        if not cfg.telegram.chat_id:
            print("Telegram is not set up. First:  python -m pult --telegram <TOKEN>")
            return 1

        async def _test() -> int:
            tg = notify.Telegram(cfg.telegram.bot_token, cfg.telegram.chat_id)
            url = appmod.phone_url(cfg)
            await tg.send(notify.online_message(cfg, url), button=("Open Pult", url))
            print("Message sent.")
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

    # Opened for the first time, the built .exe offers to install
    # itself. That is why a new computer needs nothing but this one
    # file.
    if getattr(sys, "frozen", False):
        from . import setup as setupmod
        from . import single
        from . import update as updatemod

        if setupmod.first_run():
            return 0

        # One copy is enough. There is a wait because right after an
        # update the old copy may not have exited yet.
        if not single.acquire(timeout=20):
            # Closing the second copy silently is no good: clicking the
            # desktop icon would look like nothing happened. If the
            # program is already running, a click means one thing -
            # open the window.
            logging.info("another copy is running - opening the window")
            from . import window

            window.open_url(appmod.viewer_url(cfg), size=(980, 720))
            return 0

        # A newer version replaces this one and restarts. This happens
        # before the server binds: taking the port first would leave the
        # new copy unable to get it.
        if asyncio.run(updatemod.apply_if_any(cfg)):
            return 0
        logging.info("version: %s; %s",
                     updatemod.stamp(), updatemod.describe(cfg))

    # The normal path: the tray. Without pystray we carry on headless -
    # the program still has to work.
    try:
        from . import tray
    except ImportError:
        logging.warning("pystray not found, running without a tray icon")
        return asyncio.run(run_headless(cfg, verbose=False))

    return tray.run(cfg)


if __name__ == "__main__":
    raise SystemExit(main())
