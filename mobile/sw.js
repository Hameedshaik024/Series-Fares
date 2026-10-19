// Offline support: the app and the posters already seen stay usable with
// no connection. Bump the version when the list of app files changes.
const CACHE = "fare-posters-v1";
const APP_FILES = ["./", "index.html", "app.js", "manifest.webmanifest", "logo.png", "icon-192.png"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(APP_FILES)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(caches.keys()
    .then((names) => Promise.all(names.filter((name) => name !== CACHE).map((name) => caches.delete(name))))
    .then(() => self.clients.claim()));
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET" || url.origin !== self.location.origin) return;

  if (url.pathname.includes("/posters/")) {
    // Every run writes new file names, so a cached poster never goes stale.
    event.respondWith(caches.open(CACHE).then(async (cache) => {
      const hit = await cache.match(event.request);
      if (hit) return hit;
      const res = await fetch(event.request);
      if (res.ok) cache.put(event.request, res.clone());
      return res;
    }));
    return;
  }

  // Everything else: network first, so a new app version or status.json
  // shows up straight away, falling back to the last copy when offline.
  const key = url.origin + url.pathname;
  event.respondWith(fetch(url.href, { cache: "no-cache" })
    .then((res) => {
      if (res.ok) {
        const copy = res.clone();
        caches.open(CACHE).then((cache) => cache.put(key, copy));
      }
      return res;
    })
    .catch(() => caches.match(key).then((hit) => hit || Response.error())));
});
