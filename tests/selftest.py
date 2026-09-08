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
    worst = 0
    for m in mons:
        for fx, fy in ((0.5, 0.5), (0.0, 0.0), (0.99, 0.99)):
            tx = m["x"] + int(fx * (m["w"] - 1))
            ty = m["y"] + int(fy * (m["h"] - 1))
            wi.move_to(tx, ty)
            time.sleep(0.04)
            gx, gy = wi.cursor_pos()
            worst = max(worst, abs(gx - tx), abs(gy - ty))
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
                worst = 0
                for nx, ny in ((0.5, 0.5), (0.0, 0.0), (1.0, 1.0)):
                    await ws.send_json({"t": "mouse", "a": "move", "x": nx, "y": ny})
                    await asyncio.sleep(0.1)
                    gx, gy = wi.cursor_pos()
                    ex = mon["x"] + round(nx * (mon["w"] - 1))
                    ey = mon["y"] + round(ny * (mon["h"] - 1))
                    worst = max(worst, abs(gx - ex), abs(gy - ey))
                check("protokol orqali sichqoncha", worst <= 2, f"xato {worst} px")
                wi.move_to(*saved)

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

            # -- ochiq ma'lumot
            async with sess.get(f"{base}/api/info", ssl=sslctx) as r:
                info = await r.json()
                check("/api/info kalitsiz faqat nom beradi",
                      "monitors" not in info and "name" in info, str(info)[:70])
    finally:
        await server.stop()


# ----------------------------------------------------------------- main


async def main() -> int:
    print("Pult - o'z-o'zini tekshiruv")
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
