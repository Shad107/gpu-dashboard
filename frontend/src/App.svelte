<script lang="ts">
  import { onMount, onDestroy } from "svelte";
  import Header from "./components/Header.svelte";
  import TopNav from "./components/TopNav.svelte";
  import Cards from "./components/Cards.svelte";
  import CoolingChart from "./components/CoolingChart.svelte";
  import PowerChart from "./components/PowerChart.svelte";
  import HistoryView from "./components/HistoryView.svelte";
  import StatsView from "./components/StatsView.svelte";
  import SettingsModal from "./components/SettingsModal.svelte";
  import SetupWizard from "./components/SetupWizard.svelte";
  import IdleBanner from "./components/IdleBanner.svelte";
  import LatestAlertFooter from "./components/LatestAlertFooter.svelte";
  import Toast from "./components/Toast.svelte";
  import { live, wizard, modal, toast } from "./lib/stores.svelte";
  import { view } from "./lib/view.svelte";
  import { theme } from "./lib/theme.svelte";  // applies theme class on boot
  import { push } from "./lib/push.svelte";
  import { i18n } from "./lib/i18n/index.svelte";

  // Reference theme so Svelte doesn't tree-shake the side-effect import
  void theme;

  // Global keyboard shortcuts.
  // Ignored when typing in inputs (input/textarea/select/contenteditable).
  function onKey(e: KeyboardEvent) {
    const target = e.target as HTMLElement | null;
    if (target) {
      const tag = target.tagName?.toLowerCase();
      if (tag === "input" || tag === "textarea" || tag === "select") return;
      if (target.isContentEditable) return;
    }
    if (e.ctrlKey || e.metaKey || e.altKey) return;

    if (e.key === "Escape") {
      if (modal.open) { modal.close(); e.preventDefault(); }
      return;
    }
    if (e.key === "g" || e.key === "G") {
      if (modal.open) modal.close(); else modal.show();
      e.preventDefault(); return;
    }
    if (e.key === "h" || e.key === "H") {
      view.set("history"); e.preventDefault(); return;
    }
    if (e.key === "a" || e.key === "A") {
      // About stays in the Settings modal per user feedback 23:25
      modal.show("about"); e.preventDefault(); return;
    }
    if (e.key === "d" || e.key === "D") {
      view.set("dashboard"); e.preventDefault(); return;
    }
    if (e.key === "s" || e.key === "S") {
      view.set("stats"); e.preventDefault(); return;
    }
    if (e.key === "?") {
      toast.emit("Shortcuts: g=settings · h=history · a=about · ESC=close · r=redo wizard", "ok", 5000);
      e.preventDefault(); return;
    }
    if (e.key === "r" || e.key === "R") {
      // Re-run wizard (safer than auto-restart on a single key)
      wizard.request(); e.preventDefault(); return;
    }
  }

  onMount(() => {
    live.start(5000);
    document.title = i18n.t("app.title");
    document.documentElement.lang = i18n.lang;
    window.addEventListener("keydown", onKey);
    // Wire push notifications (no-op if browser unsupported)
    push.init();
  });
  onDestroy(() => {
    live.stop();
    window.removeEventListener("keydown", onKey);
  });

  // Live tab title : useful when the dashboard tab is hidden in a tab group
  // — a glance at the tab strip shows the GPU's current temp + power.
  // Format : "44°C · 250W · GPU Dashboard"
  $effect(() => {
    if (showWizard) {
      document.title = "🧙 " + i18n.t("app.title");
      return;
    }
    const g = live.data?.gpu;
    if (g?.alive) {
      const t = g.temp.toFixed(0);
      const p = g.power.toFixed(0);
      document.title = `${t}°C · ${p}W · ${i18n.t("app.title")}`;
    } else if (live.data && g && !g.alive) {
      document.title = "⚠️ " + i18n.t("app.title");
    } else {
      document.title = i18n.t("app.title");
    }
  });

  // Show the wizard when either:
  //  - backend reports no config exists (first run), OR
  //  - user explicitly asked to re-run it from Services tab.
  const setupRequired = $derived(live.data?.setup_required === true);
  const showWizard = $derived(setupRequired || wizard.userRequested);
</script>

{#if showWizard}
  <SetupWizard dismissable={!setupRequired} />
  <Toast />
{:else}
  <Header />
  <TopNav />
  <IdleBanner />
  {#if view.current === "dashboard"}
    <Cards />
    <CoolingChart />
    <PowerChart />
    <LatestAlertFooter />
  {:else if view.current === "history"}
    <HistoryView />
  {:else if view.current === "stats"}
    <StatsView />
  {/if}
  <SettingsModal />
  <Toast />

  <div class="footer">
    {i18n.t("footer.refresh")} · <a href="https://github.com/Shad107/gpu-dashboard">github</a>
  </div>
{/if}
