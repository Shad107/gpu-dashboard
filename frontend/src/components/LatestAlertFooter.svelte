<script lang="ts">
  // Tiny line at the bottom showing the freshness of the last alert.
  // Pulls from /api/health.recent_alerts (no extra polling — same endpoint
  // Uptime Kuma uses). Refreshes every 60s.
  import { onMount, onDestroy } from "svelte";
  import { i18n } from "../lib/i18n/index.svelte";

  type HealthAlert = { ts: number; payload: any };
  let alerts = $state<HealthAlert[]>([]);
  let loaded = $state(false);
  let timer: ReturnType<typeof setInterval> | null = null;

  async function load() {
    try {
      const r = await fetch("/api/health", { cache: "no-store" });
      // /api/health returns 503 when degraded — still has the JSON body
      const j = await r.json();
      alerts = (j.recent_alerts ?? []) as HealthAlert[];
      loaded = true;
    } catch {
      // silent — keep last state
    }
  }
  onMount(() => {
    load();
    timer = setInterval(load, 60_000);
  });
  onDestroy(() => { if (timer) clearInterval(timer); });

  function fmtRelative(ts: number): string {
    const dt = Math.floor(Date.now() / 1000) - ts;
    if (dt < 60) return `${dt}s`;
    if (dt < 3600) return `${Math.floor(dt / 60)}m`;
    if (dt < 86400) return `${Math.floor(dt / 3600)}h`;
    return `${Math.floor(dt / 86400)}d`;
  }

  const latest = $derived(alerts.length > 0 ? alerts[0] : null);
  // alerts payload looks like { kind, value, threshold, title?, body? }
  const latestKind = $derived(latest?.payload?.kind ?? "");
</script>

{#if loaded}
  {#if latest}
    <div class="alert-footer alert-recent">
      <span>🚨 {i18n.t("alertfooter.last")}:</span>
      <b>{fmtRelative(latest.ts)}</b>
      <span class="ago">{i18n.t("alertfooter.ago")}</span>
      {#if latestKind}<span class="kind">— {latestKind}</span>{/if}
    </div>
  {:else}
    <div class="alert-footer alert-quiet">
      <span>✓ {i18n.t("alertfooter.no_alerts_7d")}</span>
    </div>
  {/if}
{/if}

<style>
  .alert-footer {
    display: flex;
    align-items: center;
    gap: 0.4em;
    font-size: 0.82em;
    padding: 0.4em 0.9em;
    border-radius: 6px;
    margin: 0.4em 0 0;
    border: 1px solid var(--border-subtle);
  }
  .alert-recent { color: var(--accent-warn); background: rgba(251, 191, 36, 0.05); }
  .alert-quiet  { color: var(--accent); }
  .alert-footer .ago  { color: var(--text-faint); }
  .alert-footer .kind { color: var(--text-dim); font-family: ui-monospace, monospace; font-size: 0.92em; }
</style>
