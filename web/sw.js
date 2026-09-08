/*
 * Xizmat ishchisi (service worker).
 *
 * Vazifasi faqat bitta: ilova telefon ekranidan ochilganda darhol
 * ko'rinsin. Tarmoq birinchi, kesh ikkinchi - shunda dastur yangilansa
 * telefon eski nusxada qolib ketmaydi.
 */
const CACHE = "pult-v1";
const SHELL = [
  ".",
  "index.html",
  "app.js",
  "style.css",
  "manifest.webmanifest",
  "icons/icon-192.png",
];

self.addEventListener("install", (e) => {
  e.waitUntil(
    caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  // WebSocket va API so'rovlariga umuman aralashmaymiz
  if (e.request.method !== "GET" || url.pathname.startsWith("/ws") || url.pathname.startsWith("/api")) {
    return;
  }
  e.respondWith(
    fetch(e.request)
      .then((res) => {
        if (res.ok) {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(e.request, copy));
        }
        return res;
      })
      .catch(() => caches.match(e.request).then((r) => r || caches.match("index.html")))
  );
});
