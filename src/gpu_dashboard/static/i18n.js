// Lightweight i18n — pure vanilla JS, no dependencies.
// Strings are keyed and looked up at render time. Switch language via setLang(),
// stored in localStorage. To add a language, copy the `en` block and translate.
// Strategy is simple on purpose; v0.2 (Svelte) will replace this with a typed store.

const I18N = {
  en: {
    // ── Header / chrome ────────────────────────────────────────────────────
    "app.title": "GPU Dashboard",
    "ts.loading": "loading…",
    "ts.updated": "updated",
    "ts.network_error": "Network error",
    "header.gear_title": "Advanced settings",
    "footer.refresh": "refresh 5s",

    // ── Charts ─────────────────────────────────────────────────────────────
    "chart.cooling": "Cooling — fans (RPM) + temperature",
    "chart.power": "Power — live draw + power-limit cap",
    "chart.sampling": "sampling…",
    "chart.buffer_filling": "buffer filling (one sample every 5s)…",
    "chart.info_pts": "pts since",
    "chart.cap": "cap",

    // ── Cards ──────────────────────────────────────────────────────────────
    "card.gpu": "GPU",
    "card.power_limit": "Power Limit",
    "card.fans": "Fans (actual / target)",
    "card.vram": "VRAM",
    "card.oculink": "OcuLink",
    "card.llm_model": "LLM Model",
    "card.tuning": "Tuning",
    "card.profile": "Profile",
    "gpu.util": "util",
    "gpu.draw": "draw",
    "gpu.off_bus": "OFF BUS",
    "gpu.no_response": "nvidia-smi not responding — OcuLink link probably down",
    "oculink.drops": "drop(s)",
    "tuning.memory": "Memory",
    "tuning.pstate": "P-state",
    "tuning.gpu_offset": "GPU offset",
    "tuning.mem_offset": "Mem offset",
    "perf.stock_pl": "stock 350",
    "perf.perf_short": "perf",

    // ── Modal — sidebar ───────────────────────────────────────────────────
    "modal.settings": "Settings",
    "modal.close": "Close",
    "modal.power": "Power Limit",
    "modal.clocks": "Clocks",
    "modal.stats": "Statistics",
    "modal.services": "Services",
    "modal.alerts": "Alerts",
    "modal.language": "Language",

    // ── Power section ──────────────────────────────────────────────────────
    "power.section_title": "Power Limit",
    "power.description": "Power consumption cap (nvidia-smi -pl). Persistent across reboot via the sudoers wrapper.",
    "power.limit_label": "Limit",
    "power.apply": "Apply",
    "power.preset_250": "→ 250 W",
    "power.preset_350": "→ 350 W (stock)",
    "power.stock_note": "Stock 350 W · sweet-spot ~250 W",

    // ── Clocks section ─────────────────────────────────────────────────────
    "clocks.section_title": "Clock control (Coolbits 12 · no sudo)",
    "clocks.reference_title": "RTX 3090 reference:",
    "clocks.reference_text": "typical stable sweet-spot =",
    "clocks.reference_warning": "Beyond: crash risk, visual artifacts, memory corruption. Reset at any sign of instability.",
    "clocks.zones_label": "Zones:",
    "clocks.zone_safe_help": "risk-free",
    "clocks.zone_mod_help": "typical",
    "clocks.zone_agg_help": "stability test required",
    "clocks.zone_danger_help": "may freeze the GPU",
    "clocks.gpu_offset": "GPU offset",
    "clocks.mem_offset": "Mem offset",
    "clocks.advanced_mode": "⚠️ Advanced mode — unlock GPU up to +200 / mem +1500 MHz",
    "clocks.locked": "(currently locked to safe/moderate)",
    "clocks.unlocked": "(advanced mode on — aggressive/danger zones unlocked)",
    "clocks.apply": "Apply",
    "clocks.reset": "Reset (0 / 0)",
    "clocks.test_warning": "⚠️ Test after each increase",
    "clocks.confirm_dangerous": "You are about to apply:\n  GPU +{gpu} MHz ({gz})\n  mem +{mem} MHz ({mz})\n\nAggressive/danger zones can freeze the GPU or corrupt memory. Continue?",
    "clocks.applied": "Applied: GPU +{gpu} MHz · mem +{mem} MHz",

    // ── Zones (also used in JS classification) ─────────────────────────────
    "zone.safe": "safe",
    "zone.moderate": "moderate",
    "zone.aggressive": "aggressive",
    "zone.danger": "danger",

    // ── Stats / Services ───────────────────────────────────────────────────
    "stats.title": "Fan target distribution (since startup)",
    "services.title": "System services",

    // ── Alerts section ─────────────────────────────────────────────────────
    "alerts.title": "Telegram alerts",
    "alerts.description": "Push notifications to your phone (OcuLink drop/recovery). The watchdog reloads the config at each tick (~60s) — no restart needed.",
    "alerts.enable_label": "Enable",
    "alerts.enable_help": "send Telegram notifications",
    "alerts.bot_token": "Bot token",
    "alerts.chat_id": "Chat ID",
    "alerts.events": "Events",
    "alerts.drop": "🔴 Drop",
    "alerts.recovery": "🟢 Recovery",
    "alerts.save": "Save",
    "alerts.test_btn": "📤 Send a test",
    "alerts.token_note": "Configure your Telegram bot token in secrets.env",
    "alerts.config_saved": "Config saved — applied at next watchdog tick (< 60s)",
    "alerts.message_sent": "Message sent — check your phone",
    "alerts.telegram_error": "Telegram error",

    // ── Language section ───────────────────────────────────────────────────
    "lang.title": "Language",
    "lang.description": "Choose the language for the dashboard UI. Stored locally in your browser.",
    "lang.en": "🇬🇧 English",
    "lang.fr": "🇫🇷 Français",

    // ── Toasts / generic ───────────────────────────────────────────────────
    "toast.error": "Error",
    "toast.unknown": "unknown",
    "toast.power_applied": "Power limit → {watts} W (~{perf}% perf)",
  },

  fr: {
    "app.title": "Dashboard GPU",
    "ts.loading": "chargement…",
    "ts.updated": "maj",
    "ts.network_error": "Erreur réseau",
    "header.gear_title": "Réglages avancés",
    "footer.refresh": "rafraîchissement 5s",

    "chart.cooling": "Refroidissement — ventilos (RPM) + température",
    "chart.power": "Puissance — conso live + plafond power-limit",
    "chart.sampling": "échantillonnage en cours…",
    "chart.buffer_filling": "buffer en cours de remplissage (1 échantillon / 5s)…",
    "chart.info_pts": "pts depuis",
    "chart.cap": "limite",

    "card.gpu": "GPU",
    "card.power_limit": "Power Limit",
    "card.fans": "Ventilos (réel / cible)",
    "card.vram": "VRAM",
    "card.oculink": "OcuLink",
    "card.llm_model": "Modèle LLM",
    "card.tuning": "Tuning",
    "card.profile": "Profil",
    "gpu.util": "util",
    "gpu.draw": "conso",
    "gpu.off_bus": "HORS BUS",
    "gpu.no_response": "nvidia-smi ne répond plus — lien OcuLink probablement tombé",
    "oculink.drops": "décrochage(s)",
    "tuning.memory": "Mémoire",
    "tuning.pstate": "P-state",
    "tuning.gpu_offset": "Offset GPU",
    "tuning.mem_offset": "Offset mém.",
    "perf.stock_pl": "stock 350",
    "perf.perf_short": "perf",

    "modal.settings": "Réglages",
    "modal.close": "Fermer",
    "modal.power": "Power Limit",
    "modal.clocks": "Horloges",
    "modal.stats": "Statistiques",
    "modal.services": "Services",
    "modal.alerts": "Alertes",
    "modal.language": "Langue",

    "power.section_title": "Power Limit",
    "power.description": "Plafond de consommation (nvidia-smi -pl). Persistant au reboot via le wrapper sudoers.",
    "power.limit_label": "Limite",
    "power.apply": "Appliquer",
    "power.preset_250": "→ 250 W",
    "power.preset_350": "→ 350 W (stock)",
    "power.stock_note": "Stock 350 W · sweet-spot ~250 W",

    "clocks.section_title": "Contrôle clocks (Coolbits 12 · sans sudo)",
    "clocks.reference_title": "Repères RTX 3090 :",
    "clocks.reference_text": "sweet-spot stable typique =",
    "clocks.reference_warning": "Au-delà : risque de crash, artefacts visuels, corruption mémoire. Reset au moindre signe d'instabilité.",
    "clocks.zones_label": "Zones :",
    "clocks.zone_safe_help": "sans risque",
    "clocks.zone_mod_help": "typique",
    "clocks.zone_agg_help": "test stabilité requis",
    "clocks.zone_danger_help": "peut figer la carte",
    "clocks.gpu_offset": "Offset GPU",
    "clocks.mem_offset": "Offset mém.",
    "clocks.advanced_mode": "⚠️ Mode avancé — débloquer GPU jusqu'à +200 / mém +1500 MHz",
    "clocks.locked": "(actuellement verrouillé à safe/modéré)",
    "clocks.unlocked": "(mode avancé activé — zones agressif/dangereux débloquées)",
    "clocks.apply": "Appliquer",
    "clocks.reset": "Reset (0 / 0)",
    "clocks.test_warning": "⚠️ Tester après chaque hausse",
    "clocks.confirm_dangerous": "Tu vas appliquer :\n  GPU +{gpu} MHz ({gz})\n  mém +{mem} MHz ({mz})\n\nUne valeur en zone agressif/dangereux peut figer la carte ou corrompre la mémoire. Continuer ?",
    "clocks.applied": "Appliqué : GPU +{gpu} MHz · mém +{mem} MHz",

    "zone.safe": "safe",
    "zone.moderate": "modéré",
    "zone.aggressive": "agressif",
    "zone.danger": "dangereux",

    "stats.title": "Distribution paliers fan (depuis démarrage)",
    "services.title": "Services système",

    "alerts.title": "Alertes Telegram",
    "alerts.description": "Notifications push sur ton téléphone (décrochage/reprise OcuLink). Le watchdog recharge la conf à chaque tick (~60s) — pas de restart nécessaire.",
    "alerts.enable_label": "Activer",
    "alerts.enable_help": "envoyer les notifs Telegram",
    "alerts.bot_token": "Bot token",
    "alerts.chat_id": "Chat ID",
    "alerts.events": "Événements",
    "alerts.drop": "🔴 Décrochage",
    "alerts.recovery": "🟢 Reprise",
    "alerts.save": "Sauver",
    "alerts.test_btn": "📤 Envoyer un test",
    "alerts.token_note": "Configure ton token Telegram dans secrets.env",
    "alerts.config_saved": "Config sauvegardée — appliquée au prochain tick du watchdog (< 60s)",
    "alerts.message_sent": "Message envoyé — check ton téléphone",
    "alerts.telegram_error": "Erreur Telegram",

    "lang.title": "Langue",
    "lang.description": "Choisis la langue de l'interface du dashboard. Stockée localement dans ton navigateur.",
    "lang.en": "🇬🇧 English",
    "lang.fr": "🇫🇷 Français",

    "toast.error": "Erreur",
    "toast.unknown": "inconnue",
    "toast.power_applied": "Power limit → {watts} W (~{perf}% perf)",
  },
};

// Determine initial language: localStorage > navigator.language > en
function detectLang() {
  const stored = localStorage.getItem("gpu-dashboard-lang");
  if (stored && I18N[stored]) return stored;
  const navLang = (navigator.language || "en").toLowerCase().slice(0, 2);
  return I18N[navLang] ? navLang : "en";
}

let currentLang = detectLang();

function t(key, vars) {
  let s = (I18N[currentLang] && I18N[currentLang][key]) || I18N.en[key] || key;
  if (vars) {
    for (const k in vars) s = s.split("{" + k + "}").join(vars[k]);
  }
  return s;
}

function applyStaticTranslations() {
  document.documentElement.lang = currentLang;
  document.title = t("app.title");
  document.querySelectorAll("[data-i18n]").forEach(el => {
    el.textContent = t(el.dataset.i18n);
  });
  document.querySelectorAll("[data-i18n-title]").forEach(el => {
    el.title = t(el.dataset.i18nTitle);
  });
  document.querySelectorAll("[data-i18n-aria]").forEach(el => {
    el.setAttribute("aria-label", t(el.dataset.i18nAria));
  });
}

function setLang(lang) {
  if (!I18N[lang]) return;
  currentLang = lang;
  localStorage.setItem("gpu-dashboard-lang", lang);
  applyStaticTranslations();
  // Trigger a re-render of dynamic content if app.js exposes refresh()
  if (typeof window.gpuDashboardRefresh === "function") {
    window.gpuDashboardRefresh();
  }
}

// Expose globals for app.js + the radio buttons
window.t = t;
window.setLang = setLang;
window.currentLang = () => currentLang;
window.applyStaticTranslations = applyStaticTranslations;
