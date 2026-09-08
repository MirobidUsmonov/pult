/*
 * Pult - telefon tarafi.
 *
 * Video WebCodecs orqali ochiladi: kompyuterdan kelgan H.264 kadrlari
 * to'g'ridan-to'g'ri telefonning apparat dekoderiga beriladi. Shu sababli
 * kechikish past va batareya kam yeyiladi.
 */

const $ = (id) => document.getElementById(id);
const clamp = (v, a, b) => Math.min(b, Math.max(a, v));

const KEY_STORE = "pult.key";
const PREF_STORE = "pult.prefs";

/* ---------------------------------------------------------------- kalit */

function readToken() {
  const m = location.hash.match(/[#&]k=([^&]+)/);
  if (m) {
    const t = decodeURIComponent(m[1]);
    localStorage.setItem(KEY_STORE, t);
    // Kalitni manzil qatorida qoldirmaymiz: brauzer tarixida va ekran
    // suratlarida ko'rinib qolmasligi uchun. So'rov qismi saqlanadi -
    // u yerda kalitdan boshqa narsalar bo'lishi mumkin.
    history.replaceState(null, "", location.pathname + location.search);
    return t;
  }
  return localStorage.getItem(KEY_STORE) || "";
}

const prefs = Object.assign(
  { mode: "trackpad", sens: 1.6, width: 1280, fps: 30, bitrate: 4000, cursor: true },
  JSON.parse(localStorage.getItem(PREF_STORE) || "{}")
);
const savePrefs = () => localStorage.setItem(PREF_STORE, JSON.stringify(prefs));

/* -------------------------------------------------------------- dekoder */

class Decoder {
  constructor(canvas) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d", { alpha: false, desynchronized: true });
    this.dec = null;
    this.codec = null;
    this.ts = 0;
    this.gotKey = false;
    this.frames = 0;
    this.onFirstFrame = null;
  }

  get supported() {
    return typeof window.VideoDecoder === "function";
  }

  async configure(codec) {
    if (this.dec && this.codec === codec && this.dec.state === "configured") return;
    this.close();
    this.codec = codec;

    let cfg = { codec, optimizeForLatency: true, hardwareAcceleration: "prefer-hardware" };
    try {
      const probe = await VideoDecoder.isConfigSupported(cfg);
      if (!probe.supported) cfg = { codec, optimizeForLatency: true };
    } catch {
      cfg = { codec, optimizeForLatency: true };
    }

    this.dec = new VideoDecoder({
      output: (f) => this.draw(f),
      error: (e) => this.fail(e),
    });
    this.dec.configure(cfg);
    this.gotKey = false;
    this.ts = 0;
  }

  draw(frame) {
    const w = frame.displayWidth;
    const h = frame.displayHeight;
    if (this.canvas.width !== w || this.canvas.height !== h) {
      this.canvas.width = w;
      this.canvas.height = h;
    }
    this.ctx.drawImage(frame, 0, 0);
    frame.close();
    this.frames++;
    if (this.frames === 1 && this.onFirstFrame) this.onFirstFrame();
  }

  fail(err) {
    console.warn("dekoder xatosi:", err);
    // Dekoderni qayta yig'amiz va keyingi kalit kadrni kutamiz - shunda
    // bitta buzilgan kadr butun oqimni to'xtatib qo'ymaydi.
    const codec = this.codec;
    this.close();
    if (codec) this.configure(codec);
  }

  push(data, isKey) {
    if (!this.dec || this.dec.state !== "configured") return;
    if (isKey) this.gotKey = true;
    if (!this.gotKey) return;
    this.ts += 1000;
    try {
      this.dec.decode(
        new EncodedVideoChunk({ type: isKey ? "key" : "delta", timestamp: this.ts, data })
      );
    } catch (e) {
      this.fail(e);
    }
  }

  close() {
    if (this.dec && this.dec.state !== "closed") {
      try { this.dec.close(); } catch {}
    }
    this.dec = null;
    this.gotKey = false;
  }
}

/* --------------------------------------------------------------- ulanish */

class Link {
  constructor(token) {
    this.token = token;
    this.ws = null;
    this.retry = 0;
    this.alive = false;
    this.onJson = () => {};
    this.onVideo = () => {};
    this.onState = () => {};
    this.rtt = 0;
    this._pingTimer = null;
  }

  connect() {
    if (!this.token) { this.onState("nokey"); return; }
    const scheme = location.protocol === "https:" ? "wss" : "ws";
    const url = `${scheme}://${location.host}/ws?k=${encodeURIComponent(this.token)}`;
    this.onState(this.retry ? "reconnecting" : "connecting");

    const ws = new WebSocket(url);
    ws.binaryType = "arraybuffer";
    this.ws = ws;

    ws.onopen = () => {
      this.alive = true;
      this.retry = 0;
      this.onState("open");
      this.send({ t: "hello", role: "controller", name: deviceName(), ver: 1 });
      this._pingTimer = setInterval(() => this.send({ t: "ping", ts: Date.now() }), 2000);
    };

    ws.onmessage = (ev) => {
      if (typeof ev.data === "string") {
        let msg;
        try { msg = JSON.parse(ev.data); } catch { return; }
        if (msg.t === "pong" && msg.ts) this.rtt = Date.now() - msg.ts;
        this.onJson(msg);
      } else {
        const buf = ev.data;
        if (buf.byteLength < 8) return;
        const dv = new DataView(buf);
        if (dv.getUint8(0) !== 1) return;
        this.onVideo(new Uint8Array(buf, 8), (dv.getUint8(1) & 1) === 1);
      }
    };

    ws.onclose = (ev) => {
      this.alive = false;
      clearInterval(this._pingTimer);
      if (ev.code === 1008 || ev.code === 4401) { this.onState("nokey"); return; }
      this.onState("closed");
      // Sekin ortib boruvchi kutish: tarmoq yo'q bo'lsa telefonni
      // uzluksiz urinish bilan qizdirmaymiz.
      const wait = Math.min(1000 * 2 ** this.retry++, 15000);
      setTimeout(() => this.connect(), wait);
    };

    ws.onerror = () => {};
  }

  send(obj) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(obj));
    }
  }
}

function deviceName() {
  const ua = navigator.userAgent;
  if (/Android/i.test(ua)) return "Android";
  if (/iPhone|iPad/i.test(ua)) return "iPhone";
  return "Brauzer";
}

/* ------------------------------------------------------------------ UI */

const canvas = $("screen");
const stage = $("stage");
const decoder = new Decoder(canvas);
const link = new Link(readToken());

let host = null;
let streaming = false;
let zoom = 1, panX = 0, panY = 0;

function toast(text, ms = 1800) {
  const el = $("toast");
  el.textContent = text;
  el.classList.add("show");
  clearTimeout(el._t);
  el._t = setTimeout(() => el.classList.remove("show"), ms);
}

function setPlaceholder(text, button) {
  const ph = $("placeholder");
  if (text === null) { ph.hidden = true; return; }
  ph.hidden = false;
  $("phText").textContent = text;
  $("phBtn").hidden = !button;
  if (button) $("phBtn").textContent = button;
}

function setDot(cls, label) {
  $("dot").className = "dot " + cls;
  $("infoState").textContent = label;
}

link.onState = (state) => {
  if (state === "open") {
    setDot("on", "ulangan");
  } else if (state === "nokey") {
    setDot("off", "kalit yo‘q");
    setPlaceholder(
      "Kalit topilmadi. Kompyuterdagi Pult bergan havolani to‘liq oching " +
      "(havola oxirida #k=… bo‘lishi kerak).",
      null
    );
  } else if (state === "closed") {
    setDot("off", "uzildi");
    setPlaceholder("Aloqa uzildi. Qayta ulanmoqda…", null);
  } else {
    setDot("warn", state === "reconnecting" ? "qayta ulanmoqda" : "ulanmoqda");
  }
};

link.onJson = (msg) => {
  if (msg.t === "hello") {
    host = msg.host;
    $("hostName").textContent = host.name;
    $("sheetHost").textContent = host.name;
    buildMonitors(host.monitors);
    buildCommands(host.commands || []);
    applyPrefsToUi();
    if (!decoder.supported) {
      setPlaceholder(
        "Bu brauzer video dekodlashni (WebCodecs) qo‘llab-quvvatlamaydi. " +
        "Android’da Chrome, iPhone’da iOS 17+ Safari kerak.",
        null
      );
      return;
    }
    startStream();
  } else if (msg.t === "stream") {
    $("infoEnc").textContent = msg.encoder || "—";
    $("infoSize").textContent = `${msg.w}×${msg.h} · ${msg.fps} k/s`;
    if (msg.codec) decoder.configure(msg.codec);
  } else if (msg.t === "stats") {
    $("stats").textContent = `${msg.fps} k/s · ${fmtRate(msg.kbps)}`;
    $("infoRtt").textContent = link.rtt ? `${link.rtt} ms` : "—";
  } else if (msg.t === "error") {
    toast(msg.msg);
  } else if (msg.t === "cmd_ok") {
    toast(msg.result || "bajarildi");
  }
};

link.onVideo = (data, isKey) => decoder.push(data, isKey);

decoder.onFirstFrame = () => {
  setPlaceholder(null);
  canvas.classList.remove("hidden");
};

function fmtRate(kbps) {
  return kbps >= 1000 ? (kbps / 1000).toFixed(1) + " Mbit" : kbps + " kbit";
}

function startStream() {
  streaming = true;
  setPlaceholder("Ekran kutilmoqda…");
  link.send({
    t: "view", on: true,
    monitor: prefs.monitor ?? 0,
    fps: prefs.fps, width: prefs.width,
    bitrate: prefs.bitrate, cursor: prefs.cursor,
  });
}

function stopStream() {
  streaming = false;
  link.send({ t: "view", on: false });
}

/* ---------------------------------------------------------- kiritish */

let lastMoveSent = 0;
function sendMove(x, y) {
  const now = performance.now();
  if (now - lastMoveSent < 12) return;   // ~80/s dan tez yubormaymiz
  lastMoveSent = now;
  link.send({ t: "mouse", a: "move", x, y });
}

function pointToNorm(cx, cy) {
  const r = canvas.getBoundingClientRect();
  // Kanvas hali chizilmagan bo'lsa (birinchi kadr kelmagan, ilova fonda,
  // ekran burilayotgan payt) o'lcham nol bo'ladi. Bunda nolga bo'lish
  // NaN beradi va serverga yaroqsiz koordinata ketadi - shuning uchun
  // null qaytaramiz va chaqiruvchi koordinatasiz ish ko'radi.
  if (r.width < 1 || r.height < 1) return null;
  return {
    x: clamp((cx - r.left) / r.width, 0, 1),
    y: clamp((cy - r.top) / r.height, 0, 1),
  };
}

/** Berilgan nuqtaga bosadi. Koordinata aniqlanmasa kursor turgan joyga. */
function clickAt(point, button = "left") {
  const n = point ? pointToNorm(point.x, point.y) : null;
  if (n) link.send({ t: "mouse", a: "click", b: button, x: n.x, y: n.y });
  else link.send({ t: "mouse", a: "click", b: button });
}

function applyTransform() {
  canvas.style.transform = `translate(${panX}px, ${panY}px) scale(${zoom})`;
}

function showHint(cx, cy) {
  const hint = $("cursorHint");
  const r = stage.getBoundingClientRect();
  hint.style.left = cx - r.left + "px";
  hint.style.top = cy - r.top + "px";
  hint.classList.add("show");
  clearTimeout(hint._t);
  hint._t = setTimeout(() => hint.classList.remove("show"), 400);
}

/* Imo-ishoralar sozlamalari. Barchasi piksel va millisekundda. */
const TAP_MS = 300;         // shu vaqtdan tez ko'tarilsa - bosish
const DOUBLE_MS = 330;      // ikki bosish orasidagi eng uzun tanaffus
const DOUBLE_PX = 48;       // ikkinchi bosish shuncha yaqin bo'lishi kerak
const DRAG_HOLD_MS = 190;   // ikkinchi tegish shuncha ushlansa - sudrash
const MOVE_START_PX = 5;    // barmoq titrashi harakatga aylanmasligi uchun
const SWITCH_START_PX = 45; // uch barmoq: oyna almashtirish boshlanishi
const SWITCH_STEP_PX = 75;  // har shuncha surilganda - keyingi oyna
const SWITCH_VERT_PX = 70;  // uch barmoq: yuqoriga/pastga

const touch = {
  pts: new Map(),
  gesture: null,      // 'point' | 'two' | 'switch' | 'done'
  two: null,          // 'scroll' | 'zoom' | 'pan'
  anchor: null,       // bosish yuboriladigan nuqta
  start: null,        // barmoq tushgan joy
  last: null,
  startAt: 0,
  moved: false,
  movedEnough: false,
  dragging: false,
  dragTimer: null,
  longPress: null,
  lastTap: null,      // {x, y, at} - oldingi bosish
  isSecondTap: false,
  lastDist: 0,
  lastMid: null,
  scrollAcc: 0,
  sw: null,           // uch barmoq holati
};

// Nosozlik izlash uchun: manzilga ?debug qo'shilsa ichki holat brauzer
// konsolidan ko'rinadi. Imo-ishoralar sezgir joy, ularni tekshirishning
// boshqa yo'li yo'q.
if (location.search.includes("debug")) {
  window.__pult = { touch, prefs, link, get zoom() { return zoom; } };
}

function dist(a, b) { return Math.hypot(a.x - b.x, a.y - b.y); }
function mid(a, b) { return { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 }; }
function centroid(pts) {
  const n = pts.length;
  return {
    x: pts.reduce((s, p) => s + p.x, 0) / n,
    y: pts.reduce((s, p) => s + p.y, 0) / n,
  };
}

function clearTimers() {
  clearTimeout(touch.dragTimer);
  clearTimeout(touch.longPress);
  touch.dragTimer = null;
  touch.longPress = null;
}

function beginDrag() {
  if (touch.dragging) return;
  touch.dragging = true;
  const a = touch.anchor || touch.last;
  const n = prefs.mode === "touch" && a ? pointToNorm(a.x, a.y) : null;
  if (n) link.send({ t: "mouse", a: "down", b: "left", x: n.x, y: n.y });
  else link.send({ t: "mouse", a: "down", b: "left" });
  navigator.vibrate?.(12);
  if (touch.last) showHint(touch.last.x, touch.last.y);
}

function endDrag() {
  if (!touch.dragging) return;
  link.send({ t: "mouse", a: "up", b: "left" });
  touch.dragging = false;
}

/* -- uch barmoq: oyna almashtirish -------------------------------------- */

function switchStep(dir) {
  // Alt bosilgan holda Tab - oldinga, Shift+Tab - orqaga
  if (dir < 0) {
    link.send({ t: "key", a: "down", k: "shift" });
    link.send({ t: "key", a: "tap", k: "Tab" });
    link.send({ t: "key", a: "up", k: "shift" });
  } else {
    link.send({ t: "key", a: "tap", k: "Tab" });
  }
  navigator.vibrate?.(6);
}

function endSwitch() {
  const sw = touch.sw;
  if (sw && sw.alt) {
    // Alt qo'yilganda tanlangan oyna oldinga chiqadi
    link.send({ t: "key", a: "up", k: "alt" });
  }
  touch.sw = null;
}

/* -- barmoq hodisalari --------------------------------------------------- */

stage.addEventListener("touchstart", (e) => {
  e.preventDefault();
  for (const t of e.changedTouches) {
    touch.pts.set(t.identifier, { x: t.clientX, y: t.clientY });
  }
  const pts = [...touch.pts.values()];
  const now = performance.now();

  if (pts.length === 1) {
    const p = pts[0];
    touch.startAt = now;
    touch.start = { ...p };
    touch.last = { ...p };
    touch.moved = false;
    touch.movedEnough = false;
    touch.gesture = "point";

    touch.isSecondTap = !!(
      touch.lastTap &&
      now - touch.lastTap.at < DOUBLE_MS &&
      dist(p, touch.lastTap) < DOUBLE_PX
    );

    // Ikkinchi bosish ataylab BIRINCHISINING joyiga yuboriladi. Barmoq
    // aynan bir nuqtaga ikki marta tushmaydi, ikkita bosish esa turli
    // joyga tushsa Windows ularni ikki marta bosish deb qabul qilmaydi.
    touch.anchor = touch.isSecondTap
      ? { x: touch.lastTap.x, y: touch.lastTap.y }
      : { ...p };

    if (touch.isSecondTap) {
      // Bu yerda darrov sudrashni boshlamaymiz: barmoq tez ko'tarilsa
      // bu ikki marta bosish, ushlab turilsa - sudrash. Qarorni
      // kechiktiramiz.
      touch.dragTimer = setTimeout(() => {
        touch.dragTimer = null;
        if (touch.pts.size === 1 && !touch.movedEnough) beginDrag();
      }, DRAG_HOLD_MS);
    } else if (prefs.mode === "touch") {
      const n = pointToNorm(p.x, p.y);
      if (n) sendMove(n.x, n.y);
      touch.longPress = setTimeout(() => {
        touch.longPress = null;
        if (!touch.movedEnough && touch.pts.size === 1) {
          clickAt(p, "right");
          navigator.vibrate?.(15);
          touch.gesture = "done";
        }
      }, 550);
    }
  } else if (pts.length === 2) {
    clearTimers();
    touch.gesture = "two";
    touch.two = null;
    touch.moved = false;
    touch.startAt = now;
    touch.lastDist = dist(pts[0], pts[1]);
    touch.lastMid = mid(pts[0], pts[1]);
  } else if (pts.length === 3) {
    clearTimers();
    endDrag();
    touch.gesture = "switch";
    const c = centroid(pts);
    touch.sw = { ox: c.x, oy: c.y, prevX: c.x, mode: null, alt: false, acc: 0, done: false };
  }
}, { passive: false });

stage.addEventListener("touchmove", (e) => {
  e.preventDefault();
  for (const t of e.changedTouches) {
    if (touch.pts.has(t.identifier)) {
      touch.pts.set(t.identifier, { x: t.clientX, y: t.clientY });
    }
  }
  const pts = [...touch.pts.values()];

  if (touch.gesture === "point" && pts.length === 1) {
    const p = pts[0];
    const dx = p.x - touch.last.x;
    const dy = p.y - touch.last.y;
    touch.last = { ...p };

    if (!touch.movedEnough) {
      // Boshlanishda kichik titrashni butunlay e'tiborsiz qoldiramiz -
      // aks holda bosish paytida kursor siljib ketadi.
      if (dist(p, touch.start) <= MOVE_START_PX) return;
      touch.movedEnough = true;
      touch.moved = true;
      clearTimeout(touch.longPress);
      touch.longPress = null;
      if (touch.isSecondTap && touch.dragTimer) {
        // Ikkinchi tegishda surila boshladi - demak sudrash
        clearTimeout(touch.dragTimer);
        touch.dragTimer = null;
        beginDrag();
      }
    }

    if (prefs.mode === "touch") {
      const n = pointToNorm(p.x, p.y);
      if (n) sendMove(n.x, n.y);
    } else {
      const k = prefs.sens / zoom;
      link.send({ t: "mouse", a: "moveby", dx: dx * k, dy: dy * k });
    }
  } else if (touch.gesture === "two" && pts.length === 2) {
    const d = dist(pts[0], pts[1]);
    const m = mid(pts[0], pts[1]);
    const dd = d - touch.lastDist;
    const dmy = m.y - touch.lastMid.y;
    const dmx = m.x - touch.lastMid.x;

    if (!touch.two) {
      // Barmoqlar orasi o'zgarishi ustunmi yoki siljish ustunmi - bir
      // marta qaror qilamiz va imo-ishora oxirigacha shunda qolamiz.
      if (Math.abs(dd) > Math.abs(dmy) * 1.4 + 2) touch.two = "zoom";
      else touch.two = zoom > 1.02 ? "pan" : "scroll";
    }

    if (touch.two === "zoom") {
      zoom = clamp(zoom * (1 + dd / 260), 1, 6);
      if (zoom <= 1.02) { zoom = 1; panX = 0; panY = 0; }
      applyTransform();
    } else if (touch.two === "pan") {
      panX += dmx;
      panY += dmy;
      applyTransform();
    } else {
      touch.scrollAcc += dmy;
      const ticks = touch.scrollAcc / 42;
      if (Math.abs(ticks) >= 0.2) {
        link.send({ t: "scroll", dy: ticks });
        touch.scrollAcc = 0;
      }
    }
    if (Math.abs(dd) + Math.abs(dmy) + Math.abs(dmx) > 4) touch.moved = true;
    touch.lastDist = d;
    touch.lastMid = m;
  } else if (touch.gesture === "switch" && pts.length === 3 && touch.sw) {
    const sw = touch.sw;
    const c = centroid(pts);
    const dx = c.x - sw.ox;
    const dy = c.y - sw.oy;

    if (!sw.mode) {
      if (Math.abs(dx) > SWITCH_START_PX && Math.abs(dx) > Math.abs(dy)) sw.mode = "tab";
      else if (Math.abs(dy) > SWITCH_VERT_PX && Math.abs(dy) > Math.abs(dx)) sw.mode = "vert";
    }

    if (sw.mode === "tab") {
      if (!sw.alt) {
        // Alt bosilib turadi va barmoqlar ko'tarilguncha qo'yilmaydi -
        // shunda oyna tanlash oynasi ekranda qolib, surilishga ergashadi.
        link.send({ t: "key", a: "down", k: "alt" });
        sw.alt = true;
        sw.acc = 0;
        sw.prevX = c.x;
        switchStep(dx > 0 ? 1 : -1);
      } else {
        sw.acc += c.x - sw.prevX;
        sw.prevX = c.x;
        while (sw.acc >= SWITCH_STEP_PX) { switchStep(1); sw.acc -= SWITCH_STEP_PX; }
        while (sw.acc <= -SWITCH_STEP_PX) { switchStep(-1); sw.acc += SWITCH_STEP_PX; }
      }
    } else if (sw.mode === "vert" && !sw.done) {
      sw.done = true;
      if (dy < 0) {
        link.send({ t: "combo", keys: ["win", "Tab"] });   // vazifalar ko'rinishi
        toast("Vazifalar ko‘rinishi");
      } else {
        link.send({ t: "combo", keys: ["win", "d"] });     // ish stoli
        toast("Ish stoli");
      }
      navigator.vibrate?.(15);
    }
  }
}, { passive: false });

stage.addEventListener("touchend", (e) => {
  e.preventDefault();
  const before = touch.pts.size;
  for (const t of e.changedTouches) touch.pts.delete(t.identifier);
  const remaining = touch.pts.size;
  const now = performance.now();
  const dt = now - touch.startAt;

  if (touch.gesture === "switch") {
    if (remaining === 0) {
      endSwitch();
      touch.gesture = null;
      touch.lastTap = null;
    }
    return;
  }

  if (touch.dragging) {
    if (remaining === 0) {
      endDrag();
      touch.lastTap = null;
      touch.gesture = null;
    }
    return;
  }

  clearTimers();

  const isTap = before === 1 && remaining === 0 && touch.gesture === "point"
    && !touch.movedEnough && dt < TAP_MS;

  if (isTap) {
    const a = touch.anchor;
    clickAt(prefs.mode === "touch" ? a : null, "left");
    showHint(a.x, a.y);
    navigator.vibrate?.(8);
    // Ikkinchi bosishdan keyin hisob noldan boshlanadi, aks holda uchinchi
    // tegish ham "ikkinchi" bo'lib qolaverardi.
    touch.lastTap = touch.isSecondTap ? null : { x: a.x, y: a.y, at: now };
  } else if (before === 2 && remaining === 0 && !touch.moved && dt < TAP_MS) {
    // Ikki barmoq bilan tegish = o'ng tugma (trackpad odati)
    link.send({ t: "mouse", a: "click", b: "right" });
    navigator.vibrate?.(12);
    touch.lastTap = null;
  }

  if (remaining === 0) {
    touch.gesture = null;
    touch.two = null;
    touch.scrollAcc = 0;
  }
}, { passive: false });

stage.addEventListener("touchcancel", () => {
  touch.pts.clear();
  clearTimers();
  endDrag();
  endSwitch();
  touch.gesture = null;
  touch.two = null;
  touch.lastTap = null;
});


// Kompyuter brauzeridan sinash uchun sichqoncha ham ishlaydi
canvas.addEventListener("mousemove", (e) => {
  if (e.buttons === 0 && prefs.mode !== "touch") return;
  const p = pointToNorm(e.clientX, e.clientY);
  if (p) sendMove(p.x, p.y);
});
canvas.addEventListener("mousedown", (e) => {
  const p = pointToNorm(e.clientX, e.clientY);
  const b = ["left", "middle", "right"][e.button] || "left";
  if (p) link.send({ t: "mouse", a: "down", b, x: p.x, y: p.y });
  else link.send({ t: "mouse", a: "down", b });
});
canvas.addEventListener("mouseup", (e) => {
  link.send({ t: "mouse", a: "up", b: ["left", "middle", "right"][e.button] || "left" });
});
canvas.addEventListener("contextmenu", (e) => e.preventDefault());
stage.addEventListener("wheel", (e) => {
  e.preventDefault();
  link.send({ t: "scroll", dy: -e.deltaY / 100 });
}, { passive: false });

/* -------------------------------------------------------- tugmalar */

$("btnLeft").addEventListener("click", () => link.send({ t: "mouse", a: "click", b: "left" }));
$("btnRight").addEventListener("click", () => link.send({ t: "mouse", a: "click", b: "right" }));

$("btnMode").addEventListener("click", () => {
  prefs.mode = prefs.mode === "trackpad" ? "touch" : "trackpad";
  savePrefs();
  updateModeButton();
  toast(prefs.mode === "trackpad"
    ? "Trackpad: barmoq surilsa kursor siljiydi"
    : "To‘g‘ridan-to‘g‘ri: qayerga bossang o‘sha yerga bosiladi");
});

function updateModeButton() {
  const b = $("btnMode");
  b.querySelector("span").textContent = prefs.mode === "trackpad" ? "🖱" : "👆";
  b.querySelector("i").textContent = prefs.mode === "trackpad" ? "trackpad" : "to‘g‘ri";
}

/* -------------------------------------------------------- klaviatura */

const mods = new Set();

function fireKey(code) {
  if (mods.size) {
    link.send({ t: "combo", keys: [...mods, code] });
    clearMods();
  } else {
    link.send({ t: "key", a: "tap", k: code });
  }
  navigator.vibrate?.(8);
}

function clearMods() {
  mods.clear();
  document.querySelectorAll(".k.mod").forEach((b) => b.classList.remove("on"));
}

document.addEventListener("click", (e) => {
  const btn = e.target.closest(".k");
  if (!btn) return;
  if (btn.dataset.mod) {
    const m = btn.dataset.mod;
    if (mods.has(m)) { mods.delete(m); btn.classList.remove("on"); }
    else { mods.add(m); btn.classList.add("on"); }
    navigator.vibrate?.(8);
  } else if (btn.dataset.key) {
    fireKey(btn.dataset.key);
  }
});

const fnrow = $("fnrow");
for (let i = 1; i <= 12; i++) {
  const b = document.createElement("button");
  b.className = "k";
  b.dataset.key = "F" + i;
  b.textContent = "F" + i;
  fnrow.appendChild(b);
}

const typer = $("typer");
$("btnKeys").addEventListener("click", () => {
  const row = $("keyrow");
  const show = row.hidden;
  row.hidden = !show;
  $("btnKeys").classList.toggle("active", show);
  if (show) typer.focus(); else { typer.blur(); clearMods(); }
});

typer.addEventListener("input", () => {
  const v = typer.value;
  if (!v) return;
  typer.value = "";
  if (mods.size && v.length === 1) {
    link.send({ t: "combo", keys: [...mods, v] });
    clearMods();
  } else {
    link.send({ t: "text", s: v });
  }
});

typer.addEventListener("keydown", (e) => {
  if (e.key === "Backspace" && !typer.value) {
    e.preventDefault();
    fireKey("Backspace");
  } else if (e.key === "Enter") {
    e.preventDefault();
    fireKey("Enter");
  }
});

/* ------------------------------------------------------- sozlamalar */

function buildMonitors(monitors) {
  const row = $("monitorRow");
  row.innerHTML = "";
  (monitors || []).forEach((m) => {
    const b = document.createElement("button");
    b.className = "btn" + ((prefs.monitor ?? 0) === m.index ? " on" : "");
    b.textContent = `${m.index + 1}-ekran · ${m.w}×${m.h}${m.primary ? " ★" : ""}`;
    b.onclick = () => {
      prefs.monitor = m.index;
      savePrefs();
      zoom = 1; panX = 0; panY = 0; applyTransform();
      buildMonitors(monitors);
      link.send({ t: "view", on: true, monitor: m.index });
    };
    row.appendChild(b);
  });
}

const CMD_LABELS = {
  lock: "Qulflash", sleep: "Uxlatish", hibernate: "Gibernatsiya",
  shutdown: "O‘chirish", reboot: "Qayta yuklash", logoff: "Chiqish",
  cancel_shutdown: "Bekor qilish",
  display_off: "Ekran o‘chsin", display_on: "Ekran yonsin",
};
const CMD_CONFIRM = new Set(["shutdown", "reboot", "logoff", "hibernate"]);

function buildCommands(cmds) {
  const row = $("cmdRow");
  row.innerHTML = "";
  cmds.forEach((name) => {
    const b = document.createElement("button");
    b.className = "btn" + (CMD_CONFIRM.has(name) ? " danger" : "");
    b.textContent = CMD_LABELS[name] || name;
    b.onclick = () => {
      if (CMD_CONFIRM.has(name) && !confirm(`${CMD_LABELS[name] || name}?`)) return;
      link.send({ t: "cmd", name });
    };
    row.appendChild(b);
  });
}

function applyPrefsToUi() {
  $("selWidth").value = String(prefs.width);
  $("selFps").value = String(prefs.fps);
  $("selBitrate").value = String(prefs.bitrate);
  $("chkCursor").checked = !!prefs.cursor;
  $("rngSens").value = String(prefs.sens);
  updateModeButton();
}

function pushView(partial) {
  Object.assign(prefs, partial);
  savePrefs();
  link.send({ t: "view", on: true, ...partial });
}

$("selWidth").onchange = (e) => pushView({ width: +e.target.value });
$("selFps").onchange = (e) => pushView({ fps: +e.target.value });
$("selBitrate").onchange = (e) => pushView({ bitrate: +e.target.value });
$("chkCursor").onchange = (e) => pushView({ cursor: e.target.checked });
$("rngSens").oninput = (e) => { prefs.sens = +e.target.value; savePrefs(); };

$("btnSettings").onclick = () => { $("sheet").hidden = false; };
$("btnCloseSheet").onclick = () => { $("sheet").hidden = true; };
$("sheet").addEventListener("click", (e) => { if (e.target.id === "sheet") $("sheet").hidden = true; });

$("btnRun").onclick = () => {
  const cmd = $("runCmd").value.trim();
  if (cmd) link.send({ t: "cmd", name: "run", command: cmd });
};

$("btnForget").onclick = () => {
  if (!confirm("Kalit o‘chirilsin? Qayta ulanish uchun havola kerak bo‘ladi.")) return;
  localStorage.removeItem(KEY_STORE);
  location.reload();
};

$("phBtn").onclick = () => startStream();

/* ---------------------------------------------------------- hayot sikli */

// Ilova fonga o'tganda oqimni to'xtatamiz: bekorga trafik va batareya
// sarflanmasin.
document.addEventListener("visibilitychange", () => {
  if (document.hidden) {
    // Imo-ishora o'rtasida ilova fonga o'tsa, bosilgan klavish (masalan
    // Alt+Tab paytidagi Alt) kompyuterda bosilgan holda qolib ketardi.
    clearTimers();
    endDrag();
    endSwitch();
    link.send({ t: "release_keys" });
    if (streaming) { stopStream(); streaming = true; }
  } else if (streaming) {
    startStream();
  }
});

// Boshqaruv paytida ekran o'chib qolmasin
let wakeLock = null;
async function keepAwake() {
  try {
    if ("wakeLock" in navigator && !wakeLock) {
      wakeLock = await navigator.wakeLock.request("screen");
      wakeLock.addEventListener("release", () => { wakeLock = null; });
    }
  } catch {}
}
document.addEventListener("visibilitychange", () => { if (!document.hidden) keepAwake(); });
keepAwake();

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("sw.js").catch(() => {});
}

setPlaceholder("Ulanmoqda…");
link.connect();
