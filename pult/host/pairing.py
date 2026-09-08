"""
Telefonni ulash sahifasi.

Kompyuterning o'z brauzerida ochiladi va QR kod ko'rsatadi. Shu tariqa
uzun kalitni qo'lda ko'chirish kerak bo'lmaydi - telefon kamerasi bilan
skanerlash yetarli.

QR kod segno bilan yasaladi: u sof Python, tashqi ikkilik fayllarga
bog'liq emas, shuning uchun dasturni bitta .exe qilib yig'ishga xalaqit
bermaydi.
"""
from __future__ import annotations

import html
import io

from ..config import Config


def qr_svg(data: str, scale: int = 8) -> str:
    try:
        import segno
    except ImportError:
        return '<p class="warn">QR kod uchun "segno" kutubxonasi kerak: pip install segno</p>'

    # segno SVG'ni bayt oqimiga yozadi, shuning uchun BytesIO
    buf = io.BytesIO()
    segno.make(data, error="m").save(
        buf, kind="svg", scale=scale, border=2,
        dark="#0b0d10", light="#ffffff", xmldecl=False, svgns=True,
    )
    return buf.getvalue().decode("utf-8")


def render_pair_page(target: str, urls: list[str], cfg: Config, fingerprint: str) -> str:
    alt = "".join(
        f'<li><code>{html.escape(u)}/#k={html.escape(cfg.token)}</code></li>'
        for u in urls[1:]
    )
    alt_block = f"<details><summary>Boshqa manzillar</summary><ul>{alt}</ul></details>" if alt else ""

    return f"""<!doctype html>
<html lang="uz"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Pult - telefonni ulash</title>
<style>
  :root {{ color-scheme: dark; }}
  body {{
    margin: 0; min-height: 100vh; display: grid; place-items: center;
    background: #0b0d10; color: #e7ecf2;
    font: 15px/1.6 -apple-system, "Segoe UI", Roboto, system-ui, sans-serif;
    padding: 24px;
  }}
  .card {{
    max-width: 460px; width: 100%; background: #14181d;
    border: 1px solid #262d36; border-radius: 18px; padding: 28px; text-align: center;
  }}
  h1 {{ margin: 0 0 4px; font-size: 20px; }}
  .host {{ color: #4da3ff; font-weight: 600; }}
  .qr {{ background: #fff; padding: 14px; border-radius: 14px; display: inline-block; margin: 20px 0 8px; }}
  .qr svg {{ display: block; width: 240px; height: 240px; }}
  code {{
    background: #1c222a; padding: 3px 7px; border-radius: 6px;
    font-size: 12px; word-break: break-all; color: #b9c4d0;
  }}
  ol {{ text-align: left; color: #b9c4d0; font-size: 14px; padding-left: 22px; }}
  ol li {{ margin: 7px 0; }}
  .warn {{
    background: #241d0e; border: 1px solid #4a3a12; color: #ffd48a;
    padding: 12px 14px; border-radius: 10px; font-size: 13px; text-align: left; margin-top: 18px;
  }}
  .fp {{ color: #6f7c8b; font-size: 11px; margin-top: 14px; }}
  details {{ margin-top: 14px; text-align: left; font-size: 13px; color: #8b97a6; }}
  ul {{ padding-left: 18px; }}
</style>
</head><body>
<div class="card">
  <h1>Telefonni ulash</h1>
  <div class="host">{html.escape(cfg.host_name)}</div>

  <div class="qr">{qr_svg(target)}</div>
  <div><code>{html.escape(target)}</code></div>

  <ol>
    <li>Telefon kamerasi bilan QR kodni skanerlang.</li>
    <li>Brauzer sertifikat haqida ogohlantiradi &mdash; bu kutilgan holat.
        <b>Qo&lsquo;shimcha &rarr; Baribir davom etish</b>.</li>
    <li>Sahifa ochilgach menyudan <b>&laquo;Bosh ekranga qo&lsquo;shish&raquo;</b> ni tanlang.</li>
  </ol>

  <div class="warn">
    Bu havolada kompyuterni to&lsquo;liq boshqarish kaliti bor.
    Uni birovga yubormang. Kalit chiqib ketgan bo&lsquo;lsa, sozlamalar
    faylidagi <code>token</code> ni o&lsquo;chirib, dasturni qayta ishga
    tushiring &mdash; yangi kalit yasaladi.
  </div>

  {alt_block}
  {f'<div class="fp">Sertifikat izi: {html.escape(fingerprint)}</div>' if fingerprint else ''}
</div>
</body></html>"""
