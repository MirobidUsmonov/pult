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
import re
from urllib.parse import quote

from ..config import Config


def responsive(svg: str) -> str:
    """SVG'ga viewBox qo'shib, qat'iy o'lchamini olib tashlaydi.

    segno SVG'ni qat'iy piksel o'lchami bilan yozadi va viewBox
    qo'shmaydi. Bunday SVG'ga CSS orqali kichikroq o'lcham berilsa u
    KICHRAYMAYDI - kesiladi. QR kodning o'ng va past qismi yo'qoladi,
    ya'ni kod umuman o'qilmaydi.

    Buni ko'z bilan sezish qiyin: kesilgan QR ham QR kodga o'xshab
    turadi, faqat skanerlanmaydi. Havola uzaygani sayin (masalan
    tunnel manzili qo'shilganda) kod kattaroq bo'ladi va ko'proq
    qirqiladi.
    """
    m = re.search(r'<svg[^>]*?width="([0-9.]+)"[^>]*?height="([0-9.]+)"', svg)
    if not m:
        return svg
    width, height = m.group(1), m.group(2)
    head_end = svg.index(">")
    head = svg[:head_end]
    head = re.sub(r'\s(width|height)="[0-9.]+"', "", head)
    head += f' viewBox="0 0 {width} {height}" preserveAspectRatio="xMidYMid meet"'
    return head + svg[head_end:]


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
    return responsive(buf.getvalue().decode("utf-8"))


def app_link(target: str) -> str:
    """Pult ilovasi uchun havola.

    Oddiy https havolasini kamera skanerlasa brauzer ochiladi. Ilova esa
    o'z sxemasi bilan ochiladi, shuning uchun ikkita QR kerak - qaysi biri
    kerakligini foydalanuvchi tanlaydi.
    """
    return "pult://add?u=" + quote(target, safe="")


def render_pair_page(target: str, urls: list[str], cfg: Config, fingerprint: str) -> str:
    alt = "".join(
        f'<li><code>{html.escape(u)}/#k={html.escape(cfg.token)}</code></li>'
        for u in urls[1:]
    )
    alt_block = f"<details><summary>Boshqa manzillar</summary><ul>{alt}</ul></details>" if alt else ""
    # Telegram sozlangan bo'lsagina tugma ko'rsatiladi: ishlamaydigan
    # tugma foydalanuvchini adashtiradi
    telegram = bool(cfg.telegram.bot_token and cfg.telegram.chat_id)

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
  .qr {{ background: #fff; padding: 14px; border-radius: 14px; display: inline-block; margin: 18px 0 10px; }}
  /* Balandlik berilmaydi: viewBox bor, shuning uchun SVG kengligiga
     qarab o'zi mutanosib kichrayadi. */
  .qr svg {{ display: block; width: min(300px, 60vw); height: auto; }}
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
  .tabs {{ display: flex; gap: 6px; justify-content: center; margin-top: 16px; }}
  .tab {{
    background: #1c222a; border: 1px solid #262d36; color: #b9c4d0;
    padding: 7px 16px; border-radius: 999px; font-size: 13px; cursor: pointer;
  }}
  .tab.on {{ background: #4da3ff; border-color: #4da3ff; color: #04121f; }}
  .acts {{ display: flex; gap: 8px; justify-content: center; margin: 12px 0 4px; flex-wrap: wrap; }}
  .act {{
    background: #1c222a; border: 1px solid #2f3946; color: #cfd8e3;
    padding: 9px 16px; border-radius: 10px; font-size: 13px; cursor: pointer;
  }}
  .act:hover {{ border-color: #4da3ff; color: #e7ecf2; }}
  .said {{ min-height: 18px; font-size: 12px; color: #6fd08c; margin-bottom: 4px; }}
  .said.bad {{ color: #ff9b9b; }}
  details p {{ margin: 6px 0; }}
  [hidden] {{ display: none !important; }}
</style>
</head><body>
<div class="card">
  <h1>Telefonni ulash</h1>
  <div class="host">{html.escape(cfg.host_name)}</div>

  <div class="tabs">
    <button class="tab on" data-for="qr-app">Pult ilovasi</button>
    <button class="tab" data-for="qr-web">Brauzer</button>
  </div>

  <div class="qr" id="qr-app">{qr_svg(app_link(target))}</div>
  <div class="qr" id="qr-web" hidden>{qr_svg(target)}</div>

  <div><code id="link">{html.escape(target)}</code></div>

  <div class="acts">
    <button class="act" id="copy">Havolani nusxalash</button>
    <button class="act" id="send" {"" if telegram else "hidden"}>Telegramga yuborish</button>
  </div>
  <div class="said" id="said"></div>

  <ol id="steps-app">
    <li>Telefonda Pult ilovasi bo&lsquo;lsin.</li>
    <li>Kamera bilan QR kodni skanerlang &mdash; ilova o&lsquo;zi ochiladi.</li>
    <li>Sertifikat izi so&lsquo;ralsa <b>&laquo;Ishonaman&raquo;</b> ni bosing.
        Bir marta so&lsquo;raladi.</li>
  </ol>

  <ol id="steps-web" hidden>
    <li>Telefon kamerasi bilan QR kodni skanerlang.</li>
    <li>Brauzer sertifikat haqida ogohlantirsa: <b>Qo&lsquo;shimcha &rarr;
        Baribir davom etish</b>.</li>
    <li>Menyudan <b>&laquo;Bosh ekranga qo&lsquo;shish&raquo;</b> ni tanlang.</li>
  </ol>

  <details>
    <summary>Kamera ishlamasa</summary>
    <p><b>Telegramga yuborish</b> ni bosing. Telefonda kelgan havolani
    bosib turing &rarr; <b>Ulashish</b> &rarr; <b>Pult</b>. Kompyuter
    ro&lsquo;yxatga o&lsquo;zi qo&lsquo;shiladi.</p>
    <p>Yoki havolani nusxalab, ilovadagi <b>&laquo;+ Kompyuter
    qo&lsquo;shish&raquo;</b> ga qo&lsquo;ying.</p>
  </details>

  <div class="warn">
    Bu havolada kompyuterni to&lsquo;liq boshqarish kaliti bor.
    Uni birovga yubormang. Kalit chiqib ketgan bo&lsquo;lsa, sozlamalar
    faylidagi <code>token</code> ni o&lsquo;chirib, dasturni qayta ishga
    tushiring &mdash; yangi kalit yasaladi.
  </div>

  {alt_block}
  {f'<div class="fp">Sertifikat izi: {html.escape(fingerprint)}</div>' if fingerprint else ''}
</div>
<script>
  document.querySelectorAll(".tab").forEach(function (t) {{
    t.onclick = function () {{
      var app = t.dataset.for === "qr-app";
      document.querySelectorAll(".tab").forEach(function (x) {{ x.classList.remove("on"); }});
      t.classList.add("on");
      document.getElementById("qr-app").hidden = !app;
      document.getElementById("qr-web").hidden = app;
      document.getElementById("steps-app").hidden = !app;
      document.getElementById("steps-web").hidden = app;
    }};
  }});

  var said = document.getElementById("said");
  function say(text, bad) {{
    said.textContent = text;
    said.className = bad ? "said bad" : "said";
  }}

  document.getElementById("copy").onclick = function () {{
    var link = document.getElementById("link").textContent;
    navigator.clipboard.writeText(link).then(
      function () {{ say("Nusxalandi"); }},
      function () {{ say("Nusxalab bo‘lmadi", true); }}
    );
  }};

  var send = document.getElementById("send");
  if (send) send.onclick = function () {{
    send.disabled = true;
    say("Yuborilmoqda…");
    fetch("/api/pair/send" + location.search, {{ method: "POST" }})
      .then(function (r) {{ return r.json(); }})
      .then(function (d) {{
        say(d.ok ? "Telegramga yuborildi" : (d.msg || "Yuborilmadi"), !d.ok);
      }})
      .catch(function () {{ say("Yuborilmadi", true); }})
      .then(function () {{ send.disabled = false; }});
  }};
</script>
</body></html>"""
