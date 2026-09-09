"""
Mahalliy server: veb-sahifani beradi va WebSocket ulanishini qabul qiladi.

Bu "to'g'ridan-to'g'ri" rejim - telefon kompyuterga bevosita ulanadi.
Hub rejimida ham xuddi shu HostContext ishlatiladi, faqat transport
boshqacha bo'ladi.
"""
from __future__ import annotations

import asyncio
import hmac
import json
import logging
import sys
from pathlib import Path

from aiohttp import WSMsgType, web

from .. import tls
from ..config import Config, config_dir, local_addresses
from ..platform import ffmpeg as ff
from .pairing import render_pair_page
from .session import ControllerSession, HostContext

log = logging.getLogger("pult.server")


def web_root() -> Path:
    """Veb-fayllar papkasi (dastur .exe qilib yig'ilganda ham topiladi)."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", ".")) / "web"
    return Path(__file__).resolve().parents[2] / "web"


def _authorized(request: web.Request, token: str) -> bool:
    """Kalitni tekshiradi.

    compare_digest ishlatilgan: oddiy == solishtirish javob vaqti orqali
    kalitni bitta-bitta topib olishga imkon beradi.
    """
    given = request.query.get("k") or request.headers.get("X-Pult-Key", "")
    if not given:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            given = auth[7:]
    return bool(given) and hmac.compare_digest(given, token)


class HostServer:
    def __init__(self, cfg: Config, caps: ff.Capabilities) -> None:
        self.cfg = cfg
        self.ctx = HostContext(cfg, caps)
        self.app = web.Application(middlewares=[self._no_cache])
        self._runner: web.AppRunner | None = None
        self._setup_routes()

    @staticmethod
    @web.middleware
    async def _no_cache(request: web.Request, handler):
        """Veb-fayllar keshlanmasin.

        Dastur yangilanganda telefon eski sahifani keshdan olib qolardi va
        yangi imkoniyatlar ko'rinmasdi - buni tushunish qiyin, chunki
        tashqaridan hech qanday xato ko'rinmaydi. Fayllar mahalliy
        tarmoqdan kelgani uchun keshdan yutuq deyarli yo'q.
        """
        response = await handler(request)
        if request.path.startswith("/ws"):
            return response
        response.headers["Cache-Control"] = "no-store, must-revalidate"
        return response

    def _setup_routes(self) -> None:
        root = web_root()
        self.app.router.add_get("/ws", self.ws_handler)
        self.app.router.add_get("/api/info", self.info_handler)
        self.app.router.add_get("/cert.pem", self.cert_handler)
        self.app.router.add_get("/pair", self.pair_handler)
        self.app.router.add_post("/api/pair/send", self.pair_send_handler)
        self.app.router.add_get("/", self.index_handler)
        if root.is_dir():
            self.app.router.add_static("/", root, show_index=False)
        else:
            log.warning("veb papkasi topilmadi: %s", root)

    # -- HTTP --------------------------------------------------------------

    async def index_handler(self, request: web.Request) -> web.StreamResponse:
        index = web_root() / "index.html"
        if not index.is_file():
            return web.Response(text="veb interfeys topilmadi", status=500)
        return web.FileResponse(index, headers={"Cache-Control": "no-cache"})

    async def pair_handler(self, request: web.Request) -> web.StreamResponse:
        """Telefonni ulash sahifasi: QR kod va havola.

        Kompyuterning o'z brauzerida ochiladi (treydan). Telefon QR ni
        skanerlaydi - kalitni qo'lda ko'chirish shart emas.
        """
        if not _authorized(request, self.cfg.token):
            return web.Response(text="kalit kerak", status=401)
        urls = local_addresses(self.cfg.port, self.scheme)
        # Tunnel ochiq bo'lsa QR kodga o'sha manzil tushadi: bir marta
        # skanerlangan telefon keyin istalgan tarmoqdan ulanaveradi.
        # Mahalliy manzillar ro'yxatda qoladi - ular tezroq ishlaydi.
        if self.cfg.public_url:
            urls = [self.cfg.public_url.rstrip("/")] + urls
        target = f"{urls[0]}/#k={self.cfg.token}"
        html = render_pair_page(target, urls, self.cfg, tls.fingerprint(config_dir()))
        return web.Response(text=html, content_type="text/html")

    async def pair_send_handler(self, request: web.Request) -> web.StreamResponse:
        """Ulash havolasini Telegramga yuboradi.

        Kamera bilan QR skanerlash har doim ham qulay emas: kod ekranda
        kichik bo'lishi, kamera fokusga tushmasligi mumkin. Telegram
        esa telefonda allaqachon ochiq - havola bir soniyada yetadi va
        uni ilovaga "Ulashish" orqali berish kifoya.
        """
        if not _authorized(request, self.cfg.token):
            return web.json_response({"ok": False, "msg": "kalit noto'g'ri"}, status=401)

        t = self.cfg.telegram
        if not (t.bot_token and t.chat_id):
            return web.json_response({"ok": False, "msg": "Telegram sozlanmagan"})

        from .. import notify

        base = self.cfg.public_url.rstrip("/") if self.cfg.public_url else \
            local_addresses(self.cfg.port, self.scheme)[0]
        link = f"{base}/#k={self.cfg.token}&h={self.cfg.host_id}"
        text = (
            f"🔗 <b>{self.cfg.host_name}</b> — ulash havolasi\n\n"
            f"<code>{link}</code>\n\n"
            "Havolani bosib turing → <b>Ulashish</b> → <b>Pult</b>."
        )
        try:
            await notify.Telegram(t.bot_token, t.chat_id).send(text)
            return web.json_response({"ok": True})
        except Exception as exc:
            log.warning("ulash havolasi yuborilmadi: %s", exc)
            return web.json_response({"ok": False, "msg": str(exc)[:120]})

    async def cert_handler(self, request: web.Request) -> web.StreamResponse:
        """Sertifikatni yuklab olish.

        Telefonga o'rnatib qo'ysa, brauzer har safar ogohlantirish
        ko'rsatmaydi. Ixtiyoriy qulaylik.
        """
        path = config_dir() / tls.CERT_NAME
        if not path.is_file():
            return web.Response(text="sertifikat yo'q", status=404)
        return web.FileResponse(
            path,
            headers={
                "Content-Type": "application/x-x509-ca-cert",
                "Content-Disposition": 'attachment; filename="pult.crt"',
            },
        )

    async def info_handler(self, request: web.Request) -> web.StreamResponse:
        """Qisqa ma'lumot. Kalitsiz faqat nom ko'rinadi.

        Nomni ochiq qoldirish ataylab: telefon ro'yxatdagi kompyuterlardan
        qaysi biri onlayn ekanini kalit yubormasdan bilishi uchun.
        """
        public = {"name": self.cfg.host_name, "id": self.cfg.host_id, "ok": True}
        if not _authorized(request, self.cfg.token):
            return web.json_response(public)
        data = self.ctx.hello_message()
        data.update(public)
        return web.json_response(data)

    # -- WebSocket ---------------------------------------------------------

    async def ws_handler(self, request: web.Request) -> web.StreamResponse:
        if not _authorized(request, self.cfg.token):
            log.warning("noto'g'ri kalit bilan urinish: %s", request.remote)
            return web.json_response({"error": "kalit noto'g'ri"}, status=401)

        ws = web.WebSocketResponse(heartbeat=20, max_msg_size=4 * 1024 * 1024)
        await ws.prepare(request)

        async def send_json(data: dict) -> None:
            if not ws.closed:
                await ws.send_str(json.dumps(data, ensure_ascii=False))

        async def send_binary(data: bytes) -> None:
            if not ws.closed:
                await ws.send_bytes(data)

        session = ControllerSession(self.ctx, send_json, send_binary, peer=str(request.remote))
        await self.ctx.attach(session)
        await session.start()

        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    try:
                        data = json.loads(msg.data)
                    except json.JSONDecodeError:
                        await send_json({"t": "error", "msg": "noto'g'ri JSON"})
                        continue
                    if isinstance(data, dict):
                        await session.handle(data)
                elif msg.type == WSMsgType.BINARY:
                    # Ikkilik ma'lumot faqat manbadan keladi: telefon
                    # o'z ekranining kadrlarini shu yo'l bilan yuboradi.
                    await session.on_binary(msg.data)
                elif msg.type == WSMsgType.ERROR:
                    log.info("ws xatosi: %s", ws.exception())
        finally:
            await session.close()
        return ws

    # -- hayot sikli -------------------------------------------------------

    @property
    def scheme(self) -> str:
        return "http" if self.cfg.tls == "off" else "https"

    async def start(self) -> None:
        ssl_ctx = None
        if self.cfg.tls != "off":
            ssl_ctx = tls.context(config_dir())

        self._runner = web.AppRunner(self.app, access_log=None)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self.cfg.bind, self.cfg.port, ssl_context=ssl_ctx)
        await site.start()
        log.info("server tinglayapti: %s://%s:%s", self.scheme, self.cfg.bind, self.cfg.port)

        # Kompyuterning o'zi uchun alohida, oddiy HTTP eshigi.
        #
        # Sababi sertifikat: mahalliy tarmoq uchun u o'z-o'zini
        # imzolagan bo'lgani uchun brauzer har safar ogohlantiradi va
        # dastur "shubhali sayt" bo'lib ko'rinadi. 127.0.0.1 esa
        # brauzer uchun baribir xavfsiz manzil hisoblanadi - video
        # dekodlash (WebCodecs) u yerda HTTPS'siz ham ishlaydi.
        #
        # Faqat 127.0.0.1 ga bog'lanadi, tarmoqqa chiqmaydi. Kalit esa
        # baribir tekshiriladi.
        if ssl_ctx is not None:
            try:
                local = web.TCPSite(self._runner, "127.0.0.1", self.local_port)
                await local.start()
                log.info("mahalliy eshik: http://127.0.0.1:%s", self.local_port)
            except OSError as exc:
                # Bu qo'shimcha qulaylik - band bo'lsa dastur ishlayveradi
                log.warning("mahalliy eshik ochilmadi (%s): %s", self.local_port, exc)

    @property
    def local_port(self) -> int:
        """Kompyuterning o'zi uchun HTTP porti."""
        return self.cfg.local_port or self.cfg.port + 1

    async def stop(self) -> None:
        await self.ctx.shutdown()
        if self._runner:
            await self._runner.cleanup()
            self._runner = None
