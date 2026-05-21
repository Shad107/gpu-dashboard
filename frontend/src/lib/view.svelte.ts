// Top-level view store — selects which "page" the user is looking at.
//
// User feedback 2026-05-21 23:14 : History, Stats, About are *viewing*, not
// *settings*. They should be top-level navigation tabs on the dashboard, not
// hidden behind the gear icon.

export type View = "dashboard" | "history" | "stats" | "about";

const ALL_VIEWS: View[] = ["dashboard", "history", "stats", "about"];

function parseHash(): View {
  if (typeof location === "undefined") return "dashboard";
  const h = location.hash.replace(/^#/, "").toLowerCase();
  return (ALL_VIEWS as string[]).includes(h) ? (h as View) : "dashboard";
}

class ViewStore {
  current = $state<View>(parseHash());

  set(v: View): void {
    if (this.current === v) return;
    this.current = v;
    if (typeof location !== "undefined") {
      // Use replaceState rather than location.hash to avoid building up
      // history entries on every nav click.
      const newUrl = location.pathname + location.search + (v === "dashboard" ? "" : "#" + v);
      history.replaceState(null, "", newUrl);
    }
  }
}

export const view = new ViewStore();

// Sync from URL hash if the user uses browser back/forward
if (typeof window !== "undefined") {
  window.addEventListener("hashchange", () => {
    view.current = parseHash();
  });
}
