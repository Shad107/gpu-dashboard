<script lang="ts">
  import { onMount, onDestroy } from "svelte";
  import { live } from "../lib/stores.svelte";
  import { modal } from "../lib/stores.svelte";
  import { gpu } from "../lib/gpu.svelte";
  import { i18n } from "../lib/i18n/index.svelte";

  const gpus = $derived(live.data?.gpus_available ?? []);
  const hasMultipleGpus = $derived(gpus.length > 1);
  const selectedGpuName = $derived(
    gpus.find(g => g.index === gpu.selected)?.name ?? live.data?.gpu?.name ?? "…"
  );
  const singleGpuName = $derived(live.data?.gpu?.name ?? "…");
  const tsText = $derived(
    live.error
      ? `${i18n.t("ts.network_error")}: ${live.error}`
      : live.data
        ? `${i18n.t("ts.updated")} ${new Date().toLocaleTimeString()}`
        : i18n.t("ts.loading")
  );

  // ── Status chip (R&D #3.2, cycle 135) ────────────────────────────────
  // Polls /api/health for recent_alerts + /api/profile-stats for recent
  // switches every 60s. Shows whichever is the most recent operational
  // event as a clickable pill in the header.
  type ChipState = {
    kind: "alert" | "profile_switch" | "update" | "none";
    label: string;
    ts: number;
  };
  let chip = $state<ChipState>({ kind: "none", label: "", ts: 0 });
  let chipTimer: ReturnType<typeof setInterval> | null = null;
  // Update check (cycle 138 — user request) — hourly poll, sticky chip
  let updateInfo = $state<Awaited<ReturnType<typeof api.updateCheck>> | null>(null);
  let updateTimer: ReturnType<typeof setInterval> | null = null;
  async function refreshUpdate() {
    try { updateInfo = await api.updateCheck(); } catch {}
  }

  function fmtAgo(ts: number): string {
    const dt = Math.floor(Date.now() / 1000) - ts;
    if (dt < 60) return `${dt}s`;
    if (dt < 3600) return `${Math.floor(dt / 60)}m`;
    if (dt < 86400) return `${Math.floor(dt / 3600)}h`;
    return `${Math.floor(dt / 86400)}d`;
  }

  async function refreshChip() {
    try {
      // Update available trumps everything else
      if (updateInfo?.behind && updateInfo.behind > 0) {
        chip = {
          kind: "update",
          label: `🔔 ${updateInfo.behind} ${i18n.t("header.commits_behind") ?? "commits behind"}`,
          ts: 0,
        };
        return;
      }
      const r = await fetch("/api/health", { cache: "no-store" });
      const j = await r.json();
      const a = (j.recent_alerts ?? [])[0];
      if (a && a.ts) {
        chip = { kind: "alert", label: `🚨 ${a.payload?.kind || "alert"}`, ts: a.ts };
        return;
      }
      const ps = await fetch("/api/profile-stats?since=3600", { cache: "no-store" });
      const pj = await ps.json();
      const ev = (pj.recent_events ?? [])[0];
      if (ev && ev.ts && (Date.now() / 1000 - ev.ts) < 3600) {
        const emoji = ev.to === "boost" ? "🚀" : ev.to === "sweet" ? "⭐" : ev.to === "silent" ? "🤫" : "·";
        chip = { kind: "profile_switch", label: `${emoji} ${ev.to}`, ts: ev.ts };
        return;
      }
      chip = { kind: "none", label: "", ts: 0 };
    } catch {
      // keep last state
    }
  }
  function openChipTarget() {
    if (chip.kind === "alert") modal.show("alerts");
    else if (chip.kind === "profile_switch") modal.show("about");
    else if (chip.kind === "update") modal.show("about");
  }
  onMount(() => {
    refreshChip();
    refreshUpdate();
    chipTimer = setInterval(refreshChip, 60_000);
    updateTimer = setInterval(async () => { await refreshUpdate(); refreshChip(); }, 3600_000);
  });
  onDestroy(() => {
    if (chipTimer) clearInterval(chipTimer);
    if (updateTimer) clearInterval(updateTimer);
  });
</script>

<div class="header">
  <div>
    <h1>🎛️ {i18n.t("app.title")}</h1>
    {#if hasMultipleGpus}
      <div class="ts" style="display:flex;align-items:center;gap:.6em;flex-wrap:wrap">
        <span>{i18n.t("header.gpu_picker_label")}</span>
        <select class="gpu-picker" value={gpu.selected} onchange={(e) => gpu.set(parseInt((e.target as HTMLSelectElement).value, 10))}>
          {#each gpus as g}
            <option value={g.index}>GPU {g.index} — {g.name}</option>
          {/each}
        </select>
        <span style="color:var(--text-faint)">· {tsText}</span>
      </div>
    {:else}
      <div class="ts">{singleGpuName} · {tsText}</div>
    {/if}
  </div>
  {#if chip.kind !== "none"}
    <button
      class="status-chip"
      class:chip-alert={chip.kind === "alert"}
      class:chip-profile={chip.kind === "profile_switch"}
      class:chip-update={chip.kind === "update"}
      onclick={openChipTarget}
      title={chip.kind === "update" ? (i18n.t("header.update_chip_title") ?? "Click to update") : (i18n.t("header.chip_click_to_open") ?? "Click for details")}
    >
      {chip.label}
      {#if chip.kind !== "update"}
        <span class="chip-ago">{fmtAgo(chip.ts)}</span>
      {/if}
    </button>
  {/if}
  <button
    class="gear-btn"
    class:active={modal.open}
    title={i18n.t("header.gear_title")}
    aria-label={i18n.t("modal.settings")}
    onclick={() => (modal.open ? modal.close() : modal.show())}
  >
    <svg viewBox="0 0 24 24" fill="currentColor">
      <path d="M12 15.5A3.5 3.5 0 0 1 8.5 12 3.5 3.5 0 0 1 12 8.5a3.5 3.5 0 0 1 3.5 3.5 3.5 3.5 0 0 1-3.5 3.5m7.43-2.53c.04-.32.07-.64.07-.97 0-.33-.03-.66-.07-1l2.11-1.63c.19-.15.24-.42.12-.64l-2-3.46c-.12-.22-.39-.31-.61-.22l-2.49 1c-.52-.39-1.06-.73-1.69-.98l-.37-2.65A.506.506 0 0 0 14 2h-4c-.25 0-.46.18-.5.42l-.37 2.65c-.63.25-1.17.59-1.69.98l-2.49-1c-.22-.09-.49 0-.61.22l-2 3.46c-.13.22-.07.49.12.64L4.57 11c-.04.34-.07.67-.07 1 0 .33.03.65.07.97l-2.11 1.66c-.19.15-.25.42-.12.64l2 3.46c.12.22.39.3.61.22l2.49-1.01c.52.4 1.06.74 1.69.99l.37 2.65c.04.24.25.42.5.42h4c.25 0 .46-.18.5-.42l.37-2.65c.63-.26 1.17-.59 1.69-.99l2.49 1.01c.22.08.49 0 .61-.22l2-3.46c.12-.22.07-.49-.12-.64l-2.11-1.66Z" />
    </svg>
  </button>
</div>

<style>
  .status-chip {
    display: inline-flex;
    align-items: center;
    gap: 0.4em;
    padding: 0.3em 0.7em;
    border-radius: 999px;
    border: 1px solid var(--border-subtle);
    background: var(--bg-card);
    color: var(--text-muted);
    font-size: 0.85em;
    cursor: pointer;
    margin-right: 0.6em;
    transition: background 0.15s, color 0.15s, border-color 0.15s;
  }
  .status-chip:hover { background: var(--bg-page); color: var(--accent); }
  .chip-alert {
    color: var(--accent-warn);
    border-color: rgba(251, 191, 36, 0.4);
    background: rgba(251, 191, 36, 0.08);
  }
  .chip-profile { color: var(--accent); }
  .chip-update {
    color: var(--accent-cool);
    border-color: rgba(96, 165, 250, 0.4);
    background: rgba(96, 165, 250, 0.10);
    animation: chip-pulse 2.5s ease-in-out infinite;
  }
  @keyframes chip-pulse {
    0%, 100% { box-shadow: 0 0 0 0 rgba(96, 165, 250, 0); }
    50% { box-shadow: 0 0 0 4px rgba(96, 165, 250, 0.15); }
  }
  .chip-ago { color: var(--text-dim); font-size: 0.85em; font-variant-numeric: tabular-nums; }
</style>
