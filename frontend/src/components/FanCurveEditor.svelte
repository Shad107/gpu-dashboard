<script lang="ts">
  // Fan curve visualization (slice 1/8 of the editor — read-only for now).
  // Slices 2-4 will add drag-to-edit + persistence.
  import { onMount, onDestroy } from "svelte";
  import { i18n } from "../lib/i18n/index.svelte";

  type CurvePoint = [number, number]; // [temp_°C, fan_%]
  type FanCurveData = {
    enabled: boolean;
    running: boolean;
    curve: CurvePoint[];
    current_target_pct: number | null;
  };

  let data = $state<FanCurveData | null>(null);
  let loading = $state(false);
  let error = $state<string>("");

  async function load() {
    loading = true;
    error = "";
    try {
      const r = await fetch("/api/fan-curve");
      data = await r.json();
    } catch (e: any) {
      error = e?.message ?? String(e);
    } finally {
      loading = false;
    }
  }

  let timer: ReturnType<typeof setInterval> | null = null;
  onMount(() => {
    load();
    timer = setInterval(load, 5000); // refresh every 5s to see live target_pct
  });
  onDestroy(() => { if (timer) clearInterval(timer); });

  // SVG geometry
  const W = 460;
  const H = 240;
  const PAD_L = 40;
  const PAD_R = 12;
  const PAD_T = 10;
  const PAD_B = 30;
  const innerW = W - PAD_L - PAD_R;
  const innerH = H - PAD_T - PAD_B;

  // temp 0-100°C → x, fan 0-100% → y (inverted)
  function xOf(temp: number): number { return PAD_L + (temp / 100) * innerW; }
  function yOf(fan: number):  number { return PAD_T + (1 - fan / 100) * innerH; }

  const curve = $derived<CurvePoint[]>(data?.curve ?? []);
  const path = $derived(
    curve.length >= 2
      ? curve.map((p, i) => `${i === 0 ? "M" : "L"}${xOf(p[0])},${yOf(p[1])}`).join(" ")
      : ""
  );
  const targetY = $derived(
    data?.current_target_pct != null ? yOf(data.current_target_pct) : null
  );
</script>

<div class="fancurve">
  <h3>🌀 {i18n.t("fancurve.title")}</h3>
  <p class="sub" style="margin:0 0 .8em;font-size:.82em">{i18n.t("fancurve.description")}</p>

  {#if loading && !data}
    <p class="sub">{i18n.t("fancurve.loading")}</p>
  {:else if error}
    <p class="sub" style="color:var(--accent-bad)">{error}</p>
  {:else if data}
    <div class="status-row">
      <span class:on={data.enabled} class:off={!data.enabled}>
        {data.enabled ? "✓" : "·"} {i18n.t("fancurve.module")}
      </span>
      <span class:on={data.running} class:off={!data.running}>
        {data.running ? "✓" : "·"} {i18n.t("fancurve.daemon")}
      </span>
      {#if data.current_target_pct != null}
        <span style="color:var(--accent-cool)">
          🌀 {i18n.t("fancurve.current_target")}: <b>{data.current_target_pct}%</b>
        </span>
      {/if}
    </div>

    <svg viewBox="0 0 {W} {H}" class="curve-svg" preserveAspectRatio="xMidYMid meet">
      <!-- Grid : vertical (every 10°C) + horizontal (every 20%) -->
      {#each [0,20,40,60,80,100] as t}
        <line x1={xOf(t)} x2={xOf(t)} y1={PAD_T} y2={PAD_T + innerH}
          stroke="var(--border-subtle)" stroke-width="0.5" />
        <text x={xOf(t)} y={H - 10} text-anchor="middle"
          fill="var(--text-faint)" font-size="11">{t}°</text>
      {/each}
      {#each [0,25,50,75,100] as f}
        <line x1={PAD_L} x2={PAD_L + innerW} y1={yOf(f)} y2={yOf(f)}
          stroke="var(--border-subtle)" stroke-width="0.5" />
        <text x={PAD_L - 6} y={yOf(f) + 4} text-anchor="end"
          fill="var(--text-faint)" font-size="11">{f}%</text>
      {/each}

      <!-- Current target horizontal line -->
      {#if targetY != null}
        <line x1={PAD_L} x2={PAD_L + innerW} y1={targetY} y2={targetY}
          stroke="var(--accent-cool)" stroke-width="1" stroke-dasharray="4 3" opacity="0.5" />
      {/if}

      <!-- The curve itself -->
      {#if path}
        <path d={path} fill="none" stroke="var(--accent)" stroke-width="2"
              stroke-linecap="round" stroke-linejoin="round" />
      {/if}

      <!-- Points -->
      {#each curve as p}
        <circle cx={xOf(p[0])} cy={yOf(p[1])} r="5" fill="var(--accent)" stroke="var(--bg-card)" stroke-width="2">
          <title>{p[0]}°C → {p[1]}%</title>
        </circle>
      {/each}
    </svg>

    <p class="sub" style="font-size:.78em;margin-top:.4em">
      {i18n.t("fancurve.readonly_hint")}
    </p>
  {/if}
</div>

<style>
  .fancurve { padding: 0.4em 0 0; }
  .fancurve h3 { color: var(--text-muted); margin: 0 0 .4em; font-size: 0.95em; font-weight: 600; }
  .status-row {
    display: flex;
    gap: 1em;
    flex-wrap: wrap;
    margin-bottom: 0.6em;
    font-size: 0.82em;
  }
  .status-row .on { color: var(--accent); }
  .status-row .off { color: var(--text-dim); }
  .curve-svg {
    width: 100%;
    max-width: 460px;
    height: auto;
    background: var(--bg-input);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 4px;
  }
</style>
