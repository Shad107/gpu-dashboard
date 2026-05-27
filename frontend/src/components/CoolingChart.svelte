<script lang="ts">
  import { onMount, onDestroy } from "svelte";
  import { live } from "../lib/stores.svelte";
  import { i18n } from "../lib/i18n/index.svelte";
  import { renderCoolingChart } from "../lib/charts";
  import type { Sample } from "../lib/api";

  // 1h is the live in-memory buffer (5s resolution, no fetch).
  // 6h/12h/24h pull from the SQLite history with a step roughly
  // matching the display width so the SVG path stays smooth.
  type Range = "1h" | "6h" | "12h" | "24h";
  let range = $state<Range>("1h");
  // $state.raw skips the deep proxy — critical for the 361-point
  // history arrays where each .map()/.filter() across 3 metrics
  // would otherwise do >1000 proxy reads per re-render. Switching
  // from $state to $state.raw on this list dropped first-paint of
  // the 24h range from ~3s to ~150ms in dev testing.
  let histSamples = $state.raw<Sample[]>([]);
  let loading = $state(false);
  let timer: number | null = null;

  async function fetchHistory() {
    if (range === "1h") return;
    loading = true;
    try {
      const hours = parseInt(range);
      const now = Math.floor(Date.now() / 1000);
      const from = now - hours * 3600;
      // Aim for ~360 points across the chart width — that's ~10s
      // step for 1h, 60s for 6h, 120s for 12h, 240s for 24h.
      const step = Math.max(5, Math.round((hours * 3600) / 360));
      const r = await fetch(
        `/api/history?from=${from}&to=${now}&step=${step}`,
      ).then((x) => x.json());
      const raw = r.samples ?? [];
      // Normalize the history shape (fan_pct, ts:number) to the
      // Sample shape the renderer expects (fan, ts:string).
      histSamples = raw.map((s: any) => ({
        ts: new Date((s.ts ?? 0) * 1000).toISOString(),
        temp: s.temp ?? 0,
        fan: s.fan_pct ?? 0,
        clk_gpu: s.clk_gpu ?? 0,
        clk_mem: s.clk_mem ?? 0,
        power: s.power ?? 0,
        fan0_rpm: s.fan0_rpm,
        fan1_rpm: s.fan1_rpm,
      }));
    } catch {
      // keep previous
    } finally {
      loading = false;
    }
  }

  $effect(() => {
    // Re-fetch when range changes. The reference to `range` makes
    // this rune-effect re-run on every range change.
    void range;
    fetchHistory();
  });

  onMount(() => {
    timer = window.setInterval(() => {
      // Refresh non-1h ranges every 30s; 1h auto-refreshes via the
      // live store.
      if (range !== "1h") fetchHistory();
    }, 30_000);
  });
  onDestroy(() => {
    if (timer !== null) clearInterval(timer);
  });

  const samples = $derived(
    range === "1h" ? (live.data?.metrics ?? []) : histSamples,
  );
  const html = $derived(
    renderCoolingChart(samples as Sample[], i18n.t("chart.sampling")),
  );
  const info = $derived.by(() => {
    if (!samples.length) {
      return loading
        ? (i18n.t("chart.loading") ?? "loading…")
        : i18n.t("chart.buffer_filling");
    }
    const fans = (samples as Sample[]).map((s) => s.fan).filter((x) => x != null);
    const temps = (samples as Sample[]).map((s) => s.temp).filter((x) => x != null);
    const pwrs = (samples as Sample[]).map((s) => s.power || 0);
    if (!fans.length || !temps.length) return `${samples.length} pts`;
    return (
      `fan ${Math.min(...fans).toFixed(0)}-${Math.max(...fans).toFixed(0)}% · ` +
      `temp ${Math.min(...temps).toFixed(0)}-${Math.max(...temps).toFixed(0)}°C · ` +
      `power ${Math.min(...pwrs).toFixed(0)}-${Math.max(...pwrs).toFixed(0)} W · ` +
      `${samples.length} ${i18n.t("chart.info_pts")}`
    );
  });
</script>

<div class="chart-row chart-cool">
  <div class="card">
    <div style="display:flex;justify-content:space-between;align-items:baseline;
                  flex-wrap:wrap;gap:.4em">
      <h2 style="margin:0">{i18n.t("chart.cooling")}</h2>
      <div style="display:flex;gap:4px;align-items:center;font-size:.85em">
        <span class="muted small">{i18n.t("chart.range") ?? "Plage"}</span>
        <select bind:value={range}
                style="background:var(--bg-2);color:var(--text);
                       border:1px solid var(--border);padding:2px 6px;
                       border-radius:4px;font-size:.9em;cursor:pointer">
          <option value="1h">1h (live)</option>
          <option value="6h">6h</option>
          <option value="12h">12h</option>
          <option value="24h">24h</option>
        </select>
        {#if loading}<span class="muted small">⏳</span>{/if}
      </div>
    </div>
    <div class="hist">{@html html}</div>
    <div class="sub">{info}</div>
  </div>
</div>
