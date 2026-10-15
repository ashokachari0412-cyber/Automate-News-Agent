// Minimal service worker: makes Alfred installable. Always uses the network
// (no offline caching) so news, images and API replies are never stale.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));
self.addEventListener("fetch", (event) => {
  event.respondWith(fetch(event.request));
});
