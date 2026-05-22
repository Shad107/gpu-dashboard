<script lang="ts">
  // Cheat-sheet modal triggered by `?` key (htop-style).
  // Cycle 138 — R&D #3.5 LAST.
  import { onMount, onDestroy } from "svelte";
  import { i18n } from "../lib/i18n/index.svelte";
  import { view } from "../lib/view.svelte";
  import { modal } from "../lib/stores.svelte";

  let open = $state(false);

  function isTypingTarget(e: KeyboardEvent): boolean {
    const t = e.target as HTMLElement | null;
    if (!t) return false;
    const tag = t.tagName?.toLowerCase();
    return tag === "input" || tag === "textarea" || tag === "select"
      || t.isContentEditable;
  }

  function onKey(e: KeyboardEvent) {
    if (isTypingTarget(e)) return;

    // Open / toggle cheat-sheet
    if (e.key === "?" || (e.key === "h" && !e.ctrlKey && !e.metaKey && !e.altKey)) {
      e.preventDefault();
      open = !open;
      return;
    }
    // Close on Escape
    if (e.key === "Escape" && open) {
      e.preventDefault();
      open = false;
      return;
    }
    // Don't intercept any keys while a modal section is shown (modals own ESC etc.)
    if (modal.open) return;

    // Top-nav navigation : 1/2/3
    if (e.key === "1") { view.set("dashboard"); e.preventDefault(); }
    else if (e.key === "2") { view.set("stats"); e.preventDefault(); }
    else if (e.key === "3") { view.set("history"); e.preventDefault(); }
    else if (e.key === "s" && !e.ctrlKey && !e.metaKey) {
      // s : open settings (gear)
      modal.show();
      e.preventDefault();
    }
  }

  onMount(() => window.addEventListener("keydown", onKey));
  onDestroy(() => window.removeEventListener("keydown", onKey));
</script>

{#if open}
  <div class="kbd-overlay" onclick={() => (open = false)}>
    <div class="kbd-card" onclick={(e) => e.stopPropagation()}>
      <div class="kbd-head">
        <h3>⌨️ {i18n.t("kbd.title")}</h3>
        <button class="kbd-close" onclick={() => (open = false)} aria-label="Close">✕</button>
      </div>

      <h4>{i18n.t("kbd.group_nav")}</h4>
      <table class="kbd-table">
        <tbody>
          <tr><td><kbd>1</kbd></td><td>{i18n.t("kbd.nav_dashboard")}</td></tr>
          <tr><td><kbd>2</kbd></td><td>{i18n.t("kbd.nav_stats")}</td></tr>
          <tr><td><kbd>3</kbd></td><td>{i18n.t("kbd.nav_history")}</td></tr>
          <tr><td><kbd>s</kbd></td><td>{i18n.t("kbd.nav_settings")}</td></tr>
          <tr><td><kbd>?</kbd> {i18n.t("kbd.or")} <kbd>h</kbd></td><td>{i18n.t("kbd.nav_help")}</td></tr>
          <tr><td><kbd>Esc</kbd></td><td>{i18n.t("kbd.nav_close")}</td></tr>
        </tbody>
      </table>

      <h4>{i18n.t("kbd.group_fan")}</h4>
      <table class="kbd-table">
        <tbody>
          <tr><td><kbd>←</kbd> <kbd>→</kbd></td><td>{i18n.t("kbd.fan_temp")}</td></tr>
          <tr><td><kbd>↑</kbd> <kbd>↓</kbd></td><td>{i18n.t("kbd.fan_pct")}</td></tr>
          <tr><td><kbd>Shift</kbd> + {i18n.t("kbd.arrows")}</td><td>{i18n.t("kbd.fan_shift")}</td></tr>
          <tr><td><kbd>Tab</kbd></td><td>{i18n.t("kbd.fan_tab")}</td></tr>
          <tr><td><kbd>Del</kbd> {i18n.t("kbd.or")} <kbd>Backspace</kbd></td><td>{i18n.t("kbd.fan_remove")}</td></tr>
        </tbody>
      </table>

      <h4>{i18n.t("kbd.group_chart")}</h4>
      <table class="kbd-table">
        <tbody>
          <tr><td>⬇️ {i18n.t("kbd.chart_dl_btn")}</td><td>{i18n.t("kbd.chart_dl")}</td></tr>
          <tr><td>🔔 {i18n.t("kbd.header_chip")}</td><td>{i18n.t("kbd.header_chip_desc")}</td></tr>
        </tbody>
      </table>

      <p class="kbd-hint">{i18n.t("kbd.press_to_close")}</p>
    </div>
  </div>
{/if}

<style>
  .kbd-overlay {
    position: fixed; inset: 0;
    background: rgba(0, 0, 0, 0.6);
    display: flex; align-items: center; justify-content: center;
    z-index: 99;
    backdrop-filter: blur(2px);
  }
  .kbd-card {
    background: var(--bg-card);
    border: 1px solid var(--border-subtle);
    border-radius: 10px;
    padding: 1.4em 1.6em;
    max-width: 560px;
    width: 90%;
    max-height: 80vh;
    overflow-y: auto;
    box-shadow: 0 20px 60px rgba(0,0,0,0.5);
  }
  .kbd-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 0.6em; }
  .kbd-head h3 { margin: 0; color: var(--text-muted); }
  .kbd-close {
    background: none; border: none; color: var(--text-dim);
    font-size: 1.1em; cursor: pointer; padding: 0.2em 0.5em;
  }
  .kbd-close:hover { color: var(--accent); }
  .kbd-card h4 {
    color: var(--text-muted); font-size: 0.85em; font-weight: 600;
    margin: 1.2em 0 0.4em; text-transform: uppercase; letter-spacing: 0.04em;
  }
  .kbd-card h4:first-of-type { margin-top: 0.4em; }
  .kbd-table { width: 100%; font-size: 0.88em; }
  .kbd-table td { padding: 0.3em 0.5em; vertical-align: middle; }
  .kbd-table td:first-child { width: 140px; color: var(--text-dim); }
  kbd {
    display: inline-block;
    padding: 0.15em 0.45em;
    background: var(--bg-page);
    border: 1px solid var(--border-subtle);
    border-bottom-width: 2px;
    border-radius: 3px;
    font-family: ui-monospace, monospace;
    font-size: 0.82em;
    color: var(--text-muted);
    min-width: 1.4em;
    text-align: center;
  }
  .kbd-hint {
    margin-top: 1em;
    color: var(--text-dim);
    font-size: 0.78em;
    text-align: center;
  }
</style>
