<script lang="ts">
  import { live, toast } from "../lib/stores.svelte";
  import { i18n } from "../lib/i18n/index.svelte";
  import { api } from "../lib/api";

  const IDLE_THRESHOLD = 5;       // % util considered "idle"
  const SAMPLES_NEEDED = 360;     // 30 min × 60 / 5s interval

  let dismissed = $state(false);  // user clicked X — hide until next reload
  let savingPerMonth = $state<number | null>(null);

  // Refetch electricity to compute savings (~€0.x / month if stopped)
  $effect(() => {
    if (live.data) {
      api.electricity(3600).then(e => {
        if (e.ok) savingPerMonth = e.monthly_cost;
      }).catch(() => {});
    }
  });

  const idleStreak = $derived.by(() => {
    const samples = live.data?.metrics ?? [];
    if (samples.length < SAMPLES_NEEDED) return 0;
    const tail = samples.slice(-SAMPLES_NEEDED);
    // Count consecutive low-util samples from the end
    let n = 0;
    for (let i = tail.length - 1; i >= 0; i--) {
      const u = (tail[i] as any).util_gpu ?? 0;
      if (u < IDLE_THRESHOLD) n++;
      else break;
    }
    return n;
  });

  const isIdle = $derived(idleStreak >= SAMPLES_NEEDED);
  const idleMinutes = $derived(Math.round(idleStreak * 5 / 60));

  async function stopServer() {
    try { await api.stop(); } catch { /* expected — connection drops */ }
    toast.emit("🛑 " + i18n.t("services.stopped"), "ok");
  }
</script>

{#if isIdle && !dismissed}
  <div class="idle-banner">
    <div class="idle-icon">💤</div>
    <div class="idle-text">
      <strong>{i18n.t("idle.title", { n: idleMinutes })}</strong>
      {#if savingPerMonth !== null && savingPerMonth > 0.5}
        <div class="idle-sub">{i18n.t("idle.save_hint", { eur: savingPerMonth.toFixed(2) })}</div>
      {:else}
        <div class="idle-sub">{i18n.t("idle.suggest_stop")}</div>
      {/if}
    </div>
    <div class="idle-actions">
      <button class="btn btn-danger" onclick={stopServer}>
        🛑 {i18n.t("services.stop_btn")}
      </button>
      <button class="btn" onclick={() => dismissed = true}>×</button>
    </div>
  </div>
{/if}

<style>
  .idle-banner {
    display: flex;
    align-items: center;
    gap: 0.9em;
    background: linear-gradient(to right, #14171f, #1a1d24);
    border: 1px solid #2a2e38;
    border-left: 4px solid #fbbf24;
    border-radius: 8px;
    padding: 0.7em 1em;
    margin: 0 0 0.6em;
    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.3);
  }
  .idle-icon { font-size: 1.6em; }
  .idle-text { flex: 1; }
  .idle-text strong { color: #fbbf24; }
  .idle-sub { font-size: 0.82em; color: #8a8f9a; margin-top: 0.2em; }
  .idle-actions { display: flex; gap: 0.4em; }
</style>
