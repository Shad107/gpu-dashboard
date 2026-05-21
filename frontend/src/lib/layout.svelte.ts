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

function defaultOrder(): string[] {
  return [...CARD_NAMES];
}

type StoredLayout = {
  cards: Record<string, boolean>;
  order: string[];
};

function loadFromStorage(): StoredLayout {
  const fallback: StoredLayout = { cards: defaultVisible(), order: defaultOrder() };
  if (typeof localStorage === "undefined") return fallback;
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return fallback;
    const parsed = JSON.parse(raw);
    // Merge defaults with stored to gracefully handle new cards
    const cards = { ...defaultVisible(), ...(parsed?.cards ?? {}) };
    // Stored order, augmented with any cards added in newer versions
    const storedOrder: string[] = Array.isArray(parsed?.order) ? parsed.order : [];
    const known = new Set(CARD_NAMES);
    const filtered = storedOrder.filter(n => known.has(n as any));
    const missing = CARD_NAMES.filter(n => !filtered.includes(n));
    return { cards, order: [...filtered, ...missing] };
  } catch {
    return fallback;
  }
}

class LayoutStore {
  private _state = $state<StoredLayout>(loadFromStorage());

  get cards(): Record<string, boolean> { return this._state.cards; }
  get order(): string[] { return this._state.order; }

  visible(name: string): boolean {
    // Treat 'undefined' as visible — new cards added in future versions
    // appear by default even on older localStorage state.
    return this._state.cards[name] !== false;
  }

  /** Index of `name` in the user's order. Used as CSS `order: N` on cards. */
  indexOf(name: string): number {
    const i = this._state.order.indexOf(name);
    return i >= 0 ? i : CARD_NAMES.length;  // unknown → at the end
  }

  toggle(name: string): void {
    this._state.cards[name] = !this.visible(name);
    this.persist();
  }

  set(name: string, visible: boolean): void {
    this._state.cards[name] = visible;
    this.persist();
  }

  setOrder(newOrder: string[]): void {
    // Filter to known cards + append any missing
    const known = new Set(CARD_NAMES);
    const filtered = newOrder.filter(n => known.has(n as any));
    const missing = CARD_NAMES.filter(n => !filtered.includes(n));
    this._state.order = [...filtered, ...missing];
    this.persist();
  }

  reset(): void {
    this._state = { cards: defaultVisible(), order: defaultOrder() };
    this.persist();
  }

  private persist(): void {
    if (typeof localStorage === "undefined") return;
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(this._state));
    } catch {
      // Quota exceeded / disabled — silent
    }
  }
}

export const layout = new LayoutStore();
