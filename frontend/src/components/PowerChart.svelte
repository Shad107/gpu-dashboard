<script lang="ts">
  import { live } from "../lib/stores.svelte";
  import { i18n } from "../lib/i18n/index.svelte";
  import { renderPowerChart } from "../lib/charts";

  const html = $derived(
    renderPowerChart(
      live.data?.metrics ?? [],
      live.data?.gpu?.alive ? live.data.gpu.power_limit : undefined,
      i18n.t("chart.sampling"),
      i18n.t("chart.cap"),
    )
  );
</script>

<div class="chart-row chart-pow">
  <div class="card">
    <h2>{i18n.t("chart.power")}</h2>
    <div class="hist-power">{@html html}</div>
  </div>
</div>
