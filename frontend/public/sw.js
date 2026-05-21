// gpu-dashboard service worker — handles push notifications.
// Registered from App.svelte via navigator.serviceWorker.register('/sw.js').

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));

// On push (with or without payload), fetch the most recent alert from the
// backend and use that as the notification text. This avoids the heavy
// RFC 8291 encryption ceremony — the push itself is just a "wake up and
// check" signal, the SW pulls the actual data.

async function buildNotification(event) {
  // Try to parse inline data first (future-proof for cycle 85b encrypted payloads)
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch {
    data = {};
  }
  // If push had no payload, fetch from backend
  if (!data.title && !data.body) {
    try {
      const r = await fetch("/api/alerts/latest", { credentials: "same-origin" });
      if (r.ok) {
        const j = await r.json();
        const a = j.alert;
        if (a && a.payload) {
          const p = a.payload;
          data.title = p.title || "🚨 GPU alert";
          data.body = p.body || `${p.kind || "alert"} — value ${p.value}, threshold ${p.threshold}`;
        }
      }
    } catch {
      // backend unreachable — fall back to generic
    }
  }
  return {
    title: data.title || "🖥️ GPU Dashboard",
    options: {
      body: data.body || "Alert fired — open the dashboard for details",
      icon: data.icon || "/favicon.svg",
      badge: data.badge || "/favicon.svg",
      tag: data.tag || "gpu-alert",
      data: data.url || "/",
      requireInteraction: data.requireInteraction === true,
    },
  };
}

self.addEventListener("push", (event) => {
  event.waitUntil(
    buildNotification(event).then(({ title, options }) =>
      self.registration.showNotification(title, options)
    )
  );
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
