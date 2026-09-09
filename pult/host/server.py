"""
The local server: serves the web page and accepts WebSocket connections.

This is "direct" mode - the phone connects straight to the computer.
Hub mode uses the very same HostContext; only the transport differs.
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
    """The web assets folder, found in a built .exe as well."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", ".")) / "web"
    return Path(__file__).resolve().parents[2] / "web"


def _authorized(request: web.Request, token: str) -> bool:
    """Checks the key.

    compare_digest is used on purpose: a plain == comparison leaks the
    key one character at a time through response timing.
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
        """Keep the web assets out of the cache.

        After an update the phone kept serving the old page from cache
        and the new features simply were not there - hard to diagnose,
        because nothing looks like an error from the outside. The files
        come over the local network, so caching buys almost nothing.
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
        self.app.router.add_get("/api/pair", self.pair_json_handler)
        self.app.router.add_get("/", self.index_handler)
        if root.is_dir():
            self.app.router.add_static("/", root, show_index=False)
        else:
            log.warning("web folder not found: %s", root)

    # -- HTTP --------------------------------------------------------------

    async def index_handler(self, request: web.Request) -> web.StreamResponse:
        index = web_root() / "index.html"
        if not index.is_file():
            return web.Response(text="web interface not found", status=500)
        return web.FileResponse(index, headers={"Cache-Control": "no-cache"})

    async def pair_handler(self, request: web.Request) -> web.StreamResponse:
        """The pairing page: QR code and link.

        Opened in the computer's own browser from the tray. The phone
        scans the QR code, so the key never has to be copied by hand.
        """
        if not _authorized(request, self.cfg.token):
            return web.Response(text="key required", status=401)
        urls = local_addresses(self.cfg.port, self.scheme)
        # When the tunnel is up its address goes into the QR code: a
        # phone that scanned it once can connect from any network. The
        # local addresses stay in the list - they are faster.
        if self.cfg.public_url:
            urls = [self.cfg.public_url.rstrip("/")] + urls
        target = f"{urls[0]}/#k={self.cfg.token}"
        html = render_pair_page(target, urls, self.cfg, tls.fingerprint(config_dir()))
        return web.Response(text=html, content_type="text/html")

    async def pair_json_handler(self, request: web.Request) -> web.StreamResponse:
        """QR code and link for pairing, shown inside the main window.

        With no phone connected yet the main window should not sit
        empty: the QR code appears right there. That way "where do I see
        my phone" and "how do I connect it" are answered in one place.
        """
        if not _authorized(request, self.cfg.token):
            return web.json_response({"error": "wrong key"}, status=401)

        from .pairing import app_link, qr_svg

        urls = local_addresses(self.cfg.port, self.scheme)
        if self.cfg.public_url:
            urls = [self.cfg.public_url.rstrip("/")] + urls
        target = f"{urls[0]}/#k={self.cfg.token}&h={self.cfg.host_id}"
        t = self.cfg.telegram
        return web.json_response({
            "svg": qr_svg(app_link(target)),
            "link": target,
            "telegram": bool(t.bot_token and t.chat_id),
        })

    async def pair_send_handler(self, request: web.Request) -> web.StreamResponse:
        """Sends the pairing link to Telegram.

        Scanning a QR code is not always convenient: the code may be
        small on screen, or the camera may refuse to focus. Telegram is
        already open on the phone - the link arrives in a second and
        handing it to the app through "Share" is enough.
        """
        if not _authorized(request, self.cfg.token):
            return web.json_response({"ok": False, "msg": "wrong key"}, status=401)

        t = self.cfg.telegram
        if not (t.bot_token and t.chat_id):
            return web.json_response({"ok": False, "msg": "Telegram is not set up"})

        from .. import notify

        base = self.cfg.public_url.rstrip("/") if self.cfg.public_url else \
            local_addresses(self.cfg.port, self.scheme)[0]
        link = f"{base}/#k={self.cfg.token}&h={self.cfg.host_id}"
        text = (
            f"<b>{self.cfg.host_name}</b> — pairing link\n\n"
            f"<code>{link}</code>\n\n"
            "Long-press the link → <b>Share</b> → <b>Pult</b>."
        )
        try:
            await notify.Telegram(t.bot_token, t.chat_id).send(text)
            return web.json_response({"ok": True})
        except Exception as exc:
            log.warning("pairing link not sent: %s", exc)
            return web.json_response({"ok": False, "msg": str(exc)[:120]})

    async def cert_handler(self, request: web.Request) -> web.StreamResponse:
        """Downloading the certificate.

        Installed on the phone, it stops the browser warning every time.
        Purely optional convenience.
        """
        path = config_dir() / tls.CERT_NAME
        if not path.is_file():
            return web.Response(text="no certificate", status=404)
        return web.FileResponse(
            path,
            headers={
                "Content-Type": "application/x-x509-ca-cert",
                "Content-Disposition": 'attachment; filename="pult.crt"',
            },
        )

    async def info_handler(self, request: web.Request) -> web.StreamResponse:
        """A short summary. Without a key only the name is visible.

        Leaving the name open is deliberate: it lets the phone tell
        which of the computers in its list are online without sending
        the key anywhere.
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
            log.warning("attempt with a wrong key: %s", request.remote)
            return web.json_response({"error": "wrong key"}, status=401)

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
                        await send_json({"t": "error", "msg": "invalid JSON"})
                        continue
                    if isinstance(data, dict):
                        await session.handle(data)
                elif msg.type == WSMsgType.BINARY:
                    # Binary data only ever comes from a source: this
                    # is how the phone sends its own screen frames.
                    await session.on_binary(msg.data)
                elif msg.type == WSMsgType.ERROR:
                    log.info("ws error: %s", ws.exception())
        finally:
            await session.close()
        return ws

    # -- lifecycle ---------------------------------------------------------

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
        log.info("server listening: %s://%s:%s", self.scheme, self.cfg.bind, self.cfg.port)

        # A separate, plain-HTTP door for the computer itself.
        #
        # The reason is the certificate: on the local network it is
        # self-signed, so the browser warns every time and the program
        # looks like a suspicious website. 127.0.0.1 counts as a secure
        # address anyway, so video decoding (WebCodecs) works there
        # without HTTPS.
        #
        # It binds to 127.0.0.1 only and never reaches the network. The
        # key is still checked.
        if ssl_ctx is not None:
            try:
                local = web.TCPSite(self._runner, "127.0.0.1", self.local_port)
                await local.start()
                log.info("local door: http://127.0.0.1:%s", self.local_port)
            except OSError as exc:
                # A convenience only - if the port is taken, carry on
                log.warning("local door not opened (%s): %s", self.local_port, exc)

    @property
    def local_port(self) -> int:
        """The HTTP port for the computer itself."""
        return self.cfg.local_port or self.cfg.port + 1

    async def stop(self) -> None:
        await self.ctx.shutdown()
        if self._runner:
            await self._runner.cleanup()
            self._runner = None
