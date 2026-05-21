<script lang="ts">
  import { live } from "../lib/stores.svelte";
  import { i18n } from "../lib/i18n/index.svelte";
  import { renderCoolingChart } from "../lib/charts";

  const html = $derived(renderCoolingChart(live.data?.metrics ?? [], i18n.t("chart.sampling")));
  const info = $derived.by(() => {
    const series = live.data?.metrics ?? [];
    if (!series.length) return i18n.t("chart.buffer_filling");
    const fans = series.map(s => s.fan);
    const temps = series.map(s => s.temp);
    const pwrs = series.map(s => s.power || 0);
    return `fan ${Math.min(...fans)}-${Math.max(...fans)}% · temp ${Math.min(...temps)}-${Math.max(...temps)}°C · power ${Math.min(...pwrs).toFixed(0)}-${Math.max(...pwrs).toFixed(0)} W · ${series.length} ${i18n.t("chart.info_pts")} ${series[0].ts}`;
  });
</script>

<div class="chart-row chart-cool">
  <div class="card">
    <h2>{i18n.t("chart.cooling")}</h2>
    <div class="hist">{@html html}</div>
    <div class="sub">{info}</div>
  </div>
</div>
