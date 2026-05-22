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
  "pcie",
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

export type CustomCard = {
  id: string;     // unique, e.g. "custom-<random>"
  name: string;   // user-given label shown as card header
  url: string;   // http/https URL embedded in a sandboxed iframe
};

type StoredLayout = {
  cards: Record<string, boolean>;
  order: string[];
  customCards: CustomCard[];
};

function loadFromStorage(): StoredLayout {
  const fallback: StoredLayout = {
    cards: defaultVisible(),
    order: defaultOrder(),
    customCards: [],
  };
  if (typeof localStorage === "undefined") return fallback;
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return fallback;
    const parsed = JSON.parse(raw);
    // Merge defaults with stored to gracefully handle new cards
    const cards = { ...defaultVisible(), ...(parsed?.cards ?? {}) };
    // Stored custom cards (validate shape)
    const customCards: CustomCard[] = Array.isArray(parsed?.customCards)
      ? parsed.customCards.filter(
          (c: any) => c && typeof c.id === "string" && typeof c.name === "string" && isValidUrl(c.url)
        )
      : [];
    // Custom card IDs are also valid order keys
    const validKeys = new Set([...CARD_NAMES, ...customCards.map(c => c.id)]);
    const storedOrder: string[] = Array.isArray(parsed?.order) ? parsed.order : [];
    const filtered = storedOrder.filter(n => validKeys.has(n));
    const missing = [
      ...CARD_NAMES.filter(n => !filtered.includes(n)),
      ...customCards.filter(c => !filtered.includes(c.id)).map(c => c.id),
    ];
    return { cards, order: [...filtered, ...missing], customCards };
  } catch {
    return fallback;
  }
}

export function isValidUrl(url: any): boolean {
  if (typeof url !== "string") return false;
  try {
    const u = new URL(url);
    return u.protocol === "http:" || u.protocol === "https:";
  } catch {
    return false;
  }
}

function makeCustomId(): string {
  // Short readable ID; collision risk negligible at <100 cards
  return "custom-" + Math.random().toString(36).slice(2, 9);
}

class LayoutStore {
  private _state = $state<StoredLayout>(loadFromStorage());

  get cards(): Record<string, boolean> { return this._state.cards; }
  get order(): string[] { return this._state.order; }
  get customCards(): CustomCard[] { return this._state.customCards; }

  visible(name: string): boolean {
    // Treat 'undefined' as visible — new cards added in future versions
    // appear by default even on older localStorage state.
    return this._state.cards[name] !== false;
  }

  /** Index of `name` in the user's order. Used as CSS `order: N` on cards. */
  indexOf(name: string): number {
    const i = this._state.order.indexOf(name);
    return i >= 0 ? i : this._state.order.length;  // unknown → at the end
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
    // Filter to known keys (built-in + custom) + append any missing
    const validKeys = new Set([...CARD_NAMES, ...this._state.customCards.map(c => c.id)]);
    const filtered = newOrder.filter(n => validKeys.has(n));
    const missing = [
      ...CARD_NAMES.filter(n => !filtered.includes(n)),
      ...this._state.customCards.filter(c => !filtered.includes(c.id)).map(c => c.id),
    ];
    this._state.order = [...filtered, ...missing];
    this.persist();
  }

  /** Add a custom iframe card. Returns the new card's id, or null if URL invalid. */
  addCustom(name: string, url: string): string | null {
    if (!isValidUrl(url)) return null;
    const id = makeCustomId();
    const card: CustomCard = { id, name: name.trim() || url, url };
    this._state.customCards = [...this._state.customCards, card];
    this._state.cards[id] = true;
    this._state.order = [...this._state.order, id];
    this.persist();
    return id;
  }

  /** Remove a custom card by id. Also strips it from cards + order. */
  removeCustom(id: string): void {
    this._state.customCards = this._state.customCards.filter(c => c.id !== id);
    delete this._state.cards[id];
    this._state.order = this._state.order.filter(n => n !== id);
    this.persist();
  }

  reset(): void {
    this._state = { cards: defaultVisible(), order: defaultOrder(), customCards: [] };
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
