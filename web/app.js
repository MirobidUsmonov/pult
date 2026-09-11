/*
 * Pult - the phone side.
 *
 * Video is decoded through WebCodecs: the H.264 frames from the computer
 * go straight to the phone's hardware decoder. That is what keeps
 * latency low and battery use small.
 */

const $ = (id) => document.getElementById(id);
const clamp = (v, a, b) => Math.min(b, Math.max(a, v));

const KEY_STORE = "pult.key";
const PREF_STORE = "pult.prefs";

/* "view=phone" in the link asks for the connected phone to be picked
 * automatically. "Watch the phone screen" in the computer's tray opens
 * it with that marker. readToken() cleans the address bar, so this
 * value has to be read BEFORE it runs. */
const wantView = (location.hash.match(/[#&]view=([^&]+)/) || [])[1] || "";

/* The page opens both on a phone and in a desktop browser. On a
 * computer it needs a different shape: a mouse instead of finger
 * gestures, a real keyboard instead of the on-screen key row.
 *
 * The look is switched by @media in the CSS; only the query for
 * behaviour is kept here. The important part: the value is NOT computed
 * up front. While the page is loading the window size can still be
 * wrong, and a value computed once would stick with that mistake. */
const deskQuery = matchMedia("(pointer: fine) and (min-width: 700px)");

/* ------------------------------------------------------------------ key */

function readToken() {
  const m = location.hash.match(/[#&]k=([^&]+)/);
  if (m) {
    const t = decodeURIComponent(m[1]);
    localStorage.setItem(KEY_STORE, t);
    // The key does not stay in the address bar: it must not turn up in
    // browser history or in screenshots. Only the key is stripped - the
    // rest of the markers (view=phone, for one) are not secret and have
    // to survive, otherwise a page refresh would lose the request and
    // the view would fall back to another source.
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

/* -------------------------------------------------------------- decoder */

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
      // The video size changed - the layout has to be recomputed
      if (this.onResize) this.onResize(w, h);
    }
    this.ctx.drawImage(frame, 0, 0);
    frame.close();
    this.frames++;
    // Reported on every frame, not only the first: when the stream
    // restarts (coming back to the app, changing quality) the "Waiting
    // for the screen" text appears again and has to be hidden again.
    if (this.onFrame) this.onFrame();
    if (this.frames === 1 && this.onFirstFrame) this.onFirstFrame();
  }

  fail(err) {
    console.warn("decoder error:", err);
    // Rebuild the decoder and wait for the next key frame, so a single
    // corrupt frame cannot stop the whole stream.
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

/* ------------------------------------------------------------ connection */

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
      // A slowly growing wait: with no network, do not cook the phone
      // with non-stop retries.
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
  return "Browser";
}

/* ------------------------------------------------------------------ UI */

const canvas = $("screen");
const stage = $("stage");
const decoder = new Decoder(canvas);
const link = new Link(readToken());

let host = null;
let streaming = false;
// Which screen the server is showing right now. The server changes this
// itself when the cursor moves between screens, so we trust its answer
// rather than the choice stored in the settings.
let currentMonitor = 0;
// Which source is being watched: "local" is the computer itself, or the
// id of a connected phone.
let currentSource = "local";
let sources = [];
/*
 * The view state.
 *
 * The canvas size and position follow this bookkeeping, not the CSS.
 * The reason is rotation: getBoundingClientRect() on a rotated element
 * returns its outer bounding box, which is useless for working out
 * click coordinates. So we keep the size and the angle ourselves and
 * invert the transform by hand.
 */
const view = {
  rot: 0,          // 0 or 90 degrees
  auto: true,      // rotate by itself when the phone is upright
  zoom: 1,
  panX: 0,
  panY: 0,
  k: 1,            // the video's real scale on screen
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
  // The QR code only belongs to the "waiting for a phone" state - other
  // messages (connecting, dropped) must not show it
  $("pairBox").hidden = true;
}

function setDot(cls, label) {
  $("dot").className = "dot " + cls;
  $("infoState").textContent = label;
}

link.onState = (state) => {
  if (state === "open") {
    setDot("on", "connected");
  } else if (state === "nokey") {
    setDot("off", "no key");
    setPlaceholder(
      "No key found. Open the full link that Pult gave you on the " +
      "computer (it has to end with #k=…).",
      null
    );
  } else if (state === "closed") {
    setDot("off", "dropped");
    setPlaceholder("The connection dropped. Reconnecting…", null);
  } else {
    setDot("warn", state === "reconnecting" ? "reconnecting" : "connecting");
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
    // If a phone was asked for, pick it before the stream starts, so
    // the computer screen is not turned on once for nothing.
    autoPickSource(true);
    buildMonitors(host.monitors);
    buildMonbar();
    buildToolMid();
    buildSources();
    updateHostLabel();
    buildCommands(host.commands || []);
    // The microphone needs both sides: a model on the computer and a
    // recorder in this browser. Missing either, the button stays away
    // rather than failing when pressed.
    if (mic) mic.hidden = !(host.stt && micSupported());
    applyPrefsToUi();
    if (!decoder.supported) {
      setPlaceholder(
        "This browser does not support video decoding (WebCodecs). " +
        "Use Chrome on Android, or Safari on iOS 17+.",
        null
      );
      return;
    }
    // A phone was asked for but has not connected yet, so we wait.
    // Turning the computer screen on here would be harmful: the page is
    // open on that same computer, so the screen would mirror itself
    // endlessly.
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
    $("infoSize").textContent = `${msg.w}×${msg.h} · ${msg.fps} fps`;
    if (msg.codec) decoder.configure(msg.codec);
  } else if (msg.t === "stats") {
    // With no stream, show no numbers: the server keeps sending the
    // last values and they left a false impression that something was
    // still running.
    $("stats").textContent = streaming
      ? `${msg.fps} fps · ${fmtRate(msg.kbps)}` : "";
    $("infoRtt").textContent = link.rtt ? `${link.rtt} ms` : "—";
  } else if (msg.t === "blocked") {
    /* Windows will not let anything capture the sign-in screen, so
     * while the computer is locked there is genuinely nothing to send.
     * Saying so beats a black screen that looks identical to a slow
     * connection - and it comes back by itself, so nobody needs to
     * reconnect. */
    if (msg.reason) {
      canvas.classList.add("hidden");
      // The text comes from the computer: only that side knows what is
      // in the way and whether it will clear by itself.
      setPlaceholder(msg.msg || "The screen cannot be shared right now.", null);
    } else {
      setPlaceholder("Waiting for the screen…");
    }
  } else if (msg.t === "error") {
    toast(msg.msg);
  } else if (msg.t === "cmd_ok") {
    toast(msg.result || "done");
  } else if (msg.t === "monitor_map") {
    toast("The screens were swapped");
  } else if (msg.t === "sources") {
    sources = msg.list || [];
    buildSources();
    autoPickSource(false);
    updateWaiting();
  } else if (msg.t === "source_gone") {
    toast("The source dropped");
    currentSource = "local";
    // Let it be picked again by itself if a phone reconnects
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
  // When hidden is already true this does nothing, so calling it on
  // every frame is cheap.
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

// Recompute when the screen rotates or the window is resized. After
// orientationchange the browser does not update the sizes immediately,
// so we repeat it after a short delay.
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
  setPlaceholder("Waiting for the screen…");
  link.send({
    t: "view", on: true,
    source: currentSource,
    // The server's current screen: if the cursor has moved between
    // screens the server may already be on another one, and we do not
    // force it back to the old choice.
    monitor: currentMonitor,
    fps: prefs.fps, width: prefs.width,
    bitrate: prefs.bitrate, cursor: prefs.cursor,
  });
}

function stopStream() {
  streaming = false;
  link.send({ t: "view", on: false });
}

/* -------------------------------------------------------------- input */

let lastMoveSent = 0;
function sendMove(x, y) {
  const now = performance.now();
  if (now - lastMoveSent < 12) return;   // no faster than ~80/s
  lastMoveSent = now;
  link.send({ t: "mouse", a: "move", x, y });
}

/** Turns a point on screen into a relative position (0..1) in the video. */
function pointToNorm(cx, cy) {
  const vw = canvas.width, vh = canvas.height;
  const r = stage.getBoundingClientRect();
  // While the canvas has not been drawn yet (no first frame, app in the
  // background, screen mid-rotation) the size is zero. Dividing by zero
  // gives NaN, and an invalid coordinate would go to the server.
  if (!vw || !vh || !view.k || r.width < 1 || r.height < 1) return null;

  const dx = cx - (r.left + r.width / 2) - view.panX;
  const dy = cy - (r.top + r.height / 2) - view.panY;
  const [u, v] = unrotate(dx, dy);
  const x = 0.5 + u / (vw * view.k);
  const y = 0.5 + v / (vh * view.k);
  return {
    x: clamp(x, 0, 1),
    y: clamp(y, 0, 1),
    // The raw value: outside 0..1 the finger is not on the video itself
    // but in the black margin beside it. It must stay unclamped,
    // because the margin can be only a few pixels wide.
    rx: x,
    ry: y,
    // A small tolerance for clicks: a touch that lands a pixel or two
    // past the edge should not be thrown away.
    inside: x >= -0.02 && x <= 1.02 && y >= -0.02 && y <= 1.02,
    // The screen switcher, though, needs an exact boundary.
    outside: x < 0 || x > 1 || y < 0 || y > 1,
  };
}

/** Maps a screen direction into the video's own direction.
 *
 * 90  - clockwise: the top of the video lands on the right edge of the
 *       screen (the phone is turned left to watch).
 * 270 - the other way round.
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
  // Rotated, the video fits the screen on its side
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

/** Lays the video on its side while the phone is upright. */
function autoRotate() {
  if (!view.auto) return;
  const r = stage.getBoundingClientRect();
  const portrait = r.height > r.width;
  const wide = canvas.width >= canvas.height;
  // Which way to rotate is the user's choice, and it is remembered
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

/** Clicks at a point, or where the cursor is when there is none. */
function clickAt(point, button = "left") {
  const n = point ? pointToNorm(point.x, point.y) : null;
  if (n && !n.inside) return;          // the black margin was touched
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

/** Signals the grabbed / released state. */
function grabFeedback(on) {
  const hint = $("cursorHint");
  clearTimeout(hint._t);
  if (on) {
    hint.classList.add("grab", "show");
    navigator.vibrate?.([14, 45, 28]);
    toast("Grabbed — drag it, lift your finger to drop it");
  } else {
    hint.classList.remove("grab", "show");
  }
}

/* Gesture settings. All of them in pixels and milliseconds. */
// The thresholds match Windows conventions: its double-click interval
// is 500 ms by default. The earlier 330 ms was far too short - press a
// little slower and the second click counted as a separate one.
const TAP_MS = 400;         // lifted faster than this counts as a tap
const DOUBLE_MS = 500;      // the longest gap between two taps
const DOUBLE_PX = 55;       // how close the second tap has to land
const DRAG_HOLD_MS = 320;   // hold the second touch this long to drag
const MOVE_START_PX = 5;    // so a trembling finger is not a movement
const SWITCH_START_PX = 45; // three fingers: where window switching starts
const SWITCH_STEP_PX = 75;  // every this much of a drag: the next window
const SWITCH_VERT_PX = 70;  // three fingers: up / down
const EDGE_SWIPE_PX = 55;   // a swipe in the black margin: switch screens
// Press and hold to grab. Double-tap-and-drag is a trackpad habit and
// turned out to be hard to perform on a phone; this is the simpler way:
// hold to grab, drag, lift to drop.
const HOLD_GRAB_MS = 800;

const touch = {
  pts: new Map(),
  gesture: null,      // 'point' | 'two' | 'switch' | 'done'
  two: null,          // 'scroll' | 'zoom' | 'pan'
  anchor: null,       // the point a click is sent to
  start: null,        // where the finger landed
  last: null,
  startAt: 0,
  moved: false,
  movedEnough: false,
  dragging: false,
  dragTimer: null,
  longPress: null,
  lastTap: null,      // {x, y, at} - the previous tap
  isSecondTap: false,
  lastDist: 0,
  lastMid: null,
  startDist: 0,
  startMid: null,
  scrollAcc: 0,
  scrollAxis: "y",    // which axis scrolling follows
  sw: null,           // the three-finger state
};

// For troubleshooting: adding ?debug to the address exposes the
// internal state in the browser console. Gestures are delicate and there
// is no other way to inspect them.
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

/* -- the screen indicator and switcher ---------------------------------- */

/** The screens by physical position, left to right.
 *
 * The order in the list follows the device number, which need not match
 * the physical layout. When someone says "the right-hand screen" they
 * mean the real arrangement.
 */
function monitorsByPosition() {
  return [...((host && host.monitors) || [])].sort((a, b) => a.x - b.x || a.y - b.y);
}

/** The screen number the user sees: 1, 2, 3... from left to right.
 *
 * The system's number depends on the device name and need not match the
 * physical layout, while the user thinks "the left one" and "the right
 * one" - so this number is what is shown everywhere.
 */
function monitorLabel(index) {
  const at = monitorsByPosition().findIndex((m) => m.index === index);
  return (at < 0 ? index : at) + 1;
}

function makeTool(icon, caption, onTap, active) {
  const b = document.createElement("button");
  b.className = "tool" + (active ? " active" : "");
  const sp = document.createElement("span");
  // The icon can be an SVG (a line icon) or plain text (a digit, a letter)
  if (icon.startsWith("<svg")) sp.innerHTML = icon; else sp.textContent = icon;
  const i = document.createElement("i");
  i.textContent = caption;
  b.append(sp, i);
  b.onclick = onTap;
  return b;
}

/** The middle of the bottom row: screens, or mouse buttons.
 *
 * Switching screens used to need a swipe in the black margin beside the
 * video, but on a phone that margin turned out to be around 20 pixels -
 * too little to hit accurately with a finger. Buttons are reliable. The
 * margin swipe was kept as well; it is now an extra route.
 */
function buildToolMid() {
  const mid = $("toolMid");
  mid.innerHTML = "";
  const list = currentSource === "local" ? monitorsByPosition() : [];
  if (list.length >= 2) {
    list.forEach((m, i) => {
      mid.appendChild(makeTool(
        String(i + 1), "screen",
        () => selectMonitor(m.index),
        m.index === currentMonitor,
      ));
    });
  } else {
    // With one screen there is nothing to switch - mouse buttons then.
    // Letters rather than icons: together with the "left"/"right"
    // captions they are clearer.
    mid.appendChild(makeTool("L", "left",
      () => link.send({ t: "mouse", a: "click", b: "left" })));
    mid.appendChild(makeTool("R", "right",
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
  toast(`Screen ${monitorLabel(index)}`);
}

/** Moves to the neighbouring screen. dir: +1 right, -1 left. */
function switchMonitorBy(dir) {
  const list = monitorsByPosition();
  if (list.length < 2) return false;
  const at = list.findIndex((m) => m.index === currentMonitor);
  const next = list[at + dir];
  if (!next) return false;      // the outermost screen - no wrapping
  selectMonitor(next.index);
  return true;
}

/** Which dot a touch in the black margin landed on. */
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

/* -- three fingers: switching windows ----------------------------------- */

function switchStep(dir) {
  // With Alt held, Tab goes forward and Shift+Tab back
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
    // Releasing Alt brings the selected window to the front
    link.send({ t: "key", a: "up", k: "alt" });
  }
  touch.sw = null;
}

/* -- touch events -------------------------------------------------------- */

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

    // The black margin outside the video is the screen switcher: one
    // swipe moves to the neighbouring screen without opening the
    // settings.
    // The indicator itself counts too: it is a little taller than the
    // margin and its bottom edge touches the video.
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

    // The second click is deliberately sent to the FIRST one's position.
    // A finger never lands on exactly the same spot twice, and Windows
    // does not treat two clicks in different places as a double-click.
    touch.anchor = touch.isSecondTap
      ? { x: touch.lastTap.x, y: touch.lastTap.y }
      : { ...p };

    if (touch.isSecondTap) {
      // Do not start dragging right away: lifted quickly this is a
      // double-click, held it is a drag. The decision is deferred.
      touch.dragTimer = setTimeout(() => {
        touch.dragTimer = null;
        if (touch.pts.size === 1 && !touch.movedEnough) beginDrag();
      }, DRAG_HOLD_MS);
    } else {
      if (prefs.mode === "touch") {
        const n = pointToNorm(p.x, p.y);
        if (n && n.inside) sendMove(n.x, n.y);
      }
      // Press-and-hold to grab, in both modes. It used to work only in
      // touch mode and for the right button; the right button now lives
      // on a two-finger tap.
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
      touch.gesture = "done";      // one screen per swipe
    }
    return;
  }

  if (touch.gesture === "point" && pts.length === 1) {
    const p = pts[0];
    const dx = p.x - touch.last.x;
    const dy = p.y - touch.last.y;
    touch.last = { ...p };

    if (!touch.movedEnough) {
      // Ignore a small tremble at the start entirely - otherwise the
      // cursor drifts while tapping.
      if (dist(p, touch.start) <= MOVE_START_PX) return;
      touch.movedEnough = true;
      touch.moved = true;
      clearTimeout(touch.longPress);
      touch.longPress = null;
      if (touch.isSecondTap && touch.dragTimer) {
        // The second touch started moving - so it is a drag
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
      // Classified once ENOUGH movement has accumulated, not at the
      // start. On the first touchmove the distance between the fingers
      // changes by a pixel or two at most - deciding then always came
      // out as "scroll" and zooming never worked at all.
      const spread = Math.abs(d - touch.startDist);
      const slide = Math.hypot(m.x - touch.startMid.x, m.y - touch.startMid.y);
      if (Math.max(spread, slide) > 14) {
        if (spread > slide) touch.two = "zoom";
        else touch.two = view.zoom > 1.02 ? "pan" : "scroll";
        if (touch.two === "scroll") {
          // The scroll axis is chosen once. With the view rotated the
          // user may be holding the phone rotated too, so the axis
          // follows the direction they actually dragged. Before, the
          // finger direction was forced into the video's direction and
          // a vertical drag scrolled nothing at all.
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
        // While zooming, keep the point between the fingers in place
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
        // Alt stays down and is not released until the fingers lift, so
        // the window picker stays on screen and follows the drag.
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
        link.send({ t: "combo", keys: ["win", "Tab"] });   // task view
        toast("Task view");
      } else {
        link.send({ t: "combo", keys: ["win", "d"] });     // the desktop
        toast("Desktop");
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

  // Any touch shorter than the grab threshold is a plain tap.
  // Otherwise there was a gap between 400 ms and 800 ms where nothing
  // happened at all.
  const isTap = before === 1 && remaining === 0 && touch.gesture === "point"
    && !touch.movedEnough && dt < HOLD_GRAB_MS;

  if (isTap) {
    const a = touch.anchor;
    clickAt(prefs.mode === "touch" ? a : null, "left");
    showHint(a.x, a.y);
    navigator.vibrate?.(8);
    // After a second tap the count restarts, otherwise a third touch
    // would keep counting as "the second" one.
    touch.lastTap = touch.isSecondTap ? null : { x: a.x, y: a.y, at: now };
  } else if (before === 2 && remaining === 0 && !touch.moved && dt < TAP_MS) {
    // A two-finger tap is the right button (a trackpad habit)
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


// The mouse works too, for trying it from a desktop browser
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

/* In a desktop browser the real keyboard works: a pressed key is
 * forwarded to the other side. On a phone this is not needed - there is
 * the on-screen key row, and a phone keyboard does not report letters
 * reliably in keydown. */
const DESK_KEYS = new Set([
  "Enter", "Backspace", "Tab", "Escape", "Delete", "Home", "End",
  "PageUp", "PageDown", "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight",
  "F1", "F2", "F3", "F4", "F5", "F6", "F7", "F8", "F9", "F10", "F11", "F12",
]);

addEventListener("keydown", (e) => {
  if (!deskQuery.matches) return;
  // Leave it alone while typing into a settings field
  const el = document.activeElement;
  if (el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA")) return;
  if (!streaming) return;

  const mods = [];
  if (e.ctrlKey) mods.push("ctrl");
  if (e.altKey) mods.push("alt");
  if (e.shiftKey) mods.push("shift");
  if (e.metaKey) mods.push("win");

  // F5 and Ctrl+R used to reload the page - send them across instead
  if (mods.length && (e.key.length === 1 || DESK_KEYS.has(e.key))) {
    e.preventDefault();
    link.send({ t: "combo", keys: [...mods, e.key.toLowerCase()] });
  } else if (DESK_KEYS.has(e.key)) {
    e.preventDefault();
    link.send({ t: "key", a: "tap", k: e.key });
  } else if (e.key.length === 1) {
    // A single character goes as text, so unicode and non-Latin
    // letters arrive correctly.
    e.preventDefault();
    link.send({ t: "text", s: e.key });
  }
});

/* ----------------------------------------------------------- buttons */

/* There used to be a "2x" button here, for double-clicking. It was
 * removed: with "two" on its face it was easy to read as a button for
 * watching two screens at once, and it earned nothing - the
 * double-tap gesture already exists. */

/* -- the view buttons --------------------------------------------------- */

function toggleRotate() {
  // Three states: upright -> left -> right. Both directions are there
  // so people can pick whichever way they habitually turn the phone.
  const next = { 0: 90, 90: 270, 270: 0 }[view.rot] ?? 90;
  view.rot = next;
  view.panX = view.panY = 0;
  if (next !== 0) prefs.rotDir = next;
  // Rotating by hand turns the automatic choice off, otherwise the next
  // recalculation would undo the choice.
  view.auto = next === 0 ? false : view.auto;
  if (next === 0) {
    prefs.autoRotate = false;
    $("chkAutoRotate").checked = false;
  }
  savePrefs();
  applyView();
  toast(next === 0 ? "Upright" : (next === 90 ? "Laid down ↶" : "Laid down ↷"));
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
    // Ask for landscape. Most browsers only accept this in full screen
    // and some do not support it at all, so a refusal is not an error.
    try { await screen.orientation.lock("landscape"); } catch {}
  } catch {
    toast("The browser refused full screen");
  }
  setTimeout(refreshView, 200);
}

// Switching source: it cycles through the list. With more than two
// sources the full list in the settings is more convenient, but the
// usual case is "computer <-> phone", where one press is enough.
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
  toast("Fitted to the screen");
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
    ? "Trackpad: drag your finger and the cursor moves"
    : "Touch mode: the mouse clicks wherever you tap");
});

/* The mode button's icons are line SVGs, not emoji: emoji are drawn
 * differently on every device and their colour cannot be controlled. */
const MODE_ICONS = {
  trackpad: '<svg class="ic" viewBox="0 0 24 24"><rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 14h18M12 14v5"/></svg>',
  touch: '<svg class="ic" viewBox="0 0 24 24"><circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="2.5"/></svg>',
};

function updateModeButton() {
  const b = $("btnMode");
  const direct = prefs.mode === "touch";
  b.querySelector("span").innerHTML = direct ? MODE_ICONS.touch : MODE_ICONS.trackpad;
  b.querySelector("i").textContent = direct ? "touch" : "trackpad";
  // Show the active state: the button says which mode is ON, and
  // pressing it switches to the other one.
  b.classList.toggle("active", direct);
}

/* ---------------------------------------------------------- keyboard */

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

/* ----------------------------------------------------------- settings */

function buildMonitors(monitors) {
  const row = $("monitorRow");
  row.innerHTML = "";
  monitorsByPosition().forEach((m, i) => {
    const b = document.createElement("button");
    b.className = "btn" + (currentMonitor === m.index ? " on" : "");
    b.textContent = `Screen ${i + 1} · ${m.w}×${m.h}${m.primary ? " ★" : ""}`;
    b.onclick = () => selectMonitor(m.index);
    row.appendChild(b);
  });
}

function sourceLabel(src) {
  return src.kind === "pc" ? `Computer · ${src.name}` : `Phone · ${src.name}`;
}

function sourceList() {
  return sources.length ? sources
    : [{ id: "local", name: (host && host.name) || "Computer", kind: "pc" }];
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
  // Choosing a screen only means something on a computer - a phone has one
  $("monitorSection").hidden = currentSource !== "local";
  $("sourceHint").hidden = list.length > 1;

  // The source button sits in the top bar as well. It used to live only
  // inside the settings, and there was no way to find how to watch the
  // phone on the computer - a feature nobody can see is a feature that
  // does not exist.
  const chip = $("btnSource");
  chip.hidden = list.length < 2;
  if (!chip.hidden) {
    const cur = list.find((s) => s.id === currentSource) || list[0];
    chip.textContent = sourceLabel(cur);
  }
}

/* When a phone was asked for but has not connected, say what to do
 * about it. An empty black screen explains nothing. */
function updateWaiting() {
  if (wantView !== "phone" || autoPicked) return;
  if (sources.some((s) => s.kind !== "pc")) return;
  setPlaceholder(
    "No phone is connected yet.\n" +
    "With the app: pick this computer and press \u201cShare my screen\u201d.\n" +
    "Without it: scan the QR code — the app opens itself.",
    null
  );
  showPairBox();
}

/* The pairing QR code, inside the main window. That way "where do I see
 * my phone" and "how do I connect it" are answered in one place; the QR
 * code used to be on a separate page that had to be hunted for. */
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
    $("pairQr").textContent = "The QR code did not load";
  }
}

function pairSay(text, bad) {
  const el = $("pairSaid");
  el.textContent = text;
  el.className = "pair-said" + (bad ? " bad" : "");
}

$("pairCopy").addEventListener("click", () => {
  navigator.clipboard.writeText($("pairLink").textContent).then(
    () => pairSay("Copied"),
    () => pairSay("Could not copy", true)
  );
});

$("pairSend").addEventListener("click", async () => {
  const b = $("pairSend");
  b.disabled = true;
  pairSay("Sending…");
  try {
    const r = await fetch(`/api/pair/send?k=${encodeURIComponent(link.token)}`,
                          { method: "POST" });
    const d = await r.json();
    pairSay(d.ok ? "Sent to Telegram" : (d.msg || "Not sent"), !d.ok);
  } catch (e) {
    pairSay("Not sent", true);
  }
  b.disabled = false;
});

/* Opened with view=phone, it picks the connected phone by itself. It
 * works when the phone connects later too: the check runs again when a
 * "sources" message arrives. */
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
  setPlaceholder("Connecting…");
  buildSources();
  buildToolMid();
  buildMonbar();
  updateHostLabel();
  resetView();
  // startStream() is used because it also sends the quality settings
  // and marks the stream as running. A bare "view" message did bring
  // frames in, but the settings stayed at their defaults and the
  // keyboard did not work.
  startStream();
  const src = sources.find((x) => x.id === id);
  toast(src ? sourceLabel(src) : id);
}

const CMD_LABELS = {
  lock: "Lock", sleep: "Sleep", hibernate: "Hibernate",
  shutdown: "Shut down", reboot: "Restart", logoff: "Sign out",
  cancel_shutdown: "Cancel",
  display_off: "Screen off", display_on: "Screen on",
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
    ? "The cursor moves between screens and the view follows it"
    : "The cursor stays on this screen");
};

$("btnSwapMonitors").onclick = () => link.send({ t: "swap_monitors" });

$("btnRun").onclick = () => {
  const cmd = $("runCmd").value.trim();
  if (cmd) link.send({ t: "cmd", name: "run", command: cmd });
};

$("btnForget").onclick = () => {
  if (!confirm("Forget the key? You will need the link to connect again.")) return;
  localStorage.removeItem(KEY_STORE);
  location.reload();
};

$("phBtn").onclick = () => startStream();

/* ----------------------------------------------------------- dictation */

/* Tap the microphone, speak, tap again.
 *
 * Hold-to-talk was tried first and was awkward: holding a button with
 * one thumb while the same hand steadies the phone leaves nothing to
 * speak into, and letting go early cuts the sentence. Tapping frees the
 * hand, and a pause in speech ends it anyway - so most of the time
 * there is no second tap either.
 *
 * The recording goes to the computer, which recognises it locally and
 * sends the words back. They land in the text field rather than being
 * typed straight through, because recognition is never perfect and
 * fixing a word before it lands beats fixing it afterwards on the far
 * side. */
const mic = $("btnMic");
let recorder = null;
let chunks = [];
let micBusy = false;
let micStop = null;

/* Speech stops for a moment all the time, so silence only ends the
 * recording after it has lasted longer than a pause between words. */
const QUIET_END_MS = 1600;
const MAX_RECORD_MS = 60000;

function micSupported() {
  return !!(navigator.mediaDevices && window.MediaRecorder);
}

/* Sends a line to the computer's log.
 *
 * Reading a phone's own log needs a cable and developer mode, so when
 * something here fails the reason would otherwise be lost. This is the
 * same channel the Android app uses to explain why sharing stopped. */
function note(text) {
  try { link.send({ t: "note", msg: String(text).slice(0, 300) }); } catch (e) {}
}

/* Gets Android's permission before the page asks for the microphone.
 *
 * Inside the app this matters more than it looks: until the app itself
 * holds RECORD_AUDIO, the WebView does not report a microphone at all -
 * getUserMedia does not prompt, it reports that no device was found,
 * which reads on screen as a phone without a microphone. In a plain
 * browser there is no bridge and the browser handles its own prompt. */
async function micPermission() {
  const native = window.PultNative;
  if (!native || !native.hasMic) return true;
  if (native.hasMic()) return true;

  return new Promise((resolve) => {
    let done = false;
    window.__pultMic = (granted) => {
      if (done) return;
      done = true;
      resolve(!!granted);
    };
    native.requestMic();
    // If the dialog is dismissed in a way that never reports back, do
    // not leave the button dead for the rest of the session.
    setTimeout(() => { if (!done) { done = true; resolve(native.hasMic()); } }, 60000);
  });
}

async function micStart() {
  if (recorder || micBusy || !micSupported()) return;

  if (!await micPermission()) {
    toast("Without the microphone there is no dictation");
    return;
  }

  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
    });
  } catch (e) {
    /* The reason matters, and guessing at it has already cost time.
     * Chrome refuses the microphone outright on a page whose
     * certificate did not verify - which is every local connection
     * here, since the agent signs its own. That arrives as a bare
     * NotFoundError, indistinguishable from a phone with no microphone
     * unless the page says which it was. */
    const name = (e && e.name) || "Error";
    note(`microphone refused: ${name} (secure=${window.isSecureContext}, ` +
         `origin=${location.origin})`);
    if (name === "NotAllowedError") {
      toast("Microphone access was refused — allow it for Pult");
    } else if (!window.isSecureContext) {
      toast("This connection is not trusted, so the browser blocks the " +
            "microphone. Use the internet link.");
    } else {
      toast(`No microphone (${name})`);
    }
    return;
  }

  chunks = [];
  // Speech carries fine at this bitrate, and the upload matters: over a
  // tunnel the recording has to cross the internet before a single word
  // comes back. Opus if the browser has it, otherwise its own default.
  const opts = { audioBitsPerSecond: 24000 };
  if (MediaRecorder.isTypeSupported?.("audio/webm;codecs=opus")) {
    opts.mimeType = "audio/webm;codecs=opus";
  }
  recorder = new MediaRecorder(stream, opts);
  recorder.ondataavailable = (e) => { if (e.data && e.data.size) chunks.push(e.data); };
  recorder.onstop = () => {
    // The tracks are stopped explicitly: left running, the phone keeps
    // showing its "microphone in use" indicator afterwards.
    stream.getTracks().forEach((t) => t.stop());
    send(new Blob(chunks, { type: recorder.mimeType || "audio/webm" }));
    recorder = null;
  };
  recorder.start();
  mic.classList.add("on");
  navigator.vibrate?.(12);
  toast("Listening — tap again when you are done");

  const stopAll = listenForSilence(stream);
  const hardStop = setTimeout(() => micStop && micStop(), MAX_RECORD_MS);

  micStop = () => {
    micStop = null;
    clearTimeout(hardStop);
    stopAll();
    if (recorder && recorder.state !== "inactive") recorder.stop();
    mic.classList.remove("on");
  };

  async function send(blob) {
    // Under a moment of audio is a mis-tap, not speech.
    if (blob.size < 2000) { mic.classList.remove("on"); return; }
    micBusy = true;
    mic.classList.remove("on");
    mic.classList.add("busy");
    try {
      const r = await fetch(`/api/stt?k=${encodeURIComponent(link.token)}`,
                            { method: "POST", body: blob });
      const d = await r.json();
      if (!d.ok) {
        toast(d.msg || "Could not recognise that");
        note(`dictation failed: ${d.msg || "?"}`);
      } else if (!d.text) {
        toast("Nothing was heard");
      } else {
        // Appended rather than replacing, so several goes build up one
        // sentence and anything already typed survives.
        typer.value = (typer.value ? typer.value.trimEnd() + " " : "") + d.text;
        typer.focus();
        navigator.vibrate?.(8);
      }
    } catch (e) {
      toast("The computer did not answer");
      note(`dictation upload failed: ${e}`);
    }
    mic.classList.remove("busy");
    micBusy = false;
  }
}

/* Ends the recording once speech has stopped for long enough.
 *
 * Without this every dictation needs a second tap, and the one thing
 * worse than holding a button is remembering to press it again. A pause
 * between words is far shorter than the wait here, so a sentence is not
 * cut in half.
 */
function listenForSilence(stream) {
  let ctx, timer;
  try {
    ctx = new (window.AudioContext || window.webkitAudioContext)();
    const src = ctx.createMediaStreamSource(stream);
    const analyser = ctx.createAnalyser();
    analyser.fftSize = 512;
    src.connect(analyser);
    const buf = new Uint8Array(analyser.fftSize);
    let quietSince = 0;
    let spokeAtAll = false;

    timer = setInterval(() => {
      analyser.getByteTimeDomainData(buf);
      let peak = 0;
      for (let i = 0; i < buf.length; i++) {
        const v = Math.abs(buf[i] - 128);
        if (v > peak) peak = v;
      }
      const loud = peak > 6;
      if (loud) { spokeAtAll = true; quietSince = 0; return; }
      // Only start the clock once something has actually been said, so
      // a slow start does not end the recording before it begins.
      if (!spokeAtAll) return;
      const now = Date.now();
      if (!quietSince) quietSince = now;
      else if (now - quietSince > QUIET_END_MS && micStop) micStop();
    }, 150);
  } catch (e) {
    // Without an analyser it simply needs the second tap.
    note(`silence detection unavailable: ${e}`);
  }

  return () => {
    clearInterval(timer);
    try { ctx && ctx.close(); } catch (e) {}
  };
}

if (mic) {
  mic.addEventListener("click", (e) => {
    e.preventDefault();
    if (micStop) micStop();
    else micStart();
  });
  // A tap that lands on a button should not also reach the screen
  // behind it, and a held button raises the system's own menu.
  mic.addEventListener("contextmenu", (e) => e.preventDefault());
}

/* ------------------------------------------------------------ lifecycle */

// Stop the stream when the app goes to the background, so no traffic
// and no battery are spent for nothing.
document.addEventListener("visibilitychange", () => {
  if (document.hidden) {
    // With the app backgrounded mid-gesture, a held key (Alt during an
    // Alt+Tab, say) used to stay pressed on the computer.
    clearTimers();
    endDrag();
    endSwitch();
    link.send({ t: "release_keys" });
    if (streaming) { stopStream(); streaming = true; }
  } else if (streaming) {
    startStream();
  }
});

// Do not let the screen turn off while controlling
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

setPlaceholder("Connecting…");
link.connect();
