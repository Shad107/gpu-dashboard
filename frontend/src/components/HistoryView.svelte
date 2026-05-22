<script lang="ts">
  // Top-level History view — extracted from SettingsModal at cycle 70.
  // User feedback 2026-05-21 23:14 : viewing pages belong at top-level,
  // not inside the Settings modal.
  import { onDestroy } from "svelte";
  import { view } from "../lib/view.svelte";
  import { gpu } from "../lib/gpu.svelte";
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
  let showAnomalyBand = $state(false);
  function compareLabelFor(offset: number): string {
    if (offset === 86400) return i18n.t("history.compare_label_24h");
    if (offset === 604800) return i18n.t("history.compare_label_7d");
    if (offset === 2592000) return i18n.t("history.compare_label_30d");
    return "";
  }

  // Heatmap moved to StatsView in cycle 76 (more thematic fit).

  // Simple mode : detect whether LLM features are available, to filter
  // Tokens/s + Tokens/W out of the metric dropdown otherwise.
  let llmAvailable = $state(false);
  (async () => {
    try {
      const r = await api.llmStats();
      llmAvailable = r.available === true;
    } catch { /* assume no LLM */ }
  })();

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
        api.history(from, now, step, gpu.selected),
        api.events(from).catch(() => ({ ok: false, events: [] })),
      ];
      if (offset > 0) {
        promises.push(
          api.history(from - offset, now - offset, step, gpu.selected).catch(() => ({ ok: false, samples: [] })),
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
        {#if llmAvailable}
          <option value="tokens_per_sec">{i18n.t("history.metric_tps")}</option>
          <option value="tokens_per_watt">{i18n.t("history.metric_tpw")}</option>
        {/if}
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
        showAnomalyBand={showAnomalyBand}
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
    <label style="display:flex;align-items:center;gap:.4em;cursor:pointer;font-size:.85em">
      <input type="checkbox" bind:checked={showAnomalyBand} />
      📈 {i18n.t("history.anomaly_band") ?? "Anomaly band"}
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
