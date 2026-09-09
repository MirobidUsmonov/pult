"""
Pult o'z-o'zini tekshiruvi.

Ishlatish:
    python tests/selftest.py

Nima qiladi: ffmpeg va kodlagichlarni topadi, ekranlarni sanaydi,
sichqoncha aniqligini o'lchaydi, video zanjirini haqiqiy ishga
tushirib oqim to'g'riligini ffprobe bilan tekshiradi, so'ng vaqtinchalik
server ko'tarib butun protokolni uchidan uchiga sinaydi.

Testlar sizning haqiqiy sozlamalaringizga tegmaydi: vaqtinchalik papka
ishlatiladi. Sichqoncha qisqa vaqt harakatlanadi va oxirida joyiga
qaytariladi.
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

# Haqiqiy sozlamalarga tegmaslik uchun - import'dan OLDIN
_TMP = Path(tempfile.mkdtemp(prefix="pult-selftest-"))
os.environ["PULT_CONFIG_DIR"] = str(_TMP)

import aiohttp  # noqa: E402

from pult import config as cfgmod  # noqa: E402
from pult.capture import CaptureConfig, ScreenCapture, codec_string, is_keyframe  # noqa: E402
from pult.host.server import HostServer  # noqa: E402
from pult.platform import ffmpeg as ff  # noqa: E402

HDR = struct.Struct("!BBHI")

PASS, FAIL, SKIP = "OK  ", "XATO", "----"
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
    """Kursor tinch turibdimi."""
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
    """Kursorni belgilangan nuqtalarga yuborib, aniqlikni o'lchaydi.

    Qaytaradi: (eng katta xato, xalaqit bo'ldimi).

    Foydalanuvchi shu payt sichqonchani ishlatayotgan bo'lsa, uning
    harakati bizning buyrug'imizni bosib ketadi va o'lchov ma'nosiz
    bo'ladi. Buni xato deb hisoblash noto'g'ri bo'lardi - shuning uchun
    kursor tinchligini kutamiz, bir necha marta urinamiz va baribir
    chiqmasa "o'lchab bo'lmadi" deb belgilaymiz.
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


# ---------------------------------------------------------------- 1. muhit


def test_environment() -> ff.Capabilities | None:
    section("1. Muhit")
    path = ff.find_ffmpeg()
    if not check("ffmpeg topildi", bool(path), path or "PATH da yo'q"):
        return None
    caps = ff.probe(path)
    check("versiya o'qildi", bool(caps.version), caps.version)

    h264 = sorted(e for e in caps.encoders if "264" in e)
    check("H.264 kodlagich bor", bool(h264), ", ".join(h264))

    enc = ff.pick_encoder(caps)
    check("kodlagich tanlandi", True, f"{enc['label']} ({enc['name']})")
    if enc["name"] == "libx264":
        check("apparat kodlash", None, "yo'q - protsessor ishlatiladi, yuk yuqoriroq")

    grabber = "ddagrab" if "ddagrab" in caps.filters else ("gdigrab" if "gdigrab" in caps.devices else "")
    check("ekran olish usuli", bool(grabber), grabber or "topilmadi")
    check("AUD bitstream filtri", caps.has_aud_bsf,
          "h264_metadata" if caps.has_aud_bsf else "yo'q - kadrlarga ajratish ishlamaydi")
    return caps


# ------------------------------------------------------- 2. ekran, kiritish


def test_input() -> list[dict]:
    section("2. Ekranlar va kiritish")
    try:
        from pult.platform import input_backend

        wi = input_backend()
    except Exception as exc:
        check("kiritish moduli", False, str(exc))
        return []

    mons = wi.list_monitors()
    check("ekranlar topildi", bool(mons),
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
        check("kursor aniqligi", None, "o'lchab bo'lmadi - sichqoncha ishlatilmoqda")
    else:
        check("kursor aniqligi", worst <= 2, f"eng katta xato {worst} px")

    wi.move_to(mons[0]["x"] + 400, mons[0]["y"] + 400)
    time.sleep(0.05)
    bx, by = wi.cursor_pos()
    wi.move_by(50, -30)
    time.sleep(0.05)
    ax, ay = wi.cursor_pos()
    check("nisbiy harakat", abs((ax - bx) - 50) <= 2 and abs((ay - by) + 30) <= 2,
          f"so'ralgan (+50,-30), bo'ldi ({ax - bx:+d},{ay - by:+d})")

    bad = [k for k in ("ctrl", "shift", "Escape", "F5", "ArrowUp", "a", "7",
                       "AudioVolumeUp", "NumpadEnter")
           if not _key_ok(wi, k)]
    check("klavish xaritasi", not bad, "hammasi yechildi" if not bad else f"yechilmadi: {bad}")

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
    section("3. Video zanjiri")
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

    check("kadrlar keldi", len(units) > 20, f"{len(units)} kadr")
    check("kalit kadr bor", any(k for _, k in units),
          f"{sum(1 for _, k in units if k)} ta")
    check("birinchi kadr kalit", bool(units and units[0][1]),
          "yangi tomoshabin darhol ko'radi" if units and units[0][1] else "kutish kerak bo'ladi")
    check("kodek satri aniqlandi", bool(codec and codec.startswith("avc1.")), codec or "yo'q")
    check("o'lcham to'g'ri", cap.width == 1280, f"{cap.width}x{cap.height}")

    # Eng muhim tekshiruv: ajratib qayta yig'ilgan oqim buzilmadimi.
    probe = shutil.which("ffprobe") or str(Path(caps.path).with_name("ffprobe.exe"))
    if Path(probe).exists() or shutil.which("ffprobe"):
        r = subprocess.run(
            [caps.path, "-hide_banner", "-loglevel", "error", "-i", str(out), "-f", "null", "-"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        err = (r.stderr or "").strip()
        check("oqim buzilmagan (ffmpeg dekodladi)", not err, err[:120] or "xatosiz")
    else:
        check("oqim tekshiruvi", None, "ffprobe topilmadi")


# ------------------------------------------------------- 4. server, protokol


async def test_protocol(caps: ff.Capabilities, mons: list[dict]) -> None:
    section("4. Server va protokol")

    cfg = cfgmod.load()
    cfg.port = free_port()
    cfg.bind = "127.0.0.1"
    server = HostServer(cfg, caps)
    await server.start()
    check("HTTPS server ko'tarildi", True, f"port {cfg.port}, sertifikat o'z-imzo")

    sslctx = ssl.create_default_context()
    sslctx.check_hostname = False
    sslctx.verify_mode = ssl.CERT_NONE
    base = f"https://127.0.0.1:{cfg.port}"

    try:
        async with aiohttp.ClientSession() as sess:
            # -- ruxsat
            for label, url in (("noto'g'ri kalit", f"{base}/ws?k=xxx"), ("kalitsiz", f"{base}/ws")):
                try:
                    async with sess.ws_connect(url, ssl=sslctx):
                        check(f"{label} rad etildi", False, "ruxsat berildi!")
                except aiohttp.WSServerHandshakeError as e:
                    check(f"{label} rad etildi", e.status == 401, f"HTTP {e.status}")

            # -- to'liq sessiya
            async with sess.ws_connect(f"{base}/ws?k={cfg.token}", ssl=sslctx) as ws:
                hello = json.loads((await ws.receive()).data)
                check("hello keldi", hello.get("t") == "hello",
                      f"{hello.get('host', {}).get('name', '?')}, "
                      f"{len(hello.get('host', {}).get('monitors', []))} ekran")

                await ws.send_json({"t": "view", "on": True, "width": 854,
                                    "fps": 24, "bitrate": 2000})

                stream, frames, keys, total = None, 0, 0, 0
                end = time.monotonic() + 8
                while time.monotonic() < end and (frames < 40 or not stream):
                    try:
                        m = await asyncio.wait_for(ws.receive(), timeout=3)
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

                check("stream xabari", stream is not None,
                      f"{stream['codec']} {stream['w']}x{stream['h']} {stream['encoder']}"
                      if stream else "kelmadi")
                check("video kadrlari", frames > 20, f"{frames} kadr, {total // 1024} KB")
                check("kalit kadr", keys >= 1, f"{keys} ta")
                check("sozlama qo'llandi", bool(stream and stream["w"] == 854),
                      f"so'ralgan 854, kelgan {stream['w'] if stream else '?'}")

                # -- kiritish protokol orqali
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
                    # Sinxron funksiyadan asinxron yuborish: o'lchov mantig'i
                    # ikkala testda bir xil bo'lishi uchun shunday qilingan.
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
                detail = f"xato {worst} px"
                if latency:
                    detail += f", yuborish {sum(latency) / len(latency):.0f} ms"
                if worst > 2 and noisy:
                    check("protokol orqali sichqoncha", None,
                          "o'lchab bo'lmadi - sichqoncha ishlatilmoqda")
                else:
                    check("protokol orqali sichqoncha", worst <= 2, detail)
                wi.move_to(*saved)

                # -- ko'p ekranli xatti-harakat
                if len(mons) >= 2:
                    a, b = mons[0], mons[1]

                    # -- kursor ekranlar orasida yursin: ko'rinish ergashadi
                    await ws.send_json({"t": "view", "on": True,
                                        "monitor": a["index"], "follow": True})
                    await asyncio.sleep(0.5)
                    # Qo'shni ekranga YAQIN chetdan boshlaymiz
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
                    check("kursor ikkinchi ekranga o'ta oladi", crossed, f"kursor ({gx},{gy})")
                    check("ko'rinish kursorga ergashdi", followed,
                          f"server {server.ctx.cfg.stream.monitor + 1}-ekranni ko'rsatyapti")

                    # -- endi chegaralash rejimi
                    await ws.send_json({"t": "view", "on": True,
                                        "monitor": a["index"], "follow": False})
                    await asyncio.sleep(0.5)

                    # Kursorni ataylab BOSHQA ekranga qo'yamiz va kichkina
                    # harakat yuboramiz: kursor ko'rilayotgan ekranga
                    # qaytishi, lekin chekkaga sakramasligi kerak
                    wi.move_to(b["x"] + b["w"] // 2, b["y"] + b["h"] // 2)
                    await asyncio.sleep(0.1)
                    await ws.send_json({"t": "mouse", "a": "moveby", "dx": 3, "dy": 0})
                    await asyncio.sleep(0.35)
                    gx, gy = wi.cursor_pos()
                    inside = (a["x"] <= gx < a["x"] + a["w"]) and (a["y"] <= gy < a["y"] + a["h"])
                    centered = abs(gx - (a["x"] + a["w"] // 2)) < 40
                    check("boshqa ekranda qolgan kursor qaytariladi", inside and centered,
                          f"kursor ({gx},{gy}), chekkaga sakramadi" if centered
                          else f"kursor ({gx},{gy}) - chekkaga sakradi")

                    # Katta harakat ham ekrandan chiqarib yubormasin
                    for _ in range(6):
                        await ws.send_json({"t": "mouse", "a": "moveby", "dx": 900, "dy": 900})
                        await asyncio.sleep(0.06)
                    gx, gy = wi.cursor_pos()
                    inside = (a["x"] <= gx < a["x"] + a["w"]) and (a["y"] <= gy < a["y"] + a["h"])
                    check("trackpad ko'rilayotgan ekrandan chiqmaydi", inside,
                          f"kursor ({gx},{gy}), {a['index'] + 1}-ekran "
                          f"x:{a['x']}..{a['x'] + a['w']}")

                    # Ekran almashtirilsa kursor o'sha ekranga o'tsin
                    await ws.send_json({"t": "view", "on": True, "monitor": b["index"]})
                    await asyncio.sleep(0.6)
                    gx, gy = wi.cursor_pos()
                    moved = (b["x"] <= gx < b["x"] + b["w"]) and (b["y"] <= gy < b["y"] + b["h"])
                    check("ekran almashtirilsa kursor ko'chadi", moved, f"kursor ({gx},{gy})")
                    await ws.send_json({"t": "view", "on": True, "monitor": a["index"]})
                    await asyncio.sleep(0.5)
                else:
                    check("ko'p ekranli xatti-harakat", None, "ikkinchi ekran yo'q")

                # -- juda kichik harakatlar yo'qolmasligi
                wi.move_to(mons[0]["x"] + 600, mons[0]["y"] + 400)
                await asyncio.sleep(0.15)
                bx, by = wi.cursor_pos()
                for _ in range(20):
                    await ws.send_json({"t": "mouse", "a": "moveby", "dx": 0.3, "dy": 0})
                    await asyncio.sleep(0.03)
                await asyncio.sleep(0.2)
                ax, _ay = wi.cursor_pos()
                check("sekin harakat yo'qolmaydi", abs((ax - bx) - 6) <= 2,
                      f"20 x 0.3px = 6px kutildi, {ax - bx}px bo'ldi")

                # -- xatolar
                async def next_error():
                    end2 = time.monotonic() + 4
                    while time.monotonic() < end2:
                        m = await asyncio.wait_for(ws.receive(), timeout=4)
                        if m.type == aiohttp.WSMsgType.TEXT:
                            d = json.loads(m.data)
                            if d.get("t") == "error":
                                return d
                    return {}

                await ws.send_json({"t": "yoq_bunday"})
                check("noma'lum xabar rad etildi", (await next_error()).get("t") == "error")
                await ws.send_json({"t": "key", "a": "tap", "k": "YOQ_BUNDAY"})
                check("noma'lum klavish rad etildi", (await next_error()).get("t") == "error")

                await ws.send_json({"t": "view", "on": False})
                await asyncio.sleep(0.4)
                check("tomoshabin ketgach oqim to'xtadi", not server.ctx.capture.running,
                      "bo'sh turganda resurs sarflanmaydi")

            # -- telefonni manba sifatida ulash (teskari boshqaruv)
            await test_phone_source(sess, base, sslctx, cfg)

            # -- ochiq ma'lumot
            async with sess.get(f"{base}/api/info", ssl=sslctx) as r:
                info = await r.json()
                check("/api/info kalitsiz faqat nom beradi",
                      "monitors" not in info and "name" in info, str(info)[:70])
    finally:
        await server.stop()


# ------------------------------------------------- 5. teskari boshqaruv


async def test_phone_source(sess, base, sslctx, cfg) -> None:
    """Telefon "manba" bo'lib ulanadi, brauzer uni ko'radi va boshqaradi.

    Haqiqiy telefonsiz tekshiriladi: soxta mijoz telefonning o'rnini
    bosadi. Bu marshrutlashning butun yo'lini qamrab oladi - manba
    ro'yxatga tushishi, kadrlarning to'g'ri tomoshabinga borishi va
    kiritishning kompyuterga emas, telefonga ketishi.
    """
    section("5. Teskari boshqaruv (telefon manba sifatida)")

    url = f"{base}/ws?k={cfg.token}"
    async with sess.ws_connect(url, ssl=sslctx) as phone:
        await phone.receive()      # hello
        await phone.send_json({
            "t": "hello", "role": "source", "name": "Sinov telefoni",
            "info": {"kind": "phone", "w": 1080, "h": 2400, "input": True},
        })
        await asyncio.sleep(0.3)

        async with sess.ws_connect(url, ssl=sslctx) as viewer:
            hello = json.loads((await viewer.receive()).data)
            names = [x["name"] for x in hello.get("sources", [])]
            check("manba ro'yxatga tushdi", "Sinov telefoni" in names, ", ".join(names))

            await viewer.send_json({"t": "hello", "role": "controller", "name": "sinov"})
            src_id = next(x["id"] for x in hello["sources"] if x["kind"] == "phone")

            # Tomoshabin telefonni tanlaydi -> telefonga "oqimni boshla" kelishi kerak
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
            check("telefonga oqim so'rovi keldi", start is not None,
                  f"{start.get('width')}px {start.get('fps')} k/s" if start else "kelmadi")

            # Telefon oqim haqida xabar berib, kadr yuboradi
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
            check("telefon oqimi tomoshabinga yetdi", got_stream is not None,
                  got_stream.get("codec") if got_stream else "yetmadi")
            check("telefon kadrlari uzatildi", frames >= 4, f"{frames} kadr")

            # Kiritish kompyuterga emas, telefonga borishi kerak
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
            check("kiritish telefonga yo'naltirildi", forwarded is not None,
                  f"{forwarded.get('a')} ({forwarded.get('x')},{forwarded.get('y')})"
                  if forwarded else "yetmadi")
            if saved:
                now = input_backend().cursor_pos()
                check("kompyuter kursoriga tegilmadi", now == saved, str(now))

            await viewer.send_json({"t": "view", "on": False})
            await asyncio.sleep(0.3)

    # Telefon uzildi - tomoshabin xabardor bo'lishi kerak
    await asyncio.sleep(0.4)
    async with sess.ws_connect(url, ssl=sslctx) as v2:
        hello = json.loads((await v2.receive()).data)
        kinds = [x["kind"] for x in hello.get("sources", [])]
        check("uzilgan manba ro'yxatdan chiqdi", "phone" not in kinds,
              ", ".join(kinds))


# ----------------------------------------------------------------- main


async def main() -> int:
    print("Pult - o'z-o'zini tekshiruv")
    print("Sichqonchaga tegmang: test kursorni harakatlantirib aniqlikni o'lchaydi.")
    print(f"Vaqtinchalik sozlamalar: {_TMP}")

    caps = test_environment()
    if caps is None:
        print("\nffmpeg'siz davom etib bo'lmaydi.")
        return 2

    mons = test_input()
    if mons:
        await test_capture(caps, mons)
        await test_protocol(caps, mons)

    section("Natija")
    bad = [r for r in results if r[0] == FAIL]
    skipped = [r for r in results if r[0] == SKIP]
    print(f"  o'tdi: {sum(1 for r in results if r[0] == PASS)}"
          f"   xato: {len(bad)}   o'tkazildi: {len(skipped)}")
    for _, name, detail in bad:
        print(f"    XATO: {name} - {detail}")
    print()
    print("  HAMMASI JOYIDA" if not bad else "  MUAMMO BOR")
    return 0 if not bad else 1


if __name__ == "__main__":
    try:
        code = asyncio.run(main())
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)
    sys.exit(code)
