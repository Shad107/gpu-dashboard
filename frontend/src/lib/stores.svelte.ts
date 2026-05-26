// Svelte 5 runes-based stores. Reactivity is implicit via $state.

import { api, type State } from "./api";
import { gpu } from "./gpu.svelte";

// Live data store — null until first poll completes.
class LiveStore {
  data = $state<State | null>(null);
  error = $state<string | null>(null);
  private timer: ReturnType<typeof setInterval> | null = null;

  async tick() {
    try {
      this.data = await api.state(gpu.selected);
      this.error = null;
    } catch (e) {
      this.error = (e as Error).message;
    }
  }

  start(intervalMs = 5000) {
    if (this.timer) return;
    this.tick();
    this.timer = setInterval(() => this.tick(), intervalMs);
  }

  stop() {
    if (this.timer) {
      clearInterval(this.timer);
      this.timer = null;
    }
  }
}

export const live = new LiveStore();

// Toast store
type ToastKind = "ok" | "err";

/** Short Web Audio "boop" — no external sound file, no permission needed.
 * 350 Hz square wave for errors (low pitch), 800 Hz sine for OK. */
function beep(kind: ToastKind) {
  if (typeof window === "undefined") return;
  if (localStorage.getItem("gpu-dashboard-sound") !== "1") return;
  try {
    const ctx = new (window.AudioContext || (window as any).webkitAudioContext)();
    const o = ctx.createOscillator();
    const g = ctx.createGain();
    o.type = kind === "err" ? "square" : "sine";
    o.frequency.value = kind === "err" ? 350 : 800;
    g.gain.setValueAtTime(0.08, ctx.currentTime);
    g.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.15);
    o.connect(g); g.connect(ctx.destination);
    o.start();
    o.stop(ctx.currentTime + 0.16);
    setTimeout(() => ctx.close().catch(() => {}), 300);
  } catch { /* AudioContext unsupported / blocked → silent */ }
}

class ToastStore {
  message = $state<string>("");
  kind = $state<ToastKind>("ok");
  show = $state<boolean>(false);
  private hideTimer: ReturnType<typeof setTimeout> | null = null;

  emit(message: string, kind: ToastKind = "ok", ms = 3500) {
    this.message = message;
    this.kind = kind;
    this.show = true;
    if (this.hideTimer) clearTimeout(this.hideTimer);
    this.hideTimer = setTimeout(() => { this.show = false; }, ms);
    // Only beep on errors by default (sounds for every OK is annoying)
    if (kind === "err") beep(kind);
  }
}

export const toast = new ToastStore();

// Modal store
class ModalStore {
  open = $state<boolean>(false);
  section = $state<string>("power");

  show(section?: string) {
    if (section) this.section = section;
    this.open = true;
  }
  close() { this.open = false; }
  setSection(s: string) { this.section = s; }
}

export const modal = new ModalStore();

// Initialize from URL ?modal=<section> at boot — useful for screenshot
// tooling and bookmarkable links into a specific tab.
if (typeof location !== "undefined") {
  const m = location.search.match(/[?&]modal=([a-z]+)/i);
  if (m) {
    modal.section = m[1].toLowerCase();
    modal.open = true;
  }
}

// Wizard store — separate from setup_required (first-run) so the user can
// re-open it on demand from the Services tab.
class WizardStore {
  userRequested = $state<boolean>(false);

  request() { this.userRequested = true; modal.close(); }
  dismiss() { this.userRequested = false; }
}

export const wizard = new WizardStore();

// F6 — shared installer prompt. Any card can request an install:
//   import { installPrompt } from "../lib/stores.svelte";
//   installPrompt.request("oculink_watchdog", () => refreshState());
// The InstallPromptModal mounted at the root of Cards.svelte
// handles the password prompt + POST + result toast. The optional
// callback fires after a successful install so the caller can
// refresh whatever state cared about the install status.
class InstallPromptStore {
  open = $state<boolean>(false);
  scriptId = $state<string | null>(null);
  onInstalled: (() => void) | null = null;

  request(scriptId: string, onInstalled?: () => void) {
    this.scriptId = scriptId;
    this.onInstalled = onInstalled ?? null;
    this.open = true;
  }
  close() {
    this.open = false;
    this.scriptId = null;
    this.onInstalled = null;
  }
}

export const installPrompt = new InstallPromptStore();
