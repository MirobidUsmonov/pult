"""
Generates the app icons.

They are drawn in code so no separate graphics editor is needed -
changing the colours means editing this one file, and the result comes
out identical every time.

Usage:  python scripts/make_icons.py
"""
from __future__ import annotations

import os
import sys

from PIL import Image, ImageDraw

BG = (11, 13, 16, 255)
ACCENT = (77, 163, 255, 255)
DIM = (77, 163, 255, 90)

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web", "icons")


def draw_icon(size: int, mode: str = "app") -> Image.Image:
    """mode: "app" - with a rounded-corner background;
             "maskable" - background over the whole area, smaller artwork;
             "adaptive" - no background, artwork only (Android places it).
    """
    # Drawn four times larger and scaled down, so the edges come out smooth
    s = size * 4
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    if mode == "maskable":
        # A maskable icon: the background fills the whole area and the
        # artwork is smaller and centred, so it survives being cropped
        # into a circle.
        d.rectangle([0, 0, s, s], fill=BG)
        scale = 0.62
    elif mode == "adaptive":
        # An Android adaptive icon: the background is a separate layer
        # and only the artwork is here. The system may crop the edges,
        # so the artwork has to fit inside the central safe circle.
        scale = 0.52
    else:
        r = int(s * 0.22)
        d.rounded_rectangle([0, 0, s - 1, s - 1], radius=r, fill=BG)
        scale = 0.78

    cx, cy = s / 2, s / 2
    unit = s * scale / 100.0

    def px(v: float) -> float:
        return v * unit

    # Monitor
    mw, mh = px(58), px(38)
    left, top = cx - mw / 2, cy - mh / 2 - px(6)
    d.rounded_rectangle(
        [left, top, left + mw, top + mh],
        radius=px(5), outline=ACCENT, width=int(px(4.5)),
    )
    # Oyoq
    d.rounded_rectangle(
        [cx - px(9), top + mh + px(1), cx + px(9), top + mh + px(6)],
        radius=px(2), fill=ACCENT,
    )
    d.rounded_rectangle(
        [cx - px(16), top + mh + px(7), cx + px(16), top + mh + px(11)],
        radius=px(2), fill=ACCENT,
    )

    # The signal arcs, a nod to remote control.
    # Their centre is at the monitor's top-right corner, so they spread
    # outwards rather than across the screen.
    ax, ay = left + mw - px(1), top + px(1)
    for i, radius in enumerate((px(9), px(17), px(25))):
        box = [ax - radius, ay - radius, ax + radius, ay + radius]
        d.arc(box, start=-84, end=-6, fill=ACCENT if i == 0 else DIM, width=int(px(3.6)))

    return img.resize((size, size), Image.LANCZOS)


ANDROID_RES = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "android", "app", "src", "main", "res",
)

# Android icon sizes, by screen density
DENSITIES = {"mdpi": 1, "hdpi": 1.5, "xhdpi": 2, "xxhdpi": 3, "xxxhdpi": 4}


def make_android_icons() -> None:
    """The icons for the Android app.

    Two shapes are produced: a plain PNG for older phones and an
    adaptive icon for newer ones (background and artwork separate, so
    the system can crop it into its own shape).
    """
    if not os.path.isdir(os.path.dirname(ANDROID_RES)):
        print("  no android folder - skipped")
        return

    for name, factor in DENSITIES.items():
        # The plain icon: 48dp
        d = os.path.join(ANDROID_RES, f"mipmap-{name}")
        os.makedirs(d, exist_ok=True)
        draw_icon(int(48 * factor), "app").save(os.path.join(d, "ic_launcher.png"))
        # The adaptive icon's foreground layer: 108dp
        draw_icon(int(108 * factor), "adaptive").save(
            os.path.join(d, "ic_launcher_foreground.png"))

    anydpi = os.path.join(ANDROID_RES, "mipmap-anydpi-v26")
    os.makedirs(anydpi, exist_ok=True)
    xml = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<adaptive-icon xmlns:android="http://schemas.android.com/apk/res/android">\n'
        '    <background android:drawable="@color/ic_launcher_background" />\n'
        '    <foreground android:drawable="@mipmap/ic_launcher_foreground" />\n'
        '</adaptive-icon>\n'
    )
    with open(os.path.join(anydpi, "ic_launcher.xml"), "w", encoding="utf-8") as f:
        f.write(xml)

    values = os.path.join(ANDROID_RES, "values")
    os.makedirs(values, exist_ok=True)
    colors = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<resources>\n'
        '    <color name="ic_launcher_background">#0B0D10</color>\n'
        '</resources>\n'
    )
    with open(os.path.join(values, "ic_launcher_colors.xml"), "w", encoding="utf-8") as f:
        f.write(colors)

    print(f"  android icons: {len(DENSITIES)} densities + adaptive")


def main() -> int:
    os.makedirs(OUT, exist_ok=True)
    jobs = [
        ("icon-192.png", 192, "app"),
        ("icon-512.png", 512, "app"),
        ("icon-maskable.png", 512, "maskable"),
        ("favicon.png", 64, "app"),
    ]
    for name, size, mode in jobs:
        path = os.path.join(OUT, name)
        draw_icon(size, mode).save(path)
        print(f"  {name}  {size}x{size}  {os.path.getsize(path)} bytes")

    # For the Windows .exe: one file has to hold several sizes,
    # otherwise it looks ugly in small places such as the taskbar.
    ico = os.path.join(OUT, "pult.ico")
    sizes = [16, 24, 32, 48, 64, 128, 256]
    draw_icon(256).save(ico, format="ICO", sizes=[(s, s) for s in sizes])
    print(f"  pult.ico  {','.join(map(str, sizes))}  {os.path.getsize(ico)} bytes")

    make_android_icons()
    print("icons ready:", OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
