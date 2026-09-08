"""
Ekranni olish va H.264 ga kodlash.

ffmpeg alohida jarayon sifatida ishlaydi va chiqishini quvurga (pipe)
yozadi. Biz uni Annex-B oqimidan "kirish birliklari"ga (access unit =
bitta kadr) ajratamiz va shu holicha brauzerga uzatamiz - brauzerdagi
WebCodecs dekoderi aynan shu ko'rinishni kutadi.
"""
from __future__ import annotations

import asyncio
import logging
import platform
import subprocess
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from .platform import ffmpeg as ff

log = logging.getLogger("pult.capture")

# AUD (Access Unit Delimiter) NAL - har bir kadr shu bilan boshlanadi.
# 4 baytli boshlanish kodi (00 00 00 01 09) ham shu naqshni o'z ichiga
# oladi, shuning uchun bittasini qidirish yetarli.
_AUD = b"\x00\x00\x01\x09"
_START = b"\x00\x00\x01"


@dataclass
class CaptureConfig:
    monitor: int = 0
    fps: int = 30
    scale_width: int = 0        # 0 = ekranning o'z kengligi
    bitrate_kbps: int = 4000
    draw_cursor: bool = True
    gop_seconds: float = 2.0
    encoder: str | None = None  # None = avtomatik tanlash

    def key(self) -> tuple:
        """Qayta ishga tushirish kerakmi yo'qmi - shuni solishtirish uchun."""
        return (
            self.monitor, self.fps, self.scale_width, self.bitrate_kbps,
            self.draw_cursor, self.gop_seconds, self.encoder,
        )


def codec_string(au: bytes) -> str | None:
    """SPS'dan brauzer uchun kodek satrini yasaydi, masalan "avc1.4D4028".

    Buni qo'lda yozib qo'ymaymiz: profil va daraja kodlagich va ekran
    o'lchamiga qarab o'zgaradi, noto'g'ri satr esa dekoderni ishga
    tushirmaydi. SPS'ning o'zidan olganimiz doim to'g'ri bo'ladi.
    """
    i = 0
    n = len(au)
    while True:
        j = au.find(_START, i)
        if j < 0 or j + 7 > n:
            return None
        k = j + 3
        if (au[k] & 0x1F) == 7:  # SPS
            return "avc1.%02X%02X%02X" % (au[k + 1], au[k + 2], au[k + 3])
        i = k


def is_keyframe(au: bytes) -> bool:
    """Kadr IDR (kalit kadr) ekanini aniqlaydi."""
    i = 0
    n = len(au)
    while True:
        j = au.find(_START, i)
        if j < 0 or j + 4 > n:
            return False
        k = j + 3
        t = au[k] & 0x1F
        if t == 5:      # IDR bo'lagi
            return True
        if t == 1:      # oddiy bo'lak - demak kalit kadr emas
            return False
        i = k


class H264Splitter:
    """Bayt oqimini to'liq kadrlarga ajratadi."""

    def __init__(self) -> None:
        self._buf = bytearray()

    def feed(self, chunk: bytes) -> list[bytes]:
        self._buf.extend(chunk)
        buf = self._buf

        # Buferdagi barcha kadr chegaralarini topamiz
        offsets: list[int] = []
        i = buf.find(_AUD)
        while i >= 0:
            # 4 baytli boshlanish kodi bo'lsa bir bayt orqaga suramiz
            start = i - 1 if i > 0 and buf[i - 1] == 0 else i
            offsets.append(start)
            i = buf.find(_AUD, i + 4)

        if len(offsets) < 2:
            return []

        units = [bytes(buf[offsets[k]:offsets[k + 1]]) for k in range(len(offsets) - 1)]
        del buf[:offsets[-1]]
        return units


@dataclass
class CaptureStats:
    frames: int = 0
    keyframes: int = 0
    bytes_out: int = 0
    started_at: float = 0.0
    last_frame_at: float = 0.0
    fps: float = 0.0
    kbps: float = 0.0
    _win: list = field(default_factory=list)

    def note(self, size: int, key: bool) -> None:
        now = time.monotonic()
        self.frames += 1
        self.bytes_out += size
        self.last_frame_at = now
        if key:
            self.keyframes += 1
        self._win.append((now, size))
        cutoff = now - 2.0
        while self._win and self._win[0][0] < cutoff:
            self._win.pop(0)
        if len(self._win) >= 2:
            span = self._win[-1][0] - self._win[0][0]
            if span > 0:
                self.fps = (len(self._win) - 1) / span
                self.kbps = sum(s for _, s in self._win[1:]) * 8 / span / 1000


class ScreenCapture:
    """ffmpeg jarayonini boshqaradi va kadrlarni qayta chaqiruvga uzatadi.

    Muhim xususiyat: hech kim tomosha qilmayotganda umuman ishlamaydi.
    Birinchi tomoshabin ulanganda ishga tushadi (va darrov kalit kadr
    beradi), oxirgisi uzilganda to'xtaydi - shunda kompyuter bo'sh
    turganda protsessor ham, videokarta ham tegilmaydi.
    """

    def __init__(
        self,
        caps: ff.Capabilities,
        on_unit: Callable[[bytes, bool], Awaitable[None] | None],
        monitors: list[dict] | None = None,
    ) -> None:
        self.caps = caps
        self.on_unit = on_unit
        self.monitors = monitors or []
        self.cfg = CaptureConfig()
        self.stats = CaptureStats()
        self.codec: str | None = None
        self.width = 0
        self.height = 0
        self.encoder_label = ""

        self._proc: asyncio.subprocess.Process | None = None
        self._tasks: list[asyncio.Task] = []
        self._splitter = H264Splitter()
        self._gop: list[bytes] = []      # oxirgi kalit kadrdan beri kelgan kadrlar
        self._lock = asyncio.Lock()
        self._stderr_tail: list[str] = []

        # Kodek satri faqat birinchi kalit kadr kelganda ma'lum bo'ladi
        # (u SPS ichidan o'qiladi). Tomoshabinga undan oldin xabar
        # yuborsak, brauzer dekoderni sozlay olmaydi - shuning uchun
        # kutish uchun alohida bayroq.
        self.codec_ready = asyncio.Event()

    # -- ffmpeg buyrug'i ---------------------------------------------------

    def _source_args(self, cfg: CaptureConfig) -> tuple[list[str], list[str]]:
        """(kirish argumentlari, filtr zanjirining boshi) qaytaradi."""
        system = platform.system()
        mon = None
        for m in self.monitors:
            if m.get("index") == cfg.monitor:
                mon = m
                break
        if mon is None and self.monitors:
            mon = self.monitors[0]

        if system == "Windows" and "ddagrab" in self.caps.filters:
            # Desktop Duplication API - ekran GPU xotirasida olinadi
            src = (
                f"ddagrab=output_idx={cfg.monitor}"
                f":framerate={cfg.fps}"
                f":draw_mouse={1 if cfg.draw_cursor else 0}"
            )
            return ["-f", "lavfi", "-i", src], ["hwdownload", "format=bgra"]

        if system == "Windows":
            args = ["-f", "gdigrab", "-framerate", str(cfg.fps),
                    "-draw_mouse", "1" if cfg.draw_cursor else "0"]
            if mon:
                args += ["-offset_x", str(mon["x"]), "-offset_y", str(mon["y"]),
                         "-video_size", f"{mon['w']}x{mon['h']}"]
            args += ["-i", "desktop"]
            return args, []

        if system == "Linux":
            disp = ":0.0"
            off = f"+{mon['x']},{mon['y']}" if mon else ""
            args = ["-f", "x11grab", "-framerate", str(cfg.fps),
                    "-draw_mouse", "1" if cfg.draw_cursor else "0"]
            if mon:
                args += ["-video_size", f"{mon['w']}x{mon['h']}"]
            args += ["-i", f"{disp}{off}"]
            return args, []

        if system == "Darwin":
            args = ["-f", "avfoundation", "-framerate", str(cfg.fps),
                    "-capture_cursor", "1" if cfg.draw_cursor else "0",
                    "-i", f"{cfg.monitor}:none"]
            return args, []

        raise RuntimeError(f"bu tizim uchun ekran olish yo'li yo'q: {system}")

    def build_command(self, cfg: CaptureConfig) -> list[str]:
        enc = ff.pick_encoder(self.caps, cfg.encoder)
        self.encoder_label = enc["label"]

        input_args, chain = self._source_args(cfg)

        mon = next((m for m in self.monitors if m.get("index") == cfg.monitor), None)
        src_w = mon["w"] if mon else 1920
        src_h = mon["h"] if mon else 1080

        if cfg.scale_width and cfg.scale_width < src_w:
            # Juft songa yaxlitlaymiz: H.264 toq o'lchamni qabul qilmaydi
            w = cfg.scale_width - (cfg.scale_width % 2)
            h = int(round(src_h * w / src_w))
            h -= h % 2
            # fast_bilinear - sifat farqi ko'zga tashlanmaydi, protsessor esa
            # sezilarli darajada kam ishlaydi
            chain.append(f"scale={w}:{h}:flags=fast_bilinear")
            self.width, self.height = w, h
        else:
            self.width, self.height = src_w, src_h

        chain.append(f"format={enc['pix_fmt']}")

        gop = max(int(cfg.fps * cfg.gop_seconds), 1)

        cmd = [self.caps.path, "-hide_banner", "-loglevel", "error", "-nostdin"]
        cmd += input_args
        cmd += ["-vf", ",".join(chain)]
        cmd += enc["args"]
        cmd += ff.rate_args(enc, cfg.bitrate_kbps, gop)
        if self.caps.has_aud_bsf:
            cmd += ["-bsf:v", "h264_metadata=aud=insert"]
        cmd += ["-f", "h264", "-flush_packets", "1", "pipe:1"]
        return cmd

    # -- hayot sikli -------------------------------------------------------

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    async def start(self, cfg: CaptureConfig | None = None) -> None:
        async with self._lock:
            if cfg is not None:
                self.cfg = cfg
            if self.running:
                return
            await self._spawn()

    async def apply(self, cfg: CaptureConfig) -> None:
        """Sozlamalarni o'zgartiradi; kerak bo'lsa qayta ishga tushiradi."""
        async with self._lock:
            same = cfg.key() == self.cfg.key()
            self.cfg = cfg
            if same or not self.running:
                return
            await self._kill()
            await self._spawn()

    async def stop(self) -> None:
        async with self._lock:
            await self._kill()

    async def _spawn(self) -> None:
        cmd = self.build_command(self.cfg)
        log.info("ekran olish boshlandi: %s", " ".join(cmd))

        flags = 0
        if hasattr(subprocess, "CREATE_NO_WINDOW"):
            flags = subprocess.CREATE_NO_WINDOW

        self._proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.DEVNULL,
            creationflags=flags,
        )
        self._splitter = H264Splitter()
        self._gop = []
        self.codec = None
        self.codec_ready.clear()
        self.stats = CaptureStats(started_at=time.monotonic())
        self._stderr_tail = []
        self._tasks = [
            asyncio.create_task(self._read_stdout(), name="pult-capture-stdout"),
            asyncio.create_task(self._read_stderr(), name="pult-capture-stderr"),
        ]

    async def _kill(self) -> None:
        for t in self._tasks:
            t.cancel()
        self._tasks = []
        proc = self._proc
        self._proc = None
        if proc and proc.returncode is None:
            try:
                proc.terminate()
                await asyncio.wait_for(proc.wait(), timeout=3)
            except (asyncio.TimeoutError, ProcessLookupError):
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
        log.info("ekran olish to'xtadi")

    async def _read_stdout(self) -> None:
        assert self._proc and self._proc.stdout
        stream = self._proc.stdout
        try:
            while True:
                chunk = await stream.read(65536)
                if not chunk:
                    break
                for au in self._splitter.feed(chunk):
                    key = is_keyframe(au)
                    if key:
                        self._gop = [au]
                        if self.codec is None:
                            self.codec = codec_string(au)
                            if self.codec:
                                self.codec_ready.set()
                    else:
                        # Bufer cheksiz o'smasligi uchun chegara qo'yamiz
                        if len(self._gop) < self.cfg.fps * 4:
                            self._gop.append(au)
                    self.stats.note(len(au), key)
                    res = self.on_unit(au, key)
                    if asyncio.iscoroutine(res):
                        await res
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("kadr o'qishda xato")
        finally:
            if self._proc and self._proc.returncode not in (None, 0):
                log.error("ffmpeg tugadi (kod %s): %s",
                          self._proc.returncode, " | ".join(self._stderr_tail[-5:]))

    async def _read_stderr(self) -> None:
        assert self._proc and self._proc.stderr
        stream = self._proc.stderr
        try:
            while True:
                line = await stream.readline()
                if not line:
                    break
                text = line.decode("utf-8", "replace").strip()
                if text:
                    self._stderr_tail.append(text)
                    del self._stderr_tail[:-20]
                    log.warning("ffmpeg: %s", text)
        except asyncio.CancelledError:
            raise
        except Exception:
            pass

    async def wait_codec(self, timeout: float = 5.0) -> str | None:
        """Kodek satri ma'lum bo'lguncha kutadi (birinchi kalit kadrgacha)."""
        try:
            await asyncio.wait_for(self.codec_ready.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            log.warning("kodek satri %.0f soniyada aniqlanmadi", timeout)
        return self.codec

    def catch_up(self) -> list[bytes]:
        """Yangi ulangan tomoshabinga yuboriladigan kadrlar.

        Oxirgi kalit kadrdan hozirgacha bo'lgan hammasi. Shu tufayli yangi
        tomoshabin keyingi kalit kadrni kutmaydi - rasm darhol paydo bo'ladi.
        """
        return list(self._gop)
