// gpu-dashboard service worker — handles push notifications.
// Registered from App.svelte via navigator.serviceWorker.register('/sw.js').

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));

self.addEventListener("push", (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch {
    data = { title: "GPU Dashboard", body: event.data?.text() ?? "Alert" };
  }
  const title = data.title || "🖥️ GPU Dashboard";
  const options = {
    body: data.body || "GPU alert",
    icon: data.icon || "/favicon.svg",
    badge: data.badge || "/favicon.svg",
    tag: data.tag || "gpu-alert",
    data: data.url || "/",
    requireInteraction: data.requireInteraction === true,
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = event.notification.data || "/";
  event.waitUntil(
    self.clients
      .matchAll({ type: "window", includeUncontrolled: true })
      .then((clients) => {
        // Focus existing dashboard tab if present
        for (const c of clients) {
          if ("focus" in c) return c.focus();
        }
        if (self.clients.openWindow) return self.clients.openWindow(url);
      })
  );
});
