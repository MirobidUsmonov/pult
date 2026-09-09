"""
Detecting ffmpeg's capabilities and building the command line.

The goal is portability: wherever the program runs, it should find
the fastest path available on its own. NVIDIA gets nvenc, Intel qsv,
AMD amf, and with none of them it falls back to libx264 on the CPU.
"""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field

# Encoders in order of preference. For each one the settings are
# tuned arguments. {br} is kbit/s, {gop} the key-frame interval.
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
            "-rc", "vbr",             # on a static screen traffic drops to almost nothing
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
        """The AUD-inserting bitstream filter, used to split the stream
        into access units.

        We do not rely on the encoder's own -aud option: only nvenc has it.
        h264_metadata works on the output of any encoder.
        """
        return "h264_metadata" in self.bsfs


def _run(path: str, *args: str) -> str:
    """Calls ffmpeg to read information out of it (no console window)."""
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
    """Finds ffmpeg: the configured path -> PATH -> the folder beside us."""
    import os
    import sys

    candidates = []
    if explicit:
        candidates.append(explicit)
    found = shutil.which("ffmpeg")
    if found:
        candidates.append(found)
    # When the program is built as an .exe, the ffmpeg next to it
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
    """Picks the fastest encoder available.

    When prefer is given and present, that one is used - this is how the
    user picks one by hand in the settings.
    """
    if prefer:
        for enc in ENCODERS:
            if enc["name"] == prefer and enc["name"] in caps.encoders:
                return enc
    for enc in ENCODERS:
        if enc["name"] in caps.encoders:
            return enc
    # This should almost never happen: libx264 is in every build
    return ENCODERS[-1]


def rate_args(encoder: dict, bitrate_kbps: int, gop: int) -> list[str]:
    """Bitrate and key-frame arguments, the same for every encoder."""
    br = int(bitrate_kbps)
    args = [
        "-b:v", f"{br}k",
        "-maxrate", f"{int(br * 1.5)}k",
        "-bufsize", f"{max(br // 2, 200)}k",
        "-g", str(int(gop)),
        "-bf", "0",  # B-frames add latency, which live streaming does not want
    ]
    return args
