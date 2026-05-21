<script lang="ts">
  import { onMount } from "svelte";
  import { live } from "../lib/stores.svelte";
  import { layout } from "../lib/layout.svelte";
  import { i18n } from "../lib/i18n/index.svelte";
  import { tempColor, perfEstimate } from "../lib/charts";
  import { api } from "../lib/api";

  // Electricity widget — polled every minute (independent from live data)
  let elec = $state<Awaited<ReturnType<typeof api.electricity>> | null>(null);
  let llm = $state<Awaited<ReturnType<typeof api.llmStats>> | null>(null);
  async function loadElec() {
    try { elec = await api.electricity(3600); } catch { /* keep last */ }
  }
  async function loadLlm() {
    try { llm = await api.llmStats(); } catch { /* keep last */ }
  }
  let elecTimer: ReturnType<typeof setInterval> | null = null;
  let llmTimer: ReturnType<typeof setInterval> | null = null;
  onMount(() => {
    loadElec();
    loadLlm();
    elecTimer = setInterval(loadElec, 60_000);
    llmTimer = setInterval(loadLlm, 30_000);
    return () => {
      if (elecTimer) clearInterval(elecTimer);
      if (llmTimer) clearInterval(llmTimer);
    };
  });

  const MDI_FAN = "M12,11A1,1 0 0,0 11,12A1,1 0 0,0 12,13A1,1 0 0,0 13,12A1,1 0 0,0 12,11M12.5,2C17,2 17.11,5.57 14.75,6.75C13.76,7.24 13.32,8.29 13.13,9.22C13.61,9.42 14.03,9.73 14.35,10.13C18.05,8.13 22.03,8.92 22.03,12.5C22.03,17 18.46,17.1 17.28,14.73C16.78,13.74 15.72,13.3 14.79,13.11C14.59,13.59 14.28,14 13.87,14.34C15.87,18.04 15.08,22 11.5,22C7,22 6.91,18.42 9.27,17.24C10.25,16.75 10.69,15.71 10.89,14.79C10.4,14.59 9.97,14.27 9.65,13.87C5.95,15.87 2,15.08 2,11.5C2,7 5.56,6.91 6.74,9.27C7.24,10.25 8.29,10.69 9.22,10.88C9.41,10.4 9.73,9.97 10.14,9.65C8.14,5.96 8.91,2 12.5,2Z";

  const d = $derived(live.data);
  const g = $derived(d?.gpu);
  const alive = $derived(g && g.alive === true);

  const perf = $derived(g && g.alive ? perfEstimate(g.power_limit) : 0);
  const fans = $derived(d?.fans?.length ? d.fans : [{ idx: 0, rpm: 0, pct: 0, target: 0 }]);

  const tuning = $derived(d?.tuning);
  const clocks = $derived(tuning?.clocks ?? {});
  const offsets = $derived(tuning?.offsets ?? {});
  const offGpu = $derived(offsets.GPUGraphicsClockOffsetAllPerformanceLevels ?? offsets.GPUGraphicsClockOffset ?? 0);
  const offMem = $derived(offsets.GPUMemoryTransferRateOffsetAllPerformanceLevels ?? offsets.GPUMemoryTransferRateOffset ?? 0);
  const pctGpu = $derived(clocks.gr_max ? ((clocks.gr_now ?? 0) / clocks.gr_max) * 100 : 0);
  const pctMem = $derived(clocks.mem_max ? ((clocks.mem_now ?? 0) / clocks.mem_max) * 100 : 0);
  const showTuning = $derived(tuning && (tuning.clocks || tuning.offsets));

  function fanColor(pct: number) {
    return pct >= 80 ? "#f87171" : pct >= 60 ? "#fbbf24" : pct >= 40 ? "#a3e635" : pct > 0 ? "#4ade80" : "#7c8aa3";
  }
  function fanDur(rpm: number) {
    return rpm > 0 ? Math.max(0.08, (60 / rpm) * 0.4) : 0;
  }
  function plPerfCol(p: number) {
    return p >= 95 ? "#4ade80" : p >= 85 ? "#a3e635" : p >= 75 ? "#fbbf24" : "#fb923c";
  }
  const sign = (v: number) => (v >= 0 ? "+" : "") + v;
</script>

<div class="row">
  {#if alive && g && g.alive}
    <div class="card">
      <h2>{i18n.t("card.gpu")}</h2>
      <div class="big" style:color={tempColor(g.temp)}>{g.temp}°C</div>
      <div class="sub">{i18n.t("gpu.util")} {g.util_gpu}% · {i18n.t("gpu.draw")} {g.power.toFixed(0)} W</div>
      {#if g.mem_temp !== null && g.mem_temp !== undefined}
        <div class="sub" style="margin-top:.2em" style:color={tempColor(g.mem_temp + 15)}>
          {i18n.t("gpu.mem_temp")} {g.mem_temp}°C
        </div>
      {/if}
    </div>

    <div class="card">
      <h2>{i18n.t("card.power_limit")}</h2>
      <div class="big">
        {g.power_limit.toFixed(0)} <span class="sub" style="font-size:.55em">/ 350 W</span>
      </div>
      <div class="sub">
        ~<span style:color={plPerfCol(perf)}>{perf}%</span>
        {i18n.t("perf.perf_short")} · {i18n.t("perf.stock_pl")}
      </div>
    </div>

    <div class="card">
      <h2>{i18n.t("card.fans")}</h2>
      <div class="fan-visual">
        {#each fans as f}
          <div class="fan-cell">
            <svg
              class="fan-svg {(f.rpm ?? 0) > 0 ? 'spin' : 'off'}"
              style="--fan-dur:{fanDur(f.rpm ?? 0)}s;color:{fanColor(f.pct ?? 0)}"
              viewBox="0 0 24 24"
              fill="currentColor"
            >
              <title>Fan {f.idx} — {f.rpm ?? 0} RPM · {f.pct ?? 0}% (target {f.target ?? 0}%)</title>
              <path d={MDI_FAN} />
            </svg>
            <div class="rpm">{f.rpm ?? 0} RPM</div>
            <div class="pct">
              <b>{f.pct ?? 0}%</b> <span style="color:#5a606c">/ {f.target ?? 0}%</span>
            </div>
          </div>
        {/each}
      </div>
    </div>

    <div class="card">
      <h2>{i18n.t("card.vram")}</h2>
      <div class="big">
        {(g.mem_used_mib / 1024).toFixed(1)}
        <span class="sub" style="font-size:.55em">/ {(g.mem_total_mib / 1024).toFixed(1)} GiB</span>
      </div>
    </div>
  {:else}
    <div class="card">
      <h2>{i18n.t("card.gpu")}</h2>
      <div class="big bad">{i18n.t("gpu.off_bus")}</div>
      <div class="sub">{i18n.t("gpu.no_response")}</div>
    </div>
  {/if}

  {#if d?.watchdog?.available && layout.visible("oculink")}
    <div class="card">
      <h2>{i18n.t("card.oculink")}</h2>
      <div class="big" class:warn={d.watchdog.drops > 0} class:ok={d.watchdog.drops === 0}>
        {d.watchdog.last_uptime}
      </div>
      <div class="sub">{d.watchdog.drops} {i18n.t("oculink.drops")}</div>
    </div>
  {/if}

  {#if d?.llm_model && layout.visible("llm_model")}
    <div class="card">
      <h2>{i18n.t("card.llm_model")}</h2>
      <div class="big" style="font-size:1em;word-break:break-all">{d.llm_model}</div>
    </div>
  {/if}

  {#if layout.visible("llm_throughput") && llm?.available && (llm.tokens_generated_total ?? 0) > 0}
    <div class="card">
      <h2>🪙 {i18n.t("card.llm_throughput")}</h2>
      <div class="big">
        {(llm.tokens_generated_total ?? 0).toLocaleString()}
        <span class="sub" style="font-size:.45em">{i18n.t("llm.tokens_generated")}</span>
      </div>
      {#if llm.tokens_per_watt}
        <div class="sub" style="margin-top:.2em;color:#a3e635">
          <b>{llm.tokens_per_watt.toFixed(2)}</b> {i18n.t("llm.tok_per_watt")}
        </div>
      {/if}
    </div>
  {/if}

  {#if elec && layout.visible("electricity")}
    {@const symbol = elec.currency === "EUR" ? "€" : elec.currency === "USD" ? "$" : elec.currency}
    <div class="card">
      <h2>⚡ {i18n.t("card.electricity")}</h2>
      <div class="big">
        {elec.avg_power_watts.toFixed(0)} W
        <span class="sub" style="font-size:.55em">{i18n.t("electricity.avg")}</span>
      </div>
      <div class="sub" style="margin-top:.2em">
        {elec.daily_kwh.toFixed(2)} kWh{i18n.t("electricity.per_day")} ·
        <span style="color:#a3e635">{elec.monthly_cost.toFixed(2)} {symbol}{i18n.t("electricity.per_month")}</span>
      </div>
      <div class="sub" style="font-size:.72em;margin-top:.15em;color:#7c8aa3">
        {i18n.t("electricity.at_rate", { price: elec.price_per_kwh.toFixed(3), sym: symbol })}
      </div>
    </div>
  {/if}

  {#if layout.visible("processes") && (d?.processes?.length ?? 0) > 0}
    <div class="card">
      <h2>{i18n.t("card.processes")}</h2>
      <table style="font-size:.78em;width:100%">
        <tbody>
          {#each (d?.processes ?? []).slice(0, 5) as p}
            <tr>
              <td style="color:#7c8aa3;width:55px">{p.pid}</td>
              <td style="overflow:hidden;text-overflow:ellipsis;max-width:120px;white-space:nowrap">{p.name}</td>
              <td style="text-align:right;color:#a3e635">{(p.vram_mib / 1024).toFixed(1)} GiB</td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  {/if}

  {#if showTuning && layout.visible("tuning")}
    <div class="card">
      <h2>{i18n.t("card.tuning")}</h2>
      <div class="tuning-row">
        <div class="tuning-lbl">{i18n.t("card.gpu")}</div>
        <div class="tuning-val">
          <b>{clocks.gr_now ?? "—"}</b> <span class="sub">/ {clocks.gr_max ?? "—"} MHz</span>
        </div>
        <div class="tuning-bar"><div style="width:{pctGpu}%;background:{tempColor(clocks.gr_now ?? 0)}"></div></div>
      </div>
      <div class="tuning-row">
        <div class="tuning-lbl">{i18n.t("tuning.memory")}</div>
        <div class="tuning-val">
          <b>{clocks.mem_now ?? "—"}</b> <span class="sub">/ {clocks.mem_max ?? "—"} MHz</span>
        </div>
        <div class="tuning-bar"><div style="width:{pctMem}%;background:#60a5fa"></div></div>
      </div>
      <div class="tuning-row">
        <div class="tuning-lbl">{i18n.t("tuning.pstate")}</div>
        <div class="tuning-val"><b>{clocks.pstate || "—"}</b></div>
        <div class="tuning-bar" style="opacity:0"></div>
      </div>
      <div class="tuning-row" style="margin-top:.5em;border-top:1px solid #22262e;padding-top:.4em">
        <div class="tuning-lbl">{i18n.t("tuning.gpu_offset")}</div>
        <div class="tuning-val" style:color={offGpu !== 0 ? "#a3e635" : "#7c8aa3"}>
          <b>{sign(offGpu)}</b> MHz
        </div>
        <div class="tuning-bar" style="opacity:0"></div>
      </div>
      <div class="tuning-row">
        <div class="tuning-lbl">{i18n.t("tuning.mem_offset")}</div>
        <div class="tuning-val" style:color={offMem !== 0 ? "#a3e635" : "#7c8aa3"}>
          <b>{sign(offMem)}</b> MHz
        </div>
        <div class="tuning-bar" style="opacity:0"></div>
      </div>
    </div>
  {/if}
</div>
