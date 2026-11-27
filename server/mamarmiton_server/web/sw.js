// Service worker: keeps Mamarmiton usable without a connection.
//  - the app shell (page, scripts, styles, fonts, icons) is precached and served network-first,
//    so a deploy reaches the phone, with the cached copy as fallback (also when the network is slow);
//  - recipe photos are served cache-first;
//  - everything else (the API, with its bearer token) is never touched or stored here.
// The server replaces the version below with its own, so a new release drops the old shell cache.
// SHELL_REVISION is bumped by hand whenever the list below changes between two server releases.
const VERSION = "__VERSION__";
const SHELL_REVISION = 5;
const SHELL_CACHE = `mamarmiton-shell-${VERSION}-${SHELL_REVISION}`;
const PHOTO_CACHE = "mamarmiton-photos";
const SLOW_NETWORK_MS = 4000;

const SHELL = [
  "/",
  "/app/app.js", "/app/api.js", "/app/state.js", "/app/offline.js", "/app/dom.js", "/app/ui.js",
  "/app/login.js", "/app/home.js", "/app/detail.js", "/app/editor.js", "/app/share.js", "/app/account.js",
  "/app/quantities.js", "/app/search.js", "/app/timers.js", "/app/timer-tray.js", "/app/kitchen.js",
  "/app/shopping.js", "/app/courses.js", "/app/usage.js", "/app/diagnostics.js", "/app/books.js",
  "/app/importer.js", "/app/importlogic.js", "/app/backuplogic.js", "/app/textsize.js",
  "/app/style.css", "/app/manifest.webmanifest",
  "/app/icon.svg", "/app/icon-180.png", "/app/icon-192.png", "/app/icon-512.png",
  "/app/fonts/AtkinsonHyperlegible-Regular.ttf", "/app/fonts/AtkinsonHyperlegible-Bold.ttf",
  "/app/fonts/AtkinsonHyperlegible-Italic.ttf", "/app/fonts/AtkinsonHyperlegible-BoldItalic.ttf",
  "/app/fonts/DMSerifDisplay-Regular.ttf",
];

self.addEventListener("install", (event) => {
  event.waitUntil((async () => {
    const cache = await caches.open(SHELL_CACHE);
    await cache.addAll(SHELL.map((url) => new Request(url, { cache: "reload" })));
    await self.skipWaiting();
  })());
});

self.addEventListener("activate", (event) => {
  event.waitUntil((async () => {
    for (const name of await caches.keys()) {
      if (name.startsWith("mamarmiton-shell-") && name !== SHELL_CACHE) await caches.delete(name);
    }
    await self.clients.claim();
  })());
});

self.addEventListener("message", (event) => {
  // sent when somebody signs out: the next person must not see these photos
  if (event.data && event.data.type === "clear-photos") event.waitUntil(caches.delete(PHOTO_CACHE));
});

const PHOTO_PATH = /^\/recipes\/[0-9A-Fa-f-]+\/images\/[\w-]+$/;

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  if (PHOTO_PATH.test(url.pathname)) {
    event.respondWith(photo(request));
  } else if (url.pathname === "/" || (url.pathname.startsWith("/app/") && url.pathname !== "/app/sw.js")) {
    event.respondWith(shell(event, request, url));
  }
});

async function photo(request) {
  const cache = await caches.open(PHOTO_CACHE);
  const hit = await cache.match(request.url, { ignoreVary: true });
  if (hit) return hit;
  const response = await fetch(request);
  if (response.ok) await cache.put(request.url, response.clone());
  return response;
}

async function shell(event, request, url) {
  const cache = await caches.open(SHELL_CACHE);
  const key = url.pathname === "/" ? "/" : url.pathname;
  const cached = await cache.match(key);
  const network = fetch(request).then((response) => {
    if (response.ok) {
      const copy = response.clone();
      event.waitUntil(cache.put(key, copy));
      return response;
    }
    return response.status >= 500 && cached ? cached : response;
  });
  if (!cached) return network;
  event.waitUntil(network.catch(() => {}));
  const slow = new Promise((resolve) => setTimeout(() => resolve(cached), SLOW_NETWORK_MS));
  return Promise.race([network.catch(() => cached), slow]);
}
