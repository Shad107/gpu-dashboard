<script lang="ts">
  // Top-level History view — extracted from SettingsModal at cycle 70.
  // User feedback 2026-05-21 23:14 : viewing pages belong at top-level,
  // not inside the Settings modal.
  import { onDestroy } from "svelte";
  import { view } from "../lib/view.svelte";
  import { toast } from "../lib/stores.svelte";
  import { i18n } from "../lib/i18n/index.svelte";
  import { api, type HistorySample, type StoredEvent } from "../lib/api";
  import HistoryChart from "./HistoryChart.svelte";

  type HistoryRange = "1h" | "6h" | "24h" | "7d" | "30d";
  type HistoryMetric = "power" | "temp" | "fan_pct" | "util_gpu" | "tokens_per_sec" | "tokens_per_watt";

  let historyRange = $state<HistoryRange>("24h");
  let historyMetric = $state<HistoryMetric>("power");
  let historySamples = $state<HistorySample[]>([]);
  let historyEvents = $state<StoredEvent[]>([]);
  let historyCompare = $state<HistorySample[]>([]);
  let historyCompareOffset = $state(0);
  const historyCompareMode = $derived(historyCompareOffset > 0);
  function compareLabelFor(offset: number): string {
    if (offset === 86400) return i18n.t("history.compare_label_24h");
    if (offset === 604800) return i18n.t("history.compare_label_7d");
    if (offset === 2592000) return i18n.t("history.compare_label_30d");
    return "";
  }

  let heatmapData = $state<Awaited<ReturnType<typeof api.powerHeatmap>> | null>(null);
  let heatmapDays = $state(7);
  async function loadHeatmap() {
    try { heatmapData = await api.powerHeatmap(heatmapDays); } catch { heatmapData = null; }
  }
  const heatmapMaxCost = $derived(
    heatmapData ? Math.max(0.001, ...heatmapData.hours.map(h => h.cost_per_hour)) : 1
  );
  function heatmapBg(cost: number): string {
    const ratio = cost / heatmapMaxCost;
    if (ratio < 0.05) return "#0e1014";
    if (ratio < 0.25) return `rgba(96,165,250,${0.15 + ratio * 0.5})`;
    if (ratio < 0.6)  return `rgba(251,191,36,${0.2 + ratio * 0.5})`;
    return `rgba(251,146,60,${0.3 + ratio * 0.7})`;
  }

  let historyLoading = $state(false);
  let historyAutoRefresh = $state(false);
  let historyTimer: ReturnType<typeof setInterval> | null = null;

  const RANGE_SECONDS: Record<HistoryRange, number> = {
    "1h": 3600, "6h": 21600, "24h": 86400, "7d": 7 * 86400, "30d": 30 * 86400,
  };
  const RANGE_STEP: Record<HistoryRange, number> = {
    "1h": 0, "6h": 0, "24h": 60, "7d": 600, "30d": 1800,
  };
  const METRIC_INFO: Record<HistoryMetric, { color: string; unit: string }> = {
    "power":           { color: "#22d3ee", unit: "W" },
    "temp":            { color: "#fbbf24", unit: "°C" },
    "fan_pct":         { color: "#4ade80", unit: "%" },
    "util_gpu":        { color: "#a855f7", unit: "%" },
    "tokens_per_sec":  { color: "#f472b6", unit: "/s" },
    "tokens_per_watt": { color: "#f59e0b", unit: "/W" },
  };

  function computeDerivedSamples(raw: HistorySample[], metric: HistoryMetric): HistorySample[] {
    if (metric !== "tokens_per_sec" && metric !== "tokens_per_watt") return raw;
    if (raw.length < 2) return [];
    const out: HistorySample[] = [];
    for (let i = 1; i < raw.length; i++) {
      const prev = raw[i - 1];
      const cur = raw[i];
      const dt = cur.ts - prev.ts;
      const t0 = prev.tokens_total_snapshot;
      const t1 = cur.tokens_total_snapshot;
      let value: number | null = null;
      if (dt > 0 && t0 != null && t1 != null && t1 >= t0) {
        const tps = (t1 - t0) / dt;
        if (metric === "tokens_per_sec") value = tps;
        else if (cur.power && cur.power > 0) value = tps / cur.power;
      }
      out.push({ ...cur, [metric]: value } as HistorySample);
    }
    return out;
  }
  const derivedSamples = $derived(computeDerivedSamples(historySamples, historyMetric));

  async function loadHistory() {
    historyLoading = true;
    try {
      const now = Math.floor(Date.now() / 1000);
      const from = now - RANGE_SECONDS[historyRange];
      const step = RANGE_STEP[historyRange] || undefined;
      const offset = historyCompareOffset;
      const promises: Promise<any>[] = [
        api.history(from, now, step),
        api.events(from).catch(() => ({ ok: false, events: [] })),
      ];
      if (offset > 0) {
        promises.push(
          api.history(from - offset, now - offset, step).catch(() => ({ ok: false, samples: [] })),
        );
      }
      const results = await Promise.all(promises);
      historySamples = results[0].samples ?? [];
      historyEvents = results[1].events ?? [];
      historyCompare = offset > 0 ? (results[2].samples ?? []) : [];
    } catch (e) {
      toast.emit("✗ " + i18n.t("ts.network_error") + ": " + (e as Error).message, "err");
      historySamples = []; historyEvents = []; historyCompare = [];
    } finally {
      historyLoading = false;
    }
  }

  function exportCsv() {
    const now = Math.floor(Date.now() / 1000);
    const since = now - RANGE_SECONDS[historyRange];
    window.location.href = api.exportCsvUrl(since);
  }

  const isActive = $derived(view.current === "history");

  // Initial load when this view becomes active
  $effect(() => {
    if (isActive && historySamples.length === 0) loadHistory();
  });
  // Reload on range/offset change while active
  $effect(() => {
    historyRange; historyCompareOffset;
    if (isActive && historySamples.length > 0) loadHistory();
  });
  // Heatmap on demand
  $effect(() => {
    if (isActive && !heatmapData) loadHeatmap();
  });
  $effect(() => {
    heatmapDays;
    if (isActive && heatmapData) loadHeatmap();
  });
  // Auto-refresh timer
  $effect(() => {
    if (historyTimer) { clearInterval(historyTimer); historyTimer = null; }
    if (historyAutoRefresh && isActive) {
      historyTimer = setInterval(() => loadHistory(), 30_000);
    }
    return () => { if (historyTimer) clearInterval(historyTimer); };
  });

  onDestroy(() => {
    if (historyTimer) clearInterval(historyTimer);
  });
</script>

<div class="view-history">
  <h2 class="view-title">
    <svg class="icon" viewBox="0 0 24 24" fill="currentColor"><path d="M13 3a9 9 0 0 0-9 9H1l4 4 4-4H6a7 7 0 1 1 7 7c-2.94 0-5.49-1.81-6.56-4.4l-1.89.61C5.84 18.45 9.16 21 13 21a9 9 0 0 0 0-18m-1 5v5l4.28 2.54.72-1.21-3.5-2.08V8z"/></svg>
    {i18n.t("history.title")}
  </h2>
  <p class="sub" style="margin:0 0 1em">{i18n.t("history.description")}</p>

  <div class="btn-row" style="margin-bottom:.8em">
    {#each ["1h", "6h", "24h", "7d", "30d"] as r}
      <button
        class="btn"
        class:btn-primary={historyRange === r}
        onclick={() => (historyRange = r as HistoryRange)}
      >{i18n.t(`history.range_${r}` as any)}</button>
    {/each}
  </div>

  <div class="form-row">
    <span class="form-lbl">{i18n.t("history.metric")}</span>
    <span class="form-val">
      <select bind:value={historyMetric} class="al-input" style="max-width:240px">
        <option value="power">{i18n.t("history.metric_power")}</option>
        <option value="temp">{i18n.t("history.metric_temp")}</option>
        <option value="fan_pct">{i18n.t("history.metric_fan")}</option>
        <option value="util_gpu">{i18n.t("history.metric_util")}</option>
        <option value="tokens_per_sec">{i18n.t("history.metric_tps")}</option>
        <option value="tokens_per_watt">{i18n.t("history.metric_tpw")}</option>
      </select>
    </span>
  </div>

  <div style="height:340px;background:#0e1014;border-radius:8px;padding:6px;margin-top:.8em">
    {#if historyLoading}
      <div style="color:#7c8aa3;padding:2em;text-align:center">{i18n.t("history.loading")}</div>
    {:else}
      <HistoryChart
        samples={derivedSamples}
        events={historyEvents}
        metric={historyMetric}
        color={METRIC_INFO[historyMetric].color}
        unit={METRIC_INFO[historyMetric].unit}
        compareSamples={historyCompareMode ? historyCompare : []}
        compareLabel={compareLabelFor(historyCompareOffset)}
      >
        <span slot="empty">{i18n.t("history.no_data")}</span>
      </HistoryChart>
    {/if}
  </div>

  <div class="btn-row" style="margin-top:.8em">
    <button class="btn btn-primary" onclick={loadHistory}>{i18n.t("history.refresh")}</button>
    <button class="btn" onclick={exportCsv}>📥 {i18n.t("history.export_csv")}</button>
    <label style="display:flex;align-items:center;gap:.4em;cursor:pointer;font-size:.85em">
      <input type="checkbox" bind:checked={historyAutoRefresh} />
      ⏱️ {i18n.t("history.auto_refresh")}
    </label>
    <label style="display:flex;align-items:center;gap:.4em;font-size:.85em">
      📊 {i18n.t("history.compare_to")}
      <select bind:value={historyCompareOffset} class="al-input" style="max-width:160px;font-size:.95em">
        <option value={0}>{i18n.t("history.compare_off")}</option>
        <option value={86400}>{i18n.t("history.compare_label_24h")}</option>
        <option value={604800}>{i18n.t("history.compare_label_7d")}</option>
        <option value={2592000}>{i18n.t("history.compare_label_30d")}</option>
      </select>
    </label>
    <span class="warn-text">{i18n.t("history.samples_count", { n: historySamples.length })}</span>
  </div>

  {#if heatmapData}
    {@const sym = heatmapData.currency === "EUR" ? "€" : heatmapData.currency === "USD" ? "$" : heatmapData.currency}
    <h3 style="margin-top:1.6em;color:#cdd2da;font-size:.95em;font-weight:600">
      ⏰ {i18n.t("heatmap.title")}
    </h3>
    <p class="sub" style="margin:0 0 .6em;font-size:.82em">
      {i18n.t("heatmap.description", { days: heatmapData.days })}
    </p>
    <div class="form-row" style="margin-bottom:.6em">
      <span class="form-lbl">{i18n.t("heatmap.days_label")}</span>
      <span class="form-val">
        <select bind:value={heatmapDays} class="al-input" style="max-width:120px">
          <option value={1}>1 day</option>
          <option value={7}>7 days</option>
          <option value={14}>14 days</option>
          <option value={30}>30 days</option>
        </select>
      </span>
    </div>
    {#if heatmapData.hours.every(h => h.sample_count === 0)}
      <p class="sub">{i18n.t("heatmap.no_data")}</p>
    {:else}
      <div class="heatmap-grid">
        {#each heatmapData.hours as cell}
          <div class="heatmap-cell" style:background={heatmapBg(cell.cost_per_hour)}
               title="{cell.hour}:00 — {cell.avg_watts}W · {cell.cost_per_hour.toFixed(3)}{sym}/h · {cell.sample_count} samples">
            <div class="heatmap-hour">{cell.hour}h</div>
            <div class="heatmap-watts">{cell.avg_watts.toFixed(0)}W</div>
            <div class="heatmap-cost">{cell.cost_per_hour.toFixed(2)}{sym}</div>
          </div>
        {/each}
      </div>
    {/if}
  {/if}
</div>

<style>
  .view-history { padding: 0.4em 0 2em; }
  .view-title {
    display: flex;
    align-items: center;
    gap: 0.5em;
    color: #cdd2da;
    margin: 0 0 0.4em;
    font-size: 1.1em;
    font-weight: 600;
  }
  .view-title :global(.icon) { width: 22px; height: 22px; color: #4ade80; }
</style>
