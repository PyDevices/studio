// Ostinato's service worker: everything the app and its workers fetch is
// cached as it arrives, so after one visit the app starts offline.
//
// Network first, then the cache: online you always get the current files
// (no stale copies while editing), offline you get the last good ones. The
// lvgl-bindings fonts on raw.githubusercontent.com are cached the same way.
const CACHE = "ostinato-1";
const SHELL = ["./", "./index.html", "./manifest.json", "./icon-192.png", "./icon-512.png",
  "./workers/host.js", "./workers/python_worker.js", "./workers/audio_worklet.js", "./app/boot_config.js"];
const CACHEABLE = (url) => url.origin === self.location.origin || url.hostname === "raw.githubusercontent.com";

// app/precache.json (written by tools/gen_boot.py) lists everything an
// offline start needs; the workers fetch most of it before this worker
// controls the page, so it's all fetched here at install.
self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then(async (c) => {
    await c.addAll(SHELL);
    const list = await (await fetch("./app/precache.json", { cache: "no-store" })).json();
    await Promise.all(list.map((u) => c.add(u).catch(() => null)));
  }).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys()
    .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || !CACHEABLE(url)) return;
  e.respondWith(fetch(e.request).then((resp) => {
    if (resp.ok) {
      const copy = resp.clone();
      caches.open(CACHE).then((c) => c.put(e.request, copy));
    }
    return resp;
  }).catch(() => caches.match(e.request, { ignoreSearch: true }).then((hit) => hit || Response.error())));
});
