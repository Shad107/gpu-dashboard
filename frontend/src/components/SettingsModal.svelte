<script lang="ts">
  import { modal, live, toast, wizard } from "../lib/stores.svelte";
  import { i18n, type Lang } from "../lib/i18n/index.svelte";
  import { api, type HistorySample } from "../lib/api";
  import { perfEstimate, colorFan } from "../lib/charts";
  import HistoryChart from "./HistoryChart.svelte";

  // ── Power Limit state ────────────────────────────────────────────────────
  let plWatts = $state(250);
  $effect(() => {
    const g = live.data?.gpu;
    if (g?.alive) plWatts = Math.round(g.power_limit);
  });

  async function applyPowerLimit(w: number) {
    try {
      const r = await api.setPowerLimit(w);
      if (r.ok) {
        toast.emit("✓ " + i18n.t("toast.power_applied", { watts: r.watts, perf: perfEstimate(r.watts) }), "ok");
        plWatts = r.watts;
      } else {
        toast.emit("✗ " + i18n.t("toast.error") + ": " + (r.error || i18n.t("toast.unknown")), "err");
      }
    } catch (e) {
      toast.emit("✗ " + i18n.t("ts.network_error") + ": " + (e as Error).message, "err");
    }
  }

  // ── Clock offsets state ───────────────────────────────────────────────────
  let gpuOffset = $state(0);
  let memOffset = $state(0);
  let advanced = $state(false);

  $effect(() => {
    const o = live.data?.tuning?.offsets;
    if (!o) return;
    const g = Math.max(0, o.GPUGraphicsClockOffsetAllPerformanceLevels ?? 0);
    const m = Math.max(0, o.GPUMemoryTransferRateOffsetAllPerformanceLevels ?? 0);
    if (g > 100 || m > 500) advanced = true;
    gpuOffset = Math.min(g, advanced ? 200 : 100);
    memOffset = Math.min(m, advanced ? 1500 : 500);
  });

  type Zone = { n: string; c: "safe" | "mod" | "agg" | "danger" };
  function classifyGpu(v: number): Zone {
    if (v <= 50) return { n: i18n.t("zone.safe"), c: "safe" };
    if (v <= 100) return { n: i18n.t("zone.moderate"), c: "mod" };
    if (v <= 150) return { n: i18n.t("zone.aggressive"), c: "agg" };
    return { n: i18n.t("zone.danger"), c: "danger" };
  }
  function classifyMem(v: number): Zone {
    if (v <= 300) return { n: i18n.t("zone.safe"), c: "safe" };
    if (v <= 700) return { n: i18n.t("zone.moderate"), c: "mod" };
    if (v <= 1200) return { n: i18n.t("zone.aggressive"), c: "agg" };
    return { n: i18n.t("zone.danger"), c: "danger" };
  }
  const gpuZone = $derived(classifyGpu(gpuOffset));
  const memZone = $derived(classifyMem(memOffset));

  async function applyOffsets(g: number, m: number) {
    const gz = classifyGpu(g), mz = classifyMem(m);
    if (["agg", "danger"].includes(gz.c) || ["agg", "danger"].includes(mz.c)) {
      const msg = i18n.t("clocks.confirm_dangerous", { gpu: g, mem: m, gz: gz.n, mz: mz.n });
      if (!confirm(msg)) return;
    }
    try {
      const r = await api.setOffsets(g, m);
      if (r.ok) {
        toast.emit("✓ " + i18n.t("clocks.applied", { gpu: r.gpu, mem: r.mem }), "ok");
        gpuOffset = r.gpu; memOffset = r.mem;
      } else {
        toast.emit("✗ " + i18n.t("toast.error") + ": " + (r.error || i18n.t("toast.unknown")), "err");
      }
    } catch (e) {
      toast.emit("✗ " + i18n.t("ts.network_error") + ": " + (e as Error).message, "err");
    }
  }

  // ── Alerts state ──────────────────────────────────────────────────────────
  let alEnabled = $state(false);
  let alToken = $state("");
  let alChat = $state("");
  let alOnDrop = $state(true);
  let alOnRecover = $state(true);

  $effect(() => {
    api.alertsConfig().then(c => {
      alEnabled = !!c.enabled;
      alToken = c.token || "";
      alChat = c.chat_id || "";
      alOnDrop = !!c.on_drop;
      alOnRecover = !!c.on_recover;
    }).catch(() => {});
  });

  async function saveAlerts() {
    try {
      const r = await api.saveAlertsConfig({
        enabled: alEnabled, token: alToken.trim(), chat_id: alChat.trim(),
        on_drop: alOnDrop, on_recover: alOnRecover,
      });
      toast.emit(r.ok ? "✓ " + i18n.t("alerts.config_saved") : "✗ " + (r.error || i18n.t("toast.error")), r.ok ? "ok" : "err");
    } catch (e) {
      toast.emit("✗ " + i18n.t("ts.network_error") + ": " + (e as Error).message, "err");
    }
  }
  async function testAlerts() {
    try {
      const r = await api.testAlert();
      toast.emit(r.ok ? "✓ " + i18n.t("alerts.message_sent") : "✗ " + (r.msg || r.error || i18n.t("alerts.telegram_error")), r.ok ? "ok" : "err");
    } catch (e) {
      toast.emit("✗ " + i18n.t("ts.network_error") + ": " + (e as Error).message, "err");
    }
  }

  // ── Stats / Services derived from live state ──────────────────────────────
  const distEntries = $derived.by(() => {
    const d = live.data?.fan_dist ?? {};
    const total = Object.values(d).reduce((a, b) => a + b, 0) || 1;
    return Object.keys(d).sort((a, b) => +a - +b).map(k => ({
      k, n: d[k], pct: (d[k] / total) * 100,
    }));
  });
  const svcEntries = $derived(Object.entries(live.data?.services ?? {}));

  // ── History state ─────────────────────────────────────────────────────────
  type HistoryRange = "1h" | "6h" | "24h" | "7d" | "30d";
  type HistoryMetric = "power" | "temp" | "fan_pct" | "util_gpu";
  let historyRange = $state<HistoryRange>("24h");
  let historyMetric = $state<HistoryMetric>("power");
  let historySamples = $state<HistorySample[]>([]);
  let historyLoading = $state(false);

  const RANGE_SECONDS: Record<HistoryRange, number> = {
    "1h": 3600, "6h": 21600, "24h": 86400, "7d": 7 * 86400, "30d": 30 * 86400,
  };
  const RANGE_STEP: Record<HistoryRange, number> = {
    // step de rééchantillonnage pour éviter 30j × 17280 samples sur le client
    "1h": 0, "6h": 0, "24h": 60, "7d": 600, "30d": 1800,
  };

  const METRIC_INFO: Record<HistoryMetric, { color: string; unit: string }> = {
    "power":    { color: "#22d3ee", unit: "W" },
    "temp":     { color: "#fbbf24", unit: "°C" },
    "fan_pct":  { color: "#4ade80", unit: "%" },
    "util_gpu": { color: "#a855f7", unit: "%" },
  };

  async function loadHistory() {
    historyLoading = true;
    try {
      const now = Math.floor(Date.now() / 1000);
      const from = now - RANGE_SECONDS[historyRange];
      const step = RANGE_STEP[historyRange] || undefined;
      const r = await api.history(from, now, step);
      historySamples = r.samples ?? [];
    } catch (e) {
      toast.emit("✗ " + i18n.t("ts.network_error") + ": " + (e as Error).message, "err");
      historySamples = [];
    } finally {
      historyLoading = false;
    }
  }

  function exportCsv() {
    const now = Math.floor(Date.now() / 1000);
    const since = now - RANGE_SECONDS[historyRange];
    window.location.href = api.exportCsvUrl(since);
  }

  // Auto-load quand la section History s'ouvre
  $effect(() => {
    if (modal.open && modal.section === "history") {
      loadHistory();
    }
  });
  // Recharge quand range change
  $effect(() => {
    historyRange;  // dependency
    if (modal.open && modal.section === "history") {
      loadHistory();
    }
  });

  // ── Restart action ────────────────────────────────────────────────────────
  let restarting = $state(false);
  async function restartServer() {
    if (!confirm(i18n.t("services.restart_confirm"))) return;
    restarting = true;
    try {
      await api.restart();
    } catch {
      // Connection will drop during restart — that's expected
    }
    // Poll /api/state until the server is back up, then reload
    let attempts = 0;
    const maxAttempts = 30;  // ~30s max wait
    const tryReconnect = async () => {
      attempts++;
      try {
        const r = await fetch("/api/state", { cache: "no-store" });
        if (r.ok) {
          toast.emit("✓ " + i18n.t("services.reconnected"), "ok");
          setTimeout(() => location.reload(), 800);
          return;
        }
      } catch {
        // Server still down, keep trying
      }
      if (attempts < maxAttempts) {
        setTimeout(tryReconnect, 1000);
      } else {
        restarting = false;
        toast.emit("✗ Server didn't come back. Restart it manually.", "err");
      }
    };
    setTimeout(tryReconnect, 1500);
  }

  async function stopServer() {
    if (!confirm(i18n.t("services.stop_confirm"))) return;
    try {
      await api.stop();
      toast.emit("🛑 " + i18n.t("services.stopped"), "ok");
    } catch {
      // Connection drops as the server exits — that's expected
      toast.emit("🛑 " + i18n.t("services.stopped"), "ok");
    }
  }

  // ── Language selection ────────────────────────────────────────────────────
  function selectLang(l: Lang) { i18n.setLang(l); }

  // ── Keyboard close ────────────────────────────────────────────────────────
  $effect(() => {
    if (!modal.open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") modal.close(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  const sections = [
    { id: "power", labelKey: "modal.power" as const,
      icon: "M11 21h-1l1-7H7.5c-.58 0-.57-.32-.38-.66.19-.34.05-.08.07-.12C8.48 10.94 10.42 7.54 13 3h1l-1 7h3.5c.49 0 .56.33.47.51l-.07.15C12.96 17.55 11 21 11 21z" },
    { id: "clocks", labelKey: "modal.clocks" as const,
      icon: "M3 17v2h6v-2H3M3 5v2h10V5H3m10 16v-2h8v-2h-8v-2h-2v6h2M7 9v2H3v2h4v2h2V9H7m14 4v-2H11v2h10m-6-4h2V7h4V5h-4V3h-2v6z" },
    { id: "stats", labelKey: "modal.stats" as const,
      icon: "M22 21H2V3h2v16h2v-9h4v9h2V6h4v13h2v-7h4v9z" },
    { id: "services", labelKey: "modal.services" as const,
      icon: "M14.06 9L15 9.94 5.92 19H5v-.92L14.06 9m3.6-6c-.25 0-.51.1-.7.29l-1.83 1.83 3.75 3.75 1.83-1.83a.996.996 0 0 0 0-1.41l-2.34-2.34c-.2-.2-.45-.29-.71-.29m-3.6 3.19L3 17.25V21h3.75L17.81 9.94l-3.75-3.75z" },
    { id: "alerts", labelKey: "modal.alerts" as const,
      icon: "M12 22a2.5 2.5 0 0 0 2.45-2H9.55A2.5 2.5 0 0 0 12 22m6-6V11c0-3.07-1.63-5.64-4.5-6.32V4a1.5 1.5 0 0 0-3 0v.68C7.64 5.36 6 7.92 6 11v5l-2 2v1h16v-1l-2-2z" },
    { id: "language", labelKey: "modal.language" as const,
      icon: "M12.87 15.07l-2.54-2.51.03-.03c1.74-1.94 2.98-4.17 3.71-6.53H17V4h-7V2H8v2H1v1.99h11.17C11.5 7.92 10.44 9.75 9 11.35 8.07 10.32 7.3 9.19 6.69 8h-2c.73 1.63 1.73 3.17 2.98 4.56l-5.09 5.02L4 19l5-5 3.11 3.11.76-2.04zM18.5 10h-2L12 22h2l1.12-3h4.75L21 22h2l-4.5-12zm-2.62 7l1.62-4.33L19.12 17h-3.24z" },
    { id: "history", labelKey: "modal.history" as const,
      icon: "M13 3a9 9 0 0 0-9 9H1l4 4 4-4H6a7 7 0 1 1 7 7c-2.94 0-5.49-1.81-6.56-4.4l-1.89.61C5.84 18.45 9.16 21 13 21a9 9 0 0 0 0-18m-1 5v5l4.28 2.54.72-1.21-3.5-2.08V8z" },
    { id: "about", labelKey: "modal.about" as const,
      icon: "M13 9h-2V7h2m0 10h-2v-6h2m-1-9A10 10 0 0 0 2 12a10 10 0 0 0 10 10 10 10 0 0 0 10-10A10 10 0 0 0 12 2z" },
  ];

  // ── About state ───────────────────────────────────────────────────────────
  let aboutData = $state<Awaited<ReturnType<typeof api.about>> | null>(null);
  async function loadAbout() {
    try { aboutData = await api.about(); } catch { aboutData = null; }
  }
  $effect(() => {
    if (modal.open && modal.section === "about" && !aboutData) loadAbout();
  });
  function fmtUptime(s: number): string {
    const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600);
    const m = Math.floor((s % 3600) / 60), sec = s % 60;
    if (d > 0) return `${d}d ${h}h${String(m).padStart(2, "0")}m`;
    if (h > 0) return `${h}h${String(m).padStart(2, "0")}m`;
    if (m > 0) return `${m}m${String(sec).padStart(2, "0")}s`;
    return `${sec}s`;
  }
</script>

<div
  class="modal-overlay"
  class:show={modal.open}
  onclick={(e) => { if (e.target === e.currentTarget) modal.close(); }}
  role="presentation"
>
  <div class="modal">
    <div class="modal-sidebar">
      <h3>{i18n.t("modal.settings")}</h3>
      {#each sections as s}
        <button
          class="sidebar-item"
          class:active={modal.section === s.id}
          onclick={() => modal.setSection(s.id)}
        >
          <svg class="icon" viewBox="0 0 24 24" fill="currentColor"><path d={s.icon} /></svg>
          <span>{i18n.t(s.labelKey)}</span>
        </button>
      {/each}
    </div>
    <div class="modal-content">
      <button class="modal-close" aria-label={i18n.t("modal.close")} onclick={() => modal.close()}>×</button>

      <!-- Power Limit -->
      <div class="modal-section" class:active={modal.section === "power"}>
        <h3 class="title">
          <svg class="icon" viewBox="0 0 24 24" fill="currentColor"><path d={sections[0].icon} /></svg>
          <span>{i18n.t("power.section_title")}</span>
        </h3>
        <p class="sub" style="margin:0 0 1em">{i18n.t("power.description")}</p>
        <div class="controls" style="background:transparent;border:none;padding:0">
          <div class="slider-row">
            <label for="pl-slider">{i18n.t("power.limit_label")}</label>
            <input id="pl-slider" type="range" min="100" max="350" step="10" bind:value={plWatts} />
            <div class="val">
              <span>{plWatts}</span> W
              <span class="sub" style="font-size:.78em">(~{perfEstimate(plWatts)}% {i18n.t("perf.perf_short")})</span>
            </div>
          </div>
          <div class="btn-row">
            <button class="btn btn-primary" onclick={() => applyPowerLimit(plWatts)}>{i18n.t("power.apply")}</button>
            <button class="btn" onclick={() => applyPowerLimit(250)}>{i18n.t("power.preset_250")}</button>
            <button class="btn" onclick={() => applyPowerLimit(350)}>{i18n.t("power.preset_350")}</button>
            <span class="warn-text">{i18n.t("power.stock_note")}</span>
          </div>
        </div>
      </div>

      <!-- Clocks -->
      <div class="modal-section" class:active={modal.section === "clocks"}>
        <h3 class="title">
          <svg class="icon" viewBox="0 0 24 24" fill="currentColor"><path d={sections[1].icon} /></svg>
          <span>{i18n.t("clocks.section_title")}</span>
        </h3>
        <div class="controls" style="background:transparent;border:none;padding:0">
          <div class="ref-text">
            <b>{i18n.t("clocks.reference_title")}</b>
            {i18n.t("clocks.reference_text")} <code>GPU +75–100</code> / <code>mem +500</code>.
            {i18n.t("clocks.reference_warning")}<br />
            <span style="color:#7c8aa3">
              {i18n.t("clocks.zones_label")}
              <span class="zone safe">{i18n.t("zone.safe")}</span> {i18n.t("clocks.zone_safe_help")} ·
              <span class="zone mod">{i18n.t("zone.moderate")}</span> {i18n.t("clocks.zone_mod_help")} ·
              <span class="zone agg">{i18n.t("zone.aggressive")}</span> {i18n.t("clocks.zone_agg_help")} ·
              <span class="zone danger">{i18n.t("zone.danger")}</span> {i18n.t("clocks.zone_danger_help")}
            </span>
          </div>
          <div class="slider-row">
            <label for="gpu-offset">{i18n.t("clocks.gpu_offset")}</label>
            <input
              id="gpu-offset"
              type="range"
              min="0"
              max={advanced ? 200 : 100}
              step="25"
              bind:value={gpuOffset}
            />
            <div class="val">
              <span>+{gpuOffset}</span> MHz
              <span class="zone {gpuZone.c}">{gpuZone.n}</span>
            </div>
          </div>
          <div class="slider-row">
            <label for="mem-offset">{i18n.t("clocks.mem_offset")}</label>
            <input
              id="mem-offset"
              type="range"
              min="0"
              max={advanced ? 1500 : 500}
              step="50"
              bind:value={memOffset}
            />
            <div class="val">
              <span>+{memOffset}</span> MHz
              <span class="zone {memZone.c}">{memZone.n}</span>
            </div>
          </div>
          <label class="advanced-row">
            <input type="checkbox" bind:checked={advanced} />
            <span>{i18n.t("clocks.advanced_mode")}</span>
            <span class="locked-mark" style:color={advanced ? "#fb923c" : "#5a606c"}>
              {advanced ? i18n.t("clocks.unlocked") : i18n.t("clocks.locked")}
            </span>
          </label>
          <div class="btn-row">
            <button class="btn btn-primary" onclick={() => applyOffsets(gpuOffset, memOffset)}>{i18n.t("clocks.apply")}</button>
            <button class="btn btn-danger" onclick={() => applyOffsets(0, 0)}>{i18n.t("clocks.reset")}</button>
            <span class="warn-text">{i18n.t("clocks.test_warning")}</span>
          </div>
        </div>
      </div>

      <!-- Stats -->
      <div class="modal-section" class:active={modal.section === "stats"}>
        <h3 class="title">
          <svg class="icon" viewBox="0 0 24 24" fill="currentColor"><path d={sections[2].icon} /></svg>
          <span>{i18n.t("stats.title")}</span>
        </h3>
        <table><tbody>
          {#each distEntries as r}
            <tr>
              <td>{r.k}%</td>
              <td>
                {r.n} <span class="sub">({r.pct.toFixed(1)}%)</span><br />
                <span class="bar" style:width="{r.pct.toFixed(1)}%">
                  <div style="width:100%;background:{colorFan(+r.k)}"></div>
                </span>
              </td>
            </tr>
          {/each}
        </tbody></table>
      </div>

      <!-- Services -->
      <div class="modal-section" class:active={modal.section === "services"}>
        <h3 class="title">
          <svg class="icon" viewBox="0 0 24 24" fill="currentColor"><path d={sections[3].icon} /></svg>
          <span>{i18n.t("services.title")}</span>
        </h3>
        <table><tbody>
          {#each svcEntries as [k, v]}
            <tr>
              <td>{k}</td>
              <td class={v === "active" ? "ok" : v === "unknown" ? "warn" : "bad"}>{v}</td>
            </tr>
          {/each}
        </tbody></table>

        <h3 style="margin-top:1.8em;color:#cdd2da;font-size:.95em;font-weight:600">
          {i18n.t("services.restart_label")}
        </h3>
        <p class="sub">{i18n.t("services.restart_description")}</p>
        <div class="btn-row" style="margin-top:.8em">
          <button class="btn btn-danger" disabled={restarting} onclick={restartServer}>
            {restarting ? i18n.t("services.restarting") : "🔄 " + i18n.t("services.restart_btn")}
          </button>
          <button class="btn btn-danger" onclick={stopServer}>
            🛑 {i18n.t("services.stop_btn")}
          </button>
        </div>

        <h3 style="margin-top:1.8em;color:#cdd2da;font-size:.95em;font-weight:600">
          {i18n.t("services.redo_wizard_label")}
        </h3>
        <p class="sub">{i18n.t("services.redo_wizard_description")}</p>
        <div class="btn-row" style="margin-top:.8em">
          <button class="btn" onclick={() => wizard.request()}>
            🧙 {i18n.t("services.redo_wizard_btn")}
          </button>
        </div>
      </div>

      <!-- Alerts -->
      <div class="modal-section" class:active={modal.section === "alerts"}>
        <h3 class="title">
          <svg class="icon" viewBox="0 0 24 24" fill="currentColor"><path d={sections[4].icon} /></svg>
          <span>{i18n.t("alerts.title")}</span>
        </h3>
        <p class="sub" style="margin:0 0 1em">{i18n.t("alerts.description")}</p>
        <div class="controls" style="background:transparent;border:none;padding:0">
          <label class="form-row">
            <span class="form-lbl">{i18n.t("alerts.enable_label")}</span>
            <span class="form-val"><input type="checkbox" bind:checked={alEnabled} /> {i18n.t("alerts.enable_help")}</span>
          </label>
          <label class="form-row">
            <span class="form-lbl">{i18n.t("alerts.bot_token")}</span>
            <input class="al-input" type="text" placeholder="123456789:ABC..." autocomplete="off" spellcheck="false" bind:value={alToken} />
          </label>
          <label class="form-row">
            <span class="form-lbl">{i18n.t("alerts.chat_id")}</span>
            <input class="al-input" type="text" placeholder="123456789" autocomplete="off" spellcheck="false" bind:value={alChat} />
          </label>
          <div class="form-row">
            <span class="form-lbl">{i18n.t("alerts.events")}</span>
            <span class="form-val">
              <label style="cursor:pointer;margin-right:1.2em"><input type="checkbox" bind:checked={alOnDrop} /> {i18n.t("alerts.drop")}</label>
              <label style="cursor:pointer"><input type="checkbox" bind:checked={alOnRecover} /> {i18n.t("alerts.recovery")}</label>
            </span>
          </div>
          <div class="btn-row">
            <button class="btn btn-primary" onclick={saveAlerts}>{i18n.t("alerts.save")}</button>
            <button class="btn" onclick={testAlerts}>{i18n.t("alerts.test_btn")}</button>
            <span class="warn-text">{i18n.t("alerts.token_note")}</span>
          </div>
        </div>
      </div>

      <!-- History -->
      <div class="modal-section" class:active={modal.section === "history"}>
        <h3 class="title">
          <svg class="icon" viewBox="0 0 24 24" fill="currentColor"><path d={sections[6].icon} /></svg>
          <span>{i18n.t("history.title")}</span>
        </h3>
        <p class="sub" style="margin:0 0 1em">{i18n.t("history.description")}</p>

        <div class="controls" style="background:transparent;border:none;padding:0">
          <div class="btn-row" style="margin-bottom:.8em">
            {#each ["1h", "6h", "24h", "7d", "30d"] as r}
              <button
                class="btn"
                class:btn-primary={historyRange === r}
                onclick={() => (historyRange = r as HistoryRange)}
              >{i18n.t(`history.range_${r}` as any)}</button>
            {/each}
          </div>

          <div class="form-row">
            <span class="form-lbl">{i18n.t("history.metric")}</span>
            <span class="form-val">
              <select bind:value={historyMetric} class="al-input" style="max-width:240px">
                <option value="power">{i18n.t("history.metric_power")}</option>
                <option value="temp">{i18n.t("history.metric_temp")}</option>
                <option value="fan_pct">{i18n.t("history.metric_fan")}</option>
                <option value="util_gpu">{i18n.t("history.metric_util")}</option>
              </select>
            </span>
          </div>

          <div style="height:340px;background:#0e1014;border-radius:8px;padding:6px;margin-top:.8em">
            {#if historyLoading}
              <div style="color:#7c8aa3;padding:2em;text-align:center">{i18n.t("history.loading")}</div>
            {:else}
              <HistoryChart
                samples={historySamples}
                metric={historyMetric}
                color={METRIC_INFO[historyMetric].color}
                unit={METRIC_INFO[historyMetric].unit}
              >
                <span slot="empty">{i18n.t("history.no_data")}</span>
              </HistoryChart>
            {/if}
          </div>

          <div class="btn-row" style="margin-top:.8em">
            <button class="btn btn-primary" onclick={loadHistory}>{i18n.t("history.refresh")}</button>
            <button class="btn" onclick={exportCsv}>📥 {i18n.t("history.export_csv")}</button>
            <span class="warn-text">{i18n.t("history.samples_count", { n: historySamples.length })}</span>
          </div>
        </div>
      </div>

      <!-- About -->
      <div class="modal-section" class:active={modal.section === "about"}>
        <h3 class="title">
          <svg class="icon" viewBox="0 0 24 24" fill="currentColor"><path d={sections[7].icon} /></svg>
          <span>{i18n.t("about.title")}</span>
        </h3>
        <p class="sub" style="margin:0 0 1em">{i18n.t("about.tagline")}</p>

        {#if aboutData}
          <table class="about-table">
            <tbody>
              <tr><td>{i18n.t("about.version")}</td><td><code>{aboutData.version}</code></td></tr>
              <tr><td>{i18n.t("about.uptime")}</td><td>{fmtUptime(aboutData.uptime_seconds)}</td></tr>
              <tr><td>{i18n.t("about.python")}</td><td>{aboutData.python_version}</td></tr>
              <tr><td>{i18n.t("about.platform")}</td><td style="font-size:.82em">{aboutData.platform}</td></tr>
              <tr><td>{i18n.t("about.config_path")}</td><td><code>{aboutData.config_path}</code></td></tr>
              <tr><td>{i18n.t("about.storage_path")}</td><td><code>{aboutData.storage_path}</code></td></tr>
              <tr><td>{i18n.t("about.license")}</td><td>{aboutData.license}</td></tr>
              <tr><td>{i18n.t("about.repo")}</td>
                <td><a href={aboutData.repo_url} target="_blank" rel="noopener" style="color:#4ade80">{aboutData.repo_url}</a></td>
              </tr>
            </tbody>
          </table>
        {:else}
          <p class="sub">{i18n.t("history.loading")}</p>
        {/if}
      </div>

      <!-- Language -->
      <div class="modal-section" class:active={modal.section === "language"}>
        <h3 class="title">
          <svg class="icon" viewBox="0 0 24 24" fill="currentColor"><path d={sections[5].icon} /></svg>
          <span>{i18n.t("lang.title")}</span>
        </h3>
        <p class="sub" style="margin:0 0 1em">{i18n.t("lang.description")}</p>
        <div class="controls" style="background:transparent;border:none;padding:0">
          <label class="form-row" style="cursor:pointer">
            <span class="form-lbl"><input type="radio" name="lang" value="en" checked={i18n.lang === "en"} onchange={() => selectLang("en")} /></span>
            <span class="form-val">{i18n.t("lang.en")}</span>
          </label>
          <label class="form-row" style="cursor:pointer">
            <span class="form-lbl"><input type="radio" name="lang" value="fr" checked={i18n.lang === "fr"} onchange={() => selectLang("fr")} /></span>
            <span class="form-val">{i18n.t("lang.fr")}</span>
          </label>
        </div>
      </div>
    </div>
  </div>
</div>
