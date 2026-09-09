"""
The part of startup shared by every entry point.

Whether the program is launched from a terminal or from the tray, this
code runs - so the two modes cannot drift apart.
"""
from __future__ import annotations

import asyncio
import logging
import logging.handlers
import sys

from . import config as cfgmod
from . import tls
from .host.server import HostServer
from .platform import ffmpeg as ff

log = logging.getLogger("pult")


class FfmpegMissing(RuntimeError):
    pass


def setup_logging(console: bool = False) -> None:
    cfgmod.log_path().parent.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [
        # Rotating log, so the file cannot grow without bound
        logging.handlers.RotatingFileHandler(
            cfgmod.log_path(), maxBytes=1_000_000, backupCount=2, encoding="utf-8"
        )
    ]
    if console and sys.stderr is not None:
        handlers.append(logging.StreamHandler(sys.stderr))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        handlers=handlers,
        force=True,
    )


def build_server(cfg: cfgmod.Config) -> HostServer:
    path = ff.find_ffmpeg(cfg.ffmpeg_path)
    if not path:
        raise FfmpegMissing(
            "ffmpeg not found. Install it, or point ffmpeg_path at it in "
            f"the settings file: {cfgmod.config_path()}"
        )
    caps = ff.probe(path)
    enc = ff.pick_encoder(caps, cfg.stream.encoder)
    # The settings folder is logged on purpose: depending on how the
    # program was started (terminal, task scheduler, from inside another
    # program) it can end up somewhere else, and then the keys stop
    # matching. This one line makes that obvious at a glance.
    log.info("settings: %s (computer %s)", cfgmod.config_dir(), cfg.host_id[:8])
    log.info("ffmpeg %s, encoder: %s", caps.version, enc["label"])
    return HostServer(cfg, caps)


def local_base(cfg: cfgmod.Config) -> str:
    """The address to open on the computer itself.

    A separate plain-HTTP door is used: with a self-signed certificate
    the browser warned every time and the program looked like a
    suspicious website. 127.0.0.1 counts as a secure context anyway, so
    video works there without HTTPS.
    """
    if cfg.tls == "off":
        return f"http://127.0.0.1:{cfg.port}"
    return f"http://127.0.0.1:{cfg.local_port or cfg.port + 1}"


def pair_url(cfg: cfgmod.Config) -> str:
    """The pairing page (QR code) as opened on the computer."""
    return f"{local_base(cfg)}/pair?k={cfg.token}"


def viewer_url(cfg: cfgmod.Config) -> str:
    """Link for watching the phone's screen on the computer's monitor.

    It is the same page the phone gets, only with a "view=phone" marker:
    as soon as a connected phone appears, that source is selected. If no
    phone is connected yet, the page explains what to do and waits.
    """
    return f"{local_base(cfg)}/#k={cfg.token}&view=phone"


def phone_url(cfg: cfgmod.Config) -> str:
    """The link handed to the phone.

    When a tunnel is configured the public address wins: it works from
    anywhere and carries a real certificate.
    """
    # The computer's id is included too. Because the tunnel address is
    # new on every start, this is how the app knows which computer a
    # link belongs to and which entry to update. The id is not a
    # secret - it shows up in /api/info without a key as well.
    tail = f"/#k={cfg.token}&h={cfg.host_id}"
    if cfg.public_url:
        return f"{cfg.public_url.rstrip('/')}{tail}"
    scheme = "http" if cfg.tls == "off" else "https"
    return f"{cfgmod.local_addresses(cfg.port, scheme)[0]}{tail}"


def connection_info(cfg: cfgmod.Config) -> list[str]:
    scheme = "http" if cfg.tls == "off" else "https"
    lines = [
        "",
        f"  Pult is running - {cfg.host_name}",
        "",
        "  Open this address on your phone:",
    ]
    if cfg.public_url:
        lines.append(f"    {cfg.public_url.rstrip('/')}/#k={cfg.token}   <- from anywhere")
    for url in cfgmod.local_addresses(cfg.port, scheme):
        lines.append(f"    {url}/#k={cfg.token}")
    lines += ["", f"  Or the QR code:  {pair_url(cfg)}"]
    if not cfg.public_url and cfg.remote.mode == "off":
        lines += [
            "",
            "  These addresses only work inside this Wi-Fi. To reach the",
            "  computer from anywhere:  python -m pult --remote cloudflare",
        ]
    if scheme == "https":
        lines += [
            "",
            "  The browser will warn about the certificate - that is expected.",
            "  Click \"Advanced\" -> \"Proceed anyway\". Once.",
        ]
        fp = tls.fingerprint(cfgmod.config_dir())
        if fp:
            lines.append(f"  Certificate fingerprint: {fp}")
    lines += ["", f"  Settings: {cfgmod.config_path()}", ""]
    return lines


async def start_remote(cfg: cfgmod.Config):
    """Opens the remote-access tunnel, when enabled in the settings.

    On failure the program carries on: the local network still works,
    and losing that would be worse than losing the tunnel.
    """
    from . import tunnel

    try:
        return await tunnel.create(cfg, cfgmod.config_dir())
    except Exception:
        log.warning("could not open remote access", exc_info=True)
        return None


async def track_public_url(cfg: cfgmod.Config, tun) -> None:
    """Keeps the settings in step with the tunnel's address.

    This is not a one-off: if the tunnel drops and reconnects, the
    address is new. Keeping the old one around is harmful - it does not
    work, but it looks as if it does.
    """
    while True:
        await tun.ready.wait()
        if tun.url:
            cfg.public_url = tun.url
        while tun.ready.is_set():
            await asyncio.sleep(1)
        cfg.public_url = ""


def start_notifier(cfg: cfgmod.Config, tun=None) -> asyncio.Task | None:
    """Starts the "computer is online" notification as a background task.

    Being a separate task matters: if there is no internet yet the
    notification waits for it, while the server is already up - the
    local network does not need internet at all.
    """
    from . import notify

    if not (cfg.telegram.enabled and cfg.telegram.on_start):
        return None

    async def run() -> None:
        # Wait for the tunnel's address: the message should carry the
        # address that works from anywhere, not a local IP. Since that
        # address is new every time, it is the message's main value.
        if tun is not None:
            await tun.wait_url(timeout=90)
        await notify.announce_online(cfg, phone_url(cfg))

    return asyncio.create_task(run(), name="pult-notify")


async def serve(cfg: cfgmod.Config, stop: asyncio.Event, on_ready=None) -> None:
    """Runs the server until the stop signal.

    on_ready is called once the server is listening, without waiting for
    the tunnel. The tunnel can take a few seconds; the local network is
    ready immediately.
    """
    server = build_server(cfg)
    await server.start()
    if on_ready:
        on_ready()
    tun = await start_remote(cfg)
    tracker = asyncio.create_task(track_public_url(cfg, tun)) if tun else None
    notifier = start_notifier(cfg, tun)
    try:
        await stop.wait()
    finally:
        for task in (notifier, tracker):
            if task:
                task.cancel()
        if tun:
            await tun.stop()
        await server.stop()
