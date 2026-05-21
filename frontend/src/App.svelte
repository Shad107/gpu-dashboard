<script lang="ts">
  import { onMount, onDestroy } from "svelte";
  import Header from "./components/Header.svelte";
  import Cards from "./components/Cards.svelte";
  import CoolingChart from "./components/CoolingChart.svelte";
  import PowerChart from "./components/PowerChart.svelte";
  import SettingsModal from "./components/SettingsModal.svelte";
  import SetupWizard from "./components/SetupWizard.svelte";
  import Toast from "./components/Toast.svelte";
  import { live, wizard, modal, toast } from "./lib/stores.svelte";
  import { i18n } from "./lib/i18n/index.svelte";

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
      modal.show("history"); e.preventDefault(); return;
    }
    if (e.key === "a" || e.key === "A") {
      modal.show("about"); e.preventDefault(); return;
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
  });
  onDestroy(() => {
    live.stop();
    window.removeEventListener("keydown", onKey);
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
  <Cards />
  <CoolingChart />
  <PowerChart />
  <SettingsModal />
  <Toast />

  <div class="footer">
    {i18n.t("footer.refresh")} · <a href="https://github.com/Shad107/gpu-dashboard">github</a>
  </div>
{/if}
