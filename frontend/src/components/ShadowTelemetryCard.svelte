<script lang="ts">
  /**
   * F3 — Shadow telemetry card on the main dashboard.
   *
   * Cross-checks nvidia-smi power_draw against an external Shelly
   * power meter (HTTP) and the case ambient temperature against a
   * DS18B20 thermistor (sysfs w1). Shows the delta so the user can
   * spot PSU losses, fan power, and sampling-mismatch gaps that
   * nvidia-smi hides.
   */
  import { onMount, onDestroy } from "svelte";
  import { i18n } from "../lib/i18n/index.svelte";

  type Source<T> = ({ available: true } & T) | {
    available: false;
    reason?: string;
  };
  type Shelly = Source<{
    url: string;
    switch_id: number;
    power_w: number | null;
    voltage_v: number | null;
    current_a: number | null;
    device_temp_c: number | null;
  }>;
  type W1 = Source<{ path: string; temp_c: number }>;
  type Delta = {
    wall_w: number;
    gpu_total_w: number;
    non_gpu_w: number;
    non_gpu_pct: number | null;
  };
  type Sample = {
    available: boolean;
    shelly: Shelly;
    w1: W1;
    nvml: { gpu_total_power_w: number | null };
    delta: Delta | null;
  };

  let sample = $state<Sample | null>(null);
  let timer: number | null = null;

  async function refresh() {
    try {
      const r = await fetch("/api/shadow-telemetry").then((x) => x.json());
      sample = r;
    } catch {
      // keep previous
    }
  }

  onMount(() => {
    refresh();
    timer = window.setInterval(refresh, 5000);
  });
  onDestroy(() => {
    if (timer !== null) clearInterval(timer);
  });

  function fmtW(v: number | null | undefined): string {
    if (v == null) return "—";
    return v.toFixed(1) + " W";
  }
  function fmtC(v: number | null | undefined): string {
    if (v == null) return "—";
    return v.toFixed(1) + "°C";
  }
  function fmtPct(v: number | null | undefined): string {
    if (v == null) return "—";
    return (v >= 0 ? "+" : "") + v.toFixed(1) + "%";
  }

  // Show the card always so users discover the feature, but
  // collapse to a "Setup" CTA when nothing is configured.
  const empty = $derived(
    !sample?.shelly?.available && !sample?.w1?.available,
  );
</script>

<div class="card shadow-card">
  <h2 title={i18n.t("shadow.tooltip") ??
    "Cross-check nvidia-smi (échantillonné) avec un wattmètre Shelly et une sonde DS18B20. Repère les pertes PSU, la conso fans, et les pics manqués par NVML."}>
    🔭 {i18n.t("card.shadow") ?? "Shadow"}
  </h2>

  {#if empty}
    <div class="sub muted small" style="text-align:center;padding:.4em 0">
      {i18n.t("shadow.empty") ??
        "Aucune source externe configurée."}
    </div>
    <div class="sub muted small" style="font-size:.78em;line-height:1.4">
      {i18n.t("shadow.empty_help") ??
        "Ajoute SHADOW_SHELLY_URL=http://… et/ou SHADOW_W1_DEVICE=auto dans config.env."}
    </div>
  {:else}
    {#if sample?.shelly?.available}
      <div class="big" class:warn={(sample.delta?.non_gpu_pct ?? 0) > 25}>
        {fmtW(sample.shelly.power_w)}
      </div>
      <div class="sub">
        {i18n.t("shadow.wall") ?? "mur (Shelly)"}
      </div>
      {#if sample.delta}
        <div class="sub muted small" style="margin-top:.4em">
          GPU: {fmtW(sample.delta.gpu_total_w)}
          → {i18n.t("shadow.elsewhere") ?? "ailleurs"}:
          <b>{fmtW(sample.delta.non_gpu_w)}</b>
          ({fmtPct(sample.delta.non_gpu_pct)})
        </div>
      {/if}
      {#if sample.shelly.voltage_v != null}
        <div class="sub muted small">
          {sample.shelly.voltage_v?.toFixed(1)}V
          {#if sample.shelly.current_a != null}
            · {sample.shelly.current_a?.toFixed(2)}A
          {/if}
        </div>
      {/if}
    {/if}
    {#if sample?.w1?.available}
      <div class="sub muted small" style="margin-top:.5em;
                                            padding-top:.4em;
                                            border-top:1px solid var(--border)">
        🌡 {i18n.t("shadow.ambient") ?? "ambient"}:
        <b>{fmtC(sample.w1.temp_c)}</b>
      </div>
    {/if}
    {#if sample?.shelly && !sample.shelly.available && sample.shelly.reason}
      <div class="sub muted small" style="color:var(--err);
                                            font-size:.75em">
        ⚠ Shelly: {sample.shelly.reason}
      </div>
    {/if}
  {/if}
</div>

<style>
  .shadow-card h2 { cursor: help; }
</style>
