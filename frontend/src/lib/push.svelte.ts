// Browser push notification client — wraps Notification API +
// PushManager + service worker registration. Stores reactive state.

import { api } from "./api";

/** Convert base64url string to Uint8Array for applicationServerKey. */
function urlBase64ToUint8(s: string): Uint8Array {
  const pad = "=".repeat((4 - (s.length % 4)) % 4);
  const base64 = (s + pad).replace(/-/g, "+").replace(/_/g, "/");
  const raw = atob(base64);
  const arr = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i++) arr[i] = raw.charCodeAt(i);
  return arr;
}

class PushStore {
  /** "unsupported" | "denied" | "default" | "granted-no-sub" | "granted-subbed" */
  state = $state<"unsupported" | "denied" | "default" | "granted-no-sub" | "granted-subbed">("unsupported");
  error = $state<string>("");

  /** Call from onMount in the main app to wire the service worker. */
  async init(): Promise<void> {
    if (typeof navigator === "undefined" || !("serviceWorker" in navigator) || !("PushManager" in window)) {
      this.state = "unsupported";
      return;
    }
    try {
      const reg = await navigator.serviceWorker.register("/sw.js");
      const perm = Notification.permission;
      if (perm === "denied") { this.state = "denied"; return; }
      if (perm === "default") { this.state = "default"; return; }
      // permission granted — check existing subscription
      const sub = await reg.pushManager.getSubscription();
      this.state = sub ? "granted-subbed" : "granted-no-sub";
    } catch (e: any) {
      this.error = String(e?.message ?? e);
      this.state = "unsupported";
    }
  }

  /** Request permission + subscribe + POST to backend. */
  async subscribe(): Promise<boolean> {
    try {
      if (Notification.permission !== "granted") {
        const p = await Notification.requestPermission();
        if (p !== "granted") { this.state = "denied"; return false; }
      }
      const reg = await navigator.serviceWorker.ready;
      const vapid = await api.pushVapid();
      if (!vapid.ok || !vapid.public_key) {
        this.error = "VAPID key unavailable";
        return false;
      }
      const sub = await reg.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToUint8(vapid.public_key),
      });
      const json = sub.toJSON() as any;
      const r = await api.pushSubscribe({
        endpoint: json.endpoint,
        keys: json.keys,
      });
      if (!r.ok) { this.error = r.error || "subscribe failed"; return false; }
      this.state = "granted-subbed";
      return true;
    } catch (e: any) {
      this.error = String(e?.message ?? e);
      return false;
    }
  }

  /** Unsubscribe locally + DELETE on backend. */
  async unsubscribe(): Promise<boolean> {
    try {
      const reg = await navigator.serviceWorker.ready;
      const sub = await reg.pushManager.getSubscription();
      if (sub) {
        await api.pushUnsubscribe(sub.endpoint);
        await sub.unsubscribe();
      }
      this.state = "granted-no-sub";
      return true;
    } catch (e: any) {
      this.error = String(e?.message ?? e);
      return false;
    }
  }
}

export const push = new PushStore();
