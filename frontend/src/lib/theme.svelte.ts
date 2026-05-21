// Theme store — applies html.theme-light / html.theme-dark class.
// Default = dark (gpu-dashboard's signature look).

export type Theme = "dark" | "light";

const STORAGE_KEY = "gpu-dashboard-theme";

function loadInitial(): Theme {
  if (typeof window === "undefined") return "dark";
  // URL override : ?theme=light|dark (useful for screenshots + bookmarkable shares)
  const m = location.search.match(/[?&]theme=(light|dark)/i);
  if (m) return m[1].toLowerCase() === "light" ? "light" : "dark";
  if (typeof localStorage === "undefined") return "dark";
  const v = localStorage.getItem(STORAGE_KEY);
  return v === "light" ? "light" : "dark";
}

function applyClass(theme: Theme) {
  if (typeof document === "undefined") return;
  const root = document.documentElement;
  root.classList.remove("theme-dark", "theme-light");
  root.classList.add("theme-" + theme);
}

class ThemeStore {
  current = $state<Theme>(loadInitial());

  constructor() {
    applyClass(this.current);
  }

  set(t: Theme): void {
    if (this.current === t) return;
    this.current = t;
    applyClass(t);
    if (typeof localStorage !== "undefined") {
      localStorage.setItem(STORAGE_KEY, t);
    }
  }

  toggle(): void {
    this.set(this.current === "dark" ? "light" : "dark");
  }
}

export const theme = new ThemeStore();
