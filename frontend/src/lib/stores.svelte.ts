// Svelte 5 runes-based stores. Reactivity is implicit via $state.

import { api, type State } from "./api";

// Live data store — null until first poll completes.
class LiveStore {
  data = $state<State | null>(null);
  error = $state<string | null>(null);
  private timer: ReturnType<typeof setInterval> | null = null;

  async tick() {
    try {
      this.data = await api.state();
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
