<script lang="ts">
  /**
   * F5.1 — Health Strip on the main dashboard.
   *
   * Aggregates a curated set of high-signal audit modules in
   * parallel server-side and shows a thin horizontal strip with
   * one severity-colored pill per check. Click a pill (or the
   * strip background) to open the detail modal.
   */
  import { onMount, onDestroy } from "svelte";
  import { i18n } from "../lib/i18n/index.svelte";
  import HealthStripModal from "./HealthStripModal.svelte";

  type Severity = "ok" | "warn" | "err" | "unknown";
  type Check = {
    id: string;
    label: string;
    severity: Severity;
    verdict: unknown;
    error?: string;
    elapsed_ms?: number;
  };
  type Aggregate = {
    ok: boolean;
    summary: Record<Severity | "total", number>;
    overall: Severity;
    checks: Check[];
    elapsed_ms: number;
  };

  let data = $state<Aggregate | null>(null);
  let loading = $state(false);
  let modalOpen = $state(false);
  let timer: number | null = null;

  async function refresh() {
    loading = true;
    try {
      data = await fetch("/api/health-strip").then((x) => x.json());
    } catch {
      // keep previous
    } finally {
      loading = false;
    }
  }

  onMount(() => {
    refresh();
    timer = window.setInterval(refresh, 60_000);
    // Screenshot mode — auto-open the detail modal so the
    // headless-chrome capture has interesting content.
    if (location.search.match(/[?&]screenshot=health-strip(-modal)?/)) {
      const waitForData = setInterval(() => {
        if (data) {
          modalOpen = true;
          clearInterval(waitForData);
        }
      }, 100);
    }
  });
  onDestroy(() => {
    if (timer !== null) clearInterval(timer);
  });

  function open() {
    modalOpen = true;
  }

  function sevColor(s: Severity): string {
    if (s === "err") return "var(--err)";
    if (s === "warn") return "var(--warn)";
    if (s === "ok") return "var(--ok)";
    return "var(--text-dim, #8a93a3)";
  }
  function sevIcon(s: Severity): string {
    if (s === "err") return "✗";
    if (s === "warn") return "⚠";
    if (s === "ok") return "✓";
    return "?";
  }
</script>

<div class="health-strip"
     role="button"
     tabindex="0"
     onclick={open}
     onkeydown={(e) => (e.key === "Enter" || e.key === " ") && open()}
     aria-label={i18n.t("health.aria_open") ?? "Open health detail"}>
  {#if !data}
    <span class="muted small">{loading ? "⏳" : "—"}</span>
  {:else}
    <span class="overall" style:color={sevColor(data.overall)}>
      {sevIcon(data.overall)}
    </span>
    <span class="counts">
      {#if data.summary.err > 0}
        <span class="count err">✗ {data.summary.err}/{data.summary.total}</span>
        <span class="muted small">err</span>
      {/if}
      {#if data.summary.warn > 0}
        <span class="count warn">⚠ {data.summary.warn}/{data.summary.total}</span>
        <span class="muted small">warn</span>
      {/if}
      <span class="count ok">✓ {data.summary.ok}/{data.summary.total}</span>
      <span class="muted small">ok</span>
      {#if data.summary.unknown > 0}
        <span class="count unknown">? {data.summary.unknown}/{data.summary.total}</span>
        <span class="muted small">?</span>
      {/if}
    </span>
    <span class="pills">
      {#each data.checks as c}
        <span class="pill" style:background={sevColor(c.severity) + "22"}
              style:border-color={sevColor(c.severity)}
              title="{c.label}: {String(c.verdict ?? '')}">
          <span class="pill-icon" style:color={sevColor(c.severity)}>
            {sevIcon(c.severity)}
          </span>
          <span class="pill-label">{c.label}</span>
        </span>
      {/each}
    </span>
    <span class="muted small hint">
      ({data.elapsed_ms.toFixed(0)}ms · {i18n.t("health.click_detail") ?? "détail"})
    </span>
  {/if}
</div>

<HealthStripModal bind:open={modalOpen} {data} onRefresh={refresh} />

<style>
  .health-strip {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 6px 12px;
    margin: 6px 0 8px;
    background: var(--bg-2, #1c2027);
    border: 1px solid var(--border, #2a2f36);
    border-radius: 6px;
    font-size: 0.85em;
    cursor: pointer;
    user-select: none;
    flex-wrap: wrap;
    transition: background 120ms;
  }
  .health-strip:hover {
    background: var(--bg-3, #232830);
  }
  .overall {
    font-size: 1.25em;
    font-weight: 700;
  }
  .counts {
    display: flex;
    gap: 8px;
    font-weight: 600;
  }
  .count.err  { color: var(--err); }
  .count.warn { color: var(--warn); }
  .count.ok   { color: var(--ok); }
  .count.unknown { color: var(--text-dim, #8a93a3); }

  .pills {
    display: flex;
    gap: 4px;
    flex-wrap: wrap;
    flex: 1;
  }
  .pill {
    display: inline-flex;
    align-items: center;
    gap: 3px;
    padding: 1px 6px;
    border: 1px solid;
    border-radius: 4px;
    font-size: 0.78em;
    line-height: 1.3;
    cursor: help;
  }
  .pill-icon {
    font-weight: 700;
  }
  .pill-label {
    color: var(--text);
  }
  .hint {
    font-size: 0.75em;
  }
</style>
