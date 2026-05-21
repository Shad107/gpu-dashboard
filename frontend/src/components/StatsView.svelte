<script lang="ts">
  // Top-level Stats view — multi-section perf overview with sparklines.
  // User feedback 23:35-23:37 : real perf metrics + curves, not static tiles.
  import { onDestroy } from "svelte";
  import { view } from "../lib/view.svelte";
  import { live } from "../lib/stores.svelte";
  import { i18n } from "../lib/i18n/index.svelte";
  import { colorFan } from "../lib/charts";
  import { api } from "../lib/api";
  import Sparkline from "./Sparkline.svelte";

  let llmPerf      = $state<Awaited<ReturnType<typeof api.llmPerf>> | null>(null);
  let thermalStats = $state<Awaited<ReturnType<typeof api.thermalStats>> | null>(null);
  let powerStats   = $state<Awaited<ReturnType<typeof api.powerStats>> | null>(null);
  let profileTime  = $state<Awaited<ReturnType<typeof api.profileStats>> | null>(null);
  let heatmapData  = $state<Awaited<ReturnType<typeof api.powerHeatmap>> | null>(null);
  let heatmapDays  = $state(7);
  let timer: ReturnType<typeof setInterval> | null = null;

  async function loadAll() {
    try { llmPerf      = await api.llmPerf();              } catch {}
    try { thermalStats = await api.thermalStats();         } catch {}
    try { powerStats   = await api.powerStats();           } catch {}
    try { profileTime  = await api.profileStats(86400);    } catch {}
    try { heatmapData  = await api.powerHeatmap(heatmapDays); } catch {}
  }
  // Re-fetch heatmap when window changes
  $effect(() => {
    heatmapDays;
    if (view.current === "stats" && heatmapData) {
      api.powerHeatmap(heatmapDays).then(d => heatmapData = d).catch(() => {});
    }
  });

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

  const isActive = $derived(view.current === "stats");

  $effect(() => {
    if (timer) { clearInterval(timer); timer = null; }
    if (isActive) {
      loadAll();
      timer = setInterval(loadAll, 30_000);
    }
    return () => { if (timer) clearInterval(timer); };
  });
  onDestroy(() => { if (timer) clearInterval(timer); });

  const distEntries = $derived.by(() => {
    const d = live.data?.fan_dist ?? {};
    const total = Object.values(d).reduce((a, b) => a + b, 0) || 1;
    return Object.keys(d).sort((a, b) => +a - +b).map(k => ({
      k, n: d[k], pct: (d[k] / total) * 100,
    }));
  });

  function fmtBig(n: number): string {
    if (n >= 1_000_000) return (n / 1_000_000).toFixed(2) + "M";
    if (n >= 1_000) return (n / 1_000).toFixed(1) + "k";
    return String(n);
  }
  function fmtDuration(seconds: number): string {
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    if (h > 0) return `${h}h ${String(m).padStart(2, "0")}m`;
    return `${m}m`;
  }
  function fmtRelative(ts: number): string {
    if (!ts) return "—";
    const dt = Math.floor(Date.now() / 1000) - ts;
    if (dt < 60) return `${dt}s ago`;
    if (dt < 3600) return `${Math.floor(dt / 60)}m ago`;
    if (dt < 86400) return `${Math.floor(dt / 3600)}h ago`;
    return `${Math.floor(dt / 86400)}d ago`;
  }
  const PROFILE_EMOJI: Record<string, string> = { silent: "🤫", sweet: "⭐", boost: "🚀" };
  const profSum = $derived(
    profileTime
      ? Object.values(profileTime.totals).reduce((a, b) => a + b, 0)
      : 0
  );
</script>

<div class="view-stats-wrap">
  <h2 class="view-title">
    <svg class="icon" viewBox="0 0 24 24" fill="currentColor"><path d="M22 21H2V3h2v16h2v-9h4v9h2V6h4v13h2v-7h4v9z"/></svg>
    {i18n.t("nav.stats")}
  </h2>

  <!-- 🪙 Performance LLM -->
  {#if llmPerf?.available}
    <div class="stats-card">
      <div class="stats-card-head">
        <h3>🪙 {i18n.t("stats.section_llm") ?? "Performance LLM"}</h3>
      </div>
      <div class="stats-row">
        <div class="stats-headline">
          <div class="big" style="color:#f472b6">
            {((llmPerf.avg_tps_5m ?? 0) > 0 ? llmPerf.avg_tps_5m! : (llmPerf.avg_tps_1m ?? 0)).toFixed(1)}
          </div>
          <div class="unit">tok/s · 5m</div>
        </div>
        {#if llmPerf.series_1h && llmPerf.series_1h.length > 0}
          <Sparkline values={llmPerf.series_1h} color="#f472b6" width={420} height={56} />
        {/if}
      </div>
      <div class="stats-kv">
        <div><b>{(llmPerf.avg_tps_1m ?? 0).toFixed(1)}</b><span>1min</span></div>
        <div><b>{(llmPerf.avg_tps_1h ?? 0).toFixed(1)}</b><span>1h avg</span></div>
        <div><b>{(llmPerf.avg_tps_24h ?? 0).toFixed(1)}</b><span>24h avg</span></div>
        <div><b style="color:#fbbf24">{(llmPerf.peak_tps ?? 0).toFixed(1)}</b><span>peak {fmtRelative(llmPerf.peak_ts ?? 0)}</span></div>
      </div>
    </div>
  {/if}

  <!-- ⚡ Power & cost -->
  {#if powerStats}
    {@const sym = powerStats.currency === "EUR" ? "€" : powerStats.currency === "USD" ? "$" : powerStats.currency}
    <div class="stats-card">
      <div class="stats-card-head">
        <h3>⚡ {i18n.t("stats.section_power") ?? "Power & cost"}</h3>
      </div>
      <div class="stats-row">
        <div class="stats-headline">
          <div class="big" style="color:#22d3ee">{powerStats.avg_watts_24h.toFixed(0)}</div>
          <div class="unit">W avg · 24h</div>
        </div>
        {#if powerStats.series_24h && powerStats.series_24h.length > 0}
          <Sparkline values={powerStats.series_24h} color="#22d3ee" width={420} height={56} />
        {/if}
      </div>
      <div class="stats-kv">
        <div><b style="color:#fbbf24">{powerStats.peak_watts_24h.toFixed(0)} W</b><span>peak {fmtRelative(powerStats.peak_ts)}</span></div>
        <div><b>{powerStats.kwh_today.toFixed(2)} kWh</b><span>today</span></div>
        <div><b style="color:#a3e635">{powerStats.cost_today.toFixed(2)} {sym}</b><span>cost today</span></div>
      </div>
    </div>
  {/if}

  <!-- 🌡️ Thermal -->
  {#if thermalStats}
    <div class="stats-card">
      <div class="stats-card-head">
        <h3>🌡️ {i18n.t("stats.section_thermal") ?? "Thermal"}</h3>
      </div>
      <div class="stats-row">
        <div class="stats-headline">
          <div class="big" style="color:#fbbf24">{thermalStats.avg_temp_24h.toFixed(0)}°</div>
          <div class="unit">avg · 24h</div>
        </div>
        {#if thermalStats.series_24h && thermalStats.series_24h.length > 0}
          <Sparkline values={thermalStats.series_24h} color="#fbbf24" width={420} height={56} />
        {/if}
      </div>
      <div class="stats-kv">
        <div><b style="color:#f87171">{thermalStats.peak_temp_24h}°C</b><span>peak 24h</span></div>
        <div><b>{fmtDuration(thermalStats.time_above_80c_seconds)}</b><span>over 80°C · 7d</span></div>
        <div><b>{thermalStats.samples_count}</b><span>samples</span></div>
      </div>
    </div>
  {/if}

  <!-- 🎯 Profiles -->
  {#if profileTime && profSum > 0}
    <div class="stats-card">
      <div class="stats-card-head">
        <h3>🎯 {i18n.t("stats.section_profiles") ?? "Profile time (24h)"}</h3>
      </div>
      {#each ["boost", "sweet", "silent"] as p}
        {#if profileTime.totals[p]}
          {@const pct = ((profileTime.totals[p] / profSum) * 100).toFixed(1)}
          <div class="profile-row">
            <span class="profile-name">{PROFILE_EMOJI[p]} {p}</span>
            <span class="profile-time"><b>{fmtDuration(profileTime.totals[p])}</b></span>
            <div class="profile-bar"><div style="width:{pct}%"></div></div>
            <span class="profile-pct">{pct}%</span>
          </div>
        {/if}
      {/each}
    </div>
  {/if}

  <!-- 🌀 Fan target distribution (kept, smaller) -->
  {#if distEntries.length > 0}
    <div class="stats-card">
      <div class="stats-card-head">
        <h3>🌀 {i18n.t("stats.section_fans") ?? "Fan target distribution"}</h3>
      </div>
      <table class="fan-dist-table">
        <tbody>
          {#each distEntries as r}
            <tr>
              <td>{r.k}%</td>
              <td>
                {r.n} <span class="sub">({r.pct.toFixed(1)}%)</span>
                <div class="fan-bar"><div style:width="{r.pct.toFixed(1)}%" style:background={colorFan(+r.k)}></div></div>
              </td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  {/if}

  <!-- 🗺️ Power cost heatmap (migrated from HistoryView in cycle 76) -->
  {#if heatmapData}
    {@const sym = heatmapData.currency === "EUR" ? "€" : heatmapData.currency === "USD" ? "$" : heatmapData.currency}
    <div class="stats-card">
      <div class="stats-card-head">
        <h3>🗺️ {i18n.t("heatmap.title")}</h3>
      </div>
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
    </div>
  {/if}
</div>

<style>
  .view-stats-wrap { padding: 0.4em 0 2em; }
  .view-title {
    display: flex;
    align-items: center;
    gap: 0.5em;
    color: #cdd2da;
    margin: 0 0 1em;
    font-size: 1.1em;
    font-weight: 600;
  }
  .view-title :global(.icon) { width: 22px; height: 22px; color: #4ade80; }

  .stats-card {
    background: #0e1014;
    border: 1px solid #22262e;
    border-radius: 8px;
    padding: 1em 1.2em;
    margin-bottom: 1em;
  }
  .stats-card-head h3 {
    margin: 0 0 0.6em;
    color: #cdd2da;
    font-size: 0.95em;
    font-weight: 600;
  }
  .stats-row {
    display: flex;
    align-items: center;
    gap: 1.4em;
    flex-wrap: wrap;
    margin-bottom: 0.6em;
  }
  .stats-headline {
    display: flex;
    flex-direction: column;
    min-width: 120px;
  }
  .stats-headline .big {
    font-size: 2.2em;
    font-weight: 700;
    line-height: 1;
    font-variant-numeric: tabular-nums;
  }
  .stats-headline .unit { color: #7c8aa3; font-size: 0.78em; margin-top: 0.2em; }
  .stats-kv {
    display: flex;
    gap: 1.5em;
    flex-wrap: wrap;
    padding-top: 0.6em;
    border-top: 1px solid #22262e;
    font-size: 0.85em;
  }
  .stats-kv > div {
    display: flex;
    flex-direction: column;
    gap: 0.1em;
  }
  .stats-kv b { font-variant-numeric: tabular-nums; }
  .stats-kv span { color: #7c8aa3; font-size: 0.78em; }

  .profile-row {
    display: grid;
    grid-template-columns: 110px 70px 1fr 50px;
    align-items: center;
    gap: 0.6em;
    margin-bottom: 0.5em;
    font-size: 0.88em;
  }
  .profile-name { color: #cdd2da; }
  .profile-bar { height: 6px; background: #1a1d24; border-radius: 3px; overflow: hidden; }
  .profile-bar > div { height: 100%; background: #4ade80; border-radius: 3px; }
  .profile-pct { text-align: right; color: #7c8aa3; font-size: 0.82em; }

  .fan-dist-table { width: 100%; max-width: 560px; }
  .fan-dist-table td { padding: 0.3em 0.5em; vertical-align: middle; }
  .fan-dist-table td:first-child { color: #7c8aa3; width: 60px; text-align: right; font-variant-numeric: tabular-nums; }
  .fan-bar { height: 5px; background: #14171f; border-radius: 3px; margin-top: 0.25em; overflow: hidden; }
  .fan-bar > div { height: 100%; border-radius: 3px; }

  @media (max-width: 600px) {
    .stats-row { gap: 0.8em; }
    .stats-headline .big { font-size: 1.7em; }
    .profile-row { grid-template-columns: 1fr 60px 50px; }
    .profile-bar { grid-column: 1 / -1; }
  }
</style>
