"""
Dasturni ishga tushirishning umumiy qismi.

Terminaldan ishga tushirilsa ham, treydan ishga tushirilsa ham shu
yerdagi kod ishlaydi - shunda ikkita rejim bir-biridan chetlashib
ketmaydi.
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
        # Aylanma log: fayl cheksiz o'smaydi
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
            "ffmpeg topilmadi. Uni o'rnating yoki sozlamalar faylida "
            f"ffmpeg_path ni ko'rsating: {cfgmod.config_path()}"
        )
    caps = ff.probe(path)
    enc = ff.pick_encoder(caps, cfg.stream.encoder)
    log.info("ffmpeg %s, kodlagich: %s", caps.version, enc["label"])
    return HostServer(cfg, caps)


def pair_url(cfg: cfgmod.Config) -> str:
    """Kompyuterning o'z brauzerida ochiladigan ulash sahifasi."""
    scheme = "http" if cfg.tls == "off" else "https"
    return f"{scheme}://127.0.0.1:{cfg.port}/pair?k={cfg.token}"


def phone_url(cfg: cfgmod.Config) -> str:
    scheme = "http" if cfg.tls == "off" else "https"
    return f"{cfgmod.local_addresses(cfg.port, scheme)[0]}/#k={cfg.token}"


def connection_info(cfg: cfgmod.Config) -> list[str]:
    scheme = "http" if cfg.tls == "off" else "https"
    lines = [
        "",
        f"  Pult ishga tushdi - {cfg.host_name}",
        "",
        "  Telefondan shu manzilga kiring:",
    ]
    for url in cfgmod.local_addresses(cfg.port, scheme):
        lines.append(f"    {url}/#k={cfg.token}")
    lines += ["", f"  Yoki QR kod:  {pair_url(cfg)}"]
    if scheme == "https":
        lines += [
            "",
            "  Brauzer sertifikat haqida ogohlantiradi - bu normal holat.",
            "  \"Qo'shimcha\" -> \"Baribir davom etish\" ni bosing. Bir marta.",
        ]
        fp = tls.fingerprint(cfgmod.config_dir())
        if fp:
            lines.append(f"  Sertifikat izi: {fp}")
    lines += ["", f"  Sozlamalar: {cfgmod.config_path()}", ""]
    return lines


async def serve(cfg: cfgmod.Config, stop: asyncio.Event) -> None:
    """Serverni ishga tushirib, to'xtatish signaligacha kutadi."""
    server = build_server(cfg)
    await server.start()
    try:
        await stop.wait()
    finally:
        await server.stop()
