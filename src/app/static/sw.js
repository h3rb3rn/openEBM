/**
 * Minimal service worker: exists to satisfy PWA installability and to let
 * the static app shell (CSS/JS/i18n/icons) load instantly and work offline
 * once visited.
 *
 * Deliberately does NOT cache API responses or page navigations. This is a
 * clinical billing tool — serving stale cached analysis results, case data,
 * or auth state from a service worker cache would be actively wrong, not
 * just stale. Every /api/* call and every page load always goes to the
 * network.
 */
const CACHE_NAME = "ebm-static-v1";
const STATIC_ASSETS = [
  "/static/css/tailwind.css",
  "/static/js/i18n.js",
  "/static/js/highlighter.js",
  "/static/i18n/de.json",
  "/static/i18n/en.json",
  "/static/icons/icon-192.png",
  "/static/icons/icon-512.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(STATIC_ASSETS))
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);

  if (url.origin !== self.location.origin) return;
  if (url.pathname.startsWith("/api/")) return;
  if (event.request.mode === "navigate") return;
  if (!url.pathname.startsWith("/static/")) return;

  event.respondWith(
    caches.match(event.request).then((cached) => {
      if (cached) return cached;
      return fetch(event.request).then((resp) => {
        const clone = resp.clone();
        caches.open(CACHE_NAME).then((cache) => cache.put(event.request, clone));
        return resp;
      });
    })
  );
});
