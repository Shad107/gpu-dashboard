<script lang="ts">
  import { modal, live, toast, wizard } from "../lib/stores.svelte";
  import { layout, CARD_NAMES, isValidUrl } from "../lib/layout.svelte";
  import { theme } from "../lib/theme.svelte";
  import { push } from "../lib/push.svelte";
  import FanCurveEditor from "./FanCurveEditor.svelte";
  import { i18n, type Lang } from "../lib/i18n/index.svelte";
  import { api, type HistorySample, type StoredEvent } from "../lib/api";
  import { perfEstimate, colorFan } from "../lib/charts";
  import HistoryChart from "./HistoryChart.svelte";
  import { dndzone } from "svelte-dnd-action";

  // Layout drag-and-drop items — re-derived from layout.order whenever it changes
  type LayoutItem = { id: string };
  let layoutItems = $state<LayoutItem[]>(layout.order.map(n => ({ id: n })));

  // Custom URL card form
  let newCustomName = $state("");
  let newCustomUrl = $state("");
  function isCustomId(id: string): boolean { return id.startsWith("custom-"); }
  function cardLabel(id: string): string {
    if (isCustomId(id)) {
      return layout.customCards.find(c => c.id === id)?.name ?? id;
    }
    return i18n.t(("card." + id) as any);
  }
  function addCustomCard() {
    const url = newCustomUrl.trim();
    if (!isValidUrl(url)) {
      toast.emit("✗ " + i18n.t("layout.invalid_url"), "err");
      return;
    }
    const id = layout.addCustom(newCustomName.trim() || new URL(url).hostname, url);
    if (id) {
      toast.emit("✓ " + i18n.t("layout.custom_added", { name: newCustomName.trim() || url }), "ok");
      newCustomName = "";
      newCustomUrl = "";
    }
  }
  // Keep local mirror in sync if external changes happen (eg. reset)
  $effect(() => {
    const order = layout.order;
    if (order.join(",") !== layoutItems.map(i => i.id).join(",")) {
      layoutItems = order.map(n => ({ id: n }));
    }
  });
  function handleDndConsider(e: CustomEvent<{ items: LayoutItem[] }>) {
    layoutItems = e.detail.items;
  }
  function handleDndFinalize(e: CustomEvent<{ items: LayoutItem[] }>) {
    layoutItems = e.detail.items;
    layout.setOrder(layoutItems.map(i => i.id));
  }

  // ── Power profile presets state ───────────────────────────────────────────
  let powerProfiles = $state<{ name: "silent"|"sweet"|"boost"; watts: number; gpu_offset: number; mem_offset: number }[]>([]);
  let applyingProfile = $state(false);
  $effect(() => {
    if (modal.open && modal.section === "power" && powerProfiles.length === 0) {
      api.powerProfilesList().then(r => { powerProfiles = r.profiles; }).catch(() => {});
    }
  });
  async function applyPowerProfile(name: string) {
    applyingProfile = true;
    try {
      const r = await api.powerProfileApply(name);
      if (r.ok) {
        toast.emit("✓ " + i18n.t("power.profile_applied", {
          name, w: r.watts ?? "?", g: r.gpu_offset ?? 0, m: r.mem_offset ?? 0,
        }), "ok");
      } else {
        toast.emit("✗ " + (r.error || "apply failed"), "err");
      }
    } catch (e) {
      toast.emit("✗ " + (e as Error).message, "err");
    } finally {
      applyingProfile = false;
    }
  }

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

  // ── Sound notification toggle ────────────────────────────────────────────
  let soundEnabled = $state(
    typeof localStorage !== "undefined" && localStorage.getItem("gpu-dashboard-sound") === "1"
  );
  function onSoundToggle() {
    localStorage.setItem("gpu-dashboard-sound", soundEnabled ? "1" : "0");
    // Preview the sound on enable so the user knows what to expect
    if (soundEnabled) {
      toast.emit("🔊 Sound enabled — test beep", "err", 1500);
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
  type HistoryMetric = "power" | "temp" | "fan_pct" | "util_gpu" | "tokens_per_sec" | "tokens_per_watt";
  let historyRange = $state<HistoryRange>("24h");
  let historyMetric = $state<HistoryMetric>("power");
  let historySamples = $state<HistorySample[]>([]);
  let historyEvents = $state<StoredEvent[]>([]);
  let historyCompare = $state<HistorySample[]>([]);
  /** Seconds offset for comparison series. 0 = disabled. Common values :
   *  86400 (24h), 604800 (7d), 2592000 (30d) */
  let historyCompareOffset = $state(0);
  const historyCompareMode = $derived(historyCompareOffset > 0);
  function compareLabelFor(offset: number): string {
    if (offset === 86400) return i18n.t("history.compare_label_24h");
    if (offset === 604800) return i18n.t("history.compare_label_7d");
    if (offset === 2592000) return i18n.t("history.compare_label_30d");
    return "";
  }

  // Power heatmap (24 hours × N days window)
  let heatmapData = $state<Awaited<ReturnType<typeof api.powerHeatmap>> | null>(null);
  let heatmapDays = $state(7);
  async function loadHeatmap() {
    try { heatmapData = await api.powerHeatmap(heatmapDays); } catch { heatmapData = null; }
  }
  $effect(() => {
    if (modal.open && modal.section === "history" && !heatmapData) loadHeatmap();
  });
  $effect(() => {
    heatmapDays;  // re-fetch when days changes
    if (modal.open && modal.section === "history" && heatmapData) loadHeatmap();
  });
  const heatmapMaxCost = $derived(
    heatmapData ? Math.max(0.001, ...heatmapData.hours.map(h => h.cost_per_hour)) : 1
  );
  function heatmapBg(cost: number): string {
    const ratio = cost / heatmapMaxCost;
    // Color from dark blue (low) → orange (high)
    if (ratio < 0.05) return "#0e1014";
    if (ratio < 0.25) return `rgba(96,165,250,${0.15 + ratio * 0.5})`;
    if (ratio < 0.6)  return `rgba(251,191,36,${0.2 + ratio * 0.5})`;
    return `rgba(251,146,60,${0.3 + ratio * 0.7})`;
  }
  let historyLoading = $state(false);
  let historyAutoRefresh = $state(false);
  let historyTimer: ReturnType<typeof setInterval> | null = null;

  const RANGE_SECONDS: Record<HistoryRange, number> = {
    "1h": 3600, "6h": 21600, "24h": 86400, "7d": 7 * 86400, "30d": 30 * 86400,
  };
  const RANGE_STEP: Record<HistoryRange, number> = {
    // step de rééchantillonnage pour éviter 30j × 17280 samples sur le client
    "1h": 0, "6h": 0, "24h": 60, "7d": 600, "30d": 1800,
  };

  const METRIC_INFO: Record<HistoryMetric, { color: string; unit: string }> = {
    "power":           { color: "#22d3ee", unit: "W" },
    "temp":            { color: "#fbbf24", unit: "°C" },
    "fan_pct":         { color: "#4ade80", unit: "%" },
    "util_gpu":        { color: "#a855f7", unit: "%" },
    "tokens_per_sec":  { color: "#f472b6", unit: "/s" },
    "tokens_per_watt": { color: "#f59e0b", unit: "/W" },
  };

  /** Compute derived metric series from raw samples.
   * Tokens/s = delta(tokens_total_snapshot) / delta(ts) between consecutive samples.
   * Tokens/W = (tokens delta / time delta) / avg power over that interval.
   * Both prepend a 0 for the first sample (no delta yet). */
  function computeDerivedSamples(raw: HistorySample[], metric: HistoryMetric): HistorySample[] {
    if (metric !== "tokens_per_sec" && metric !== "tokens_per_watt") return raw;
    if (raw.length < 2) return [];
    const out: HistorySample[] = [];
    for (let i = 1; i < raw.length; i++) {
      const prev = raw[i - 1];
      const cur = raw[i];
      const dt = cur.ts - prev.ts;
      const t0 = prev.tokens_total_snapshot;
      const t1 = cur.tokens_total_snapshot;
      let value: number | null = null;
      if (dt > 0 && t0 != null && t1 != null && t1 >= t0) {
        const tps = (t1 - t0) / dt;
        if (metric === "tokens_per_sec") value = tps;
        else if (cur.power && cur.power > 0) value = tps / cur.power;  // tokens/W = tok/s ÷ W
      }
      // Stamp the value into the metric slot the chart already reads from.
      out.push({ ...cur, [metric]: value } as HistorySample);
    }
    return out;
  }
  const derivedSamples = $derived(computeDerivedSamples(historySamples, historyMetric));

  async function loadHistory() {
    historyLoading = true;
    try {
      const now = Math.floor(Date.now() / 1000);
      const from = now - RANGE_SECONDS[historyRange];
      const step = RANGE_STEP[historyRange] || undefined;
      // Parallel fetch: samples + events overlay + optional historical overlay (offset by N seconds)
      const offset = historyCompareOffset;
      const promises: Promise<any>[] = [
        api.history(from, now, step),
        api.events(from).catch(() => ({ ok: false, events: [] })),
      ];
      if (offset > 0) {
        promises.push(
          api.history(from - offset, now - offset, step).catch(() => ({ ok: false, samples: [] })),
        );
      }
      const results = await Promise.all(promises);
      historySamples = results[0].samples ?? [];
      historyEvents = results[1].events ?? [];
      historyCompare = offset > 0 ? (results[2].samples ?? []) : [];
    } catch (e) {
      toast.emit("✗ " + i18n.t("ts.network_error") + ": " + (e as Error).message, "err");
      historySamples = []; historyEvents = []; historyCompare = [];
    } finally {
      historyLoading = false;
    }
  }
  // Reload when compare toggle flips
  $effect(() => {
    historyCompareOffset;  // dependency
    if (modal.open && modal.section === "history" && historySamples.length > 0) {
      loadHistory();
    }
  });

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
  // Auto-refresh timer
  $effect(() => {
    if (historyTimer) { clearInterval(historyTimer); historyTimer = null; }
    if (historyAutoRefresh && modal.open && modal.section === "history") {
      historyTimer = setInterval(() => loadHistory(), 30_000);
    }
    return () => { if (historyTimer) clearInterval(historyTimer); };
  });

  // ── Module toggles (cycle 139, user feedback) ────────────────────────────
  type ModuleInfo = { key: string; label: string; description: string; enabled: boolean };
  let modulesList = $state<ModuleInfo[] | null>(null);
  let togglingKey = $state<string | null>(null);
  async function loadModules() {
    try {
      const r = await fetch("/api/modules");
      const j = await r.json();
      modulesList = j.modules ?? [];
    } catch { modulesList = []; }
  }
  async function waitForServerBack(timeoutMs = 30000): Promise<boolean> {
    const start = Date.now();
    while (Date.now() - start < timeoutMs) {
      await new Promise(r => setTimeout(r, 800));
      try {
        const r = await fetch("/api/version", { cache: "no-store" });
        if (r.ok) return true;
      } catch {}
    }
    return false;
  }
  async function toggleModule(key: string, enabled: boolean) {
    togglingKey = key;
    try {
      const r = await fetch("/api/modules/toggle", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ key, enabled }),
      });
      const j = await r.json();
      if (!j.ok) {
        toast.show(j.error || "toggle failed", "error");
        togglingKey = null;
        return;
      }
      // Wait for the server to come back up after the auto-restart
      const back = await waitForServerBack();
      if (back) {
        toast.show(i18n.t("services.modules_applied") ?? "Module appliqué", "success");
        await loadModules();
      } else {
        toast.show(i18n.t("services.modules_timeout") ?? "Timeout — recharge la page", "error");
      }
    } catch (e: any) {
      toast.show(e?.message || "toggle failed", "error");
    } finally {
      togglingKey = null;
    }
  }
  $effect(() => {
    if (modal.open && modal.section === "services" && modulesList === null) loadModules();
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

  // ── Update check state ────────────────────────────────────────────────────
  let updateChecking = $state(false);
  let pulling = $state(false);
  let updateStatus = $state<Awaited<ReturnType<typeof api.updateCheck>> | null>(null);

  async function checkUpdate() {
    updateChecking = true;
    try {
      updateStatus = await api.updateCheck();
    } catch (e) {
      toast.emit("✗ " + (e as Error).message, "err");
    } finally {
      updateChecking = false;
    }
  }

  async function pullAndRestart() {
    pulling = true;
    try {
      const r = await api.updatePull();
      if (!r.ok) {
        toast.emit("✗ " + (r.error || r.stderr || "pull failed"), "err");
        if (r.dirty_files?.length) {
          toast.emit("Dirty: " + r.dirty_files.slice(0, 3).join(", "), "err");
        }
        pulling = false;
        return;
      }
      toast.emit("✓ Pulled. Restarting…", "ok");
      try { await api.restart(); } catch {}
      setTimeout(() => location.reload(), 2500);
    } catch (e) {
      toast.emit("✗ " + (e as Error).message, "err");
      pulling = false;
    }
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

  // Sections grouped by usage frequency + category:
  //   🎚️ Tuning (most-used)     : Power Limit, Clocks
  //   📊 Review                  : History, Stats (fan-distribution)
  //   🔔 Notifications           : Alerts
  //   ⚙️ Operations              : Services (restart/stop/snapshot/update), Diagnostics
  //   🧩 Advanced configuration  : Profile (JSON override)
  //   🌐 Preferences / meta      : Language, About
  // Section labels in the sidebar render group separators (see HTML below).
  const sections = [
    { id: "power", group: "tuning", labelKey: "modal.power" as const,
      icon: "M11 21h-1l1-7H7.5c-.58 0-.57-.32-.38-.66.19-.34.05-.08.07-.12C8.48 10.94 10.42 7.54 13 3h1l-1 7h3.5c.49 0 .56.33.47.51l-.07.15C12.96 17.55 11 21 11 21z" },
    { id: "clocks", group: "tuning", labelKey: "modal.clocks" as const,
      icon: "M3 17v2h6v-2H3M3 5v2h10V5H3m10 16v-2h8v-2h-8v-2h-2v6h2M7 9v2H3v2h4v2h2V9H7m14 4v-2H11v2h10m-6-4h2V7h4V5h-4V3h-2v6z" },
    { id: "fancurve", group: "tuning", labelKey: "modal.fancurve" as const,
      icon: "M12,11A1,1 0 0,0 11,12A1,1 0 0,0 12,13A1,1 0 0,0 13,12A1,1 0 0,0 12,11M12.5,2C17,2 17.11,5.57 14.75,6.75C13.76,7.24 13.32,8.29 13.13,9.22C13.61,9.42 14.03,9.73 14.35,10.13C18.05,8.13 22.03,8.92 22.03,12.5C22.03,17 18.46,17.1 17.28,14.73C16.78,13.74 15.72,13.3 14.79,13.11C14.59,13.59 14.28,14 13.87,14.34C15.87,18.04 15.08,22 11.5,22C7,22 6.91,18.42 9.27,17.24C10.25,16.75 10.69,15.71 10.89,14.79C10.4,14.59 9.97,14.27 9.65,13.87C5.95,15.87 2,15.08 2,11.5C2,7 5.56,6.91 6.74,9.27C7.24,10.25 8.29,10.69 9.22,10.88C9.41,10.4 9.73,9.97 10.14,9.65C8.14,5.96 8.91,2 12.5,2Z" },
    // History + Stats removed in cycle 75 — they live as top-level views now.
    { id: "alerts", group: "notify", labelKey: "modal.alerts" as const,
      icon: "M12 22a2.5 2.5 0 0 0 2.45-2H9.55A2.5 2.5 0 0 0 12 22m6-6V11c0-3.07-1.63-5.64-4.5-6.32V4a1.5 1.5 0 0 0-3 0v.68C7.64 5.36 6 7.92 6 11v5l-2 2v1h16v-1l-2-2z" },
    { id: "services", group: "ops", labelKey: "modal.services" as const,
      icon: "M14.06 9L15 9.94 5.92 19H5v-.92L14.06 9m3.6-6c-.25 0-.51.1-.7.29l-1.83 1.83 3.75 3.75 1.83-1.83a.996.996 0 0 0 0-1.41l-2.34-2.34c-.2-.2-.45-.29-.71-.29m-3.6 3.19L3 17.25V21h3.75L17.81 9.94l-3.75-3.75z" },
    { id: "diagnostics", group: "ops", labelKey: "modal.diagnostics" as const,
      icon: "M14.6 16.6L19.2 12L14.6 7.4L16 6l6 6-6 6-1.4-1.4M9.4 16.6L4.8 12l4.6-4.6L8 6l-6 6 6 6 1.4-1.4z" },
    { id: "integrations", group: "ops", labelKey: "modal.integrations" as const,
      icon: "M12 2a10 10 0 1 0 10 10A10 10 0 0 0 12 2zm-1 17.93A8 8 0 0 1 4.07 13H7v1a2 2 0 0 0 2 2h1zm6.9-2.54A2 2 0 0 0 16 16h-1v-3a1 1 0 0 0-1-1H8v-2h2a1 1 0 0 0 1-1V7h2a2 2 0 0 0 2-2v-.41a8 8 0 0 1 2.9 12.8z" },
    { id: "profile", group: "advanced", labelKey: "modal.profile" as const,
      icon: "M14.06 9.02l.92.92L5.92 19H5v-.92l9.06-9.06M17.66 3c-.25 0-.51.1-.7.29l-1.83 1.83 3.75 3.75 1.83-1.83c.39-.39.39-1.04 0-1.41l-2.34-2.34c-.2-.2-.45-.29-.71-.29m-3.6 3.19L3 17.25V21h3.75L17.81 9.94l-3.75-3.75z" },
    { id: "apptriggers", group: "advanced", labelKey: "modal.apptriggers" as const,
      icon: "M4 2h16a2 2 0 0 1 2 2v4H2V4a2 2 0 0 1 2-2zm0 8h6v10H4a2 2 0 0 1-2-2V10zm8 0h10v8a2 2 0 0 1-2 2h-8V10z" },
    { id: "benchmark", group: "advanced", labelKey: "modal.benchmark" as const,
      icon: "M9 11H7v6h2v-6m4 0h-2v6h2v-6m4 0h-2v6h2v-6M3 3v18h18V3H3m16 16H5V5h14v14z" },
    { id: "layout", group: "meta", labelKey: "modal.layout" as const,
      icon: "M3 5v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2zm16 0v3H5V5zm-7 5v9H5v-9zm2 0h5v9h-5z" },
    { id: "language", group: "meta", labelKey: "modal.language" as const,
      icon: "M12.87 15.07l-2.54-2.51.03-.03c1.74-1.94 2.98-4.17 3.71-6.53H17V4h-7V2H8v2H1v1.99h11.17C11.5 7.92 10.44 9.75 9 11.35 8.07 10.32 7.3 9.19 6.69 8h-2c.73 1.63 1.73 3.17 2.98 4.56l-5.09 5.02L4 19l5-5 3.11 3.11.76-2.04zM18.5 10h-2L12 22h2l1.12-3h4.75L21 22h2l-4.5-12zm-2.62 7l1.62-4.33L19.12 17h-3.24z" },
    { id: "about", group: "meta", labelKey: "modal.about" as const,
      icon: "M13 9h-2V7h2m0 10h-2v-6h2m-1-9A10 10 0 0 0 2 12a10 10 0 0 0 10 10 10 10 0 0 0 10-10A10 10 0 0 0 12 2z" },
  ];

  /** Look up an icon by section id — safer than `sections[N]` (which breaks
   * whenever the array changes). */
  function iconOf(id: string): string {
    return sections.find(s => s.id === id)?.icon ?? "";
  }

  // Group headers (only shown on desktop sidebar)
  const GROUP_LABELS: Record<string, string> = {
    tuning: "modal.group_tuning",
    review: "modal.group_review",
    notify: "modal.group_notify",
    ops: "modal.group_ops",
    advanced: "modal.group_advanced",
    meta: "modal.group_meta",
  };

  // ── Profile editor state ──────────────────────────────────────────────────
  let profileText = $state("");
  let profileSaving = $state(false);
  $effect(() => {
    if (modal.open && modal.section === "profile" && !profileText) {
      profileText = JSON.stringify(live.data?.profile ?? {}, null, 2);
    }
  });
  function formatProfileJson() {
    try {
      profileText = JSON.stringify(JSON.parse(profileText), null, 2);
    } catch (e) {
      toast.emit("✗ " + i18n.t("profile.invalid_json") + ": " + (e as Error).message, "err");
    }
  }
  function resetProfile() {
    profileText = JSON.stringify(live.data?.profile ?? {}, null, 2);
  }
  async function saveProfile() {
    let parsed: object;
    try { parsed = JSON.parse(profileText); }
    catch (e) {
      toast.emit("✗ " + i18n.t("profile.invalid_json") + ": " + (e as Error).message, "err");
      return;
    }
    profileSaving = true;
    try {
      const r = await api.profileSave(parsed);
      if (r.ok) {
        toast.emit("✓ " + i18n.t("profile.saved", { path: r.path || "?" }), "ok");
      } else {
        toast.emit("✗ " + i18n.t("profile.schema_violation") + ": " + (r.error || "?"), "err");
      }
    } catch (e) {
      toast.emit("✗ " + (e as Error).message, "err");
    } finally {
      profileSaving = false;
    }
  }

  // ── Diagnostics / logs state ──────────────────────────────────────────────
  let logTail = $state(100);
  let logsData = $state<Awaited<ReturnType<typeof api.logs>> | null>(null);
  let logsLoading = $state(false);
  async function loadLogs() {
    logsLoading = true;
    try { logsData = await api.logs(logTail); } catch { logsData = null; }
    finally { logsLoading = false; }
  }
  $effect(() => {
    if (modal.open && modal.section === "diagnostics" && !logsData) loadLogs();
  });

  // ── R&D #12 UI sprint — Integrations section ─────────────────────────────
  // Watchdog
  let wdLoading = $state(false);
  let wdInstalled = $state(false);
  let wdActive = $state(false);
  let wdStrict = $state(false);
  let wdInterval = $state(60);
  async function loadWatchdog() {
    wdLoading = true;
    try {
      const r = await api.watchdogStatus();
      wdInstalled = r.installed;
      wdActive = r.active;
    } catch (e) {
      toast.emit("✗ " + (e as Error).message, "err");
    } finally { wdLoading = false; }
  }
  async function watchdogEnable() {
    wdLoading = true;
    try {
      const r = await api.watchdogEnable({ strict: wdStrict, interval_s: wdInterval });
      if (r.ok) {
        wdInstalled = r.installed;
        wdActive = r.active;
        toast.emit("✓ Watchdog enabled", "ok");
      } else {
        toast.emit("✗ " + (r.msg || "enable failed"), "err");
      }
    } catch (e) {
      toast.emit("✗ " + (e as Error).message, "err");
    } finally { wdLoading = false; }
  }
  async function watchdogDisable() {
    wdLoading = true;
    try {
      const r = await api.watchdogDisable();
      if (r.ok) {
        wdInstalled = r.installed;
        wdActive = r.active;
        toast.emit("✓ Watchdog disabled", "ok");
      } else {
        toast.emit("✗ " + (r.msg || "disable failed"), "err");
      }
    } catch (e) {
      toast.emit("✗ " + (e as Error).message, "err");
    } finally { wdLoading = false; }
  }

  // Service discovery
  let svcLoading = $state(false);
  let services = $state<Awaited<ReturnType<typeof api.servicesDiscovered>>["services"]>([]);
  let unknownListeners = $state<Awaited<ReturnType<typeof api.servicesDiscovered>>["unknown_listeners"]>([]);
  async function loadServices() {
    svcLoading = true;
    try {
      const r = await api.servicesDiscovered();
      services = r.services ?? [];
      unknownListeners = r.unknown_listeners ?? [];
    } catch (e) {
      toast.emit("✗ " + (e as Error).message, "err");
    } finally { svcLoading = false; }
  }

  // HF Janitor
  let hfLoading = $state(false);
  let hfStats = $state<Awaited<ReturnType<typeof api.hfJanitor>> | null>(null);
  async function loadHFJanitor() {
    hfLoading = true;
    try {
      hfStats = await api.hfJanitor(20);
    } catch (e) {
      toast.emit("✗ " + (e as Error).message, "err");
    } finally { hfLoading = false; }
  }

  // Notif Hub (R&D #6.1)
  let notifLoading = $state(false);
  let notifChannels = $state<Record<string, any>[]>([]);
  let notifTypes = $state<string[]>([]);
  let notifEditing = $state<Record<string, any> | null>(null);
  let notifBusy = $state(false);
  async function loadNotifChannels() {
    notifLoading = true;
    try {
      const r = await api.notifChannelsList();
      notifChannels = r.channels ?? [];
      notifTypes = r.types_supported ?? [];
    } catch (e) {
      toast.emit("✗ " + (e as Error).message, "err");
    } finally { notifLoading = false; }
  }
  function notifStartNew() {
    notifEditing = {
      id: "channel-" + Math.random().toString(36).slice(2, 8),
      type: notifTypes[0] || "discord",
      name: "",
      enabled: true,
      min_level: "warn",
      url: "",
      token: "",
    };
  }
  async function notifSave() {
    if (!notifEditing) return;
    notifBusy = true;
    try {
      const r = await api.notifChannelSave(notifEditing);
      if (r.ok) {
        toast.emit("✓ Channel saved", "ok");
        notifEditing = null;
        await loadNotifChannels();
      } else {
        toast.emit("✗ " + (r.error || "save failed"), "err");
      }
    } catch (e) {
      toast.emit("✗ " + (e as Error).message, "err");
    } finally { notifBusy = false; }
  }
  async function notifTest() {
    if (!notifEditing) return;
    notifBusy = true;
    try {
      const r = await api.notifChannelTest(notifEditing);
      toast.emit((r.ok ? "✓ " : "✗ ") + (r.msg || ""), r.ok ? "ok" : "err");
    } catch (e) {
      toast.emit("✗ " + (e as Error).message, "err");
    } finally { notifBusy = false; }
  }
  async function notifDelete(id: string) {
    if (!confirm(`Delete channel ${id}?`)) return;
    try {
      await api.notifChannelDelete(id);
      toast.emit("✓ Channel deleted", "ok");
      await loadNotifChannels();
    } catch (e) {
      toast.emit("✗ " + (e as Error).message, "err");
    }
  }

  // Auth tokens (R&D #9.3)
  let authLoading = $state(false);
  let authTokens = $state<Array<{ id: string; name: string; scope: string;
                                    created_ts: number; expires_ts: number | null }>>([]);
  let authNewName = $state("");
  let authNewScope = $state<"read"|"write"|"admin">("read");
  let authNewTtlDays = $state<number | "">("");
  let authJustCreatedSecret = $state<string | null>(null);
  let authBusy = $state(false);

  // Share-link generator
  let shareScope = $state<"read"|"write"|"admin">("read");
  let shareTtlHours = $state(24);
  let shareSub = $state("shared");
  let shareGeneratedToken = $state<string | null>(null);

  async function loadAuthTokens() {
    authLoading = true;
    try {
      const r = await api.authTokensList();
      authTokens = r.tokens ?? [];
    } catch (e) {
      toast.emit("✗ " + (e as Error).message, "err");
    } finally { authLoading = false; }
  }
  async function authCreate() {
    if (!authNewName.trim()) {
      toast.emit("✗ name required", "err");
      return;
    }
    authBusy = true;
    try {
      const ttl = authNewTtlDays === "" ? null : Math.max(1, Number(authNewTtlDays)) * 86400;
      const r = await api.authTokenCreate({
        name: authNewName.trim(),
        scope: authNewScope,
        ttl_s: ttl,
      });
      if (r.ok && r.secret) {
        authJustCreatedSecret = r.secret;
        toast.emit("✓ Token created", "ok");
        authNewName = "";
        await loadAuthTokens();
      } else {
        toast.emit("✗ " + (r.error || "create failed"), "err");
      }
    } catch (e) {
      toast.emit("✗ " + (e as Error).message, "err");
    } finally { authBusy = false; }
  }
  async function authDelete(id: string) {
    if (!confirm(`Revoke token ${id}?`)) return;
    try {
      await api.authTokenDelete(id);
      toast.emit("✓ Token revoked", "ok");
      await loadAuthTokens();
    } catch (e) {
      toast.emit("✗ " + (e as Error).message, "err");
    }
  }
  async function shareMake() {
    try {
      const r = await api.authShareCreate({
        scope: shareScope,
        ttl_s: shareTtlHours * 3600,
        sub: shareSub,
      });
      if (r.ok && r.share_token) {
        shareGeneratedToken = r.share_token;
        toast.emit("✓ Share-link generated", "ok");
      } else {
        toast.emit("✗ " + (r.error || "failed"), "err");
      }
    } catch (e) {
      toast.emit("✗ " + (e as Error).message, "err");
    }
  }
  function copyToClipboard(text: string) {
    navigator.clipboard?.writeText(text).then(
      () => toast.emit("✓ Copied", "ok"),
      () => toast.emit("✗ Clipboard error", "err"),
    );
  }

  // ── UI sprint cycle 3 — R&D #12 cards ────────────────────────────────────
  let diskLoading = $state(false);
  let diskStats = $state<Awaited<ReturnType<typeof api.diskHealth>> | null>(null);
  async function loadDiskHealth() {
    diskLoading = true;
    try {
      diskStats = await api.diskHealth();
    } catch (e) {
      toast.emit("✗ " + (e as Error).message, "err");
    } finally { diskLoading = false; }
  }

  let airgapStat = $state<Awaited<ReturnType<typeof api.airgapStatus>> | null>(null);
  let airgapAudit = $state<Array<{ts: number; url: string; reason: string}> | null>(null);
  async function loadAirgapStatus() {
    try { airgapStat = await api.airgapStatus(); } catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadAirgapAudit() {
    try {
      const r = await api.airgapAudit(50);
      airgapAudit = r.blocked;
    } catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }

  let wallMeter = $state<Awaited<ReturnType<typeof api.wallMeter>> | null>(null);
  async function loadWallMeter() {
    try { wallMeter = await api.wallMeter(); } catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }

  let peersData = $state<Awaited<ReturnType<typeof api.peers>> | null>(null);
  async function loadPeers() {
    try { peersData = await api.peers(); } catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }

  function _verdictColor(kind?: string): string {
    if (kind === "ok") return "var(--ok)";
    if (kind === "warn") return "var(--warn)";
    if (kind === "fail") return "var(--err)";
    return "var(--text-dim)";
  }

  // ── UI sprint cycle 4 — R&D #13 features ────────────────────────────────
  let wizardLoading = $state(false);
  let wizardData = $state<Awaited<ReturnType<typeof api.hotGpuWizard>> | null>(null);
  async function runHotGpuWizard() {
    wizardLoading = true;
    try { wizardData = await api.hotGpuWizard(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
    finally { wizardLoading = false; }
  }

  let vramQuotaData = $state<Awaited<ReturnType<typeof api.vramQuotaStatus>> | null>(null);
  let vramEvalLoading = $state(false);
  async function loadVramQuota() {
    try { vramQuotaData = await api.vramQuotaStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function evalVramQuota() {
    vramEvalLoading = true;
    try {
      const r = await api.vramQuotaEvaluate(true);
      toast.emit(`✓ Evaluated — ${r.fires.length} breach(es)`, "ok");
      await loadVramQuota();  // reload audit
    } catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
    finally { vramEvalLoading = false; }
  }

  let carbonData = $state<Awaited<ReturnType<typeof api.carbon>> | null>(null);
  async function loadCarbon() {
    try { carbonData = await api.carbon(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }

  let bestGpuData = $state<Awaited<ReturnType<typeof api.bestGpu>> | null>(null);
  async function loadBestGpu() {
    try { bestGpuData = await api.bestGpu(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }

  // ── UI sprint cycle 5 — R&D #14 features ────────────────────────────────
  let xidData = $state<Awaited<ReturnType<typeof api.xidEvents>> | null>(null);
  async function loadXid() {
    try { xidData = await api.xidEvents("24h"); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }

  let hotSwapData = $state<Awaited<ReturnType<typeof api.hotSwapStatus>> | null>(null);
  async function loadHotSwap() {
    try { hotSwapData = await api.hotSwapStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }

  let costData = $state<Awaited<ReturnType<typeof api.inferenceCost>> | null>(null);
  async function loadInferenceCost() {
    try { costData = await api.inferenceCost(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }

  let labUsageData = $state<Awaited<ReturnType<typeof api.labUsageLive>> | null>(null);
  async function loadLabUsage() {
    try { labUsageData = await api.labUsageLive(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }

  function _windowLabel(seconds: number): string {
    if (seconds < 60) return `${seconds}s`;
    if (seconds < 3600) return `${seconds / 60}m`;
    if (seconds < 86400) return `${seconds / 3600}h`;
    return `${seconds / 86400}d`;
  }

  // ── UI sprint cycle 6 — R&D #15 features ────────────────────────────────
  let bootProfileData = $state<Awaited<ReturnType<typeof api.bootProfileStatus>> | null>(null);
  let bootProfileForm = $state<{ name: string; power_limit_w: number; persistence_mode: boolean }>({
    name: "", power_limit_w: 250, persistence_mode: true,
  });
  let bootSaving = $state(false);
  async function loadBootProfile() {
    try {
      bootProfileData = await api.bootProfileStatus();
      // Pre-fill the form if a profile is loaded
      if (bootProfileData?.profile) {
        bootProfileForm.name = bootProfileData.profile.name || "";
        bootProfileForm.power_limit_w = bootProfileData.profile.power_limit_w ?? 250;
        bootProfileForm.persistence_mode = bootProfileData.profile.persistence_mode ?? true;
      }
    } catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function saveBootProfile() {
    if (!bootProfileForm.name.trim()) {
      toast.emit("✗ name required", "err");
      return;
    }
    bootSaving = true;
    try {
      const r = await api.bootProfileSave({ ...bootProfileForm });
      if (r.ok) {
        toast.emit("✓ Boot profile saved", "ok");
        await loadBootProfile();
      } else {
        toast.emit("✗ " + (r.error || "save failed"), "err");
      }
    } catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
    finally { bootSaving = false; }
  }
  async function clearBootProfile() {
    if (!confirm("Clear boot profile?")) return;
    try {
      await api.bootProfileClear();
      bootProfileData = null;
      toast.emit("✓ Cleared", "ok");
      await loadBootProfile();
    } catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function applyBootProfileNow() {
    try {
      const r = await api.bootProfileApplyNow();
      toast.emit(r.ok ? "✓ Applied" : "✗ Apply failed (see logs)",
                 r.ok ? "ok" : "err");
      await loadBootProfile();
    } catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }

  let tariffData = $state<Awaited<ReturnType<typeof api.tariffStatus>> | null>(null);
  let cheapestData = $state<Awaited<ReturnType<typeof api.tariffCheapest>> | null>(null);
  async function loadTariff() {
    try {
      tariffData = await api.tariffStatus();
      if (tariffData?.available) {
        // Default : 300W × 4h
        cheapestData = await api.tariffCheapest(300, 4 * 3600);
      }
    } catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }

  let dedupData = $state<Awaited<ReturnType<typeof api.hfDedupPlan>> | null>(null);
  let dedupScanning = $state(false);
  let dedupExecuting = $state(false);
  async function scanDedup() {
    dedupScanning = true;
    try { dedupData = await api.hfDedupPlan(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
    finally { dedupScanning = false; }
  }
  async function executeDedup(dryRun: boolean) {
    if (!dedupData || !dedupData.plan || dedupData.plan.length === 0) return;
    if (!dryRun && !confirm("Live dedup ! Files will be replaced with hardlinks. Proceed?")) return;
    dedupExecuting = true;
    try {
      const r = await api.hfDedupExecute(dedupData.plan, dryRun);
      const tag = dryRun ? "dry-run" : "LIVE";
      toast.emit(`✓ ${tag} : ${r.applied} ops · ${r.reclaim_mib.toFixed(1)} MiB`, "ok");
    } catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
    finally { dedupExecuting = false; }
  }

  let discordData = $state<Awaited<ReturnType<typeof api.discordRpcStatus>> | null>(null);
  async function loadDiscordRpc() {
    try { discordData = await api.discordRpcStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }

  // ── UI sprint cycle 7 — R&D #16 features ────────────────────────────────
  let drBundles = $state<Awaited<ReturnType<typeof api.drBundleList>>["bundles"] | null>(null);
  let drBuilding = $state(false);
  async function loadDrBundles() {
    try { const r = await api.drBundleList(); drBundles = r.bundles; }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function buildDrBundle() {
    drBuilding = true;
    try {
      const r = await api.drBundleBuild();
      if (r.ok) {
        toast.emit(`✓ Bundle built · ${((r.size_bytes ?? 0) / 1024).toFixed(1)} KiB · ${r.file_count} files`, "ok");
        await loadDrBundles();
      } else {
        toast.emit("✗ " + (r.error || "build failed"), "err");
      }
    } catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
    finally { drBuilding = false; }
  }
  async function deleteDrBundle(name: string) {
    if (!confirm(`Delete bundle ${name}?`)) return;
    try {
      await api.drBundleDelete(name);
      toast.emit("✓ Deleted", "ok");
      await loadDrBundles();
    } catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }

  let lmStudioData = $state<Awaited<ReturnType<typeof api.lmStudioInventory>> | null>(null);
  async function loadLmStudio() {
    try { lmStudioData = await api.lmStudioInventory(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }

  let driverVaultData = $state<Awaited<ReturnType<typeof api.driverVaultStatus>> | null>(null);
  let driverVaultScript = $state<string | null>(null);
  async function loadDriverVault() {
    try { driverVaultData = await api.driverVaultStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function stashDriver() {
    try {
      const r = await api.driverVaultStash();
      toast.emit(r.ok ? "✓ Driver stashed" : "✗ " + (r.reason || "stash failed"),
                 r.ok ? "ok" : "err");
      await loadDriverVault();
    } catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function showRollbackScript(name: string) {
    try {
      const r = await api.driverVaultRollbackScript(name);
      if (r.ok && r.script) {
        driverVaultScript = r.script;
      } else {
        toast.emit("✗ " + (r.error || "script generation failed"), "err");
      }
    } catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }

  // ── R&D #17.5 LLM hot-swap orchestrator (UI sprint 8) ────────────────────
  let llmSwapData = $state<Awaited<ReturnType<typeof api.llmSwapStatus>> | null>(null);
  let llmSwapNeededGib = $state<number>(8);
  let llmSwapSuggestion = $state<Awaited<ReturnType<typeof api.llmSwapSuggest>> | null>(null);
  async function loadLlmSwap() {
    try { llmSwapData = await api.llmSwapStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function toggleLlmPin(name: string, isPinned: boolean) {
    try {
      const r = await api.llmSwapPin(name, isPinned ? "unpin" : "pin");
      if (r.ok) {
        toast.emit(isPinned ? "Unpinned" : "📌 Pinned", "ok");
        await loadLlmSwap();
      } else { toast.emit("✗ " + (r.error || "pin failed"), "err"); }
    } catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function previewLlmSwap() {
    const needed = Math.max(1, Math.floor(llmSwapNeededGib * 1024 ** 3));
    try { llmSwapSuggestion = await api.llmSwapSuggest(needed); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }

  // ── R&D #18 (UI sprint 9) ──────────────────────────────────────────────
  let cudaAdvisorData = $state<Awaited<ReturnType<typeof api.cudaAdvisorStatus>> | null>(null);
  let nvmeSwapData    = $state<Awaited<ReturnType<typeof api.nvmeSwapStatus>>    | null>(null);
  let cudaMatrixData  = $state<Awaited<ReturnType<typeof api.cudaMatrixStatus>>  | null>(null);
  let pcieHistData    = $state<Awaited<ReturnType<typeof api.pcieHistogramStatus>> | null>(null);
  async function loadCudaAdvisor() {
    try { cudaAdvisorData = await api.cudaAdvisorStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadNvmeSwap() {
    try { nvmeSwapData = await api.nvmeSwapStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadCudaMatrix() {
    try { cudaMatrixData = await api.cudaMatrixStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadPcieHist() {
    try { pcieHistData = await api.pcieHistogramStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }

  // ── R&D #19 (UI sprint 10) ────────────────────────────────────────────
  let throttleCauseData = $state<Awaited<ReturnType<typeof api.throttleCauseStatus>> | null>(null);
  let mpsHealthData     = $state<Awaited<ReturnType<typeof api.mpsHealthStatus>>     | null>(null);
  let processNiceData   = $state<Awaited<ReturnType<typeof api.processNiceStatus>>   | null>(null);
  let warmupData        = $state<Awaited<ReturnType<typeof api.warmupProfileStatus>> | null>(null);
  let warmupProbing     = $state<boolean>(false);
  async function loadThrottleCause() {
    try { throttleCauseData = await api.throttleCauseStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadMpsHealth() {
    try { mpsHealthData = await api.mpsHealthStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadProcessNice() {
    try { processNiceData = await api.processNiceStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadWarmup() {
    try { warmupData = await api.warmupProfileStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function fireWarmupProbe() {
    warmupProbing = true;
    try {
      const r = await api.warmupProfileProbe({
        model: "Qwen3.5-35B", source: "llamacpp",
        host: "localhost", port: 8080, prompt: "Hi",
      });
      if (r.ok) {
        toast.emit(`✓ TTFT ${r.ttft_ms?.toFixed(0)} ms`, "ok");
        await loadWarmup();
      } else {
        toast.emit("✗ " + (r.error || "probe failed"), "err");
      }
    } catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
    finally { warmupProbing = false; }
  }

  // Auto-load each card the first time the section is opened
  $effect(() => {
    if (modal.open && modal.section === "integrations") {
      if (!wdLoading) loadWatchdog();
      if (services.length === 0 && !svcLoading) loadServices();
      if (notifChannels.length === 0 && !notifLoading) loadNotifChannels();
      if (authTokens.length === 0 && !authLoading) loadAuthTokens();
      if (!diskStats && !diskLoading) loadDiskHealth();
      if (!airgapStat) loadAirgapStatus();
      if (!wallMeter) loadWallMeter();
      if (!peersData) loadPeers();
      // R&D #13 cards
      if (!vramQuotaData) loadVramQuota();
      if (!carbonData) loadCarbon();
      if (!bestGpuData) loadBestGpu();
      // R&D #14 cards
      if (!xidData) loadXid();
      if (!hotSwapData) loadHotSwap();
      if (!costData) loadInferenceCost();
      if (!labUsageData) loadLabUsage();
      // R&D #15 cards
      if (!bootProfileData) loadBootProfile();
      if (!tariffData) loadTariff();
      if (!discordData) loadDiscordRpc();
      // R&D #16 cards
      if (!drBundles) loadDrBundles();
      if (!lmStudioData) loadLmStudio();
      if (!driverVaultData) loadDriverVault();
      // R&D #17 cards
      if (!llmSwapData) loadLlmSwap();
      // R&D #18 cards
      if (!cudaAdvisorData) loadCudaAdvisor();
      if (!nvmeSwapData)    loadNvmeSwap();
      if (!cudaMatrixData)  loadCudaMatrix();
      if (!pcieHistData)    loadPcieHist();
      // R&D #19 cards
      if (!throttleCauseData) loadThrottleCause();
      if (!mpsHealthData)     loadMpsHealth();
      if (!processNiceData)   loadProcessNice();
      if (!warmupData)        loadWarmup();
      // Dedup is on-demand only (scan is expensive)
    }
  });

  // ── About state ───────────────────────────────────────────────────────────
  // About is now lean — perf totals moved to Stats page in cycle 115.
  // Lifetime records added back in cycle 130 (small + meta-info, fits About).
  let aboutData = $state<Awaited<ReturnType<typeof api.about>> | null>(null);
  let idleAudit = $state<Awaited<ReturnType<typeof api.idleAudit>> | null>(null);
  async function loadIdleAudit() {
    try { idleAudit = await api.idleAudit(); } catch { idleAudit = null; }
  }
  let eccHealth = $state<Awaited<ReturnType<typeof api.eccHealth>> | null>(null);
  async function loadEccHealth() {
    try { eccHealth = await api.eccHealth(); } catch { eccHealth = null; }
  }
  let driftInfo = $state<Awaited<ReturnType<typeof api.drift>> | null>(null);
  async function loadDrift() {
    try { driftInfo = await api.drift(); } catch { driftInfo = null; }
  }
  let lifetimeStats = $state<Awaited<ReturnType<typeof api.lifetimeStats>> | null>(null);
  async function loadAbout() {
    try { aboutData = await api.about(); } catch { aboutData = null; }
    try { lifetimeStats = await api.lifetimeStats(); } catch { lifetimeStats = null; }
  }
  $effect(() => {
    if (modal.open && modal.section === "about" && !aboutData) loadAbout();
    if (modal.open && modal.section === "about") loadIdleAudit();
    if (modal.open && modal.section === "about") loadEccHealth();
    if (modal.open && modal.section === "about") loadDrift();
  });

  function fmtTrackingSince(ts: number | null): string {
    if (!ts) return "";
    const days = Math.floor((Date.now() / 1000 - ts) / 86400);
    if (days < 1) return "today";
    if (days < 31) return `${days}d`;
    if (days < 365) return `${Math.floor(days / 30)} mo`;
    return `${(days / 365).toFixed(1)} y`;
  }

  // ── App triggers state (R&D #1, cycle 118) ────────────────────────────
  type TrigEntry = { app: string; profile: string };
  let triggerEntries = $state<TrigEntry[]>([]);
  let triggersLoaded = $state(false);
  let triggersSaving = $state(false);
  async function loadAppTriggers() {
    try {
      const r = await api.getAppTriggers();
      triggerEntries = Object.entries(r.triggers || {}).map(([app, profile]) => ({ app, profile }));
    } catch {
      triggerEntries = [];
    }
    triggersLoaded = true;
  }
  function addTrigger() {
    triggerEntries = [...triggerEntries, { app: "", profile: "boost" }];
  }
  function removeTrigger(i: number) {
    triggerEntries = triggerEntries.filter((_, idx) => idx !== i);
  }
  async function saveTriggers() {
    triggersSaving = true;
    const dict: Record<string, string> = {};
    for (const t of triggerEntries) {
      const k = t.app.trim();
      if (k) dict[k] = t.profile;
    }
    try {
      const r = await api.setAppTriggers(dict);
      if (r.ok) toast.show(i18n.t("apptriggers.saved"), "success");
      else toast.show(r.error || "save failed", "error");
    } catch (e: any) {
      toast.show(e?.message || "save failed", "error");
    } finally {
      triggersSaving = false;
    }
  }
  $effect(() => {
    if (modal.open && modal.section === "apptriggers" && !triggersLoaded) loadAppTriggers();
  });

  // ── Benchmark A/B state (R&D #4.2, cycle 123) ─────────────────────────
  let benchA = $state("silent");
  let benchB = $state("boost");
  let benchDuration = $state(30);
  let benchRunning = $state(false);
  let benchResult = $state<Awaited<ReturnType<typeof api.runBenchmark>> | null>(null);
  async function runBench() {
    benchRunning = true;
    benchResult = null;
    try {
      const r = await api.runBenchmark({ profileA: benchA, profileB: benchB, durationS: benchDuration });
      if (r.ok) benchResult = r;
      else toast.show(r.error || "benchmark failed", "error");
    } catch (e: any) {
      toast.show(e?.message || "benchmark failed", "error");
    } finally {
      benchRunning = false;
    }
  }
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
      {#each sections as s, i}
        {#if i === 0 || sections[i - 1].group !== s.group}
          <div class="sidebar-group">{i18n.t(GROUP_LABELS[s.group] as any)}</div>
        {/if}
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
          <svg class="icon" viewBox="0 0 24 24" fill="currentColor"><path d={iconOf("power")} /></svg>
          <span>{i18n.t("power.section_title")}</span>
        </h3>
        <p class="sub" style="margin:0 0 1em">{i18n.t("power.description")}</p>

        <!-- One-click profiles -->
        <h3 style="margin:0 0 .4em;color:#cdd2da;font-size:.92em;font-weight:600">
          {i18n.t("power.profiles_title")}
        </h3>
        <p class="sub" style="margin:0 0 .6em;font-size:.8em">{i18n.t("power.profiles_description")}</p>
        <div class="btn-row" style="margin-bottom:1.2em">
          {#each powerProfiles as p}
            <button class="btn" onclick={() => applyPowerProfile(p.name)} disabled={applyingProfile}>
              {i18n.t(("power.profile_" + p.name) as any)}
              <span class="sub" style="font-size:.78em;margin-left:.4em">{p.watts}W · +{p.gpu_offset}/+{p.mem_offset}</span>
            </button>
          {/each}
        </div>
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
          <svg class="icon" viewBox="0 0 24 24" fill="currentColor"><path d={iconOf("clocks")} /></svg>
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

      <!-- Stats section removed in cycle 75 — now lives as top-level view (StatsView.svelte) -->

      <!-- Fan curve (slice 1/8 : visualization only) -->
      <div class="modal-section" class:active={modal.section === "fancurve"}>
        <h3 class="title">
          <svg class="icon" viewBox="0 0 24 24" fill="currentColor"><path d={iconOf("fancurve")} /></svg>
          <span>{i18n.t("modal.fancurve")}</span>
        </h3>
        <FanCurveEditor />
      </div>

      <!-- Services -->
      <div class="modal-section" class:active={modal.section === "services"}>
        <h3 class="title">
          <svg class="icon" viewBox="0 0 24 24" fill="currentColor"><path d={iconOf("services")} /></svg>
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

        <!-- ─── Modules (cycle 139, user feedback) ─────────────────── -->
        <h3 style="margin-top:1.8em;color:#cdd2da;font-size:.95em;font-weight:600">
          🧩 {i18n.t("services.modules_label") ?? "Modules optionnels"}
        </h3>
        <p class="sub">{i18n.t("services.modules_description") ?? "Active/désactive un module — le service redémarre automatiquement."}</p>
        {#if modulesList === null}
          <p class="sub">{i18n.t("history.loading")}</p>
        {:else}
          <div class="module-list">
            {#each modulesList as m (m.key)}
              <label class="module-row">
                <input
                  type="checkbox"
                  checked={m.enabled}
                  disabled={togglingKey === m.key}
                  onchange={(e) => toggleModule(m.key, (e.target as HTMLInputElement).checked)}
                />
                <div class="module-info">
                  <span class="module-label">{m.label}</span>
                  <span class="sub" style="font-size:.78em">{m.description}</span>
                </div>
                {#if togglingKey === m.key}
                  <span class="sub" style="font-size:.78em">⏳ {i18n.t("services.modules_applying") ?? "Redémarrage..."}</span>
                {/if}
              </label>
            {/each}
          </div>
        {/if}

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

        <h3 style="margin-top:1.8em;color:#cdd2da;font-size:.95em;font-weight:600">
          {i18n.t("services.snapshot_label")}
        </h3>
        <p class="sub">{i18n.t("services.snapshot_description")}</p>
        <div class="btn-row" style="margin-top:.8em">
          <button class="btn" onclick={() => window.location.href = api.snapshotUrl()}>
            📦 {i18n.t("services.snapshot_btn")}
          </button>
        </div>

        <p class="sub" style="margin-top:1.4em;font-size:.78em">
          💡 {i18n.t("services.update_moved_hint") ?? "Mise à jour : voir l'onglet À propos."}
        </p>
      </div>

      <!-- Alerts -->
      <div class="modal-section" class:active={modal.section === "alerts"}>
        <h3 class="title">
          <svg class="icon" viewBox="0 0 24 24" fill="currentColor"><path d={iconOf("alerts")} /></svg>
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

          <label class="form-row" style="margin-top:1.4em;cursor:pointer">
            <span class="form-lbl">🔊 {i18n.t("alerts.sound_label").split(" ")[0]}</span>
            <span class="form-val">
              <input type="checkbox" bind:checked={soundEnabled} onchange={onSoundToggle} />
              {i18n.t("alerts.sound_label")}
            </span>
          </label>
          <p class="sub" style="margin:.2em 0 0 110px;font-size:.78em">{i18n.t("alerts.sound_hint")}</p>

          <h3 style="margin-top:1.8em;color:var(--text-muted);font-size:.92em;font-weight:600">
            🔔 {i18n.t("push.title")}
          </h3>
          <p class="sub" style="margin:0 0 .6em;font-size:.82em">{i18n.t("push.description")}</p>
          {#if push.state === "unsupported"}
            <p class="sub" style="color:var(--accent-warn)">{i18n.t("push.unsupported")}</p>
          {:else if push.state === "denied"}
            <p class="sub" style="color:var(--accent-bad)">{i18n.t("push.denied")}</p>
          {:else if push.state === "granted-subbed"}
            <div class="btn-row">
              <span style="color:var(--accent)">✓ {i18n.t("push.active")}</span>
              <button class="btn" onclick={() => push.unsubscribe()}>{i18n.t("push.disable")}</button>
            </div>
          {:else}
            <div class="btn-row">
              <button class="btn btn-primary" onclick={() => push.subscribe()}>{i18n.t("push.enable")}</button>
              <span class="sub" style="font-size:.78em">{i18n.t("push.enable_hint")}</span>
            </div>
          {/if}
          {#if push.error}
            <p class="sub" style="color:var(--accent-bad);font-size:.78em">{push.error}</p>
          {/if}
        </div>
      </div>

      <!-- History section removed in cycle 75 — now lives as top-level view (HistoryView.svelte) -->

      <!-- About -->
      <div class="modal-section" class:active={modal.section === "about"}>
        <h3 class="title">
          <svg class="icon" viewBox="0 0 24 24" fill="currentColor"><path d={iconOf("about")} /></svg>
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
              {#if aboutData.vbios_version}
                <tr><td>{i18n.t("about.vbios")}</td><td><code>{aboutData.vbios_version}</code></td></tr>
              {/if}
              <tr><td>{i18n.t("about.config_path")}</td><td><code>{aboutData.config_path}</code></td></tr>
              <tr><td>{i18n.t("about.storage_path")}</td><td><code>{aboutData.storage_path}</code></td></tr>
              <tr><td>{i18n.t("about.license")}</td><td>{aboutData.license}</td></tr>
              <tr><td>{i18n.t("about.repo")}</td>
                <td><a href={aboutData.repo_url} target="_blank" rel="noopener" style="color:#4ade80">{aboutData.repo_url}</a></td>
              </tr>
            </tbody>
          </table>

          {#if lifetimeStats && lifetimeStats.samples_count > 0}
            <h3 style="margin-top:1.6em;color:var(--text-muted);font-size:.95em;font-weight:600">
              🏆 {i18n.t("about.lifetime_records") ?? "Lifetime records"}
            </h3>
            <table class="about-table" style="margin-top:.4em;max-width:380px">
              <tbody>
                {#if lifetimeStats.peak_temp_c != null}
                  <tr>
                    <td>🌡️ {i18n.t("about.peak_temp")}</td>
                    <td><b style="color:var(--accent-warn)">{lifetimeStats.peak_temp_c}°C</b></td>
                  </tr>
                {/if}
                {#if lifetimeStats.peak_power_w != null}
                  <tr>
                    <td>⚡ {i18n.t("about.peak_power")}</td>
                    <td><b style="color:var(--accent-cool)">{lifetimeStats.peak_power_w.toFixed(0)} W</b></td>
                  </tr>
                {/if}
                {#if lifetimeStats.peak_fan_pct != null}
                  <tr>
                    <td>🌀 {i18n.t("about.peak_fan")}</td>
                    <td><b>{lifetimeStats.peak_fan_pct}%</b>
                      {#if lifetimeStats.peak_fan_rpm}<span class="sub" style="margin-left:.4em">({lifetimeStats.peak_fan_rpm} RPM)</span>{/if}
                    </td>
                  </tr>
                {/if}
                {#if lifetimeStats.lowest_idle_power_w != null}
                  <tr>
                    <td>💤 {i18n.t("about.lowest_idle")}</td>
                    <td><b style="color:var(--accent)">{lifetimeStats.lowest_idle_power_w.toFixed(1)} W</b></td>
                  </tr>
                {/if}
                <tr>
                  <td>📅 {i18n.t("about.tracking_since")}</td>
                  <td>
                    {fmtTrackingSince(lifetimeStats.first_ts)}
                    <span class="sub" style="margin-left:.4em">({lifetimeStats.samples_count.toLocaleString()} samples)</span>
                  </td>
                </tr>
              </tbody>
            </table>
          {/if}

          <!-- ─── R&D #5.2 Driver/kernel drift detector ─── -->
          {#if driftInfo?.has_baseline && driftInfo.last_drift}
            <h3 style="margin-top:1.6em;color:var(--text-muted);font-size:.95em;font-weight:600">
              🔧 {i18n.t("about.drift") ?? "Drift driver / kernel"}
            </h3>
            <p class="sub" style="margin:0 0 .4em;font-size:.82em;color:var(--accent-warn)">
              ⚠️ {driftInfo.last_drift.diffs.length} {i18n.t("about.drift_changed") ?? "changement(s) depuis le dernier démarrage"} :
            </p>
            <ul style="margin:0;padding-left:1.4em;font-size:.78em">
              {#each driftInfo.last_drift.diffs as d}
                <li>
                  <b>{d.field}</b> :
                  <span style="color:var(--text-dim)">{d.old ?? "—"}</span>
                  →
                  <b style="color:var(--accent-cool)">{d.new ?? "—"}</b>
                </li>
              {/each}
            </ul>
            <p class="sub" style="margin-top:.4em;font-size:.72em;color:var(--text-dim)">
              {driftInfo.history_count} {i18n.t("about.drift_history_count") ?? "drift(s) enregistré(s) au total"}
            </p>
          {:else if driftInfo?.has_baseline}
            <h3 style="margin-top:1.6em;color:var(--text-muted);font-size:.95em;font-weight:600">
              🔧 {i18n.t("about.drift") ?? "Drift driver / kernel"}
            </h3>
            <p class="sub" style="margin:0;font-size:.82em;color:var(--accent)">
              ✓ {i18n.t("about.drift_none") ?? "Aucun changement depuis la baseline initiale."}
            </p>
          {/if}

          <!-- ─── R&D #4.3 ECC + memory health ─── -->
          {#if eccHealth?.available}
            <h3 style="margin-top:1.6em;color:var(--text-muted);font-size:.95em;font-weight:600">
              💾 {i18n.t("about.ecc_health") ?? "Santé mémoire (ECC)"}
            </h3>
            {#if eccHealth.verdict_kind === "ok"}
              <p class="sub" style="margin:0;font-size:.82em;color:var(--accent)">
                ✓ {eccHealth.verdict_msg}
              </p>
            {:else if eccHealth.verdict_kind === "watch"}
              <p class="sub" style="margin:0;font-size:.82em;color:var(--accent-warn)">
                ⚠️ {eccHealth.verdict_msg}
              </p>
            {:else if eccHealth.verdict_kind === "failing"}
              <p class="sub" style="margin:0;font-size:.82em;color:var(--accent-bad)">
                🚨 {eccHealth.verdict_msg}
              </p>
            {/if}
            <div class="sub" style="margin-top:.4em;font-size:.74em;display:grid;grid-template-columns:auto auto;gap:.2em .8em">
              <span>ECC mode :</span><b>{eccHealth.ecc_mode ?? "—"}</b>
              <span>Corrigées (total) :</span><b>{eccHealth.corrected_total ?? "—"}</b>
              <span>Non-corrigées (total) :</span><b>{eccHealth.uncorrected_total ?? "—"}</b>
              <span>Rows remapped (corr/uncorr) :</span><b>{eccHealth.remapped_correctable ?? "—"} / {eccHealth.remapped_uncorrectable ?? "—"}</b>
              <span>Rows pending / failed :</span><b>{eccHealth.remapped_pending ?? "—"} / {eccHealth.remapped_failure ?? "—"}</b>
            </div>
          {/if}

          <!-- ─── R&D #4.5 Idle-state audit ─── -->
          {#if idleAudit?.available && idleAudit.status !== "unknown"}
            <h3 style="margin-top:1.6em;color:var(--text-muted);font-size:.95em;font-weight:600">
              🛌 {i18n.t("about.idle_audit") ?? "Audit veille (idle)"}
            </h3>
            {#if idleAudit.status === "busy"}
              <p class="sub" style="margin:0;font-size:.82em">
                ⏳ {idleAudit.verdict} ({idleAudit.util_gpu}% util · {idleAudit.power?.toFixed(1)} W)
              </p>
            {:else if idleAudit.verdict_kind === "ok"}
              <p class="sub" style="margin:0;font-size:.82em;color:var(--accent)">
                ✓ {idleAudit.verdict}
              </p>
            {:else if idleAudit.verdict_kind === "high"}
              <p class="sub" style="margin:0;font-size:.82em;color:var(--accent-warn)">
                ⚠️ {idleAudit.verdict}
              </p>
              <ul style="margin:.4em 0 0;padding-left:1.4em;font-size:.78em">
                {#each idleAudit.checklist ?? [] as item}
                  <li title={item.hint}><b>{item.label}</b> — <span class="sub">{item.hint}</span></li>
                {/each}
              </ul>
            {/if}
          {/if}

          <!-- ─── Update check + 1-click pull (cycle 144 — moved from Services per user fb) ─── -->
          <h3 style="margin-top:1.6em;color:var(--text-muted);font-size:.95em;font-weight:600">
            ⬇️ {i18n.t("about.update_section") ?? "Mise à jour"}
          </h3>
          <p class="sub" style="margin:0 0 .6em;font-size:.82em">
            {i18n.t("about.update_description") ?? "Vérifie la version distante sur GitHub. Si une nouvelle est dispo, applique avec git pull + restart automatique."}
          </p>
          <div class="btn-row">
            <button class="btn" disabled={updateChecking} onclick={checkUpdate}>
              🔍 {updateChecking ? "…" : i18n.t("services.update_check_btn")}
            </button>
            {#if updateStatus}
              {#if updateStatus.behind === null}
                <span class="sub">{i18n.t("services.update_unknown")}</span>
              {:else if updateStatus.behind === 0}
                <span class="ok">✓ {i18n.t("services.update_up_to_date")}</span>
                <span class="sub">@{updateStatus.current_sha}</span>
              {:else}
                <span class="warn">🔔 {i18n.t("services.update_behind", { n: updateStatus.behind, s: (updateStatus.behind || 0) > 1 ? "s" : "" })}</span>
                <button class="btn btn-primary" disabled={pulling} onclick={pullAndRestart}>
                  ⬇️ {pulling ? "…" : i18n.t("services.update_pull_btn")}
                </button>
              {/if}
            {/if}
          </div>
          {#if updateStatus?.last_remote_msg}
            <p class="sub" style="margin-top:.4em;font-size:.78em;font-style:italic">
              Latest commit : "{updateStatus.last_remote_msg}"
            </p>
          {/if}

          <p class="sub" style="margin-top:1.4em;font-size:.78em">
            💡 {i18n.t("about.stats_moved_hint")}
          </p>
        {:else}
          <p class="sub">{i18n.t("history.loading")}</p>
        {/if}
      </div>

      <!-- Profile editor -->
      <div class="modal-section" class:active={modal.section === "profile"}>
        <h3 class="title">
          <svg class="icon" viewBox="0 0 24 24" fill="currentColor"><path d={iconOf("profile")} /></svg>
          <span>{i18n.t("profile.title")}</span>
        </h3>
        <p class="sub" style="margin:0 0 .8em">{i18n.t("profile.description")}</p>
        <p class="sub" style="margin:.4em 0">
          <strong>{i18n.t("profile.current")}:</strong> {live.data?.profile?.model ?? "_generic"}
        </p>

        <label class="sub" style="display:block;margin:.6em 0 .3em">{i18n.t("profile.editor_label")}</label>
        <textarea class="profile-editor" bind:value={profileText} spellcheck="false"></textarea>

        <div class="btn-row" style="margin-top:.6em">
          <button class="btn btn-primary" disabled={profileSaving} onclick={saveProfile}>
            💾 {profileSaving ? "…" : i18n.t("profile.save_btn")}
          </button>
          <button class="btn" onclick={formatProfileJson}>📝 {i18n.t("profile.format_btn")}</button>
          <button class="btn" onclick={resetProfile}>↩️ {i18n.t("profile.reset_btn")}</button>
        </div>
      </div>

      <!-- ─── Per-app profile triggers (R&D #1, cycle 118) ─────────────── -->
      <div class="modal-section" class:active={modal.section === "apptriggers"}>
        <h3 class="title">
          <svg class="icon" viewBox="0 0 24 24" fill="currentColor"><path d={iconOf("apptriggers")} /></svg>
          <span>{i18n.t("apptriggers.title")}</span>
        </h3>
        <p class="sub" style="margin:0 0 .8em">{i18n.t("apptriggers.hint")}</p>

        {#if !triggersLoaded}
          <p class="sub">{i18n.t("history.loading")}</p>
        {:else}
          <div class="trig-rows">
            {#each triggerEntries as t, i}
              <div class="trig-row">
                <input
                  type="text"
                  bind:value={t.app}
                  placeholder={i18n.t("apptriggers.app_placeholder")}
                  class="trig-app"
                />
                <span class="trig-arrow">→</span>
                <select bind:value={t.profile} class="trig-profile">
                  <option value="boost">🚀 boost</option>
                  <option value="sweet">⭐ sweet</option>
                  <option value="silent">🤫 silent</option>
                </select>
                <button class="btn btn-icon" onclick={() => removeTrigger(i)} title={i18n.t("apptriggers.remove")}>✕</button>
              </div>
            {/each}
          </div>
          <div class="btn-row" style="margin-top:.6em">
            <button class="btn" onclick={addTrigger}>+ {i18n.t("apptriggers.add")}</button>
            <button class="btn btn-primary" disabled={triggersSaving} onclick={saveTriggers}>
              💾 {triggersSaving ? "…" : i18n.t("apptriggers.save")}
            </button>
          </div>
          <p class="sub" style="margin-top:.8em;font-size:.78em;color:var(--text-dim)">
            💡 {i18n.t("apptriggers.examples")}
          </p>
        {/if}
      </div>

      <!-- ─── Profile A/B Benchmark (R&D #4 — polished cycle 140) ──── -->
      <div class="modal-section" class:active={modal.section === "benchmark"}>
        <h3 class="title">
          <svg class="icon" viewBox="0 0 24 24" fill="currentColor"><path d={iconOf("benchmark")} /></svg>
          <span>⚖️ {i18n.t("benchmark.title")}</span>
        </h3>

        <div class="bench-explainer">
          <p style="margin:0 0 .4em;color:var(--text-muted)">
            <b>{i18n.t("benchmark.what_label")}</b>
          </p>
          <ol style="margin:0;padding-left:1.2em;font-size:.86em;line-height:1.5">
            <li>{i18n.t("benchmark.step1")}</li>
            <li>{i18n.t("benchmark.step2")}</li>
            <li>{i18n.t("benchmark.step3")}</li>
          </ol>
          <p class="sub" style="margin:.5em 0 0;font-size:.78em">
            💡 {i18n.t("benchmark.workload_hint")}
          </p>
        </div>

        <h4 class="bench-section-h">{i18n.t("benchmark.config_label")}</h4>
        <div class="bench-form">
          <label class="bench-cell">
            <span class="sub">{i18n.t("benchmark.profile_a")}</span>
            <select bind:value={benchA}>
              <option value="silent">🤫 silent</option>
              <option value="sweet">⭐ sweet</option>
              <option value="boost">🚀 boost</option>
            </select>
          </label>
          <span class="bench-vs">vs</span>
          <label class="bench-cell">
            <span class="sub">{i18n.t("benchmark.profile_b")}</span>
            <select bind:value={benchB}>
              <option value="silent">🤫 silent</option>
              <option value="sweet">⭐ sweet</option>
              <option value="boost">🚀 boost</option>
            </select>
          </label>
          <label class="bench-cell">
            <span class="sub">{i18n.t("benchmark.duration_s")}</span>
            <input type="number" min="5" max="300" step="5" bind:value={benchDuration} class="bench-dur" />
          </label>
          <button class="btn btn-primary" disabled={benchRunning} onclick={runBench}>
            {benchRunning ? "⏳ " + i18n.t("benchmark.running_short") : "▶ " + i18n.t("benchmark.run")}
          </button>
        </div>
        {#if benchRunning}
          <div class="bench-progress">
            ⏳ {i18n.t("benchmark.running", { d: benchDuration * 2 })}
          </div>
        {/if}
        {#if benchResult}
          {@const a = benchResult.segment_a}
          {@const b = benchResult.segment_b}
          {@const w = benchResult.comparison.winners}
          <h4 class="bench-section-h">{i18n.t("benchmark.result_label")}</h4>
          <div class="bench-result-card">
            <table class="bench-table">
              <thead>
                <tr>
                  <th></th>
                  <th class="bench-col-a">{a.profile}</th>
                  <th class="bench-col-b">{b.profile}</th>
                  <th>🏆 {i18n.t("benchmark.winner")}</th>
                </tr>
              </thead>
              <tbody>
                <tr><td>🌡️ {i18n.t("benchmark.col_temp")}</td>
                  <td class:win-a={w.cooler === a.profile}>{a.avg_temp_c}°C</td>
                  <td class:win-b={w.cooler === b.profile}>{b.avg_temp_c}°C</td>
                  <td><b>{w.cooler}</b></td></tr>
                <tr><td>⚡ {i18n.t("benchmark.col_power")}</td>
                  <td class:win-a={w.lower_power === a.profile}>{a.avg_power_w} W</td>
                  <td class:win-b={w.lower_power === b.profile}>{b.avg_power_w} W</td>
                  <td><b>{w.lower_power}</b></td></tr>
                <tr><td>🪙 {i18n.t("benchmark.col_throughput")}</td>
                  <td class:win-a={w.higher_throughput === a.profile}>{a.tokens_per_s} tok/s</td>
                  <td class:win-b={w.higher_throughput === b.profile}>{b.tokens_per_s} tok/s</td>
                  <td><b>{w.higher_throughput}</b></td></tr>
                <tr><td>⚖️ {i18n.t("benchmark.col_efficiency")}</td>
                  <td class:win-a={w.more_efficient === a.profile}>{a.tokens_per_kwh.toFixed(0)} tok/kWh</td>
                  <td class:win-b={w.more_efficient === b.profile}>{b.tokens_per_kwh.toFixed(0)} tok/kWh</td>
                  <td><b>{w.more_efficient}</b></td></tr>
                <tr><td>💸 {i18n.t("benchmark.col_cost")}</td>
                  <td class:win-a={w.cheaper === a.profile}>{a.cost.toFixed(4)}</td>
                  <td class:win-b={w.cheaper === b.profile}>{b.cost.toFixed(4)}</td>
                  <td><b>{w.cheaper}</b></td></tr>
              </tbody>
            </table>
            <p class="sub" style="margin-top:.6em;font-size:.78em">
              {i18n.t("benchmark.result_note")}
            </p>
          </div>
        {/if}
      </div>

      <!-- Diagnostics / logs -->
      <div class="modal-section" class:active={modal.section === "diagnostics"}>
        <h3 class="title">
          <svg class="icon" viewBox="0 0 24 24" fill="currentColor"><path d={iconOf("diagnostics")} /></svg>
          <span>{i18n.t("diagnostics.title")}</span>
        </h3>
        <p class="sub" style="margin:0 0 .8em">{i18n.t("diagnostics.description")}</p>

        <div class="btn-row" style="margin-bottom:.8em">
          <button class="btn btn-primary" disabled={logsLoading} onclick={loadLogs}>
            {logsLoading ? "…" : "🔍 " + i18n.t("diagnostics.refresh")}
          </button>
          <label style="display:flex;align-items:center;gap:.4em;font-size:.85em">
            <span class="sub">{i18n.t("diagnostics.tail_lines", { n: "" })}</span>
            <select bind:value={logTail} class="al-input" style="max-width:90px">
              <option value={50}>50</option>
              <option value={100}>100</option>
              <option value={250}>250</option>
              <option value={500}>500</option>
            </select>
          </label>
          {#if logsData?.source}
            <span class="sub" style="font-size:.78em">
              📄 {logsData.source === "file" ? logsData.path : logsData.unit}
            </span>
          {/if}
        </div>

        {#if !logsData}
          <p class="sub">{i18n.t("history.loading")}</p>
        {:else if !logsData.ok}
          <p class="sub" style="color:#fb923c">⚠ {logsData.reason}</p>
        {:else if logsData.lines && logsData.lines.length === 0}
          <p class="sub">{i18n.t("diagnostics.no_log")}</p>
        {:else}
          <!-- Cycle 150 user fb : 'log viewer pas clean' — parse each line,
               extract short HH:MM:SS + level keyword + message, color-coded. -->
          {@const parsedLines = (logsData.lines || []).map((raw) => {
            const s = raw.replace(/\n$/, "");
            // Match : 2026-05-22T14:24:25+02:00 desktop python3[12345]: msg
            const m = s.match(/^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}:\d{2})[^ ]*\s+\S+\s+(\S+):\s*(.*)$/);
            const hms = m ? m[2] : "";
            const proc = m ? m[3].replace(/\[\d+\]$/, "") : "";
            const msg = m ? m[4] : s;
            // Severity detection
            const lower = msg.toLowerCase();
            let level = "info";
            if (/error|exception|traceback|failed|fatal/.test(lower)) level = "err";
            else if (/warn|deprecat/.test(lower)) level = "warn";
            else if (/^\s+File "|^\s+~+/.test(s)) level = "trace";
            return { hms, proc, msg, level };
          })}
          <div class="logs-viewer">
            {#each parsedLines as l}
              <div class="log-row log-{l.level}">
                <span class="log-time">{l.hms}</span>
                <span class="log-msg">{l.msg}</span>
              </div>
            {/each}
          </div>
        {/if}
      </div>

      <!-- Integrations : Watchdog + Services + HF Janitor (R&D #12 UI sprint) -->
      <div class="modal-section" class:active={modal.section === "integrations"}>
        <h3 class="title">
          <svg class="icon" viewBox="0 0 24 24" fill="currentColor"><path d={iconOf("integrations")} /></svg>
          {i18n.t("integrations.title")}
        </h3>
        <p class="muted">{i18n.t("integrations.description")}</p>

        <!-- 🐕 Watchdog -->
        <div class="card-form">
          <h4>{i18n.t("integrations.watchdog.title")}</h4>
          <p class="muted">{i18n.t("integrations.watchdog.desc")}</p>
          {#if wdLoading}
            <p class="muted">⏳…</p>
          {:else}
            <div class="form-row">
              <span class="kv">
                {i18n.t("integrations.watchdog.installed")} :
                <b style:color={wdInstalled ? "var(--ok)" : "var(--text-dim)"}>{wdInstalled ? "✓" : "—"}</b>
              </span>
              <span class="kv">
                {i18n.t("integrations.watchdog.active")} :
                <b style:color={wdActive ? "var(--ok)" : "var(--text-dim)"}>{wdActive ? "✓" : "—"}</b>
              </span>
            </div>
            <div class="form-row" style="flex-wrap: wrap; gap: 12px;">
              <label class="kv">
                <input type="checkbox" bind:checked={wdStrict} />
                {i18n.t("integrations.watchdog.strict")}
              </label>
              <label class="kv">
                {i18n.t("integrations.watchdog.interval")} :
                <input type="number" min="30" max="3600" bind:value={wdInterval}
                       style="width: 80px; margin-left: 6px;" />
              </label>
            </div>
            <div class="form-row">
              {#if wdActive}
                <button class="btn btn-danger" onclick={watchdogDisable}>
                  {i18n.t("integrations.watchdog.disable")}
                </button>
              {:else}
                <button class="btn btn-primary" onclick={watchdogEnable}>
                  {i18n.t("integrations.watchdog.enable")}
                </button>
              {/if}
            </div>
          {/if}
        </div>

        <!-- 🔍 Services discovered -->
        <div class="card-form">
          <h4>{i18n.t("integrations.services.title")}</h4>
          <p class="muted">{i18n.t("integrations.services.desc")}</p>
          <div class="form-row">
            <button class="btn" onclick={loadServices}>
              {i18n.t("integrations.services.refresh")}
            </button>
          </div>
          {#if svcLoading}
            <p class="muted">⏳…</p>
          {:else if services.length === 0}
            <p class="muted">{i18n.t("integrations.services.none")}</p>
          {:else}
            <table style="width:100%; font-size:0.92em; margin-top: 8px; border-collapse: collapse;">
              <thead>
                <tr style="text-align: left; color: var(--text-dim); border-bottom: 1px solid var(--border);">
                  <th style="padding: 4px 6px;">Service</th>
                  <th style="padding: 4px 6px;">Category</th>
                  <th style="padding: 4px 6px;">Ports</th>
                  <th style="padding: 4px 6px;">Health</th>
                </tr>
              </thead>
              <tbody>
                {#each services as svc (svc.pid ?? svc.service + svc.primary_port)}
                  <tr style="border-bottom: 1px solid var(--border);">
                    <td style="padding: 6px;"><b>{svc.service}</b></td>
                    <td style="padding: 6px; color: var(--text-dim);">{svc.category}</td>
                    <td style="padding: 6px; font-variant-numeric: tabular-nums;">
                      {svc.ports.join(", ")}
                    </td>
                    <td style="padding: 6px;">
                      {#if svc.health?.ok}
                        <span style="color: var(--ok);">✓ {svc.health.status} ({svc.health.ms}ms)</span>
                      {:else if svc.health}
                        <span style="color: var(--err);">✗</span>
                      {:else}
                        <span class="muted">—</span>
                      {/if}
                    </td>
                  </tr>
                {/each}
              </tbody>
            </table>
            {#if unknownListeners.length > 0}
              <p class="muted" style="margin-top: 12px;">
                {i18n.t("integrations.services.unknown")} ({unknownListeners.length}) :
                {unknownListeners.slice(0, 5).map(u => `${u.proc_name}:${u.port}`).join(", ")}
                {unknownListeners.length > 5 ? "…" : ""}
              </p>
            {/if}
          {/if}
        </div>

        <!-- 🧹 HF Cache Janitor -->
        <div class="card-form">
          <h4>{i18n.t("integrations.hf.title")}</h4>
          <p class="muted">{i18n.t("integrations.hf.desc")}</p>
          <div class="form-row">
            <button class="btn" onclick={loadHFJanitor}>{i18n.t("integrations.hf.scan")}</button>
          </div>
          {#if hfLoading}
            <p class="muted">⏳…</p>
          {:else if hfStats}
            {#if !hfStats.available}
              <p class="muted">— {hfStats.reason ?? ""}</p>
            {:else}
              <div class="form-row" style="flex-wrap: wrap; gap: 14px; margin-top: 8px;">
                <span class="kv">{i18n.t("integrations.hf.total")} : <b>{((hfStats.total_size_mib ?? 0) / 1024).toFixed(1)} GiB</b></span>
                <span class="kv">{i18n.t("integrations.hf.cold")} : <b style="color: var(--warn);">{((hfStats.cold_size_mib ?? 0) / 1024).toFixed(1)} GiB</b></span>
                <span class="kv">{i18n.t("integrations.hf.hot")} : <b>{hfStats.hot_count ?? 0}</b></span>
              </div>
              <p class="muted" style="margin-top: 6px; font-size: 0.88em;">
                {i18n.t("integrations.hf.dirs")} : {(hfStats.dirs_scanned ?? []).join(", ")}
              </p>
              {#if (hfStats.top_cold?.length ?? 0) > 0}
                <h5 style="margin: 14px 0 6px 0;">{i18n.t("integrations.hf.top")}</h5>
                <table style="width:100%; font-size:0.88em; border-collapse: collapse;">
                  <tbody>
                    {#each hfStats.top_cold ?? [] as f, i (f.path)}
                      {#if i < 10}
                        <tr style="border-bottom: 1px solid var(--border);">
                          <td style="padding: 5px; font-variant-numeric: tabular-nums; text-align: right;">
                            {(f.size_mib / 1024).toFixed(1)} GiB
                          </td>
                          <td style="padding: 5px; text-align: right; color: var(--text-dim);">
                            {f.age_days}d
                          </td>
                          <td style="padding: 5px;">
                            {f.is_hot ? "🔥" : "❄️"}
                          </td>
                          <td style="padding: 5px; font-family: monospace; font-size: 0.85em; color: var(--text-dim);
                                     overflow: hidden; text-overflow: ellipsis; max-width: 0;">
                            {f.path.replace(/^.*\/(models--[^\/]+|[^\/]+)\/(blobs|snapshots).*$/, "$1")}
                          </td>
                        </tr>
                      {/if}
                    {/each}
                  </tbody>
                </table>
              {/if}
            {/if}
          {/if}
        </div>
      </div>

      <!-- Card 4 — 🔔 Notification channels (lives inside Integrations) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.notif.title")}</h4>
        <p class="muted">{i18n.t("integrations.notif.desc")}</p>
        {#if notifLoading}
          <p class="muted">⏳…</p>
        {:else}
          {#if notifChannels.length === 0}
            <p class="muted">{i18n.t("integrations.notif.none")}</p>
          {:else}
            <table style="width:100%; font-size:0.92em; border-collapse: collapse; margin-top: 6px;">
              <thead>
                <tr style="text-align:left; color: var(--text-dim); border-bottom: 1px solid var(--border);">
                  <th style="padding: 4px;">ID</th>
                  <th style="padding: 4px;">Type</th>
                  <th style="padding: 4px;">Name</th>
                  <th style="padding: 4px;">Level</th>
                  <th style="padding: 4px;">On</th>
                  <th style="padding: 4px;"></th>
                </tr>
              </thead>
              <tbody>
                {#each notifChannels as c (c.id)}
                  <tr style="border-bottom: 1px solid var(--border);">
                    <td style="padding: 4px; font-family: monospace; font-size: 0.85em;">{c.id}</td>
                    <td style="padding: 4px;">{c.type}</td>
                    <td style="padding: 4px;">{c.name ?? ""}</td>
                    <td style="padding: 4px;">{c.min_level ?? "—"}</td>
                    <td style="padding: 4px;">{c.enabled ? "✓" : "—"}</td>
                    <td style="padding: 4px;">
                      <button class="btn btn-small btn-danger" onclick={() => notifDelete(c.id)}>×</button>
                    </td>
                  </tr>
                {/each}
              </tbody>
            </table>
          {/if}
          {#if notifEditing}
            <div class="form-row" style="flex-direction: column; align-items: stretch; gap: 8px;
                                          background: var(--bg-2); border: 1px solid var(--border);
                                          padding: 10px; margin-top: 10px; border-radius: 6px;">
              <div style="display:flex; gap: 8px; align-items: center;">
                <label class="kv" style="flex: 1;">
                  {i18n.t("integrations.notif.id")}
                  <input type="text" bind:value={notifEditing.id} style="width: 100%;" />
                </label>
                <label class="kv">
                  {i18n.t("integrations.notif.type")}
                  <select bind:value={notifEditing.type}>
                    {#each notifTypes as t}
                      <option value={t}>{t}</option>
                    {/each}
                  </select>
                </label>
              </div>
              <label class="kv">
                {i18n.t("integrations.notif.name")}
                <input type="text" bind:value={notifEditing.name} style="width: 100%;" />
              </label>
              <div style="display:flex; gap: 12px;">
                <label class="kv">
                  <input type="checkbox" bind:checked={notifEditing.enabled} />
                  {i18n.t("integrations.notif.enabled")}
                </label>
                <label class="kv">
                  {i18n.t("integrations.notif.min_level")}
                  <select bind:value={notifEditing.min_level}>
                    <option value="info">info</option>
                    <option value="warn">warn</option>
                    <option value="crit">crit</option>
                  </select>
                </label>
              </div>
              {#if ["discord","slack","ntfy","gotify","generic"].includes(notifEditing.type)}
                <label class="kv">
                  {i18n.t("integrations.notif.url")}
                  <input type="text" bind:value={notifEditing.url} style="width: 100%;"
                         placeholder="https://..." />
                </label>
              {/if}
              {#if notifEditing.type === "pushover"}
                <label class="kv">
                  App token
                  <input type="text" bind:value={notifEditing.token} style="width: 100%;" />
                </label>
                <label class="kv">
                  {i18n.t("integrations.notif.user")} key
                  <input type="text" bind:value={notifEditing.user} style="width: 100%;" />
                </label>
              {/if}
              {#if notifEditing.type === "gotify"}
                <label class="kv">
                  {i18n.t("integrations.notif.token")}
                  <input type="text" bind:value={notifEditing.token} style="width: 100%;" />
                </label>
              {/if}
              {#if notifEditing.type === "smtp"}
                <label class="kv">Host
                  <input type="text" bind:value={notifEditing.host} style="width: 100%;" /></label>
                <label class="kv">Port
                  <input type="number" bind:value={notifEditing.port} /></label>
                <label class="kv">From
                  <input type="text" bind:value={notifEditing.from_addr} style="width: 100%;" /></label>
                <label class="kv">To
                  <input type="text" bind:value={notifEditing.to_addr} style="width: 100%;" /></label>
              {/if}
              <div class="form-row" style="gap: 6px;">
                <button class="btn btn-primary" onclick={notifSave} disabled={notifBusy}>
                  {i18n.t("integrations.notif.save")}
                </button>
                <button class="btn" onclick={notifTest} disabled={notifBusy}>
                  {i18n.t("integrations.notif.test")}
                </button>
                <button class="btn" onclick={() => (notifEditing = null)}>
                  {i18n.t("integrations.notif.cancel")}
                </button>
              </div>
            </div>
          {:else}
            <div class="form-row" style="margin-top: 8px;">
              <button class="btn btn-primary" onclick={notifStartNew}>
                {i18n.t("integrations.notif.add")}
              </button>
            </div>
          {/if}
        {/if}
      </div>

      <!-- Card 5 — 🔐 Auth tokens & share-links -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.auth.title")}</h4>
        <p class="muted">{i18n.t("integrations.auth.desc")}</p>

        <!-- Token list -->
        {#if authLoading}
          <p class="muted">⏳…</p>
        {:else if authTokens.length === 0}
          <p class="muted">{i18n.t("integrations.auth.none")}</p>
        {:else}
          <table style="width:100%; font-size:0.9em; border-collapse: collapse; margin-top: 8px;">
            <thead>
              <tr style="text-align:left; color: var(--text-dim); border-bottom: 1px solid var(--border);">
                <th style="padding: 4px;">{i18n.t("integrations.auth.name")}</th>
                <th style="padding: 4px;">{i18n.t("integrations.auth.scope")}</th>
                <th style="padding: 4px;">{i18n.t("integrations.auth.created")}</th>
                <th style="padding: 4px;">{i18n.t("integrations.auth.expires")}</th>
                <th style="padding: 4px;"></th>
              </tr>
            </thead>
            <tbody>
              {#each authTokens as t (t.id)}
                <tr style="border-bottom: 1px solid var(--border);">
                  <td style="padding: 5px;">{t.name}</td>
                  <td style="padding: 5px;">
                    <span style="padding: 1px 6px; border-radius: 3px; background: var(--bg-2); font-size: 0.85em;">
                      {t.scope}
                    </span>
                  </td>
                  <td style="padding: 5px; color: var(--text-dim); font-size: 0.85em;">
                    {new Date(t.created_ts * 1000).toISOString().slice(0, 10)}
                  </td>
                  <td style="padding: 5px; color: var(--text-dim); font-size: 0.85em;">
                    {t.expires_ts ? new Date(t.expires_ts * 1000).toISOString().slice(0, 10) : i18n.t("integrations.auth.never")}
                  </td>
                  <td style="padding: 5px;">
                    <button class="btn btn-small btn-danger" onclick={() => authDelete(t.id)}>×</button>
                  </td>
                </tr>
              {/each}
            </tbody>
          </table>
        {/if}

        <!-- Just-created secret callout -->
        {#if authJustCreatedSecret}
          <div style="margin-top: 12px; padding: 10px; background: rgba(252, 211, 77, 0.1);
                      border: 1px solid #fbbf24; border-radius: 6px;">
            <p style="margin: 0 0 6px 0; color: #fbbf24;">
              {i18n.t("integrations.auth.secret_shown_once")}
            </p>
            <div style="display:flex; gap: 6px; align-items: center;">
              <code style="flex: 1; padding: 4px 8px; background: var(--bg-1); font-family: monospace;
                            font-size: 0.85em; word-break: break-all;">{authJustCreatedSecret}</code>
              <button class="btn btn-small" onclick={() => copyToClipboard(authJustCreatedSecret!)}>📋</button>
              <button class="btn btn-small" onclick={() => (authJustCreatedSecret = null)}>×</button>
            </div>
          </div>
        {/if}

        <!-- Create-token form -->
        <div class="form-row" style="margin-top: 12px; flex-wrap: wrap; gap: 8px; align-items: end;">
          <label class="kv">
            {i18n.t("integrations.auth.name")}
            <input type="text" bind:value={authNewName} placeholder="my-laptop" style="width: 140px;" />
          </label>
          <label class="kv">
            {i18n.t("integrations.auth.scope")}
            <select bind:value={authNewScope}>
              <option value="read">read</option>
              <option value="write">write</option>
              <option value="admin">admin</option>
            </select>
          </label>
          <label class="kv">
            {i18n.t("integrations.auth.ttl_days")}
            <input type="number" min="1" max="3650" bind:value={authNewTtlDays}
                   placeholder="∞" style="width: 70px;" />
          </label>
          <button class="btn btn-primary" onclick={authCreate} disabled={authBusy}>
            {i18n.t("integrations.auth.create")}
          </button>
        </div>

        <!-- Share-link generator -->
        <hr style="border: none; border-top: 1px solid var(--border); margin: 18px 0 12px 0;" />
        <h5 style="margin: 0 0 6px 0;">{i18n.t("integrations.auth.share_title")}</h5>
        <div class="form-row" style="flex-wrap: wrap; gap: 10px; align-items: end;">
          <label class="kv">
            {i18n.t("integrations.auth.share_scope")}
            <select bind:value={shareScope}>
              <option value="read">read</option>
              <option value="write">write</option>
            </select>
          </label>
          <label class="kv">
            {i18n.t("integrations.auth.share_ttl_hours")}
            <input type="number" min="1" max="720" bind:value={shareTtlHours} style="width: 70px;" />
          </label>
          <label class="kv">
            {i18n.t("integrations.auth.share_sub")}
            <input type="text" bind:value={shareSub} style="width: 120px;" />
          </label>
          <button class="btn" onclick={shareMake}>
            {i18n.t("integrations.auth.share_make")}
          </button>
        </div>
        {#if shareGeneratedToken}
          <div style="margin-top: 10px; padding: 8px; background: var(--bg-2); border-radius: 4px;">
            <code style="font-family: monospace; font-size: 0.8em; word-break: break-all;">
              ?share={shareGeneratedToken}
            </code>
            <button class="btn btn-small" style="margin-left: 6px;"
                    onclick={() => copyToClipboard("?share=" + shareGeneratedToken)}>📋</button>
          </div>
        {/if}
      </div>

      <!-- UI sprint cycle 3 — R&D #12 features (inline cards within Integrations) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.disk.title")}</h4>
        <p class="muted">{i18n.t("integrations.disk.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadDiskHealth}>{i18n.t("integrations.disk.refresh")}</button>
        </div>
        {#if diskLoading}
          <p class="muted">⏳…</p>
        {:else if diskStats && diskStats.available}
          <p class="muted" style="margin: 6px 0; font-size: 0.88em;">
            {diskStats.device_count} {diskStats.device_count === 1 ? "disk" : "disks"} ·
            worst : <b style:color={_verdictColor(diskStats.worst_verdict)}>{diskStats.worst_verdict}</b>
          </p>
          <table style="width:100%; font-size:0.88em; border-collapse: collapse;">
            <tbody>
              {#each (diskStats.disks ?? []) as d}
                <tr style="border-bottom: 1px solid var(--border);">
                  <td style="padding: 5px; font-family: monospace; font-size: 0.9em;">{d.device}</td>
                  <td style="padding: 5px;">{d.model ?? '?'}</td>
                  <td style="padding: 5px;">{d.is_nvme ? "NVMe" : "SATA"}</td>
                  <td style="padding: 5px;">{d.temp_c ?? '—'}°C</td>
                  <td style="padding: 5px;">{d.wearout_pct != null ? `${d.wearout_pct}%` : '—'}</td>
                  <td style="padding: 5px;">
                    <span style:color={_verdictColor(d.verdict?.kind)}>
                      {d.verdict?.kind ?? '?'}
                    </span>
                    {#if d.verdict && d.verdict.reasons && d.verdict.reasons.length > 0}
                      <span class="muted" style="font-size: 0.85em; margin-left: 4px;">
                        ({d.verdict.reasons[0]})
                      </span>
                    {/if}
                  </td>
                </tr>
              {/each}
            </tbody>
          </table>
        {:else if diskStats}
          <p class="muted">— {diskStats.reason ?? i18n.t("integrations.disk.none")}</p>
        {/if}
      </div>

      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.airgap.title")}</h4>
        <p class="muted">{i18n.t("integrations.airgap.desc")}</p>
        {#if airgapStat}
          <div class="form-row" style="flex-wrap: wrap; gap: 12px;">
            <span class="kv" style:color={airgapStat.enabled ? "var(--warn)" : "var(--text-dim)"}>
              {airgapStat.enabled ? i18n.t("integrations.airgap.status_on") : i18n.t("integrations.airgap.status_off")}
            </span>
            {#if airgapStat.lan_allowed}
              <span class="kv muted">· {i18n.t("integrations.airgap.lan_allowed")}</span>
            {/if}
            <span class="kv muted">· {i18n.t("integrations.airgap.blocked_24h")} :
              <b>{airgapStat.blocked_count_24h}</b>
            </span>
          </div>
          {#if airgapStat.enabled}
            <div class="form-row" style="margin-top: 8px;">
              <button class="btn" onclick={loadAirgapAudit}>
                {i18n.t("integrations.airgap.audit_view")}
              </button>
            </div>
            {#if airgapAudit && airgapAudit.length > 0}
              <table style="width:100%; font-size:0.85em; margin-top: 8px; border-collapse: collapse;">
                <tbody>
                  {#each airgapAudit.slice(0, 10) as e}
                    <tr style="border-bottom: 1px solid var(--border);">
                      <td style="padding: 4px; color: var(--text-dim); font-size: 0.85em;">
                        {new Date(e.ts * 1000).toLocaleTimeString()}
                      </td>
                      <td style="padding: 4px; font-family: monospace; font-size: 0.85em; max-width: 280px; overflow: hidden; text-overflow: ellipsis;">
                        {e.url}
                      </td>
                      <td style="padding: 4px; color: var(--warn);">{e.reason}</td>
                    </tr>
                  {/each}
                </tbody>
              </table>
            {:else if airgapAudit}
              <p class="muted" style="margin-top: 6px;">{i18n.t("integrations.airgap.audit_empty")}</p>
            {/if}
          {/if}
        {/if}
        <p class="muted" style="font-size: 0.82em; margin-top: 10px;">
          {i18n.t("integrations.airgap.note")}
        </p>
      </div>

      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.wall.title")}</h4>
        <p class="muted">{i18n.t("integrations.wall.desc")}</p>
        {#if wallMeter && wallMeter.available}
          <div class="form-row" style="flex-wrap: wrap; gap: 14px; margin-top: 8px;">
            <span class="kv">{i18n.t("integrations.wall.wall_w")} : <b>{wallMeter.wall_w} W</b></span>
            <span class="kv">{i18n.t("integrations.wall.gpu_w")} : <b>{wallMeter.gpu_w ?? '—'} W</b></span>
            <span class="kv">{i18n.t("integrations.wall.headroom")} : <b>{wallMeter.headroom_w} W</b></span>
            {#if wallMeter.psu_efficiency_pct != null}
              <span class="kv">{i18n.t("integrations.wall.efficiency")} :
                <b style:color={wallMeter.psu_efficiency_pct > 90 ? 'var(--ok)' : 'var(--warn)'}>
                  {wallMeter.psu_efficiency_pct}%
                </b>
              </span>
            {/if}
          </div>
        {:else if wallMeter}
          <p class="muted">{wallMeter.reason ?? i18n.t("integrations.wall.not_configured")}</p>
        {/if}
      </div>

      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.peers.title")}</h4>
        <p class="muted">{i18n.t("integrations.peers.desc")}</p>
        {#if peersData && peersData.peers && peersData.peers.length > 0}
          <table style="width:100%; font-size:0.9em; border-collapse: collapse; margin-top: 6px;">
            <tbody>
              {#each peersData.peers as p}
                <tr style="border-bottom: 1px solid var(--border);">
                  <td style="padding: 5px;"><b>{p.host}</b></td>
                  <td style="padding: 5px; font-family: monospace; color: var(--text-dim);">
                    {p.ip}:{p.port}
                  </td>
                  <td style="padding: 5px;">{p.gpu_count}× {p.gpu_model}</td>
                  <td style="padding: 5px; color: var(--text-dim);">
                    v{p.version}
                  </td>
                </tr>
              {/each}
            </tbody>
          </table>
        {:else}
          <p class="muted">{i18n.t("integrations.peers.none")}</p>
        {/if}
      </div>

      <!-- UI sprint cycle 4 — R&D #13 cards -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.wizard.title")}</h4>
        <p class="muted">{i18n.t("integrations.wizard.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={runHotGpuWizard} disabled={wizardLoading}>
            {wizardLoading ? "⏳…" : i18n.t("integrations.wizard.run")}
          </button>
        </div>
        {#if wizardData}
          <p style="margin: 8px 0;">
            <b style:color={_verdictColor(wizardData.verdict)}>
              {wizardData.verdict === "pass" ? i18n.t("integrations.wizard.verdict_pass")
                : wizardData.verdict === "warn" ? i18n.t("integrations.wizard.verdict_warn")
                : wizardData.verdict === "fail" ? i18n.t("integrations.wizard.verdict_fail")
                : i18n.t("integrations.wizard.verdict_skip")}
            </b>
          </p>
          <table style="width:100%; font-size:0.88em; border-collapse: collapse;">
            <tbody>
              {#each wizardData.steps as s}
                <tr style="border-bottom: 1px solid var(--border);">
                  <td style="padding: 5px; width: 90px;"><b>{s.step}</b></td>
                  <td style="padding: 5px;">
                    <span style:color={_verdictColor(s.kind)}>{s.kind}</span>
                  </td>
                  <td style="padding: 5px; color: var(--text-dim);">{s.detail}</td>
                </tr>
              {/each}
            </tbody>
          </table>
          {#if wizardData.actions && wizardData.actions.length > 0}
            <p style="margin: 10px 0 4px 0;"><b>{i18n.t("integrations.wizard.actions_label")} :</b></p>
            <ul style="margin: 0; padding-left: 18px; font-size: 0.9em;">
              {#each wizardData.actions as action}
                <li>{action}</li>
              {/each}
            </ul>
          {/if}
        {/if}
      </div>

      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.vram.title")}</h4>
        <p class="muted">{i18n.t("integrations.vram.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={evalVramQuota} disabled={vramEvalLoading}>
            {vramEvalLoading ? "⏳…" : i18n.t("integrations.vram.eval")}
          </button>
        </div>
        {#if vramQuotaData}
          {#if vramQuotaData.rules.length === 0}
            <p class="muted">{i18n.t("integrations.vram.no_rules")}</p>
          {:else}
            <table style="width:100%; font-size:0.9em; border-collapse: collapse; margin-top: 6px;">
              <thead>
                <tr style="text-align:left; color: var(--text-dim); border-bottom: 1px solid var(--border);">
                  <th style="padding: 4px;">id</th>
                  <th style="padding: 4px;">regex</th>
                  <th style="padding: 4px;">max</th>
                  <th style="padding: 4px;">grace</th>
                  <th style="padding: 4px;">action</th>
                </tr>
              </thead>
              <tbody>
                {#each vramQuotaData.rules as r}
                  <tr style="border-bottom: 1px solid var(--border);">
                    <td style="padding: 5px;"><b>{r.id}</b></td>
                    <td style="padding: 5px; font-family: monospace; font-size: 0.85em;">{r.process_regex}</td>
                    <td style="padding: 5px;">{r.max_vram_mib} MiB</td>
                    <td style="padding: 5px;">{r.grace_s ?? 60}s</td>
                    <td style="padding: 5px;">{r.action}</td>
                  </tr>
                {/each}
              </tbody>
            </table>
          {/if}
          {#if vramQuotaData.audit.length > 0}
            <p style="margin: 10px 0 4px 0;"><b>{i18n.t("integrations.vram.recent_fires")} :</b></p>
            <table style="width:100%; font-size:0.85em; border-collapse: collapse;">
              <tbody>
                {#each vramQuotaData.audit.slice(0, 5) as f}
                  <tr style="border-bottom: 1px solid var(--border);">
                    <td style="padding: 4px; color: var(--text-dim); font-size: 0.85em;">
                      {new Date(f.ts * 1000).toLocaleTimeString()}
                    </td>
                    <td style="padding: 4px;">{f.name?.split("/").pop()}</td>
                    <td style="padding: 4px;">{f.used_mib}/{f.max_mib} MiB</td>
                    <td style="padding: 4px;">
                      <span style:color={f.escalation.includes("kill") ? "var(--err)" : f.escalation.includes("term") ? "var(--warn)" : "var(--text-dim)"}>
                        {f.escalation}
                      </span>
                    </td>
                  </tr>
                {/each}
              </tbody>
            </table>
          {/if}
        {/if}
      </div>

      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.carbon.title")}</h4>
        <p class="muted">{i18n.t("integrations.carbon.desc")}</p>
        {#if carbonData && carbonData.available}
          <div class="form-row" style="flex-wrap: wrap; gap: 14px; margin-top: 8px;">
            <span class="kv">{i18n.t("integrations.carbon.current")} :
              <b>{carbonData.current_gco2_per_kwh} gCO2/kWh</b>
            </span>
            {#if carbonData.gco2_today_g != null}
              <span class="kv">{i18n.t("integrations.carbon.today")} :
                <b>{carbonData.gco2_today_g} g</b>
              </span>
            {/if}
            {#if carbonData.gco2_month_kg != null}
              <span class="kv">{i18n.t("integrations.carbon.month")} :
                <b>{carbonData.gco2_month_kg} kg</b>
              </span>
            {/if}
            {#if carbonData.gco2_per_token_g != null}
              <span class="kv">{i18n.t("integrations.carbon.per_token")} :
                <b style="color: var(--ok);">{carbonData.gco2_per_token_g.toFixed(6)} g</b>
              </span>
            {/if}
          </div>
          {#if carbonData.day_min_gco2_per_kwh != null}
            <p class="muted" style="margin: 8px 0; font-size: 0.88em;">
              {i18n.t("integrations.carbon.day_range")} :
              {carbonData.day_min_gco2_per_kwh} ·
              {carbonData.day_avg_gco2_per_kwh} ·
              {carbonData.day_max_gco2_per_kwh} gCO2/kWh
            </p>
          {/if}
        {:else if carbonData}
          <p class="muted">{carbonData.reason ?? i18n.t("integrations.carbon.not_configured")}</p>
        {/if}
      </div>

      <!-- UI sprint cycle 5 — R&D #14 cards (rendered before best-GPU so they
           appear higher in the Integrations stack ; visually grouped with #13) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.xid.title")}</h4>
        <p class="muted">{i18n.t("integrations.xid.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadXid}>{i18n.t("integrations.xid.refresh")}</button>
        </div>
        {#if xidData}
          {#if xidData.total_24h === 0}
            <p class="muted" style="color: var(--ok);">{i18n.t("integrations.xid.none")}</p>
          {:else}
            <p style="margin: 8px 0;">
              <b style:color={_verdictColor(xidData.worst_severity === "ok" ? "pass" : xidData.worst_severity === "fail" ? "fail" : "warn")}>
                {xidData.total_24h} events
              </b>
              <span class="muted" style="margin-left: 10px;">
                fail={xidData.counts_by_severity.fail} ·
                warn={xidData.counts_by_severity.warn} ·
                info={xidData.counts_by_severity.info}
              </span>
            </p>
            <table style="width:100%; font-size:0.88em; border-collapse: collapse;">
              <tbody>
                {#each xidData.events.slice(0, 10) as e}
                  <tr style="border-bottom: 1px solid var(--border);">
                    <td style="padding: 5px; font-family: monospace;">Xid {e.code}</td>
                    <td style="padding: 5px;">
                      <span style:color={e.severity === "fail" ? "var(--err)" : e.severity === "warn" ? "var(--warn)" : "var(--text-dim)"}>
                        {e.severity}
                      </span>
                    </td>
                    <td style="padding: 5px;"><b>{e.name}</b></td>
                    <td style="padding: 5px; color: var(--text-dim); font-size: 0.85em;">
                      {e.remediation ?? ""}
                    </td>
                  </tr>
                {/each}
              </tbody>
            </table>
          {/if}
        {/if}
      </div>

      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.hotswap.title")}</h4>
        <p class="muted">{i18n.t("integrations.hotswap.desc")}</p>
        {#if hotSwapData && hotSwapData.current && hotSwapData.current.pci}
          {#each Object.entries(hotSwapData.current.pci) as [bdf, p]}
            <div class="form-row" style="flex-wrap: wrap; gap: 14px; margin-top: 6px;">
              <span class="kv" style="font-family: monospace;">{bdf}</span>
              <span class="kv">{i18n.t("integrations.hotswap.current_link")} :
                <b>{p.current_link_speed} ×{p.current_link_width}</b>
                {#if p.current_link_speed !== p.max_link_speed || p.current_link_width !== p.max_link_width}
                  <span class="muted" style="margin-left: 6px;">
                    (max {p.max_link_speed} ×{p.max_link_width})
                  </span>
                {/if}
              </span>
              <span class="kv">{i18n.t("integrations.hotswap.power_state")} :
                <b style:color={p.power_state === "D0" ? "var(--ok)" : "var(--text-dim)"}>
                  {p.power_state}
                </b>
              </span>
            </div>
          {/each}
          {#if hotSwapData.events.length > 0}
            <h5 style="margin: 12px 0 4px 0;">Recent events ({hotSwapData.events.length}) :</h5>
            <table style="width:100%; font-size:0.85em; border-collapse: collapse;">
              <tbody>
                {#each hotSwapData.events.slice(0, 8) as e}
                  <tr style="border-bottom: 1px solid var(--border);">
                    <td style="padding: 4px; color: var(--text-dim); font-size: 0.85em;">
                      {e.ts ? new Date(e.ts * 1000).toLocaleTimeString() : "—"}
                    </td>
                    <td style="padding: 4px;">
                      <span style:color={e.kind.includes("disconnect") || e.kind.includes("downgrade") ? "var(--warn)" : "var(--text-dim)"}>
                        {e.kind}
                      </span>
                    </td>
                    <td style="padding: 4px; font-family: monospace; font-size: 0.85em;">
                      {e.gpu ?? e.target ?? ""}
                    </td>
                  </tr>
                {/each}
              </tbody>
            </table>
          {:else}
            <p class="muted" style="margin-top: 8px;">{i18n.t("integrations.hotswap.no_events")}</p>
          {/if}
        {/if}
      </div>

      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.cost.title")}</h4>
        <p class="muted">{i18n.t("integrations.cost.desc")}</p>
        {#if costData && costData.available}
          {#if costData.headline_tok_per_wh != null}
            <p style="margin: 8px 0; font-size: 1.05em;">
              <b style="color: var(--ok); font-size: 1.4em;">
                {costData.headline_tok_per_wh}
              </b>
              <span class="muted" style="margin-left: 8px;">
                {i18n.t("integrations.cost.headline")} · @ {costData.price_eur_per_kwh} €/kWh
              </span>
            </p>
          {/if}
          <table style="width:100%; font-size:0.9em; border-collapse: collapse;">
            <thead>
              <tr style="text-align:left; color: var(--text-dim); border-bottom: 1px solid var(--border);">
                <th style="padding: 4px;">{i18n.t("integrations.cost.window")}</th>
                <th style="padding: 4px;">{i18n.t("integrations.cost.tokens")}</th>
                <th style="padding: 4px;">{i18n.t("integrations.cost.kwh")}</th>
                <th style="padding: 4px;">{i18n.t("integrations.cost.cost_eur")}</th>
                <th style="padding: 4px;">tok/Wh</th>
                <th style="padding: 4px;">{i18n.t("integrations.cost.per_1k")}</th>
              </tr>
            </thead>
            <tbody>
              {#each Object.entries(costData.windows) as [secStr, w]}
                <tr style="border-bottom: 1px solid var(--border);">
                  <td style="padding: 5px;"><b>{_windowLabel(parseInt(secStr))}</b></td>
                  <td style="padding: 5px;">{w.tokens_delta.toLocaleString()}</td>
                  <td style="padding: 5px;">{w.kwh.toFixed(4)}</td>
                  <td style="padding: 5px;">{w.cost_gpu_eur.toFixed(4)} €</td>
                  <td style="padding: 5px;">
                    {#if w.tok_per_wh_gpu != null}
                      <b>{w.tok_per_wh_gpu}</b>
                    {:else}<span class="muted">—</span>{/if}
                  </td>
                  <td style="padding: 5px;">
                    {#if w.cost_per_1k_tokens_eur != null}
                      {w.cost_per_1k_tokens_eur.toFixed(6)}
                    {:else}<span class="muted">—</span>{/if}
                  </td>
                </tr>
              {/each}
            </tbody>
          </table>
        {/if}
      </div>

      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.usage.title")}</h4>
        <p class="muted">{i18n.t("integrations.usage.desc")}</p>
        {#if labUsageData}
          {#if labUsageData.users.length === 0}
            <p class="muted">{i18n.t("integrations.usage.no_users")}</p>
          {:else}
            <table style="width:100%; font-size:0.9em; border-collapse: collapse;">
              <thead>
                <tr style="text-align:left; color: var(--text-dim); border-bottom: 1px solid var(--border);">
                  <th style="padding: 4px;">user</th>
                  <th style="padding: 4px;">PIDs</th>
                  <th style="padding: 4px;">VRAM</th>
                  <th style="padding: 4px;">~ Watts</th>
                  <th style="padding: 4px;">processes</th>
                </tr>
              </thead>
              <tbody>
                {#each labUsageData.users as u}
                  <tr style="border-bottom: 1px solid var(--border);">
                    <td style="padding: 5px;"><b>{u.name}</b>
                      <span class="muted" style="margin-left: 4px; font-size: 0.85em;">(uid {u.uid})</span>
                    </td>
                    <td style="padding: 5px;">{u.pid_count}</td>
                    <td style="padding: 5px;">{(u.vram_used_mib / 1024).toFixed(1)} GiB</td>
                    <td style="padding: 5px;">{u.watts_share ?? '—'} W</td>
                    <td style="padding: 5px; font-family: monospace; font-size: 0.85em;">
                      {u.processes.slice(0, 3).map(p => p.name).join(", ")}
                    </td>
                  </tr>
                {/each}
              </tbody>
            </table>
          {/if}
        {/if}
      </div>

      <!-- UI sprint cycle 6 — R&D #15 cards (rendered before bestgpu so they
           appear in document order with the R&D #14 cards) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.boot.title")}</h4>
        <p class="muted">{i18n.t("integrations.boot.desc")}</p>
        {#if bootProfileData}
          <div class="form-row" style="flex-wrap: wrap; gap: 12px; margin-top: 6px;">
            <label class="kv">{i18n.t("integrations.boot.name_label")}
              <input type="text" bind:value={bootProfileForm.name} style="width: 140px;" />
            </label>
            <label class="kv">{i18n.t("integrations.boot.pl_label")}
              <input type="number" min="50" max="600"
                     bind:value={bootProfileForm.power_limit_w} style="width: 80px;" />
            </label>
            <label class="kv">
              <input type="checkbox" bind:checked={bootProfileForm.persistence_mode} />
              {i18n.t("integrations.boot.pm_label")}
            </label>
          </div>
          <div class="form-row" style="gap: 6px; margin-top: 8px;">
            <button class="btn btn-primary" onclick={saveBootProfile} disabled={bootSaving}>
              {i18n.t("integrations.boot.save")}
            </button>
            <button class="btn" onclick={applyBootProfileNow}
                    disabled={!bootProfileData.configured}>
              {i18n.t("integrations.boot.apply_now")}
            </button>
            {#if bootProfileData.configured}
              <button class="btn btn-danger" onclick={clearBootProfile}>
                {i18n.t("integrations.boot.clear")}
              </button>
            {/if}
          </div>
          {#if !bootProfileData.configured}
            <p class="muted">{i18n.t("integrations.boot.not_configured")}</p>
          {/if}
          {#if bootProfileData.last_outcome}
            <div style="margin-top: 10px; padding: 8px; background: var(--bg-2); border-radius: 4px;
                          font-size: 0.85em;">
              <b>{i18n.t("integrations.boot.last_outcome")} :</b>
              <span style:color={bootProfileData.last_outcome.ok ? 'var(--ok)' : 'var(--err)'}>
                {bootProfileData.last_outcome.ok ? "✓ success" : "✗ failed"}
              </span>
              {#if bootProfileData.last_outcome.ready_probe}
                <span class="muted" style="margin-left: 8px;">
                  driver ready in {bootProfileData.last_outcome.ready_probe.elapsed_s}s
                </span>
              {/if}
            </div>
          {/if}
        {/if}
      </div>

      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.tariff.title")}</h4>
        <p class="muted">{i18n.t("integrations.tariff.desc")}</p>
        {#if tariffData && tariffData.available}
          <div class="form-row" style="flex-wrap: wrap; gap: 14px; margin-top: 8px;">
            <span class="kv">{i18n.t("integrations.tariff.current_rate")} :
              <b>{tariffData.current_eur_per_kwh} €/kWh</b>
              <span class="muted" style="margin-left: 4px;">@ {tariffData.current_hour}h</span>
            </span>
            <span class="kv">{i18n.t("integrations.tariff.day_range_label")} :
              <b>{tariffData.day_min_eur_per_kwh} · {tariffData.day_avg_eur_per_kwh} · {tariffData.day_max_eur_per_kwh}</b>
            </span>
          </div>
          <p class="muted" style="margin-top: 6px; font-size: 0.88em;">
            {i18n.t("integrations.tariff.cheapest_hours_label")} :
            <b>{tariffData.cheapest_hours?.sort((a,b)=>a-b).join(", ")}h</b>
            ·
            {i18n.t("integrations.tariff.peak_hours_label")} :
            <b>{tariffData.peak_hours?.sort((a,b)=>a-b).join(", ")}h</b>
          </p>
          {#if cheapestData?.best}
            <div style="margin-top: 10px; padding: 8px; background: var(--bg-2);
                         border-radius: 4px;">
              <b>Cheapest start for 300W × 4h :</b>
              <span style="color: var(--ok); font-weight: 600; margin-left: 6px;">
                {String(cheapestData.best.start_hour).padStart(2, "0")}h00
              </span>
              <span class="muted">(+{cheapestData.best.hours_until_start}h)</span>
              <span style="margin-left: 8px;">→ {cheapestData.best.cost_eur.toFixed(4)} €</span>
              <span class="muted" style="margin-left: 8px;">
                (save {cheapestData.absolute_savings_eur?.toFixed(4)} € vs worst,
                 {cheapestData.savings_pct?.toFixed(0)}%)
              </span>
            </div>
          {/if}
        {:else if tariffData}
          <p class="muted">{tariffData.reason ?? i18n.t("integrations.tariff.not_configured")}</p>
        {/if}
      </div>

      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.dedup.title")}</h4>
        <p class="muted">{i18n.t("integrations.dedup.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={scanDedup} disabled={dedupScanning}>
            {dedupScanning ? "⏳…" : i18n.t("integrations.dedup.scan")}
          </button>
        </div>
        {#if dedupData && dedupData.available}
          <div class="form-row" style="flex-wrap: wrap; gap: 14px; margin-top: 8px;">
            <span class="kv">{i18n.t("integrations.dedup.files_scanned")} :
              <b>{dedupData.files_scanned}</b>
            </span>
            <span class="kv">{i18n.t("integrations.dedup.dupe_groups")} :
              <b>{dedupData.duplicate_groups}</b>
            </span>
            <span class="kv">{i18n.t("integrations.dedup.reclaim")} :
              <b style="color: var(--ok);">{((dedupData.reclaim_mib ?? 0) / 1024).toFixed(2)} GiB</b>
            </span>
            {#if (dedupData.cross_device_skipped?.length ?? 0) > 0}
              <span class="kv muted">
                {i18n.t("integrations.dedup.cross_device")} : {dedupData.cross_device_skipped?.length}
              </span>
            {/if}
          </div>
          {#if (dedupData.plan?.length ?? 0) > 0}
            <div class="form-row" style="gap: 6px; margin-top: 10px;">
              <button class="btn" onclick={() => executeDedup(true)} disabled={dedupExecuting}>
                {i18n.t("integrations.dedup.execute_dry")}
              </button>
              <button class="btn btn-danger" onclick={() => executeDedup(false)} disabled={dedupExecuting}>
                {i18n.t("integrations.dedup.execute_live")}
              </button>
            </div>
          {/if}
        {/if}
      </div>

      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.discord.title")}</h4>
        <p class="muted">{i18n.t("integrations.discord.desc")}</p>
        {#if discordData}
          <div class="form-row" style="flex-wrap: wrap; gap: 12px; margin-top: 6px;">
            <span class="kv">IPC :
              <b style:color={discordData.discord_ipc_present ? "var(--ok)" : "var(--text-dim)"}>
                {discordData.discord_ipc_present ? "✓ detected" : "—"}
              </b>
            </span>
            <span class="kv">App ID :
              <b style:color={discordData.app_id_configured ? "var(--ok)" : "var(--text-dim)"}>
                {discordData.app_id_configured ? "✓ set" : "not set"}
              </b>
            </span>
            <span class="kv">Bridge :
              <b style:color={discordData.enabled ? "var(--ok)" : "var(--text-dim)"}>
                {discordData.enabled ? "✓ enabled" : "off"}
              </b>
            </span>
          </div>
          {#if !discordData.discord_ipc_present}
            <p class="muted" style="margin-top: 8px; font-size: 0.88em;">
              {i18n.t("integrations.discord.no_discord")}
            </p>
          {:else if !discordData.app_id_configured}
            <p class="muted" style="margin-top: 8px; font-size: 0.88em;">
              {i18n.t("integrations.discord.no_app_id")}
            </p>
          {:else if !discordData.enabled}
            <p class="muted" style="margin-top: 8px; font-size: 0.88em;">
              {i18n.t("integrations.discord.ready")}
            </p>
          {/if}
        {/if}
      </div>

      <!-- UI sprint cycle 7 — R&D #16 cards (rendered before bestgpu) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.noc.title")}</h4>
        <p class="muted">{i18n.t("integrations.noc.desc")}</p>
        <div class="form-row">
          <a class="btn" href="/noc" target="_blank" rel="noopener">
            {i18n.t("integrations.noc.open")} ↗
          </a>
        </div>
        <p class="muted" style="font-size: 0.82em; margin-top: 8px;
                                  font-family: monospace;">
          {i18n.t("integrations.noc.kiosk_hint")}
        </p>
      </div>

      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.dr.title")}</h4>
        <p class="muted">{i18n.t("integrations.dr.desc")}</p>
        <div class="form-row">
          <button class="btn btn-primary" onclick={buildDrBundle} disabled={drBuilding}>
            {drBuilding ? i18n.t("integrations.dr.building") : i18n.t("integrations.dr.build")}
          </button>
        </div>
        {#if drBundles}
          {#if drBundles.length === 0}
            <p class="muted">{i18n.t("integrations.dr.list_empty")}</p>
          {:else}
            <p style="margin: 8px 0 4px 0;"><b>{i18n.t("integrations.dr.bundles_label")} :</b></p>
            <table style="width:100%; font-size:0.88em; border-collapse: collapse;">
              <tbody>
                {#each drBundles as b}
                  <tr style="border-bottom: 1px solid var(--border);">
                    <td style="padding: 5px; font-family: monospace; font-size: 0.85em;">{b.name}</td>
                    <td style="padding: 5px; text-align: right;">
                      {(b.size_bytes / 1024).toFixed(1)} KiB
                    </td>
                    <td style="padding: 5px; color: var(--text-dim); font-size: 0.85em;">
                      {new Date(b.ts * 1000).toLocaleString()}
                    </td>
                    <td style="padding: 5px;">
                      <button class="btn btn-small btn-danger" onclick={() => deleteDrBundle(b.name)}>×</button>
                    </td>
                  </tr>
                {/each}
              </tbody>
            </table>
          {/if}
        {/if}
      </div>

      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.lmstudio.title")}</h4>
        <p class="muted">{i18n.t("integrations.lmstudio.desc")}</p>
        {#if lmStudioData}
          {#if !lmStudioData.available}
            <p class="muted">{lmStudioData.reason ?? i18n.t("integrations.lmstudio.no_lmstudio")}</p>
          {:else}
            <div class="form-row" style="flex-wrap: wrap; gap: 14px; margin-top: 6px;">
              <span class="kv">{i18n.t("integrations.lmstudio.models_count")} :
                <b>{lmStudioData.models_count}</b>
              </span>
              <span class="kv">{i18n.t("integrations.lmstudio.total_size")} :
                <b>{lmStudioData.total_size_gib} GiB</b>
              </span>
              {#if (lmStudioData.duplication_suspect_count ?? 0) > 0}
                <span class="kv">{i18n.t("integrations.lmstudio.dup_suspects")} :
                  <b style="color: var(--warn);">
                    {lmStudioData.duplication_suspect_count}
                    ({lmStudioData.duplication_suspect_gib} GB)
                  </b>
                </span>
              {/if}
            </div>
            <table style="width:100%; font-size:0.88em; border-collapse: collapse; margin-top: 8px;">
              <thead>
                <tr style="text-align: left; color: var(--text-dim); border-bottom: 1px solid var(--border);">
                  <th style="padding: 4px;">name</th>
                  <th style="padding: 4px;">size</th>
                  <th style="padding: 4px;">quant</th>
                </tr>
              </thead>
              <tbody>
                {#each (lmStudioData.models ?? []).slice(0, 10) as m}
                  <tr style="border-bottom: 1px solid var(--border);">
                    <td style="padding: 4px; font-family: monospace; font-size: 0.85em;">{m.name}</td>
                    <td style="padding: 4px; text-align: right;">
                      {(m.size_mib / 1024).toFixed(1)} GiB
                    </td>
                    <td style="padding: 4px;">
                      <code style="font-size: 0.85em;">{m.quant ?? '—'}</code>
                    </td>
                  </tr>
                {/each}
              </tbody>
            </table>
          {/if}
        {/if}
      </div>

      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.driver.title")}</h4>
        <p class="muted">{i18n.t("integrations.driver.desc")}</p>
        {#if driverVaultData?.current}
          <p style="margin: 8px 0;">
            <b>{i18n.t("integrations.driver.current")} :</b>
            <code style="margin-left: 6px; font-size: 0.9em;">
              {driverVaultData.current.package} {driverVaultData.current.version}
            </code>
          </p>
        {/if}
        <div class="form-row">
          <button class="btn btn-primary" onclick={stashDriver}>
            {i18n.t("integrations.driver.stash")}
          </button>
        </div>
        {#if driverVaultData}
          {#if driverVaultData.vaulted.length === 0}
            <p class="muted" style="margin-top: 8px;">{i18n.t("integrations.driver.vault_empty")}</p>
          {:else}
            <table style="width:100%; font-size:0.88em; border-collapse: collapse; margin-top: 8px;">
              <tbody>
                {#each driverVaultData.vaulted as v}
                  <tr style="border-bottom: 1px solid var(--border);">
                    <td style="padding: 5px; font-family: monospace; font-size: 0.85em;">{v.name}</td>
                    <td style="padding: 5px; text-align: right;">
                      {(v.size_bytes / 1024 / 1024).toFixed(1)} MiB
                    </td>
                    <td style="padding: 5px;">
                      <button class="btn btn-small" onclick={() => showRollbackScript(v.name)}>
                        {i18n.t("integrations.driver.show_script")}
                      </button>
                    </td>
                  </tr>
                {/each}
              </tbody>
            </table>
          {/if}
          {#if driverVaultData.recent_events && driverVaultData.recent_events.length > 0}
            <p style="margin: 10px 0 4px 0;">
              <b>{i18n.t("integrations.driver.recent_events")} ({driverVaultData.recent_events.length}) :</b>
            </p>
            <table style="width:100%; font-size:0.82em; border-collapse: collapse;">
              <tbody>
                {#each driverVaultData.recent_events.slice(0, 3) as e}
                  <tr style="border-bottom: 1px solid var(--border);">
                    <td style="padding: 4px; color: var(--text-dim);">{e.start}</td>
                    <td style="padding: 4px;">{e.action}</td>
                    <td style="padding: 4px; font-family: monospace; font-size: 0.85em;">
                      {e.packages[0]?.name}
                      {#if e.packages[0]?.ver_from && e.packages[0]?.ver_to}
                        {e.packages[0].ver_from} → {e.packages[0].ver_to}
                      {/if}
                    </td>
                  </tr>
                {/each}
              </tbody>
            </table>
          {/if}
        {/if}
        {#if driverVaultScript}
          <div style="margin-top: 12px; padding: 10px; background: var(--bg-2); border-radius: 4px;">
            <div class="form-row" style="margin-bottom: 6px;">
              <button class="btn btn-small" onclick={() => copyToClipboard(driverVaultScript!)}>📋 Copy</button>
              <button class="btn btn-small" onclick={() => (driverVaultScript = null)}>×</button>
            </div>
            <pre style="white-space: pre-wrap; font-size: 0.78em; max-height: 280px; overflow: auto;
                          margin: 0; font-family: monospace;">{driverVaultScript}</pre>
          </div>
        {/if}
      </div>

      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.bestgpu.title")}</h4>
        <p class="muted">{i18n.t("integrations.bestgpu.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadBestGpu}>{i18n.t("integrations.bestgpu.refresh")}</button>
        </div>
        {#if bestGpuData && bestGpuData.available}
          <p style="margin: 8px 0;">
            {bestGpuData.reasoning}
          </p>
          <div class="form-row" style="gap: 8px; margin-top: 6px;">
            <code style="flex: 1; padding: 6px 10px; background: var(--bg-2);
                          font-family: monospace; border-radius: 4px;">
              {bestGpuData.shell_export}
            </code>
            <button class="btn btn-small"
                    onclick={() => copyToClipboard(bestGpuData?.shell_export ?? "")}>📋</button>
          </div>
          {#if bestGpuData.ranked && bestGpuData.ranked.length > 1}
            <table style="width:100%; font-size:0.88em; border-collapse: collapse; margin-top: 10px;">
              <thead>
                <tr style="text-align:left; color: var(--text-dim); border-bottom: 1px solid var(--border);">
                  <th style="padding: 4px;">idx</th>
                  <th style="padding: 4px;">temp</th>
                  <th style="padding: 4px;">util</th>
                  <th style="padding: 4px;">vram</th>
                  <th style="padding: 4px;">score</th>
                </tr>
              </thead>
              <tbody>
                {#each bestGpuData.ranked as r, i}
                  <tr style="border-bottom: 1px solid var(--border);">
                    <td style="padding: 4px;">{r.index}{i === 0 ? " ★" : ""}</td>
                    <td style="padding: 4px;">{r.temp_c ?? '—'}°C</td>
                    <td style="padding: 4px;">{r.util_pct ?? '—'}%</td>
                    <td style="padding: 4px;">{r.vram_used_mib ?? '—'}/{r.vram_total_mib ?? '—'} MiB</td>
                    <td style="padding: 4px; font-variant-numeric: tabular-nums;">{r.score}</td>
                  </tr>
                {/each}
              </tbody>
            </table>
          {/if}
        {/if}
      </div>

      <!-- R&D #17.5 LLM hot-swap orchestrator (UI sprint 8) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.llmswap.title")}</h4>
        <p class="muted">{i18n.t("integrations.llmswap.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadLlmSwap}>{i18n.t("integrations.llmswap.refresh")}</button>
          <span class="kv" style="margin-left: 12px;">
            {i18n.t("integrations.llmswap.total_vram")} :
            <b>{llmSwapData?.total_vram_gib ?? 0} GiB</b>
          </span>
        </div>
        {#if llmSwapData && llmSwapData.loaded_count === 0}
          <p class="muted" style="margin-top: 8px;">{i18n.t("integrations.llmswap.none")}</p>
        {/if}
        {#if llmSwapData && llmSwapData.loaded_count > 0}
          <table style="width:100%; font-size:0.88em; border-collapse: collapse; margin-top: 10px;">
            <thead>
              <tr style="text-align:left; color: var(--text-dim); border-bottom: 1px solid var(--border);">
                <th style="padding: 4px;">{i18n.t("integrations.llmswap.source")}</th>
                <th style="padding: 4px;">model</th>
                <th style="padding: 4px;">{i18n.t("integrations.llmswap.vram")}</th>
                <th style="padding: 4px;">{i18n.t("integrations.llmswap.size")}</th>
                <th style="padding: 4px;"></th>
              </tr>
            </thead>
            <tbody>
              {#each llmSwapData.loaded as m}
                {@const pinned = llmSwapData.pins.includes(m.name)}
                <tr style="border-bottom: 1px solid var(--border);">
                  <td style="padding: 4px; color: var(--text-dim);">{m.source}</td>
                  <td style="padding: 4px; font-family: monospace; font-size: 0.92em;">
                    {pinned ? "📌 " : ""}{m.name}
                  </td>
                  <td style="padding: 4px;">
                    {((m.vram_bytes ?? 0) / 1024 ** 3).toFixed(2)} GiB
                  </td>
                  <td style="padding: 4px;">
                    {((m.size_bytes ?? 0) / 1024 ** 3).toFixed(2)} GiB
                  </td>
                  <td style="padding: 4px;">
                    <button class="btn btn-small"
                            onclick={() => toggleLlmPin(m.name, pinned)}>
                      {pinned ? i18n.t("integrations.llmswap.unpin")
                              : i18n.t("integrations.llmswap.pin")}
                    </button>
                  </td>
                </tr>
              {/each}
            </tbody>
          </table>
        {/if}
        <div class="form-row" style="margin-top: 14px; gap: 10px; align-items: center;">
          <label style="margin: 0;">
            {i18n.t("integrations.llmswap.suggest_label")}
            <input type="number" min="1" max="48" step="1"
                   bind:value={llmSwapNeededGib}
                   style="width: 64px; margin-left: 6px;" />
          </label>
          <button class="btn btn-small" onclick={previewLlmSwap}>
            {i18n.t("integrations.llmswap.suggest_btn")}
          </button>
        </div>
        {#if llmSwapSuggestion}
          <p class="muted" style="margin-top: 8px;"
             style:color={llmSwapSuggestion.sufficient ? "var(--ok)" : "var(--warn)"}>
            {llmSwapSuggestion.sufficient
              ? i18n.t("integrations.llmswap.suggest_sufficient",
                       { gib: ((llmSwapSuggestion.freed_bytes / 1024 ** 3).toFixed(2)) })
              : i18n.t("integrations.llmswap.suggest_insufficient")}
          </p>
          {#if llmSwapSuggestion.to_evict.length > 0}
            <ul style="margin: 4px 0 0 18px; font-size: 0.88em;">
              {#each llmSwapSuggestion.to_evict as e}
                <li>
                  <code>{e.name}</code>
                  <span class="muted">({(e.vram_bytes / 1024 ** 3).toFixed(2)} GiB, {e.source})</span>
                </li>
              {/each}
            </ul>
          {/if}
        {/if}
        {#if llmSwapData && llmSwapData.recent_events.length > 0}
          <h5 style="margin: 14px 0 4px 0;">{i18n.t("integrations.llmswap.timeline_events")}
            ({llmSwapData.timeline_count})</h5>
          <table style="width:100%; font-size:0.85em; border-collapse: collapse;">
            <tbody>
              {#each llmSwapData.recent_events.slice(-10).reverse() as e}
                <tr style="border-bottom: 1px solid var(--border);">
                  <td style="padding: 4px; color: var(--text-dim); font-size: 0.85em;">
                    {e.ts ? new Date(e.ts * 1000).toLocaleTimeString() : "—"}
                  </td>
                  <td style="padding: 4px;">
                    <span style:color={e.kind === "unload" ? "var(--warn)" : "var(--ok)"}>
                      {e.kind === "load"
                        ? i18n.t("integrations.llmswap.event_load")
                        : i18n.t("integrations.llmswap.event_unload")}
                    </span>
                  </td>
                  <td style="padding: 4px; font-family: monospace; font-size: 0.85em;">
                    {e.name}
                  </td>
                </tr>
              {/each}
            </tbody>
          </table>
        {:else if llmSwapData}
          <p class="muted" style="margin-top: 8px;">{i18n.t("integrations.llmswap.no_events")}</p>
        {/if}
      </div>

      <!-- R&D #18.3 CUDA_VISIBLE_DEVICES advisor (UI sprint 9) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.cudaadvisor.title")}</h4>
        <p class="muted">{i18n.t("integrations.cudaadvisor.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadCudaAdvisor}>{i18n.t("integrations.cudaadvisor.refresh")}</button>
          {#if cudaAdvisorData}
            <span class="kv" style="margin-left: 12px;">
              {i18n.t("integrations.cudaadvisor.gpus_visible")} :
              <b>{cudaAdvisorData.gpu_count}</b>
            </span>
            <span class="kv">
              {i18n.t("integrations.cudaadvisor.proc_count")} :
              <b>{cudaAdvisorData.process_count}</b>
            </span>
            <span class="kv"
                  style:color={cudaAdvisorData.drift_count > 0 ? "var(--warn)" : "var(--text-dim)"}>
              {i18n.t("integrations.cudaadvisor.drift_count")} :
              <b>{cudaAdvisorData.drift_count}</b>
            </span>
          {/if}
        </div>
        {#if cudaAdvisorData}
          <p class="muted" style="margin-top: 6px;">{cudaAdvisorData.recommendation}</p>
          {#if cudaAdvisorData.processes.length > 0}
            <table style="width:100%; font-size:0.88em; border-collapse: collapse; margin-top: 8px;">
              <thead>
                <tr style="text-align:left; color: var(--text-dim); border-bottom: 1px solid var(--border);">
                  <th style="padding: 4px;">{i18n.t("integrations.cudaadvisor.process")}</th>
                  <th style="padding: 4px;">{i18n.t("integrations.cudaadvisor.pinned_to")}</th>
                  <th style="padding: 4px;">{i18n.t("integrations.cudaadvisor.resolved")}</th>
                </tr>
              </thead>
              <tbody>
                {#each cudaAdvisorData.processes as p}
                  <tr style="border-bottom: 1px solid var(--border);">
                    <td style="padding: 4px;">{p.comm} <span class="muted">(pid {p.pid})</span></td>
                    <td style="padding: 4px; font-family: monospace;">{p.raw}</td>
                    <td style="padding: 4px;"
                        style:color={p.has_drift ? "var(--warn)" : "var(--ok)"}>
                      {p.has_drift
                        ? i18n.t("integrations.cudaadvisor.drift_flag")
                        : i18n.t("integrations.cudaadvisor.ok")}
                    </td>
                  </tr>
                {/each}
              </tbody>
            </table>
          {/if}
        {/if}
      </div>

      <!-- R&D #18.1 NVMe-as-VRAM-swap monitor (UI sprint 9) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.nvmeswap.title")}</h4>
        <p class="muted">{i18n.t("integrations.nvmeswap.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadNvmeSwap}>{i18n.t("integrations.nvmeswap.refresh")}</button>
          {#if nvmeSwapData}
            <span class="kv" style="margin-left: 12px;">
              {i18n.t("integrations.nvmeswap.total_swap")} :
              <b>{nvmeSwapData.llm_total_swap_gib} GiB</b>
            </span>
          {/if}
        </div>
        {#if nvmeSwapData && nvmeSwapData.warning}
          <p style="color: var(--warn); margin-top: 6px;">⚠ {nvmeSwapData.warning}</p>
        {/if}
        {#if nvmeSwapData && nvmeSwapData.llm_processes.length === 0}
          <p class="muted" style="margin-top: 6px;">{i18n.t("integrations.nvmeswap.no_procs")}</p>
        {/if}
        {#if nvmeSwapData && nvmeSwapData.llm_processes.length > 0}
          <table style="width:100%; font-size:0.88em; border-collapse: collapse; margin-top: 8px;">
            <tbody>
              {#each nvmeSwapData.llm_processes as p}
                <tr style="border-bottom: 1px solid var(--border);">
                  <td style="padding: 4px;">{p.comm} <span class="muted">(pid {p.pid})</span></td>
                  <td style="padding: 4px; font-family: monospace; font-size: 0.85em;">
                    {(p.rss_bytes / 1024 ** 3).toFixed(2)} GiB RSS
                  </td>
                  <td style="padding: 4px;"
                      style:color={p.swap_bytes > 1024 ** 3 ? "var(--warn)" : "var(--text-dim)"}>
                    {(p.swap_bytes / 1024 ** 2).toFixed(1)} MiB swap
                  </td>
                </tr>
              {/each}
            </tbody>
          </table>
        {/if}
        {#if nvmeSwapData && nvmeSwapData.nvme_devices.length === 0}
          <p class="muted" style="margin-top: 6px;">{i18n.t("integrations.nvmeswap.no_nvme")}</p>
        {/if}
        {#if nvmeSwapData && nvmeSwapData.nvme_devices.length > 0}
          <table style="width:100%; font-size:0.88em; border-collapse: collapse; margin-top: 8px;">
            <thead>
              <tr style="text-align:left; color: var(--text-dim); border-bottom: 1px solid var(--border);">
                <th style="padding: 4px;">device</th>
                <th style="padding: 4px;">{i18n.t("integrations.nvmeswap.write_rate")}</th>
                <th style="padding: 4px;">{i18n.t("integrations.nvmeswap.endurance")}</th>
                <th style="padding: 4px;">{i18n.t("integrations.nvmeswap.days_remaining")}</th>
              </tr>
            </thead>
            <tbody>
              {#each nvmeSwapData.nvme_devices as d}
                <tr style="border-bottom: 1px solid var(--border);">
                  <td style="padding: 4px; font-family: monospace;">/dev/{d.device}</td>
                  <td style="padding: 4px;">
                    {d.write_rate_mibps !== null ? `${d.write_rate_mibps} MiB/s` : "—"}
                  </td>
                  <td style="padding: 4px;">
                    {d.endurance.used_tb} / {d.endurance.rated_tb} TB
                    <span class="muted">({d.endurance.pct_used}%)</span>
                  </td>
                  <td style="padding: 4px;">
                    {d.endurance.days_remaining !== null
                      ? `${d.endurance.days_remaining} d`
                      : "—"}
                  </td>
                </tr>
              {/each}
            </tbody>
          </table>
        {/if}
      </div>

      <!-- R&D #18.2 CUDA / cuDNN / driver matrix (UI sprint 9) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.cudamatrix.title")}</h4>
        <p class="muted">{i18n.t("integrations.cudamatrix.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadCudaMatrix}>{i18n.t("integrations.cudamatrix.refresh")}</button>
        </div>
        {#if cudaMatrixData}
          <div class="form-row" style="gap: 18px; margin-top: 8px; flex-wrap: wrap;">
            <span class="kv">{i18n.t("integrations.cudamatrix.driver")} :
              <b>{cudaMatrixData.driver_version ?? "—"}</b>
            </span>
            <span class="kv">{i18n.t("integrations.cudamatrix.cuda_toolkit")} :
              <b>{cudaMatrixData.cuda_toolkit?.version ?? "—"}</b>
            </span>
            <span class="kv">{i18n.t("integrations.cudamatrix.cudnn")} :
              <b>{cudaMatrixData.cudnn_version ?? "—"}</b>
            </span>
          </div>
          <p style="margin-top: 8px;"
             style:color={cudaMatrixData.compat.ok === true ? "var(--ok)"
                        : cudaMatrixData.compat.ok === false ? "var(--warn)"
                        : "var(--text-dim)"}>
            <b>
              {cudaMatrixData.compat.ok === true
                ? i18n.t("integrations.cudamatrix.verdict_ok")
                : cudaMatrixData.compat.ok === false
                ? i18n.t("integrations.cudamatrix.verdict_fail")
                : i18n.t("integrations.cudamatrix.verdict_unknown")}
            </b>
            — {cudaMatrixData.compat.reason}
          </p>
        {/if}
      </div>

      <!-- R&D #18.6 PCIe link-state thrasher histogram (UI sprint 9) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.pciehist.title")}</h4>
        <p class="muted">{i18n.t("integrations.pciehist.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadPcieHist}>{i18n.t("integrations.pciehist.refresh")}</button>
        </div>
        {#if pcieHistData}
          <div class="form-row" style="gap: 18px; margin-top: 8px; flex-wrap: wrap;">
            <span class="kv">{i18n.t("integrations.pciehist.transitions_1h")} :
              <b>{pcieHistData.histogram_1h.transition_count}</b>
              <span class="muted">({pcieHistData.histogram_1h.transitions_per_min} / min)</span>
            </span>
            <span class="kv">{i18n.t("integrations.pciehist.transitions_24h")} :
              <b>{pcieHistData.histogram_24h.transition_count}</b>
            </span>
            <span class="kv"
                  style:color={pcieHistData.histogram_1h.verdict === "stable" ? "var(--ok)"
                             : pcieHistData.histogram_1h.verdict === "thrashing" ? "var(--warn)"
                             : "var(--text-dim)"}>
              {i18n.t("integrations.pciehist.verdict")} :
              <b>
                {pcieHistData.histogram_1h.verdict === "stable"
                  ? i18n.t("integrations.pciehist.verdict_stable")
                  : pcieHistData.histogram_1h.verdict === "intermittent"
                  ? i18n.t("integrations.pciehist.verdict_intermittent")
                  : i18n.t("integrations.pciehist.verdict_thrashing")}
              </b>
            </span>
          </div>
          {#if Object.keys(pcieHistData.histogram_24h.buckets).length > 0}
            <h5 style="margin: 12px 0 4px 0;">{i18n.t("integrations.pciehist.buckets")} (24h)</h5>
            <table style="width:100%; font-size:0.85em; border-collapse: collapse;">
              <tbody>
                {#each Object.entries(pcieHistData.histogram_24h.buckets) as [b, n]}
                  <tr style="border-bottom: 1px solid var(--border);">
                    <td style="padding: 4px; font-family: monospace;">{b}</td>
                    <td style="padding: 4px;">{n}</td>
                  </tr>
                {/each}
              </tbody>
            </table>
          {:else}
            <p class="muted" style="margin-top: 6px;">{i18n.t("integrations.pciehist.no_events")}</p>
          {/if}
        {/if}
      </div>

      <!-- R&D #19.2 Throttle classifier (UI sprint 10) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.throttle.title")}</h4>
        <p class="muted">{i18n.t("integrations.throttle.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadThrottleCause}>{i18n.t("integrations.throttle.refresh")}</button>
        </div>
        {#if throttleCauseData && !throttleCauseData.ok}
          <p class="muted" style="margin-top: 8px;">{i18n.t("integrations.throttle.unreachable")}</p>
        {/if}
        {#if throttleCauseData && throttleCauseData.gpus.length > 0}
          {#each throttleCauseData.gpus as g}
            <div style="margin-top: 10px; padding: 8px; border-left: 3px solid {
              g.verdict.severity === 'critical' ? 'var(--warn)' :
              g.verdict.severity === 'warn' ? 'var(--accent)' : 'var(--ok)'
            };">
              <div class="form-row" style="gap: 14px; flex-wrap: wrap;">
                <b>GPU{g.index} — {g.name}</b>
                <span class="kv">{g.temp_c ?? '—'}°C</span>
                <span class="kv">{g.clock_mhz ?? '—'} MHz</span>
                <span class="kv">{g.power_w ?? '—'} / {g.power_limit_w ?? '—'} W</span>
              </div>
              <p style="margin: 4px 0;"
                 style:color={g.verdict.severity === 'critical' ? 'var(--warn)' :
                            g.verdict.severity === 'warn' ? 'var(--accent)' : 'var(--text-dim)'}>
                <b>{g.verdict.reason}</b>
              </p>
              {#if g.verdict.recommendation}
                <p class="muted" style="margin: 4px 0;">{g.verdict.recommendation}</p>
              {/if}
            </div>
          {/each}
        {/if}
      </div>

      <!-- R&D #19.6 MPS daemon health (UI sprint 10) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.mps.title")}</h4>
        <p class="muted">{i18n.t("integrations.mps.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadMpsHealth}>{i18n.t("integrations.mps.refresh")}</button>
          {#if mpsHealthData}
            <span class="kv" style="margin-left: 12px;"
                  style:color={mpsHealthData.state === 'running' ? 'var(--ok)' :
                             mpsHealthData.state === 'stalled' ? 'var(--warn)' :
                             'var(--text-dim)'}>
              {i18n.t("integrations.mps.state")} : <b>{mpsHealthData.state}</b>
            </span>
            {#if mpsHealthData.default_sm_share_pct !== null}
              <span class="kv">
                {i18n.t("integrations.mps.sm_share")} : <b>{mpsHealthData.default_sm_share_pct}%</b>
              </span>
            {/if}
          {/if}
        </div>
        {#if mpsHealthData}
          <p class="muted" style="margin-top: 6px;">{mpsHealthData.advice}</p>
          {#if mpsHealthData.clients.length > 0}
            <h5 style="margin: 8px 0 4px 0;">{i18n.t("integrations.mps.clients")} ({mpsHealthData.clients.length})</h5>
            <table style="width:100%; font-size:0.88em; border-collapse: collapse;">
              <tbody>
                {#each mpsHealthData.clients as c}
                  <tr style="border-bottom: 1px solid var(--border);">
                    <td style="padding: 4px;">pid {c.pid}</td>
                    <td style="padding: 4px; color: var(--text-dim);">uid {c.uid ?? '—'}</td>
                    <td style="padding: 4px; font-family: monospace;">{c.name ?? '—'}</td>
                  </tr>
                {/each}
              </tbody>
            </table>
          {/if}
        {/if}
      </div>

      <!-- R&D #19.1 GPU process nice advisor (UI sprint 10) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.nice.title")}</h4>
        <p class="muted">{i18n.t("integrations.nice.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadProcessNice}>{i18n.t("integrations.nice.refresh")}</button>
          {#if processNiceData}
            <span class="kv" style="margin-left: 12px;"
                  style:color={processNiceData.needs_action_count > 0 ? 'var(--warn)' : 'var(--text-dim)'}>
              {i18n.t("integrations.nice.needs_action")} :
              <b>{processNiceData.needs_action_count}</b>
            </span>
          {/if}
        </div>
        {#if processNiceData?.reason}
          <p class="muted" style="margin-top: 6px;">{processNiceData.reason}</p>
        {/if}
        {#if processNiceData?.processes && processNiceData.processes.length > 0}
          <table style="width:100%; font-size:0.88em; border-collapse: collapse; margin-top: 8px;">
            <thead>
              <tr style="text-align:left; color: var(--text-dim); border-bottom: 1px solid var(--border);">
                <th style="padding: 4px;">{i18n.t("integrations.nice.process")}</th>
                <th style="padding: 4px;">{i18n.t("integrations.nice.class")}</th>
                <th style="padding: 4px;">{i18n.t("integrations.nice.current")}</th>
                <th style="padding: 4px;">{i18n.t("integrations.nice.suggested")}</th>
                <th style="padding: 4px;">{i18n.t("integrations.nice.command")}</th>
              </tr>
            </thead>
            <tbody>
              {#each processNiceData.processes as p}
                <tr style="border-bottom: 1px solid var(--border);">
                  <td style="padding: 4px;">{p.comm} <span class="muted">(pid {p.pid})</span></td>
                  <td style="padding: 4px;">{p.class}</td>
                  <td style="padding: 4px;">{p.current_nice ?? '—'}</td>
                  <td style="padding: 4px;"
                      style:color={p.needs_change ? 'var(--warn)' : 'var(--ok)'}>
                    {p.suggested_nice ?? '—'}
                  </td>
                  <td style="padding: 4px;">
                    {#if p.shell_command}
                      <code style="font-family: monospace; font-size: 0.85em;">{p.shell_command}</code>
                      <button class="btn btn-small" onclick={() => copyToClipboard(p.shell_command ?? "")}>
                        {i18n.t("integrations.nice.copy")}
                      </button>
                    {/if}
                  </td>
                </tr>
              {/each}
            </tbody>
          </table>
        {/if}
      </div>

      <!-- R&D #19.4 Warmup profile (UI sprint 10) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.warmup.title")}</h4>
        <p class="muted">{i18n.t("integrations.warmup.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadWarmup}>{i18n.t("integrations.warmup.refresh")}</button>
          <button class="btn" onclick={fireWarmupProbe} disabled={warmupProbing}>
            {warmupProbing ? "…" : i18n.t("integrations.warmup.probe_btn")}
          </button>
        </div>
        {#if warmupData && warmupData.models.length === 0}
          <p class="muted" style="margin-top: 6px;">{i18n.t("integrations.warmup.none")}</p>
        {/if}
        {#if warmupData && warmupData.models.length > 0}
          {#each warmupData.models as m}
            <div style="margin-top: 10px; padding: 8px; border-left: 3px solid var(--border);">
              <div class="form-row" style="gap: 14px; flex-wrap: wrap;">
                <b style="font-family: monospace;">{m.model}</b>
                <span class="kv" style="color: var(--text-dim);">{m.source}</span>
                <span class="kv">{m.stats.count} {i18n.t("integrations.warmup.samples")}</span>
                {#if m.stats.cold_ttft_ms !== undefined}
                  <span class="kv">{i18n.t("integrations.warmup.cold_ttft")} :
                    <b>{(m.stats.cold_ttft_ms / 1000).toFixed(2)} s</b>
                  </span>
                {/if}
                {#if m.stats.hot_median_ttft_ms}
                  <span class="kv">{i18n.t("integrations.warmup.hot_median")} :
                    <b>{(m.stats.hot_median_ttft_ms / 1000).toFixed(2)} s</b>
                  </span>
                {/if}
                {#if m.stats.cold_minus_hot_ms}
                  <span class="kv"
                        style:color={m.stats.cold_minus_hot_ms > 1000 ? 'var(--warn)' : 'var(--text-dim)'}>
                    {i18n.t("integrations.warmup.cold_gap")} :
                    <b>+{(m.stats.cold_minus_hot_ms / 1000).toFixed(2)} s</b>
                  </span>
                {/if}
              </div>
              <p class="muted" style="margin: 4px 0;">{m.recommendation}</p>
            </div>
          {/each}
        {/if}
      </div>

      <!-- Layout : card hide/show + drag-and-drop reorder -->
      <div class="modal-section" class:active={modal.section === "layout"}>
        <h3 class="title">
          <svg class="icon" viewBox="0 0 24 24" fill="currentColor"><path d="M3 5v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2zm16 0v3H5V5zm-7 5v9H5v-9zm2 0h5v9h-5z"/></svg>
          <span>{i18n.t("layout.title")}</span>
        </h3>
        <p class="sub" style="margin:0 0 1em">{i18n.t("layout.description")}</p>
        <div
          class="layout-dnd"
          use:dndzone={{ items: layoutItems, flipDurationMs: 200, type: "layout-cards", dragDisabled: false }}
          onconsider={handleDndConsider}
          onfinalize={handleDndFinalize}
        >
          {#each layoutItems as item (item.id)}
            <div class="layout-row" class:custom={isCustomId(item.id)}>
              <span class="layout-handle" title={i18n.t("layout.drag_hint")}>⋮⋮</span>
              <input
                type="checkbox"
                checked={layout.visible(item.id)}
                onchange={() => layout.toggle(item.id)}
              />
              <span class="layout-name">
                {#if isCustomId(item.id)}🧩 {/if}{cardLabel(item.id)}
              </span>
              <span class="layout-status" class:on={layout.visible(item.id)}>
                {layout.visible(item.id) ? i18n.t("layout.shown") : i18n.t("layout.hidden")}
              </span>
              {#if isCustomId(item.id)}
                <button class="layout-delete" onclick={() => layout.removeCustom(item.id)}
                  title={i18n.t("layout.remove_custom")}>🗑️</button>
              {:else}
                <span></span>
              {/if}
            </div>
          {/each}
        </div>
        <p class="sub" style="font-size:.78em;margin-top:.8em">{i18n.t("layout.drag_hint")}</p>

        <h3 style="margin-top:1.6em;color:#cdd2da;font-size:.92em;font-weight:600">
          ➕ {i18n.t("layout.add_custom_title")}
        </h3>
        <p class="sub" style="margin:0 0 .6em;font-size:.82em">{i18n.t("layout.add_custom_description")}</p>
        <div class="form-row">
          <span class="form-lbl">{i18n.t("layout.custom_name")}</span>
          <input class="al-input" type="text" bind:value={newCustomName}
            placeholder={i18n.t("layout.custom_name_placeholder")} />
        </div>
        <div class="form-row">
          <span class="form-lbl">{i18n.t("layout.custom_url")}</span>
          <input class="al-input" type="url" bind:value={newCustomUrl}
            placeholder="https://grafana.local/d-solo/..." />
        </div>
        <div class="btn-row" style="margin-top:.4em">
          <button class="btn btn-primary" onclick={addCustomCard}>
            ➕ {i18n.t("layout.add_btn")}
          </button>
          <button class="btn" onclick={() => {
            if (confirm(i18n.t("layout.reset_confirm"))) layout.reset();
          }}>↺ {i18n.t("layout.reset_btn")}</button>
        </div>

        <h3 style="margin-top:1.8em;color:var(--text-muted);font-size:.92em;font-weight:600">
          🎨 {i18n.t("theme.title")}
        </h3>
        <p class="sub" style="margin:0 0 .6em;font-size:.82em">{i18n.t("theme.description")}</p>
        <div class="mode-tiles">
          <button
            class="mode-tile"
            class:active={theme.current === "dark"}
            onclick={() => theme.set("dark")}
          >
            <div class="mode-tile-emoji">🌙</div>
            <div class="mode-tile-name">{i18n.t("theme.dark")}</div>
            <div class="mode-tile-desc">{i18n.t("theme.dark_desc")}</div>
          </button>
          <button
            class="mode-tile"
            class:active={theme.current === "light"}
            onclick={() => theme.set("light")}
          >
            <div class="mode-tile-emoji">☀️</div>
            <div class="mode-tile-name">{i18n.t("theme.light")}</div>
            <div class="mode-tile-desc">{i18n.t("theme.light_desc")}</div>
          </button>
        </div>
      </div>

      <!-- Language -->
      <div class="modal-section" class:active={modal.section === "language"}>
        <h3 class="title">
          <svg class="icon" viewBox="0 0 24 24" fill="currentColor"><path d={iconOf("language")} /></svg>
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
