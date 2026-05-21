<script lang="ts">
  // Top-level Stats view — fan distribution from live sampler buffer.
  // Extracted from SettingsModal at cycle 71 per user feedback 2026-05-21 23:14.
  import { live } from "../lib/stores.svelte";
  import { i18n } from "../lib/i18n/index.svelte";
  import { colorFan } from "../lib/charts";

  const distEntries = $derived.by(() => {
    const d = live.data?.fan_dist ?? {};
    const total = Object.values(d).reduce((a, b) => a + b, 0) || 1;
    return Object.keys(d).sort((a, b) => +a - +b).map(k => ({
      k, n: d[k], pct: (d[k] / total) * 100,
    }));
  });
</script>

<div class="view-stats">
  <h2 class="view-title">
    <svg class="icon" viewBox="0 0 24 24" fill="currentColor"><path d="M22 21H2V3h2v16h2v-9h4v9h2V6h4v13h2v-7h4v9z"/></svg>
    {i18n.t("stats.title")}
  </h2>
  <p class="sub" style="margin:0 0 1em">{i18n.t("stats.description") ?? "Distribution des consignes ventilo dans le buffer en mémoire."}</p>

  {#if distEntries.length === 0}
    <p class="sub">{i18n.t("stats.no_data") ?? "Pas encore de données — laisse le sampler tourner quelques minutes."}</p>
  {:else}
    <table class="stats-table">
      <tbody>
        {#each distEntries as r}
          <tr>
            <td>{r.k}%</td>
            <td>
              {r.n} <span class="sub">({r.pct.toFixed(1)}%)</span>
              <div class="bar-row">
                <div class="bar" style:width="{r.pct.toFixed(1)}%" style:background={colorFan(+r.k)}></div>
              </div>
            </td>
          </tr>
        {/each}
      </tbody>
    </table>
  {/if}
</div>

<style>
  .view-stats { padding: 0.4em 0 2em; }
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
  .stats-table { width: 100%; max-width: 560px; }
  .stats-table td { padding: 0.4em 0.5em; vertical-align: middle; }
  .stats-table td:first-child {
    color: #7c8aa3;
    font-variant-numeric: tabular-nums;
    width: 60px;
    text-align: right;
  }
  .bar-row {
    height: 6px;
    background: #14171f;
    border-radius: 3px;
    margin-top: 0.3em;
    overflow: hidden;
  }
  .bar-row .bar { height: 100%; border-radius: 3px; }
</style>
