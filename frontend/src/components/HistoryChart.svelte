<script lang="ts">
  import { smoothPath, exportSvgAsFile } from "../lib/charts";
  import { i18n } from "../lib/i18n/index.svelte";
  import type { HistorySample, StoredEvent } from "../lib/api";

  type Metric = "power" | "temp" | "fan_pct" | "util_gpu" | "tokens_per_sec" | "tokens_per_watt";
  type Props = {
    samples: HistorySample[];
    metric: Metric;
    color: string;
    unit: string;
    events?: StoredEvent[];
    /** Optional comparison series (rendered as dimmer overlay) — typically same range −24h */
    compareSamples?: HistorySample[];
    compareLabel?: string;
  };
  const { samples, metric, color, unit, events = [], compareSamples = [], compareLabel }: Props = $props();

  let svgEl: SVGSVGElement | undefined = $state();
  function downloadSvg() {
    if (svgEl) {
      const ts = new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);
      exportSvgAsFile(svgEl, `gpu-history-${metric}-${ts}.svg`);
    }
  }

  // Marker color per event kind
  const EVENT_COLORS: Record<string, string> = {
    drop: "#f87171",          // red — OcuLink drop
    recover: "#4ade80",       // green — link recovered
    pl_change: "#22d3ee",     // cyan — power-limit changed
    offset_change: "#a855f7", // purple — clock offset changed
    alert_sent: "#fbbf24",    // amber — telegram alert
  };
  function eventColor(kind: string): string {
    return EVENT_COLORS[kind] ?? "#fbbf24";
  }

  const W = 1200, H = 320, PAD_L = 56, PAD_R = 24, PAD_T = 20, PAD_B = 36;
  const innerW = W - PAD_L - PAD_R;
  const innerH = H - PAD_T - PAD_B;

  // Y-axis spans BOTH series so the overlay isn't squashed
  const allValues = $derived([
    ...samples.map(s => (s[metric] as number) ?? 0),
    ...compareSamples.map(s => (s[metric] as number) ?? 0),
  ]);
  const minV = $derived(allValues.length ? Math.min(...allValues) : 0);
  const maxV = $derived(allValues.length ? Math.max(...allValues) : 1);
  const span = $derived(Math.max(1, maxV - minV));

  function buildPath(arr: HistorySample[]): string {
    if (arr.length < 2) return "";
    const x = (i: number) => PAD_L + (i / (arr.length - 1)) * innerW;
    const y = (v: number) => PAD_T + (1 - (v - minV) / span) * innerH;
    return smoothPath(arr.map((s, i) => ({ x: x(i), y: y((s[metric] as number) ?? 0) })));
  }

  const path = $derived(buildPath(samples));
  const comparePath = $derived(compareSamples.length >= 2 ? buildPath(compareSamples) : "");

  const gridY = $derived.by(() => {
    const steps = 5;
    return Array.from({ length: steps + 1 }, (_, k) => {
      const v = minV + (span * k) / steps;
      const yy = PAD_T + (1 - k / steps) * innerH;
      return { v, yy };
    });
  });

  function fmtTime(ts: number): string {
    const d = new Date(ts * 1000);
    const hh = String(d.getHours()).padStart(2, "0");
    const mm = String(d.getMinutes()).padStart(2, "0");
    return `${hh}:${mm}`;
  }

  function fmtDate(ts: number): string {
    const d = new Date(ts * 1000);
    return `${d.getMonth() + 1}/${d.getDate()} ${fmtTime(ts)}`;
  }

  const nTicks = $derived(Math.min(7, samples.length));
  const xTicks = $derived.by(() => {
    if (samples.length < 2) return [];
    const x = (i: number) => PAD_L + (i / (samples.length - 1)) * innerW;
    const spanSec = samples[samples.length - 1].ts - samples[0].ts;
    const useDate = spanSec > 86400;  // > 24h: show date
    return Array.from({ length: nTicks }, (_, k) => {
      const idx = Math.round((k * (samples.length - 1)) / Math.max(1, nTicks - 1));
      return { x: x(idx), label: useDate ? fmtDate(samples[idx].ts) : fmtTime(samples[idx].ts) };
    });
  });

  // Event markers: map each event to an x position based on its ts within
  // the visible window [samples[0].ts, samples[last].ts]. Out-of-range events
  // are clipped.
  const eventMarkers = $derived.by(() => {
    if (samples.length < 2) return [];
    const tStart = samples[0].ts;
    const tEnd = samples[samples.length - 1].ts;
    const span = Math.max(1, tEnd - tStart);
    return events
      .filter(e => e.ts >= tStart && e.ts <= tEnd)
      .map(e => ({
        x: PAD_L + ((e.ts - tStart) / span) * innerW,
        color: eventColor(e.kind),
        kind: e.kind,
        ts: e.ts,
        label: `${e.kind} @ ${fmtTime(e.ts)}`,
      }));
  });
</script>

{#if samples.length === 0}
  <div style="color:#7c8aa3;padding:2em;text-align:center">
    <slot name="empty" />
  </div>
{:else}
  <div class="chart-wrap">
    <button class="chart-export" title={i18n.t("chart.download_svg") ?? "Download as SVG"} onclick={downloadSvg}>⬇️</button>
  <svg bind:this={svgEl} viewBox="0 0 {W} {H}" preserveAspectRatio="none" style="width:100%;height:100%;display:block">
    {#each gridY as g}
      <line x1={PAD_L} x2={W - PAD_R} y1={g.yy} y2={g.yy} stroke="#22262e" stroke-width="0.5" />
      <text x={PAD_L - 6} y={g.yy + 3.5} fill="#7c8aa3" font-size="10" text-anchor="end">
        {g.v.toFixed(0)}{unit}
      </text>
    {/each}
    {#each xTicks as t}
      <line x1={t.x} x2={t.x} y1={PAD_T + innerH} y2={PAD_T + innerH + 4} stroke="#3a3f4d" stroke-width="0.7" />
      <text x={t.x} y={PAD_T + innerH + 18} fill="#7c8aa3" font-size="10" text-anchor="middle">{t.label}</text>
    {/each}
    <!-- Comparison series (dimmer, dashed) — rendered BEFORE the main path so the latter sits on top -->
    {#if comparePath}
      <path d={comparePath} fill="none" stroke="#7c8aa3" stroke-width="1.4" stroke-dasharray="6 3" stroke-linejoin="round" stroke-linecap="round" vector-effect="non-scaling-stroke" opacity="0.7">
        <title>{compareLabel || "compare"}</title>
      </path>
    {/if}

    <path d={path} fill="none" stroke={color} stroke-width="2" stroke-linejoin="round" stroke-linecap="round" vector-effect="non-scaling-stroke" />

    {#if comparePath && compareLabel}
      <g font-size="11">
        <line x1={PAD_L + 6} x2={PAD_L + 20} y1={PAD_T - 4} y2={PAD_T - 4} stroke="#7c8aa3" stroke-width="1.4" stroke-dasharray="6 3" opacity="0.7"/>
        <text x={PAD_L + 24} y={PAD_T - 1} fill="#7c8aa3">{compareLabel}</text>
      </g>
    {/if}

    <!-- Event markers (vertical lines + small circle on top axis) -->
    {#each eventMarkers as m}
      <line x1={m.x} x2={m.x} y1={PAD_T} y2={PAD_T + innerH}
            stroke={m.color} stroke-width="1.2" stroke-dasharray="3 2" opacity="0.55">
        <title>{m.label}</title>
      </line>
      <circle cx={m.x} cy={PAD_T} r="4" fill={m.color} stroke="#0e1014" stroke-width="1">
        <title>{m.label}</title>
      </circle>
    {/each}
  </svg>
  </div>
{/if}

<style>
  .chart-wrap { position: relative; width: 100%; height: 100%; }
  .chart-export {
    position: absolute;
    top: 0.4em;
    right: 0.4em;
    background: rgba(15, 19, 28, 0.85);
    color: var(--text-muted);
    border: 1px solid var(--border-subtle);
    border-radius: 4px;
    padding: 0.2em 0.5em;
    font-size: 0.95em;
    cursor: pointer;
    z-index: 2;
    transition: background 0.15s, color 0.15s;
  }
  .chart-export:hover {
    background: var(--bg-card);
    color: var(--accent);
  }
</style>
