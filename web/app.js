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

/* Havoladagi "view=phone" - ulangan telefonni o'zi tanlash so'rovi.
 * Kompyuter treyidagi "Telefon ekranini ko'rish" shu belgi bilan ochadi.
 * readToken() manzil qatorini tozalagani uchun bu qiymat undan OLDIN
 * o'qilishi shart. */
const wantView = (location.hash.match(/[#&]view=([^&]+)/) || [])[1] || "";

/* Sahifa telefonda ham, kompyuter brauzerida ham ochiladi. Kompyuterda
 * boshqacha ko'rinish kerak: barmoq imo-ishoralari o'rniga sichqoncha,
 * ekrandagi tugmalar qatori o'rniga haqiqiy klaviatura.
 *
 * Ko'rinish CSS'dagi @media orqali o'zgaradi, bu yerda esa faqat
 * xatti-harakat uchun so'rov saqlanadi. Muhimi: qiymat OLDINDAN
 * hisoblanmaydi. Sahifa yuklanayotganda oyna o'lchami hali noto'g'ri
 * bo'lishi mumkin va bir marta hisoblangan qiymat shu xato bilan
 * qotib qolardi. */
const deskQuery = matchMedia("(pointer: fine) and (min-width: 700px)");

/* ---------------------------------------------------------------- kalit */

function readToken() {
  const m = location.hash.match(/[#&]k=([^&]+)/);
  if (m) {
    const t = decodeURIComponent(m[1]);
    localStorage.setItem(KEY_STORE, t);
    // Kalitni manzil qatorida qoldirmaymiz: brauzer tarixida va ekran
    // suratlarida ko'rinib qolmasligi uchun. Faqat kalit olib
    // tashlanadi - qolgan belgilar (masalan view=phone) sir emas va
    // saqlanishi shart, aks holda sahifa yangilanganda so'rov
    // yo'qolib, ko'rinish boshqa manbaga qaytib ketardi.
    const rest = location.hash.replace(/^#/, "").split("&")
      .filter((p) => p && !p.startsWith("k="));
    history.replaceState(
      null, "",
      location.pathname + location.search + (rest.length ? "#" + rest.join("&") : "")
    );
    return t;
  }
  return localStorage.getItem(KEY_STORE) || "";
}

const prefs = Object.assign(
  { mode: "trackpad", sens: 1.6, width: 1280, fps: 30, bitrate: 4000, cursor: true,
    autoRotate: true, rotDir: 90 },
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
    this.onFrame = null;
    this.onResize = null;
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
      // Video o'lchami o'zgardi - joylashuvni qayta hisoblash kerak
      if (this.onResize) this.onResize(w, h);
    }
    this.ctx.drawImage(frame, 0, 0);
    frame.close();
    this.frames++;
    // Har kadrda xabar qildiramiz, faqat birinchisida emas: oqim qayta
    // boshlanganda (ilovaga qaytilganda, sifat o'zgarganda) "Ekran
    // kutilmoqda" yozuvi qayta chiqadi va uni yana yashirish kerak.
    if (this.onFrame) this.onFrame();
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
// Serverda hozir qaysi ekran ko'rsatilayotgani. Kursor ekranlar orasida
// yurganda server buni o'zi o'zgartiradi, shuning uchun sozlamalardagi
// tanlovga emas, serverning javobiga ishonamiz.
let currentMonitor = 0;
// Hozir qaysi manba ko'rilyapti: "local" - kompyuterning o'zi, yoki
// ulangan telefonning raqami.
let currentSource = "local";
let sources = [];
/*
 * Ko'rinish holati.
 *
 * Kanvas o'lchami va joyi CSS'ga emas, shu yerdagi hisobga bo'ysunadi.
 * Sababi burish: burilgan elementning getBoundingClientRect() natijasi
 * uning tashqi to'rtburchagini beradi va bosish koordinatalarini
 * hisoblashga yaramaydi. Shuning uchun o'lcham va burchakni o'zimiz
 * saqlab, koordinatani teskari hisoblaymiz.
 */
const view = {
  rot: 0,          // 0 yoki 90 daraja
  auto: true,      // telefon tik turganda o'zi bursin
  zoom: 1,
  panX: 0,
  panY: 0,
  k: 1,            // videoning ekrandagi haqiqiy masshtabi
};

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
  // QR faqat "telefon kutilmoqda" holatida ko'rinadi - boshqa
  // xabarlar (ulanmoqda, uzildi) uni ko'rsatmasin
  $("pairBox").hidden = true;
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
    updateHostLabel();
    $("sheetHost").textContent = host.name;
    sources = msg.sources || [];
    currentMonitor = (msg.stream && msg.stream.monitor) || 0;
    if (msg.stream && typeof msg.stream.follow_cursor === "boolean") {
      $("chkFollow").checked = msg.stream.follow_cursor;
    }
    // Telefonni so'ragan bo'lsak - oqim boshlanishidan oldin tanlaymiz,
    // shunda kompyuter ekrani bekorga bir marta yoqilmaydi.
    autoPickSource(true);
    buildMonitors(host.monitors);
    buildMonbar();
    buildToolMid();
    buildSources();
    updateHostLabel();
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
    // Telefon so'ralgan, lekin hali ulanmagan - kutamiz. Kompyuter
    // ekranini yoqish bu yerda zararli: sahifa kompyuterning o'zida
    // ochilgani uchun ekran o'zini o'zi cheksiz aks ettiradi.
    if (wantView === "phone" && !autoPicked) {
      updateWaiting();
      return;
    }
    startStream();
  } else if (msg.t === "stream") {
    if (typeof msg.monitor === "number" && msg.monitor !== currentMonitor) {
      currentMonitor = msg.monitor;
      if (host) buildMonitors(host.monitors);
      buildMonbar();
      buildToolMid();
      flashMonbar();
      resetView();
    }
    $("infoEnc").textContent = msg.encoder || "—";
    $("infoSize").textContent = `${msg.w}×${msg.h} · ${msg.fps} k/s`;
    if (msg.codec) decoder.configure(msg.codec);
  } else if (msg.t === "stats") {
    // Oqim yo'q bo'lsa raqamlarni ko'rsatmaymiz: server oxirgi
    // qiymatlarni yuborishda davom etadi va ular ekranda "ishlayapti"
    // degan yolg'on taassurot qoldirardi.
    $("stats").textContent = streaming
      ? `${msg.fps} k/s · ${fmtRate(msg.kbps)}` : "";
    $("infoRtt").textContent = link.rtt ? `${link.rtt} ms` : "—";
  } else if (msg.t === "error") {
    toast(msg.msg);
  } else if (msg.t === "cmd_ok") {
    toast(msg.result || "bajarildi");
  } else if (msg.t === "monitor_map") {
    toast("Ekranlar almashtirildi");
  } else if (msg.t === "sources") {
    sources = msg.list || [];
    buildSources();
    autoPickSource(false);
    updateWaiting();
  } else if (msg.t === "source_gone") {
    toast("Manba uzildi");
    currentSource = "local";
    // Yana telefon ulansa o'zi qaytib tanlansin
    autoPicked = false;
    buildSources();
    buildToolMid();
    updateHostLabel();
    startStream();
    updateWaiting();
  }
};

link.onVideo = (data, isKey) => decoder.push(data, isKey);

decoder.onFrame = () => {
  // hidden allaqachon true bo'lsa bu hech narsa qilmaydi, shuning uchun
  // har kadrda chaqirish arzon.
  setPlaceholder(null);
};

decoder.onFirstFrame = () => {
  canvas.classList.remove("hidden");
  resetView();
};

decoder.onResize = () => {
  autoRotate();
  applyView();
};

// Ekran burilganda yoki oyna o'lchami o'zgarganda qayta hisoblaymiz.
// orientationchange dan keyin brauzer o'lchamlarni darrov yangilamaydi,
// shuning uchun kichik kechikish bilan takrorlaymiz.
function refreshView() {
  autoRotate();
  applyView();
}
addEventListener("resize", refreshView);
addEventListener("orientationchange", () => {
  refreshView();
  setTimeout(refreshView, 250);
});

function updateHostLabel() {
  const src = sources.find((x) => x.id === currentSource);
  $("hostName").textContent = src && src.id !== "local"
    ? src.name
    : (host ? host.name : "Pult");
}

function fmtRate(kbps) {
  return kbps >= 1000 ? (kbps / 1000).toFixed(1) + " Mbit" : kbps + " kbit";
}

function startStream() {
  streaming = true;
  setPlaceholder("Ekran kutilmoqda…");
  link.send({
    t: "view", on: true,
    source: currentSource,
    // Serverdagi joriy ekran: kursor ekranlar orasida yurgan bo'lsa
    // server allaqachon boshqasiga o'tgan bo'lishi mumkin, uni
    // eski tanlovga majburan qaytarmaymiz.
    monitor: currentMonitor,
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

/** Ekrandagi nuqtani video ichidagi nisbiy o'ringa (0..1) o'giradi. */
function pointToNorm(cx, cy) {
  const vw = canvas.width, vh = canvas.height;
  const r = stage.getBoundingClientRect();
  // Kanvas hali chizilmagan bo'lsa (birinchi kadr kelmagan, ilova fonda,
  // ekran burilayotgan payt) o'lcham nol bo'ladi. Nolga bo'lish NaN
  // beradi va serverga yaroqsiz koordinata ketardi.
  if (!vw || !vh || !view.k || r.width < 1 || r.height < 1) return null;

  const dx = cx - (r.left + r.width / 2) - view.panX;
  const dy = cy - (r.top + r.height / 2) - view.panY;
  const [u, v] = unrotate(dx, dy);
  const x = 0.5 + u / (vw * view.k);
  const y = 0.5 + v / (vh * view.k);
  return {
    x: clamp(x, 0, 1),
    y: clamp(y, 0, 1),
    // Xom qiymat: 0..1 dan tashqarida bo'lsa barmoq videoning o'zida
    // emas, yon-veridagi qora chekkada. Qirqilmagani kerak, chunki
    // chekka atigi bir necha piksel bo'lishi mumkin.
    rx: x,
    ry: y,
    // Bosish uchun kichik tolerans qoldiramiz: chekkaga bir-ikki
    // piksel chiqib ketgan tegish bekor ketmasin.
    inside: x >= -0.02 && x <= 1.02 && y >= -0.02 && y <= 1.02,
    // Ekran almashtirgich uchun esa aniq chegara kerak.
    outside: x < 0 || x > 1 || y < 0 || y > 1,
  };
}

/** Ekran yo'nalishini videoning o'z yo'nalishiga o'giradi.
 *
 * 90  - soat yo'nalishi bo'yicha: videoning tepasi ekranning o'ng chetiga
 *       tushadi (telefonni chapga burib qaraladi).
 * 270 - teskari tomonga.
 */
function unrotate(dx, dy) {
  if (view.rot === 90) return [dy, -dx];
  if (view.rot === 270) return [-dy, dx];
  return [dx, dy];
}

function applyView() {
  const vw = canvas.width, vh = canvas.height;
  const r = stage.getBoundingClientRect();
  if (!vw || !vh || r.width < 1 || r.height < 1) return;
  // Burilgan holatda video ekranga yon tomoni bilan sig'adi
  const sideways = view.rot === 90 || view.rot === 270;
  const fit = sideways
    ? Math.min(r.width / vh, r.height / vw)
    : Math.min(r.width / vw, r.height / vh);
  view.k = fit * view.zoom;
  canvas.style.width = (vw * view.k) + "px";
  canvas.style.height = (vh * view.k) + "px";
  canvas.style.transform =
    `translate(-50%, -50%) translate(${view.panX}px, ${view.panY}px) rotate(${view.rot}deg)`;
  const chip = $("btnZoom");
  chip.hidden = view.zoom <= 1.01;
  chip.textContent = view.zoom.toFixed(1) + "×";
  $("btnRotate").classList.toggle("on", view.rot !== 0);
}

/** Telefon tik turganda videoni yotqizadi. */
function autoRotate() {
  if (!view.auto) return;
  const r = stage.getBoundingClientRect();
  const portrait = r.height > r.width;
  const wide = canvas.width >= canvas.height;
  // Qaysi tomonga burish - foydalanuvchi tanlovi, uni saqlaymiz
  const want = portrait && wide ? (prefs.rotDir === 270 ? 270 : 90) : 0;
  if (want !== view.rot) {
    view.rot = want;
    view.panX = view.panY = 0;
  }
}

function resetView() {
  view.zoom = 1;
  view.panX = view.panY = 0;
  autoRotate();
  applyView();
}

/** Berilgan nuqtaga bosadi. Koordinata aniqlanmasa kursor turgan joyga. */
function clickAt(point, button = "left") {
  const n = point ? pointToNorm(point.x, point.y) : null;
  if (n && !n.inside) return;          // qora chekkaga tegildi
  if (n) link.send({ t: "mouse", a: "click", b: button, x: n.x, y: n.y });
  else link.send({ t: "mouse", a: "click", b: button });
}

function moveHint(cx, cy) {
  const hint = $("cursorHint");
  const r = stage.getBoundingClientRect();
  hint.style.left = cx - r.left + "px";
  hint.style.top = cy - r.top + "px";
}

function showHint(cx, cy) {
  const hint = $("cursorHint");
  moveHint(cx, cy);
  hint.classList.add("show");
  clearTimeout(hint._t);
  hint._t = setTimeout(() => hint.classList.remove("show"), 400);
}

/** Ushlangan/qo'yilgan holatni bildiradi. */
function grabFeedback(on) {
  const hint = $("cursorHint");
  clearTimeout(hint._t);
  if (on) {
    hint.classList.add("grab", "show");
    navigator.vibrate?.([14, 45, 28]);
    toast("Ushlandi — suring, barmoqni ko‘tarsangiz qo‘yiladi");
  } else {
    hint.classList.remove("grab", "show");
  }
}

/* Imo-ishoralar sozlamalari. Barchasi piksel va millisekundda. */
// Chegaralar Windows odatlariga moslangan: uning ikki marta bosish
// oralig'i standart holda 500 ms. Avvalgi 330 ms juda qisqa edi -
// odam biroz sekinroq bossa ikkinchi bosish alohida hisoblanardi.
const TAP_MS = 400;         // shu vaqtdan tez ko'tarilsa - bosish
const DOUBLE_MS = 500;      // ikki bosish orasidagi eng uzun tanaffus
const DOUBLE_PX = 55;       // ikkinchi bosish shuncha yaqin bo'lishi kerak
const DRAG_HOLD_MS = 320;   // ikkinchi tegish shuncha ushlansa - sudrash
const MOVE_START_PX = 5;    // barmoq titrashi harakatga aylanmasligi uchun
const SWITCH_START_PX = 45; // uch barmoq: oyna almashtirish boshlanishi
const SWITCH_STEP_PX = 75;  // har shuncha surilganda - keyingi oyna
const SWITCH_VERT_PX = 70;  // uch barmoq: yuqoriga/pastga
const EDGE_SWIPE_PX = 55;   // qora chekkada surish: ekran almashtirish
// Bosib turib ushlash. Ikki marta bosib sudrash trackpad odati bo'lib,
// telefonda uni bajarish qiyin ekan - bu esa oddiyroq yo'l: barmoqni
// bosib tursang ushlaydi, surasan, ko'tarsang qo'yadi.
const HOLD_GRAB_MS = 800;

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
  startDist: 0,
  startMid: null,
  scrollAcc: 0,
  scrollAxis: "y",    // aylantirish qaysi o'q bo'yicha
  sw: null,           // uch barmoq holati
};

// Nosozlik izlash uchun: manzilga ?debug qo'shilsa ichki holat brauzer
// konsolidan ko'rinadi. Imo-ishoralar sezgir joy, ularni tekshirishning
// boshqa yo'li yo'q.
if (location.search.includes("debug")) {
  window.__pult = { touch, prefs, link, view };
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
  grabFeedback(false);
}

/* -- ekran ko'rsatkichi va almashtirgichi -------------------------------- */

/** Ekranlar jismoniy joylashuvi bo'yicha, chapdan o'ngga.
 *
 * Ro'yxatdagi tartib qurilma raqamiga asoslangan va u jismoniy
 * joylashuvga mos kelmasligi mumkin. "O'ngdagi ekran" deganda esa
 * foydalanuvchi haqiqiy joylashuvni nazarda tutadi.
 */
function monitorsByPosition() {
  return [...((host && host.monitors) || [])].sort((a, b) => a.x - b.x || a.y - b.y);
}

/** Ekranning foydalanuvchi ko'radigan raqami: chapdan o'ngga 1, 2, 3...
 *
 * Tizimdagi raqam qurilma nomiga bog'liq va jismoniy joylashuvga mos
 * kelmasligi mumkin. Foydalanuvchi esa "chapdagi" va "o'ngdagi" deb
 * o'ylaydi, shuning uchun hamma joyda shu raqam ko'rsatiladi.
 */
function monitorLabel(index) {
  const at = monitorsByPosition().findIndex((m) => m.index === index);
  return (at < 0 ? index : at) + 1;
}

function makeTool(icon, caption, onTap, active) {
  const b = document.createElement("button");
  b.className = "tool" + (active ? " active" : "");
  const sp = document.createElement("span");
  sp.textContent = icon;
  const i = document.createElement("i");
  i.textContent = caption;
  b.append(sp, i);
  b.onclick = onTap;
  return b;
}

/** Pastki qatorning o'rtasi: ekranlar yoki sichqoncha tugmalari.
 *
 * Ekran almashtirish uchun avval videoning yon-veridagi qora chekkani
 * surish kerak edi, lekin u telefonda atigi 20 chogli piksel bo'lib
 * chiqdi - barmoq bilan aniq tegib bo'lmaydi. Tugmalar ishonchli.
 * Chekkani surish ham qoldirildi, u endi qo'shimcha yo'l.
 */
function buildToolMid() {
  const mid = $("toolMid");
  mid.innerHTML = "";
  const list = currentSource === "local" ? monitorsByPosition() : [];
  if (list.length >= 2) {
    list.forEach((m, i) => {
      mid.appendChild(makeTool(
        String(i + 1), "ekran",
        () => selectMonitor(m.index),
        m.index === currentMonitor,
      ));
    });
  } else {
    // Bitta ekranda almashtiradigan narsa yo'q - sichqoncha tugmalari
    mid.appendChild(makeTool("🖯", "chap",
      () => link.send({ t: "mouse", a: "click", b: "left" })));
    mid.appendChild(makeTool("🖱", "o‘ng",
      () => link.send({ t: "mouse", a: "click", b: "right" })));
  }
}

function buildMonbar() {
  const bar = $("monbar");
  const list = monitorsByPosition();
  bar.hidden = list.length < 2;
  if (bar.hidden) return;
  bar.innerHTML = "";
  for (const m of list) {
    const d = document.createElement("div");
    d.className = "md" + (m.index === currentMonitor ? " on" : "");
    d.dataset.index = String(m.index);
    bar.appendChild(d);
  }
}

let monbarTimer = null;
function flashMonbar() {
  const bar = $("monbar");
  if (bar.hidden) return;
  bar.classList.add("show");
  clearTimeout(monbarTimer);
  monbarTimer = setTimeout(() => bar.classList.remove("show"), 1600);
}

function selectMonitor(index) {
  if (index === currentMonitor) return;
  currentMonitor = index;
  prefs.monitor = index;
  savePrefs();
  buildMonbar();
  buildToolMid();
  if (host) buildMonitors(host.monitors);
  resetView();
  link.send({ t: "view", on: true, monitor: index });
  flashMonbar();
  navigator.vibrate?.(12);
  toast(`${monitorLabel(index)}-ekran`);
}

/** Yonidagi ekranga o'tadi. dir: +1 o'ngdagi, -1 chapdagi. */
function switchMonitorBy(dir) {
  const list = monitorsByPosition();
  if (list.length < 2) return false;
  const at = list.findIndex((m) => m.index === currentMonitor);
  const next = list[at + dir];
  if (!next) return false;      // chekkadagi ekran - aylanmaymiz
  selectMonitor(next.index);
  return true;
}

/** Qora chekkadagi tegish qaysi nuqtaga tushdi. */
function monbarDotAt(x, y) {
  const bar = $("monbar");
  if (bar.hidden) return null;
  const r = bar.getBoundingClientRect();
  if (x < r.left - 12 || x > r.right + 12 || y < r.top - 12 || y > r.bottom + 12) return null;
  for (const d of bar.children) {
    const dr = d.getBoundingClientRect();
    if (x >= dr.left - 9 && x <= dr.right + 9) return Number(d.dataset.index);
  }
  return null;
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

    // Videodan tashqaridagi qora chekka - ekran almashtirgich.
    // Sozlamalarga kirmasdan bitta surish bilan qo'shni ekranga
    // o'tish uchun.
    // Ko'rsatkichning o'zi ham hisoblanadi: u qora chekkadan bir oz
    // balandroq va pastki qismi videoga tegib turadi.
    const n0 = pointToNorm(p.x, p.y);
    const onBar = monbarDotAt(p.x, p.y) !== null;
    if ((onBar || (n0 && n0.outside)) && monitorsByPosition().length > 1) {
      touch.gesture = "edge";
      flashMonbar();
      return;
    }

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
    } else {
      if (prefs.mode === "touch") {
        const n = pointToNorm(p.x, p.y);
        if (n && n.inside) sendMove(n.x, n.y);
      }
      // Bosib turib ushlash - ikkala rejimda ham. Avval bu faqat
      // sensor rejimida va o'ng tugma uchun ishlatilardi; o'ng tugma
      // ikki barmoq bilan tegishda qoldi.
      touch.longPress = setTimeout(() => {
        touch.longPress = null;
        if (!touch.movedEnough && touch.pts.size === 1 && !touch.dragging) {
          beginDrag();
          grabFeedback(true);
          moveHint(touch.last.x, touch.last.y);
        }
      }, HOLD_GRAB_MS);
    }
  } else if (pts.length === 2) {
    clearTimers();
    touch.gesture = "two";
    touch.two = null;
    touch.moved = false;
    touch.startAt = now;
    touch.lastDist = touch.startDist = dist(pts[0], pts[1]);
    touch.lastMid = touch.startMid = mid(pts[0], pts[1]);
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

  if (touch.gesture === "edge" && pts.length === 1) {
    const p = pts[0];
    const dx = p.x - touch.start.x;
    const dy = p.y - touch.start.y;
    touch.last = { ...p };
    if (Math.abs(dx) > EDGE_SWIPE_PX && Math.abs(dx) > Math.abs(dy)) {
      touch.moved = true;
      switchMonitorBy(dx > 0 ? 1 : -1);
      touch.gesture = "done";      // bitta surishda bitta ekran
    }
    return;
  }

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

    if (touch.dragging) moveHint(p.x, p.y);

    if (prefs.mode === "touch") {
      const n = pointToNorm(p.x, p.y);
      if (n && n.inside) sendMove(n.x, n.y);
    } else {
      const k = prefs.sens / view.zoom;
      const [mu, mv] = unrotate(dx, dy);
      link.send({ t: "mouse", a: "moveby", dx: mu * k, dy: mv * k });
    }
  } else if (touch.gesture === "two" && pts.length === 2) {
    const d = dist(pts[0], pts[1]);
    const m = mid(pts[0], pts[1]);
    const dd = d - touch.lastDist;
    const dmy = m.y - touch.lastMid.y;
    const dmx = m.x - touch.lastMid.x;

    if (!touch.two) {
      // Turkumlash boshlanishidan emas, YETARLI harakat to'planganidan
      // keyin qilinadi. Birinchi touchmove'da barmoqlar orasi bir-ikki
      // pikselga o'zgaradi, xolos - o'sha payt qaror qilinsa har doim
      // "aylantirish" chiqib qolar va yaqinlashtirish umuman ishlamasdi.
      const spread = Math.abs(d - touch.startDist);
      const slide = Math.hypot(m.x - touch.startMid.x, m.y - touch.startMid.y);
      if (Math.max(spread, slide) > 14) {
        if (spread > slide) touch.two = "zoom";
        else touch.two = view.zoom > 1.02 ? "pan" : "scroll";
        if (touch.two === "scroll") {
          // Aylantirish o'qini bir marta tanlaymiz. Ko'rinish burilgan
          // bo'lsa foydalanuvchi telefonni ham burib ushlashi mumkin,
          // shuning uchun qaysi tomonga surgan bo'lsa - o'sha o'q.
          // Avval barmoq yo'nalishi majburan video yo'nalishiga
          // o'girilardi va tik surish umuman aylantirmasdi.
          const sy = Math.abs(m.y - touch.startMid.y);
          const sx = Math.abs(m.x - touch.startMid.x);
          touch.scrollAxis = (view.rot === 0 || sy >= sx) ? "y" : "x";
        }
      }
    }

    if (touch.two === "zoom") {
      const prev = view.zoom;
      view.zoom = clamp(view.zoom * (1 + dd / 220), 1, 8);
      if (view.zoom <= 1.02) { view.zoom = 1; view.panX = view.panY = 0; }
      else if (prev > 1.01) {
        // Yaqinlashtirganda barmoqlar orasidagi nuqta joyida qolsin
        const f = view.zoom / prev;
        const r2 = stage.getBoundingClientRect();
        const ax = m.x - (r2.left + r2.width / 2);
        const ay = m.y - (r2.top + r2.height / 2);
        view.panX = ax - (ax - view.panX) * f;
        view.panY = ay - (ay - view.panY) * f;
      }
      applyView();
    } else if (touch.two === "pan") {
      view.panX += dmx;
      view.panY += dmy;
      applyView();
    } else if (touch.two === "scroll") {
      const sv = touch.scrollAxis === "x"
        ? (view.rot === 270 ? dmx : -dmx)
        : dmy;
      touch.scrollAcc += sv;
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

  if (touch.gesture === "edge" || touch.gesture === "done") {
    if (remaining === 0) {
      if (!touch.moved && touch.last) {
        const idx = monbarDotAt(touch.last.x, touch.last.y);
        if (idx !== null) selectMonitor(idx);
      }
      touch.gesture = null;
      touch.moved = false;
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

  // Ushlash chegarasigacha bo'lgan har qanday tegish - oddiy bosish.
  // Aks holda 400 ms bilan 800 ms orasida "hech narsa bo'lmaydigan"
  // bo'shliq qolardi.
  const isTap = before === 1 && remaining === 0 && touch.gesture === "point"
    && !touch.movedEnough && dt < HOLD_GRAB_MS;

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
  if (p && p.inside) sendMove(p.x, p.y);
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

/* Kompyuter brauzerida haqiqiy klaviatura ishlaydi: bosilgan klavish
 * narigi tomonga uzatiladi. Telefonda bu kerak emas - u yerda ekrandagi
 * tugmalar qatori bor, va telefon klaviaturasi keydown'da harflarni
 * ishonchli bermaydi. */
const DESK_KEYS = new Set([
  "Enter", "Backspace", "Tab", "Escape", "Delete", "Home", "End",
  "PageUp", "PageDown", "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight",
  "F1", "F2", "F3", "F4", "F5", "F6", "F7", "F8", "F9", "F10", "F11", "F12",
]);

addEventListener("keydown", (e) => {
  if (!deskQuery.matches) return;
  // Sozlamalardagi maydonlarga yozayotgan bo'lsa - tegmaymiz
  const el = document.activeElement;
  if (el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA")) return;
  if (!streaming) return;

  const mods = [];
  if (e.ctrlKey) mods.push("ctrl");
  if (e.altKey) mods.push("alt");
  if (e.shiftKey) mods.push("shift");
  if (e.metaKey) mods.push("win");

  // F5 va Ctrl+R sahifani yangilab yuborardi - narigi tomonga ketsin
  if (mods.length && (e.key.length === 1 || DESK_KEYS.has(e.key))) {
    e.preventDefault();
    link.send({ t: "combo", keys: [...mods, e.key.toLowerCase()] });
  } else if (DESK_KEYS.has(e.key)) {
    e.preventDefault();
    link.send({ t: "key", a: "tap", k: e.key });
  } else if (e.key.length === 1) {
    // Bitta belgi - matn sifatida yuboramiz: shunda unicode va
    // o'zbek harflari ham to'g'ri tushadi.
    e.preventDefault();
    link.send({ t: "text", s: e.key });
  }
});

/* -------------------------------------------------------- tugmalar */

$("btnDouble").addEventListener("click", () => {
  link.send({ t: "mouse", a: "dblclick", b: "left" });
  navigator.vibrate?.(10);
});

/* -- ko'rinish tugmalari ------------------------------------------------ */

function toggleRotate() {
  // Uch holat: tik -> chapga -> o'ngga. Telefonni qaysi tomonga burish
  // odat bo'lsa, o'shanisini tanlash uchun ikkala yo'nalish ham bor.
  const next = { 0: 90, 90: 270, 270: 0 }[view.rot] ?? 90;
  view.rot = next;
  view.panX = view.panY = 0;
  if (next !== 0) prefs.rotDir = next;
  // Qo'lda burilganda avtomatik tanlov o'chadi, aks holda keyingi qayta
  // hisobda tanlov bekor bo'lib ketardi.
  view.auto = next === 0 ? false : view.auto;
  if (next === 0) {
    prefs.autoRotate = false;
    $("chkAutoRotate").checked = false;
  }
  savePrefs();
  applyView();
  toast(next === 0 ? "Tik holat" : (next === 90 ? "Yotqizildi ↶" : "Yotqizildi ↷"));
}

async function toggleFullscreen() {
  try {
    if (document.fullscreenElement) {
      await document.exitFullscreen();
      $("btnFull").classList.remove("on");
      return;
    }
    await document.documentElement.requestFullscreen({ navigationUI: "hide" });
    $("btnFull").classList.add("on");
    // Yotqizishni so'raymiz. Ko'p brauzerlar buni faqat to'liq ekranda
    // qabul qiladi, ba'zilari umuman qo'llab-quvvatlamaydi - shuning
    // uchun rad javobi xato hisoblanmaydi.
    try { await screen.orientation.lock("landscape"); } catch {}
  } catch {
    toast("Brauzer to‘liq ekranga ruxsat bermadi");
  }
  setTimeout(refreshView, 200);
}

// Manbani almashtirish: ro'yxat bo'ylab aylanadi. Ikkitadan ko'p
// manba bo'lsa sozlamalardagi to'liq ro'yxat qulayroq, lekin odatiy
// holat - "kompyuter <-> telefon", unga bitta bosish yetadi.
$("btnSource").addEventListener("click", () => {
  const list = sourceList();
  if (list.length < 2) return;
  const i = list.findIndex((s) => s.id === currentSource);
  selectSource(list[(i + 1) % list.length].id);
});

$("btnRotate").addEventListener("click", toggleRotate);
$("btnRotate2").addEventListener("click", toggleRotate);
$("btnFull").addEventListener("click", toggleFullscreen);
$("btnFull2").addEventListener("click", toggleFullscreen);
$("btnFit").addEventListener("click", () => {
  view.auto = prefs.autoRotate !== false;
  resetView();
  toast("O‘lchamga solindi");
});
$("btnZoom").addEventListener("click", () => {
  view.zoom = 1;
  view.panX = view.panY = 0;
  applyView();
});
$("chkAutoRotate").addEventListener("change", (e) => {
  prefs.autoRotate = e.target.checked;
  view.auto = e.target.checked;
  savePrefs();
  refreshView();
});
document.addEventListener("fullscreenchange", () => {
  $("btnFull").classList.toggle("on", !!document.fullscreenElement);
  setTimeout(refreshView, 150);
});

$("btnMode").addEventListener("click", () => {
  prefs.mode = prefs.mode === "trackpad" ? "touch" : "trackpad";
  savePrefs();
  updateModeButton();
  toast(prefs.mode === "trackpad"
    ? "Trackpad: barmoq surilsa kursor siljiydi"
    : "Sensor rejimi: qayerga bossang, sichqoncha o‘sha yerga bosadi");
});

function updateModeButton() {
  const b = $("btnMode");
  const direct = prefs.mode === "touch";
  b.querySelector("span").textContent = direct ? "👆" : "🖱";
  b.querySelector("i").textContent = direct ? "sensor" : "trackpad";
  // Faol holat ko'rinib tursin: tugma qaysi rejim YOQILGANINI ko'rsatadi,
  // bosilganda ikkinchisiga o'tadi.
  b.classList.toggle("active", direct);
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
  monitorsByPosition().forEach((m, i) => {
    const b = document.createElement("button");
    b.className = "btn" + (currentMonitor === m.index ? " on" : "");
    b.textContent = `${i + 1}-ekran · ${m.w}×${m.h}${m.primary ? " ★" : ""}`;
    b.onclick = () => selectMonitor(m.index);
    row.appendChild(b);
  });
}

function sourceLabel(src) {
  return src.kind === "pc" ? `💻 ${src.name}` : `📱 ${src.name}`;
}

function sourceList() {
  return sources.length ? sources
    : [{ id: "local", name: (host && host.name) || "Kompyuter", kind: "pc" }];
}

function buildSources() {
  const row = $("sourceRow");
  row.innerHTML = "";
  const list = sourceList();
  list.forEach((src) => {
    const b = document.createElement("button");
    b.className = "btn" + (currentSource === src.id ? " on" : "");
    b.textContent = sourceLabel(src);
    b.onclick = () => selectSource(src.id);
    row.appendChild(b);
  });
  // Ekran tanlash faqat kompyuterda ma'noga ega - telefonda bitta ekran
  $("monitorSection").hidden = currentSource !== "local";
  $("sourceHint").hidden = list.length > 1;

  // Manba tugmasi yuqori qatorda ham turadi. Ilgari u faqat
  // sozlamalar ichida edi va telefonni kompyuterda ko'rish yo'lini
  // topib bo'lmasdi - ko'rinmagan imkoniyat yo'q imkoniyat bilan teng.
  const chip = $("btnSource");
  chip.hidden = list.length < 2;
  if (!chip.hidden) {
    const cur = list.find((s) => s.id === currentSource) || list[0];
    chip.textContent = sourceLabel(cur);
  }
}

/* Telefon so'ralgan, lekin hali ulanmagan bo'lsa - nima qilish
 * kerakligini aytib turamiz. Bo'sh qora ekran hech narsa tushuntirmaydi. */
function updateWaiting() {
  if (wantView !== "phone" || autoPicked) return;
  if (sources.some((s) => s.kind !== "pc")) return;
  setPlaceholder(
    "Telefon hali ulanmagan.\n" +
    "Ilova bo‘lsa: kompyuterni tanlab «Ekranimni uzatish» ni bosing.\n" +
    "Bo‘lmasa: QR kodni skanerlang — ilova o‘zi ochiladi.",
    null
  );
  showPairBox();
}

/* Ulash uchun QR - asosiy oynaning o'zida. Shunda "telefonni qayerda
 * ko'raman" va "qanday ulayman" bitta joyda javob topadi; ilgari
 * QR alohida sahifada edi va uni izlab yurish kerak bo'lardi. */
let pairLoaded = false;
async function showPairBox() {
  const box = $("pairBox");
  box.hidden = false;
  if (pairLoaded) return;
  try {
    const r = await fetch(`/api/pair?k=${encodeURIComponent(link.token)}`,
                          { cache: "no-store" });
    const d = await r.json();
    $("pairQr").innerHTML = d.svg || "";
    $("pairLink").textContent = d.link || "";
    $("pairSend").hidden = !d.telegram;
    pairLoaded = true;
  } catch (e) {
    $("pairQr").textContent = "QR yuklanmadi";
  }
}

function pairSay(text, bad) {
  const el = $("pairSaid");
  el.textContent = text;
  el.className = "pair-said" + (bad ? " bad" : "");
}

$("pairCopy").addEventListener("click", () => {
  navigator.clipboard.writeText($("pairLink").textContent).then(
    () => pairSay("Nusxalandi"),
    () => pairSay("Nusxalab bo‘lmadi", true)
  );
});

$("pairSend").addEventListener("click", async () => {
  const b = $("pairSend");
  b.disabled = true;
  pairSay("Yuborilmoqda…");
  try {
    const r = await fetch(`/api/pair/send?k=${encodeURIComponent(link.token)}`,
                          { method: "POST" });
    const d = await r.json();
    pairSay(d.ok ? "Telegramga yuborildi" : (d.msg || "Yuborilmadi"), !d.ok);
  } catch (e) {
    pairSay("Yuborilmadi", true);
  }
  b.disabled = false;
});

/* view=phone bilan ochilganda ulangan telefonni o'zi tanlaydi.
 * Telefon keyinroq ulansa ham ishlaydi: "sources" xabari kelganda
 * yana tekshiriladi. */
let autoPicked = false;
function autoPickSource(quiet) {
  if (autoPicked || wantView !== "phone") return false;
  const phone = sources.find((s) => s.kind !== "pc");
  if (!phone) return false;
  autoPicked = true;
  if (quiet) {
    currentSource = phone.id;
    return true;
  }
  selectSource(phone.id);
  return true;
}

function selectSource(id) {
  if (id === currentSource) return;
  currentSource = id;
  decoder.close();
  setPlaceholder("Ulanmoqda…");
  buildSources();
  buildToolMid();
  buildMonbar();
  updateHostLabel();
  resetView();
  // startStream() ishlatiladi, chunki u sifat sozlamalarini ham
  // yuboradi va "oqim yonyapti" holatini belgilaydi. Yalang'och
  // "view" xabari kadrlarni keltirardi, lekin sozlamalar standart
  // bo'lib qolar va klaviatura ishlamasdi.
  startStream();
  const src = sources.find((x) => x.id === id);
  toast(src ? sourceLabel(src) : id);
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
  $("chkAutoRotate").checked = prefs.autoRotate !== false;
  view.auto = prefs.autoRotate !== false;
  updateModeButton();
  refreshView();
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

$("chkFollow").onchange = (e) => {
  link.send({ t: "view", on: true, follow: e.target.checked });
  toast(e.target.checked
    ? "Kursor ekranlar orasida yuradi, ko‘rinish unga ergashadi"
    : "Kursor shu ekrandan chiqmaydi");
};

$("btnSwapMonitors").onclick = () => link.send({ t: "swap_monitors" });

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
