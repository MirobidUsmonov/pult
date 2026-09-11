"""
The control session and the host context.

There is no transport here: it makes no difference who connected - a
phone browser, a connection arriving through the hub, or an AI agent.
They all send the same messages and get the same answers, which is why
adding an AI needs no separate code: it is simply one more session.
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

# Binary frame header: kind(1) + flags(1) + reserved(2) + timestamp(4)
_BIN_HEADER = struct.Struct("!BBHI")
BIN_VIDEO = 1

# A cap so the queue cannot grow without bound on a slow network. Once
# it is passed the queue is cleared and nothing is sent until the next
# key frame - the picture freezes for a moment, but latency stops piling
# up.
MAX_QUEUED_FRAMES = 60

# Messages that are forwarded to the source unchanged when they come
# from a controller that is watching some other source.
FORWARDED = {"mouse", "scroll", "key", "combo", "text", "release_keys", "cmd"}

# How the view follows the cursor between screens. Rebuilding the stream
# costs about a second of black picture, so these are deliberately
# unhurried - see HostContext.follow_cursor for what went wrong when they
# were not.
SWITCH_DWELL = 0.8        # seconds the cursor must stay on the other screen
SWITCH_COOLDOWN = 3.0     # seconds before another switch is considered
EDGE_MARGIN = 80          # pixels clear of the edge before it counts


def pack_video(au: bytes, key: bool, ts_ms: int) -> bytes:
    return _BIN_HEADER.pack(BIN_VIDEO, 1 if key else 0, 0, ts_ms & 0xFFFFFFFF) + au


class ControllerSession:
    """A single connected controller."""

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
        self.name = "controller"
        self.role = "controller"
        self.viewing = False
        self.connected_at = time.monotonic()

        self._queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=MAX_QUEUED_FRAMES)
        self._need_key = True
        self._writer: asyncio.Task | None = None
        self._closed = False

        # Keys currently held down. In a gesture like Alt+Tab the key is
        # pressed in one message and released in another. If the
        # connection dropped in between, the key stayed down on the
        # computer - and a computer with Alt stuck down is unusable. So
        # everything is released when the session closes.
        self._held_keys: set[str] = set()

        # The un-rounded remainder of relative movement
        self._frac_x = 0.0
        self._frac_y = 0.0

        # A session comes in two kinds. A "controller" watches a screen
        # and sends commands (phone, browser, AI). A "source" hands over
        # its own screen and carries out commands (the phone app). One
        # class covers both, because the protocol is the same.
        self.is_source = False
        self.source_key = ""          # for a source, its own id
        self.source_info: dict = {}
        self.source_stream: dict | None = None
        self.source_gop: list[bytes] = []
        # For a controller, which source it is watching right now
        self.source_id = "local"
        # When it connected: on disconnect the log says how long it held
        self.started_at = time.monotonic()

    # -- lifecycle ---------------------------------------------------------

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
        """Releases every key this session left pressed."""
        for name in list(self._held_keys):
            try:
                self.ctx.input.key(name, "up")
            except Exception:
                pass
        if self._held_keys:
            log.info("session %s: released held keys: %s",
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
            log.info("session %s dropped: %s", self.id, exc)
            self._closed = True

    # -- video -------------------------------------------------------------

    def offer_unit(self, au: bytes, key: bool, ts_ms: int) -> None:
        """Queues a frame, dropping it when the queue is full."""
        if not self.viewing or self._closed:
            return
        if self._need_key:
            if not key:
                return
            self._need_key = False
        try:
            self._queue.put_nowait(pack_video(au, key, ts_ms))
        except asyncio.QueueFull:
            # The network cannot keep up: throw away the built-up delay
            while not self._queue.empty():
                try:
                    self._queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
            self._need_key = True
            log.debug("session %s: queue full, waiting for a key frame", self.id)

    async def on_binary(self, data: bytes) -> None:
        """A frame from a source - hand it to everyone watching it."""
        if not self.is_source or len(data) < _BIN_HEADER.size:
            return
        kind, flags, _r, _ts = _BIN_HEADER.unpack_from(data, 0)
        if kind != BIN_VIDEO:
            return
        key = bool(flags & 1)
        # Keep everything since the last key frame: a new viewer sees a
        # picture without waiting for the next one.
        if key:
            self.source_gop = [data]
        elif len(self.source_gop) < 240:
            self.source_gop.append(data)
        await self.ctx.route_source_frame(self, data)

    async def send_catch_up(self) -> None:
        """On connect, replays from the last key frame onwards."""
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

    # -- incoming messages -------------------------------------------------

    async def handle(self, msg: dict) -> None:
        kind = msg.get("t")

        # When the controller is watching a phone screen, input has to
        # go to that phone rather than to the computer. The message has
        # the same shape either way, so it is forwarded unchanged.
        if kind in FORWARDED and not self.is_source and self.source_id != "local":
            target = self.ctx.remote_sources.get(self.source_id)
            if target is None:
                await self.send_json({"t": "error", "msg": "source not connected"})
                return
            try:
                await target.send_json(msg)
            except Exception as exc:
                log.info("not forwarded to the source: %s", exc)
            return

        handler = getattr(self, f"_on_{kind}", None)
        if handler is None:
            await self.send_json({"t": "error", "msg": f"unknown message: {kind}"})
            return
        try:
            await handler(msg)
        except (ValueError, KeyError, PermissionError, TypeError) as exc:
            # A bad message from the client is not our failure, so it is
            # logged short and without a stack trace.
            log.warning("rejected (%s): %s", kind, exc)
            await self.send_json({"t": "error", "msg": str(exc)})
        except Exception as exc:
            log.exception("unexpected error while handling a message: %s", msg)
            await self.send_json({"t": "error", "msg": str(exc)})

    async def _on_note(self, msg: dict) -> None:
        """A note the client makes about itself - it goes to the log.

        There is no easy way to see what is happening on the phone:
        reading its logs needs a cable and developer mode. So the app
        reports the events that matter (why it stopped, why it dropped)
        this way, and they show up in the computer's log.
        """
        text = str(msg.get("msg") or "")[:300]
        log.info("[%s] %s", self.name, text)

    async def _on_hello(self, msg: dict) -> None:
        self.name = str(msg.get("name") or self.name)[:64]
        self.role = str(msg.get("role") or "controller")[:32]
        log.info("connected: %s (%s) %s", self.name, self.role, self.peer)

        if self.role == "source":
            self.is_source = True
            self.source_key = f"s{self.id}"
            info = msg.get("info")
            self.source_info = dict(info) if isinstance(info, dict) else {}
            await self.ctx.add_source(self)

    async def _on_ping(self, msg: dict) -> None:
        await self.send_json({"t": "pong", "id": msg.get("id"), "ts": msg.get("ts")})

    async def _on_stream(self, msg: dict) -> None:
        """A source described its stream - pass it to everyone watching."""
        if not self.is_source:
            raise ValueError("only a source sends this message")
        self.source_stream = {
            "t": "stream",
            "codec": msg.get("codec"),
            "w": msg.get("w"),
            "h": msg.get("h"),
            "fps": msg.get("fps"),
            "encoder": msg.get("encoder", "phone"),
            "source": self.source_key,
        }
        self.source_gop = []
        for v in self.ctx.viewers_of(self.source_key):
            try:
                await v.send_json(self.source_stream)
            except Exception:
                pass

    async def _on_view(self, msg: dict) -> None:
        """Turns the stream on or off and changes its settings."""
        want = bool(msg.get("on", True))

        # Switching to another source (a phone)
        if "source" in msg:
            new_id = str(msg["source"])
            if new_id != self.source_id:
                if new_id != "local" and new_id not in self.ctx.remote_sources:
                    raise ValueError(f"no such source: {new_id}")
                self.source_id = new_id
                self._need_key = True

        if self.source_id != "local":
            self.viewing = want
            await self.ctx.sync_capture()      # the computer stream is not needed
            await self.ctx.sync_sources()
            src = self.ctx.remote_sources.get(self.source_id)
            if want and src is not None:
                if src.source_stream:
                    await self.send_json(src.source_stream)
                # Send everything since the key frame - the picture is instant
                for frame in list(src.source_gop):
                    try:
                        self._queue.put_nowait(frame)
                        self._need_key = False
                    except asyncio.QueueFull:
                        break
            return

        s = self.ctx.cfg.stream
        monitor_changed = False
        if "monitor" in msg and int(msg["monitor"]) != s.monitor:
            s.monitor = int(msg["monitor"])
            monitor_changed = True
        if "fps" in msg:
            s.fps = max(1, min(60, int(msg["fps"])))
        if "width" in msg:
            s.width = max(0, min(3840, int(msg["width"])))
        if "bitrate" in msg:
            s.bitrate_kbps = max(200, min(50000, int(msg["bitrate"])))
        if "cursor" in msg:
            s.cursor = bool(msg["cursor"])
        if "follow" in msg:
            s.follow_cursor = bool(msg["follow"])

        was = self.viewing
        self.viewing = want
        if monitor_changed:
            self._frac_x = self._frac_y = 0.0
            self.ctx.ensure_cursor_on_monitor()
        await self.ctx.sync_capture(changed=(want and was))
        await self.ctx.sync_sources()
        if want:
            # Wait until the codec string is known: the browser sets up
            # its decoder from exactly that string, and frames sent
            # before it are wasted.
            await self.ctx.capture.wait_codec()
            # Someone joining while the screen is already out of reach
            # gets told straight away, rather than waiting out a
            # timeout to learn nothing.
            if self.ctx.capture.blocked:
                await self.send_json({"t": "blocked",
                                      "reason": self.ctx.capture.blocked})
                return
            await self.send_json(self.ctx.stream_message())
            await self.send_catch_up()

    # -- input -------------------------------------------------------------

    def _to_desktop(self, x: float, y: float) -> tuple[int, int]:
        """Turns a normalised (0..1) coordinate into desktop pixels."""
        mon = self.ctx.monitor()
        px = mon["x"] + x * (mon["w"] - 1)
        py = mon["y"] + y * (mon["h"] - 1)
        return int(round(px)), int(round(py))

    def _accumulate(self, dx: float, dy: float) -> tuple[int, int]:
        """Carries the fractional part over to the next movement.

        The cursor only lands on whole pixels. With a finger moving
        slowly each step was under half a pixel and rounded to zero - the
        cursor did not move at all. Accumulating the remainder keeps slow
        movement smooth.
        """
        dx += self._frac_x
        dy += self._frac_y
        ix, iy = int(dx), int(dy)
        self._frac_x = dx - ix
        self._frac_y = dy - iy
        return ix, iy

    def _check_input(self) -> None:
        if not self.ctx.cfg.security.allow_input:
            raise PermissionError("input is disabled in the settings")

    async def _on_mouse(self, msg: dict) -> None:
        self._check_input()
        wi = self.ctx.input
        action = msg.get("a", "move")
        if action == "move":
            wi.move_to(*self._to_desktop(float(msg["x"]), float(msg["y"])))
        elif action == "moveby":
            ix, iy = self._accumulate(float(msg.get("dx", 0)), float(msg.get("dy", 0)))
            if ix or iy:
                if self.ctx.cfg.stream.follow_cursor:
                    # The cursor moves freely between screens and the
                    # view follows it, so it can never wander somewhere
                    # that is not visible.
                    wi.move_by(ix, iy)
                    await self.ctx.follow_cursor()
                else:
                    # If the cursor was left on another screen, bring it
                    # here first. Simply clamping it to the edge made
                    # even a tiny movement jump it to a far corner -
                    # surprising and awkward.
                    self.ctx.ensure_cursor_on_monitor()
                    wi.move_by(ix, iy, bounds=self.ctx.monitor_bounds())
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
            raise ValueError(f"unknown mouse action: {action}")

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

    async def _on_swap_monitors(self, msg: dict) -> None:
        """Swaps them when the screen number and the video source disagree."""
        from .. import config as cfgmod

        new_map = self.ctx.rotate_monitor_map()
        cfgmod.save(self.ctx.cfg)
        log.info("monitor map changed: %s", new_map)
        await self.ctx.sync_capture(changed=True)
        for s in list(self.ctx.sessions):
            try:
                await s.send_json({"t": "monitor_map", "map": new_map})
            except Exception:
                pass

    async def _on_release_keys(self, msg: dict) -> None:
        """Release on the client's own request too (app going background)."""
        self.release_keys()

    async def _on_combo(self, msg: dict) -> None:
        self._check_input()
        keys = msg.get("keys") or []
        if not isinstance(keys, list) or not keys:
            raise ValueError("combo needs a keys list")
        self.ctx.input.combo([str(k) for k in keys])

    async def _on_text(self, msg: dict) -> None:
        self._check_input()
        text = str(msg.get("s", ""))
        if text:
            self.ctx.input.type_text(text)

    async def _on_cmd(self, msg: dict) -> None:
        if not self.ctx.cfg.security.allow_commands:
            raise PermissionError("system commands are disabled in the settings")
        name = str(msg.get("name", ""))
        result = self.ctx.run_command(name, msg)
        await self.send_json({"t": "cmd_ok", "name": name, "result": result})


class HostContext:
    """The computer's shared state: capture, input, connected clients."""

    def __init__(self, cfg: Config, caps: ff.Capabilities) -> None:
        from ..platform import input_backend

        self.cfg = cfg
        self.caps = caps
        self.input = input_backend()
        self.monitors = self.input.list_monitors()
        self.sessions: set[ControllerSession] = set()
        # Connected phones: they both give a screen and carry out commands
        self.remote_sources: dict[str, ControllerSession] = {}
        self._addr_cache: list[str] | None = None
        self._addr_at = 0.0
        self.started_at = time.time()

        self.capture = ScreenCapture(caps, self._on_unit, monitors=self.monitors)
        # The viewer gets the codec string even when it arrives late.
        # Before, we only waited for it: if it never came the "stream"
        # message went out with an empty codec and was never updated
        # again - the viewer sat on "Waiting for the screen" forever.
        self.capture.on_codec = self._codec_ready
        self.capture.on_blocked = self._capture_blocked
        self._stats_task: asyncio.Task | None = None
        self._cross_since = 0.0
        self._switched_at = 0.0

        # Built on first use: loading the speech model costs both time
        # and several hundred megabytes, and most sessions never dictate.
        self._recogniser = None

        self._system = None
        if system_name() == "Windows":
            from ..platform import win_system

            self._system = win_system

    # -- sources -----------------------------------------------------------

    def sources_list(self) -> list[dict]:
        """Everything that can be watched: the computer itself and phones."""
        out = [{
            "id": "local",
            "name": self.cfg.host_name,
            "kind": "pc",
            "monitors": self.monitors,
        }]
        for key, sess in self.remote_sources.items():
            info = dict(sess.source_info)
            out.append({
                "id": key,
                "name": sess.name or info.get("name") or "phone",
                "kind": info.get("kind", "phone"),
                "w": info.get("w"),
                "h": info.get("h"),
                "input": bool(info.get("input")),
            })
        return out

    async def add_source(self, sess: "ControllerSession") -> None:
        self.remote_sources[sess.source_key] = sess
        log.info("source added: %s (%s)", sess.name, sess.source_key)
        await self.broadcast_sources()

    async def remove_source(self, sess: "ControllerSession") -> None:
        if self.remote_sources.pop(sess.source_key, None) is None:
            return
        # How long it held is logged too: when a phone keeps dropping,
        # that is the first number to look at.
        alive = time.monotonic() - sess.started_at if sess.started_at else 0.0
        log.info("source dropped: %s (held for %.0f seconds)", sess.name, alive)
        # Send anyone watching it back to the computer screen
        for s in list(self.sessions):
            if s.source_id == sess.source_key:
                s.source_id = "local"
                try:
                    await s.send_json({"t": "source_gone", "id": sess.source_key})
                except Exception:
                    pass
        await self.broadcast_sources()
        await self.sync_capture()

    async def broadcast_sources(self) -> None:
        msg = {"t": "sources", "list": self.sources_list()}
        for s in list(self.sessions):
            if s.is_source:
                continue
            try:
                await s.send_json(msg)
            except Exception:
                pass

    def viewers_of(self, source_id: str) -> list["ControllerSession"]:
        return [s for s in self.sessions
                if s.viewing and not s.is_source and s.source_id == source_id]

    async def route_source_frame(self, sess: "ControllerSession", data: bytes) -> None:
        """Routes a frame from a phone to everyone watching that phone."""
        for s in self.viewers_of(sess.source_key):
            try:
                s._queue.put_nowait(data)
            except asyncio.QueueFull:
                # One slow viewer must not slow the whole stream down
                while not s._queue.empty():
                    try:
                        s._queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break

    async def sync_sources(self) -> None:
        """Tells each phone whether anyone is watching it.

        With nobody watching, the phone does not capture its screen -
        no battery and no traffic spent for nothing.
        """
        for key, sess in list(self.remote_sources.items()):
            want = bool(self.viewers_of(key))
            if want == getattr(sess, "_streaming", False):
                continue
            sess._streaming = want
            s = self.cfg.stream
            try:
                await sess.send_json({
                    "t": "stream_start" if want else "stream_stop",
                    "fps": s.fps, "width": s.width, "bitrate": s.bitrate_kbps,
                })
            except Exception:
                pass

    # -- information -------------------------------------------------------

    def monitor(self) -> dict:
        idx = self.cfg.stream.monitor
        for m in self.monitors:
            if m["index"] == idx:
                return m
        return self.monitors[0] if self.monitors else {"x": 0, "y": 0, "w": 1920, "h": 1080}

    def monitor_at(self, x: int, y: int) -> int | None:
        for m in self.monitors:
            if m["x"] <= x < m["x"] + m["w"] and m["y"] <= y < m["y"] + m["h"]:
                return m["index"]
        return None

    async def follow_cursor(self) -> None:
        """Moves the view along when the cursor crosses to another screen.

        Switching is expensive: it tears down ffmpeg and the hardware
        encoder and builds them again, which costs about a second of
        black picture and a burst of GPU work. Doing it on every crossing
        of the boundary was worse than not following at all - in one
        recorded session the cursor sat near the edge and capture
        restarted forty times in two minutes, which stuttered everything
        else on the machine, games included.

        So a crossing has to look deliberate before it is acted on:

        - the cursor must stay on the other screen for a full second,
          not a third of one, which is longer than any pass through on
          the way somewhere else;
        - it must be properly inside that screen rather than hugging the
          line, so a cursor resting on the boundary cannot flip back and
          forth between two readings;
        - and after a switch there is a pause before the next one, so
          even deliberate crossings cannot queue up faster than the
          stream can be rebuilt.
        """
        now = time.monotonic()
        if now - self._switched_at < SWITCH_COOLDOWN:
            self._cross_since = 0.0
            return

        x, y = self.input.cursor_pos()
        idx = self.monitor_at(x, y)
        if idx is None or idx == self.cfg.stream.monitor:
            self._cross_since = 0.0
            return

        # Well inside, not merely across. Without this a cursor sitting
        # on the seam between two screens reads as first one and then the
        # other as it trembles by a single pixel.
        mon = next((m for m in self.monitors if m["index"] == idx), None)
        if mon is None:
            return
        margin = min(EDGE_MARGIN, mon["w"] // 4, mon["h"] // 4)
        if not (mon["x"] + margin <= x < mon["x"] + mon["w"] - margin
                and mon["y"] + margin <= y < mon["y"] + mon["h"] - margin):
            self._cross_since = 0.0
            return

        if not self._cross_since:
            self._cross_since = now
            return
        if now - self._cross_since < SWITCH_DWELL:
            return

        self._cross_since = 0.0
        self._switched_at = now
        self.cfg.stream.monitor = idx
        log.info("cursor moved to screen %d, the view followed", idx + 1)
        await self.sync_capture(changed=True)

    def monitor_bounds(self) -> tuple[int, int, int, int]:
        m = self.monitor()
        return m["x"], m["y"], m["w"], m["h"]

    def ensure_cursor_on_monitor(self) -> None:
        """Brings the cursor onto the screen that is being watched.

        After switching screens the cursor stayed on the old one, and a
        tap on the trackpad landed on a screen the user could not see.
        A cursor already on the right screen is left alone - no need to
        make it jump for nothing.
        """
        if not self.cfg.security.allow_input:
            return
        bounds = self.monitor_bounds()
        x, y = self.input.cursor_pos()
        if self.input.contains(bounds, x, y):
            return
        bx, by, bw, bh = bounds
        self.input.move_to(bx + bw // 2, by + bh // 2)

    def addresses(self) -> list[str]:
        """Addresses that reach this agent, fastest first.

        Local addresses come first: on the same network they are several
        times faster than the tunnel and have less latency. The tunnel
        goes last - it works from anywhere, but the traffic detours
        through Cloudflare.
        """
        from ..config import local_addresses

        # Finding the local addresses takes a network query, and that
        # was repeated on every connection. IP addresses rarely change,
        # so they are cached for a short while. The tunnel address stays
        # outside the cache - it is updated often.
        now = time.monotonic()
        if self._addr_cache is None or now - self._addr_at > 30:
            scheme = "http" if self.cfg.tls == "off" else "https"
            self._addr_cache = list(local_addresses(self.cfg.port, scheme))
            self._addr_at = now

        out = list(self._addr_cache)
        if self.cfg.public_url:
            out.append(self.cfg.public_url.rstrip("/"))
        return out

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
                # Every address that reaches this computer. The phone
                # picks the fastest itself: on the same network a local
                # address beats the tunnel by a wide margin, and from
                # another network only the tunnel works. The tunnel
                # address is new on every start, so it has to be
                # restated on every connection.
                "addresses": self.addresses(),
                # Whether speaking instead of typing is possible here.
                # The phone hides the microphone button when it is not,
                # because a button that always answers "not set up" is
                # worse than no button.
                "stt": self.stt_available(),
            },
            "sources": self.sources_list(),
            "stream": {
                "monitor": self.cfg.stream.monitor,
                "monitor_map": self.cfg.stream.monitor_map,
                "follow_cursor": self.cfg.stream.follow_cursor,
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

    def source_for(self, monitor: int) -> int:
        """The capture source that matches a screen number."""
        m = self.cfg.stream.monitor_map
        if m and 0 <= monitor < len(m):
            return m[monitor]
        return monitor

    def rotate_monitor_map(self) -> list[int]:
        """Rotates the screen-to-source mapping by one step.

        On a two-screen computer this swaps them. There is no reliable
        way to work out the order of the graphics card's outputs, so when
        the guess comes out wrong the user can fix it with one press.
        """
        n = len(self.monitors)
        if n < 2:
            raise ValueError("swapping needs at least two screens")
        cur = list(self.cfg.stream.monitor_map) or list(range(n))
        if len(cur) != n:
            cur = list(range(n))
        cur = cur[1:] + cur[:1]
        self.cfg.stream.monitor_map = cur
        return cur

    def _capture_config(self) -> CaptureConfig:
        s = self.cfg.stream
        return CaptureConfig(
            monitor=s.monitor,
            source=self.source_for(s.monitor),
            fps=s.fps,
            scale_width=s.width,
            bitrate_kbps=s.bitrate_kbps,
            draw_cursor=s.cursor,
            encoder=s.encoder,
        )

    # -- connections -------------------------------------------------------

    async def attach(self, session: ControllerSession) -> None:
        self.sessions.add(session)
        if self._stats_task is None:
            self._stats_task = asyncio.create_task(self._stats_loop(), name="pult-stats")

    async def detach(self, session: ControllerSession) -> None:
        self.sessions.discard(session)
        if session.is_source:
            await self.remove_source(session)
        await self.sync_capture()
        await self.sync_sources()
        if not self.sessions and self._stats_task:
            self._stats_task.cancel()
            self._stats_task = None

    async def sync_capture(self, changed: bool = False) -> None:
        """Starts or stops capture depending on whether anyone is watching.

        With nobody looking, ffmpeg does not run at all - that is why the
        program costs nothing while the computer sits idle.
        """
        want = bool(self.viewers_of("local"))
        if want and not self.capture.running:
            await self.capture.start(self._capture_config())
        elif want and changed:
            await self.capture.apply(self._capture_config())
            # Changing a setting restarts the stream and can change the
            # codec string. The other viewers, who did not ask for the
            # change, need to hear about it too.
            asyncio.create_task(self._announce_stream(), name="pult-announce")
        elif not want and self.capture.running:
            await self.capture.stop()

    def _codec_ready(self) -> None:
        """The codec is known - tell the watchers at once."""
        asyncio.create_task(self._send_stream(), name="pult-codec")

    def _capture_blocked(self, reason: str) -> None:
        """The system will not let us capture - say so, and say when it will.

        Called from the capture thread's callback, so the sending is
        handed to the loop rather than done here.
        """
        asyncio.create_task(self._send_blocked(reason), name="pult-blocked")

    async def _send_blocked(self, reason: str) -> None:
        msg = {"t": "blocked", "reason": reason}
        for s in list(self.sessions):
            if not s.viewing or s.source_id != "local":
                continue
            try:
                await s.send_json(msg)
            except Exception:
                pass

    async def _send_stream(self) -> None:
        msg = self.stream_message()
        for s in list(self.sessions):
            if not s.viewing:
                continue
            try:
                await s.send_json(msg)
                await s.send_catch_up()
            except Exception:
                pass

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
        for s in self.viewers_of("local"):
            s.offer_unit(au, key, ts)

    async def _stats_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(1.0)
                if not self.sessions:
                    continue

                # The view follows the cursor from here as well as from
                # the mouse messages themselves. Checking only on those
                # meant that crossing to the other screen and stopping
                # never switched anything: the dwell has to elapse, and
                # with the mouse at rest nothing was left to notice that
                # it had.
                if self.cfg.stream.follow_cursor and self.viewers_of("local"):
                    try:
                        await self.follow_cursor()
                    except Exception:
                        log.debug("following the cursor failed", exc_info=True)
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

    # -- dictation ---------------------------------------------------------

    def recogniser(self):
        """The speech recogniser, built the first time it is wanted."""
        if self._recogniser is None:
            from .. import stt as sttmod

            self._recogniser = sttmod.Recogniser(self.cfg)
        return self._recogniser

    def stt_available(self) -> bool:
        from .. import stt as sttmod

        return sttmod.available(self.cfg)

    # -- commands ----------------------------------------------------------

    def run_command(self, name: str, msg: dict) -> str:
        if self._system is None:
            raise RuntimeError("commands are not supported on this system")
        if name == "run":
            command = str(msg.get("command", "")).strip()
            if not command:
                raise ValueError("command is empty")
            self._system.run_program(command)
            return f"started: {command}"
        fn = self._system.COMMANDS.get(name)
        if fn is None:
            raise ValueError(f"unknown command: {name}")
        fn()
        return "done"

    async def shutdown(self) -> None:
        if self._stats_task:
            self._stats_task.cancel()
            self._stats_task = None
        await self.capture.stop()
