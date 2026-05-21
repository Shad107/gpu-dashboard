<script lang="ts">
  // Compact SVG sparkline — used in Stats page and LLM card.
  // Renders a tiny line chart (no axes, no labels) sized to fit inline.
  type Props = {
    values: number[];
    color?: string;
    height?: number;
    width?: number;
    /** Optional fill below the curve (semi-transparent) */
    fill?: boolean;
  };
  const {
    values,
    color = "#4ade80",
    height = 24,
    width = 100,
    fill = true,
  }: Props = $props();

  const min = $derived(values.length ? Math.min(...values) : 0);
  const max = $derived(values.length ? Math.max(...values) : 1);
  const span = $derived(Math.max(0.001, max - min));

  function buildPath(): string {
    if (values.length < 2) return "";
    const stepX = width / (values.length - 1);
    return values
      .map((v, i) => {
        const x = i * stepX;
        const y = height - ((v - min) / span) * height;
        return `${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`;
      })
      .join(" ");
  }

  const linePath = $derived(buildPath());
  const fillPath = $derived(
    linePath ? `${linePath} L${width.toFixed(1)},${height} L0,${height} Z` : ""
  );

  const lastX = $derived(width);
  const lastY = $derived(
    values.length
      ? height - ((values[values.length - 1] - min) / span) * height
      : height / 2
  );
</script>

{#if values.length === 0}
  <span class="sparkline-empty">—</span>
{:else}
  <svg class="sparkline" viewBox="0 0 {width} {height}" style:width="{width}px" style:height="{height}px" preserveAspectRatio="none">
    {#if fill && fillPath}
      <path d={fillPath} fill={color} opacity="0.15" />
    {/if}
    {#if linePath}
      <path d={linePath} fill="none" stroke={color} stroke-width="1.4" stroke-linejoin="round" stroke-linecap="round" vector-effect="non-scaling-stroke" />
      <circle cx={lastX} cy={lastY} r="1.8" fill={color} />
    {/if}
  </svg>
{/if}

<style>
  .sparkline {
    display: inline-block;
    vertical-align: middle;
  }
  .sparkline-empty {
    color: #5a606c;
    font-size: 0.78em;
  }
</style>
