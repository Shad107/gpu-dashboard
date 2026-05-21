// Layout store — controls which dashboard cards are visible.
// Persisted in localStorage so the choice survives page reloads.
//
// Default = all cards visible (zero regression).

const STORAGE_KEY = "gpu-dashboard-layout";

/** All known card identifiers. Add new ones here when introducing a new card. */
export const CARD_NAMES = [
  "gpu",
  "power_limit",
  "fans",
  "vram",
  "oculink",
  "llm_model",
  "llm_throughput",
  "electricity",
  "processes",
  "tuning",
] as const;

export type CardName = (typeof CARD_NAMES)[number];

function defaultVisible(): Record<string, boolean> {
  const out: Record<string, boolean> = {};
  for (const n of CARD_NAMES) out[n] = true;
  return out;
}

function loadFromStorage(): Record<string, boolean> {
  if (typeof localStorage === "undefined") return defaultVisible();
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return defaultVisible();
    const parsed = JSON.parse(raw);
    return { ...defaultVisible(), ...(parsed?.cards ?? {}) };
  } catch {
    return defaultVisible();
  }
}

class LayoutStore {
  cards = $state<Record<string, boolean>>(loadFromStorage());

  visible(name: string): boolean {
    // Treat 'undefined' as visible — new cards added in future versions
    // appear by default even on older localStorage state.
    return this.cards[name] !== false;
  }

  toggle(name: string): void {
    this.cards[name] = !this.visible(name);
    this.persist();
  }

  set(name: string, visible: boolean): void {
    this.cards[name] = visible;
    this.persist();
  }

  reset(): void {
    this.cards = defaultVisible();
    this.persist();
  }

  private persist(): void {
    if (typeof localStorage === "undefined") return;
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify({ cards: this.cards }));
    } catch {
      // Quota exceeded / disabled — silent
    }
  }
}

export const layout = new LayoutStore();
