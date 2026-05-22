<script lang="ts">
  import { onMount } from "svelte";
  import { live, toast } from "../lib/stores.svelte";
  import { layout } from "../lib/layout.svelte";
  import { i18n } from "../lib/i18n/index.svelte";
  import { tempColor, perfEstimate } from "../lib/charts";
  import { api } from "../lib/api";
  import { gpu } from "../lib/gpu.svelte";
  import Sparkline from "./Sparkline.svelte";

  // Electricity widget — polled every minute (independent from live data)
  let elec = $state<Awaited<ReturnType<typeof api.electricity>> | null>(null);
  let llm = $state<Awaited<ReturnType<typeof api.llmStats>> | null>(null);
  let llmLifetime = $state<Awaited<ReturnType<typeof api.llmLifetime>> | null>(null);
  let llmPerf = $state<Awaited<ReturnType<typeof api.llmPerf>> | null>(null);
  async function loadElec() {
    try { elec = await api.electricity(3600, gpu.selected); } catch { /* keep last */ }
  }

  // Inline price edit (cycle 139, user feedback)
  let editingPrice = $state(false);
  let priceEdit = $state(0);
  let priceSaving = $state(false);
  async function savePrice() {
    if (!elec) return;
    priceSaving = true;
    try {
      const r = await fetch("/api/electricity/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          price_per_kwh: priceEdit,
          currency: elec.currency,
        }),
      });
      const j = await r.json();
      if (j.ok) {
        toast.show(i18n.t("electricity.rate_saved") ?? "Rate saved", "success");
        await loadElec();
        editingPrice = false;
      } else {
        toast.show(j.error || "save failed", "error");
      }
    } catch (e: any) {
      toast.show(e?.message || "save failed", "error");
    } finally {
      priceSaving = false;
    }
  }
  async function loadLlm() {
    try { llm = await api.llmStats(gpu.selected); } catch { /* keep last */ }
  }
  async function loadLlmLifetime() {
    try { llmLifetime = await api.llmLifetime(gpu.selected); } catch { /* keep last */ }
  }
  async function loadLlmPerf() {
    try { llmPerf = await api.llmPerf(gpu.selected); } catch { /* keep last */ }
  }

  // Re-fetch immediately when the user picks a different GPU
  $effect(() => {
    gpu.selected;  // dependency
    loadElec(); loadLlm(); loadLlmLifetime(); loadLlmPerf();
  });
  let elecTimer: ReturnType<typeof setInterval> | null = null;
  let llmTimer: ReturnType<typeof setInterval> | null = null;
  let llmLifeTimer: ReturnType<typeof setInterval> | null = null;
  let llmPerfTimer: ReturnType<typeof setInterval> | null = null;
  onMount(() => {
    loadElec();
    loadLlm();
    loadLlmLifetime();
    loadLlmPerf();
    elecTimer = setInterval(loadElec, 60_000);
    llmTimer = setInterval(loadLlm, 30_000);
    llmLifeTimer = setInterval(loadLlmLifetime, 120_000);
    llmPerfTimer = setInterval(loadLlmPerf, 30_000);
    return () => {
      if (elecTimer) clearInterval(elecTimer);
      if (llmTimer) clearInterval(llmTimer);
      if (llmLifeTimer) clearInterval(llmLifeTimer);
      if (llmPerfTimer) clearInterval(llmPerfTimer);
    };
  });

  function fmtBig(n: number): string {
    if (n >= 1_000_000) return (n / 1_000_000).toFixed(2) + "M";
    if (n >= 1_000) return (n / 1_000).toFixed(1) + "k";
    return String(n);
  }

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

  // Strip scrolling : wheel-to-X + canScroll left/right state for arrows
  // (cycle 144c, user fb : 'fleche a gauche et a droite quand nécessaire
  // plutot que la scrollbar horizontal').
  let stripEl = $state<HTMLElement | undefined>();
  let canScrollLeft = $state(false);
  let canScrollRight = $state(false);

  function updateScrollState() {
    if (!stripEl) return;
    canScrollLeft = stripEl.scrollLeft > 4;
    canScrollRight = stripEl.scrollLeft < stripEl.scrollWidth - stripEl.clientWidth - 4;
  }

  function scrollByCards(dir: -1 | 1) {
    if (!stripEl) return;
    // Scroll ~3 cards worth (3 × (200 + 8 gap) ≈ 624)
    stripEl.scrollBy({ left: dir * 624, behavior: "smooth" });
  }

  function wheelScroll(node: HTMLElement) {
    stripEl = node;
    function handle(e: WheelEvent) {
      if (e.deltaY !== 0 && Math.abs(e.deltaY) > Math.abs(e.deltaX)) {
        if (node.scrollWidth > node.clientWidth) {
          e.preventDefault();
          node.scrollLeft += e.deltaY;
        }
      }
    }
    function onScroll() { updateScrollState(); }
    node.addEventListener("wheel", handle, { passive: false });
    node.addEventListener("scroll", onScroll, { passive: true });
    // Update on resize too
    const ro = new ResizeObserver(updateScrollState);
    ro.observe(node);
    // Initial state after layout
    setTimeout(updateScrollState, 50);
    return {
      destroy() {
        node.removeEventListener("wheel", handle);
        node.removeEventListener("scroll", onScroll);
        ro.disconnect();
      }
    };
  }
</script>

<!-- Cycle 144c — ONE horizontal strip + ‹ › arrows replacing scrollbar
     (user : 'fleche a gauche et a droite quand nécessaire plutot que
     la scrollbar horizontal'). -->

<div class="strip-container">
  <button class="strip-arrow strip-arrow-left"
    class:visible={canScrollLeft}
    onclick={() => scrollByCards(-1)}
    aria-label="Scroll left">‹</button>
  <button class="strip-arrow strip-arrow-right"
    class:visible={canScrollRight}
    onclick={() => scrollByCards(1)}
    aria-label="Scroll right">›</button>

<div class="strip strip-merged no-scrollbar" use:wheelScroll>
  <span class="group-divider"><span class="group-divider-label">🖥️ {i18n.t("group.gpu") ?? "GPU"}</span></span>
    {#if alive && g && g.alive}
      {#if layout.visible("gpu")}
      <div class="card">
        <h2>{i18n.t("card.gpu")}</h2>
        <div class="big" style:color={tempColor(g.temp)}>{g.temp}°C</div>
        <div class="sub">{i18n.t("gpu.util")} {g.util_gpu}% · {i18n.t("gpu.draw")} {g.power.toFixed(0)} W</div>
        {#if g.mem_temp !== null && g.mem_temp !== undefined}
          <div class="sub" style="margin-top:.2em" style:color={tempColor(g.mem_temp + 15)}>
            {i18n.t("gpu.mem_temp")} {g.mem_temp}°C
          </div>
        {/if}
        {#if (g.util_enc != null && g.util_enc > 0) || (g.util_dec != null && g.util_dec > 0)}
          <div class="sub" style="margin-top:.2em">
            ENC <b>{g.util_enc ?? 0}%</b> · DEC <b>{g.util_dec ?? 0}%</b>
          </div>
        {/if}
      </div>
      {/if}

      {#if layout.visible("vram")}
      <div class="card">
        <h2>{i18n.t("card.vram")}</h2>
        <div class="big">
          {(g.mem_used_mib / 1024).toFixed(1)}
          <span class="sub" style="font-size:.55em">/ {(g.mem_total_mib / 1024).toFixed(1)} GiB</span>
        </div>
      </div>
      {/if}

      {#if layout.visible("fans")}
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
      {/if}

      {#if layout.visible("pcie") && g.pcie_gen != null && g.pcie_width != null}
        {@const downgrade = (g.pcie_gen_max != null && g.pcie_gen < g.pcie_gen_max)
                         || (g.pcie_width_max != null && g.pcie_width < g.pcie_width_max)}
        <div class="card">
          <h2>{i18n.t("card.pcie") ?? "PCIe"}</h2>
          <div class="big" style:color={downgrade ? "var(--accent-warn)" : "var(--accent)"}>
            Gen {g.pcie_gen} ×{g.pcie_width}
          </div>
          <div class="sub">
            {#if g.pcie_gen_max != null && g.pcie_width_max != null}
              max Gen {g.pcie_gen_max} ×{g.pcie_width_max}
              {#if downgrade}<span style="color:var(--accent-warn);margin-left:.4em">⚠️ {i18n.t("pcie.downgrade") ?? "downgraded"}</span>{/if}
            {/if}
          </div>
        </div>
      {/if}
    {:else}
      <div class="card">
        <h2>{i18n.t("card.gpu")}</h2>
        <div class="big bad">{i18n.t("gpu.off_bus")}</div>
        <div class="sub">{i18n.t("gpu.no_response")}</div>
      </div>
    {/if}

  <span class="group-divider"><span class="group-divider-label">🔧 {i18n.t("group.tuning") ?? "Tuning"}</span></span>
    {#if alive && g && g.alive && layout.visible("power_limit")}
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

    {#if d?.watchdog?.available && layout.visible("oculink")}
      <div class="card">
        <h2>{i18n.t("card.oculink")}</h2>
        <div class="big" class:warn={d.watchdog.drops > 0} class:ok={d.watchdog.drops === 0}>
          {d.watchdog.last_uptime}
        </div>
        <div class="sub">{d.watchdog.drops} {i18n.t("oculink.drops")}</div>
      </div>
    {/if}
    </div>
  </div>

  <div class="strip-group">
    <h4 class="strip-group-label">🪙 {i18n.t("group.llm") ?? "LLM"}</h4>
    <div class="strip-cards">
    {#if d?.llm_model && layout.visible("llm_model")}
      <div class="card">
        <h2>{i18n.t("card.llm_model")}</h2>
        <div class="big" style="font-size:1em;word-break:break-all">{d.llm_model}</div>
      </div>
    {/if}

    {#if layout.visible("llm_throughput") && llm?.available && (llm.tokens_generated_total ?? 0) > 0}
      <div class="card">
        <h2>🪙 {i18n.t("card.llm_throughput")}</h2>
        {#if llmPerf?.available && (llmPerf.series_1h?.some(v => v > 0) ?? false)}
          {@const headline = (llmPerf.avg_tps_1m ?? 0) > 0
            ? (llmPerf.avg_tps_1m ?? 0)
            : (llmPerf.avg_tps_5m ?? 0)}
          <div class="big" style="color:#f472b6">
            {headline.toFixed(1)}
            <span class="sub" style="font-size:.45em">tok/s</span>
          </div>
          {#if llmPerf.series_1h && llmPerf.series_1h.length > 0}
            <div style="margin-top:-.2em;margin-bottom:.3em">
              <Sparkline values={llmPerf.series_1h} color="#f472b6" width={180} height={26} />
            </div>
          {/if}
          <div class="sub" style="font-size:.75em">
            5m <b>{(llmPerf.avg_tps_5m ?? 0).toFixed(1)}</b> ·
            1h <b>{(llmPerf.avg_tps_1h ?? 0).toFixed(1)}</b>
            {#if llm.tokens_per_watt}
              · <b style="color:#a3e635">{llm.tokens_per_watt.toFixed(2)}</b> tok/W
            {/if}
          </div>
        {:else}
          <div class="big">
            {(llm.tokens_generated_total ?? 0).toLocaleString()}
            <span class="sub" style="font-size:.45em">{i18n.t("llm.tokens_generated")}</span>
          </div>
          {#if llm.tokens_per_watt}
            <div class="sub" style="margin-top:.2em;color:#a3e635">
              <b>{llm.tokens_per_watt.toFixed(2)}</b> {i18n.t("llm.tok_per_watt")}
            </div>
          {/if}
        {/if}
        {#if llmLifetime?.available && llmLifetime.total_tokens_generated > 0}
          <div class="sub" style="margin-top:.35em;padding-top:.3em;border-top:1px solid #22262e;font-size:.78em">
            {i18n.t("llm.lifetime")} <b style="color:#fbbf24">{fmtBig(llmLifetime.total_tokens_generated)}</b>
            {#if llmLifetime.avg_tokens_per_watt}
              · <b style="color:#a3e635">{llmLifetime.avg_tokens_per_watt.toFixed(2)}</b> {i18n.t("llm.tok_per_watt")}
            {/if}
          </div>
        {/if}
      </div>
    {/if}
    </div>
  </div>

  <div class="strip-group">
    <h4 class="strip-group-label">💸 {i18n.t("group.cost") ?? "Coût"}</h4>
    <div class="strip-cards">
    {#if elec && layout.visible("electricity")}
      {@const symbol = elec.currency === "EUR" ? "€" : elec.currency === "USD" ? "$" : elec.currency}
      {@const budgetUsedPct = elec.budget_kwh > 0 ? Math.min(100, (elec.kwh_month / elec.budget_kwh) * 100) : 0}
      {@const budgetForecastPct = elec.budget_kwh > 0 ? Math.min(150, (elec.forecast_kwh / elec.budget_kwh) * 100) : 0}
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
          {#if editingPrice}
            <span style="display:inline-flex;gap:.3em;align-items:center">
              <input type="number" step="0.001" min="0" max="5" bind:value={priceEdit}
                     class="price-input" autofocus />
              <span>{symbol}/kWh</span>
              <button class="btn-mini" onclick={savePrice} disabled={priceSaving}>
                {priceSaving ? "…" : "💾"}
              </button>
              <button class="btn-mini" onclick={() => { editingPrice = false; }}>✕</button>
            </span>
          {:else}
            {i18n.t("electricity.at_rate", { price: elec.price_per_kwh.toFixed(3), sym: symbol })}
            <button class="edit-rate" onclick={() => { priceEdit = elec.price_per_kwh; editingPrice = true; }}
                    title={i18n.t("electricity.edit_rate") ?? "Edit price"}>✎</button>
          {/if}
        </div>
        {#if elec.budget_kwh > 0}
          <div class="budget-tracker" style="margin-top:.5em">
            <div class="budget-line">
              <span class="sub" style="font-size:.74em">{i18n.t("electricity.budget_label") ?? "Budget"}:</span>
              <span style:color={elec.over_budget ? "var(--accent-warn)" : "var(--accent)"}>
                <b>{elec.kwh_month.toFixed(1)}</b> / {elec.budget_kwh.toFixed(0)} kWh
              </span>
              <span class="sub" style="font-size:.7em;margin-left:.4em">({elec.month_progress_pct.toFixed(0)}% {i18n.t("electricity.month_done") ?? "of month"})</span>
            </div>
            <div class="budget-bar">
              <div class="budget-fill" style:width="{budgetUsedPct}%" style:background={elec.over_budget ? "var(--accent-warn)" : "var(--accent)"}></div>
              {#if budgetForecastPct > budgetUsedPct}
                <div class="budget-forecast" style:left="{budgetUsedPct}%" style:width="{Math.max(0, budgetForecastPct - budgetUsedPct)}%"></div>
              {/if}
            </div>
            <div class="sub" style="font-size:.7em;margin-top:.15em" style:color={elec.over_budget ? "var(--accent-warn)" : "var(--text-dim)"}>
              {#if elec.over_budget}⚠️ {/if}{i18n.t("electricity.forecast") ?? "Forecast"}:
              <b>{elec.forecast_kwh.toFixed(1)} kWh</b>
            </div>
          </div>
        {/if}
      </div>
    {/if}

    {#if layout.visible("processes") && (d?.processes?.length ?? 0) > 0}
      {@const vramTotalMib = d?.gpu?.mem_total_mib ?? 24576}
      <div class="card">
        <h2>{i18n.t("card.processes")}</h2>
        <table class="proc-table">
          <tbody>
            {#each (d?.processes ?? []).slice(0, 5) as p}
              {@const pct = Math.min(100, (p.vram_mib / vramTotalMib) * 100)}
              {@const barColor = pct < 50 ? "#4ade80" : pct < 80 ? "#fbbf24" : "#f87171"}
              <tr title={p.cmdline || `${p.name} (PID ${p.pid})`}>
                <td class="proc-pid">{p.pid}</td>
                <td class="proc-name">{p.name}</td>
                <td class="proc-vram">
                  <span style="color:#a3e635">{(p.vram_mib / 1024).toFixed(1)} GiB</span>
                  <div class="proc-bar"><div style:width="{pct.toFixed(1)}%" style:background={barColor}></div></div>
                </td>
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
    {/if}

    {#each layout.customCards as cc (cc.id)}
      {#if layout.visible(cc.id)}
        <div class="card custom-card">
          <h2>🧩 {cc.name}</h2>
          <iframe
            src={cc.url}
            title={cc.name}
            sandbox="allow-scripts allow-same-origin"
            referrerpolicy="no-referrer"
            loading="lazy"
          ></iframe>
        </div>
      {/if}
    {/each}
    </div>
  </div>
</div>
</div>
