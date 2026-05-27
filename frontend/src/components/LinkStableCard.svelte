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
    gen_presets?: Record<string, { min_mhz: number; max_mhz: number } | null>;
    stable?: {
      since_ts: number | null;
      for_seconds: number;
      transitions: number;
    };
    locked?: {
      target_gen: number | null;
      min_mhz: number | null;
      max_mhz: number | null;
    };
  };

  let s = $state<Status | null>(null);
  let busy = $state(false);
  let installedScripts = $state<Record<string, boolean>>({});
  let timer: number | null = null;
  // F7.4 — the stable-for clock now lives on the backend, which
  // survives browser refreshes. We just tick a `nowTick` locally so
  // the displayed seconds count smoothly between the 4s status
  // polls (instead of jumping in 4s steps).
  let nowTick = $state(Date.now());
  let pollAt = $state(Date.now());  // wall-clock when last poll landed

  async function refresh() {
    try {
      s = await fetch("/api/link-stable/status").then((x) => x.json());
      pollAt = Date.now();
    } catch {}
  }

  function fmtStable(ms: number): string {
    const s = Math.floor(ms / 1000);
    if (s < 60) return `${s}s`;
    const m = Math.floor(s / 60);
    if (m < 60) return `${m}m${(s % 60).toString().padStart(2, "0")}s`;
    const h = Math.floor(m / 60);
    return `${h}h${(m % 60).toString().padStart(2, "0")}m`;
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
    // tick the "stable for" clock every 500ms so the badge ages
    // smoothly without waiting for the 4s status poll.
    const t2 = window.setInterval(() => (nowTick = Date.now()), 500);
    return () => clearInterval(t2);
  });
  onDestroy(() => {
    if (timer !== null) clearInterval(timer);
  });

  // F7.4 — derived from the backend's authoritative timer. We add
  // a local elapsed-since-poll delta so the displayed seconds tick
  // smoothly between the 4s polls (otherwise it would jump in
  // 4-second steps).
  const stableForSec = $derived.by(() => {
    if (!s?.stable) return 0;
    return s.stable.for_seconds + (nowTick - pollAt) / 1000;
  });
  const stableFor = $derived.by(() => {
    if (!s?.stable) return null;
    return fmtStable(stableForSec * 1000);
  });
  const transitions = $derived(s?.stable?.transitions ?? 0);
  // Settling = the link just transitioned and the firmware is still
  // letting the LTSSM converge. On flaky retimers the speed shown
  // RIGHT after a page reload (or after a manual Gen change) often
  // briefly displays the firmware's preferred speed before
  // auto-downgrading. < 3s means "this value isn't yet trustworthy".
  const settling = $derived(stableForSec < 3);

  async function enableAt(targetGen: number) {
    if (busy || !s) return;
    busy = true;
    try {
      const r = await fetch("/api/link-stable/enable", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ target_gen: targetGen }),
      }).then((x) => x.json());
      if (r.ok) {
        // Give the firmware ~1.5s to re-negotiate then verify what
        // we actually got. If the retimer refused the requested
        // speed, the link will stay at a lower Gen and we surface
        // that honestly instead of pretending the click succeeded.
        await new Promise((res) => setTimeout(res, 1500));
        await refresh();
        const got = parseInt(linkBadge?.gen ?? "0");
        if (targetGen === 1) {
          toast.emit("🔓 " + (i18n.t("link_stable.disabled")
            ?? "Mode stable désactivé"), "ok");
        } else if (got === targetGen) {
          toast.emit(`🔒 Gen ${targetGen} OK`, "ok");
        } else if (got > 0 && got < targetGen) {
          toast.emit(`⚠ ${(i18n.t("link_stable.refused") ?? "Retimer refused Gen")} ${targetGen} → Gen ${got}`, "warn");
        } else {
          toast.emit(`🔒 ${(i18n.t("link_stable.try_gen") ?? "Verrouiller Gen {n}").replace("{n}", String(targetGen))}`, "ok");
        }
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

  // The mode is "on" when the backend recorded a locked target Gen
  // from a previous enable() call. Using the backend-tracked lock
  // state instead of a current_clock_mhz heuristic is reliable —
  // the actual GPU clock can briefly dip below the lock floor
  // between firmware boost cycles and used to flip isActive false
  // intermittently, hiding the ✓ on the right Gen button.
  const isActive = $derived.by(() => {
    const lockedGen = s?.locked?.target_gen;
    return lockedGen != null && lockedGen >= 2;
  });
  // The Gen highlighted as "current" is whatever the user last
  // chose via the Lock buttons. If never locked, fall back to the
  // observed link Gen (for users who just opened the dashboard
  // without ever touching the buttons).
  const highlightedGen = $derived(
    s?.locked?.target_gen ?? parseInt(linkBadge?.gen ?? "0"),
  );

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
        <span class="warn">⚠ {i18n.t("link_stable.downgraded") ?? "rétrogradé"}</span>
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
            class:ok={isActive && !settling}
            class:settling>
        Gen {linkBadge?.gen ?? "?"}
      </span>
      <span class="muted small">/ {linkBadge?.maxGen ?? "?"}</span>
      {#if settling}
        <span class="settling-tag"
              title={i18n.t("link_stable.settling_tip") ??
                "Le lien vient de se renégocier, attends 3-5s pour la valeur stable"}>
          📡 {i18n.t("link_stable.settling") ?? "négociation"}
        </span>
      {/if}
    </div>
    <div class="sub muted small">
      {s.link.current_link_speed} · x{s.link.current_link_width}
      {#if linkBadge?.downgraded && !isActive}
        <span class="warn">↓</span>
      {/if}
    </div>
    {#if stableFor}
      <div class="sub muted small" style="font-size:.75em">
        ⏱ {i18n.t("link_stable.stable_for") ?? "stable depuis"} {stableFor}
        {#if transitions > 0}
          <span class="muted">· {transitions} {i18n.t("link_stable.transitions") ?? "transitions"}</span>
        {/if}
      </div>
    {/if}
    {#if s.clocks.current_clock_mhz != null}
      <div class="sub muted small">
        {s.pstate ?? "?"} · {s.clocks.current_clock_mhz} MHz
      </div>
    {/if}

    <div class="gen-row">
      {#each [1, 2, 3, 4] as g}
        <button class="btn-gen"
                class:current={highlightedGen === g}
                disabled={busy}
                title={i18n.t(`link_stable.gen_${g}_hint` as any) ?? ""}
                onclick={() => enableAt(g)}>
          Gen {g}
        </button>
      {/each}
    </div>
    {#if busy}
      <div class="sub muted small" style="text-align:center;margin-top:.2em">⏳ ...</div>
    {/if}
  {/if}
</div>

<style>
  .link-stable-card h2 { cursor: help; }
  .big.settling {
    color: var(--text-dim, #8a93a3);
    font-style: italic;
  }
  .settling-tag {
    font-size: .7em;
    padding: 1px 5px;
    background: rgba(234, 179, 8, 0.18);
    color: var(--warn, #eab308);
    border-radius: 3px;
    margin-left: 4px;
    white-space: nowrap;
  }
  .link-line {
    display: flex;
    align-items: baseline;
    gap: 6px;
  }
  .gen-row {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 4px;
    margin-top: .4em;
  }
  .btn-gen {
    padding: 4px 2px;
    font-size: 0.75em;
    background: transparent;
    border: 1px solid var(--border);
    color: var(--text);
    border-radius: 4px;
    cursor: pointer;
    transition: background 100ms, border-color 100ms;
  }
  .btn-gen:hover:not(:disabled) {
    background: var(--bg-2);
    border-color: var(--accent);
  }
  .btn-gen.current {
    background: var(--accent);
    color: var(--bg-1);
    border-color: var(--accent);
    font-weight: 600;
  }
  .btn-gen:disabled {
    opacity: 0.5;
    cursor: progress;
  }
</style>
