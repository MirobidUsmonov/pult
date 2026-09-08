"""
ffmpeg imkoniyatlarini aniqlash va buyruq qatorini qurish.

Maqsad - universallik: dastur qaysi kompyuterda ishga tushmasin, o'zi
mavjud eng tez yo'lni topsin. NVIDIA bo'lsa nvenc, Intel bo'lsa qsv,
AMD bo'lsa amf, hech biri bo'lmasa protsessorda libx264.
"""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field

# Kodlagichlar afzallik tartibida. Har biri uchun past kechikishga
# sozlangan argumentlar. {br} - kbit/s, {gop} - kalit kadr oralig'i.
ENCODERS: list[dict] = [
    {
        "name": "h264_nvenc",
        "label": "NVIDIA NVENC",
        "pix_fmt": "nv12",
        "args": [
            "-c:v", "h264_nvenc",
            "-preset", "p1",          # eng tez
            "-tune", "ll",            # past kechikish
            "-zerolatency", "1",
            "-delay", "0",
            "-rc", "vbr",             # statik ekranda trafik deyarli nolga tushadi
            "-forced-idr", "1",
            "-profile:v", "main",
        ],
    },
    {
        "name": "h264_qsv",
        "label": "Intel Quick Sync",
        "pix_fmt": "nv12",
        "args": [
            "-c:v", "h264_qsv",
            "-preset", "veryfast",
            "-low_delay_brc", "1",
            "-async_depth", "1",
            "-profile:v", "main",
        ],
    },
    {
        "name": "h264_amf",
        "label": "AMD AMF",
        "pix_fmt": "nv12",
        "args": [
            "-c:v", "h264_amf",
            "-usage", "lowlatency",
            "-quality", "speed",
            "-rc", "vbr_latency",
            "-profile:v", "main",
        ],
    },
    {
        "name": "h264_mf",
        "label": "Windows Media Foundation",
        "pix_fmt": "nv12",
        "args": ["-c:v", "h264_mf", "-rate_control", "cbr"],
    },
    {
        "name": "libx264",
        "label": "Protsessor (x264)",
        "pix_fmt": "yuv420p",
        "args": [
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-tune", "zerolatency",
            "-profile:v", "baseline",
        ],
    },
]


@dataclass
class Capabilities:
    path: str
    version: str = ""
    encoders: set[str] = field(default_factory=set)
    filters: set[str] = field(default_factory=set)
    bsfs: set[str] = field(default_factory=set)
    devices: set[str] = field(default_factory=set)

    @property
    def has_aud_bsf(self) -> bool:
        """AUD qo'shuvchi bitstream filtri - oqimni kadrlarga ajratish uchun.

        Kodlagichning o'z -aud sozlamasiga tayanmaymiz: u faqat nvenc'da bor.
        h264_metadata esa har qanday kodlagich chiqishida ishlaydi.
        """
        return "h264_metadata" in self.bsfs


def _run(path: str, *args: str) -> str:
    """ffmpeg'ni ma'lumot olish uchun chaqiradi (konsol oynasisiz)."""
    flags = 0
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        flags = subprocess.CREATE_NO_WINDOW
    try:
        out = subprocess.run(
            [path, "-hide_banner", *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            creationflags=flags,
        )
        return (out.stdout or "") + (out.stderr or "")
    except Exception:
        return ""


def find_ffmpeg(explicit: str | None = None) -> str | None:
    """ffmpeg'ni topadi: sozlamadagi yo'l -> PATH -> yonidagi papka."""
    import os
    import sys

    candidates = []
    if explicit:
        candidates.append(explicit)
    found = shutil.which("ffmpeg")
    if found:
        candidates.append(found)
    # Dastur .exe qilib yig'ilgan bo'lsa - yonida turgan ffmpeg
    base = os.path.dirname(sys.executable if getattr(sys, "frozen", False) else __file__)
    for rel in ("ffmpeg.exe", "ffmpeg", os.path.join("bin", "ffmpeg.exe")):
        candidates.append(os.path.join(base, rel))
    for c in candidates:
        if c and os.path.isfile(c):
            return c
        if c and shutil.which(c):
            return shutil.which(c)
    return None


def probe(path: str) -> Capabilities:
    caps = Capabilities(path=path)

    head = _run(path, "-version").splitlines()
    if head:
        caps.version = head[0].replace("ffmpeg version ", "").split(" ")[0]

    for line in _run(path, "-encoders").splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0].startswith(("V", "A", "S")) and len(parts[0]) == 6:
            caps.encoders.add(parts[1])

    for line in _run(path, "-filters").splitlines():
        parts = line.split()
        if len(parts) >= 2:
            caps.filters.add(parts[1])

    caps.bsfs = {ln.strip() for ln in _run(path, "-bsfs").splitlines() if ln.strip() and " " not in ln.strip()}

    for line in _run(path, "-devices").splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] in ("D", "E", "DE"):
            caps.devices.add(parts[1])

    return caps


def pick_encoder(caps: Capabilities, prefer: str | None = None) -> dict:
    """Mavjud eng tez kodlagichni tanlaydi.

    prefer berilgan bo'lsa va u mavjud bo'lsa - o'sha ishlatiladi. Bu
    sozlamalarda foydalanuvchi qo'lda tanlashi uchun.
    """
    if prefer:
        for enc in ENCODERS:
            if enc["name"] == prefer and enc["name"] in caps.encoders:
                return enc
    for enc in ENCODERS:
        if enc["name"] in caps.encoders:
            return enc
    # Bu holat deyarli bo'lmaydi: libx264 har qanday yig'mada bor
    return ENCODERS[-1]


def rate_args(encoder: dict, bitrate_kbps: int, gop: int) -> list[str]:
    """Bitreyt va kalit kadr argumentlari - kodlagichdan qat'i nazar bir xil."""
    br = int(bitrate_kbps)
    args = [
        "-b:v", f"{br}k",
        "-maxrate", f"{int(br * 1.5)}k",
        "-bufsize", f"{max(br // 2, 200)}k",
        "-g", str(int(gop)),
        "-bf", "0",  # B-kadrlar kechikish qo'shadi, jonli oqimda kerak emas
    ]
    return args
