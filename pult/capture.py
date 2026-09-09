"""
Screen capture and H.264 encoding.

ffmpeg runs as a separate process and writes its output to a pipe. We
split that Annex-B stream into access units (one access unit = one
frame) and pass them on to the browser as they are - the browser's
WebCodecs decoder expects exactly that shape.
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

# The AUD (Access Unit Delimiter) NAL - every frame starts with one.
# The four-byte start code (00 00 00 01 09) contains this pattern too,
# so searching for the one pattern is enough.
_AUD = b"\x00\x00\x01\x09"
_START = b"\x00\x00\x01"


@dataclass
class CaptureConfig:
    monitor: int = 0
    # The index of the capture source. -1 means the same as monitor.
    # It is separate because the order of the graphics card's outputs
    # does not always match the order of the system's screens.
    source: int = -1
    fps: int = 30
    scale_width: int = 0        # 0 = the screen's own width
    bitrate_kbps: int = 4000
    draw_cursor: bool = True
    gop_seconds: float = 2.0
    encoder: str | None = None  # None = pick automatically

    def key(self) -> tuple:
        """Compared to decide whether a restart is needed."""
        return (
            self.monitor, self.source, self.fps, self.scale_width,
            self.bitrate_kbps, self.draw_cursor, self.gop_seconds, self.encoder,
        )


def codec_string(au: bytes) -> str | None:
    """Builds the browser's codec string from the SPS, e.g. "avc1.4D4028".

    It is not hard-coded: the profile and level change with the encoder
    and the screen size, and a wrong string simply will not start the
    decoder. Taken from the SPS itself, it is always right.
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
    """Tells whether the frame is an IDR (a key frame)."""
    i = 0
    n = len(au)
    while True:
        j = au.find(_START, i)
        if j < 0 or j + 4 > n:
            return False
        k = j + 3
        t = au[k] & 0x1F
        if t == 5:      # an IDR slice
            return True
        if t == 1:      # a plain slice - so not a key frame
            return False
        i = k


class H264Splitter:
    """Splits a byte stream into whole frames."""

    def __init__(self) -> None:
        self._buf = bytearray()

    def feed(self, chunk: bytes) -> list[bytes]:
        self._buf.extend(chunk)
        buf = self._buf

        # Find every frame boundary in the buffer
        offsets: list[int] = []
        i = buf.find(_AUD)
        while i >= 0:
            # Step back one byte for a four-byte start code
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
    """Drives the ffmpeg process and hands frames to a callback.

    The important part: with nobody watching it does not run at all. It
    starts when the first viewer connects (and gives a key frame right
    away) and stops when the last one leaves, so an idle computer costs
    neither CPU nor GPU.
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
        self._gop: list[bytes] = []      # frames since the last key frame
        self._lock = asyncio.Lock()
        self._stderr_tail: list[str] = []
        self._last_spawn = 0.0
        # ddagrab only gives its first frame when the screen CHANGES.
        # With a still desktop (a full-screen game in front, say) it can
        # wait forever. Then we fall back to gdigrab: slower and heavier
        # on the CPU, but it runs off a timer and always produces
        # frames.
        self._no_dda = False
        # Called once the codec string is known. Pushing is safer than
        # waiting: however late capture starts, the viewer eventually
        # gets the right message.
        self.on_codec = None

        # The codec string is only known once the first key frame
        # arrives (it is read out of the SPS). Told any earlier, the
        # browser cannot configure its decoder - hence a separate flag
        # to wait on.
        self.codec_ready = asyncio.Event()

    # -- the ffmpeg command ------------------------------------------------

    def _source_args(self, cfg: CaptureConfig) -> tuple[list[str], list[str]]:
        """Returns (input arguments, the head of the filter chain)."""
        system = platform.system()
        source = cfg.source if cfg.source >= 0 else cfg.monitor
        mon = None
        for m in self.monitors:
            if m.get("index") == cfg.monitor:
                mon = m
                break
        if mon is None and self.monitors:
            mon = self.monitors[0]

        if system == "Windows" and "ddagrab" in self.caps.filters and not self._no_dda:
            # Desktop Duplication API - captured in GPU memory
            src = (
                f"ddagrab=output_idx={source}"
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

        raise RuntimeError(f"no capture path for this system: {system}")

    def build_command(self, cfg: CaptureConfig) -> list[str]:
        enc = ff.pick_encoder(self.caps, cfg.encoder)
        self.encoder_label = enc["label"]

        input_args, chain = self._source_args(cfg)

        mon = next((m for m in self.monitors if m.get("index") == cfg.monitor), None)
        src_w = mon["w"] if mon else 1920
        src_h = mon["h"] if mon else 1080

        if cfg.scale_width and cfg.scale_width < src_w:
            # Round to an even number: H.264 refuses odd dimensions
            w = cfg.scale_width - (cfg.scale_width % 2)
            h = int(round(src_h * w / src_w))
            h -= h % 2
            # fast_bilinear - the quality difference is invisible, and
            # the CPU does noticeably less work
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

    # -- lifecycle ---------------------------------------------------------

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
        """Changes the settings, restarting if that is needed."""
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
        # Do not restart in quick succession. Switching screens can
        # call this several times in a row, and neither the encoder nor
        # the screen grabber has let go of the previous instance yet -
        # ffmpeg then stays alive but produces nothing.
        gap = time.monotonic() - self._last_spawn
        if gap < 0.6:
            await asyncio.sleep(0.6 - gap)
        self._last_spawn = time.monotonic()
        self._codec_retried = False

        cmd = self.build_command(self.cfg)
        log.info("capture started: %s", " ".join(cmd))

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
            asyncio.create_task(self._watchdog(), name="pult-capture-watchdog"),
        ]

    async def _watchdog(self) -> None:
        """Switches capture method when no frames arrive.

        The task is cancelled in _kill(), so there is no risk of it
        starting capture again after the viewer has left.
        """
        # In a healthy state the first frame arrives within a second.
        # Waiting longer buys nothing: it only keeps the user in front
        # of a black screen, and gdigrab works regardless - it simply
        # costs more CPU.
        await asyncio.sleep(2.5)
        if self.stats.bytes_out or self._no_dda or self._proc is None:
            return
        log.warning("ddagrab gave no frame in 2.5s - falling back to gdigrab "
                    "(the desktop may simply be still)")
        self._no_dda = True
        async with self._lock:
            if self._proc is not None:
                await self._kill()
                await self._spawn()

    async def _kill(self) -> None:
        # Do not cancel the current task: the watchdog is on this list
        # and calls _kill() itself - cancelling itself would cut it off
        # before the next line.
        current = asyncio.current_task()
        for t in self._tasks:
            if t is not current:
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
        log.info("capture stopped")

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
                                # Tell any waiting viewers. The codec
                                # can arrive late (see wait_codec
                                # below), in which case they already
                                # got a message with an empty codec.
                                if self.on_codec:
                                    res = self.on_codec()
                                    if asyncio.iscoroutine(res):
                                        await res
                    else:
                        # A cap so the buffer cannot grow without bound
                        if len(self._gop) < self.cfg.fps * 4:
                            self._gop.append(au)
                    self.stats.note(len(au), key)
                    res = self.on_unit(au, key)
                    if asyncio.iscoroutine(res):
                        await res
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("error while reading frames")
        finally:
            if self._proc and self._proc.returncode not in (None, 0):
                log.error("ffmpeg exited (code %s): %s",
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

    async def wait_codec(self, timeout: float = 12.0) -> str | None:
        """Waits until the codec string is known (the first key frame).

        The timeout is deliberately long. The reason is how the Desktop
        Duplication API behaves: it gives its first frame when the screen
        CHANGES. With a still desktop (a full-screen game in front, for
        instance) the first frame is several seconds late - measured
        between 2.7 and 6 seconds.

        Not getting it is not fatal either: the moment the codec is
        known, on_codec tells the viewers again.
        """
        try:
            await asyncio.wait_for(self.codec_ready.wait(), timeout=timeout)
            return self.codec
        except asyncio.TimeoutError:
            pass

        # Write down the reason: this failure used to be entirely
        # silent, and the log said nothing about what happened
        log.warning("codec string not found in %.0fs (%d bytes arrived)%s",
                    timeout, self.stats.bytes_out,
                    "; ffmpeg: " + " | ".join(self._stderr_tail[-3:])
                    if self._stderr_tail else "")

        # No restart: capture is running, and as soon as a frame comes
        # in, on_codec tells the viewer. Restarting would only add
        # delay, and if the viewer had already left it would bring
        # capture back up for nothing.
        return self.codec

    def catch_up(self) -> list[bytes]:
        """The frames sent to a freshly connected viewer.

        Everything from the last key frame up to now, so a new viewer
        does not wait for the next one - the picture appears at once.
        """
        return list(self._gop)
