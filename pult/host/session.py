"""
Boshqaruv sessiyasi va host konteksti.

Bu yerda transport yo'q: kim ulangani (telefon brauzeri, hub orqali
kelgan ulanish yoki AI agent) ahamiyatsiz. Hammasi bir xil xabarlarni
yuboradi va bir xil javob oladi. Shuning uchun AI qo'shish alohida
kod yozishni talab qilmaydi - u shunchaki yana bitta sessiya.
"""
from __future__ import annotations

import asyncio
import logging
import struct
import time
from typing import Any, Awaitable, Callable

from ..capture import CaptureConfig, ScreenCapture
from ..config import Config
from ..platform import ffmpeg as ff
from ..platform import system_name

log = logging.getLogger("pult.session")

PROTOCOL_VERSION = 1

# Ikkilik kadr sarlavhasi: tur(1) + bayroqlar(1) + zaxira(2) + vaqt(4)
_BIN_HEADER = struct.Struct("!BBHI")
BIN_VIDEO = 1

# Sekin tarmoqda navbat cheksiz o'smasligi uchun chegara. Oshib ketsa
# navbat tozalanadi va keyingi kalit kadrgacha hech narsa yuborilmaydi -
# rasm bir lahza qotadi, lekin kechikish to'planib ketmaydi.
MAX_QUEUED_FRAMES = 60


def pack_video(au: bytes, key: bool, ts_ms: int) -> bytes:
    return _BIN_HEADER.pack(BIN_VIDEO, 1 if key else 0, 0, ts_ms & 0xFFFFFFFF) + au


class ControllerSession:
    """Bitta ulangan boshqaruvchi."""

    _counter = 0

    def __init__(
        self,
        ctx: "HostContext",
        send_json: Callable[[dict], Awaitable[None]],
        send_binary: Callable[[bytes], Awaitable[None]],
        peer: str = "",
    ) -> None:
        ControllerSession._counter += 1
        self.id = ControllerSession._counter
        self.ctx = ctx
        self.send_json = send_json
        self.send_binary = send_binary
        self.peer = peer
        self.name = "boshqaruvchi"
        self.role = "controller"
        self.viewing = False
        self.connected_at = time.monotonic()

        self._queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=MAX_QUEUED_FRAMES)
        self._need_key = True
        self._writer: asyncio.Task | None = None
        self._closed = False

        # Bosib turilgan klavishlar. Alt+Tab kabi imo-ishoralarda klavish
        # bir xabarda bosilib, boshqasida qo'yiladi. Agar orada aloqa
        # uzilsa, klavish kompyuterda bosilgan holda qolib ketardi -
        # Alt bosilib qolgan kompyuterni ishlatib bo'lmaydi. Shuning
        # uchun sessiya yopilganda hammasini qo'yib yuboramiz.
        self._held_keys: set[str] = set()

    # -- hayot sikli -------------------------------------------------------

    async def start(self) -> None:
        self._writer = asyncio.create_task(self._write_loop(), name=f"pult-tx-{self.id}")
        await self.send_json(self.ctx.hello_message())

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.release_keys()
        if self._writer:
            self._writer.cancel()
        await self.ctx.detach(self)

    def release_keys(self) -> None:
        """Bu sessiya bosib qo'ygan klavishlarni qo'yib yuboradi."""
        for name in list(self._held_keys):
            try:
                self.ctx.input.key(name, "up")
            except Exception:
                pass
        if self._held_keys:
            log.info("sessiya %s: bosilgan klavishlar qo'yildi: %s",
                     self.id, ", ".join(sorted(self._held_keys)))
        self._held_keys.clear()

    async def _write_loop(self) -> None:
        try:
            while True:
                data = await self._queue.get()
                await self.send_binary(data)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.info("sessiya %s uzildi: %s", self.id, exc)
            self._closed = True

    # -- video -------------------------------------------------------------

    def offer_unit(self, au: bytes, key: bool, ts_ms: int) -> None:
        """Kadrni navbatga qo'yadi. Navbat to'lgan bo'lsa tashlab yuboradi."""
        if not self.viewing or self._closed:
            return
        if self._need_key:
            if not key:
                return
            self._need_key = False
        try:
            self._queue.put_nowait(pack_video(au, key, ts_ms))
        except asyncio.QueueFull:
            # Tarmoq yetishmayapti: to'plangan kechikishni tashlaymiz
            while not self._queue.empty():
                try:
                    self._queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
            self._need_key = True
            log.debug("sessiya %s: navbat to'ldi, kalit kadr kutilmoqda", self.id)

    async def send_catch_up(self) -> None:
        """Ulangan zahoti oxirgi kalit kadrdan boshlab yuboradi."""
        units = self.ctx.capture.catch_up()
        if not units:
            return
        self._need_key = False
        ts = int(time.monotonic() * 1000)
        for i, au in enumerate(units):
            try:
                self._queue.put_nowait(pack_video(au, i == 0, ts))
            except asyncio.QueueFull:
                break

    # -- kiruvchi xabarlar -------------------------------------------------

    async def handle(self, msg: dict) -> None:
        kind = msg.get("t")
        handler = getattr(self, f"_on_{kind}", None)
        if handler is None:
            await self.send_json({"t": "error", "msg": f"noma'lum xabar: {kind}"})
            return
        try:
            await handler(msg)
        except (ValueError, KeyError, PermissionError, TypeError) as exc:
            # Mijozning noto'g'ri xabari - bu bizning nosozligimiz emas,
            # shuning uchun stek izisiz, qisqa yozamiz.
            log.warning("rad etildi (%s): %s", kind, exc)
            await self.send_json({"t": "error", "msg": str(exc)})
        except Exception as exc:
            log.exception("xabarni qayta ishlashda kutilmagan xato: %s", msg)
            await self.send_json({"t": "error", "msg": str(exc)})

    async def _on_hello(self, msg: dict) -> None:
        self.name = str(msg.get("name") or self.name)[:64]
        self.role = str(msg.get("role") or "controller")[:32]
        log.info("ulandi: %s (%s) %s", self.name, self.role, self.peer)

    async def _on_ping(self, msg: dict) -> None:
        await self.send_json({"t": "pong", "id": msg.get("id"), "ts": msg.get("ts")})

    async def _on_view(self, msg: dict) -> None:
        """Oqimni yoqish/o'chirish va sozlamalarini o'zgartirish."""
        want = bool(msg.get("on", True))
        s = self.ctx.cfg.stream
        if "monitor" in msg:
            s.monitor = int(msg["monitor"])
        if "fps" in msg:
            s.fps = max(1, min(60, int(msg["fps"])))
        if "width" in msg:
            s.width = max(0, min(3840, int(msg["width"])))
        if "bitrate" in msg:
            s.bitrate_kbps = max(200, min(50000, int(msg["bitrate"])))
        if "cursor" in msg:
            s.cursor = bool(msg["cursor"])

        was = self.viewing
        self.viewing = want
        await self.ctx.sync_capture(changed=(want and was))
        if want:
            # Kodek satri aniqlanmaguncha kutamiz: brauzer dekoderni
            # aynan shu satr bilan sozlaydi, undan oldin yuborilgan
            # kadrlar behuda ketadi.
            await self.ctx.capture.wait_codec()
            await self.send_json(self.ctx.stream_message())
            await self.send_catch_up()

    # -- kiritish ----------------------------------------------------------

    def _to_desktop(self, x: float, y: float) -> tuple[int, int]:
        """Normallashtirilgan (0..1) koordinatani ish stoli piksellariga."""
        mon = self.ctx.monitor()
        px = mon["x"] + x * (mon["w"] - 1)
        py = mon["y"] + y * (mon["h"] - 1)
        return int(round(px)), int(round(py))

    def _check_input(self) -> None:
        if not self.ctx.cfg.security.allow_input:
            raise PermissionError("kiritish sozlamalarda o'chirilgan")

    async def _on_mouse(self, msg: dict) -> None:
        self._check_input()
        wi = self.ctx.input
        action = msg.get("a", "move")
        if action == "move":
            wi.move_to(*self._to_desktop(float(msg["x"]), float(msg["y"])))
        elif action == "moveby":
            wi.move_by(float(msg.get("dx", 0)), float(msg.get("dy", 0)))
        elif action in ("down", "up", "click", "dblclick"):
            pos = None
            if "x" in msg and "y" in msg:
                pos = self._to_desktop(float(msg["x"]), float(msg["y"]))
            button = str(msg.get("b", "left"))
            if action == "dblclick":
                wi.button("click", button, *(pos or (None, None)))
                wi.button("click", button)
            else:
                wi.button(action, button, *(pos or (None, None)))
        else:
            raise ValueError(f"noma'lum sichqoncha amali: {action}")

    async def _on_scroll(self, msg: dict) -> None:
        self._check_input()
        self.ctx.input.scroll(float(msg.get("dy", 0)), float(msg.get("dx", 0)))

    async def _on_key(self, msg: dict) -> None:
        self._check_input()
        name = str(msg["k"])
        action = str(msg.get("a", "tap"))
        self.ctx.input.key(name, action)
        if action == "down":
            self._held_keys.add(name)
        elif action == "up":
            self._held_keys.discard(name)

    async def _on_release_keys(self, msg: dict) -> None:
        """Mijoz o'zi so'rasa ham qo'yib yuboramiz (ilova fonga o'tganda)."""
        self.release_keys()

    async def _on_combo(self, msg: dict) -> None:
        self._check_input()
        keys = msg.get("keys") or []
        if not isinstance(keys, list) or not keys:
            raise ValueError("combo uchun keys ro'yxati kerak")
        self.ctx.input.combo([str(k) for k in keys])

    async def _on_text(self, msg: dict) -> None:
        self._check_input()
        text = str(msg.get("s", ""))
        if text:
            self.ctx.input.type_text(text)

    async def _on_cmd(self, msg: dict) -> None:
        if not self.ctx.cfg.security.allow_commands:
            raise PermissionError("tizim buyruqlari sozlamalarda o'chirilgan")
        name = str(msg.get("name", ""))
        result = self.ctx.run_command(name, msg)
        await self.send_json({"t": "cmd_ok", "name": name, "result": result})


class HostContext:
    """Kompyuterning umumiy holati: ekran olish, kiritish, ulanganlar."""

    def __init__(self, cfg: Config, caps: ff.Capabilities) -> None:
        from ..platform import input_backend

        self.cfg = cfg
        self.caps = caps
        self.input = input_backend()
        self.monitors = self.input.list_monitors()
        self.sessions: set[ControllerSession] = set()
        self.started_at = time.time()

        self.capture = ScreenCapture(caps, self._on_unit, monitors=self.monitors)
        self._stats_task: asyncio.Task | None = None

        self._system = None
        if system_name() == "Windows":
            from ..platform import win_system

            self._system = win_system

    # -- ma'lumot ----------------------------------------------------------

    def monitor(self) -> dict:
        idx = self.cfg.stream.monitor
        for m in self.monitors:
            if m["index"] == idx:
                return m
        return self.monitors[0] if self.monitors else {"x": 0, "y": 0, "w": 1920, "h": 1080}

    def hello_message(self) -> dict:
        return {
            "t": "hello",
            "ver": PROTOCOL_VERSION,
            "host": {
                "id": self.cfg.host_id,
                "name": self.cfg.host_name,
                "os": system_name(),
                "monitors": self.monitors,
                "encoders": sorted(e for e in self.caps.encoders if "264" in e),
                "commands": sorted(self._system.COMMANDS) if self._system else [],
                "uptime": int(time.time() - self.started_at),
            },
            "stream": {
                "monitor": self.cfg.stream.monitor,
                "fps": self.cfg.stream.fps,
                "width": self.cfg.stream.width,
                "bitrate": self.cfg.stream.bitrate_kbps,
                "cursor": self.cfg.stream.cursor,
            },
        }

    def stream_message(self) -> dict:
        return {
            "t": "stream",
            "codec": self.capture.codec,
            "w": self.capture.width,
            "h": self.capture.height,
            "fps": self.cfg.stream.fps,
            "encoder": self.capture.encoder_label,
            "monitor": self.cfg.stream.monitor,
        }

    def _capture_config(self) -> CaptureConfig:
        s = self.cfg.stream
        return CaptureConfig(
            monitor=s.monitor,
            fps=s.fps,
            scale_width=s.width,
            bitrate_kbps=s.bitrate_kbps,
            draw_cursor=s.cursor,
            encoder=s.encoder,
        )

    # -- ulanishlar --------------------------------------------------------

    async def attach(self, session: ControllerSession) -> None:
        self.sessions.add(session)
        if self._stats_task is None:
            self._stats_task = asyncio.create_task(self._stats_loop(), name="pult-stats")

    async def detach(self, session: ControllerSession) -> None:
        self.sessions.discard(session)
        await self.sync_capture()
        if not self.sessions and self._stats_task:
            self._stats_task.cancel()
            self._stats_task = None

    async def sync_capture(self, changed: bool = False) -> None:
        """Tomoshabin bor-yo'qligiga qarab ekran olishni yoqadi yoki o'chiradi.

        Hech kim qaramayotganda ffmpeg umuman ishlamaydi - kompyuter bo'sh
        turganda dastur resurs yemasligi shundan.
        """
        want = any(s.viewing for s in self.sessions)
        if want and not self.capture.running:
            await self.capture.start(self._capture_config())
        elif want and changed:
            await self.capture.apply(self._capture_config())
            # Sozlama o'zgarsa oqim qayta boshlanadi va kodek satri ham
            # o'zgarishi mumkin. Buni o'zgartirishni so'ramagan boshqa
            # tomoshabinlar ham bilishi kerak.
            asyncio.create_task(self._announce_stream(), name="pult-announce")
        elif not want and self.capture.running:
            await self.capture.stop()

    async def _announce_stream(self) -> None:
        await self.capture.wait_codec()
        msg = self.stream_message()
        for s in list(self.sessions):
            if not s.viewing:
                continue
            try:
                await s.send_json(msg)
                await s.send_catch_up()
            except Exception:
                pass

    def _on_unit(self, au: bytes, key: bool) -> None:
        ts = int(time.monotonic() * 1000)
        for s in list(self.sessions):
            s.offer_unit(au, key, ts)

    async def _stats_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(1.0)
                if not self.sessions:
                    continue
                msg = {
                    "t": "stats",
                    "fps": round(self.capture.stats.fps, 1),
                    "kbps": round(self.capture.stats.kbps),
                    "frames": self.capture.stats.frames,
                    "viewers": sum(1 for s in self.sessions if s.viewing),
                    "clients": len(self.sessions),
                }
                for s in list(self.sessions):
                    try:
                        await s.send_json(msg)
                    except Exception:
                        pass
        except asyncio.CancelledError:
            raise

    # -- buyruqlar ---------------------------------------------------------

    def run_command(self, name: str, msg: dict) -> str:
        if self._system is None:
            raise RuntimeError("bu tizimda buyruqlar qo'llab-quvvatlanmaydi")
        if name == "run":
            command = str(msg.get("command", "")).strip()
            if not command:
                raise ValueError("command bo'sh")
            self._system.run_program(command)
            return f"ishga tushirildi: {command}"
        fn = self._system.COMMANDS.get(name)
        if fn is None:
            raise ValueError(f"noma'lum buyruq: {name}")
        fn()
        return "bajarildi"

    async def shutdown(self) -> None:
        if self._stats_task:
            self._stats_task.cancel()
            self._stats_task = None
        await self.capture.stop()
