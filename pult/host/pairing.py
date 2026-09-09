"""
The pairing page.

It opens in the computer's own browser and shows a QR code, so the long
key never has to be copied by hand - scanning it with the phone's camera
is enough.

The QR code is made with segno: pure Python, no external binaries, so it
does not get in the way of building the program into a single .exe.
"""
from __future__ import annotations

import html
import io
import re
from urllib.parse import quote

from ..config import Config

# Deliberately at the top rather than inside the function. A one-file
# build reads its modules out of its own .exe, and a self-update
# replaces that file. After that a lazily imported module can no longer
# be read ("incorrect header check"). Importing at startup avoids it.
try:
    import segno
except ImportError:  # pragma: no cover - the QR code is optional
    segno = None


def responsive(svg: str) -> str:
    """Adds a viewBox to the SVG and drops its fixed size.

    segno writes the SVG with a fixed pixel size and no viewBox. Give
    such an SVG a smaller size through CSS and it does not SCALE - it is
    CROPPED. The right and bottom of the QR code disappear, and the code
    stops being readable at all.

    This is hard to catch by eye: a cropped QR still looks like a QR, it
    simply does not scan. The longer the link (a tunnel address, for
    instance) the bigger the code and the more of it is cut off.
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
    if segno is None:
        return '<p class="warn">The QR code needs "segno": pip install segno</p>'

    # segno writes the SVG to a byte stream, hence BytesIO
    buf = io.BytesIO()
    segno.make(data, error="m").save(
        buf, kind="svg", scale=scale, border=2,
        dark="#0b0d10", light="#ffffff", xmldecl=False, svgns=True,
    )
    return responsive(buf.getvalue().decode("utf-8"))


def app_link(target: str) -> str:
    """A link that opens the Pult app.

    Scanning a plain https link with the camera opens the browser; the
    app opens through its own scheme. Hence two QR codes, with the user
    picking the one they need.
    """
    return "pult://add?u=" + quote(target, safe="")


def render_pair_page(target: str, urls: list[str], cfg: Config, fingerprint: str) -> str:
    alt = "".join(
        f'<li><code>{html.escape(u)}/#k={html.escape(cfg.token)}</code></li>'
        for u in urls[1:]
    )
    alt_block = f"<details><summary>Other addresses</summary><ul>{alt}</ul></details>" if alt else ""
    # The button only appears when Telegram is set up: a button that
    # does nothing is worse than no button
    telegram = bool(cfg.telegram.bot_token and cfg.telegram.chat_id)

    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Pult - connect your phone</title>
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
  /* No height is set: the viewBox is there, so the SVG scales
     proportionally with its width. */
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
  <h1>Connect your phone</h1>
  <div class="host">{html.escape(cfg.host_name)}</div>

  <div class="tabs">
    <button class="tab on" data-for="qr-app">Pult app</button>
    <button class="tab" data-for="qr-web">Browser</button>
  </div>

  <div class="qr" id="qr-app">{qr_svg(app_link(target))}</div>
  <div class="qr" id="qr-web" hidden>{qr_svg(target)}</div>

  <div><code id="link">{html.escape(target)}</code></div>

  <div class="acts">
    <button class="act" id="copy">Copy link</button>
    <button class="act" id="send" {"" if telegram else "hidden"}>Send to Telegram</button>
  </div>
  <div class="said" id="said"></div>

  <ol id="steps-app">
    <li>Make sure the Pult app is installed on the phone.</li>
    <li>Scan the QR code with the camera &mdash; the app opens itself.</li>
    <li>If you are asked about the certificate fingerprint, tap
        <b>Trust</b>. You are only asked once.</li>
  </ol>

  <ol id="steps-web" hidden>
    <li>Scan the QR code with the phone's camera.</li>
    <li>If the browser warns about the certificate: <b>Advanced &rarr;
        Proceed anyway</b>.</li>
    <li>From the menu choose <b>Add to Home screen</b>.</li>
  </ol>

  <details>
    <summary>If the camera will not do it</summary>
    <p>Press <b>Send to Telegram</b>. On the phone, long-press the link
    that arrives &rarr; <b>Share</b> &rarr; <b>Pult</b>. The computer
    adds itself to the list.</p>
    <p>Or copy the link and paste it into <b>+ Add computer</b> in the
    app.</p>
  </details>

  <div class="warn">
    This link carries the key to full control of the computer. Do not
    forward it to anyone. If the key gets out, delete <code>token</code>
    from the settings file and restart the program &mdash; a new key is
    generated.
  </div>

  {alt_block}
  {f'<div class="fp">Certificate fingerprint: {html.escape(fingerprint)}</div>' if fingerprint else ''}
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
      function () {{ say("Copied"); }},
      function () {{ say("Could not copy", true); }}
    );
  }};

  var send = document.getElementById("send");
  if (send) send.onclick = function () {{
    send.disabled = true;
    say("Sending…");
    fetch("/api/pair/send" + location.search, {{ method: "POST" }})
      .then(function (r) {{ return r.json(); }})
      .then(function (d) {{
        say(d.ok ? "Sent to Telegram" : (d.msg || "Not sent"), !d.ok);
      }})
      .catch(function () {{ say("Not sent", true); }})
      .then(function () {{ send.disabled = false; }});
  }};
</script>
</body></html>"""
