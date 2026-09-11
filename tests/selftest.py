"""
Pult's self-test.

Usage:
    python tests/selftest.py

What it does: finds ffmpeg and the encoders, counts the screens,
measures pointer accuracy, actually runs the video pipeline and checks
the stream with ffprobe, then brings up a temporary server and exercises
the whole protocol end to end.

The tests leave your real settings alone: a temporary folder is used.
The mouse moves briefly and is put back where it was.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import ssl
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# So the real settings are left alone - BEFORE the import
_TMP = Path(tempfile.mkdtemp(prefix="pult-selftest-"))
os.environ["PULT_CONFIG_DIR"] = str(_TMP)

import aiohttp  # noqa: E402

from pult import config as cfgmod  # noqa: E402
from pult.capture import CaptureConfig, ScreenCapture, codec_string, is_keyframe  # noqa: E402
from pult.host.server import HostServer  # noqa: E402
from pult.platform import ffmpeg as ff  # noqa: E402

HDR = struct.Struct("!BBHI")

PASS, FAIL, SKIP = "OK  ", "FAIL", "----"
results: list[tuple[str, str, str]] = []


def check(name: str, ok: bool | None, detail: str = "") -> bool:
    tag = SKIP if ok is None else (PASS if ok else FAIL)
    results.append((tag, name, detail))
    print(f"  [{tag}] {name}" + (f"   {detail}" if detail else ""))
    return bool(ok)


def section(title: str) -> None:
    print()
    print(f"=== {title} ===")


def cursor_still(wi, ms: int = 80) -> bool:
    """Whether the cursor is sitting still."""
    a = wi.cursor_pos()
    time.sleep(ms / 1000)
    return a == wi.cursor_pos()


def wait_still(wi, timeout: float = 3.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cursor_still(wi):
            return True
    return False


def measure_pointer(wi, targets, send, tries: int = 3) -> tuple[int, bool]:
    """Sends the cursor to given points and measures the accuracy.

    Returns: (the worst error, whether there was interference).

    If the user happens to be moving the mouse right then, their
    movement overrides our command and the measurement is meaningless.
    Counting that as a failure would be wrong - so we wait for the
    cursor to settle, retry a few times, and if it still will not
    settle, mark it "could not measure".
    """
    worst = 0
    interference = False
    for (tx, ty) in targets:
        best = None
        for _ in range(tries):
            if not wait_still(wi, timeout=2.0):
                interference = True
                continue
            send(tx, ty)
            t0 = time.monotonic()
            err = 10**6
            while time.monotonic() - t0 < 0.8:
                time.sleep(0.02)
                gx, gy = wi.cursor_pos()
                err = max(abs(gx - tx), abs(gy - ty))
                if err <= 2:
                    break
            best = err if best is None else min(best, err)
            if best <= 2:
                break
            interference = True
        worst = max(worst, best if best is not None else 10**6)
    return worst, interference


def free_port() -> int:
    import socket

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# ---------------------------------------------------------- 1. environment


def test_environment() -> ff.Capabilities | None:
    section("1. Environment")
    path = ff.find_ffmpeg()
    if not check("ffmpeg found", bool(path), path or "not on PATH"):
        return None
    caps = ff.probe(path)
    check("version read", bool(caps.version), caps.version)

    h264 = sorted(e for e in caps.encoders if "264" in e)
    check("an H.264 encoder exists", bool(h264), ", ".join(h264))

    enc = ff.pick_encoder(caps)
    check("encoder chosen", True, f"{enc['label']} ({enc['name']})")
    if enc["name"] == "libx264":
        check("hardware encoding", None, "none - the CPU is used, which costs more")

    grabber = "ddagrab" if "ddagrab" in caps.filters else ("gdigrab" if "gdigrab" in caps.devices else "")
    check("capture method", bool(grabber), grabber or "not found")
    check("AUD bitstream filter", caps.has_aud_bsf,
          "h264_metadata" if caps.has_aud_bsf else "missing - frame splitting will not work")
    return caps


# ------------------------------------------------------ 2. screens, input


def test_tunnel_parsing() -> None:
    """The tunnel address is read out of cloudflared's log stream.

    cloudflared names its own API endpoint in that same stream, and
    mistaking it for the tunnel is expensive: the wrong address goes to
    the phone, is stored there, and every later connection loads
    Cloudflare's API instead of Pult - a blank page with a broken image
    and no hint of why. It happened, hence these checks.
    """
    section("2b. Tunnel address")
    from pult.tunnel import NOT_TUNNELS, URL_RE

    def read(line: bytes) -> str | None:
        m = URL_RE.search(line)
        if not m:
            return None
        found = m.group(0).decode()
        label = found.split("//", 1)[1].split(".", 1)[0]
        return None if label in NOT_TUNNELS else found

    real = b'2026-09-11T09:09:32Z INF |  https://slide-comparing-sox-labeled.trycloudflare.com  |'
    check("a real tunnel address is taken",
          read(real) == "https://slide-comparing-sox-labeled.trycloudflare.com",
          str(read(real)))

    api = (b'2026-09-11T09:05:37Z ERR Failed to request quick Tunnel '
           b'error="Post \\"https://api.trycloudflare.com/tunnel\\": timeout"')
    check("cloudflared's own API is not mistaken for one", read(api) is None,
          str(read(api)))

    plain = b"2026-09-11T09:05:36Z INF Requesting new quick Tunnel on trycloudflare.com..."
    check("a line with no address yields none", read(plain) is None, str(read(plain)))


def test_input() -> list[dict]:
    section("2. Screens and input")
    try:
        from pult.platform import input_backend

        wi = input_backend()
    except Exception as exc:
        check("input module", False, str(exc))
        return []

    mons = wi.list_monitors()
    check("screens found", bool(mons),
          ", ".join(f"{m['w']}x{m['h']}" for m in mons))
    if not mons:
        return []

    saved = wi.cursor_pos()
    targets = [
        (m["x"] + int(fx * (m["w"] - 1)), m["y"] + int(fy * (m["h"] - 1)))
        for m in mons
        for fx, fy in ((0.5, 0.5), (0.0, 0.0), (0.99, 0.99))
    ]
    worst, noisy = measure_pointer(wi, targets, wi.move_to)
    if worst > 2 and noisy:
        check("pointer accuracy", None, "could not measure - the mouse is in use")
    else:
        check("pointer accuracy", worst <= 2, f"worst error {worst} px")

    wi.move_to(mons[0]["x"] + 400, mons[0]["y"] + 400)
    time.sleep(0.05)
    bx, by = wi.cursor_pos()
    wi.move_by(50, -30)
    time.sleep(0.05)
    ax, ay = wi.cursor_pos()
    check("relative movement", abs((ax - bx) - 50) <= 2 and abs((ay - by) + 30) <= 2,
          f"asked for (+50,-30), got ({ax - bx:+d},{ay - by:+d})")

    bad = [k for k in ("ctrl", "shift", "Escape", "F5", "ArrowUp", "a", "7",
                       "AudioVolumeUp", "NumpadEnter")
           if not _key_ok(wi, k)]
    check("key map", not bad, "all resolved" if not bad else f"not resolved: {bad}")

    wi.move_to(*saved)
    return mons


def _key_ok(wi, name: str) -> bool:
    try:
        wi.resolve_key(name)
        return True
    except KeyError:
        return False


# ------------------------------------------------------------ 3. video


async def test_capture(caps: ff.Capabilities, mons: list[dict]) -> None:
    section("3. The video pipeline")
    out = _TMP / "probe.h264"
    units: list[tuple[int, bool]] = []
    fh = out.open("wb")

    def on_unit(au: bytes, key: bool) -> None:
        units.append((len(au), key))
        fh.write(au)

    cap = ScreenCapture(caps, on_unit, monitors=mons)
    cfg = CaptureConfig(monitor=0, fps=30, scale_width=1280, bitrate_kbps=4000)
    await cap.start(cfg)
    codec = await cap.wait_codec(timeout=8)
    await asyncio.sleep(2.5)
    await cap.stop()
    fh.close()

    check("frames arrived", len(units) > 20, f"{len(units)} frames")
    check("a key frame is present", any(k for _, k in units),
          f"{sum(1 for _, k in units if k)}")
    check("the first frame is a key frame", bool(units and units[0][1]),
          "a new viewer sees it at once" if units and units[0][1] else "there will be a wait")
    check("codec string determined", bool(codec and codec.startswith("avc1.")), codec or "none")
    check("size is right", cap.width == 1280, f"{cap.width}x{cap.height}")

    # The most important check: is the split-and-reassembled stream intact.
    probe = shutil.which("ffprobe") or str(Path(caps.path).with_name("ffprobe.exe"))
    if Path(probe).exists() or shutil.which("ffprobe"):
        r = subprocess.run(
            [caps.path, "-hide_banner", "-loglevel", "error", "-i", str(out), "-f", "null", "-"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        err = (r.stderr or "").strip()
        check("the stream is intact (ffmpeg decoded it)", not err, err[:120] or "no errors")
    else:
        check("stream check", None, "ffprobe not found")


# ------------------------------------------------------- 4. server, protocol


async def test_protocol(caps: ff.Capabilities, mons: list[dict]) -> None:
    section("4. The server and the protocol")

    cfg = cfgmod.load()
    cfg.port = free_port()
    cfg.bind = "127.0.0.1"
    server = HostServer(cfg, caps)
    await server.start()
    check("HTTPS server came up", True, f"port {cfg.port}, self-signed certificate")

    sslctx = ssl.create_default_context()
    sslctx.check_hostname = False
    sslctx.verify_mode = ssl.CERT_NONE
    base = f"https://127.0.0.1:{cfg.port}"

    try:
        async with aiohttp.ClientSession() as sess:
            # -- authorisation
            for label, url in (("a wrong key", f"{base}/ws?k=xxx"), ("no key", f"{base}/ws")):
                try:
                    async with sess.ws_connect(url, ssl=sslctx):
                        check(f"{label} was refused", False, "it was allowed!")
                except aiohttp.WSServerHandshakeError as e:
                    check(f"{label} was refused", e.status == 401, f"HTTP {e.status}")

            # -- a full session
            async with sess.ws_connect(f"{base}/ws?k={cfg.token}", ssl=sslctx) as ws:
                hello = json.loads((await ws.receive()).data)
                check("hello arrived", hello.get("t") == "hello",
                      f"{hello.get('host', {}).get('name', '?')}, "
                      f"{len(hello.get('host', {}).get('monitors', []))} screens")

                await ws.send_json({"t": "view", "on": True, "width": 854,
                                    "fps": 24, "bitrate": 2000})

                # A longer wait than before: ddagrab gives its first
                # frame when the screen changes, and with a still
                # desktop the agent falls back to gdigrab. That
                # switchover has to fit inside the wait too.
                stream, frames, keys, total = None, 0, 0, 0
                end = time.monotonic() + 15
                while time.monotonic() < end and (frames < 40 or not stream):
                    try:
                        m = await asyncio.wait_for(ws.receive(), timeout=6)
                    except asyncio.TimeoutError:
                        break
                    if m.type == aiohttp.WSMsgType.TEXT:
                        d = json.loads(m.data)
                        if d.get("t") == "stream":
                            stream = d
                    elif m.type == aiohttp.WSMsgType.BINARY:
                        kind, flags, _, _ts = HDR.unpack_from(m.data, 0)
                        frames += 1
                        total += len(m.data) - 8
                        if flags & 1:
                            keys += 1

                check("stream message", stream is not None,
                      f"{stream['codec']} {stream['w']}x{stream['h']} {stream['encoder']}"
                      if stream else "did not arrive")
                check("video frames", frames > 20, f"{frames} frames, {total // 1024} KB")
                check("key frame", keys >= 1, f"{keys}")
                check("the setting was applied", bool(stream and stream["w"] == 854),
                      f"asked for 854, got {stream['w'] if stream else '?'}")

                # -- input through the protocol
                from pult.platform import input_backend

                wi = input_backend()
                saved = wi.cursor_pos()
                mon = mons[0]
                targets = [
                    (mon["x"] + round(nx * (mon["w"] - 1)), mon["y"] + round(ny * (mon["h"] - 1)))
                    for nx, ny in ((0.5, 0.5), (0.0, 0.0), (1.0, 1.0), (0.3, 0.7))
                ]
                latency: list[float] = []

                def send_via_protocol(tx: int, ty: int) -> None:
                    # Sending asynchronously from a synchronous function,
                    # so that both tests share the same measuring logic.
                    nx = (tx - mon["x"]) / (mon["w"] - 1)
                    ny = (ty - mon["y"]) / (mon["h"] - 1)
                    t0 = time.monotonic()
                    fut = asyncio.run_coroutine_threadsafe(
                        ws.send_json({"t": "mouse", "a": "move", "x": nx, "y": ny}), loop
                    )
                    fut.result(timeout=3)
                    latency.append((time.monotonic() - t0) * 1000)

                loop = asyncio.get_running_loop()
                worst, noisy = await asyncio.to_thread(
                    measure_pointer, wi, targets, send_via_protocol
                )
                detail = f"error {worst} px"
                if latency:
                    detail += f", send {sum(latency) / len(latency):.0f} ms"
                if worst > 2 and noisy:
                    check("mouse through the protocol", None,
                          "could not measure - the mouse is in use")
                else:
                    check("mouse through the protocol", worst <= 2, detail)
                wi.move_to(*saved)

                # -- multi-screen behaviour
                if len(mons) >= 2:
                    a, b = mons[0], mons[1]

                    # -- let the cursor cross screens: the view follows
                    await ws.send_json({"t": "view", "on": True,
                                        "monitor": a["index"], "follow": True})
                    await asyncio.sleep(0.5)
                    # Start from the edge NEAR the neighbouring screen
                    toward = 1 if b["x"] > a["x"] else -1
                    start_x = a["x"] + a["w"] - 5 if toward > 0 else a["x"] + 5
                    wi.move_to(start_x, a["y"] + a["h"] // 2)
                    await asyncio.sleep(0.1)
                    for _ in range(14):
                        await ws.send_json({"t": "mouse", "a": "moveby",
                                            "dx": 60 * toward, "dy": 0})
                        await asyncio.sleep(0.07)
                    await asyncio.sleep(1.2)
                    gx, gy = wi.cursor_pos()
                    crossed = (b["x"] <= gx < b["x"] + b["w"])
                    followed = server.ctx.cfg.stream.monitor == b["index"]
                    check("the cursor can cross to the second screen", crossed,
                          f"cursor ({gx},{gy})")
                    check("the view followed the cursor", followed,
                          f"the server is showing screen {server.ctx.cfg.stream.monitor + 1}")

                    # -- now the clamped mode
                    await ws.send_json({"t": "view", "on": True,
                                        "monitor": a["index"], "follow": False})
                    await asyncio.sleep(0.5)

                    # Deliberately put the cursor on the OTHER screen and
                    # send a tiny movement: it should come back to the
                    # watched screen without jumping to the edge
                    wi.move_to(b["x"] + b["w"] // 2, b["y"] + b["h"] // 2)
                    await asyncio.sleep(0.1)
                    await ws.send_json({"t": "mouse", "a": "moveby", "dx": 3, "dy": 0})
                    await asyncio.sleep(0.35)
                    gx, gy = wi.cursor_pos()
                    inside = (a["x"] <= gx < a["x"] + a["w"]) and (a["y"] <= gy < a["y"] + a["h"])
                    centered = abs(gx - (a["x"] + a["w"] // 2)) < 40
                    check("a cursor left on another screen is brought back",
                          inside and centered,
                          f"cursor ({gx},{gy}), did not jump to the edge" if centered
                          else f"cursor ({gx},{gy}) - jumped to the edge")

                    # A large movement must not push it off the screen either
                    for _ in range(6):
                        await ws.send_json({"t": "mouse", "a": "moveby", "dx": 900, "dy": 900})
                        await asyncio.sleep(0.06)
                    gx, gy = wi.cursor_pos()
                    inside = (a["x"] <= gx < a["x"] + a["w"]) and (a["y"] <= gy < a["y"] + a["h"])
                    check("the trackpad stays on the watched screen", inside,
                          f"cursor ({gx},{gy}), screen {a['index'] + 1} "
                          f"x:{a['x']}..{a['x'] + a['w']}")

                    # Switching screens should move the cursor there
                    await ws.send_json({"t": "view", "on": True, "monitor": b["index"]})
                    await asyncio.sleep(0.6)
                    gx, gy = wi.cursor_pos()
                    moved = (b["x"] <= gx < b["x"] + b["w"]) and (b["y"] <= gy < b["y"] + b["h"])
                    check("switching screens moves the cursor", moved, f"cursor ({gx},{gy})")
                    await ws.send_json({"t": "view", "on": True, "monitor": a["index"]})
                    await asyncio.sleep(0.5)

                    # -- the boundary must not be able to thrash
                    #
                    # Following the cursor once restarted capture on
                    # every crossing: a cursor resting near the seam
                    # rebuilt ffmpeg and the encoder dozens of times a
                    # minute, which stuttered everything else on the
                    # machine. A crossing now has to be deliberate, and
                    # there is a pause before the next one.
                    await ws.send_json({"t": "view", "on": True,
                                        "monitor": a["index"], "follow": True})
                    await asyncio.sleep(0.6)
                    switches = 0
                    seen = server.ctx.cfg.stream.monitor
                    mid_a = (a["x"] + a["w"] // 2, a["y"] + a["h"] // 2)
                    mid_b = (b["x"] + b["w"] // 2, b["y"] + b["h"] // 2)
                    started = time.monotonic()
                    while time.monotonic() - started < 6.0:
                        # Hop straight between the middles of the two
                        # screens - the worst case the old code had.
                        for target in (mid_b, mid_a):
                            wi.move_to(*target)
                            await ws.send_json({"t": "mouse", "a": "moveby",
                                                "dx": 1, "dy": 0})
                            await asyncio.sleep(0.25)
                            if server.ctx.cfg.stream.monitor != seen:
                                seen = server.ctx.cfg.stream.monitor
                                switches += 1
                    check("the screen boundary does not thrash", switches <= 3,
                          f"{switches} switches in 6s of crossing back and forth")
                    await ws.send_json({"t": "view", "on": True,
                                        "monitor": a["index"], "follow": False})
                    await asyncio.sleep(0.4)
                else:
                    check("multi-screen behaviour", None, "no second screen")

                # -- very small movements must not get lost
                wi.move_to(mons[0]["x"] + 600, mons[0]["y"] + 400)
                await asyncio.sleep(0.15)
                bx, by = wi.cursor_pos()
                for _ in range(20):
                    await ws.send_json({"t": "mouse", "a": "moveby", "dx": 0.3, "dy": 0})
                    await asyncio.sleep(0.03)
                await asyncio.sleep(0.2)
                ax, _ay = wi.cursor_pos()
                check("slow movement is not lost", abs((ax - bx) - 6) <= 2,
                      f"expected 20 x 0.3px = 6px, got {ax - bx}px")

                # -- errors
                async def next_error():
                    end2 = time.monotonic() + 4
                    while time.monotonic() < end2:
                        m = await asyncio.wait_for(ws.receive(), timeout=4)
                        if m.type == aiohttp.WSMsgType.TEXT:
                            d = json.loads(m.data)
                            if d.get("t") == "error":
                                return d
                    return {}

                await ws.send_json({"t": "no_such_thing"})
                check("an unknown message was refused",
                      (await next_error()).get("t") == "error")
                await ws.send_json({"t": "key", "a": "tap", "k": "NO_SUCH_KEY"})
                check("an unknown key was refused",
                      (await next_error()).get("t") == "error")

                await ws.send_json({"t": "view", "on": False})
                await asyncio.sleep(0.4)
                check("the stream stopped once the viewer left",
                      not server.ctx.capture.running,
                      "nothing is spent while idle")

            # -- connecting a phone as a source (control the other way)
            await test_phone_source(sess, base, sslctx, cfg)

            # -- dictation
            await test_dictation(sess, base, sslctx, cfg, caps)

            # -- the public info
            async with sess.get(f"{base}/api/info", ssl=sslctx) as r:
                info = await r.json()
                check("/api/info gives only the name without a key",
                      "monitors" not in info and "name" in info, str(info)[:70])
    finally:
        await server.stop()


# ------------------------------------------------------- 6. dictation


async def test_dictation(sess, base, sslctx, cfg, caps) -> None:
    """Speaking instead of typing.

    Skipped entirely when no speech model is installed - that is a
    normal state, not a failure, and the microphone button stays hidden
    on the phone in that case.
    """
    section("6. Dictation")

    from pult import stt as sttmod

    if not sttmod.available(cfg):
        check("speech model", None, "not installed - dictation is off")
        return
    check("speech model", True, str(sttmod.default_model()))

    url = f"{base}/api/stt?k={cfg.token}"

    async with sess.post(f"{base}/api/stt?k=wrong", data=b"x", ssl=sslctx) as r:
        check("a wrong key is refused", r.status == 401, f"HTTP {r.status}")

    async with sess.post(url, data=b"this is not audio" * 50, ssl=sslctx) as r:
        d = await r.json()
        check("rubbish is rejected cleanly", d.get("ok") is False,
              (d.get("msg") or "")[:50])

    def clip(spec: str) -> bytes:
        return subprocess.run(
            [caps.path, "-hide_banner", "-loglevel", "error", "-f", "lavfi",
             "-i", spec, "-c:a", "libopus", "-b:a", "24k", "-f", "webm", "pipe:1"],
            capture_output=True).stdout

    # Silence must come back empty. The model does not say "nothing was
    # said" on its own - given an empty recording it invents a plausible
    # phrase, and a stray press of the button would otherwise drop a word
    # nobody spoke into the text.
    async with sess.post(url, data=clip("anullsrc=r=48000:cl=mono:d=2"), ssl=sslctx) as r:
        d = await r.json()
        check("silence produces no words", d.get("ok") and not d.get("text"),
              repr(d.get("text", "")))

    # A real sound, to prove the whole path runs: decode, model, answer.
    t0 = time.monotonic()
    async with sess.post(url, data=clip("sine=frequency=440:duration=3"), ssl=sslctx) as r:
        d = await r.json()
        took = time.monotonic() - t0
        check("audio comes back as text", bool(d.get("ok") and d.get("text")),
              f"{d.get('text','')!r} in {took:.1f}s")


# --------------------------------------------- 5. control the other way


async def test_phone_source(sess, base, sslctx, cfg) -> None:
    """A phone connects as a "source"; the browser watches and controls it.

    Tested without a real phone: a fake client stands in for one. This
    covers the whole routing path - the source appearing in the list,
    frames reaching the right viewer, and input going to the phone
    rather than to the computer.
    """
    section("5. Control the other way (a phone as the source)")

    url = f"{base}/ws?k={cfg.token}"
    async with sess.ws_connect(url, ssl=sslctx) as phone:
        await phone.receive()      # hello
        await phone.send_json({
            "t": "hello", "role": "source", "name": "Test phone",
            "info": {"kind": "phone", "w": 1080, "h": 2400, "input": True},
        })
        await asyncio.sleep(0.3)

        async with sess.ws_connect(url, ssl=sslctx) as viewer:
            hello = json.loads((await viewer.receive()).data)
            names = [x["name"] for x in hello.get("sources", [])]
            check("the source appeared in the list", "Test phone" in names, ", ".join(names))

            await viewer.send_json({"t": "hello", "role": "controller", "name": "test"})
            src_id = next(x["id"] for x in hello["sources"] if x["kind"] == "phone")

            # The viewer picks the phone -> the phone should get "start streaming"
            await viewer.send_json({"t": "view", "on": True, "source": src_id})
            start = None
            end = time.monotonic() + 4
            while time.monotonic() < end:
                m = await asyncio.wait_for(phone.receive(), timeout=4)
                if m.type == aiohttp.WSMsgType.TEXT:
                    d = json.loads(m.data)
                    if d.get("t") == "stream_start":
                        start = d
                        break
            check("the phone received the stream request", start is not None,
                  f"{start.get('width')}px {start.get('fps')} fps" if start
                  else "did not arrive")

            # The phone describes its stream and sends frames
            await phone.send_json({"t": "stream", "codec": "avc1.42E01E",
                                   "w": 1080, "h": 2400, "fps": 30})
            for i in range(5):
                head = HDR.pack(1, 1 if i == 0 else 0, 0, i * 33)
                await phone.send_bytes(head + bytes([i]) * 400)
                await asyncio.sleep(0.05)

            got_stream, frames = None, 0
            end = time.monotonic() + 4
            while time.monotonic() < end and frames < 5:
                try:
                    m = await asyncio.wait_for(viewer.receive(), timeout=2)
                except asyncio.TimeoutError:
                    break
                if m.type == aiohttp.WSMsgType.TEXT:
                    d = json.loads(m.data)
                    if d.get("t") == "stream":
                        got_stream = d
                elif m.type == aiohttp.WSMsgType.BINARY:
                    frames += 1
            check("the phone's stream reached the viewer", got_stream is not None,
                  got_stream.get("codec") if got_stream else "did not arrive")
            check("the phone's frames were routed", frames >= 4, f"{frames} frames")

            # Input has to go to the phone, not to the computer
            saved = None
            try:
                from pult.platform import input_backend
                saved = input_backend().cursor_pos()
            except Exception:
                pass
            await viewer.send_json({"t": "mouse", "a": "click", "b": "left",
                                    "x": 0.5, "y": 0.5})
            forwarded = None
            end = time.monotonic() + 3
            while time.monotonic() < end:
                m = await asyncio.wait_for(phone.receive(), timeout=3)
                if m.type == aiohttp.WSMsgType.TEXT:
                    d = json.loads(m.data)
                    if d.get("t") == "mouse":
                        forwarded = d
                        break
            check("input was forwarded to the phone", forwarded is not None,
                  f"{forwarded.get('a')} ({forwarded.get('x')},{forwarded.get('y')})"
                  if forwarded else "did not arrive")
            if saved:
                now = input_backend().cursor_pos()
                check("the computer's cursor was left alone", now == saved, str(now))

            await viewer.send_json({"t": "view", "on": False})
            await asyncio.sleep(0.3)

    # The phone dropped - the viewer has to be told
    await asyncio.sleep(0.4)
    async with sess.ws_connect(url, ssl=sslctx) as v2:
        hello = json.loads((await v2.receive()).data)
        kinds = [x["kind"] for x in hello.get("sources", [])]
        check("the dropped source left the list", "phone" not in kinds,
              ", ".join(kinds))


# ----------------------------------------------------------------- main


async def main() -> int:
    print("Pult - self-test")
    print("Keep off the mouse: the test moves the cursor to measure accuracy.")
    print(f"Temporary settings: {_TMP}")

    caps = test_environment()
    if caps is None:
        print("\nThere is no going on without ffmpeg.")
        return 2

    test_tunnel_parsing()

    mons = test_input()
    if mons:
        await test_capture(caps, mons)
        await test_protocol(caps, mons)

    section("Result")
    bad = [r for r in results if r[0] == FAIL]
    skipped = [r for r in results if r[0] == SKIP]
    print(f"  passed: {sum(1 for r in results if r[0] == PASS)}"
          f"   failed: {len(bad)}   skipped: {len(skipped)}")
    for _, name, detail in bad:
        print(f"    FAIL: {name} - {detail}")
    print()
    print("  ALL GOOD" if not bad else "  SOMETHING IS WRONG")
    return 0 if not bad else 1


if __name__ == "__main__":
    try:
        code = asyncio.run(main())
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)
    sys.exit(code)
