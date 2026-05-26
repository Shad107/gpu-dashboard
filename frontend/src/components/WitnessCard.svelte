<script lang="ts">
  /**
   * F2.1 — State Witness card on the main dashboard.
   *
   * Lets the user snapshot the system state at a moment in time,
   * then diff two snapshots to see what changed when LLM tok/s
   * suddenly tanks after a driver bump, kernel update, or apt
   * upgrade.
   *
   * The diff result opens in WitnessDiffModal.
   */
  import { onMount } from "svelte";
  import { toast } from "../lib/stores.svelte";
  import { i18n } from "../lib/i18n/index.svelte";
  import WitnessDiffModal from "./WitnessDiffModal.svelte";

  type SnapshotMeta = {
    id: string;
    taken_at: string | null;
    reason: string | null;
    size_bytes: number;
    hostname?: string | null;
  };

  let snapshots = $state<SnapshotMeta[]>([]);
  let taking = $state(false);
  let diffOpen = $state(false);
  let diffBefore = $state<string | null>(null);
  let diffAfter = $state<string | null>(null);

  async function refresh() {
    try {
      const r = await fetch("/api/witness/list").then((x) => x.json());
      snapshots = r.snapshots ?? [];
    } catch {
      snapshots = [];
    }
  }

  onMount(refresh);

  async function takeSnapshot() {
    if (taking) return;
    taking = true;
    try {
      const r = await fetch("/api/witness/take", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reason: "manual" }),
      }).then((x) => x.json());
      if (r.ok) {
        toast.emit("📸 " + (i18n.t("witness.snapshot_taken") ??
          "Snapshot pris"), "ok");
        await refresh();
      } else {
        toast.emit("✗ " + (r.message ?? r.error ?? "failed"), "err");
      }
    } catch (e) {
      toast.emit("✗ " + (e as Error).message, "err");
    } finally {
      taking = false;
    }
  }

  function diffLastTwo() {
    if (snapshots.length < 2) return;
    // Snapshots are sorted newest-first; "before" = older, "after" = newer.
    diffBefore = snapshots[1].id;
    diffAfter = snapshots[0].id;
    diffOpen = true;
  }

  function fmtAge(iso: string | null): string {
    if (!iso) return "?";
    const t = Date.parse(iso);
    if (isNaN(t)) return "?";
    const s = Math.floor((Date.now() - t) / 1000);
    if (s < 60) return `${s}s`;
    const m = Math.floor(s / 60);
    if (m < 60) return `${m}m`;
    const h = Math.floor(m / 60);
    if (h < 24) return `${h}h${(m % 60).toString().padStart(2, "0")}`;
    const d = Math.floor(h / 24);
    return `${d}j${(h % 24)}h`;
  }
</script>

<div class="card witness-card">
  <h2 title={i18n.t("witness.tooltip") ??
    "Snapshot du driver, des modules, des paquets et du sysfs PCIe. Diff entre deux snapshots pour bisect une régression tok/s."}>
    🔍 {i18n.t("card.witness") ?? "Witness"}
  </h2>

  <button class="btn"
          style="margin:.4em 0;width:100%;
                 background:var(--accent);color:var(--bg-1);font-weight:600"
          disabled={taking}
          onclick={takeSnapshot}>
    {taking ? "⏳ ..." : "📸 " + (i18n.t("witness.take") ?? "Prendre un snapshot")}
  </button>

  {#if snapshots.length === 0}
    <div class="sub muted small" style="text-align:center">
      {i18n.t("witness.empty") ??
        "Pas encore de snapshot. Prends-en un avant chaque upgrade driver/kernel."}
    </div>
  {:else}
    <div class="sub muted small" style="margin-bottom:.3em">
      {snapshots.length} {i18n.t("witness.count_suffix") ?? "snapshot(s)"}
    </div>
    <ul class="snap-list">
      {#each snapshots.slice(0, 4) as s}
        <li>
          <span class="age">{fmtAge(s.taken_at)}</span>
          <span class="reason muted">{s.reason ?? "?"}</span>
        </li>
      {/each}
      {#if snapshots.length > 4}
        <li class="muted small">… {snapshots.length - 4} {i18n.t("witness.more") ?? "de plus"}</li>
      {/if}
    </ul>
    {#if snapshots.length >= 2}
      <button class="btn btn-small"
              style="width:100%;margin-top:.4em"
              onclick={diffLastTwo}>
        🔀 {i18n.t("witness.diff_last_two") ?? "Diff les 2 derniers"}
      </button>
    {:else}
      <div class="sub muted small" style="text-align:center;margin-top:.3em">
        {i18n.t("witness.need_second") ??
          "Prends-en un autre pour activer le diff"}
      </div>
    {/if}
  {/if}
</div>

<WitnessDiffModal bind:open={diffOpen}
                   beforeId={diffBefore}
                   afterId={diffAfter} />

<style>
  .witness-card h2 {
    cursor: help;
  }
  .snap-list {
    list-style: none;
    padding: 0;
    margin: 0;
    font-size: 0.85em;
  }
  .snap-list li {
    display: flex;
    justify-content: space-between;
    padding: 2px 0;
    border-bottom: 1px solid var(--border, #2a2f36);
  }
  .snap-list li:last-child { border-bottom: none; }
  .age {
    font-weight: 600;
  }
  .reason {
    font-style: italic;
  }
</style>
