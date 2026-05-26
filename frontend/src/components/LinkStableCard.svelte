<script lang="ts">
  /**
   * F7 — Link Stable Mode card.
   *
   * Lets the user toggle a "clock-lock" mode that keeps the GPU
   * awake so its PCIe link negotiates a sustained higher speed
   * (typically Gen 2 instead of Gen 1) on flaky OcuLink/Thunderbolt
   * docks where the retimer can't survive endless Gen1↔Gen4
   * renegotiation cycles.
   *
   * Trade-off: idle power ~7W → ~20W in exchange for stable link.
   */
  import { onMount, onDestroy } from "svelte";
  import { toast, installPrompt } from "../lib/stores.svelte";
  import { i18n } from "../lib/i18n/index.svelte";

  type Status = {
    wrapper_available: boolean;
    wrapper_path: string;
    link: {
      bdf: string;
      current_link_speed: string | null;
      current_link_width: string | null;
      max_link_speed: string | null;
      max_link_width: string | null;
      downgraded: boolean;
    };
    pstate: string | null;
    clocks: {
      persistence_mode: boolean | null;
      current_clock_mhz: number | null;
      max_clock_mhz: number | null;
    };
    defaults: { min_mhz: number; max_mhz: number };
  };

  let s = $state<Status | null>(null);
  let busy = $state(false);
  let installedScripts = $state<Record<string, boolean>>({});
  let timer: number | null = null;

  async function refresh() {
    try {
      s = await fetch("/api/link-stable/status").then((x) => x.json());
    } catch {}
  }
  async function refreshInstalled() {
    try {
      const r = await fetch("/api/install/list").then((x) => x.json());
      const out: Record<string, boolean> = {};
      for (const v of r.scripts ?? []) out[v.id] = !!v.installed;
      installedScripts = out;
    } catch {}
  }

  onMount(() => {
    refresh();
    refreshInstalled();
    timer = window.setInterval(refresh, 4000);
  });
  onDestroy(() => {
    if (timer !== null) clearInterval(timer);
  });

  async function enable() {
    if (busy || !s) return;
    busy = true;
    try {
      const r = await fetch("/api/link-stable/enable", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          min_mhz: s.defaults.min_mhz,
          max_mhz: s.defaults.max_mhz,
        }),
      }).then((x) => x.json());
      if (r.ok) {
        toast.emit("🔒 " + (i18n.t("link_stable.enabled") ?? "Mode stable activé"), "ok");
        await refresh();
      } else {
        toast.emit("✗ " + (r.message ?? r.error ?? "enable failed"), "err");
      }
    } catch (e) {
      toast.emit("✗ " + (e as Error).message, "err");
    } finally {
      busy = false;
    }
  }

  async function disable() {
    if (busy) return;
    busy = true;
    try {
      const r = await fetch("/api/link-stable/disable", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      }).then((x) => x.json());
      if (r.ok) {
        toast.emit("🔓 " + (i18n.t("link_stable.disabled") ?? "Mode stable désactivé"), "ok");
        await refresh();
      } else {
        toast.emit("✗ " + (r.message ?? r.error ?? "disable failed"), "err");
      }
    } catch (e) {
      toast.emit("✗ " + (e as Error).message, "err");
    } finally {
      busy = false;
    }
  }

  function parseGT(speed: string | null): number {
    // "2.5 GT/s PCIe" → 2.5
    if (!speed) return 0;
    const m = speed.match(/^([\d.]+)/);
    return m ? parseFloat(m[1]) : 0;
  }
  function gtToGen(gt: number): string {
    if (gt >= 32) return "5";
    if (gt >= 16) return "4";
    if (gt >= 8) return "3";
    if (gt >= 5) return "2";
    if (gt >= 2) return "1";
    return "?";
  }

  // The mode is "on" when the GPU is being kept awake by a clock-lock.
  // We don't have a direct "is_locked" signal from nvidia-smi on older
  // drivers, so we infer: persistence mode on AND current_clock_mhz
  // is well above the typical idle floor (~210MHz on Ampere).
  const isActive = $derived.by(() => {
    if (!s?.clocks) return false;
    if (!s.clocks.persistence_mode) return false;
    if (s.clocks.current_clock_mhz === null) return false;
    return s.clocks.current_clock_mhz >= (s.defaults.min_mhz - 100);
  });

  const linkBadge = $derived.by(() => {
    if (!s?.link) return null;
    const cur = parseGT(s.link.current_link_speed);
    const mx = parseGT(s.link.max_link_speed);
    return {
      gen: gtToGen(cur),
      maxGen: gtToGen(mx),
      cur, mx,
      downgraded: s.link.downgraded,
    };
  });
</script>

<div class="card link-stable-card">
  <h2 title={i18n.t("link_stable.tooltip") ??
    "Verrouille les clocks GPU pour maintenir le lien PCIe à sa vitesse soutenable la plus haute (souvent Gen 2 sur les docks OcuLink Aliexpress)."}>
    🔒 {i18n.t("card.link_stable") ?? "Link Stable"}
  </h2>

  {#if !s}
    <div class="sub muted small">⏳</div>
  {:else if !s.wrapper_available}
    <div class="sub muted small" style="margin:.3em 0">
      {linkBadge ? `Gen ${linkBadge.gen}` : "—"}
      {#if linkBadge?.downgraded}
        <span class="warn">⚠ downgraded</span>
      {/if}
    </div>
    <button class="btn btn-small"
            style="width:100%;background:transparent;border:1px dashed var(--accent);
                   color:var(--accent);font-size:.78em"
            onclick={() => installPrompt.request("link_stable_wrapper",
              () => { refresh(); refreshInstalled(); })}>
      🔧 {i18n.t("link_stable.install") ?? "Installer le wrapper"}
    </button>
  {:else}
    <div class="link-line">
      <span class="big" class:warn={linkBadge?.downgraded}
            class:ok={isActive}>
        Gen {linkBadge?.gen ?? "?"}
      </span>
      <span class="muted small">/ {linkBadge?.maxGen ?? "?"}</span>
    </div>
    <div class="sub muted small">
      {s.link.current_link_speed} · x{s.link.current_link_width}
      {#if linkBadge?.downgraded && !isActive}
        <span class="warn">↓</span>
      {/if}
    </div>
    {#if s.clocks.current_clock_mhz != null}
      <div class="sub muted small">
        {s.pstate ?? "?"} · {s.clocks.current_clock_mhz} MHz
      </div>
    {/if}

    <button class="btn btn-small"
            style="width:100%;margin-top:.4em"
            class:active={isActive}
            disabled={busy}
            onclick={isActive ? disable : enable}>
      {#if busy}
        ⏳
      {:else if isActive}
        🔓 {i18n.t("link_stable.btn_off") ?? "Désactiver"}
      {:else}
        🔒 {i18n.t("link_stable.btn_on") ?? "Verrouiller"}
        {s.defaults.min_mhz}–{s.defaults.max_mhz}
      {/if}
    </button>
  {/if}
</div>

<style>
  .link-stable-card h2 { cursor: help; }
  .link-line {
    display: flex;
    align-items: baseline;
    gap: 6px;
  }
  .btn.active {
    background: var(--accent);
    color: var(--bg-1);
    font-weight: 600;
  }
</style>
