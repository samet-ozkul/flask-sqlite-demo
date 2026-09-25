// Kişisel Pano service worker
// - Sayfalar her zaman ağdan gelir (kişisel veriler önbelleğe alınmaz)
// - Ağ yoksa çevrimdışı sayfası gösterilir
// - CSS/JS/ikonlar önbellekten hızlı yüklenir
const VERSION = "pano-v1";
const PRECACHE = [
  "/cevrimdisi",
  "/static/style.css?v=1",
  "/static/app.js?v=1",
  "/static/icons/icon-192.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(VERSION).then((cache) => cache.addAll(PRECACHE)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== VERSION).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== location.origin) return;

  if (req.mode === "navigate") {
    event.respondWith(fetch(req).catch(() => caches.match("/cevrimdisi")));
    return;
  }
  if (url.pathname.startsWith("/static/")) {
    event.respondWith(
      caches.match(req).then((hit) => hit || fetch(req).then((resp) => {
        if (resp.ok) {
          const copy = resp.clone();
          caches.open(VERSION).then((cache) => cache.put(req, copy));
        }
        return resp;
      }))
    );
  }
});
