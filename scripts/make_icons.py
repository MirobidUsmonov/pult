"""
Ilova ikonkalarini yasaydi.

Alohida grafik muharrir kerak bo'lmasligi uchun ikonkalar kod bilan
chiziladi - shunda ranglarni o'zgartirish uchun shu faylni tahrirlash
kifoya va natija har safar bir xil chiqadi.

Ishlatish:  python scripts/make_icons.py
"""
from __future__ import annotations

import os
import sys

from PIL import Image, ImageDraw

BG = (11, 13, 16, 255)
ACCENT = (77, 163, 255, 255)
DIM = (77, 163, 255, 90)

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web", "icons")


def draw_icon(size: int, maskable: bool = False) -> Image.Image:
    # 4 barobar kattaroq chizib, keyin kichraytiramiz: chekkalar silliq chiqadi
    s = size * 4
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    if maskable:
        # Maskable ikonka: fon butun maydonni to'ldiradi, tasvir markazda
        # kichikroq bo'ladi - tizim uni doira qilib kessa ham buzilmaydi.
        d.rectangle([0, 0, s, s], fill=BG)
        scale = 0.62
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

    # Signal yoylari - masofadan boshqarish ishorasi.
    # Markaz monitorning o'ng-yuqori burchagida: yoylar ekran ichiga
    # kirmasdan, undan tashqariga tarqaladi.
    ax, ay = left + mw - px(1), top + px(1)
    for i, radius in enumerate((px(9), px(17), px(25))):
        box = [ax - radius, ay - radius, ax + radius, ay + radius]
        d.arc(box, start=-84, end=-6, fill=ACCENT if i == 0 else DIM, width=int(px(3.6)))

    return img.resize((size, size), Image.LANCZOS)


def main() -> int:
    os.makedirs(OUT, exist_ok=True)
    jobs = [
        ("icon-192.png", 192, False),
        ("icon-512.png", 512, False),
        ("icon-maskable.png", 512, True),
        ("favicon.png", 64, False),
    ]
    for name, size, maskable in jobs:
        path = os.path.join(OUT, name)
        draw_icon(size, maskable).save(path)
        print(f"  {name}  {size}x{size}  {os.path.getsize(path)} bayt")
    print("ikonkalar tayyor:", OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
