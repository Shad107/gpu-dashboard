// Typed i18n with Svelte 5 runes. Switching language is reactive.
import en from "./en.json";
import fr from "./fr.json";

export type Lang = "en" | "fr";
export type Dict = typeof en;
export type Key = keyof Dict;

const DICTS: Record<Lang, Dict> = { en, fr };

function detectLang(): Lang {
  if (typeof localStorage !== "undefined") {
    const stored = localStorage.getItem("gpu-dashboard-lang");
    if (stored === "en" || stored === "fr") return stored;
  }
  if (typeof navigator !== "undefined") {
    const nav = (navigator.language || "en").toLowerCase().slice(0, 2);
    if (nav === "fr") return "fr";
  }
  return "en";
}

class I18n {
  lang = $state<Lang>(detectLang());

  setLang(l: Lang) {
    this.lang = l;
    if (typeof localStorage !== "undefined") {
      localStorage.setItem("gpu-dashboard-lang", l);
    }
    if (typeof document !== "undefined") {
      document.documentElement.lang = l;
    }
  }

  t(key: Key, vars?: Record<string, string | number>): string {
    let s: string = DICTS[this.lang][key] ?? DICTS.en[key] ?? key;
    if (vars) for (const k in vars) s = s.split(`{${k}}`).join(String(vars[k]));
    return s;
  }
}

export const i18n = new I18n();
