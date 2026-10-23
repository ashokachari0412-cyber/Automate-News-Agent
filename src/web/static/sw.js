// Minimal service worker: makes Alfred installable. It does not intercept requests, so the browser
// (and the Android app) handle network errors normally and can show a proper "can't reach" screen.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));
self.addEventListener("fetch", () => {});
