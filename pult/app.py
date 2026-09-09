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
    # Sozlama papkasini yozib qo'yamiz: dastur turli usullar bilan ishga
    # tushirilganda (terminal, vazifa rejalashtiruvchisi, boshqa dastur
    # ichidan) papka boshqacha bo'lib qolishi mumkin va shunda kalitlar
    # mos kelmaydi. Bu qatorni ko'rib darrov tushunish oson.
    log.info("sozlamalar: %s (kompyuter %s)", cfgmod.config_dir(), cfg.host_id[:8])
    log.info("ffmpeg %s, kodlagich: %s", caps.version, enc["label"])
    return HostServer(cfg, caps)


def pair_url(cfg: cfgmod.Config) -> str:
    """Kompyuterning o'z brauzerida ochiladigan ulash sahifasi."""
    scheme = "http" if cfg.tls == "off" else "https"
    return f"{scheme}://127.0.0.1:{cfg.port}/pair?k={cfg.token}"


def viewer_url(cfg: cfgmod.Config) -> str:
    """Telefon ekranini kompyuter monitorida ko'rish uchun havola.

    Sahifa telefondagining o'zi, faqat "view=phone" belgisi bilan:
    ulangan telefon paydo bo'lishi bilan o'sha manba tanlanadi.
    Telefon hali ulanmagan bo'lsa sahifa nima qilish kerakligini
    yozib kutib turadi.
    """
    scheme = "http" if cfg.tls == "off" else "https"
    return f"{scheme}://127.0.0.1:{cfg.port}/#k={cfg.token}&view=phone"


def phone_url(cfg: cfgmod.Config) -> str:
    """Telefonga beriladigan havola.

    Tunnel sozlangan bo'lsa tashqi manzil afzal: u har joydan ishlaydi
    va haqiqiy sertifikatga ega.
    """
    # Kompyuter raqami ham qo'shiladi: tunnel manzili har safar
    # o'zgargani uchun ilova havolani ko'rib, qaysi kompyuter ekanini
    # va qaysi yozuvni yangilash kerakligini shundan biladi. Raqam sir
    # emas - u kalitsiz /api/info da ham ko'rinadi.
    tail = f"/#k={cfg.token}&h={cfg.host_id}"
    if cfg.public_url:
        return f"{cfg.public_url.rstrip('/')}{tail}"
    scheme = "http" if cfg.tls == "off" else "https"
    return f"{cfgmod.local_addresses(cfg.port, scheme)[0]}{tail}"


def connection_info(cfg: cfgmod.Config) -> list[str]:
    scheme = "http" if cfg.tls == "off" else "https"
    lines = [
        "",
        f"  Pult ishga tushdi - {cfg.host_name}",
        "",
        "  Telefondan shu manzilga kiring:",
    ]
    if cfg.public_url:
        lines.append(f"    {cfg.public_url.rstrip('/')}/#k={cfg.token}   <- har joydan")
    for url in cfgmod.local_addresses(cfg.port, scheme):
        lines.append(f"    {url}/#k={cfg.token}")
    lines += ["", f"  Yoki QR kod:  {pair_url(cfg)}"]
    if not cfg.public_url and cfg.remote.mode == "off":
        lines += [
            "",
            "  Bu manzillar faqat shu Wi-Fi ichida ishlaydi. Har joydan",
            "  ulanish uchun:  python -m pult --remote cloudflare",
        ]
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


async def start_remote(cfg: cfgmod.Config):
    """Tashqi kirish tunnelini ochadi (sozlamada yoqilgan bo'lsa).

    Xato bo'lsa dastur ishlashda davom etadi: mahalliy tarmoqdan
    ulanish baribir mumkin va uni yo'qotish tunneldan ko'ra yomonroq.
    """
    from . import tunnel

    try:
        return await tunnel.create(cfg, cfgmod.config_dir())
    except Exception:
        log.warning("tashqi kirish ochilmadi", exc_info=True)
        return None


async def track_public_url(cfg: cfgmod.Config, tun) -> None:
    """Tunnel manzilini sozlamaga ko'chirib turadi.

    Bu bir martalik ish emas: tunnel uzilib qayta ulansa manzil yangi
    bo'ladi. Eski manzilni saqlab qolish zararli - u ishlamaydi,
    lekin ishlaydigandek ko'rinadi.
    """
    while True:
        await tun.ready.wait()
        if tun.url:
            cfg.public_url = tun.url
        while tun.ready.is_set():
            await asyncio.sleep(1)
        cfg.public_url = ""


def start_notifier(cfg: cfgmod.Config, tun=None) -> asyncio.Task | None:
    """Kompyuter onlayn bo'lganini xabar qilishni fon vazifasi sifatida
    boshlaydi.

    Alohida vazifa bo'lgani muhim: internet hali yo'q bo'lsa xabarnoma
    uni kutadi, lekin server bu vaqtda allaqachon ishlab turadi -
    mahalliy tarmoqdan ulanish uchun internet shart emas.
    """
    from . import notify

    if not (cfg.telegram.enabled and cfg.telegram.on_start):
        return None

    async def run() -> None:
        # Tunnel manzilini kutamiz: xabarda mahalliy IP emas, har
        # joydan ochiladigan manzil bo'lishi kerak. Manzil har safar
        # yangi bo'lgani uchun xabarning asosiy foydasi ham shu.
        if tun is not None:
            await tun.wait_url(timeout=90)
        await notify.announce_online(cfg, phone_url(cfg))

    return asyncio.create_task(run(), name="pult-notify")


async def serve(cfg: cfgmod.Config, stop: asyncio.Event, on_ready=None) -> None:
    """Serverni ishga tushirib, to'xtatish signaligacha kutadi.

    on_ready server tinglay boshlagach chaqiriladi - tunnel ochilishini
    kutmasdan. Tunnel bir necha soniya olishi mumkin, mahalliy
    tarmoqdan ulanish esa allaqachon tayyor.
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
