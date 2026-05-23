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

  // ── R&D #20 (UI sprint 11) ────────────────────────────────────────────
  let suspendGuardData   = $state<Awaited<ReturnType<typeof api.suspendGuardStatus>>   | null>(null);
  let containerAuditData = $state<Awaited<ReturnType<typeof api.containerAuditStatus>> | null>(null);
  let upsRuntimeData     = $state<Awaited<ReturnType<typeof api.upsRuntimeStatus>>     | null>(null);
  let vbiosDriftData     = $state<Awaited<ReturnType<typeof api.vbiosDriftStatus>>     | null>(null);
  let vbiosRebaselining  = $state<boolean>(false);
  async function loadSuspendGuard() {
    try { suspendGuardData = await api.suspendGuardStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadContainerAudit() {
    try { containerAuditData = await api.containerAuditStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadUpsRuntime() {
    try { upsRuntimeData = await api.upsRuntimeStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadVbiosDrift() {
    try { vbiosDriftData = await api.vbiosDriftStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function rebaselineVbios() {
    vbiosRebaselining = true;
    try {
      const r = await api.vbiosDriftRebaseline();
      if (r.ok) {
        toast.emit(`✓ Baseline reset (${r.baseline_size} GPUs)`, "ok");
        await loadVbiosDrift();
      } else { toast.emit("✗ rebaseline failed", "err"); }
    } catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
    finally { vbiosRebaselining = false; }
  }

  // ── R&D #21 (UI sprint 12) ────────────────────────────────────────────
  let pstateAuditData   = $state<Awaited<ReturnType<typeof api.pstateAuditStatus>>     | null>(null);
  let persistenceData   = $state<Awaited<ReturnType<typeof api.persistenceModeStatus>> | null>(null);
  let gspStatusData     = $state<Awaited<ReturnType<typeof api.gspStatus>>             | null>(null);
  let sdCacheData       = $state<Awaited<ReturnType<typeof api.sdCacheJanitorStatus>>  | null>(null);

  // ── R&D #22 (UI sprint 13) ────────────────────────────────────────────
  let vramLeakData      = $state<Awaited<ReturnType<typeof api.vramLeakStatus>>        | null>(null);
  let gpuResetData      = $state<Awaited<ReturnType<typeof api.gpuResetStatus>>        | null>(null);
  let cudaInvData       = $state<Awaited<ReturnType<typeof api.cudaInventoryStatus>>   | null>(null);
  let driverFlavorData  = $state<Awaited<ReturnType<typeof api.driverFlavorStatus>>    | null>(null);

  // ── R&D #23 (UI sprint 14) ────────────────────────────────────────────
  let procDeepData      = $state<Awaited<ReturnType<typeof api.procDeepStateStatus>>   | null>(null);
  let pcieAspmData      = $state<Awaited<ReturnType<typeof api.pcieAspmStatus>>        | null>(null);
  let fsAuditData       = $state<Awaited<ReturnType<typeof api.fsMountAuditStatus>>    | null>(null);
  let batchAdvisorData  = $state<Awaited<ReturnType<typeof api.batchAdvisorStatus>>    | null>(null);

  // ── R&D #24 (UI sprint 15) ────────────────────────────────────────────
  let dkmsStatusData    = $state<Awaited<ReturnType<typeof api.dkmsStatus>>            | null>(null);
  let pcieAerData       = $state<Awaited<ReturnType<typeof api.pcieAerStatus>>         | null>(null);
  let memTempDriftData  = $state<Awaited<ReturnType<typeof api.memTempDriftStatus>>    | null>(null);
  let accountingData    = $state<Awaited<ReturnType<typeof api.accountingStatus>>      | null>(null);

  // ── R&D #25 (UI sprint 16) ────────────────────────────────────────────
  let trimAuditData     = $state<Awaited<ReturnType<typeof api.trimAuditStatus>>       | null>(null);
  let throttleBitsData  = $state<Awaited<ReturnType<typeof api.throttleBitsStatus>>    | null>(null);
  let retiredPagesData  = $state<Awaited<ReturnType<typeof api.retiredPagesStatus>>    | null>(null);
  let bugRepPrepData    = $state<Awaited<ReturnType<typeof api.bugReportPrepStatus>>   | null>(null);
  async function loadTrimAudit() {
    try { trimAuditData = await api.trimAuditStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadThrottleBits() {
    try { throttleBitsData = await api.throttleBitsStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadRetiredPages() {
    try { retiredPagesData = await api.retiredPagesStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadBugRepPrep() {
    try { bugRepPrepData = await api.bugReportPrepStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }

  // ── R&D #26 (UI sprint 17) ────────────────────────────────────────────
  let pcieWidthData     = $state<Awaited<ReturnType<typeof api.pcieWidthWatcherStatus>> | null>(null);
  let cudaCtxLeakData   = $state<Awaited<ReturnType<typeof api.cudaCtxLeakStatus>>      | null>(null);
  let procStaticData    = $state<Awaited<ReturnType<typeof api.procStaticAuditStatus>>  | null>(null);
  let memBwGaugeData    = $state<Awaited<ReturnType<typeof api.memBwGaugeStatus>>       | null>(null);
  async function loadPcieWidth() {
    try { pcieWidthData = await api.pcieWidthWatcherStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadCudaCtxLeak() {
    try { cudaCtxLeakData = await api.cudaCtxLeakStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadProcStatic() {
    try { procStaticData = await api.procStaticAuditStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadMemBwGauge() {
    try { memBwGaugeData = await api.memBwGaugeStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }

  // ── R&D #27 (UI sprint 18) ────────────────────────────────────────────
  let pwrEnvDriftData   = $state<Awaited<ReturnType<typeof api.powerEnvelopeDriftStatus>> | null>(null);
  let rebarAuditData    = $state<Awaited<ReturnType<typeof api.rebarAuditStatus>>        | null>(null);
  let cpuRaplData       = $state<Awaited<ReturnType<typeof api.cpuRaplStatus>>           | null>(null);
  let clockGapData      = $state<Awaited<ReturnType<typeof api.clockGapStatus>>          | null>(null);
  async function loadPwrEnvDrift() {
    try { pwrEnvDriftData = await api.powerEnvelopeDriftStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadRebarAudit() {
    try { rebarAuditData = await api.rebarAuditStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadCpuRapl() {
    try { cpuRaplData = await api.cpuRaplStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadClockGap() {
    try { clockGapData = await api.clockGapStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }

  // ── R&D #28 (UI sprint 19) ────────────────────────────────────────────
  let pcieRpmData       = $state<Awaited<ReturnType<typeof api.pcieRpmAuditStatus>>   | null>(null);
  let thermalZonesData  = $state<Awaited<ReturnType<typeof api.thermalZonesStatus>>   | null>(null);
  let nvrmTailData      = $state<Awaited<ReturnType<typeof api.nvrmTailStatus>>       | null>(null);
  let nvlinkHealthData  = $state<Awaited<ReturnType<typeof api.nvlinkHealthStatus>>   | null>(null);
  async function loadPcieRpm() {
    try { pcieRpmData = await api.pcieRpmAuditStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadThermalZones() {
    try { thermalZonesData = await api.thermalZonesStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadNvrmTail() {
    try { nvrmTailData = await api.nvrmTailStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadNvlinkHealth() {
    try { nvlinkHealthData = await api.nvlinkHealthStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }

  // ── R&D #29 (UI sprint 20) ────────────────────────────────────────────
  let kmodParamsData     = $state<Awaited<ReturnType<typeof api.kmodParamsStatus>>          | null>(null);
  let d3coldPolicyData   = $state<Awaited<ReturnType<typeof api.d3coldPolicyStatus>>        | null>(null);
  let thermalSlowdownData = $state<Awaited<ReturnType<typeof api.thermalSlowdownKindStatus>> | null>(null);
  let rlimitAuditData    = $state<Awaited<ReturnType<typeof api.rlimitAuditStatus>>         | null>(null);
  async function loadKmodParams() {
    try { kmodParamsData = await api.kmodParamsStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadD3coldPolicy() {
    try { d3coldPolicyData = await api.d3coldPolicyStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadThermalSlowdown() {
    try { thermalSlowdownData = await api.thermalSlowdownKindStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadRlimitAudit() {
    try { rlimitAuditData = await api.rlimitAuditStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }

  // ── R&D #30 (UI sprint 21) ────────────────────────────────────────────
  let dmiBiosData      = $state<Awaited<ReturnType<typeof api.dmiBiosStatus>>      | null>(null);
  let nvmeIoschedData  = $state<Awaited<ReturnType<typeof api.nvmeIoschedStatus>>  | null>(null);
  let iommuGroupsData  = $state<Awaited<ReturnType<typeof api.iommuGroupsStatus>>  | null>(null);
  let msiInventoryData = $state<Awaited<ReturnType<typeof api.msiInventoryStatus>> | null>(null);
  async function loadDmiBios() {
    try { dmiBiosData = await api.dmiBiosStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadNvmeIosched() {
    try { nvmeIoschedData = await api.nvmeIoschedStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadIommuGroups() {
    try { iommuGroupsData = await api.iommuGroupsStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadMsiInventory() {
    try { msiInventoryData = await api.msiInventoryStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }

  // ── R&D #31 (UI sprint 22) ────────────────────────────────────────────
  let oomPriorityData    = $state<Awaited<ReturnType<typeof api.oomPriorityStatus>>    | null>(null);
  let cpuTopologyData    = $state<Awaited<ReturnType<typeof api.cpuTopologyStatus>>    | null>(null);
  let procSmapsData      = $state<Awaited<ReturnType<typeof api.procSmapsStatus>>      | null>(null);
  let hwmonInventoryData = $state<Awaited<ReturnType<typeof api.hwmonInventoryStatus>> | null>(null);
  async function loadOomPriority() {
    try { oomPriorityData = await api.oomPriorityStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadCpuTopology() {
    try { cpuTopologyData = await api.cpuTopologyStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadProcSmaps() {
    try { procSmapsData = await api.procSmapsStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadHwmonInventory() {
    try { hwmonInventoryData = await api.hwmonInventoryStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }

  // ── R&D #32 (UI sprint 23) ────────────────────────────────────────────
  let vmSysctlData     = $state<Awaited<ReturnType<typeof api.vmSysctlStatus>>     | null>(null);
  let psiPressureData  = $state<Awaited<ReturnType<typeof api.psiPressureStatus>>  | null>(null);
  let procWchanData    = $state<Awaited<ReturnType<typeof api.procWchanStatus>>    | null>(null);
  let cgroupMemcapData = $state<Awaited<ReturnType<typeof api.cgroupMemcapStatus>> | null>(null);
  async function loadVmSysctl() {
    try { vmSysctlData = await api.vmSysctlStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadPsiPressure() {
    try { psiPressureData = await api.psiPressureStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadProcWchan() {
    try { procWchanData = await api.procWchanStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadCgroupMemcap() {
    try { cgroupMemcapData = await api.cgroupMemcapStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }

  // ── R&D #33 (UI sprint 24) ────────────────────────────────────────────
  let clocksourceData   = $state<Awaited<ReturnType<typeof api.clocksourceStatus>>   | null>(null);
  let nicHealthData     = $state<Awaited<ReturnType<typeof api.nicHealthStatus>>     | null>(null);
  let procIoData        = $state<Awaited<ReturnType<typeof api.procIoStatus>>        | null>(null);
  let cgroupCpuioData   = $state<Awaited<ReturnType<typeof api.cgroupCpuioStatus>>   | null>(null);
  async function loadClocksource() {
    try { clocksourceData = await api.clocksourceStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadNicHealth() {
    try { nicHealthData = await api.nicHealthStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadProcIo() {
    try { procIoData = await api.procIoStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadCgroupCpuio() {
    try { cgroupCpuioData = await api.cgroupCpuioStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }

  // ── R&D #34 (UI sprint 25) ────────────────────────────────────────────
  let thpAuditData    = $state<Awaited<ReturnType<typeof api.thpAuditStatus>>    | null>(null);
  let buddyinfoData   = $state<Awaited<ReturnType<typeof api.buddyinfoStatus>>   | null>(null);
  let procSchedData   = $state<Awaited<ReturnType<typeof api.procSchedStatus>>   | null>(null);
  let oomdData        = $state<Awaited<ReturnType<typeof api.oomdStatus>>        | null>(null);
  async function loadThpAudit() {
    try { thpAuditData = await api.thpAuditStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadBuddyinfo() {
    try { buddyinfoData = await api.buddyinfoStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadProcSched() {
    try { procSchedData = await api.procSchedStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadOomd() {
    try { oomdData = await api.oomdStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }

  // ── R&D #35 (UI sprint 26) ────────────────────────────────────────────
  let cpuBoostData      = $state<Awaited<ReturnType<typeof api.cpuBoostStatus>>      | null>(null);
  let netSysctlData     = $state<Awaited<ReturnType<typeof api.netSysctlStatus>>     | null>(null);
  let smtAuditData      = $state<Awaited<ReturnType<typeof api.smtAuditStatus>>      | null>(null);
  let numaPlacementData = $state<Awaited<ReturnType<typeof api.numaPlacementStatus>> | null>(null);
  async function loadCpuBoost() {
    try { cpuBoostData = await api.cpuBoostStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadNetSysctl() {
    try { netSysctlData = await api.netSysctlStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadSmtAudit() {
    try { smtAuditData = await api.smtAuditStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadNumaPlacement() {
    try { numaPlacementData = await api.numaPlacementStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }

  // ── R&D #36 (UI sprint 27) ────────────────────────────────────────────
  let kernelTaintData  = $state<Awaited<ReturnType<typeof api.kernelTaintStatus>>  | null>(null);
  let cpuMicrocodeData = $state<Awaited<ReturnType<typeof api.cpuMicrocodeStatus>> | null>(null);
  let hwpEppData       = $state<Awaited<ReturnType<typeof api.hwpEppStatus>>       | null>(null);
  let cpuidleData      = $state<Awaited<ReturnType<typeof api.cpuidleStatus>>      | null>(null);
  async function loadKernelTaint() {
    try { kernelTaintData = await api.kernelTaintStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadCpuMicrocode() {
    try { cpuMicrocodeData = await api.cpuMicrocodeStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadHwpEpp() {
    try { hwpEppData = await api.hwpEppStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadCpuidle() {
    try { cpuidleData = await api.cpuidleStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }

  // ── R&D #37 (UI sprint 28) + PAM limits (bonus) ──────────────────────
  let cpuVulnsData       = $state<Awaited<ReturnType<typeof api.cpuVulnsStatus>>       | null>(null);
  let hwWatchdogData     = $state<Awaited<ReturnType<typeof api.hwWatchdogStatus>>     | null>(null);
  let gpuCpuAffinityData = $state<Awaited<ReturnType<typeof api.gpuCpuAffinityStatus>> | null>(null);
  let cacheTopologyData  = $state<Awaited<ReturnType<typeof api.cacheTopologyStatus>>  | null>(null);
  let limitsAuditData    = $state<Awaited<ReturnType<typeof api.limitsAuditStatus>>    | null>(null);
  async function loadCpuVulns() {
    try { cpuVulnsData = await api.cpuVulnsStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadHwWatchdog() {
    try { hwWatchdogData = await api.hwWatchdogStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadGpuCpuAffinity() {
    try { gpuCpuAffinityData = await api.gpuCpuAffinityStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadCacheTopology() {
    try { cacheTopologyData = await api.cacheTopologyStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadLimitsAudit() {
    try { limitsAuditData = await api.limitsAuditStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }

  // ── R&D #38 (UI sprint 29) ────────────────────────────────────────────
  let pcieAerTrendData   = $state<Awaited<ReturnType<typeof api.pcieAerTrendStatus>>   | null>(null);
  let gpuIrqAffinityData = $state<Awaited<ReturnType<typeof api.gpuIrqAffinityStatus>> | null>(null);
  let modprobeAuditData  = $state<Awaited<ReturnType<typeof api.modprobeAuditStatus>>  | null>(null);
  let procMapsLibsData   = $state<Awaited<ReturnType<typeof api.procMapsLibsStatus>>   | null>(null);
  async function loadPcieAerTrend() {
    try { pcieAerTrendData = await api.pcieAerTrendStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadGpuIrqAffinity() {
    try { gpuIrqAffinityData = await api.gpuIrqAffinityStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadModprobeAudit() {
    try { modprobeAuditData = await api.modprobeAuditStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadProcMapsLibs() {
    try { procMapsLibsData = await api.procMapsLibsStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }

  // ── R&D #39 (UI sprint 30) ────────────────────────────────────────────
  let cmdlineAuditData = $state<Awaited<ReturnType<typeof api.cmdlineAuditStatus>> | null>(null);
  let coredumpData     = $state<Awaited<ReturnType<typeof api.coredumpStatus>>     | null>(null);
  let hostClassData    = $state<Awaited<ReturnType<typeof api.hostClassStatus>>    | null>(null);
  let sysctlDAuditData = $state<Awaited<ReturnType<typeof api.sysctlDAuditStatus>> | null>(null);
  async function loadCmdlineAudit() {
    try { cmdlineAuditData = await api.cmdlineAuditStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadCoredump() {
    try { coredumpData = await api.coredumpStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadHostClass() {
    try { hostClassData = await api.hostClassStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadSysctlDAudit() {
    try { sysctlDAuditData = await api.sysctlDAuditStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }

  // ── R&D #40 (UI sprint 31) ────────────────────────────────────────────
  let ksmAdvisorData     = $state<Awaited<ReturnType<typeof api.ksmAdvisorStatus>>     | null>(null);
  let vmTuningDeepData   = $state<Awaited<ReturnType<typeof api.vmTuningDeepStatus>>   | null>(null);
  let gpuPciBindData     = $state<Awaited<ReturnType<typeof api.gpuPciBindStatus>>     | null>(null);
  let nicQueueAffinityData = $state<Awaited<ReturnType<typeof api.nicQueueAffinityStatus>> | null>(null);
  async function loadKsmAdvisor() {
    try { ksmAdvisorData = await api.ksmAdvisorStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadVmTuningDeep() {
    try { vmTuningDeepData = await api.vmTuningDeepStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadGpuPciBind() {
    try { gpuPciBindData = await api.gpuPciBindStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadNicQueueAffinity() {
    try { nicQueueAffinityData = await api.nicQueueAffinityStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }

  // ── R&D #41 (UI sprint 32) ────────────────────────────────────────────
  let panicPolicyData    = $state<Awaited<ReturnType<typeof api.panicPolicyStatus>>    | null>(null);
  let edacRamEccData     = $state<Awaited<ReturnType<typeof api.edacRamEccStatus>>     | null>(null);
  let inotifyAuditData   = $state<Awaited<ReturnType<typeof api.inotifyAuditStatus>>   | null>(null);
  let zswapZramData      = $state<Awaited<ReturnType<typeof api.zswapZramStatus>>      | null>(null);
  async function loadPanicPolicy() {
    try { panicPolicyData = await api.panicPolicyStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadEdacRamEcc() {
    try { edacRamEccData = await api.edacRamEccStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadInotifyAudit() {
    try { inotifyAuditData = await api.inotifyAuditStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadZswapZram() {
    try { zswapZramData = await api.zswapZramStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }

  // ── R&D #42 (UI sprint 33) ────────────────────────────────────────────
  let cpuEpbData          = $state<Awaited<ReturnType<typeof api.cpuEpbStatus>>          | null>(null);
  let coolingDevicesData  = $state<Awaited<ReturnType<typeof api.coolingDevicesStatus>>  | null>(null);
  let hybridCpuTopoData   = $state<Awaited<ReturnType<typeof api.hybridCpuTopoStatus>>   | null>(null);
  let fileLocksAuditData  = $state<Awaited<ReturnType<typeof api.fileLocksAuditStatus>>  | null>(null);
  async function loadCpuEpb() {
    try { cpuEpbData = await api.cpuEpbStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadCoolingDevices() {
    try { coolingDevicesData = await api.coolingDevicesStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadHybridCpuTopo() {
    try { hybridCpuTopoData = await api.hybridCpuTopoStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadFileLocksAudit() {
    try { fileLocksAuditData = await api.fileLocksAuditStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }

  // ── R&D #43 (UI sprint 34) ────────────────────────────────────────────
  let nicRingAuditData    = $state<Awaited<ReturnType<typeof api.nicRingAuditStatus>>    | null>(null);
  let irqRatesAuditData   = $state<Awaited<ReturnType<typeof api.irqRatesAuditStatus>>   | null>(null);
  let zoneinfoAuditData   = $state<Awaited<ReturnType<typeof api.zoneinfoAuditStatus>>   | null>(null);
  let blockQueueAuditData = $state<Awaited<ReturnType<typeof api.blockQueueAuditStatus>> | null>(null);
  async function loadNicRingAudit() {
    try { nicRingAuditData = await api.nicRingAuditStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadIrqRatesAudit() {
    try { irqRatesAuditData = await api.irqRatesAuditStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadZoneinfoAudit() {
    try { zoneinfoAuditData = await api.zoneinfoAuditStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadBlockQueueAudit() {
    try { blockQueueAuditData = await api.blockQueueAuditStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }

  // ── R&D #44 (UI sprint 35) ────────────────────────────────────────────
  let watchdogInventoryData = $state<Awaited<ReturnType<typeof api.watchdogInventoryStatus>> | null>(null);
  let diskIoLatencyData     = $state<Awaited<ReturnType<typeof api.diskIoLatencyStatus>>     | null>(null);
  let netProtoCountersData  = $state<Awaited<ReturnType<typeof api.netProtoCountersStatus>>  | null>(null);
  let slabAuditData         = $state<Awaited<ReturnType<typeof api.slabAuditStatus>>         | null>(null);
  async function loadWatchdogInventory() {
    try { watchdogInventoryData = await api.watchdogInventoryStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadDiskIoLatency() {
    try { diskIoLatencyData = await api.diskIoLatencyStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadNetProtoCounters() {
    try { netProtoCountersData = await api.netProtoCountersStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadSlabAudit() {
    try { slabAuditData = await api.slabAuditStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }

  // ── R&D #45 (UI sprint 36) ────────────────────────────────────────────
  let entropyAuditData      = $state<Awaited<ReturnType<typeof api.entropyAuditStatus>>      | null>(null);
  let nfConntrackAuditData  = $state<Awaited<ReturnType<typeof api.nfConntrackAuditStatus>>  | null>(null);
  let sysvipcAuditData      = $state<Awaited<ReturnType<typeof api.sysvipcAuditStatus>>      | null>(null);
  let mdraidHealthData      = $state<Awaited<ReturnType<typeof api.mdraidHealthStatus>>      | null>(null);
  async function loadEntropyAudit() {
    try { entropyAuditData = await api.entropyAuditStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadNfConntrackAudit() {
    try { nfConntrackAuditData = await api.nfConntrackAuditStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadSysvipcAudit() {
    try { sysvipcAuditData = await api.sysvipcAuditStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadMdraidHealth() {
    try { mdraidHealthData = await api.mdraidHealthStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }

  // ── R&D #46 (UI sprint 37) ────────────────────────────────────────────
  let keyringAuditData    = $state<Awaited<ReturnType<typeof api.keyringAuditStatus>>    | null>(null);
  let securityPostureData = $state<Awaited<ReturnType<typeof api.securityPostureStatus>> | null>(null);
  let vfsLimitsAuditData  = $state<Awaited<ReturnType<typeof api.vfsLimitsAuditStatus>>  | null>(null);
  async function loadKeyringAudit() {
    try { keyringAuditData = await api.keyringAuditStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadSecurityPosture() {
    try { securityPostureData = await api.securityPostureStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadVfsLimitsAudit() {
    try { vfsLimitsAuditData = await api.vfsLimitsAuditStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }

  // ── R&D #47 (UI sprint 38) ────────────────────────────────────────────
  let nvidiaRmAuditData = $state<Awaited<ReturnType<typeof api.nvidiaRmAuditStatus>> | null>(null);
  let mceAuditData      = $state<Awaited<ReturnType<typeof api.mceAuditStatus>>      | null>(null);
  let acpiAuditData     = $state<Awaited<ReturnType<typeof api.acpiAuditStatus>>     | null>(null);
  let schedAuditData    = $state<Awaited<ReturnType<typeof api.schedAuditStatus>>    | null>(null);
  async function loadNvidiaRmAudit() {
    try { nvidiaRmAuditData = await api.nvidiaRmAuditStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadMceAudit() {
    try { mceAuditData = await api.mceAuditStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadAcpiAudit() {
    try { acpiAuditData = await api.acpiAuditStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadSchedAudit() {
    try { schedAuditData = await api.schedAuditStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }

  // ── R&D #48 (UI sprint 39) ────────────────────────────────────────────
  let dmaAuditData          = $state<Awaited<ReturnType<typeof api.dmaAuditStatus>>          | null>(null);
  let ftraceAuditData       = $state<Awaited<ReturnType<typeof api.ftraceAuditStatus>>       | null>(null);
  let usbTopologyAuditData  = $state<Awaited<ReturnType<typeof api.usbTopologyAuditStatus>>  | null>(null);
  let journalAuditData      = $state<Awaited<ReturnType<typeof api.journalAuditStatus>>      | null>(null);
  async function loadDmaAudit() {
    try { dmaAuditData = await api.dmaAuditStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadFtraceAudit() {
    try { ftraceAuditData = await api.ftraceAuditStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadUsbTopologyAudit() {
    try { usbTopologyAuditData = await api.usbTopologyAuditStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadJournalAudit() {
    try { journalAuditData = await api.journalAuditStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }

  // ── R&D #49 (UI sprint 40) ────────────────────────────────────────────
  let rtcClockAuditData    = $state<Awaited<ReturnType<typeof api.rtcClockAuditStatus>>    | null>(null);
  let tpmAuditData         = $state<Awaited<ReturnType<typeof api.tpmAuditStatus>>         | null>(null);
  let wmiVendorAuditData   = $state<Awaited<ReturnType<typeof api.wmiVendorAuditStatus>>   | null>(null);
  let kmsgAuditData        = $state<Awaited<ReturnType<typeof api.kmsgAuditStatus>>        | null>(null);
  async function loadRtcClockAudit() {
    try { rtcClockAuditData = await api.rtcClockAuditStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadTpmAudit() {
    try { tpmAuditData = await api.tpmAuditStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadWmiVendorAudit() {
    try { wmiVendorAuditData = await api.wmiVendorAuditStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadKmsgAudit() {
    try { kmsgAuditData = await api.kmsgAuditStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }

  // ── R&D #50 (UI sprint 41) ────────────────────────────────────────────
  let sockPoolAuditData          = $state<Awaited<ReturnType<typeof api.sockPoolAuditStatus>>          | null>(null);
  let iioSensorAuditData         = $state<Awaited<ReturnType<typeof api.iioSensorAuditStatus>>         | null>(null);
  let drmAuditData               = $state<Awaited<ReturnType<typeof api.drmAuditStatus>>               | null>(null);
  let cgroupMemeventsAuditData   = $state<Awaited<ReturnType<typeof api.cgroupMemeventsAuditStatus>>   | null>(null);
  async function loadSockPoolAudit() {
    try { sockPoolAuditData = await api.sockPoolAuditStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadIioSensorAudit() {
    try { iioSensorAuditData = await api.iioSensorAuditStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadDrmAudit() {
    try { drmAuditData = await api.drmAuditStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadCgroupMemeventsAudit() {
    try { cgroupMemeventsAuditData = await api.cgroupMemeventsAuditStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }

  // ── R&D #51 (UI sprint 42) ────────────────────────────────────────────
  let powerSupplyAuditData     = $state<Awaited<ReturnType<typeof api.powerSupplyAuditStatus>>     | null>(null);
  let typecAuditData           = $state<Awaited<ReturnType<typeof api.typecAuditStatus>>           | null>(null);
  let perfPmuAuditData         = $state<Awaited<ReturnType<typeof api.perfPmuAuditStatus>>         | null>(null);
  let iomemPciAuditData        = $state<Awaited<ReturnType<typeof api.iomemPciAuditStatus>>        | null>(null);
  async function loadPowerSupplyAudit() {
    try { powerSupplyAuditData = await api.powerSupplyAuditStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadTypecAudit() {
    try { typecAuditData = await api.typecAuditStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadPerfPmuAudit() {
    try { perfPmuAuditData = await api.perfPmuAuditStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadIomemPciAudit() {
    try { iomemPciAuditData = await api.iomemPciAuditStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }

  // ── R&D #52 (UI sprint 43) ────────────────────────────────────────────
  let ksmAuditData               = $state<Awaited<ReturnType<typeof api.ksmAuditStatus>>               | null>(null);
  let i2cSmbusAuditData          = $state<Awaited<ReturnType<typeof api.i2cSmbusAuditStatus>>          | null>(null);
  let moduleIntegrityAuditData   = $state<Awaited<ReturnType<typeof api.moduleIntegrityAuditStatus>>   | null>(null);
  async function loadKsmAudit() {
    try { ksmAuditData = await api.ksmAuditStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadI2cSmbusAudit() {
    try { i2cSmbusAuditData = await api.i2cSmbusAuditStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadModuleIntegrityAudit() {
    try { moduleIntegrityAuditData = await api.moduleIntegrityAuditStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }

  // ── R&D #53 (UI sprint 44) ────────────────────────────────────────────
  let psiPressureAuditData       = $state<Awaited<ReturnType<typeof api.psiPressureAuditStatus>>       | null>(null);
  let cpuVulnerabilitiesAuditData= $state<Awaited<ReturnType<typeof api.cpuVulnerabilitiesAuditStatus>>| null>(null);
  let imaIntegrityAuditData      = $state<Awaited<ReturnType<typeof api.imaIntegrityAuditStatus>>      | null>(null);
  let raplPowerCapAuditData      = $state<Awaited<ReturnType<typeof api.raplPowerCapAuditStatus>>      | null>(null);
  async function loadPsiPressureAudit() {
    try { psiPressureAuditData = await api.psiPressureAuditStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadCpuVulnerabilitiesAudit() {
    try { cpuVulnerabilitiesAuditData = await api.cpuVulnerabilitiesAuditStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadImaIntegrityAudit() {
    try { imaIntegrityAuditData = await api.imaIntegrityAuditStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadRaplPowerCapAudit() {
    try { raplPowerCapAuditData = await api.raplPowerCapAuditStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }

  // ── R&D #54 (UI sprint 45) ────────────────────────────────────────────
  let swapTunablesAuditData      = $state<Awaited<ReturnType<typeof api.swapTunablesAuditStatus>>      | null>(null);
  let hugepagesAuditData         = $state<Awaited<ReturnType<typeof api.hugepagesAuditStatus>>         | null>(null);
  let kvmMiscAuditData           = $state<Awaited<ReturnType<typeof api.kvmMiscAuditStatus>>           | null>(null);
  let ioUringRuntimeAuditData    = $state<Awaited<ReturnType<typeof api.ioUringRuntimeAuditStatus>>    | null>(null);
  async function loadSwapTunablesAudit() {
    try { swapTunablesAuditData = await api.swapTunablesAuditStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadHugepagesAudit() {
    try { hugepagesAuditData = await api.hugepagesAuditStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadKvmMiscAudit() {
    try { kvmMiscAuditData = await api.kvmMiscAuditStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadIoUringRuntimeAudit() {
    try { ioUringRuntimeAuditData = await api.ioUringRuntimeAuditStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }

  // ── R&D #55 (UI sprint 46) ────────────────────────────────────────────
  let edacEccAuditData           = $state<Awaited<ReturnType<typeof api.edacEccAuditStatus>>           | null>(null);
  let numaTopologyAuditData      = $state<Awaited<ReturnType<typeof api.numaTopologyAuditStatus>>      | null>(null);
  let hwmonSensorsAuditData      = $state<Awaited<ReturnType<typeof api.hwmonSensorsAuditStatus>>      | null>(null);
  let efiBootOrderAuditData      = $state<Awaited<ReturnType<typeof api.efiBootOrderAuditStatus>>      | null>(null);
  async function loadEdacEccAudit() {
    try { edacEccAuditData = await api.edacEccAuditStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadNumaTopologyAudit() {
    try { numaTopologyAuditData = await api.numaTopologyAuditStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadHwmonSensorsAudit() {
    try { hwmonSensorsAuditData = await api.hwmonSensorsAuditStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadEfiBootOrderAudit() {
    try { efiBootOrderAuditData = await api.efiBootOrderAuditStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }

  // ── R&D #56 (UI sprint 47) ────────────────────────────────────────────
  let sataLinkPmAuditData        = $state<Awaited<ReturnType<typeof api.sataLinkPmAuditStatus>>        | null>(null);
  let bdiWritebackAuditData      = $state<Awaited<ReturnType<typeof api.bdiWritebackAuditStatus>>      | null>(null);
  let procCryptoAuditData        = $state<Awaited<ReturnType<typeof api.procCryptoAuditStatus>>        | null>(null);
  let wakeupSourcesAuditData     = $state<Awaited<ReturnType<typeof api.wakeupSourcesAuditStatus>>     | null>(null);
  async function loadSataLinkPmAudit() {
    try { sataLinkPmAuditData = await api.sataLinkPmAuditStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadBdiWritebackAudit() {
    try { bdiWritebackAuditData = await api.bdiWritebackAuditStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadProcCryptoAudit() {
    try { procCryptoAuditData = await api.procCryptoAuditStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadWakeupSourcesAudit() {
    try { wakeupSourcesAuditData = await api.wakeupSourcesAuditStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }

  // ── R&D #57 (UI sprint 48) ────────────────────────────────────────────
  let livepatchAuditData         = $state<Awaited<ReturnType<typeof api.livepatchAuditStatus>>         | null>(null);
  let pagetypeinfoAuditData      = $state<Awaited<ReturnType<typeof api.pagetypeinfoAuditStatus>>      | null>(null);
  let backlightPwmAuditData      = $state<Awaited<ReturnType<typeof api.backlightPwmAuditStatus>>      | null>(null);
  let loadavgPressureAuditData   = $state<Awaited<ReturnType<typeof api.loadavgPressureAuditStatus>>   | null>(null);
  async function loadLivepatchAudit() {
    try { livepatchAuditData = await api.livepatchAuditStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadPagetypeinfoAudit() {
    try { pagetypeinfoAuditData = await api.pagetypeinfoAuditStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadBacklightPwmAudit() {
    try { backlightPwmAuditData = await api.backlightPwmAuditStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadLoadavgPressureAudit() {
    try { loadavgPressureAuditData = await api.loadavgPressureAuditStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }

  // ── R&D #58 (UI sprint 49) ────────────────────────────────────────────
  let cgroupRootAuditData        = $state<Awaited<ReturnType<typeof api.cgroupRootAuditStatus>>        | null>(null);
  let kernelBuildConfigAuditData = $state<Awaited<ReturnType<typeof api.kernelBuildConfigAuditStatus>> | null>(null);
  let scsiTransportAuditData     = $state<Awaited<ReturnType<typeof api.scsiTransportAuditStatus>>     | null>(null);
  let alsaCardsAuditData         = $state<Awaited<ReturnType<typeof api.alsaCardsAuditStatus>>         | null>(null);
  async function loadCgroupRootAudit() {
    try { cgroupRootAuditData = await api.cgroupRootAuditStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadKernelBuildConfigAudit() {
    try { kernelBuildConfigAuditData = await api.kernelBuildConfigAuditStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadScsiTransportAudit() {
    try { scsiTransportAuditData = await api.scsiTransportAuditStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadAlsaCardsAudit() {
    try { alsaCardsAuditData = await api.alsaCardsAuditStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }

  // ── R&D #59 (UI sprint 50) ────────────────────────────────────────────
  let dmiSmbiosAuditData         = $state<Awaited<ReturnType<typeof api.dmiSmbiosAuditStatus>>         | null>(null);
  let iommuGroupsAuditData       = $state<Awaited<ReturnType<typeof api.iommuGroupsAuditStatus>>       | null>(null);
  let pidRlimitsAuditData        = $state<Awaited<ReturnType<typeof api.pidRlimitsAuditStatus>>        | null>(null);
  async function loadDmiSmbiosAudit() {
    try { dmiSmbiosAuditData = await api.dmiSmbiosAuditStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadIommuGroupsAudit() {
    try { iommuGroupsAuditData = await api.iommuGroupsAuditStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadPidRlimitsAudit() {
    try { pidRlimitsAuditData = await api.pidRlimitsAuditStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }

  // ── R&D #60 (UI sprint 51) ────────────────────────────────────────────
  let virtGuestDetectAuditData   = $state<Awaited<ReturnType<typeof api.virtGuestDetectAuditStatus>>   | null>(null);
  async function loadVirtGuestDetectAudit() {
    try { virtGuestDetectAuditData = await api.virtGuestDetectAuditStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }

  // ── R&D #61 (UI sprint 52) ────────────────────────────────────────────
  let regulatorAuditData         = $state<Awaited<ReturnType<typeof api.regulatorAuditStatus>>         | null>(null);
  let alsaCodecDeepAuditData     = $state<Awaited<ReturnType<typeof api.alsaCodecDeepAuditStatus>>     | null>(null);
  async function loadRegulatorAudit() {
    try { regulatorAuditData = await api.regulatorAuditStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadAlsaCodecDeepAudit() {
    try { alsaCodecDeepAuditData = await api.alsaCodecDeepAuditStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }

  // ── R&D #62 (UI sprint 53) ────────────────────────────────────────────
  let devfreqAuditData           = $state<Awaited<ReturnType<typeof api.devfreqAuditStatus>>           | null>(null);
  let meiIntelMeAuditData        = $state<Awaited<ReturnType<typeof api.meiIntelMeAuditStatus>>        | null>(null);
  let memoryHotplugAuditData     = $state<Awaited<ReturnType<typeof api.memoryHotplugAuditStatus>>     | null>(null);
  let procTaskAffinityAuditData  = $state<Awaited<ReturnType<typeof api.procTaskAffinityAuditStatus>>  | null>(null);
  async function loadDevfreqAudit() {
    try { devfreqAuditData = await api.devfreqAuditStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadMeiIntelMeAudit() {
    try { meiIntelMeAuditData = await api.meiIntelMeAuditStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadMemoryHotplugAudit() {
    try { memoryHotplugAuditData = await api.memoryHotplugAuditStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadProcTaskAffinityAudit() {
    try { procTaskAffinityAuditData = await api.procTaskAffinityAuditStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }

  // ── R&D #63 (UI sprint 54) ────────────────────────────────────────────
  let rfkillBluetoothAuditData   = $state<Awaited<ReturnType<typeof api.rfkillBluetoothAuditStatus>>   | null>(null);
  let ledsClassAuditData         = $state<Awaited<ReturnType<typeof api.ledsClassAuditStatus>>         | null>(null);
  let binfmtMiscAuditData        = $state<Awaited<ReturnType<typeof api.binfmtMiscAuditStatus>>        | null>(null);
  let ptpClockAuditData          = $state<Awaited<ReturnType<typeof api.ptpClockAuditStatus>>          | null>(null);
  async function loadRfkillBluetoothAudit() {
    try { rfkillBluetoothAuditData = await api.rfkillBluetoothAuditStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadLedsClassAudit() {
    try { ledsClassAuditData = await api.ledsClassAuditStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadBinfmtMiscAudit() {
    try { binfmtMiscAuditData = await api.binfmtMiscAuditStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadPtpClockAudit() {
    try { ptpClockAuditData = await api.ptpClockAuditStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }

  // ── R&D #64 (UI sprint 55) ────────────────────────────────────────────
  let meiHdcpPxpAuditData        = $state<Awaited<ReturnType<typeof api.meiHdcpPxpAuditStatus>>        | null>(null);
  let firmwareEddMmcAuditData    = $state<Awaited<ReturnType<typeof api.firmwareEddMmcAuditStatus>>    | null>(null);
  let devlinkSmartnicAuditData   = $state<Awaited<ReturnType<typeof api.devlinkSmartnicAuditStatus>>   | null>(null);
  let procNsMountinfoAuditData   = $state<Awaited<ReturnType<typeof api.procNsMountinfoAuditStatus>>   | null>(null);
  async function loadMeiHdcpPxpAudit() {
    try { meiHdcpPxpAuditData = await api.meiHdcpPxpAuditStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadFirmwareEddMmcAudit() {
    try { firmwareEddMmcAuditData = await api.firmwareEddMmcAuditStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadDevlinkSmartnicAudit() {
    try { devlinkSmartnicAuditData = await api.devlinkSmartnicAuditStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadProcNsMountinfoAudit() {
    try { procNsMountinfoAuditData = await api.procNsMountinfoAuditStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }

  // ── R&D #65 (UI sprint 56) ────────────────────────────────────────────
  let cpuidleResidencyAuditData  = $state<Awaited<ReturnType<typeof api.cpuidleResidencyAuditStatus>>  | null>(null);
  let cpufreqResidencyAuditData  = $state<Awaited<ReturnType<typeof api.cpufreqResidencyAuditStatus>>  | null>(null);
  let efiRuntimeMapAuditData     = $state<Awaited<ReturnType<typeof api.efiRuntimeMapAuditStatus>>     | null>(null);
  let devfreqEventAuditData      = $state<Awaited<ReturnType<typeof api.devfreqEventAuditStatus>>      | null>(null);
  async function loadCpuidleResidencyAudit() {
    try { cpuidleResidencyAuditData = await api.cpuidleResidencyAuditStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadCpufreqResidencyAudit() {
    try { cpufreqResidencyAuditData = await api.cpufreqResidencyAuditStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadEfiRuntimeMapAudit() {
    try { efiRuntimeMapAuditData = await api.efiRuntimeMapAuditStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadDevfreqEventAudit() {
    try { devfreqEventAuditData = await api.devfreqEventAuditStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }

  // ── R&D #66 (UI sprint 57) ────────────────────────────────────────────
  let mtdFlashAuditData               = $state<Awaited<ReturnType<typeof api.mtdFlashAuditStatus>>               | null>(null);
  let spiFirmwareLoaderAuditData      = $state<Awaited<ReturnType<typeof api.spiFirmwareLoaderAuditStatus>>      | null>(null);
  let procSyscallAuxvAuditData        = $state<Awaited<ReturnType<typeof api.procSyscallAuxvAuditStatus>>        | null>(null);
  let btfBpfAuditData                 = $state<Awaited<ReturnType<typeof api.btfBpfAuditStatus>>                 | null>(null);
  async function loadMtdFlashAudit() {
    try { mtdFlashAuditData = await api.mtdFlashAuditStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadSpiFirmwareLoaderAudit() {
    try { spiFirmwareLoaderAuditData = await api.spiFirmwareLoaderAuditStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadProcSyscallAuxvAudit() {
    try { procSyscallAuxvAuditData = await api.procSyscallAuxvAuditStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadBtfBpfAudit() {
    try { btfBpfAuditData = await api.btfBpfAuditStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }

  async function loadDkmsStatus() {
    try { dkmsStatusData = await api.dkmsStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadPcieAer() {
    try { pcieAerData = await api.pcieAerStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadMemTempDrift() {
    try { memTempDriftData = await api.memTempDriftStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadAccounting() {
    try { accountingData = await api.accountingStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadProcDeep() {
    try { procDeepData = await api.procDeepStateStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadPcieAspm() {
    try { pcieAspmData = await api.pcieAspmStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadFsAudit() {
    try { fsAuditData = await api.fsMountAuditStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadBatchAdvisor() {
    try { batchAdvisorData = await api.batchAdvisorStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadVramLeak() {
    try { vramLeakData = await api.vramLeakStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadGpuReset() {
    try { gpuResetData = await api.gpuResetStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadCudaInv() {
    try { cudaInvData = await api.cudaInventoryStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadDriverFlavor() {
    try { driverFlavorData = await api.driverFlavorStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadPstateAudit() {
    try { pstateAuditData = await api.pstateAuditStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadPersistence() {
    try { persistenceData = await api.persistenceModeStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadGspStatus() {
    try { gspStatusData = await api.gspStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
  }
  async function loadSdCache() {
    try { sdCacheData = await api.sdCacheJanitorStatus(); }
    catch (e) { toast.emit("✗ " + (e as Error).message, "err"); }
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
      // R&D #20 cards
      if (!suspendGuardData)   loadSuspendGuard();
      if (!containerAuditData) loadContainerAudit();
      if (!upsRuntimeData)     loadUpsRuntime();
      if (!vbiosDriftData)     loadVbiosDrift();
      // R&D #21 cards
      if (!pstateAuditData)    loadPstateAudit();
      if (!persistenceData)    loadPersistence();
      if (!gspStatusData)      loadGspStatus();
      if (!sdCacheData)        loadSdCache();
      // R&D #22 cards
      if (!vramLeakData)       loadVramLeak();
      if (!gpuResetData)       loadGpuReset();
      if (!cudaInvData)        loadCudaInv();
      if (!driverFlavorData)   loadDriverFlavor();
      // R&D #23 cards
      if (!procDeepData)       loadProcDeep();
      if (!pcieAspmData)       loadPcieAspm();
      if (!fsAuditData)        loadFsAudit();
      if (!batchAdvisorData)   loadBatchAdvisor();
      // R&D #24 cards
      if (!dkmsStatusData)     loadDkmsStatus();
      if (!pcieAerData)        loadPcieAer();
      if (!memTempDriftData)   loadMemTempDrift();
      if (!accountingData)     loadAccounting();
      // R&D #25 cards
      if (!trimAuditData)      loadTrimAudit();
      if (!throttleBitsData)   loadThrottleBits();
      if (!retiredPagesData)   loadRetiredPages();
      if (!bugRepPrepData)     loadBugRepPrep();
      // R&D #26 cards
      if (!pcieWidthData)      loadPcieWidth();
      if (!cudaCtxLeakData)    loadCudaCtxLeak();
      if (!procStaticData)     loadProcStatic();
      if (!memBwGaugeData)     loadMemBwGauge();
      // R&D #27 cards
      if (!pwrEnvDriftData)    loadPwrEnvDrift();
      if (!rebarAuditData)     loadRebarAudit();
      if (!cpuRaplData)        loadCpuRapl();
      if (!clockGapData)       loadClockGap();
      // R&D #28 cards
      if (!pcieRpmData)        loadPcieRpm();
      if (!thermalZonesData)   loadThermalZones();
      if (!nvrmTailData)       loadNvrmTail();
      if (!nvlinkHealthData)   loadNvlinkHealth();
      // R&D #29 cards
      if (!kmodParamsData)     loadKmodParams();
      if (!d3coldPolicyData)   loadD3coldPolicy();
      if (!thermalSlowdownData) loadThermalSlowdown();
      if (!rlimitAuditData)    loadRlimitAudit();
      // R&D #30 cards
      if (!dmiBiosData)        loadDmiBios();
      if (!nvmeIoschedData)    loadNvmeIosched();
      if (!iommuGroupsData)    loadIommuGroups();
      if (!msiInventoryData)   loadMsiInventory();
      // R&D #31 cards
      if (!oomPriorityData)    loadOomPriority();
      if (!cpuTopologyData)    loadCpuTopology();
      if (!procSmapsData)      loadProcSmaps();
      if (!hwmonInventoryData) loadHwmonInventory();
      // R&D #32 cards
      if (!vmSysctlData)       loadVmSysctl();
      if (!psiPressureData)    loadPsiPressure();
      if (!procWchanData)      loadProcWchan();
      if (!cgroupMemcapData)   loadCgroupMemcap();
      // R&D #33 cards
      if (!clocksourceData)    loadClocksource();
      if (!nicHealthData)      loadNicHealth();
      if (!procIoData)         loadProcIo();
      if (!cgroupCpuioData)    loadCgroupCpuio();
      // R&D #34 cards
      if (!thpAuditData)       loadThpAudit();
      if (!buddyinfoData)      loadBuddyinfo();
      if (!procSchedData)      loadProcSched();
      if (!oomdData)           loadOomd();
      // R&D #35 cards
      if (!cpuBoostData)       loadCpuBoost();
      if (!netSysctlData)      loadNetSysctl();
      if (!smtAuditData)       loadSmtAudit();
      if (!numaPlacementData)  loadNumaPlacement();
      // R&D #36 cards
      if (!kernelTaintData)    loadKernelTaint();
      if (!cpuMicrocodeData)   loadCpuMicrocode();
      if (!hwpEppData)         loadHwpEpp();
      if (!cpuidleData)        loadCpuidle();
      // R&D #37 cards + PAM limits bonus
      if (!cpuVulnsData)       loadCpuVulns();
      if (!hwWatchdogData)     loadHwWatchdog();
      if (!gpuCpuAffinityData) loadGpuCpuAffinity();
      if (!cacheTopologyData)  loadCacheTopology();
      if (!limitsAuditData)    loadLimitsAudit();
      // R&D #38 cards
      if (!pcieAerTrendData)   loadPcieAerTrend();
      if (!gpuIrqAffinityData) loadGpuIrqAffinity();
      if (!modprobeAuditData)  loadModprobeAudit();
      if (!procMapsLibsData)   loadProcMapsLibs();
      // R&D #39 cards
      if (!cmdlineAuditData)   loadCmdlineAudit();
      if (!coredumpData)       loadCoredump();
      if (!hostClassData)      loadHostClass();
      if (!sysctlDAuditData)   loadSysctlDAudit();
      // R&D #40 cards
      if (!ksmAdvisorData)      loadKsmAdvisor();
      if (!vmTuningDeepData)    loadVmTuningDeep();
      if (!gpuPciBindData)      loadGpuPciBind();
      if (!nicQueueAffinityData) loadNicQueueAffinity();
      // R&D #41 cards
      if (!panicPolicyData)     loadPanicPolicy();
      if (!edacRamEccData)      loadEdacRamEcc();
      if (!inotifyAuditData)    loadInotifyAudit();
      if (!zswapZramData)       loadZswapZram();
      // R&D #42 cards
      if (!cpuEpbData)          loadCpuEpb();
      if (!coolingDevicesData)  loadCoolingDevices();
      if (!hybridCpuTopoData)   loadHybridCpuTopo();
      if (!fileLocksAuditData)  loadFileLocksAudit();
      // R&D #43 cards
      if (!nicRingAuditData)    loadNicRingAudit();
      if (!irqRatesAuditData)   loadIrqRatesAudit();
      if (!zoneinfoAuditData)   loadZoneinfoAudit();
      if (!blockQueueAuditData) loadBlockQueueAudit();
      // R&D #44 cards
      if (!watchdogInventoryData) loadWatchdogInventory();
      if (!diskIoLatencyData)     loadDiskIoLatency();
      if (!netProtoCountersData)  loadNetProtoCounters();
      if (!slabAuditData)         loadSlabAudit();
      // R&D #45 cards
      if (!entropyAuditData)      loadEntropyAudit();
      if (!nfConntrackAuditData)  loadNfConntrackAudit();
      if (!sysvipcAuditData)      loadSysvipcAudit();
      if (!mdraidHealthData)      loadMdraidHealth();
      // R&D #46 cards
      if (!keyringAuditData)      loadKeyringAudit();
      if (!securityPostureData)   loadSecurityPosture();
      if (!vfsLimitsAuditData)    loadVfsLimitsAudit();
      // R&D #47 cards
      if (!nvidiaRmAuditData)     loadNvidiaRmAudit();
      if (!mceAuditData)          loadMceAudit();
      if (!acpiAuditData)         loadAcpiAudit();
      if (!schedAuditData)        loadSchedAudit();
      // R&D #48 cards
      if (!dmaAuditData)          loadDmaAudit();
      if (!ftraceAuditData)       loadFtraceAudit();
      if (!usbTopologyAuditData)  loadUsbTopologyAudit();
      if (!journalAuditData)      loadJournalAudit();
      // R&D #49 cards
      if (!rtcClockAuditData)     loadRtcClockAudit();
      if (!tpmAuditData)          loadTpmAudit();
      if (!wmiVendorAuditData)    loadWmiVendorAudit();
      if (!kmsgAuditData)         loadKmsgAudit();
      // R&D #50 cards
      if (!sockPoolAuditData)         loadSockPoolAudit();
      if (!iioSensorAuditData)        loadIioSensorAudit();
      if (!drmAuditData)              loadDrmAudit();
      if (!cgroupMemeventsAuditData)  loadCgroupMemeventsAudit();
      // R&D #51 cards
      if (!powerSupplyAuditData)      loadPowerSupplyAudit();
      if (!typecAuditData)            loadTypecAudit();
      if (!perfPmuAuditData)          loadPerfPmuAudit();
      if (!iomemPciAuditData)         loadIomemPciAudit();
      // R&D #52 cards
      if (!ksmAuditData)              loadKsmAudit();
      if (!i2cSmbusAuditData)         loadI2cSmbusAudit();
      if (!moduleIntegrityAuditData)  loadModuleIntegrityAudit();
      // R&D #53 cards
      if (!psiPressureAuditData)        loadPsiPressureAudit();
      if (!cpuVulnerabilitiesAuditData) loadCpuVulnerabilitiesAudit();
      if (!imaIntegrityAuditData)       loadImaIntegrityAudit();
      if (!raplPowerCapAuditData)       loadRaplPowerCapAudit();
      // R&D #54 cards
      if (!swapTunablesAuditData)       loadSwapTunablesAudit();
      if (!hugepagesAuditData)          loadHugepagesAudit();
      if (!kvmMiscAuditData)            loadKvmMiscAudit();
      if (!ioUringRuntimeAuditData)     loadIoUringRuntimeAudit();
      // R&D #55 cards
      if (!edacEccAuditData)            loadEdacEccAudit();
      if (!numaTopologyAuditData)       loadNumaTopologyAudit();
      if (!hwmonSensorsAuditData)       loadHwmonSensorsAudit();
      if (!efiBootOrderAuditData)       loadEfiBootOrderAudit();
      // R&D #56 cards
      if (!sataLinkPmAuditData)         loadSataLinkPmAudit();
      if (!bdiWritebackAuditData)       loadBdiWritebackAudit();
      if (!procCryptoAuditData)         loadProcCryptoAudit();
      if (!wakeupSourcesAuditData)      loadWakeupSourcesAudit();
      // R&D #57 cards
      if (!livepatchAuditData)          loadLivepatchAudit();
      if (!pagetypeinfoAuditData)       loadPagetypeinfoAudit();
      if (!backlightPwmAuditData)       loadBacklightPwmAudit();
      if (!loadavgPressureAuditData)    loadLoadavgPressureAudit();
      // R&D #58 cards
      if (!cgroupRootAuditData)         loadCgroupRootAudit();
      if (!kernelBuildConfigAuditData)  loadKernelBuildConfigAudit();
      if (!scsiTransportAuditData)      loadScsiTransportAudit();
      if (!alsaCardsAuditData)          loadAlsaCardsAudit();
      // R&D #59 cards
      if (!dmiSmbiosAuditData)          loadDmiSmbiosAudit();
      if (!iommuGroupsAuditData)        loadIommuGroupsAudit();
      if (!pidRlimitsAuditData)         loadPidRlimitsAudit();
      // R&D #60 cards
      if (!virtGuestDetectAuditData)    loadVirtGuestDetectAudit();
      // R&D #61 cards
      if (!regulatorAuditData)          loadRegulatorAudit();
      if (!alsaCodecDeepAuditData)      loadAlsaCodecDeepAudit();
      // R&D #62 cards
      if (!devfreqAuditData)            loadDevfreqAudit();
      if (!meiIntelMeAuditData)         loadMeiIntelMeAudit();
      if (!memoryHotplugAuditData)      loadMemoryHotplugAudit();
      if (!procTaskAffinityAuditData)   loadProcTaskAffinityAudit();
      // R&D #63 cards
      if (!rfkillBluetoothAuditData)    loadRfkillBluetoothAudit();
      if (!ledsClassAuditData)          loadLedsClassAudit();
      if (!binfmtMiscAuditData)         loadBinfmtMiscAudit();
      if (!ptpClockAuditData)           loadPtpClockAudit();
      // R&D #64 cards
      if (!meiHdcpPxpAuditData)         loadMeiHdcpPxpAudit();
      if (!firmwareEddMmcAuditData)     loadFirmwareEddMmcAudit();
      if (!devlinkSmartnicAuditData)    loadDevlinkSmartnicAudit();
      if (!procNsMountinfoAuditData)    loadProcNsMountinfoAudit();
      // R&D #65 cards
      if (!cpuidleResidencyAuditData)   loadCpuidleResidencyAudit();
      if (!cpufreqResidencyAuditData)   loadCpufreqResidencyAudit();
      if (!efiRuntimeMapAuditData)      loadEfiRuntimeMapAudit();
      if (!devfreqEventAuditData)       loadDevfreqEventAudit();
      // R&D #66 cards
      if (!mtdFlashAuditData)           loadMtdFlashAudit();
      if (!spiFirmwareLoaderAuditData)  loadSpiFirmwareLoaderAudit();
      if (!procSyscallAuxvAuditData)    loadProcSyscallAuxvAudit();
      if (!btfBpfAuditData)             loadBtfBpfAudit();
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

      <!-- R&D #20.5 Suspend safety preflight (UI sprint 11) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.suspend.title")}</h4>
        <p class="muted">{i18n.t("integrations.suspend.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadSuspendGuard}>{i18n.t("integrations.suspend.refresh")}</button>
          {#if suspendGuardData}
            <span class="kv" style="margin-left: 12px;">
              {i18n.t("integrations.suspend.compute_count")} : <b>{suspendGuardData.compute_count}</b>
            </span>
            {#if suspendGuardData.lid_state}
              <span class="kv">{i18n.t("integrations.suspend.lid_state")} : <b>{suspendGuardData.lid_state}</b></span>
            {/if}
          {/if}
        </div>
        {#if suspendGuardData}
          <p style="margin-top: 8px;"
             style:color={suspendGuardData.verdict.verdict === 'safe' ? 'var(--ok)' :
                        suspendGuardData.verdict.verdict === 'blocked' ? 'var(--warn)' :
                        'var(--accent)'}>
            <b>{i18n.t("integrations.suspend.verdict")} : {suspendGuardData.verdict.verdict}</b> — {suspendGuardData.verdict.reason}
          </p>
          {#if suspendGuardData.verdict.recommendation}
            <p class="muted" style="margin: 4px 0;">{suspendGuardData.verdict.recommendation}</p>
          {/if}
          <div class="form-row" style="gap: 8px; margin-top: 6px;">
            <code style="flex: 1; padding: 6px 10px; background: var(--bg-2);
                          font-family: monospace; font-size: 0.85em;
                          border-radius: 4px;">
              {suspendGuardData.inhibit_snippet}
            </code>
            <button class="btn btn-small"
                    onclick={() => copyToClipboard(suspendGuardData?.inhibit_snippet ?? "")}>📋</button>
          </div>
        {/if}
      </div>

      <!-- R&D #20.1 Container GPU audit (UI sprint 11) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.containers.title")}</h4>
        <p class="muted">{i18n.t("integrations.containers.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadContainerAudit}>{i18n.t("integrations.containers.refresh")}</button>
          {#if containerAuditData?.ok}
            <span class="kv" style="margin-left: 12px;">
              {i18n.t("integrations.containers.total")} : <b>{containerAuditData.container_count}</b>
            </span>
            <span class="kv"
                  style:color={containerAuditData.cpu_fallback_count > 0 ? 'var(--warn)' : 'var(--text-dim)'}>
              {i18n.t("integrations.containers.cpu_fallbacks")} : <b>{containerAuditData.cpu_fallback_count}</b>
            </span>
          {/if}
        </div>
        {#if containerAuditData && !containerAuditData.ok}
          <p class="muted" style="margin-top: 6px;">{i18n.t("integrations.containers.no_docker")}</p>
        {/if}
        {#if containerAuditData?.containers && containerAuditData.containers.length > 0}
          <table style="width:100%; font-size:0.88em; border-collapse: collapse; margin-top: 8px;">
            <thead>
              <tr style="text-align:left; color: var(--text-dim); border-bottom: 1px solid var(--border);">
                <th style="padding: 4px;">name</th>
                <th style="padding: 4px;">{i18n.t("integrations.containers.image")}</th>
                <th style="padding: 4px;">{i18n.t("integrations.containers.runtime")}</th>
                <th style="padding: 4px;">{i18n.t("integrations.containers.verdict")}</th>
              </tr>
            </thead>
            <tbody>
              {#each containerAuditData.containers as c}
                <tr style="border-bottom: 1px solid var(--border);">
                  <td style="padding: 4px; font-family: monospace; font-size: 0.85em;">
                    {(c.names[0] || c.id).replace(/^\//, "")}
                  </td>
                  <td style="padding: 4px;">{c.image}</td>
                  <td style="padding: 4px;">{c.runtime}</td>
                  <td style="padding: 4px;"
                      style:color={c.verdict === 'gpu_ok' ? 'var(--ok)' :
                                 c.verdict === 'cpu_fallback' ? 'var(--warn)' :
                                 c.verdict === 'partial' ? 'var(--accent)' :
                                 'var(--text-dim)'}>
                    {c.verdict}
                  </td>
                </tr>
              {/each}
            </tbody>
          </table>
        {/if}
      </div>

      <!-- R&D #20.7 UPS runtime estimator (UI sprint 11) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.ups.title")}</h4>
        <p class="muted">{i18n.t("integrations.ups.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadUpsRuntime}>{i18n.t("integrations.ups.refresh")}</button>
          {#if upsRuntimeData}
            <span class="kv" style="margin-left: 12px;">
              {i18n.t("integrations.ups.state")} :
              <b>{upsRuntimeData.on_battery ? "on battery" : "on grid"}</b>
              {#if upsRuntimeData.low_battery}
                <span style="color: var(--warn);"> · LOW</span>
              {/if}
            </span>
            {#if upsRuntimeData.gpu_total_power_w !== null}
              <span class="kv">{i18n.t("integrations.ups.gpu_load")} : <b>{upsRuntimeData.gpu_total_power_w.toFixed(0)} W</b></span>
            {/if}
          {/if}
        </div>
        {#if upsRuntimeData && !upsRuntimeData.ups_available}
          <p class="muted" style="margin-top: 6px;">{i18n.t("integrations.ups.no_ups")}</p>
        {/if}
        {#if upsRuntimeData?.ups_available}
          <div class="form-row" style="gap: 18px; margin-top: 6px; flex-wrap: wrap;">
            {#if upsRuntimeData.reported_runtime_s !== null}
              <span class="kv">{i18n.t("integrations.ups.reported")} :
                <b>{Math.floor(upsRuntimeData.reported_runtime_s / 60)} min</b>
              </span>
            {/if}
            {#if upsRuntimeData.adjusted_runtime_s !== null}
              <span class="kv">{i18n.t("integrations.ups.adjusted")} :
                <b>{Math.floor(upsRuntimeData.adjusted_runtime_s / 60)} min</b>
              </span>
            {/if}
            {#if upsRuntimeData.verdict.safe_runtime_s !== null}
              <span class="kv">{i18n.t("integrations.ups.safe")} :
                <b>{Math.floor(upsRuntimeData.verdict.safe_runtime_s / 60)} min</b>
              </span>
            {/if}
          </div>
          <p style="margin-top: 8px;"
             style:color={upsRuntimeData.verdict.verdict === 'on_grid' ? 'var(--text-dim)' :
                        upsRuntimeData.verdict.verdict === 'safe' ? 'var(--ok)' :
                        upsRuntimeData.verdict.verdict === 'pause_jobs' ? 'var(--accent)' :
                        'var(--warn)'}>
            <b>{i18n.t("integrations.ups.verdict")} : {upsRuntimeData.verdict.verdict}</b> — {upsRuntimeData.verdict.reason}
          </p>
        {/if}
      </div>

      <!-- R&D #20.2 VBIOS drift tracker (UI sprint 11) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.vbios.title")}</h4>
        <p class="muted">{i18n.t("integrations.vbios.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadVbiosDrift}>{i18n.t("integrations.vbios.refresh")}</button>
          <button class="btn" onclick={rebaselineVbios} disabled={vbiosRebaselining}>
            {vbiosRebaselining ? "…" : i18n.t("integrations.vbios.rebaseline_btn")}
          </button>
          {#if vbiosDriftData}
            <span class="kv" style="margin-left: 12px;"
                  style:color={vbiosDriftData.drift_count > 0 ? 'var(--warn)' : 'var(--ok)'}>
              {i18n.t("integrations.vbios.drift_count")} : <b>{vbiosDriftData.drift_count}</b>
            </span>
          {/if}
        </div>
        {#if vbiosDriftData?.gpus && vbiosDriftData.gpus.length > 0}
          {#each vbiosDriftData.gpus as g}
            <div style="margin-top: 10px; padding: 8px;
                        border-left: 3px solid {g.drift ? 'var(--warn)' : 'var(--ok)'};">
              <div class="form-row" style="gap: 12px; flex-wrap: wrap;">
                <b>{g.name}</b>
                <span class="kv" style="font-family: monospace; font-size: 0.85em;">{g.bdf}</span>
              </div>
              <div class="form-row" style="gap: 14px; flex-wrap: wrap; margin-top: 4px;">
                <span class="kv">{i18n.t("integrations.vbios.baseline")} :
                  <b style="font-family: monospace;">{g.baseline_vbios ?? '—'}</b>
                </span>
                <span class="kv">{i18n.t("integrations.vbios.current")} :
                  <b style="font-family: monospace;">{g.current_vbios}</b>
                </span>
                {#if g.current_rom_sha256}
                  <span class="kv">{i18n.t("integrations.vbios.rom_hash")} :
                    <code style="font-family: monospace; font-size: 0.8em;">
                      {g.current_rom_sha256.slice(0, 12)}…
                    </code>
                  </span>
                {/if}
              </div>
              <p class="muted" style="margin: 4px 0; font-size: 0.85em;"
                 style:color={g.drift ? 'var(--warn)' : 'var(--text-dim)'}>
                {g.reasons.join(", ")}
              </p>
            </div>
          {/each}
        {/if}
      </div>

      <!-- R&D #21.1 P-state pinning advisor (UI sprint 12) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.pstate.title")}</h4>
        <p class="muted">{i18n.t("integrations.pstate.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadPstateAudit}>{i18n.t("integrations.pstate.refresh")}</button>
          {#if pstateAuditData?.downshift_count !== undefined}
            <span class="kv" style="margin-left: 12px;"
                  style:color={pstateAuditData.downshift_count > 0 ? 'var(--warn)' : 'var(--ok)'}>
              {i18n.t("integrations.pstate.downshift_count")} : <b>{pstateAuditData.downshift_count}</b>
            </span>
          {/if}
        </div>
        {#if pstateAuditData?.gpus && pstateAuditData.gpus.length > 0}
          {#each pstateAuditData.gpus as g}
            <div style="margin-top: 8px; padding: 8px;
                        border-left: 3px solid {
                          g.verdict.verdict === 'silent_downshift' ? 'var(--warn)' :
                          g.verdict.verdict === 'clock_locked' ? 'var(--accent)' :
                          'var(--ok)'};">
              <div class="form-row" style="gap: 12px; flex-wrap: wrap;">
                <b>GPU{g.index}</b>
                {#if g.pstate !== null}
                  <span class="kv" style="font-family: monospace;">P{g.pstate}</span>
                {/if}
                <span class="kv">{i18n.t("integrations.pstate.util")} : {g.util_pct ?? '—'}%</span>
                <span class="kv">{i18n.t("integrations.pstate.clock")} : {g.clock_mhz ?? '—'} MHz</span>
              </div>
              <p style="margin: 4px 0;">
                <b>{i18n.t("integrations.pstate.verdict")} : {g.verdict.verdict}</b> — {g.verdict.reason}
              </p>
              {#if g.verdict.advisory}
                <code style="font-family: monospace; font-size: 0.85em;
                              background: var(--bg-2); padding: 4px 8px;
                              border-radius: 4px;">
                  {g.verdict.advisory}
                </code>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(g.verdict.advisory)}>📋</button>
              {/if}
            </div>
          {/each}
        {/if}
      </div>

      <!-- R&D #21.2 nvidia-persistenced check (UI sprint 12) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.persistence.title")}</h4>
        <p class="muted">{i18n.t("integrations.persistence.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadPersistence}>{i18n.t("integrations.persistence.refresh")}</button>
          {#if persistenceData}
            <span class="kv" style="margin-left: 12px;"
                  style:color={persistenceData.daemon_running ? 'var(--ok)' : 'var(--warn)'}>
              {i18n.t("integrations.persistence.daemon")} :
              {persistenceData.daemon_running
                ? i18n.t("integrations.persistence.daemon_up")
                : i18n.t("integrations.persistence.daemon_off")}
            </span>
          {/if}
        </div>
        {#if persistenceData?.verdict}
          <p style="margin-top: 6px;"
             style:color={persistenceData.verdict.verdict === 'ok' ? 'var(--ok)' :
                        persistenceData.verdict.verdict === 'off' ? 'var(--warn)' :
                        'var(--accent)'}>
            <b>{persistenceData.verdict.verdict}</b> — {persistenceData.verdict.reason}
          </p>
          {#if persistenceData.verdict.advisory}
            <div class="form-row" style="gap: 8px; margin-top: 6px;">
              <code style="flex: 1; padding: 6px 10px; background: var(--bg-2);
                            font-family: monospace; font-size: 0.85em;
                            border-radius: 4px;">{persistenceData.verdict.advisory}</code>
              <button class="btn btn-small"
                      onclick={() => copyToClipboard(persistenceData?.verdict?.advisory ?? "")}>📋</button>
            </div>
          {/if}
        {/if}
        {#if persistenceData?.gpus && persistenceData.gpus.length > 0}
          <table style="width:100%; font-size:0.88em; border-collapse: collapse; margin-top: 8px;">
            <tbody>
              {#each persistenceData.gpus as g}
                <tr style="border-bottom: 1px solid var(--border);">
                  <td style="padding: 4px;">GPU{g.index} {g.name}</td>
                  <td style="padding: 4px;"
                      style:color={g.enabled ? 'var(--ok)' : 'var(--warn)'}>{g.raw}</td>
                </tr>
              {/each}
            </tbody>
          </table>
        {/if}
      </div>

      <!-- R&D #21.3 GSP-RM surfacer (UI sprint 12) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.gsp.title")}</h4>
        <p class="muted">{i18n.t("integrations.gsp.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadGspStatus}>{i18n.t("integrations.gsp.refresh")}</button>
          {#if gspStatusData}
            <span class="kv" style="margin-left: 12px;"
                  style:color={gspStatusData.event_count > 0 ? 'var(--warn)' : 'var(--text-dim)'}>
              {i18n.t("integrations.gsp.event_count")} : <b>{gspStatusData.event_count}</b>
            </span>
            <span class="kv">{i18n.t("integrations.gsp.in_use")} :
              <b>{gspStatusData.verdict.gsp_in_use ? '✓' : '—'}</b>
            </span>
          {/if}
        </div>
        {#if gspStatusData?.verdict}
          <p style="margin-top: 6px;"
             style:color={gspStatusData.verdict.verdict === 'ok' ? 'var(--ok)' :
                        gspStatusData.verdict.verdict === 'crashed' ? 'var(--warn)' :
                        gspStatusData.verdict.verdict === 'fallback' ? 'var(--accent)' :
                        'var(--text-dim)'}>
            <b>{i18n.t("integrations.gsp.verdict")} : {gspStatusData.verdict.verdict}</b> — {gspStatusData.verdict.reason}
          </p>
          {#if gspStatusData.verdict.recovery}
            <div class="form-row" style="gap: 8px; margin-top: 6px;">
              <code style="flex: 1; padding: 6px 10px; background: var(--bg-2);
                            font-family: monospace; font-size: 0.85em;
                            border-radius: 4px;">{gspStatusData.verdict.recovery}</code>
              <button class="btn btn-small"
                      onclick={() => copyToClipboard(gspStatusData?.verdict?.recovery ?? "")}>📋</button>
            </div>
          {/if}
        {/if}
        {#if gspStatusData?.gsp_events && gspStatusData.gsp_events.length > 0}
          <h5 style="margin: 10px 0 4px 0;">{i18n.t("integrations.gsp.recent")}
            ({gspStatusData.gsp_events.length} of {gspStatusData.event_count})</h5>
          <table style="width:100%; font-size:0.82em; border-collapse: collapse;">
            <tbody>
              {#each gspStatusData.gsp_events.slice(-6).reverse() as e}
                <tr style="border-bottom: 1px solid var(--border);">
                  <td style="padding: 4px; color: var(--warn);">{e.kind}</td>
                  <td style="padding: 4px; font-family: monospace; font-size: 0.85em;">{e.line}</td>
                </tr>
              {/each}
            </tbody>
          </table>
        {/if}
      </div>

      <!-- R&D #21.5 SD / ComfyUI cache janitor (UI sprint 12) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.sdcache.title")}</h4>
        <p class="muted">{i18n.t("integrations.sdcache.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadSdCache}>{i18n.t("integrations.sdcache.refresh")}</button>
          {#if sdCacheData}
            <span class="kv" style="margin-left: 12px;">
              {i18n.t("integrations.sdcache.total")} :
              <b>{sdCacheData.total_gib} GiB</b>
            </span>
            <span class="kv"
                  style:color={sdCacheData.cold_gib > 5 ? 'var(--warn)' : 'var(--text-dim)'}>
              {i18n.t("integrations.sdcache.cold", { days: String(sdCacheData.cold_age_days) })} :
              <b>{sdCacheData.cold_gib} GiB</b>
            </span>
          {/if}
        </div>
        {#if sdCacheData && sdCacheData.scanned_count === 0}
          <p class="muted" style="margin-top: 6px;">{i18n.t("integrations.sdcache.no_dirs")}</p>
        {/if}
        {#if sdCacheData?.per_dir && sdCacheData.per_dir.length > 0}
          <table style="width:100%; font-size:0.88em; border-collapse: collapse; margin-top: 8px;">
            <thead>
              <tr style="text-align:left; color: var(--text-dim); border-bottom: 1px solid var(--border);">
                <th style="padding: 4px;">path</th>
                <th style="padding: 4px;">total</th>
                <th style="padding: 4px;">cold</th>
                <th style="padding: 4px;">files</th>
              </tr>
            </thead>
            <tbody>
              {#each sdCacheData.per_dir as d}
                <tr style="border-bottom: 1px solid var(--border);">
                  <td style="padding: 4px; font-family: monospace; font-size: 0.82em;">{d.path.replace(/^\/home\/[^/]+/, "~")}</td>
                  <td style="padding: 4px;">{(d.total_mib / 1024).toFixed(2)} GiB</td>
                  <td style="padding: 4px;"
                      style:color={d.cold_mib > 1024 ? 'var(--warn)' : 'var(--text-dim)'}>
                    {(d.cold_mib / 1024).toFixed(2)} GiB
                  </td>
                  <td style="padding: 4px;">{d.file_count}</td>
                </tr>
              {/each}
            </tbody>
          </table>
        {/if}
        {#if sdCacheData?.top_candidates && sdCacheData.top_candidates.length > 0}
          <h5 style="margin: 10px 0 4px 0;">{i18n.t("integrations.sdcache.top_candidates")}</h5>
          <table style="width:100%; font-size:0.82em; border-collapse: collapse;">
            <tbody>
              {#each sdCacheData.top_candidates.slice(0, 10) as c}
                <tr style="border-bottom: 1px solid var(--border);">
                  <td style="padding: 4px; font-family: monospace;">{c.path.replace(/^\/home\/[^/]+/, "~")}</td>
                  <td style="padding: 4px;">{c.size_mib} MiB</td>
                  <td style="padding: 4px; color: var(--text-dim);">{c.age_days} d</td>
                </tr>
              {/each}
            </tbody>
          </table>
        {/if}
      </div>

      <!-- R&D #22.3 VRAM leak detector (UI sprint 13) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.leak.title")}</h4>
        <p class="muted">{i18n.t("integrations.leak.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadVramLeak}>{i18n.t("integrations.leak.refresh")}</button>
          {#if vramLeakData}
            <span class="kv" style="margin-left: 12px;">
              {i18n.t("integrations.leak.window")} : <b>{Math.floor(vramLeakData.window_s / 60)} min</b>
            </span>
            <span class="kv"
                  style:color={vramLeakData.leaking_count > 0 ? 'var(--warn)' : 'var(--text-dim)'}>
              {i18n.t("integrations.leak.leaking")} : <b>{vramLeakData.leaking_count}</b>
            </span>
            <span class="kv">
              {i18n.t("integrations.leak.growing")} : <b>{vramLeakData.growing_count}</b>
            </span>
          {/if}
        </div>
        {#if vramLeakData?.processes && vramLeakData.processes.length > 0}
          <table style="width:100%; font-size:0.88em; border-collapse: collapse; margin-top: 8px;">
            <thead>
              <tr style="text-align:left; color: var(--text-dim); border-bottom: 1px solid var(--border);">
                <th style="padding: 4px;">{i18n.t("integrations.leak.process")}</th>
                <th style="padding: 4px;">{i18n.t("integrations.leak.current")}</th>
                <th style="padding: 4px;">{i18n.t("integrations.leak.slope")}</th>
                <th style="padding: 4px;">{i18n.t("integrations.leak.verdict")}</th>
                <th style="padding: 4px;">{i18n.t("integrations.leak.oom_in")}</th>
              </tr>
            </thead>
            <tbody>
              {#each vramLeakData.processes as p}
                <tr style="border-bottom: 1px solid var(--border);">
                  <td style="padding: 4px;">{p.comm} <span class="muted">(pid {p.pid})</span></td>
                  <td style="padding: 4px;">{p.current_mib} MiB</td>
                  <td style="padding: 4px;">
                    {p.verdict.slope_mib_per_hour !== null
                      ? `${p.verdict.slope_mib_per_hour.toFixed(1)} MiB/h`
                      : '—'}
                  </td>
                  <td style="padding: 4px;"
                      style:color={p.verdict.verdict === 'leaking' ? 'var(--warn)' :
                                 p.verdict.verdict === 'growing' ? 'var(--accent)' :
                                 'var(--ok)'}>{p.verdict.verdict}</td>
                  <td style="padding: 4px;">
                    {p.verdict.projected_oom_minutes !== null
                      ? `${Math.floor(p.verdict.projected_oom_minutes)} min`
                      : '—'}
                  </td>
                </tr>
              {/each}
            </tbody>
          </table>
        {/if}
      </div>

      <!-- R&D #22.1 GPU reset counter (UI sprint 13) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.reset.title")}</h4>
        <p class="muted">{i18n.t("integrations.reset.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadGpuReset}>{i18n.t("integrations.reset.refresh")}</button>
          {#if gpuResetData}
            <span class="kv" style="margin-left: 12px;"
                  style:color={gpuResetData.total_delta_resets > 0 ? 'var(--warn)' : 'var(--text-dim)'}>
              {i18n.t("integrations.reset.delta")} : <b>{gpuResetData.total_delta_resets}</b>
            </span>
            <span class="kv"
                  style:color={gpuResetData.kernel_event_count > 0 ? 'var(--warn)' : 'var(--text-dim)'}>
              {i18n.t("integrations.reset.events")} : <b>{gpuResetData.kernel_event_count}</b>
            </span>
          {/if}
        </div>
        {#if gpuResetData?.verdict}
          <p style="margin-top: 6px;"
             style:color={gpuResetData.verdict.verdict === 'clean' ? 'var(--ok)' :
                        gpuResetData.verdict.verdict === 'rma' ? 'var(--warn)' :
                        gpuResetData.verdict.verdict === 'frequent' ? 'var(--accent)' :
                        'var(--text-dim)'}>
            <b>{gpuResetData.verdict.verdict}</b> — {gpuResetData.verdict.reason}
          </p>
          {#if gpuResetData.verdict.recommendation}
            <p class="muted">{i18n.t("integrations.reset.recovery")} :
              <code style="font-family: monospace; font-size: 0.85em;">{gpuResetData.verdict.recommendation}</code>
            </p>
          {/if}
        {/if}
        {#if gpuResetData?.kernel_events && gpuResetData.kernel_events.length > 0}
          <table style="width:100%; font-size:0.82em; border-collapse: collapse; margin-top: 6px;">
            <tbody>
              {#each gpuResetData.kernel_events.slice(-6).reverse() as e}
                <tr style="border-bottom: 1px solid var(--border);">
                  <td style="padding: 4px; color: var(--warn);">{e.kind}</td>
                  <td style="padding: 4px; font-family: monospace; font-size: 0.85em;">{e.line}</td>
                </tr>
              {/each}
            </tbody>
          </table>
        {/if}
      </div>

      <!-- R&D #22.5 CUDA toolkit inventory (UI sprint 13) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.cudainv.title")}</h4>
        <p class="muted">{i18n.t("integrations.cudainv.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadCudaInv}>{i18n.t("integrations.cudainv.refresh")}</button>
          {#if cudaInvData}
            <span class="kv" style="margin-left: 12px;">
              {i18n.t("integrations.cudainv.installs")} : <b>{cudaInvData.install_count}</b>
            </span>
            <span class="kv"
                  style:color={cudaInvData.collision_count > 0 ? 'var(--warn)' : 'var(--text-dim)'}>
              {i18n.t("integrations.cudainv.collisions")} : <b>{cudaInvData.collision_count}</b>
            </span>
          {/if}
        </div>
        {#if cudaInvData?.verdict}
          <p style="margin-top: 6px;"
             style:color={cudaInvData.verdict.verdict === 'clean' ? 'var(--ok)' :
                        cudaInvData.verdict.verdict === 'version_conflict' ? 'var(--warn)' :
                        'var(--text-dim)'}>
            <b>{cudaInvData.verdict.verdict}</b> — {cudaInvData.verdict.reason}
          </p>
        {/if}
        {#if cudaInvData?.toolkits && cudaInvData.toolkits.length > 0}
          <h5 style="margin: 8px 0 4px 0;">{i18n.t("integrations.cudainv.toolkits")}</h5>
          <table style="width:100%; font-size:0.85em; border-collapse: collapse;">
            <tbody>
              {#each cudaInvData.toolkits as t}
                <tr style="border-bottom: 1px solid var(--border);">
                  <td style="padding: 4px; font-family: monospace;">{t.path}</td>
                  <td style="padding: 4px;">{t.version ?? '—'}</td>
                </tr>
              {/each}
            </tbody>
          </table>
        {/if}
        {#if cudaInvData?.conda_envs && cudaInvData.conda_envs.length > 0}
          <h5 style="margin: 8px 0 4px 0;">{i18n.t("integrations.cudainv.conda")}</h5>
          <table style="width:100%; font-size:0.85em; border-collapse: collapse;">
            <tbody>
              {#each cudaInvData.conda_envs as t}
                <tr style="border-bottom: 1px solid var(--border);">
                  <td style="padding: 4px; font-family: monospace;">{t.source}</td>
                  <td style="padding: 4px;">{t.version}</td>
                </tr>
              {/each}
            </tbody>
          </table>
        {/if}
      </div>

      <!-- R&D #22.2 Open vs proprietary driver advisor (UI sprint 13) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.flavor.title")}</h4>
        <p class="muted">{i18n.t("integrations.flavor.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadDriverFlavor}>{i18n.t("integrations.flavor.refresh")}</button>
          {#if driverFlavorData}
            <span class="kv" style="margin-left: 12px;">
              {i18n.t("integrations.flavor.version")} : <b>{driverFlavorData.kernel_module_version ?? '—'}</b>
            </span>
            <span class="kv">
              {i18n.t("integrations.flavor.flavor")} :
              <b style:color={driverFlavorData.flavor === 'open' ? 'var(--ok)' :
                            driverFlavorData.flavor === 'proprietary' ? 'var(--accent)' :
                            'var(--warn)'}>{driverFlavorData.flavor}</b>
            </span>
          {/if}
        </div>
        {#if driverFlavorData?.verdict}
          <p style="margin-top: 6px;"
             style:color={driverFlavorData.verdict.verdict === 'ok' ? 'var(--ok)' :
                        driverFlavorData.verdict.verdict === 'wrong_flavor' ? 'var(--warn)' :
                        driverFlavorData.verdict.verdict === 'could_upgrade' ? 'var(--accent)' :
                        'var(--text-dim)'}>
            <b>{driverFlavorData.verdict.verdict}</b> — {driverFlavorData.verdict.reason}
          </p>
          {#if driverFlavorData.verdict.recommendation}
            <div class="form-row" style="gap: 8px; margin-top: 6px;">
              <code style="flex: 1; padding: 6px 10px; background: var(--bg-2);
                            font-family: monospace; font-size: 0.85em;
                            border-radius: 4px;">{driverFlavorData.verdict.recommendation}</code>
              <button class="btn btn-small"
                      onclick={() => copyToClipboard(driverFlavorData?.verdict?.recommendation ?? "")}>📋</button>
            </div>
          {/if}
        {/if}
        {#if driverFlavorData?.gpus && driverFlavorData.gpus.length > 0}
          <table style="width:100%; font-size:0.88em; border-collapse: collapse; margin-top: 8px;">
            <thead>
              <tr style="text-align:left; color: var(--text-dim); border-bottom: 1px solid var(--border);">
                <th style="padding: 4px;">{i18n.t("integrations.flavor.gpus")}</th>
                <th style="padding: 4px;">{i18n.t("integrations.flavor.arch")}</th>
                <th style="padding: 4px;">compute</th>
                <th style="padding: 4px;">open</th>
              </tr>
            </thead>
            <tbody>
              {#each driverFlavorData.gpus as g}
                <tr style="border-bottom: 1px solid var(--border);">
                  <td style="padding: 4px;">{g.name}</td>
                  <td style="padding: 4px;">{g.arch}</td>
                  <td style="padding: 4px;">sm_{g.compute_cap.replace(".", "")}</td>
                  <td style="padding: 4px;"
                      style:color={g.open_supported ? 'var(--ok)' : 'var(--warn)'}>
                    {g.open_supported
                      ? i18n.t("integrations.flavor.open_ok")
                      : i18n.t("integrations.flavor.open_no")}
                  </td>
                </tr>
              {/each}
            </tbody>
          </table>
        {/if}
      </div>

      <!-- R&D #23.6 procfs deep-state (UI sprint 14) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.procdeep.title")}</h4>
        <p class="muted">{i18n.t("integrations.procdeep.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadProcDeep}>{i18n.t("integrations.procdeep.refresh")}</button>
          {#if procDeepData}
            <span class="kv" style="margin-left: 12px;"
                  style:color={procDeepData.drift_count > 0 ? 'var(--warn)' : 'var(--text-dim)'}>
              {i18n.t("integrations.procdeep.drifters")} : <b>{procDeepData.drift_count}</b>
            </span>
            <span class="kv"
                  style:color={procDeepData.excluded_count > 0 ? 'var(--warn)' : 'var(--text-dim)'}>
              {i18n.t("integrations.procdeep.excluded")} : <b>{procDeepData.excluded_count}</b>
            </span>
          {/if}
        </div>
        {#if procDeepData?.verdict}
          <p style="margin-top: 6px;"
             style:color={procDeepData.verdict.severity === 'critical' ? 'var(--warn)' :
                        procDeepData.verdict.severity === 'warn' ? 'var(--accent)' :
                        'var(--text-dim)'}>
            <b>{i18n.t("integrations.procdeep.verdict")} : {procDeepData.verdict.verdict}</b> —
            {procDeepData.verdict.reason}
          </p>
        {/if}
        {#if procDeepData?.gpus && procDeepData.gpus.length > 0}
          <table style="width:100%; font-size:0.88em; border-collapse: collapse; margin-top: 8px;">
            <tbody>
              {#each procDeepData.gpus as g}
                <tr style="border-bottom: 1px solid var(--border);">
                  <td style="padding: 4px;">{g.model}</td>
                  <td style="padding: 4px; font-family: monospace; font-size: 0.85em;">VBIOS {g.video_bios}</td>
                  <td style="padding: 4px; font-family: monospace; font-size: 0.85em;">GSP {g.gpu_firmware}</td>
                  <td style="padding: 4px;"
                      style:color={g.excluded ? 'var(--warn)' : 'var(--ok)'}>
                    {g.excluded ? '⚠ excluded' : 'ok'}
                  </td>
                </tr>
              {/each}
            </tbody>
          </table>
        {/if}
      </div>

      <!-- R&D #23.4 PCIe ASPM audit (UI sprint 14) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.aspm.title")}</h4>
        <p class="muted">{i18n.t("integrations.aspm.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadPcieAspm}>{i18n.t("integrations.aspm.refresh")}</button>
          {#if pcieAspmData?.policy}
            <span class="kv" style="margin-left: 12px;">
              {i18n.t("integrations.aspm.policy")} : <b>{pcieAspmData.policy.active ?? '—'}</b>
            </span>
          {/if}
          {#if pcieAspmData?.board?.name}
            <span class="kv"
                  style:color={pcieAspmData.board_known_risky ? 'var(--warn)' : 'var(--text-dim)'}>
              {i18n.t("integrations.aspm.board")} : <b>{pcieAspmData.board.name}</b>
            </span>
          {/if}
        </div>
        {#if pcieAspmData?.verdict}
          <p style="margin-top: 6px;"
             style:color={pcieAspmData.verdict.verdict === 'ok' ? 'var(--ok)' :
                        pcieAspmData.verdict.verdict === 'risky' ? 'var(--warn)' :
                        pcieAspmData.verdict.verdict === 'warn' ? 'var(--accent)' :
                        'var(--text-dim)'}>
            <b>{i18n.t("integrations.aspm.verdict")} : {pcieAspmData.verdict.verdict}</b> —
            {pcieAspmData.verdict.reason}
          </p>
          {#if pcieAspmData.verdict.recommendation}
            <div class="form-row" style="gap: 8px; margin-top: 6px;">
              <code style="flex: 1; padding: 6px 10px; background: var(--bg-2);
                            font-family: monospace; font-size: 0.85em;
                            border-radius: 4px;">{pcieAspmData.verdict.recommendation}</code>
              <button class="btn btn-small"
                      onclick={() => copyToClipboard(pcieAspmData?.verdict?.recommendation ?? "")}>📋</button>
            </div>
          {/if}
        {/if}
      </div>

      <!-- R&D #23.2 FS mount audit (UI sprint 14) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.fsaudit.title")}</h4>
        <p class="muted">{i18n.t("integrations.fsaudit.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadFsAudit}>{i18n.t("integrations.fsaudit.refresh")}</button>
          {#if fsAuditData}
            <span class="kv" style="margin-left: 12px;">
              {i18n.t("integrations.fsaudit.checked")} : <b>{fsAuditData.audit_count}</b>
            </span>
            <span class="kv"
                  style:color={fsAuditData.warn_count > 0 ? 'var(--warn)' : 'var(--text-dim)'}>
              {i18n.t("integrations.fsaudit.warn")} : <b>{fsAuditData.warn_count}</b>
            </span>
            {#if fsAuditData.fail_count > 0}
              <span class="kv" style="color: var(--warn);">
                {i18n.t("integrations.fsaudit.fail")} : <b>{fsAuditData.fail_count}</b>
              </span>
            {/if}
          {/if}
        </div>
        {#if fsAuditData?.verdict}
          <p class="muted" style="margin-top: 6px;">{fsAuditData.verdict.reason}</p>
        {/if}
        {#if fsAuditData?.audits && fsAuditData.audits.length > 0}
          <table style="width:100%; font-size:0.88em; border-collapse: collapse; margin-top: 8px;">
            <tbody>
              {#each fsAuditData.audits as a}
                <tr style="border-bottom: 1px solid var(--border);">
                  <td style="padding: 4px; font-family: monospace; font-size: 0.82em;">
                    {a.directory.replace(/^\/home\/[^/]+/, "~")}
                  </td>
                  <td style="padding: 4px;">{a.fstype}</td>
                  <td style="padding: 4px;"
                      style:color={a.severity === 'ok' ? 'var(--ok)' :
                                 a.severity === 'fail' ? 'var(--warn)' :
                                 'var(--accent)'}>{a.severity}</td>
                </tr>
                {#if a.issues.length > 0}
                  {#each a.issues as iss}
                    <tr><td colspan="3" class="muted" style="padding: 2px 4px 6px 20px; font-size: 0.85em;">
                      ⚠ {iss.label}: {iss.recommendation}
                    </td></tr>
                  {/each}
                {/if}
              {/each}
            </tbody>
          </table>
        {/if}
      </div>

      <!-- R&D #23.1 Batch / ctx-length advisor (UI sprint 14) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.batch.title")}</h4>
        <p class="muted">{i18n.t("integrations.batch.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadBatchAdvisor}>{i18n.t("integrations.batch.refresh")}</button>
          {#if batchAdvisorData?.vram}
            <span class="kv" style="margin-left: 12px;">
              free VRAM : <b>{batchAdvisorData.vram.free_mib} MiB</b>
            </span>
          {/if}
        </div>
        {#if batchAdvisorData && batchAdvisorData.advisors.length === 0}
          <p class="muted" style="margin-top: 6px;">{i18n.t("integrations.batch.no_advisors")}</p>
        {/if}
        {#if batchAdvisorData?.advisors && batchAdvisorData.advisors.length > 0}
          {#each batchAdvisorData.advisors as adv}
            <div style="margin-top: 10px; padding: 8px; border-left: 3px solid var(--border);">
              <div class="form-row" style="gap: 14px; flex-wrap: wrap;">
                <b style="font-family: monospace;">{adv.model}</b>
                <span class="kv">{i18n.t("integrations.batch.headroom")} :
                  <b>{adv.headroom_mib} MiB</b>
                </span>
                <span class="kv">{i18n.t("integrations.batch.max_ctx")} :
                  <b>{adv.max_ctx_at_batch}</b> tokens
                </span>
                <span class="kv">{i18n.t("integrations.batch.max_batch")} :
                  <b>{adv.max_batch_at_ctx_train}</b>
                </span>
              </div>
              <p class="muted" style="margin: 4px 0; font-size: 0.85em;">
                {i18n.t("integrations.batch.kv_per_token")} : {Math.round(adv.kv_per_token_bytes / 1024)} KiB
              </p>
              <p style="margin: 4px 0;">{adv.recommendation}</p>
            </div>
          {/each}
        {/if}
      </div>

      <!-- R&D #24.3 DKMS rebuild status (UI sprint 15) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.dkms.title")}</h4>
        <p class="muted">{i18n.t("integrations.dkms.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadDkmsStatus}>{i18n.t("integrations.dkms.refresh")}</button>
          {#if dkmsStatusData}
            <span class="kv" style="margin-left: 12px;">
              {i18n.t("integrations.dkms.kernel")} :
              <b style="font-family: monospace;">{dkmsStatusData.running_kernel}</b>
            </span>
          {/if}
        </div>
        {#if dkmsStatusData?.verdict}
          <p style="margin-top: 6px;"
             style:color={dkmsStatusData.verdict.verdict === 'ok' ? 'var(--ok)' :
                        dkmsStatusData.verdict.verdict === 'rebuild_needed' ? 'var(--warn)' :
                        'var(--text-dim)'}>
            <b>{i18n.t("integrations.dkms.verdict")} : {dkmsStatusData.verdict.verdict}</b> — {dkmsStatusData.verdict.reason}
          </p>
          {#if dkmsStatusData.verdict.recovery}
            <div class="form-row" style="gap: 8px; margin-top: 6px;">
              <code style="flex: 1; padding: 6px 10px; background: var(--bg-2);
                            font-family: monospace; font-size: 0.85em;
                            border-radius: 4px;">{dkmsStatusData.verdict.recovery}</code>
              <button class="btn btn-small"
                      onclick={() => copyToClipboard(dkmsStatusData?.verdict?.recovery ?? "")}>📋</button>
            </div>
          {/if}
        {/if}
        {#if dkmsStatusData?.dkms_entries && dkmsStatusData.dkms_entries.length > 0}
          <table style="width:100%; font-size:0.85em; border-collapse: collapse; margin-top: 6px;">
            <tbody>
              {#each dkmsStatusData.dkms_entries as e}
                <tr style="border-bottom: 1px solid var(--border);">
                  <td style="padding: 4px;">{e.module}/{e.version ?? '—'}</td>
                  <td style="padding: 4px; font-family: monospace;">{e.kernel ?? '—'}</td>
                  <td style="padding: 4px;">{e.state}</td>
                </tr>
              {/each}
            </tbody>
          </table>
        {/if}
      </div>

      <!-- R&D #24.2 PCIe AER counter (UI sprint 15) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.aer.title")}</h4>
        <p class="muted">{i18n.t("integrations.aer.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadPcieAer}>{i18n.t("integrations.aer.refresh")}</button>
        </div>
        {#if pcieAerData?.verdict}
          <p style="margin-top: 6px;"
             style:color={pcieAerData.verdict.verdict === 'clean' ? 'var(--ok)' :
                        pcieAerData.verdict.verdict === 'fatal' ? 'var(--warn)' :
                        pcieAerData.verdict.verdict === 'non_fatal' ? 'var(--warn)' :
                        pcieAerData.verdict.verdict === 'high_correctable' ? 'var(--accent)' :
                        'var(--text-dim)'}>
            <b>{i18n.t("integrations.aer.verdict")} : {pcieAerData.verdict.verdict}</b> — {pcieAerData.verdict.reason}
          </p>
          {#if pcieAerData.verdict.recovery}
            <p class="muted">{i18n.t("integrations.aer.recovery")} :
              <code style="font-family: monospace; font-size: 0.85em;">{pcieAerData.verdict.recovery}</code>
            </p>
          {/if}
        {/if}
        {#if pcieAerData?.devices && pcieAerData.devices.length > 0}
          <table style="width:100%; font-size:0.85em; border-collapse: collapse; margin-top: 6px;">
            <thead>
              <tr style="text-align:left; color: var(--text-dim); border-bottom: 1px solid var(--border);">
                <th style="padding: 4px;">device</th>
                <th style="padding: 4px;">cor</th>
                <th style="padding: 4px;">non-fatal</th>
                <th style="padding: 4px;">fatal</th>
                <th style="padding: 4px;">verdict</th>
              </tr>
            </thead>
            <tbody>
              {#each pcieAerData.devices as d}
                <tr style="border-bottom: 1px solid var(--border);">
                  <td style="padding: 4px; font-family: monospace;">{d.bdf}</td>
                  <td style="padding: 4px;">{d.totals.correctable}</td>
                  <td style="padding: 4px;">{d.totals.nonfatal}</td>
                  <td style="padding: 4px;">{d.totals.fatal}</td>
                  <td style="padding: 4px;"
                      style:color={d.verdict.verdict === 'clean' ? 'var(--ok)' :
                                 d.verdict.verdict === 'fatal' ? 'var(--warn)' :
                                 'var(--accent)'}>{d.verdict.verdict}</td>
                </tr>
              {/each}
            </tbody>
          </table>
        {/if}
      </div>

      <!-- R&D #24.4 VRAM thermal-pad drift (UI sprint 15) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.memtemp.title")}</h4>
        <p class="muted">{i18n.t("integrations.memtemp.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadMemTempDrift}>{i18n.t("integrations.memtemp.refresh")}</button>
          {#if memTempDriftData?.summary_verdict}
            <span class="kv" style="margin-left: 12px;"
                  style:color={memTempDriftData.summary_verdict === 'urgent' ? 'var(--warn)' :
                             memTempDriftData.summary_verdict === 'pad_degraded' ? 'var(--accent)' :
                             memTempDriftData.summary_verdict === 'improving' ? 'var(--ok)' :
                             'var(--text-dim)'}>
              {i18n.t("integrations.memtemp.verdict")} :
              <b>{memTempDriftData.summary_verdict}</b>
            </span>
          {/if}
        </div>
        {#if memTempDriftData?.gpus && memTempDriftData.gpus.length > 0}
          {#each memTempDriftData.gpus as g}
            <div style="margin-top: 8px; padding: 8px;
                        border-left: 3px solid {
                          g.verdict.verdict === 'urgent' ? 'var(--warn)' :
                          g.verdict.verdict === 'pad_degraded' ? 'var(--accent)' :
                          'var(--ok)'};">
              <div class="form-row" style="gap: 14px; flex-wrap: wrap;">
                <b>{g.name}</b>
                <span class="kv">GPU {g.gpu_temp_c}°C</span>
                <span class="kv">VRAM {g.mem_temp_c}°C</span>
                {#if g.delta_now !== null}
                  <span class="kv">{i18n.t("integrations.memtemp.delta_now")} :
                    <b>+{g.delta_now}°C</b>
                  </span>
                {/if}
                {#if g.drift.drift_c !== null}
                  <span class="kv"
                        style:color={g.drift.drift_c > 5 ? 'var(--warn)' : 'var(--text-dim)'}>
                    {i18n.t("integrations.memtemp.drift")} :
                    <b>{g.drift.drift_c > 0 ? '+' : ''}{g.drift.drift_c}°C</b>
                  </span>
                {/if}
                <span class="kv">{g.drift.sample_count} {i18n.t("integrations.memtemp.samples")}</span>
              </div>
              <p class="muted" style="margin: 4px 0;">{g.verdict.reason}</p>
            </div>
          {/each}
        {/if}
      </div>

      <!-- R&D #24.1 NVML accounting harvester (UI sprint 15) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.acc.title")}</h4>
        <p class="muted">{i18n.t("integrations.acc.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadAccounting}>{i18n.t("integrations.acc.refresh")}</button>
          {#if accountingData}
            <span class="kv" style="margin-left: 12px;"
                  style:color={accountingData.accounting_mode === 'Enabled' ? 'var(--ok)' : 'var(--warn)'}>
              {i18n.t("integrations.acc.mode")} :
              <b>{accountingData.accounting_mode ?? '—'}</b>
            </span>
            {#if accountingData.record_count !== undefined}
              <span class="kv">{i18n.t("integrations.acc.record_count")} :
                <b>{accountingData.record_count}</b>
              </span>
            {/if}
          {/if}
        </div>
        {#if accountingData?.advisory}
          <p class="muted" style="margin-top: 6px;">{accountingData.advisory}</p>
        {/if}
        {#if accountingData?.enable_command}
          <div class="form-row" style="gap: 8px; margin-top: 6px;">
            <code style="flex: 1; padding: 6px 10px; background: var(--bg-2);
                          font-family: monospace; font-size: 0.85em;
                          border-radius: 4px;">{accountingData.enable_command}</code>
            <button class="btn btn-small"
                    onclick={() => copyToClipboard(accountingData?.enable_command ?? "")}>📋</button>
          </div>
        {/if}
        {#if accountingData?.by_command && accountingData.by_command.length > 0}
          <table style="width:100%; font-size:0.88em; border-collapse: collapse; margin-top: 8px;">
            <thead>
              <tr style="text-align:left; color: var(--text-dim); border-bottom: 1px solid var(--border);">
                <th style="padding: 4px;">{i18n.t("integrations.acc.process")}</th>
                <th style="padding: 4px;">{i18n.t("integrations.acc.runs")}</th>
                <th style="padding: 4px;">{i18n.t("integrations.acc.peak_vram")}</th>
                <th style="padding: 4px;">{i18n.t("integrations.acc.total_wall")}</th>
                <th style="padding: 4px;">{i18n.t("integrations.acc.mean_util")}</th>
              </tr>
            </thead>
            <tbody>
              {#each accountingData.by_command as r}
                <tr style="border-bottom: 1px solid var(--border);">
                  <td style="padding: 4px;">{r.comm}</td>
                  <td style="padding: 4px;">{r.count}</td>
                  <td style="padding: 4px;">{r.max_memory_mib} MiB</td>
                  <td style="padding: 4px;">{Math.floor(r.total_wall_ms / 1000)} s</td>
                  <td style="padding: 4px;">{r.mean_gpu_util_pct ?? '—'}%</td>
                </tr>
              {/each}
            </tbody>
          </table>
        {/if}
      </div>

      <!-- R&D #25.2 TRIM/discard auditor (UI sprint 16) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.trim.title")}</h4>
        <p class="muted">{i18n.t("integrations.trim.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadTrimAudit}>{i18n.t("integrations.trim.refresh")}</button>
          {#if trimAuditData?.fstrim_timer}
            <span class="kv" style="margin-left: 12px;"
                  style:color={trimAuditData.fstrim_timer.active === 'active' ? 'var(--ok)' : 'var(--warn)'}>
              {i18n.t("integrations.trim.timer")} :
              <b>{trimAuditData.fstrim_timer.active ?? '—'} / {trimAuditData.fstrim_timer.enabled ?? '—'}</b>
            </span>
          {/if}
        </div>
        {#if trimAuditData?.verdict}
          <p style="margin-top: 6px;"
             style:color={trimAuditData.verdict.verdict === 'ok' ? 'var(--ok)' :
                        trimAuditData.verdict.verdict === 'no_trim' ? 'var(--warn)' :
                        'var(--text-dim)'}>
            <b>{i18n.t("integrations.trim.verdict")} : {trimAuditData.verdict.verdict}</b> — {trimAuditData.verdict.reason}
          </p>
          {#if trimAuditData.verdict.recommendation}
            <div class="form-row" style="gap: 8px; margin-top: 6px;">
              <code style="flex: 1; padding: 6px 10px; background: var(--bg-2);
                            font-family: monospace; font-size: 0.85em;
                            border-radius: 4px;">{trimAuditData.verdict.recommendation}</code>
              <button class="btn btn-small"
                      onclick={() => copyToClipboard(trimAuditData?.verdict?.recommendation ?? "")}>📋</button>
            </div>
          {/if}
        {/if}
        {#if trimAuditData?.audits && trimAuditData.audits.length > 0}
          <table style="width:100%; font-size:0.85em; border-collapse: collapse; margin-top: 6px;">
            <tbody>
              {#each trimAuditData.audits as a}
                <tr style="border-bottom: 1px solid var(--border);">
                  <td style="padding: 4px; font-family: monospace; font-size: 0.82em;">
                    {a.directory.replace(/^\/home\/[^/]+/, "~")}
                  </td>
                  <td style="padding: 4px;">{a.fstype}</td>
                  <td style="padding: 4px;"
                      style:color={a.on_ssd ? 'var(--text-dim)' : 'var(--text-dim)'}>
                    {a.on_ssd ? 'SSD' : 'HDD/NA'}
                  </td>
                  <td style="padding: 4px;"
                      style:color={a.has_discard_mount ? 'var(--ok)' : 'var(--text-dim)'}>
                    {a.has_discard_mount ? '✓ discard' : '— no discard'}
                  </td>
                </tr>
              {/each}
            </tbody>
          </table>
        {/if}
      </div>

      <!-- R&D #25.5 Throttle bits decoder (UI sprint 16) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.tbits.title")}</h4>
        <p class="muted">{i18n.t("integrations.tbits.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadThrottleBits}>{i18n.t("integrations.tbits.refresh")}</button>
        </div>
        {#if throttleBitsData?.gpus && throttleBitsData.gpus.length > 0}
          {#each throttleBitsData.gpus as g}
            <div style="margin-top: 8px; padding: 8px;
                        border-left: 3px solid {
                          g.verdict.severity === 'critical' ? 'var(--warn)' :
                          g.verdict.severity === 'warn' ? 'var(--accent)' :
                          'var(--ok)'};">
              <div class="form-row" style="gap: 14px; flex-wrap: wrap;">
                <b>GPU{g.index} — {g.name}</b>
                <span class="kv">{i18n.t("integrations.tbits.verdict")} :
                  <b style:color={g.verdict.severity === 'critical' ? 'var(--warn)' :
                                g.verdict.severity === 'warn' ? 'var(--accent)' :
                                'var(--ok)'}>{g.verdict.verdict}</b>
                </span>
                <span class="kv">{i18n.t("integrations.tbits.active_count")} : <b>{g.active_count}</b></span>
              </div>
              <table style="width:100%; font-size:0.82em; border-collapse: collapse; margin-top: 4px;">
                <tbody>
                  {#each g.bits as bit}
                    <tr style="border-bottom: 1px solid var(--border);">
                      <td style="padding: 3px;"
                          style:color={bit.active
                            ? (bit.severity === 'critical' ? 'var(--warn)' :
                               bit.severity === 'warn' ? 'var(--accent)' : 'var(--ok)')
                            : 'var(--text-dim)'}>
                        {bit.active ? '●' : '○'}
                      </td>
                      <td style="padding: 3px;">{bit.label}</td>
                      <td style="padding: 3px; color: var(--text-dim);">{bit.severity}</td>
                      <td style="padding: 3px; color: var(--text-dim); font-size: 0.85em;">{bit.meaning}</td>
                    </tr>
                  {/each}
                </tbody>
              </table>
            </div>
          {/each}
        {/if}
      </div>

      <!-- R&D #25.1 Retired-page trend (UI sprint 16) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.retire.title")}</h4>
        <p class="muted">{i18n.t("integrations.retire.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadRetiredPages}>{i18n.t("integrations.retire.refresh")}</button>
        </div>
        {#if retiredPagesData && !retiredPagesData.supported}
          <p class="muted" style="margin-top: 6px;">{i18n.t("integrations.retire.unsupported")}</p>
        {/if}
        {#if retiredPagesData?.per_gpu && retiredPagesData.per_gpu.length > 0}
          <table style="width:100%; font-size:0.88em; border-collapse: collapse; margin-top: 6px;">
            <thead>
              <tr style="text-align:left; color: var(--text-dim); border-bottom: 1px solid var(--border);">
                <th style="padding: 4px;">GPU UUID</th>
                <th style="padding: 4px;">{i18n.t("integrations.retire.sbe")}</th>
                <th style="padding: 4px;">{i18n.t("integrations.retire.dbe")}</th>
                <th style="padding: 4px;">{i18n.t("integrations.retire.delta")}</th>
                <th style="padding: 4px;">verdict</th>
              </tr>
            </thead>
            <tbody>
              {#each retiredPagesData.per_gpu as g}
                <tr style="border-bottom: 1px solid var(--border);">
                  <td style="padding: 4px; font-family: monospace; font-size: 0.82em;">
                    {g.uuid.slice(0, 16)}…
                  </td>
                  <td style="padding: 4px;">{g.sbe}</td>
                  <td style="padding: 4px;"
                      style:color={g.dbe > 0 ? 'var(--warn)' : 'var(--text-dim)'}>{g.dbe}</td>
                  <td style="padding: 4px;">+{g.delta_sbe} / +{g.delta_dbe}</td>
                  <td style="padding: 4px;"
                      style:color={g.verdict.severity === 'critical' ? 'var(--warn)' :
                                 g.verdict.severity === 'warn' ? 'var(--accent)' :
                                 'var(--text-dim)'}>{g.verdict.label}</td>
                </tr>
              {/each}
            </tbody>
          </table>
        {/if}
      </div>

      <!-- R&D #25.3 NVIDIA bug-report prepper (UI sprint 16) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.bugrep.title")}</h4>
        <p class="muted">{i18n.t("integrations.bugrep.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadBugRepPrep}>{i18n.t("integrations.bugrep.refresh")}</button>
          {#if bugRepPrepData?.template_text}
            <button class="btn"
                    onclick={() => copyToClipboard(bugRepPrepData?.template_text ?? "")}>
              {i18n.t("integrations.bugrep.copy")}
            </button>
          {/if}
        </div>
        {#if bugRepPrepData?.context_summary}
          <div class="form-row" style="gap: 14px; margin-top: 6px; flex-wrap: wrap;">
            <span class="kv">kernel : <b>{bugRepPrepData.context_summary.kernel ?? '—'}</b></span>
            <span class="kv">driver : <b>{bugRepPrepData.context_summary.driver_flavor ?? '—'}</b></span>
            <span class="kv">GPUs : <b>{bugRepPrepData.context_summary.gpu_count}</b></span>
            <span class="kv"
                  style:color={bugRepPrepData.context_summary.xid_event_count > 0 ? 'var(--warn)' : 'var(--text-dim)'}>
              XID : <b>{bugRepPrepData.context_summary.xid_event_count}</b>
            </span>
            <span class="kv"
                  style:color={bugRepPrepData.context_summary.gsp_event_count > 0 ? 'var(--warn)' : 'var(--text-dim)'}>
              GSP : <b>{bugRepPrepData.context_summary.gsp_event_count}</b>
            </span>
          </div>
        {/if}
        {#if bugRepPrepData?.bug_report_command}
          <div class="form-row" style="gap: 8px; margin-top: 6px;">
            <span class="muted">{i18n.t("integrations.bugrep.run_cmd")} :</span>
            <code style="flex: 1; padding: 6px 10px; background: var(--bg-2);
                          font-family: monospace; font-size: 0.85em;
                          border-radius: 4px;">{bugRepPrepData.bug_report_command}</code>
            <button class="btn btn-small"
                    onclick={() => copyToClipboard(bugRepPrepData?.bug_report_command ?? "")}>📋</button>
          </div>
        {/if}
        {#if bugRepPrepData?.template_text}
          <pre style="margin-top: 6px; padding: 8px; background: var(--bg-2);
                       font-family: monospace; font-size: 0.78em;
                       max-height: 280px; overflow: auto; border-radius: 4px;">{bugRepPrepData.template_text}</pre>
        {/if}
      </div>

      <!-- R&D #26.5 PCIe link-width watcher (UI sprint 17) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.pwidth.title")}</h4>
        <p class="muted">{i18n.t("integrations.pwidth.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadPcieWidth}>{i18n.t("integrations.pwidth.refresh")}</button>
          {#if pcieWidthData}
            <span class="kv" style="margin-left: 12px;"
                  style:color={pcieWidthData.worst_verdict?.startsWith('downgraded') ? 'var(--warn)' : 'var(--text-dim)'}>
              {i18n.t("integrations.pwidth.verdict")} : <b>{pcieWidthData.worst_verdict}</b>
            </span>
          {/if}
        </div>
        {#if pcieWidthData?.summary_reason}
          <p class="muted" style="margin-top: 6px;">{pcieWidthData.summary_reason}</p>
        {/if}
        {#if pcieWidthData?.devices && pcieWidthData.devices.length > 0}
          <table style="width:100%; font-size:0.88em; border-collapse: collapse; margin-top: 6px;">
            <thead>
              <tr style="text-align:left; color: var(--text-dim); border-bottom: 1px solid var(--border);">
                <th style="padding: 4px;">device</th>
                <th style="padding: 4px;">width</th>
                <th style="padding: 4px;">gen</th>
                <th style="padding: 4px;">verdict</th>
              </tr>
            </thead>
            <tbody>
              {#each pcieWidthData.devices as d}
                <tr style="border-bottom: 1px solid var(--border);">
                  <td style="padding: 4px; font-family: monospace; font-size: 0.85em;">{d.bdf}</td>
                  <td style="padding: 4px;">x{d.current_width ?? '?'} / x{d.max_width ?? '?'}</td>
                  <td style="padding: 4px;">{d.current_gen ?? '?'} / {d.max_gen ?? '?'}</td>
                  <td style="padding: 4px;"
                      style:color={d.verdict.verdict === 'ok' ? 'var(--ok)' :
                                 d.verdict.verdict.startsWith('downgraded') ? 'var(--warn)' :
                                 'var(--text-dim)'}>{d.verdict.verdict}</td>
                </tr>
              {/each}
            </tbody>
          </table>
        {/if}
      </div>

      <!-- R&D #26.2 CUDA context-leak detector (UI sprint 17) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.ctxleak.title")}</h4>
        <p class="muted">{i18n.t("integrations.ctxleak.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadCudaCtxLeak}>{i18n.t("integrations.ctxleak.refresh")}</button>
          {#if cudaCtxLeakData}
            <span class="kv" style="margin-left: 12px;">
              {i18n.t("integrations.ctxleak.fd_holders")} : <b>{cudaCtxLeakData.fd_holder_count}</b>
            </span>
            <span class="kv">
              {i18n.t("integrations.ctxleak.compute_pids")} : <b>{cudaCtxLeakData.compute_pid_count}</b>
            </span>
            <span class="kv"
                  style:color={cudaCtxLeakData.leak_count > 0 ? 'var(--warn)' : 'var(--text-dim)'}>
              {i18n.t("integrations.ctxleak.leaks")} : <b>{cudaCtxLeakData.leak_count}</b>
            </span>
          {/if}
        </div>
        {#if cudaCtxLeakData?.verdict}
          <p class="muted" style="margin-top: 6px;">{cudaCtxLeakData.verdict.reason}</p>
        {/if}
        {#if cudaCtxLeakData?.leaks && cudaCtxLeakData.leaks.length > 0}
          <table style="width:100%; font-size:0.85em; border-collapse: collapse;">
            <tbody>
              {#each cudaCtxLeakData.leaks as l}
                <tr style="border-bottom: 1px solid var(--border);">
                  <td style="padding: 4px;">{l.comm} <span class="muted">(pid {l.pid})</span></td>
                  <td style="padding: 4px; font-family: monospace; font-size: 0.8em;">{l.devices.join(", ")}</td>
                  <td style="padding: 4px;">
                    <code style="font-family: monospace; font-size: 0.85em;">{l.kill_cmd}</code>
                    <button class="btn btn-small"
                            onclick={() => copyToClipboard(l.kill_cmd)}>📋</button>
                  </td>
                </tr>
              {/each}
            </tbody>
          </table>
        {/if}
      </div>

      <!-- R&D #26.1 procfs static-asset auditor (UI sprint 17) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.staticaud.title")}</h4>
        <p class="muted">{i18n.t("integrations.staticaud.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadProcStatic}>{i18n.t("integrations.staticaud.refresh")}</button>
          {#if procStaticData}
            <span class="kv" style="margin-left: 12px;"
                  style:color={procStaticData.worst_severity === 'critical' ? 'var(--warn)' :
                             procStaticData.worst_severity === 'warn' ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {i18n.t("integrations.staticaud.verdict")} : <b>{procStaticData.worst_severity}</b>
            </span>
          {/if}
        </div>
        {#if procStaticData?.cards && procStaticData.cards.length > 0}
          {#each procStaticData.cards as c}
            <div style="margin-top: 8px; padding: 8px;
                        border-left: 3px solid {
                          c.verdict.severity === 'critical' ? 'var(--warn)' :
                          c.verdict.severity === 'warn' ? 'var(--accent)' :
                          'var(--ok)'};">
              <div class="form-row" style="gap: 14px; flex-wrap: wrap;">
                <b style="font-family: monospace;">{c.bdf}</b>
                <span class="kv">vendor:device {c.vendor_device}</span>
                <span class="kv">subsystem {c.subsystem}</span>
                <span class="kv">IRQ {c.irq ?? '—'}</span>
              </div>
              <p class="muted" style="margin: 4px 0; font-size: 0.82em;">
                {i18n.t("integrations.staticaud.fp")} :
                <code style="font-family: monospace;">{c.fingerprint.slice(0, 16)}…</code>
              </p>
              <p style:color={c.verdict.severity === 'critical' ? 'var(--warn)' :
                             c.verdict.severity === 'warn' ? 'var(--accent)' :
                             'var(--text-dim)'}>
                <b>{c.verdict.verdict}</b> — {c.verdict.reason}
              </p>
            </div>
          {/each}
        {/if}
      </div>

      <!-- R&D #26.8 mem-bw saturation gauge (UI sprint 17) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.bwgauge.title")}</h4>
        <p class="muted">{i18n.t("integrations.bwgauge.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadMemBwGauge}>{i18n.t("integrations.bwgauge.refresh")}</button>
        </div>
        {#if memBwGaugeData?.per_gpu && memBwGaugeData.per_gpu.length > 0}
          {#each memBwGaugeData.per_gpu as g}
            <div style="margin-top: 8px; padding: 8px;
                        border-left: 3px solid {
                          g.verdict.verdict === 'bandwidth_bound' ? 'var(--accent)' :
                          g.verdict.verdict === 'compute_bound' ? 'var(--accent)' :
                          g.verdict.verdict === 'balanced' ? 'var(--ok)' :
                          'var(--text-dim)'};">
              <div class="form-row" style="gap: 14px; flex-wrap: wrap;">
                <b>GPU{g.index}</b>
                <span class="kv">{i18n.t("integrations.bwgauge.gpu_util")} : <b>{g.gpu_util_mean}%</b></span>
                <span class="kv">{i18n.t("integrations.bwgauge.mem_util")} : <b>{g.mem_util_mean}%</b></span>
                {#if g.ratio_mem_over_gpu !== null}
                  <span class="kv">{i18n.t("integrations.bwgauge.ratio")} : <b>{g.ratio_mem_over_gpu.toFixed(2)}</b></span>
                {/if}
                <span class="kv"><b>{g.verdict.verdict}</b></span>
              </div>
              <p class="muted" style="margin: 4px 0;">{g.verdict.reason}</p>
              {#if g.verdict.recommendation}
                <p style="margin: 4px 0; color: var(--accent);">{g.verdict.recommendation}</p>
              {/if}
            </div>
          {/each}
        {/if}
      </div>

      <!-- R&D #27.4 Power-envelope drift (UI sprint 18) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.pwrenv.title")}</h4>
        <p class="muted">{i18n.t("integrations.pwrenv.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadPwrEnvDrift}>{i18n.t("integrations.pwrenv.refresh")}</button>
          {#if pwrEnvDriftData}
            <span class="kv" style="margin-left: 12px;"
                  style:color={pwrEnvDriftData.worst_severity === 'warn' ? 'var(--warn)' : 'var(--text-dim)'}>
              {i18n.t("integrations.pwrenv.verdict")} : <b>{pwrEnvDriftData.worst_severity}</b>
            </span>
          {/if}
        </div>
        {#if pwrEnvDriftData?.gpus && pwrEnvDriftData.gpus.length > 0}
          {#each pwrEnvDriftData.gpus as g}
            <div style="margin-top: 8px; padding: 8px;
                        border-left: 3px solid {
                          g.verdict.severity === 'warn' ? 'var(--warn)' :
                          g.verdict.severity === 'critical' ? 'var(--warn)' :
                          'var(--ok)'};">
              <div class="form-row" style="gap: 14px; flex-wrap: wrap;">
                <b>{g.name}</b>
                <span class="kv">{i18n.t("integrations.pwrenv.current")} : <b>{g.current_w ?? '—'} W</b></span>
                <span class="kv">{i18n.t("integrations.pwrenv.baseline")} : <b>{g.baseline_w ?? '—'} W</b></span>
                <span class="kv">{i18n.t("integrations.pwrenv.default")} : <b>{g.default_w ?? '—'} W</b></span>
              </div>
              <p style="margin: 4px 0;">{g.verdict.reason}</p>
              {#if g.recovery_cmd}
                <div class="form-row" style="gap: 8px;">
                  <code style="flex: 1; padding: 6px 10px; background: var(--bg-2);
                                font-family: monospace; font-size: 0.85em;
                                border-radius: 4px;">{g.recovery_cmd}</code>
                  <button class="btn btn-small" onclick={() => copyToClipboard(g.recovery_cmd)}>📋</button>
                </div>
              {/if}
            </div>
          {/each}
        {/if}
      </div>

      <!-- R&D #27.1 ReBAR auditor (UI sprint 18) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.rebar.title")}</h4>
        <p class="muted">{i18n.t("integrations.rebar.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadRebarAudit}>{i18n.t("integrations.rebar.refresh")}</button>
          {#if rebarAuditData}
            <span class="kv" style="margin-left: 12px;"
                  style:color={rebarAuditData.worst_verdict === 'rebar_off' ? 'var(--warn)' :
                             rebarAuditData.worst_verdict === 'partial' ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {i18n.t("integrations.rebar.verdict")} : <b>{rebarAuditData.worst_verdict}</b>
            </span>
          {/if}
        </div>
        {#if rebarAuditData?.cards && rebarAuditData.cards.length > 0}
          {#each rebarAuditData.cards as c}
            <div style="margin-top: 8px; padding: 8px;
                        border-left: 3px solid {
                          c.verdict.verdict === 'rebar_on' ? 'var(--ok)' :
                          c.verdict.verdict === 'partial' ? 'var(--accent)' :
                          c.verdict.verdict === 'rebar_off' ? 'var(--warn)' :
                          'var(--text-dim)'};">
              <div class="form-row" style="gap: 14px; flex-wrap: wrap;">
                <b style="font-family: monospace;">{c.bdf}</b>
                <span class="kv">{i18n.t("integrations.rebar.bar1_size")} : <b>{c.bar1_mib ?? '—'} MiB</b></span>
                <span class="kv">{i18n.t("integrations.rebar.vram_total")} : <b>{c.total_vram_gib ?? '—'} GiB</b></span>
                {#if c.verdict.bar1_pct_of_vram !== null}
                  <span class="kv">{i18n.t("integrations.rebar.pct")} : <b>{c.verdict.bar1_pct_of_vram}%</b></span>
                {/if}
              </div>
              <p style="margin: 4px 0;"
                 style:color={c.verdict.verdict === 'rebar_on' ? 'var(--ok)' :
                            c.verdict.verdict === 'rebar_off' ? 'var(--warn)' :
                            'var(--text-dim)'}>
                <b>{c.verdict.verdict}</b> — {c.verdict.reason}
              </p>
              {#if c.verdict.recommendation}
                <p class="muted" style="margin: 4px 0;">
                  <b>{i18n.t("integrations.rebar.uefi_hint")} :</b> {c.verdict.recommendation}
                </p>
              {/if}
            </div>
          {/each}
        {/if}
      </div>

      <!-- R&D #27.3 CPU-RAPL harvester (UI sprint 18) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.rapl.title")}</h4>
        <p class="muted">{i18n.t("integrations.rapl.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadCpuRapl}>{i18n.t("integrations.rapl.refresh")}</button>
          {#if cpuRaplData?.supported && cpuRaplData.total_watts !== null}
            <span class="kv" style="margin-left: 12px;">
              {i18n.t("integrations.rapl.total_w")} : <b>{cpuRaplData.total_watts} W</b>
            </span>
          {/if}
        </div>
        {#if cpuRaplData && !cpuRaplData.supported}
          <p class="muted" style="margin-top: 6px;">{i18n.t("integrations.rapl.not_supported")}</p>
        {/if}
        {#if cpuRaplData?.samples && cpuRaplData.samples.length > 0}
          <table style="width:100%; font-size:0.88em; border-collapse: collapse; margin-top: 6px;">
            <tbody>
              {#each cpuRaplData.samples as s}
                <tr style="border-bottom: 1px solid var(--border);">
                  <td style="padding: 4px;">{i18n.t("integrations.rapl.package")} : <b>{s.name}</b></td>
                  <td style="padding: 4px;">{s.watts !== null ? `${s.watts} W` : (s.error ?? '—')}</td>
                </tr>
              {/each}
            </tbody>
          </table>
        {/if}
      </div>

      <!-- R&D #27.7 Clock-gap detector (UI sprint 18) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.clockgap.title")}</h4>
        <p class="muted">{i18n.t("integrations.clockgap.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadClockGap}>{i18n.t("integrations.clockgap.refresh")}</button>
          {#if clockGapData?.any_capped}
            <span class="kv" style="margin-left: 12px; color: var(--warn);">⚠ at least one GPU capped</span>
          {/if}
        </div>
        {#if clockGapData?.gpus && clockGapData.gpus.length > 0}
          {#each clockGapData.gpus as g}
            <div style="margin-top: 8px; padding: 8px;
                        border-left: 3px solid {
                          g.verdict === 'applied' ? 'var(--ok)' :
                          g.verdict === 'no_apps_clock' ? 'var(--text-dim)' :
                          g.verdict.startsWith('capped_') ? 'var(--warn)' :
                          'var(--accent)'};">
              <div class="form-row" style="gap: 14px; flex-wrap: wrap;">
                <b>GPU{g.index} — {g.name}</b>
                <span class="kv">{i18n.t("integrations.clockgap.applied")} : <b>{g.applied_clk ?? '—'} MHz</b></span>
                <span class="kv">{i18n.t("integrations.clockgap.actual")} : <b>{g.current_clk ?? '—'} MHz</b></span>
                {#if g.gap_mhz !== null}
                  <span class="kv">{i18n.t("integrations.clockgap.gap")} : <b>{g.gap_mhz > 0 ? '+' : ''}{g.gap_mhz} MHz</b></span>
                {/if}
                <span class="kv"><b>{g.verdict}</b></span>
              </div>
              <p class="muted" style="margin: 4px 0;">{g.reason}</p>
              {#if g.binding}
                <p style="margin: 4px 0; font-size: 0.85em;">
                  {i18n.t("integrations.clockgap.binding")} :
                  <code style="font-family: monospace;">{g.binding}</code>
                </p>
              {/if}
            </div>
          {/each}
        {/if}
      </div>

      <!-- R&D #28.1 PCIe runtime-PM auditor (UI sprint 19) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.rpm.title")}</h4>
        <p class="muted">{i18n.t("integrations.rpm.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadPcieRpm}>{i18n.t("integrations.rpm.refresh")}</button>
          {#if pcieRpmData}
            <span class="kv" style="margin-left: 12px;"
                  style:color={pcieRpmData.worst_verdict === 'active' ? 'var(--ok)' :
                             pcieRpmData.worst_verdict === 'suspended_now' ? 'var(--warn)' :
                             pcieRpmData.worst_verdict === 'error' ? 'var(--warn)' :
                             'var(--text-dim)'}>
              {i18n.t("integrations.rpm.verdict")} : <b>{pcieRpmData.worst_verdict}</b>
            </span>
          {/if}
        </div>
        {#if pcieRpmData?.cards}
          {#each pcieRpmData.cards as c}
            <div style="margin-top: 8px; padding: 8px;
                        border-left: 3px solid {
                          c.verdict.verdict === 'active' ? 'var(--ok)' :
                          c.verdict.verdict === 'suspended_now' ? 'var(--warn)' :
                          'var(--text-dim)'};">
              <div class="form-row" style="gap: 14px; flex-wrap: wrap;">
                <b style="font-family: monospace;">{c.bdf}</b>
                <span class="kv">control={c.control ?? '—'}</span>
                <span class="kv">status={c.runtime_status ?? '—'}</span>
                <span class="kv"><b>{c.verdict.verdict}</b></span>
              </div>
              <p class="muted" style="margin: 4px 0;">{c.verdict.reason}</p>
              {#if c.verdict.recommendation}
                <p style="margin: 4px 0;">{c.verdict.recommendation}</p>
              {/if}
              {#if c.udev_recipe}
                <details style="margin-top: 4px;">
                  <summary class="muted">{i18n.t("integrations.rpm.recipe")}</summary>
                  <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                               border-radius: 4px; overflow-x: auto;">{c.udev_recipe}</pre>
                  <button class="btn btn-small"
                          onclick={() => copyToClipboard(c.udev_recipe)}>📋 Copy</button>
                </details>
              {/if}
            </div>
          {/each}
        {/if}
      </div>

      <!-- R&D #28.5 Thermal-zone correlator (UI sprint 19) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.thermzones.title")}</h4>
        <p class="muted">{i18n.t("integrations.thermzones.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadThermalZones}>{i18n.t("integrations.thermzones.refresh")}</button>
          {#if thermalZonesData?.category_counts}
            <span class="kv" style="margin-left: 12px;"
                  style:color={(thermalZonesData.category_counts.critical ?? 0) > 0 ? 'var(--warn)' :
                             (thermalZonesData.category_counts.hot ?? 0) > 0 ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {thermalZonesData.category_counts.cool ?? 0} {i18n.t("integrations.thermzones.cool")} ·
              {thermalZonesData.category_counts.warm ?? 0} {i18n.t("integrations.thermzones.warm")} ·
              {thermalZonesData.category_counts.hot ?? 0} {i18n.t("integrations.thermzones.hot")} ·
              {thermalZonesData.category_counts.critical ?? 0} {i18n.t("integrations.thermzones.critical")}
            </span>
          {/if}
        </div>
        {#if thermalZonesData?.summary}
          <p class="muted" style="margin-top: 6px;">{thermalZonesData.summary}</p>
        {/if}
        {#if thermalZonesData?.advice && thermalZonesData.advice.length > 0}
          <h5 style="margin: 10px 0 4px 0;">{i18n.t("integrations.thermzones.advice")}</h5>
          <ul style="margin: 4px 0 0 18px;">
            {#each thermalZonesData.advice as a}
              <li>{a}</li>
            {/each}
          </ul>
        {/if}
        {#if thermalZonesData?.zones && thermalZonesData.zones.length > 0}
          <table style="width:100%; font-size:0.85em; border-collapse: collapse; margin-top: 6px;">
            <tbody>
              {#each thermalZonesData.zones as z}
                <tr style="border-bottom: 1px solid var(--border);">
                  <td style="padding: 4px; font-family: monospace; font-size: 0.85em;">{z.type}</td>
                  <td style="padding: 4px;">{z.temp_c} °C</td>
                  <td style="padding: 4px;"
                      style:color={z.category === 'cool' ? 'var(--text-dim)' :
                                 z.category === 'warm' ? 'var(--accent)' :
                                 z.category === 'hot' ? 'var(--warn)' :
                                 'var(--warn)'}>{z.category}</td>
                </tr>
              {/each}
            </tbody>
          </table>
        {/if}
      </div>

      <!-- R&D #28.7 NVRM log tailer (UI sprint 19) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.nvrmtail.title")}</h4>
        <p class="muted">{i18n.t("integrations.nvrmtail.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadNvrmTail}>{i18n.t("integrations.nvrmtail.refresh")}</button>
          {#if nvrmTailData?.since}
            <span class="kv" style="margin-left: 12px;">
              {i18n.t("integrations.nvrmtail.since")} : <b>{nvrmTailData.since}</b>
            </span>
          {/if}
          {#if nvrmTailData?.entry_count !== undefined}
            <span class="kv">entries : <b>{nvrmTailData.entry_count}</b></span>
          {/if}
        </div>
        {#if nvrmTailData?.category_counts && Object.keys(nvrmTailData.category_counts).length > 0}
          <div class="form-row" style="gap: 14px; margin-top: 6px; flex-wrap: wrap;">
            {#each Object.entries(nvrmTailData.category_counts) as [cat, n]}
              <span class="kv"
                    style:color={cat === 'xid' || cat === 'gsp' ? 'var(--warn)' : 'var(--text-dim)'}>
                {cat} : <b>{n}</b>
              </span>
            {/each}
          </div>
        {/if}
        {#if nvrmTailData?.entries && nvrmTailData.entries.length === 0}
          <p class="muted" style="margin-top: 6px;">{i18n.t("integrations.nvrmtail.no_entries")}</p>
        {/if}
        {#if nvrmTailData?.entries && nvrmTailData.entries.length > 0}
          <table style="width:100%; font-size:0.82em; border-collapse: collapse;
                         margin-top: 6px; font-family: monospace;">
            <tbody>
              {#each nvrmTailData.entries.slice(-30).reverse() as e}
                <tr style="border-bottom: 1px solid var(--border);">
                  <td style="padding: 3px;"
                      style:color={e.category === 'xid' || e.category === 'gsp' ? 'var(--warn)' :
                                 e.category === 'rm_init' ? 'var(--ok)' :
                                 'var(--text-dim)'}>{e.category}</td>
                  <td style="padding: 3px; color: var(--text-dim);">{e.ts}</td>
                  <td style="padding: 3px;">{e.body}</td>
                </tr>
              {/each}
            </tbody>
          </table>
        {/if}
      </div>

      <!-- R&D #28.4 NVLink health (UI sprint 19) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.nvlink.title")}</h4>
        <p class="muted">{i18n.t("integrations.nvlink.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadNvlinkHealth}>{i18n.t("integrations.nvlink.refresh")}</button>
          {#if nvlinkHealthData?.verdict}
            <span class="kv" style="margin-left: 12px;"
                  style:color={nvlinkHealthData.verdict.verdict === 'clean' ? 'var(--ok)' :
                             nvlinkHealthData.verdict.verdict === 'link_down' ? 'var(--warn)' :
                             nvlinkHealthData.verdict.verdict === 'crc_growth' ? 'var(--warn)' :
                             nvlinkHealthData.verdict.verdict === 'replay_growth' ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {i18n.t("integrations.nvlink.verdict")} : <b>{nvlinkHealthData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if nvlinkHealthData && !nvlinkHealthData.supported}
          <p class="muted" style="margin-top: 6px;">{i18n.t("integrations.nvlink.unsupported")}</p>
        {/if}
        {#if nvlinkHealthData?.supported && nvlinkHealthData.verdict}
          <p style="margin-top: 6px;">{nvlinkHealthData.verdict.reason}</p>
          <div class="form-row" style="gap: 14px; margin-top: 4px; flex-wrap: wrap;">
            <span class="kv">{i18n.t("integrations.nvlink.replay_delta")} : <b>{nvlinkHealthData.verdict.replay_delta ?? 0}</b></span>
            <span class="kv">{i18n.t("integrations.nvlink.crc_delta")} : <b>{nvlinkHealthData.verdict.crc_delta ?? 0}</b></span>
            <span class="kv">{i18n.t("integrations.nvlink.links_down")} : <b>{nvlinkHealthData.verdict.link_down_count ?? 0}</b></span>
          </div>
          {#if nvlinkHealthData.verdict.recommendation}
            <p style="margin: 4px 0; color: var(--accent);">
              {i18n.t("integrations.nvlink.fix")} : {nvlinkHealthData.verdict.recommendation}
            </p>
          {/if}
        {/if}
      </div>

      <!-- R&D #29.1 nvidia kmod params (UI sprint 20) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.kmod.title")}</h4>
        <p class="muted">{i18n.t("integrations.kmod.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadKmodParams}>{i18n.t("integrations.kmod.refresh")}</button>
          {#if kmodParamsData}
            <span class="kv" style="margin-left: 12px;">
              {i18n.t("integrations.kmod.param_count")} : <b>{kmodParamsData.param_count ?? 0}</b>
            </span>
            <span class="kv"
                  style:color={(kmodParamsData.footgun_count ?? 0) > 0 ? 'var(--warn)' : 'var(--text-dim)'}>
              {i18n.t("integrations.kmod.footguns")} : <b>{kmodParamsData.footgun_count ?? 0}</b>
            </span>
          {/if}
        </div>
        {#if kmodParamsData?.footguns && kmodParamsData.footguns.length > 0}
          {#each kmodParamsData.footguns as f}
            <div style="margin-top: 8px; padding: 8px;
                        border-left: 3px solid {
                          f.severity === 'warn' ? 'var(--warn)' :
                          f.severity === 'critical' ? 'var(--warn)' :
                          'var(--text-dim)'};">
              <div class="form-row" style="gap: 12px; flex-wrap: wrap;">
                <b style="font-family: monospace;">{f.param}</b>
                <span class="kv">current : <b>{f.current}</b></span>
                {#if f.recommended}
                  <span class="kv">recommended : <b>{f.recommended}</b></span>
                {/if}
              </div>
              <p class="muted" style="margin: 4px 0;">{f.advice}</p>
              {#if f.recipe}
                <details style="margin-top: 4px;">
                  <summary class="muted">{i18n.t("integrations.kmod.recipe")}</summary>
                  <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                               border-radius: 4px; overflow-x: auto;">{f.recipe}</pre>
                  <button class="btn btn-small"
                          onclick={() => copyToClipboard(f.recipe)}>📋 Copy</button>
                </details>
              {/if}
            </div>
          {/each}
        {/if}
      </div>

      <!-- R&D #29.3 D3cold policy (UI sprint 20) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.d3cold.title")}</h4>
        <p class="muted">{i18n.t("integrations.d3cold.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadD3coldPolicy}>{i18n.t("integrations.d3cold.refresh")}</button>
          {#if d3coldPolicyData}
            <span class="kv" style="margin-left: 12px;"
                  style:color={d3coldPolicyData.worst_verdict.startsWith('mismatched') ? 'var(--warn)' :
                             d3coldPolicyData.worst_verdict === 'unknown' ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {i18n.t("integrations.d3cold.verdict")} : <b>{d3coldPolicyData.worst_verdict}</b>
            </span>
          {/if}
        </div>
        {#if d3coldPolicyData?.cards && d3coldPolicyData.cards.length > 0}
          {#each d3coldPolicyData.cards as c}
            <div style="margin-top: 8px; padding: 8px;
                        border-left: 3px solid {
                          c.verdict.verdict.startsWith('mismatched') ? 'var(--warn)' :
                          c.verdict.verdict.startsWith('aligned') ? 'var(--ok)' :
                          'var(--text-dim)'};">
              <div class="form-row" style="gap: 14px; flex-wrap: wrap;">
                <b style="font-family: monospace;">{c.gpu_bdf}</b>
                <span class="kv">control={c.gpu_control ?? '—'}</span>
                <span class="kv">{i18n.t("integrations.d3cold.bridge")} :
                  <b style="font-family: monospace;">{c.bridge_bdf ?? '—'}</b>
                </span>
                <span class="kv">d3cold_allowed={c.bridge_d3cold_allowed ?? '—'}</span>
              </div>
              <p style="margin: 4px 0;">{c.verdict.reason}</p>
              {#if c.verdict.recommendation}
                <p class="muted">{c.verdict.recommendation}</p>
              {/if}
            </div>
          {/each}
        {/if}
      </div>

      <!-- R&D #29.7 Thermal slowdown kind (UI sprint 20) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.tslow.title")}</h4>
        <p class="muted">{i18n.t("integrations.tslow.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadThermalSlowdown}>{i18n.t("integrations.tslow.refresh")}</button>
          {#if thermalSlowdownData?.any_critical}
            <span class="kv" style="margin-left: 12px; color: var(--warn);">⚠ critical</span>
          {/if}
        </div>
        {#if thermalSlowdownData?.gpus && thermalSlowdownData.gpus.length > 0}
          {#each thermalSlowdownData.gpus as g}
            <div style="margin-top: 8px; padding: 8px;
                        border-left: 3px solid {
                          g.verdict.severity === 'critical' ? 'var(--warn)' :
                          g.verdict.severity === 'warn' ? 'var(--accent)' :
                          'var(--ok)'};">
              <div class="form-row" style="gap: 14px; flex-wrap: wrap;">
                <b>GPU{g.index} — {g.name}</b>
                <span class="kv">GPU {g.gpu_temp_c ?? '—'}°C</span>
                <span class="kv">VRAM {g.mem_temp_c ?? '—'}°C</span>
                <span class="kv"><b>{g.verdict.verdict}</b></span>
              </div>
              <p style="margin: 4px 0;">{g.verdict.reason}</p>
              {#if g.verdict.recommendation}
                <p style="margin: 4px 0; color: var(--accent);">
                  {i18n.t("integrations.tslow.recommend")} : {g.verdict.recommendation}
                </p>
              {/if}
            </div>
          {/each}
        {/if}
      </div>

      <!-- R&D #29.8 rlimit auditor (UI sprint 20) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.rlimit.title")}</h4>
        <p class="muted">{i18n.t("integrations.rlimit.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadRlimitAudit}>{i18n.t("integrations.rlimit.refresh")}</button>
          {#if rlimitAuditData}
            <span class="kv" style="margin-left: 12px;"
                  style:color={rlimitAuditData.worst_verdict === 'severely_low' ? 'var(--warn)' :
                             rlimitAuditData.worst_verdict === 'low_limit' ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {rlimitAuditData.process_count} processes · worst : <b>{rlimitAuditData.worst_verdict}</b>
            </span>
          {/if}
        </div>
        {#if rlimitAuditData?.process_count === 0}
          <p class="muted" style="margin-top: 6px;">{i18n.t("integrations.rlimit.no_procs")}</p>
        {/if}
        {#if rlimitAuditData?.processes && rlimitAuditData.processes.length > 0}
          {#each rlimitAuditData.processes as p}
            <div style="margin-top: 8px; padding: 8px;
                        border-left: 3px solid {
                          p.verdict.verdict === 'severely_low' ? 'var(--warn)' :
                          p.verdict.verdict === 'low_limit' ? 'var(--accent)' :
                          'var(--ok)'};">
              <div class="form-row" style="gap: 14px; flex-wrap: wrap;">
                <b>{p.comm} <span class="muted">(pid {p.pid})</span></b>
                <span class="kv">{i18n.t("integrations.rlimit.memlock")} :
                  <b>{p.memlock_bytes !== null
                       ? (p.memlock_bytes > 1024 ** 3
                          ? (p.memlock_bytes >= 2 ** 60 ? "∞" : `${Math.round(p.memlock_bytes / 1024 ** 3)} GiB`)
                          : `${Math.round(p.memlock_bytes / 1024 ** 2)} MiB`)
                       : '—'}</b>
                </span>
                <span class="kv">{i18n.t("integrations.rlimit.vm_lck")} :
                  <b>{p.vm_lck_bytes !== null
                       ? `${Math.round((p.vm_lck_bytes ?? 0) / 1024 ** 2)} MiB`
                       : '—'}</b>
                </span>
                <span class="kv"><b>{p.verdict.verdict}</b></span>
              </div>
              <p style="margin: 4px 0;">{p.verdict.reason}</p>
              {#if p.recipe}
                <details style="margin-top: 4px;">
                  <summary class="muted">{i18n.t("integrations.rlimit.recipe")}</summary>
                  <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                               border-radius: 4px; overflow-x: auto;">{p.recipe}</pre>
                  <button class="btn btn-small"
                          onclick={() => copyToClipboard(p.recipe)}>📋 Copy</button>
                </details>
              {/if}
            </div>
          {/each}
        {/if}
      </div>

      <!-- R&D #30.5 DMI/BIOS revision tracker (UI sprint 21) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.dmi.title")}</h4>
        <p class="muted">{i18n.t("integrations.dmi.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadDmiBios}>{i18n.t("integrations.dmi.refresh")}</button>
          {#if dmiBiosData?.ok && dmiBiosData.verdict}
            <span class="kv" style="margin-left: 12px;"
                  style:color={dmiBiosData.verdict.verdict === 'outdated' ? 'var(--warn)' :
                             dmiBiosData.verdict.verdict === 'up_to_date' ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {i18n.t("integrations.dmi.verdict")} : <b>{dmiBiosData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if dmiBiosData?.ok === false}
          <p class="muted" style="margin-top: 6px;">{i18n.t("integrations.dmi.unavailable")} {dmiBiosData.reason ?? ''}</p>
        {/if}
        {#if dmiBiosData?.ok && dmiBiosData.dmi}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        dmiBiosData.verdict?.verdict === 'outdated' ? 'var(--warn)' :
                        dmiBiosData.verdict?.verdict === 'up_to_date' ? 'var(--accent)' :
                        'var(--text-dim)'};">
            <div class="form-row" style="gap: 12px; flex-wrap: wrap;">
              <span class="kv">{i18n.t("integrations.dmi.board")} :
                <b>{dmiBiosData.dmi.board_name ?? dmiBiosData.dmi.product_name ?? '—'}</b>
              </span>
              <span class="kv">{i18n.t("integrations.dmi.bios")} :
                <b style="font-family: monospace;">{dmiBiosData.dmi.bios_version ?? '—'}</b>
              </span>
              <span class="kv">{i18n.t("integrations.dmi.date")} :
                <b>{dmiBiosData.bios_date_iso ?? dmiBiosData.dmi.bios_date ?? '—'}</b>
              </span>
              {#if dmiBiosData.drift && dmiBiosData.drift.status !== 'no_drift'}
                <span class="kv" style:color="var(--warn)">{i18n.t("integrations.dmi.drift")} :
                  <b>{dmiBiosData.drift.status}</b>
                </span>
              {/if}
            </div>
            {#if dmiBiosData.verdict}
              <p style="margin: 4px 0;">{dmiBiosData.verdict.reason}</p>
            {/if}
            {#if dmiBiosData.verdict?.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.dmi.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{dmiBiosData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(dmiBiosData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #30.3 NVMe I/O scheduler tuner (UI sprint 21) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.nvme.title")}</h4>
        <p class="muted">{i18n.t("integrations.nvme.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadNvmeIosched}>{i18n.t("integrations.nvme.refresh")}</button>
          {#if nvmeIoschedData}
            <span class="kv" style="margin-left: 12px;"
                  style:color={nvmeIoschedData.worst_verdict === 'both_bad' ||
                              nvmeIoschedData.worst_verdict === 'suboptimal_scheduler' ? 'var(--warn)' :
                             nvmeIoschedData.worst_verdict === 'low_readahead' ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {nvmeIoschedData.device_count} NVMe · {i18n.t("integrations.nvme.verdict")} : <b>{nvmeIoschedData.worst_verdict}</b>
            </span>
          {/if}
        </div>
        {#if nvmeIoschedData?.worst_verdict === 'no_nvme'}
          <p class="muted" style="margin-top: 6px;">{i18n.t("integrations.nvme.no_nvme")}</p>
        {/if}
        {#if nvmeIoschedData?.devices && nvmeIoschedData.devices.length > 0}
          {#each nvmeIoschedData.devices as d}
            <div style="margin-top: 8px; padding: 8px;
                        border-left: 3px solid {
                          d.verdict.verdict === 'both_bad' ||
                          d.verdict.verdict === 'suboptimal_scheduler' ? 'var(--warn)' :
                          d.verdict.verdict === 'low_readahead' ? 'var(--accent)' :
                          'var(--text-dim)'};">
              <div class="form-row" style="gap: 12px; flex-wrap: wrap;">
                <b style="font-family: monospace;">{d.device}</b>
                <span class="kv">{i18n.t("integrations.nvme.scheduler")} : <b>{d.scheduler ?? '—'}</b></span>
                <span class="kv">{i18n.t("integrations.nvme.readahead")} : <b>{d.read_ahead_kb ?? '—'} KiB</b></span>
                <span class="kv">{i18n.t("integrations.nvme.requests")} : <b>{d.nr_requests ?? '—'}</b></span>
                <span class="kv"><b>{d.verdict.verdict}</b></span>
              </div>
              <p style="margin: 4px 0;">{d.verdict.reason}</p>
              {#if d.verdict.recommendation}
                <details style="margin-top: 4px;">
                  <summary class="muted">{i18n.t("integrations.nvme.recipe")}</summary>
                  <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                               border-radius: 4px; overflow-x: auto;">{d.verdict.recommendation}</pre>
                  <button class="btn btn-small"
                          onclick={() => copyToClipboard(d.verdict.recommendation)}>📋 Copy</button>
                </details>
              {/if}
            </div>
          {/each}
        {/if}
      </div>

      <!-- R&D #30.2 IOMMU group auditor (UI sprint 21) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.iommu.title")}</h4>
        <p class="muted">{i18n.t("integrations.iommu.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadIommuGroups}>{i18n.t("integrations.iommu.refresh")}</button>
          {#if iommuGroupsData?.ok && iommuGroupsData.worst_verdict}
            <span class="kv" style="margin-left: 12px;"
                  style:color={iommuGroupsData.worst_verdict === 'chipset_shared' ? 'var(--warn)' :
                             iommuGroupsData.worst_verdict === 'unknown' ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {iommuGroupsData.device_count} GPU · {i18n.t("integrations.iommu.verdict")} : <b>{iommuGroupsData.worst_verdict}</b>
            </span>
          {/if}
        </div>
        {#if iommuGroupsData?.ok === false}
          <p class="muted" style="margin-top: 6px;">{i18n.t("integrations.iommu.disabled")} {iommuGroupsData.reason ?? ''}</p>
          {#if iommuGroupsData?.recommendation}
            <details style="margin-top: 4px;">
              <summary class="muted">{i18n.t("integrations.iommu.recipe")}</summary>
              <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                           border-radius: 4px; overflow-x: auto;">{iommuGroupsData.recommendation}</pre>
              <button class="btn btn-small"
                      onclick={() => copyToClipboard(iommuGroupsData!.recommendation!)}>📋 Copy</button>
            </details>
          {/if}
        {/if}
        {#if iommuGroupsData?.cards && iommuGroupsData.cards.length > 0}
          {#each iommuGroupsData.cards as c}
            <div style="margin-top: 8px; padding: 8px;
                        border-left: 3px solid {
                          c.verdict.verdict === 'chipset_shared' ? 'var(--warn)' :
                          c.verdict.verdict === 'unknown' ? 'var(--accent)' :
                          'var(--text-dim)'};">
              <div class="form-row" style="gap: 12px; flex-wrap: wrap;">
                <b style="font-family: monospace;">{c.gpu_bdf}</b>
                <span class="kv">{i18n.t("integrations.iommu.group")} : <b>{c.iommu_group ?? '—'}</b></span>
                <span class="kv"><b>{c.verdict.verdict}</b></span>
              </div>
              {#if c.siblings && c.siblings.length > 0}
                <p class="muted" style="margin: 4px 0;">
                  {i18n.t("integrations.iommu.siblings")} :
                  {#each c.siblings as s}
                    <span class="kv" style="margin: 0 4px;">{s.bdf} ({s.kind})</span>
                  {/each}
                </p>
              {/if}
              <p style="margin: 4px 0;">{c.verdict.reason}</p>
              {#if c.verdict.recommendation}
                <details style="margin-top: 4px;">
                  <summary class="muted">{i18n.t("integrations.iommu.recipe")}</summary>
                  <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                               border-radius: 4px; overflow-x: auto;">{c.verdict.recommendation}</pre>
                  <button class="btn btn-small"
                          onclick={() => copyToClipboard(c.verdict.recommendation)}>📋 Copy</button>
                </details>
              {/if}
            </div>
          {/each}
        {/if}
      </div>

      <!-- R&D #39.1 cmdline audit (UI sprint 30) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.cmd.title")}</h4>
        <p class="muted">{i18n.t("integrations.cmd.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadCmdlineAudit}>{i18n.t("integrations.cmd.refresh")}</button>
          {#if cmdlineAuditData?.ok && cmdlineAuditData.verdict}
            <span class="kv" style="margin-left: 12px;"
                  style:color={cmdlineAuditData.verdict.verdict === 'safety_disabled' ? 'var(--warn)' :
                             ['perf_tuned','power_or_virt'].includes(cmdlineAuditData.verdict.verdict) ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {i18n.t("integrations.cmd.verdict")} : <b>{cmdlineAuditData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if cmdlineAuditData?.ok === false}
          <p class="muted" style="margin-top: 6px;">{i18n.t("integrations.cmd.unavailable")}</p>
        {/if}
        {#if cmdlineAuditData?.ok && cmdlineAuditData.verdict}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        cmdlineAuditData.verdict.verdict === 'safety_disabled' ? 'var(--warn)' :
                        ['perf_tuned','power_or_virt'].includes(cmdlineAuditData.verdict.verdict) ? 'var(--accent)' :
                        'var(--text-dim)'};">
            {#if cmdlineAuditData.raw}
              <p class="muted" style="margin: 4px 0; font-family: monospace; font-size: 0.85em; word-break: break-all;">{cmdlineAuditData.raw}</p>
            {/if}
            <p style="margin: 4px 0;">{cmdlineAuditData.verdict.reason}</p>
            {#if cmdlineAuditData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.cmd.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{cmdlineAuditData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(cmdlineAuditData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #39.3 coredump readiness (UI sprint 30) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.core.title")}</h4>
        <p class="muted">{i18n.t("integrations.core.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadCoredump}>{i18n.t("integrations.core.refresh")}</button>
          {#if coredumpData?.ok}
            <span class="kv" style="margin-left: 12px;"
                  style:color={['core_disabled','filter_too_low'].includes(coredumpData.verdict.verdict) ? 'var(--warn)' :
                             coredumpData.verdict.verdict === 'relative_pattern' ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {i18n.t("integrations.core.verdict")} : <b>{coredumpData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if coredumpData?.ok}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        ['core_disabled','filter_too_low'].includes(coredumpData.verdict.verdict) ? 'var(--warn)' :
                        coredumpData.verdict.verdict === 'relative_pattern' ? 'var(--accent)' :
                        'var(--text-dim)'};">
            <div class="form-row" style="gap: 12px; flex-wrap: wrap;">
              <span class="kv">core_pattern : <b style="font-family: monospace;">{coredumpData.core_pattern || '—'}</b></span>
              {#if coredumpData.processes && coredumpData.processes.length > 0}
                {#each coredumpData.processes as p}
                  <span class="kv">{p.comm} filter=<b>0x{(p.filter_value ?? 0).toString(16)}</b></span>
                {/each}
              {/if}
            </div>
            <p style="margin: 4px 0;">{coredumpData.verdict.reason}</p>
            {#if coredumpData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.core.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{coredumpData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(coredumpData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #39.4 host class (UI sprint 30) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.host.title")}</h4>
        <p class="muted">{i18n.t("integrations.host.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadHostClass}>{i18n.t("integrations.host.refresh")}</button>
          {#if hostClassData?.ok && hostClassData.verdict}
            <span class="kv" style="margin-left: 12px;"
                  style:color={hostClassData.verdict.verdict === 'vm' ? 'var(--accent)' :
                             ['laptop','server'].includes(hostClassData.verdict.verdict) ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {i18n.t("integrations.host.verdict")} : <b>{hostClassData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if hostClassData?.ok && hostClassData.verdict}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid var(--text-dim);">
            <div class="form-row" style="gap: 12px; flex-wrap: wrap;">
              {#if hostClassData.chassis_kind}
                <span class="kv">{i18n.t("integrations.host.chassis")} : <b>{hostClassData.chassis_kind}</b></span>
              {/if}
              {#if hostClassData.sys_vendor}
                <span class="kv">{hostClassData.sys_vendor} {hostClassData.product_name ?? ''}</span>
              {/if}
              {#if hostClassData.virt?.is_virt}
                <span class="kv">{i18n.t("integrations.host.virt")} : <b>{hostClassData.virt.platform ?? 'yes'}</b></span>
              {/if}
            </div>
            <p style="margin: 4px 0;">{hostClassData.verdict.reason}</p>
            {#if hostClassData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.host.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{hostClassData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(hostClassData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #39.2 sysctl.d drift (UI sprint 30) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.sysd.title")}</h4>
        <p class="muted">{i18n.t("integrations.sysd.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadSysctlDAudit}>{i18n.t("integrations.sysd.refresh")}</button>
          {#if sysctlDAuditData?.ok}
            <span class="kv" style="margin-left: 12px;"
                  style:color={sysctlDAuditData.verdict.verdict === 'drift' ? 'var(--warn)' :
                             sysctlDAuditData.verdict.verdict === 'no_config' ? 'var(--text-dim)' :
                             'var(--text-dim)'}>
              {sysctlDAuditData.on_disk_count} keys · {i18n.t("integrations.sysd.verdict")} : <b>{sysctlDAuditData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if sysctlDAuditData?.ok}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        sysctlDAuditData.verdict.verdict === 'drift' ? 'var(--warn)' :
                        'var(--text-dim)'};">
            <p style="margin: 4px 0;">{sysctlDAuditData.verdict.reason}</p>
            {#if sysctlDAuditData.drift_rows && sysctlDAuditData.drift_rows.length > 0}
              <details style="margin-top: 4px;">
                <summary class="muted">{sysctlDAuditData.drift_rows.length} drift row(s)</summary>
                <ul style="font-size: 0.85em; margin: 4px 0; padding-left: 20px;">
                  {#each sysctlDAuditData.drift_rows as r}
                    <li>
                      <span style="font-family: monospace;">{r.key}</span>:
                      on-disk=<b>{r.on_disk}</b>,
                      runtime=<b style:color="var(--warn)">{r.runtime}</b>
                    </li>
                  {/each}
                </ul>
              </details>
            {/if}
            {#if sysctlDAuditData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.sysd.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{sysctlDAuditData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(sysctlDAuditData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #40.2 KSM advisor (UI sprint 31) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.ksm.title")}</h4>
        <p class="muted">{i18n.t("integrations.ksm.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadKsmAdvisor}>{i18n.t("integrations.ksm.refresh")}</button>
          {#if ksmAdvisorData?.ok && ksmAdvisorData.verdict}
            <span class="kv" style="margin-left: 12px;"
                  style:color={ksmAdvisorData.verdict.verdict === 'hurting_inference' ? 'var(--warn)' :
                             ksmAdvisorData.verdict.verdict === 'running_no_dedup' ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {i18n.t("integrations.ksm.verdict")} : <b>{ksmAdvisorData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if ksmAdvisorData?.ok && ksmAdvisorData.verdict}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        ksmAdvisorData.verdict.verdict === 'hurting_inference' ? 'var(--warn)' :
                        ksmAdvisorData.verdict.verdict === 'running_no_dedup' ? 'var(--accent)' :
                        'var(--text-dim)'};">
            <div class="form-row" style="gap: 12px; flex-wrap: wrap;">
              <span class="kv">run=<b>{ksmAdvisorData.state.run ?? '—'}</b></span>
              <span class="kv">pages_sharing=<b>{ksmAdvisorData.state.pages_sharing ?? 0}</b></span>
              <span class="kv">merge_across_nodes=<b>{ksmAdvisorData.state.merge_across_nodes ?? '—'}</b></span>
              <span class="kv">{ksmAdvisorData.process_count} LLM proc(s)</span>
            </div>
            <p style="margin: 4px 0;">{ksmAdvisorData.verdict.reason}</p>
            {#if ksmAdvisorData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.ksm.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{ksmAdvisorData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(ksmAdvisorData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #40.3 deeper VM tuning (UI sprint 31) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.vmd.title")}</h4>
        <p class="muted">{i18n.t("integrations.vmd.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadVmTuningDeep}>{i18n.t("integrations.vmd.refresh")}</button>
          {#if vmTuningDeepData?.ok}
            <span class="kv" style="margin-left: 12px;"
                  style:color={['zone_reclaim_conflict','nvme_swap_readahead_waste','late_kswapd_wake'].includes(vmTuningDeepData.verdict.verdict) ? 'var(--warn)' :
                             vmTuningDeepData.verdict.verdict === 'defaults_on_tight_box' ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {i18n.t("integrations.vmd.verdict")} : <b>{vmTuningDeepData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if vmTuningDeepData?.ok}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        ['zone_reclaim_conflict','nvme_swap_readahead_waste','late_kswapd_wake'].includes(vmTuningDeepData.verdict.verdict) ? 'var(--warn)' :
                        vmTuningDeepData.verdict.verdict === 'defaults_on_tight_box' ? 'var(--accent)' :
                        'var(--text-dim)'};">
            <div class="form-row" style="gap: 12px; flex-wrap: wrap;">
              {#each Object.entries(vmTuningDeepData.knobs) as [k, v]}
                <span class="kv">{k}=<b>{v}</b></span>
              {/each}
              {#if vmTuningDeepData.swap_active}
                <span class="kv" style:color="var(--warn)">swap active</span>
              {/if}
              {#if vmTuningDeepData.mem_pressure !== null}
                <span class="kv">mem={(vmTuningDeepData.mem_pressure * 100).toFixed(0)}%</span>
              {/if}
            </div>
            <p style="margin: 4px 0;">{vmTuningDeepData.verdict.reason}</p>
            {#if vmTuningDeepData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.vmd.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{vmTuningDeepData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(vmTuningDeepData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #40.1 GPU PCIe driver-binding (UI sprint 31) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.gpb.title")}</h4>
        <p class="muted">{i18n.t("integrations.gpb.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadGpuPciBind}>{i18n.t("integrations.gpb.refresh")}</button>
          {#if gpuPciBindData?.ok && gpuPciBindData.verdict}
            <span class="kv" style="margin-left: 12px;"
                  style:color={['stuck_or_orphaned','mixed_function_bind'].includes(gpuPciBindData.verdict.verdict) ? 'var(--warn)' :
                             gpuPciBindData.verdict.verdict === 'vfio_bound' ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {gpuPciBindData.device_count} dev · {i18n.t("integrations.gpb.verdict")} : <b>{gpuPciBindData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if gpuPciBindData?.ok && gpuPciBindData.verdict}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        ['stuck_or_orphaned','mixed_function_bind'].includes(gpuPciBindData.verdict.verdict) ? 'var(--warn)' :
                        gpuPciBindData.verdict.verdict === 'vfio_bound' ? 'var(--accent)' :
                        'var(--text-dim)'};">
            <table style="font-size: 0.85em; margin: 4px 0; border-collapse: collapse;">
              <thead><tr style="text-align: left;">
                <th style="padding-right: 8px;">BDF</th>
                <th style="padding-right: 8px;">Role</th>
                <th style="padding-right: 8px;">Device</th>
                <th style="padding-right: 8px;">Driver</th>
                <th style="padding-right: 8px;">Enable</th>
                <th>IOMMU</th>
              </tr></thead>
              <tbody>
              {#each gpuPciBindData.devices as d}
                <tr>
                  <td style="font-family: monospace; padding-right: 8px;">{d.bdf}</td>
                  <td style="padding-right: 8px;">{d.function_role}</td>
                  <td style="padding-right: 8px;">{d.device_id}</td>
                  <td style="padding-right: 8px;"><b>{d.driver ?? '—'}</b></td>
                  <td style="padding-right: 8px;">{d.enable ?? '—'}</td>
                  <td>{d.iommu_group ?? '—'}</td>
                </tr>
              {/each}
              </tbody>
            </table>
            <p style="margin: 4px 0;">{gpuPciBindData.verdict.reason}</p>
            {#if gpuPciBindData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.gpb.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{gpuPciBindData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(gpuPciBindData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #40.4 NIC queue affinity (UI sprint 31) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.nic.title")}</h4>
        <p class="muted">{i18n.t("integrations.nic.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadNicQueueAffinity}>{i18n.t("integrations.nic.refresh")}</button>
          {#if nicQueueAffinityData?.ok && nicQueueAffinityData.verdict}
            <span class="kv" style="margin-left: 12px;"
                  style:color={['rps_misaligned_with_gpu_numa','multi_queue_no_rps','xps_single_cpu_bottleneck'].includes(nicQueueAffinityData.verdict.verdict) ? 'var(--warn)' :
                             nicQueueAffinityData.verdict.verdict === 'rfs_disabled' ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {i18n.t("integrations.nic.verdict")} : <b>{nicQueueAffinityData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if nicQueueAffinityData?.ok && nicQueueAffinityData.verdict}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        ['rps_misaligned_with_gpu_numa','multi_queue_no_rps','xps_single_cpu_bottleneck'].includes(nicQueueAffinityData.verdict.verdict) ? 'var(--warn)' :
                        nicQueueAffinityData.verdict.verdict === 'rfs_disabled' ? 'var(--accent)' :
                        'var(--text-dim)'};">
            <div class="form-row" style="gap: 12px; flex-wrap: wrap;">
              {#each nicQueueAffinityData.devices as d}
                <span class="kv">{d.dev}({d.operstate})
                  rx=<b>{d.rx_queue_count}</b> tx=<b>{d.tx_queue_count}</b></span>
              {/each}
              {#if nicQueueAffinityData.gpu_numa_cpus.length > 0}
                <span class="kv">GPU NUMA cores : {nicQueueAffinityData.gpu_numa_cpus.length}</span>
              {/if}
            </div>
            <p style="margin: 4px 0;">{nicQueueAffinityData.verdict.reason}</p>
            {#if nicQueueAffinityData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.nic.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{nicQueueAffinityData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(nicQueueAffinityData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #41.3 panic policy (UI sprint 32) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.pan.title")}</h4>
        <p class="muted">{i18n.t("integrations.pan.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadPanicPolicy}>{i18n.t("integrations.pan.refresh")}</button>
          {#if panicPolicyData?.ok && panicPolicyData.verdict}
            <span class="kv" style="margin-left: 12px;"
                  style:color={['stuck_forever_on_panic','silent_on_hung_task'].includes(panicPolicyData.verdict.verdict) ? 'var(--warn)' :
                             panicPolicyData.verdict.verdict === 'watchdog_disabled' ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {i18n.t("integrations.pan.verdict")} : <b>{panicPolicyData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if panicPolicyData?.ok && panicPolicyData.verdict}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        ['stuck_forever_on_panic','silent_on_hung_task'].includes(panicPolicyData.verdict.verdict) ? 'var(--warn)' :
                        panicPolicyData.verdict.verdict === 'watchdog_disabled' ? 'var(--accent)' :
                        'var(--text-dim)'};">
            <div class="form-row" style="gap: 12px; flex-wrap: wrap;">
              <span class="kv">panic=<b>{panicPolicyData.knobs.panic ?? '?'}</b></span>
              <span class="kv">panic_on_oops=<b>{panicPolicyData.knobs.panic_on_oops ?? '?'}</b></span>
              <span class="kv">hung_task_panic=<b>{panicPolicyData.knobs.hung_task_panic ?? '?'}</b></span>
              <span class="kv">softlockup_panic=<b>{panicPolicyData.knobs.softlockup_panic ?? '?'}</b></span>
              <span class="kv">nmi_watchdog=<b>{panicPolicyData.knobs.nmi_watchdog ?? '?'}</b></span>
              {#if panicPolicyData.host_form_factor}
                <span class="kv">host=<b>{panicPolicyData.host_form_factor}</b></span>
              {/if}
            </div>
            <p style="margin: 4px 0;">{panicPolicyData.verdict.reason}</p>
            {#if panicPolicyData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.pan.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{panicPolicyData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(panicPolicyData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #41.2 EDAC RAM ECC (UI sprint 32) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.ecc.title")}</h4>
        <p class="muted">{i18n.t("integrations.ecc.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadEdacRamEcc}>{i18n.t("integrations.ecc.refresh")}</button>
          {#if edacRamEccData?.ok}
            <span class="kv" style="margin-left: 12px;"
                  style:color={edacRamEccData.verdict.verdict === 'ue_present' ? 'var(--warn)' :
                             edacRamEccData.verdict.verdict === 'ce_climbing' ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {i18n.t("integrations.ecc.verdict")} : <b>{edacRamEccData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if edacRamEccData?.ok}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        edacRamEccData.verdict.verdict === 'ue_present' ? 'var(--warn)' :
                        edacRamEccData.verdict.verdict === 'ce_climbing' ? 'var(--accent)' :
                        'var(--text-dim)'};">
            <div class="form-row" style="gap: 12px; flex-wrap: wrap;">
              <span class="kv">controllers=<b>{edacRamEccData.controllers.length}</b></span>
              <span class="kv">ce_total=<b>{edacRamEccData.ce_total}</b></span>
              <span class="kv">ue_total=<b
                style:color={edacRamEccData.ue_total > 0 ? 'var(--warn)' : 'inherit'}
              >{edacRamEccData.ue_total}</b></span>
            </div>
            {#if edacRamEccData.controllers.length > 0}
              <details style="margin-top: 4px;">
                <summary class="muted">DIMM detail</summary>
                <ul style="font-size: 0.85em; margin: 4px 0; padding-left: 20px;">
                  {#each edacRamEccData.controllers as c}
                    {#each c.dimms as d}
                      <li>{c.name}/{d.name} label=<b>{d.label ?? '?'}</b>
                        size={d.size_mb ?? '?'}MB
                        ce=<b>{d.ce_count}</b>
                        ue=<b style:color={d.ue_count > 0 ? 'var(--warn)' : 'inherit'}>{d.ue_count}</b></li>
                    {/each}
                  {/each}
                </ul>
              </details>
            {/if}
            <p style="margin: 4px 0;">{edacRamEccData.verdict.reason}</p>
            {#if edacRamEccData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.ecc.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{edacRamEccData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(edacRamEccData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #41.4 inotify audit (UI sprint 32) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.ino.title")}</h4>
        <p class="muted">{i18n.t("integrations.ino.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadInotifyAudit}>{i18n.t("integrations.ino.refresh")}</button>
          {#if inotifyAuditData?.ok}
            <span class="kv" style="margin-left: 12px;"
                  style:color={['approaching_max_watches','instance_per_pid_high'].includes(inotifyAuditData.verdict.verdict) ? 'var(--warn)' :
                             'var(--text-dim)'}>
              {inotifyAuditData.process_count} watchers · {i18n.t("integrations.ino.verdict")} : <b>{inotifyAuditData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if inotifyAuditData?.ok}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        ['approaching_max_watches','instance_per_pid_high'].includes(inotifyAuditData.verdict.verdict) ? 'var(--warn)' :
                        'var(--text-dim)'};">
            <div class="form-row" style="gap: 12px; flex-wrap: wrap;">
              <span class="kv">max_watches=<b>{inotifyAuditData.limits.max_user_watches ?? '?'}</b></span>
              <span class="kv">max_instances=<b>{inotifyAuditData.limits.max_user_instances ?? '?'}</b></span>
              {#each Object.entries(inotifyAuditData.by_uid) as [uid, agg]}
                <span class="kv">uid={uid}: <b>{agg.watches}</b> watches / <b>{agg.instances}</b> inst</span>
              {/each}
            </div>
            {#if inotifyAuditData.top_processes.length > 0}
              <details style="margin-top: 4px;">
                <summary class="muted">Top watchers</summary>
                <ul style="font-size: 0.85em; margin: 4px 0; padding-left: 20px;">
                  {#each inotifyAuditData.top_processes.slice(0, 10) as p}
                    <li>{p.comm}(pid {p.pid}) — <b>{p.inotify_watches}</b> watches, {p.inotify_instances} inst</li>
                  {/each}
                </ul>
              </details>
            {/if}
            <p style="margin: 4px 0;">{inotifyAuditData.verdict.reason}</p>
            {#if inotifyAuditData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.ino.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{inotifyAuditData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(inotifyAuditData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #41.1 zswap + zram (UI sprint 32) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.zsw.title")}</h4>
        <p class="muted">{i18n.t("integrations.zsw.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadZswapZram}>{i18n.t("integrations.zsw.refresh")}</button>
          {#if zswapZramData?.ok}
            <span class="kv" style="margin-left: 12px;"
                  style:color={['zswap_disabled_on_tight_box','legacy_compressor','pool_too_small'].includes(zswapZramData.verdict.verdict) ? 'var(--warn)' :
                             zswapZramData.verdict.verdict === 'zram_idle_when_useful' ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {i18n.t("integrations.zsw.verdict")} : <b>{zswapZramData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if zswapZramData?.ok}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        ['zswap_disabled_on_tight_box','legacy_compressor','pool_too_small'].includes(zswapZramData.verdict.verdict) ? 'var(--warn)' :
                        zswapZramData.verdict.verdict === 'zram_idle_when_useful' ? 'var(--accent)' :
                        'var(--text-dim)'};">
            <div class="form-row" style="gap: 12px; flex-wrap: wrap;">
              <span class="kv">enabled=<b>{zswapZramData.zswap.enabled ? 'yes' : zswapZramData.zswap.enabled === false ? 'no' : '?'}</b></span>
              <span class="kv">comp=<b>{zswapZramData.zswap.compressor ?? '?'}</b></span>
              <span class="kv">pool=<b>{zswapZramData.zswap.zpool ?? '?'}</b></span>
              <span class="kv">max_pool=<b>{zswapZramData.zswap.max_pool_percent ?? '?'}%</b></span>
              <span class="kv">RAM={zswapZramData.mem_total_gb !== null ? (zswapZramData.mem_total_gb).toFixed(0) + ' GB' : '?'}</span>
              <span class="kv">zram=<b>{zswapZramData.zram_devices.length}</b></span>
              <span class="kv">swap=<b>{zswapZramData.swap_devices.length}</b></span>
            </div>
            <p style="margin: 4px 0;">{zswapZramData.verdict.reason}</p>
            {#if zswapZramData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.zsw.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{zswapZramData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(zswapZramData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #42.4 CPU EPB (UI sprint 33) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.epb.title")}</h4>
        <p class="muted">{i18n.t("integrations.epb.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadCpuEpb}>{i18n.t("integrations.epb.refresh")}</button>
          {#if cpuEpbData?.ok}
            <span class="kv" style="margin-left: 12px;"
                  style:color={cpuEpbData.verdict.verdict === 'uniform_powersave' ? 'var(--warn)' :
                             cpuEpbData.verdict.verdict === 'mixed_across_cpus' ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {cpuEpbData.cpu_count} CPU · {cpuEpbData.epb_exposed_count} EPB · {i18n.t("integrations.epb.verdict")} : <b>{cpuEpbData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if cpuEpbData?.ok}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        cpuEpbData.verdict.verdict === 'uniform_powersave' ? 'var(--warn)' :
                        cpuEpbData.verdict.verdict === 'mixed_across_cpus' ? 'var(--accent)' :
                        'var(--text-dim)'};">
            <p style="margin: 4px 0;">{cpuEpbData.verdict.reason}</p>
            {#if cpuEpbData.epb_exposed_count > 0}
              <details style="margin-top: 4px;">
                <summary class="muted">Per-CPU EPB</summary>
                <div style="font-size: 0.85em; display: grid; grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); gap: 4px;">
                  {#each cpuEpbData.per_cpu as c}
                    {#if c.epb !== null}
                      <span>cpu{c.cpu}: {c.epb} ({c.label})</span>
                    {/if}
                  {/each}
                </div>
              </details>
            {/if}
            {#if cpuEpbData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.epb.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{cpuEpbData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(cpuEpbData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #42.3 cooling devices (UI sprint 33) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.cdev.title")}</h4>
        <p class="muted">{i18n.t("integrations.cdev.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadCoolingDevices}>{i18n.t("integrations.cdev.refresh")}</button>
          {#if coolingDevicesData?.ok}
            <span class="kv" style="margin-left: 12px;"
                  style:color={coolingDevicesData.verdict.verdict === 'saturated_cdev' ? 'var(--warn)' :
                             ['unbound_zone','no_cooling'].includes(coolingDevicesData.verdict.verdict) ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {coolingDevicesData.cooling_devices.length} cdev · {coolingDevicesData.thermal_zones.length} zone · {i18n.t("integrations.cdev.verdict")} : <b>{coolingDevicesData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if coolingDevicesData?.ok}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        coolingDevicesData.verdict.verdict === 'saturated_cdev' ? 'var(--warn)' :
                        ['unbound_zone','no_cooling'].includes(coolingDevicesData.verdict.verdict) ? 'var(--accent)' :
                        'var(--text-dim)'};">
            <p style="margin: 4px 0;">{coolingDevicesData.verdict.reason}</p>
            {#if coolingDevicesData.cooling_devices.length > 0}
              <details style="margin-top: 4px;">
                <summary class="muted">{coolingDevicesData.cooling_devices.length} cooling devices</summary>
                <ul style="font-size: 0.85em; margin: 4px 0; padding-left: 20px; max-height: 200px; overflow-y: auto;">
                  {#each coolingDevicesData.cooling_devices as cd}
                    <li>{cd.name} ({cd.type ?? '?'}) cur=<b
                      style:color={cd.cur_state !== null && cd.max_state !== null && cd.max_state > 0 && cd.cur_state >= cd.max_state ? 'var(--warn)' : 'inherit'}
                    >{cd.cur_state ?? '?'}</b>/<b>{cd.max_state ?? '?'}</b></li>
                  {/each}
                </ul>
              </details>
            {/if}
            {#if coolingDevicesData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.cdev.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{coolingDevicesData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(coolingDevicesData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #42.2 hybrid CPU topology (UI sprint 33) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.hcpu.title")}</h4>
        <p class="muted">{i18n.t("integrations.hcpu.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadHybridCpuTopo}>{i18n.t("integrations.hcpu.refresh")}</button>
          {#if hybridCpuTopoData?.ok}
            <span class="kv" style="margin-left: 12px;"
                  style:color={['p_e_hybrid','multi_ccd_or_multi_die','multi_cluster_uniform'].includes(hybridCpuTopoData.verdict.verdict) ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {hybridCpuTopoData.cpu_count} CPU · {i18n.t("integrations.hcpu.verdict")} : <b>{hybridCpuTopoData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if hybridCpuTopoData?.ok}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        ['p_e_hybrid','multi_ccd_or_multi_die','multi_cluster_uniform'].includes(hybridCpuTopoData.verdict.verdict) ? 'var(--accent)' :
                        'var(--text-dim)'};">
            <div class="form-row" style="gap: 12px; flex-wrap: wrap;">
              <span class="kv">packages=<b>{hybridCpuTopoData.packages.length}</b></span>
              <span class="kv">dies=<b>{hybridCpuTopoData.dies.length}</b></span>
              <span class="kv">clusters=<b>{hybridCpuTopoData.clusters.length}</b></span>
              {#if hybridCpuTopoData.freq_tiers_khz.length > 0}
                <span class="kv">tiers: {hybridCpuTopoData.freq_tiers_khz.map(f => (f/1000).toFixed(0) + ' MHz').join(' / ')}</span>
              {/if}
            </div>
            <p style="margin: 4px 0;">{hybridCpuTopoData.verdict.reason}</p>
            {#if hybridCpuTopoData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.hcpu.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{hybridCpuTopoData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(hybridCpuTopoData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #42.1 file locks (UI sprint 33) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.lock.title")}</h4>
        <p class="muted">{i18n.t("integrations.lock.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadFileLocksAudit}>{i18n.t("integrations.lock.refresh")}</button>
          {#if fileLocksAuditData?.ok}
            <span class="kv" style="margin-left: 12px;"
                  style:color={fileLocksAuditData.verdict.verdict === 'contention_on_model' ? 'var(--warn)' :
                             ['contention_general','orphan_lock'].includes(fileLocksAuditData.verdict.verdict) ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {fileLocksAuditData.lock_count} locks · {i18n.t("integrations.lock.verdict")} : <b>{fileLocksAuditData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if fileLocksAuditData?.ok}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        fileLocksAuditData.verdict.verdict === 'contention_on_model' ? 'var(--warn)' :
                        ['contention_general','orphan_lock'].includes(fileLocksAuditData.verdict.verdict) ? 'var(--accent)' :
                        'var(--text-dim)'};">
            <div class="form-row" style="gap: 12px; flex-wrap: wrap;">
              <span class="kv">conflicts=<b
                style:color={fileLocksAuditData.conflict_count > 0 ? 'var(--warn)' : 'inherit'}
              >{fileLocksAuditData.conflict_count}</b></span>
              <span class="kv">orphans=<b>{fileLocksAuditData.orphan_count}</b></span>
              <span class="kv">llm locks=<b>{fileLocksAuditData.llm_lock_count}</b></span>
            </div>
            {#if fileLocksAuditData.conflicts.length > 0}
              <details style="margin-top: 4px;" open>
                <summary class="muted">Conflicts</summary>
                <ul style="font-size: 0.85em; margin: 4px 0; padding-left: 20px;">
                  {#each fileLocksAuditData.conflicts as c}
                    <li>inode {c.inode_key.join(':')}{c.is_llm ? ' (LLM)' : ''}:
                      {#each c.writers as w}
                        <span> pid {w.pid}({w.comm ?? '?'})</span>
                      {/each}
                      {#if c.paths.length > 0}
                        <br/><code style="font-size: 0.85em;">{c.paths.join(', ')}</code>
                      {/if}
                    </li>
                  {/each}
                </ul>
              </details>
            {/if}
            {#if fileLocksAuditData.llm_locks.length > 0}
              <details style="margin-top: 4px;">
                <summary class="muted">{fileLocksAuditData.llm_locks.length} LLM-pattern lock(s)</summary>
                <ul style="font-size: 0.85em; margin: 4px 0; padding-left: 20px;">
                  {#each fileLocksAuditData.llm_locks as L}
                    <li>{L.comm ?? '?'}(pid {L.pid}) {L.access}: {L.path ?? '?'}</li>
                  {/each}
                </ul>
              </details>
            {/if}
            <p style="margin: 4px 0;">{fileLocksAuditData.verdict.reason}</p>
            {#if fileLocksAuditData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.lock.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{fileLocksAuditData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(fileLocksAuditData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #43.4 NIC ring-buffer drops (UI sprint 34) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.ring.title")}</h4>
        <p class="muted">{i18n.t("integrations.ring.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadNicRingAudit}>{i18n.t("integrations.ring.refresh")}</button>
          {#if nicRingAuditData?.ok}
            <span class="kv" style="margin-left: 12px;"
                  style:color={['fifo_overrun','rx_drops_climbing','cable_or_duplex'].includes(nicRingAuditData.verdict.verdict) ? 'var(--warn)' :
                             nicRingAuditData.verdict.verdict === 'tx_drops' ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {i18n.t("integrations.ring.verdict")} : <b>{nicRingAuditData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if nicRingAuditData?.ok}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        ['fifo_overrun','rx_drops_climbing','cable_or_duplex'].includes(nicRingAuditData.verdict.verdict) ? 'var(--warn)' :
                        nicRingAuditData.verdict.verdict === 'tx_drops' ? 'var(--accent)' :
                        'var(--text-dim)'};">
            <details style="margin-top: 4px;">
              <summary class="muted">Per-device counters</summary>
              <ul style="font-size: 0.85em; margin: 4px 0; padding-left: 20px;">
                {#each nicRingAuditData.devices as d}
                  <li>{d.dev} ({d.operstate}): rx={d.rx_packets ?? '?'}/{d.rx_dropped ?? 0} dropped, fifo={d.rx_fifo_errors ?? 0}+{d.rx_missed_errors ?? 0}, crc={d.rx_crc_errors ?? 0}</li>
                {/each}
              </ul>
            </details>
            <p style="margin: 4px 0;">{nicRingAuditData.verdict.reason}</p>
            {#if nicRingAuditData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.ring.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{nicRingAuditData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(nicRingAuditData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #43.1 IRQ rates (UI sprint 34) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.irq.title")}</h4>
        <p class="muted">{i18n.t("integrations.irq.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadIrqRatesAudit}>{i18n.t("integrations.irq.refresh")}</button>
          {#if irqRatesAuditData?.ok}
            <span class="kv" style="margin-left: 12px;"
                  style:color={irqRatesAuditData.verdict.verdict === 'cpu_pinned' ? 'var(--warn)' :
                             irqRatesAuditData.verdict.verdict === 'softirq_imbalance' ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {irqRatesAuditData.nonzero_irq_count} IRQs · {irqRatesAuditData.cpu_count} CPU · {i18n.t("integrations.irq.verdict")} : <b>{irqRatesAuditData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if irqRatesAuditData?.ok}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        irqRatesAuditData.verdict.verdict === 'cpu_pinned' ? 'var(--warn)' :
                        irqRatesAuditData.verdict.verdict === 'softirq_imbalance' ? 'var(--accent)' :
                        'var(--text-dim)'};">
            <details style="margin-top: 4px;">
              <summary class="muted">Top IRQs by total</summary>
              <ul style="font-size: 0.85em; margin: 4px 0; padding-left: 20px; max-height: 200px; overflow-y: auto;">
                {#each irqRatesAuditData.top_irqs.slice(0, 15) as r}
                  <li>IRQ {r.irq} ({r.device.substring(0, 50)}) total={r.total} hot=CPU{r.hot_cpu} ({(r.hot_share * 100).toFixed(0)}%)</li>
                {/each}
              </ul>
            </details>
            <p style="margin: 4px 0;">{irqRatesAuditData.verdict.reason}</p>
            {#if irqRatesAuditData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.irq.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{irqRatesAuditData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(irqRatesAuditData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #43.3 zoneinfo + vmstat (UI sprint 34) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.zi.title")}</h4>
        <p class="muted">{i18n.t("integrations.zi.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadZoneinfoAudit}>{i18n.t("integrations.zi.refresh")}</button>
          {#if zoneinfoAuditData?.ok}
            <span class="kv" style="margin-left: 12px;"
                  style:color={['direct_reclaim_active','compaction_failures'].includes(zoneinfoAuditData.verdict.verdict) ? 'var(--warn)' :
                             zoneinfoAuditData.verdict.verdict === 'zone_low' ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {zoneinfoAuditData.zone_count} zones · {i18n.t("integrations.zi.verdict")} : <b>{zoneinfoAuditData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if zoneinfoAuditData?.ok}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        ['direct_reclaim_active','compaction_failures'].includes(zoneinfoAuditData.verdict.verdict) ? 'var(--warn)' :
                        zoneinfoAuditData.verdict.verdict === 'zone_low' ? 'var(--accent)' :
                        'var(--text-dim)'};">
            <div class="form-row" style="gap: 12px; flex-wrap: wrap;">
              <span class="kv">pgsteal_kswapd=<b>{zoneinfoAuditData.vmstat.pgsteal_kswapd ?? '?'}</b></span>
              <span class="kv">pgsteal_direct=<b
                style:color={(zoneinfoAuditData.vmstat.pgsteal_direct ?? 0) > 0 ? 'var(--warn)' : 'inherit'}
              >{zoneinfoAuditData.vmstat.pgsteal_direct ?? 0}</b></span>
              <span class="kv">compact_ok=<b>{zoneinfoAuditData.vmstat.compact_success ?? 0}</b>/fail=<b>{zoneinfoAuditData.vmstat.compact_fail ?? 0}</b></span>
            </div>
            <details style="margin-top: 4px;">
              <summary class="muted">Zone watermarks</summary>
              <ul style="font-size: 0.85em; margin: 4px 0; padding-left: 20px;">
                {#each zoneinfoAuditData.zones as z}
                  <li>node {z.node}/{z.zone}: free=<b>{z.free ?? '?'}</b> low={z.low ?? '?'} high={z.high ?? '?'} managed={z.managed ?? '?'}</li>
                {/each}
              </ul>
            </details>
            <p style="margin: 4px 0;">{zoneinfoAuditData.verdict.reason}</p>
            {#if zoneinfoAuditData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.zi.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{zoneinfoAuditData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(zoneinfoAuditData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #43.2 block queue (UI sprint 34) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.bq.title")}</h4>
        <p class="muted">{i18n.t("integrations.bq.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadBlockQueueAudit}>{i18n.t("integrations.bq.refresh")}</button>
          {#if blockQueueAuditData?.ok}
            <span class="kv" style="margin-left: 12px;"
                  style:color={['rotational_misdetect','scheduler_mismatch'].includes(blockQueueAuditData.verdict.verdict) ? 'var(--warn)' :
                             ['readahead_too_low','wbt_throttling'].includes(blockQueueAuditData.verdict.verdict) ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {blockQueueAuditData.devices.length} dev · {i18n.t("integrations.bq.verdict")} : <b>{blockQueueAuditData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if blockQueueAuditData?.ok}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        ['rotational_misdetect','scheduler_mismatch'].includes(blockQueueAuditData.verdict.verdict) ? 'var(--warn)' :
                        ['readahead_too_low','wbt_throttling'].includes(blockQueueAuditData.verdict.verdict) ? 'var(--accent)' :
                        'var(--text-dim)'};">
            <details style="margin-top: 4px;">
              <summary class="muted">Per-device knobs</summary>
              <ul style="font-size: 0.85em; margin: 4px 0; padding-left: 20px;">
                {#each blockQueueAuditData.devices as d}
                  <li>{d.dev} ({d.model ?? '?'}): sched=<b>{d.scheduler ?? '?'}</b>, rot={d.rotational ?? '?'}, ra_kb={d.read_ahead_kb ?? '?'}, nr_req={d.nr_requests ?? '?'}, wbt={d.wbt_lat_usec ?? '?'} μs</li>
                {/each}
              </ul>
            </details>
            <p style="margin: 4px 0;">{blockQueueAuditData.verdict.reason}</p>
            {#if blockQueueAuditData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.bq.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{blockQueueAuditData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(blockQueueAuditData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #44.3 watchdog inventory (UI sprint 35) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.wd.title")}</h4>
        <p class="muted">{i18n.t("integrations.wd.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadWatchdogInventory}>{i18n.t("integrations.wd.refresh")}</button>
          {#if watchdogInventoryData?.ok}
            <span class="kv" style="margin-left: 12px;"
                  style:color={watchdogInventoryData.verdict.verdict === 'boot_due_to_watchdog' ? 'var(--warn)' :
                             ['no_watchdog','multiple_watchdogs'].includes(watchdogInventoryData.verdict.verdict) ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {watchdogInventoryData.devices.length} dev · {i18n.t("integrations.wd.verdict")} : <b>{watchdogInventoryData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if watchdogInventoryData?.ok}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        watchdogInventoryData.verdict.verdict === 'boot_due_to_watchdog' ? 'var(--warn)' :
                        ['no_watchdog','multiple_watchdogs'].includes(watchdogInventoryData.verdict.verdict) ? 'var(--accent)' :
                        'var(--text-dim)'};">
            {#if watchdogInventoryData.devices.length > 0}
              <ul style="font-size: 0.85em; margin: 4px 0; padding-left: 20px;">
                {#each watchdogInventoryData.devices as wd}
                  <li>{wd.name} ({wd.identity ?? '?'}): timeout={wd.timeout ?? '?'}s, bootstatus=0x{(wd.bootstatus ?? 0).toString(16)}{#if wd.bootstatus_bits.length > 0} ({wd.bootstatus_bits.map(b => b.key).join(', ')}){/if}</li>
                {/each}
              </ul>
            {/if}
            <p style="margin: 4px 0;">{watchdogInventoryData.verdict.reason}</p>
            {#if watchdogInventoryData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.wd.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{watchdogInventoryData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(watchdogInventoryData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #44.1 disk I/O latency (UI sprint 35) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.dio.title")}</h4>
        <p class="muted">{i18n.t("integrations.dio.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadDiskIoLatency}>{i18n.t("integrations.dio.refresh")}</button>
          {#if diskIoLatencyData?.ok}
            <span class="kv" style="margin-left: 12px;"
                  style:color={['queue_saturated','read_stall','write_stall'].includes(diskIoLatencyData.verdict.verdict) ? 'var(--warn)' :
                             'var(--text-dim)'}>
              {diskIoLatencyData.devices.length} dev · {i18n.t("integrations.dio.verdict")} : <b>{diskIoLatencyData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if diskIoLatencyData?.ok}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        ['queue_saturated','read_stall','write_stall'].includes(diskIoLatencyData.verdict.verdict) ? 'var(--warn)' :
                        'var(--text-dim)'};">
            <ul style="font-size: 0.85em; margin: 4px 0; padding-left: 20px;">
              {#each diskIoLatencyData.devices as d}
                <li>{d.dev}{d.rotational === 0 ? ' (SSD)' : d.rotational === 1 ? ' (HDD)' : ''}:
                  reads={d.reads_completed} avg_rwait=<b style:color={d.avg_read_wait_ms >= 100 ? 'var(--warn)' : 'inherit'}>{d.avg_read_wait_ms.toFixed(2)}ms</b>,
                  writes={d.writes_completed} avg_wwait=<b style:color={d.avg_write_wait_ms >= 500 ? 'var(--warn)' : 'inherit'}>{d.avg_write_wait_ms.toFixed(2)}ms</b>,
                  inflight={d.inflight_total}</li>
              {/each}
            </ul>
            <p style="margin: 4px 0;">{diskIoLatencyData.verdict.reason}</p>
            {#if diskIoLatencyData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.dio.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{diskIoLatencyData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(diskIoLatencyData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #44.4 net proto counters (UI sprint 35) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.npc.title")}</h4>
        <p class="muted">{i18n.t("integrations.npc.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadNetProtoCounters}>{i18n.t("integrations.npc.refresh")}</button>
          {#if netProtoCountersData?.ok}
            <span class="kv" style="margin-left: 12px;"
                  style:color={netProtoCountersData.verdict.verdict === 'listen_overflow' ? 'var(--warn)' :
                             ['rcvbuf_errors','high_retrans','tcp_memory_pressure','backlog_drops'].includes(netProtoCountersData.verdict.verdict) ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {i18n.t("integrations.npc.verdict")} : <b>{netProtoCountersData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if netProtoCountersData?.ok}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        netProtoCountersData.verdict.verdict === 'listen_overflow' ? 'var(--warn)' :
                        ['rcvbuf_errors','high_retrans','tcp_memory_pressure','backlog_drops'].includes(netProtoCountersData.verdict.verdict) ? 'var(--accent)' :
                        'var(--text-dim)'};">
            <div class="form-row" style="gap: 12px; flex-wrap: wrap;">
              <span class="kv">retrans=<b>{netProtoCountersData.headline.tcp_retrans ?? 0}</b>/{netProtoCountersData.headline.tcp_out_segs ?? 0}</span>
              <span class="kv">listen_overflows=<b
                style:color={(netProtoCountersData.headline.tcp_listen_overflows ?? 0) > 0 ? 'var(--warn)' : 'inherit'}
              >{netProtoCountersData.headline.tcp_listen_overflows ?? 0}</b></span>
              <span class="kv">udp_rcvbuf_err=<b
                style:color={(netProtoCountersData.headline.udp_rcvbuf_errors ?? 0) > 0 ? 'var(--warn)' : 'inherit'}
              >{netProtoCountersData.headline.udp_rcvbuf_errors ?? 0}</b></span>
              <span class="kv">tcp_mem_press=<b>{netProtoCountersData.headline.tcp_memory_pressures ?? 0}</b></span>
              <span class="kv">tcp_inuse=<b>{netProtoCountersData.sockstat?.TCP?.inuse ?? 0}</b> tw=<b>{netProtoCountersData.sockstat?.TCP?.tw ?? 0}</b></span>
            </div>
            <p style="margin: 4px 0;">{netProtoCountersData.verdict.reason}</p>
            {#if netProtoCountersData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.npc.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{netProtoCountersData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(netProtoCountersData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #44.2 slab audit (UI sprint 35) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.slab.title")}</h4>
        <p class="muted">{i18n.t("integrations.slab.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadSlabAudit}>{i18n.t("integrations.slab.refresh")}</button>
          {#if slabAuditData?.ok}
            <span class="kv" style="margin-left: 12px;"
                  style:color={slabAuditData.verdict.verdict === 'leak_suspect' ? 'var(--warn)' :
                             ['fragmented','requires_root'].includes(slabAuditData.verdict.verdict) ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {slabAuditData.cache_count} caches · {i18n.t("integrations.slab.verdict")} : <b>{slabAuditData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if slabAuditData?.ok}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        slabAuditData.verdict.verdict === 'leak_suspect' ? 'var(--warn)' :
                        ['fragmented','requires_root'].includes(slabAuditData.verdict.verdict) ? 'var(--accent)' :
                        'var(--text-dim)'};">
            {#if slabAuditData.top_caches.length > 0}
              <details style="margin-top: 4px;">
                <summary class="muted">Top caches by resident_kb</summary>
                <ul style="font-size: 0.85em; margin: 4px 0; padding-left: 20px; max-height: 200px; overflow-y: auto;">
                  {#each slabAuditData.top_caches.slice(0, 15) as c}
                    <li>{c.name}: {c.resident_kb ?? 0} KB resident{c.objects !== undefined ? `, ${c.objects} objects` : ''}{c.slabs && c.partial !== undefined ? `, ${(c.partial / c.slabs * 100).toFixed(0)}% partial` : ''}</li>
                  {/each}
                </ul>
              </details>
            {/if}
            <p style="margin: 4px 0;">{slabAuditData.verdict.reason}</p>
            {#if slabAuditData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.slab.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{slabAuditData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(slabAuditData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #45.4 entropy + hwrng (UI sprint 36) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.ent.title")}</h4>
        <p class="muted">{i18n.t("integrations.ent.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadEntropyAudit}>{i18n.t("integrations.ent.refresh")}</button>
          {#if entropyAuditData?.ok}
            <span class="kv" style="margin-left: 12px;"
                  style:color={['no_hwrng','low_entropy'].includes(entropyAuditData.verdict.verdict) ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {i18n.t("integrations.ent.verdict")} : <b>{entropyAuditData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if entropyAuditData?.ok}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        ['no_hwrng','low_entropy'].includes(entropyAuditData.verdict.verdict) ? 'var(--accent)' :
                        'var(--text-dim)'};">
            <div class="form-row" style="gap: 12px; flex-wrap: wrap;">
              <span class="kv">entropy_avail=<b>{entropyAuditData.random.entropy_avail ?? '?'}</b></span>
              <span class="kv">poolsize=<b>{entropyAuditData.random.poolsize ?? '?'}</b></span>
              <span class="kv">hwrng=<b>{entropyAuditData.hwrng.current ?? '(none)'}</b></span>
              {#if entropyAuditData.hwrng.available_list && entropyAuditData.hwrng.available_list.length > 0}
                <span class="kv">available: {entropyAuditData.hwrng.available_list.join(', ')}</span>
              {/if}
            </div>
            <p style="margin: 4px 0;">{entropyAuditData.verdict.reason}</p>
            {#if entropyAuditData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.ent.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{entropyAuditData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(entropyAuditData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #45.1 conntrack (UI sprint 36) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.ct.title")}</h4>
        <p class="muted">{i18n.t("integrations.ct.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadNfConntrackAudit}>{i18n.t("integrations.ct.refresh")}</button>
          {#if nfConntrackAuditData?.ok}
            <span class="kv" style="margin-left: 12px;"
                  style:color={['insert_drops','table_saturated'].includes(nfConntrackAuditData.verdict.verdict) ? 'var(--warn)' :
                             nfConntrackAuditData.verdict.verdict === 'time_wait_bloat' ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {i18n.t("integrations.ct.verdict")} : <b>{nfConntrackAuditData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if nfConntrackAuditData?.ok}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        ['insert_drops','table_saturated'].includes(nfConntrackAuditData.verdict.verdict) ? 'var(--warn)' :
                        nfConntrackAuditData.verdict.verdict === 'time_wait_bloat' ? 'var(--accent)' :
                        'var(--text-dim)'};">
            <div class="form-row" style="gap: 12px; flex-wrap: wrap;">
              <span class="kv">count=<b>{nfConntrackAuditData.sysctls.nf_conntrack_count ?? '?'}</b>/{nfConntrackAuditData.sysctls.nf_conntrack_max ?? '?'}</span>
              <span class="kv">tw_timeout=<b>{nfConntrackAuditData.sysctls.nf_conntrack_tcp_timeout_time_wait ?? '?'}s</b></span>
              {#if nfConntrackAuditData.stats.insert_failed !== undefined}
                <span class="kv">insert_failed=<b>{nfConntrackAuditData.stats.insert_failed}</b></span>
              {/if}
            </div>
            <p style="margin: 4px 0;">{nfConntrackAuditData.verdict.reason}</p>
            {#if nfConntrackAuditData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.ct.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{nfConntrackAuditData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(nfConntrackAuditData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #45.3 SysV IPC (UI sprint 36) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.ipc.title")}</h4>
        <p class="muted">{i18n.t("integrations.ipc.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadSysvipcAudit}>{i18n.t("integrations.ipc.refresh")}</button>
          {#if sysvipcAuditData?.ok}
            <span class="kv" style="margin-left: 12px;"
                  style:color={['stale_shm','sem_exhaustion','msg_queue_backlog'].includes(sysvipcAuditData.verdict.verdict) ? 'var(--warn)' :
                             'var(--text-dim)'}>
              {sysvipcAuditData.shm_count ?? 0} shm · {sysvipcAuditData.sem_count ?? 0} sem · {sysvipcAuditData.msg_count ?? 0} msg · {i18n.t("integrations.ipc.verdict")} : <b>{sysvipcAuditData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if sysvipcAuditData?.ok}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        ['stale_shm','sem_exhaustion','msg_queue_backlog'].includes(sysvipcAuditData.verdict.verdict) ? 'var(--warn)' :
                        'var(--text-dim)'};">
            {#if sysvipcAuditData.shm_total_bytes !== undefined}
              <p class="muted">shm total : {(sysvipcAuditData.shm_total_bytes / (1024*1024)).toFixed(1)} MB</p>
            {/if}
            <p style="margin: 4px 0;">{sysvipcAuditData.verdict.reason}</p>
            {#if sysvipcAuditData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.ipc.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{sysvipcAuditData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(sysvipcAuditData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #45.2 mdraid (UI sprint 36) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.md.title")}</h4>
        <p class="muted">{i18n.t("integrations.md.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadMdraidHealth}>{i18n.t("integrations.md.refresh")}</button>
          {#if mdraidHealthData?.ok}
            <span class="kv" style="margin-left: 12px;"
                  style:color={['degraded','mismatch_present'].includes(mdraidHealthData.verdict.verdict) ? 'var(--warn)' :
                             mdraidHealthData.verdict.verdict === 'resyncing' ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {mdraidHealthData.array_count ?? 0} arrays · {i18n.t("integrations.md.verdict")} : <b>{mdraidHealthData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if mdraidHealthData?.ok}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        ['degraded','mismatch_present'].includes(mdraidHealthData.verdict.verdict) ? 'var(--warn)' :
                        mdraidHealthData.verdict.verdict === 'resyncing' ? 'var(--accent)' :
                        'var(--text-dim)'};">
            {#if mdraidHealthData.arrays.length > 0}
              <ul style="font-size: 0.85em; margin: 4px 0; padding-left: 20px;">
                {#each mdraidHealthData.arrays as a}
                  <li>{a.name} ({a.level}): [{a.marker}], state={a.state}{a.resync ? ', resync active' : ''}</li>
                {/each}
              </ul>
            {/if}
            <p style="margin: 4px 0;">{mdraidHealthData.verdict.reason}</p>
            {#if mdraidHealthData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.md.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{mdraidHealthData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(mdraidHealthData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #46.4 keyring audit (UI sprint 37) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.kr.title")}</h4>
        <p class="muted">{i18n.t("integrations.kr.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadKeyringAudit}>{i18n.t("integrations.kr.refresh")}</button>
          {#if keyringAuditData?.ok}
            <span class="kv" style="margin-left: 12px;"
                  style:color={keyringAuditData.verdict.verdict === 'uid_quota_approaching' ? 'var(--warn)' :
                             keyringAuditData.verdict.verdict === 'many_session_keyrings' ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {keyringAuditData.user_count ?? 0} UIDs · {i18n.t("integrations.kr.verdict")} : <b>{keyringAuditData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if keyringAuditData?.ok}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        keyringAuditData.verdict.verdict === 'uid_quota_approaching' ? 'var(--warn)' :
                        keyringAuditData.verdict.verdict === 'many_session_keyrings' ? 'var(--accent)' :
                        'var(--text-dim)'};">
            <details style="margin-top: 4px;">
              <summary class="muted">Per-UID quota</summary>
              <ul style="font-size: 0.85em; margin: 4px 0; padding-left: 20px;">
                {#each keyringAuditData.users as u}
                  <li>uid {u.uid}: keys={u.keys}/{u.maxkeys}, bytes={u.bytes}/{u.maxbytes}</li>
                {/each}
              </ul>
            </details>
            <p style="margin: 4px 0;">{keyringAuditData.verdict.reason}</p>
            {#if keyringAuditData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.kr.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{keyringAuditData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(keyringAuditData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #46.2 security posture (UI sprint 37) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.sec.title")}</h4>
        <p class="muted">{i18n.t("integrations.sec.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadSecurityPosture}>{i18n.t("integrations.sec.refresh")}</button>
          {#if securityPostureData?.ok}
            <span class="kv" style="margin-left: 12px;"
                  style:color={securityPostureData.verdict.verdict === 'paranoid_too_loose' ? 'var(--warn)' :
                             securityPostureData.verdict.verdict === 'lockdown_confined' ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {i18n.t("integrations.sec.verdict")} : <b>{securityPostureData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if securityPostureData?.ok}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        securityPostureData.verdict.verdict === 'paranoid_too_loose' ? 'var(--warn)' :
                        securityPostureData.verdict.verdict === 'lockdown_confined' ? 'var(--accent)' :
                        'var(--text-dim)'};">
            <div class="form-row" style="gap: 12px; flex-wrap: wrap;">
              <span class="kv">LSMs: {securityPostureData.security.lsm?.join(', ') ?? '?'}</span>
              <span class="kv">lockdown=<b>{securityPostureData.security.lockdown ?? '?'}</b></span>
              <span class="kv">ptrace_scope=<b>{securityPostureData.sysctls.ptrace_scope ?? '?'}</b></span>
              <span class="kv">perf_paranoid=<b>{securityPostureData.sysctls.perf_event_paranoid ?? '?'}</b></span>
              <span class="kv">kptr=<b>{securityPostureData.sysctls.kptr_restrict ?? '?'}</b></span>
            </div>
            <p style="margin: 4px 0;">{securityPostureData.verdict.reason}</p>
            {#if securityPostureData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.sec.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{securityPostureData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(securityPostureData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #46.3 VFS limits (UI sprint 37) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.vfs.title")}</h4>
        <p class="muted">{i18n.t("integrations.vfs.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadVfsLimitsAudit}>{i18n.t("integrations.vfs.refresh")}</button>
          {#if vfsLimitsAuditData?.ok}
            <span class="kv" style="margin-left: 12px;"
                  style:color={['file_nr_high','aio_nr_high'].includes(vfsLimitsAuditData.verdict.verdict) ? 'var(--warn)' :
                             'var(--text-dim)'}>
              {i18n.t("integrations.vfs.verdict")} : <b>{vfsLimitsAuditData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if vfsLimitsAuditData?.ok}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        ['file_nr_high','aio_nr_high'].includes(vfsLimitsAuditData.verdict.verdict) ? 'var(--warn)' :
                        'var(--text-dim)'};">
            <div class="form-row" style="gap: 12px; flex-wrap: wrap;">
              {#if vfsLimitsAuditData.limits.file_nr}
                <span class="kv">file-nr: <b>{vfsLimitsAuditData.limits.file_nr.allocated}</b> allocated</span>
              {/if}
              <span class="kv">nr_open=<b>{vfsLimitsAuditData.limits.nr_open ?? '?'}</b></span>
              <span class="kv">aio=<b>{vfsLimitsAuditData.limits.aio_nr ?? 0}</b>/{vfsLimitsAuditData.limits.aio_max_nr ?? '?'}</span>
              <span class="kv">pipe_max=<b>{vfsLimitsAuditData.limits.pipe_max_size ?? '?'}</b> B</span>
            </div>
            <p style="margin: 4px 0;">{vfsLimitsAuditData.verdict.reason}</p>
            {#if vfsLimitsAuditData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.vfs.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{vfsLimitsAuditData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(vfsLimitsAuditData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #47.3 nvidia RM (UI sprint 38) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.nv.title")}</h4>
        <p class="muted">{i18n.t("integrations.nv.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadNvidiaRmAudit}>{i18n.t("integrations.nv.refresh")}</button>
          {#if nvidiaRmAuditData?.ok || nvidiaRmAuditData?.driver_present === false}
            <span class="kv" style="margin-left: 12px;"
                  style:color={['driver_kmod_mismatch','caps_missing'].includes(nvidiaRmAuditData.verdict.verdict) ? 'var(--warn)' :
                             nvidiaRmAuditData.verdict.verdict === 'no_nvidia_driver' ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {i18n.t("integrations.nv.verdict")} : <b>{nvidiaRmAuditData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if nvidiaRmAuditData}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        ['driver_kmod_mismatch','caps_missing'].includes(nvidiaRmAuditData.verdict.verdict) ? 'var(--warn)' :
                        nvidiaRmAuditData.verdict.verdict === 'no_nvidia_driver' ? 'var(--accent)' :
                        'var(--text-dim)'};">
            {#if nvidiaRmAuditData.driver_present}
              <div class="form-row" style="gap: 12px; flex-wrap: wrap;">
                <span class="kv">version_proc=<b>{nvidiaRmAuditData.version_proc ?? '?'}</b></span>
                {#if nvidiaRmAuditData.version_smi}
                  <span class="kv">version_smi=<b>{nvidiaRmAuditData.version_smi}</b></span>
                {/if}
                <span class="kv">caps={nvidiaRmAuditData.capability_count}</span>
              </div>
              {#if nvidiaRmAuditData.capabilities.length > 0}
                <details style="margin-top: 4px;">
                  <summary class="muted">Capability paths</summary>
                  <ul style="font-size: 0.85em; margin: 4px 0; padding-left: 20px;">
                    {#each nvidiaRmAuditData.capabilities as cap}
                      <li>{cap}</li>
                    {/each}
                  </ul>
                </details>
              {/if}
            {/if}
            <p style="margin: 4px 0;">{nvidiaRmAuditData.verdict.reason}</p>
            {#if nvidiaRmAuditData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.nv.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{nvidiaRmAuditData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(nvidiaRmAuditData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #47.4 MCE audit (UI sprint 38) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.mce.title")}</h4>
        <p class="muted">{i18n.t("integrations.mce.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadMceAudit}>{i18n.t("integrations.mce.refresh")}</button>
          {#if mceAuditData?.ok}
            <span class="kv" style="margin-left: 12px;"
                  style:color={['ignore_ce_masked','tolerant_too_high'].includes(mceAuditData.verdict.verdict) ? 'var(--warn)' :
                             ['cmci_disabled_intel','bank_silenced'].includes(mceAuditData.verdict.verdict) ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {mceAuditData.cpu_count} CPU · {i18n.t("integrations.mce.verdict")} : <b>{mceAuditData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if mceAuditData?.ok}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        ['ignore_ce_masked','tolerant_too_high'].includes(mceAuditData.verdict.verdict) ? 'var(--warn)' :
                        ['cmci_disabled_intel','bank_silenced'].includes(mceAuditData.verdict.verdict) ? 'var(--accent)' :
                        'var(--text-dim)'};">
            <div class="form-row" style="gap: 12px; flex-wrap: wrap;">
              <span class="kv">check_interval=<b>{mceAuditData.cpu0_state.check_interval ?? '?'}s</b></span>
              <span class="kv">ignore_ce=<b>{mceAuditData.cpu0_state.ignore_ce ?? '?'}</b></span>
              <span class="kv">cmci_disabled=<b>{mceAuditData.cpu0_state.cmci_disabled ?? '?'}</b></span>
              {#if mceAuditData.cpu0_state.tolerant !== undefined}
                <span class="kv">tolerant=<b>{mceAuditData.cpu0_state.tolerant}</b></span>
              {/if}
              <span class="kv">banks={mceAuditData.cpu0_state.banks ? Object.keys(mceAuditData.cpu0_state.banks).length : 0}</span>
            </div>
            <p style="margin: 4px 0;">{mceAuditData.verdict.reason}</p>
            {#if mceAuditData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.mce.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{mceAuditData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(mceAuditData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #47.2 ACPI audit (UI sprint 38) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.acpi.title")}</h4>
        <p class="muted">{i18n.t("integrations.acpi.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadAcpiAudit}>{i18n.t("integrations.acpi.refresh")}</button>
          {#if acpiAuditData?.ok}
            <span class="kv" style="margin-left: 12px;"
                  style:color={['gpe_storm','pcie_root_wakeup','quiet_profile_on_workstation'].includes(acpiAuditData.verdict.verdict) ? 'var(--warn)' :
                             'var(--text-dim)'}>
              {i18n.t("integrations.acpi.verdict")} : <b>{acpiAuditData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if acpiAuditData?.ok}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        ['gpe_storm','pcie_root_wakeup','quiet_profile_on_workstation'].includes(acpiAuditData.verdict.verdict) ? 'var(--warn)' :
                        'var(--text-dim)'};">
            <div class="form-row" style="gap: 12px; flex-wrap: wrap;">
              <span class="kv">profile=<b>{acpiAuditData.platform_profile.current ?? 'absent'}</b></span>
              <span class="kv">pm_profile=<b>{acpiAuditData.platform_profile.pm_profile ?? '?'}</b></span>
              <span class="kv">wakeups={acpiAuditData.wakeup_count} (enabled={acpiAuditData.wakeups_enabled.length})</span>
              <span class="kv">gpes={acpiAuditData.gpe_count}</span>
            </div>
            {#if acpiAuditData.top_gpes.length > 0}
              <details style="margin-top: 4px;">
                <summary class="muted">Top GPEs by count</summary>
                <ul style="font-size: 0.85em; margin: 4px 0; padding-left: 20px;">
                  {#each acpiAuditData.top_gpes.slice(0, 5) as g}
                    <li>{g.name}: count={g.count}, flag={g.flag}</li>
                  {/each}
                </ul>
              </details>
            {/if}
            <p style="margin: 4px 0;">{acpiAuditData.verdict.reason}</p>
            {#if acpiAuditData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.acpi.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{acpiAuditData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(acpiAuditData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #47.1 sched audit (UI sprint 38) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.sched.title")}</h4>
        <p class="muted">{i18n.t("integrations.sched.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadSchedAudit}>{i18n.t("integrations.sched.refresh")}</button>
          {#if schedAuditData?.ok}
            <span class="kv" style="margin-left: 12px;"
                  style:color={schedAuditData.verdict.verdict === 'runqueue_wait_pileup' ? 'var(--warn)' :
                             schedAuditData.verdict.verdict === 'sched_feat_hostile' ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {schedAuditData.cpu_count} CPU · {i18n.t("integrations.sched.verdict")} : <b>{schedAuditData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if schedAuditData?.ok}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        schedAuditData.verdict.verdict === 'runqueue_wait_pileup' ? 'var(--warn)' :
                        schedAuditData.verdict.verdict === 'sched_feat_hostile' ? 'var(--accent)' :
                        'var(--text-dim)'};">
            <div class="form-row" style="gap: 12px; flex-wrap: wrap;">
              <span class="kv">schedstat v={schedAuditData.schedstat_version ?? '?'}</span>
              <span class="kv">features_readable=<b>{schedAuditData.features_readable}</b></span>
            </div>
            <details style="margin-top: 4px;">
              <summary class="muted">Top CPUs by avg runqueue-wait</summary>
              <ul style="font-size: 0.85em; margin: 4px 0; padding-left: 20px;">
                {#each schedAuditData.top_cpus_by_wait.slice(0, 8) as c}
                  <li>cpu{c.cpu}: avg_wait=<b
                    style:color={c.avg_wait_ns >= 100000 ? 'var(--warn)' : 'inherit'}
                  >{(c.avg_wait_ns / 1000).toFixed(1)} µs</b> over {c.pcount} slices</li>
                {/each}
              </ul>
            </details>
            <p style="margin: 4px 0;">{schedAuditData.verdict.reason}</p>
            {#if schedAuditData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.sched.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{schedAuditData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(schedAuditData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #48.3 DMA + SWIOTLB (UI sprint 39) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.dma.title")}</h4>
        <p class="muted">{i18n.t("integrations.dma.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadDmaAudit}>{i18n.t("integrations.dma.refresh")}</button>
          {#if dmaAuditData?.ok}
            <span class="kv" style="margin-left: 12px;"
                  style:color={dmaAuditData.verdict.verdict === 'swiotlb_bounce_high' ? 'var(--warn)' :
                             'var(--text-dim)'}>
              {dmaAuditData.dma_engines.length} engines · {i18n.t("integrations.dma.verdict")} : <b>{dmaAuditData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if dmaAuditData?.ok}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        dmaAuditData.verdict.verdict === 'swiotlb_bounce_high' ? 'var(--warn)' :
                        'var(--text-dim)'};">
            {#if dmaAuditData.swiotlb?.available}
              <div class="form-row" style="gap: 12px; flex-wrap: wrap;">
                <span class="kv">swiotlb_nslabs={dmaAuditData.swiotlb.io_tlb_nslabs ?? '?'}</span>
                <span class="kv">swiotlb_used={dmaAuditData.swiotlb.io_tlb_used ?? 0}</span>
                {#if dmaAuditData.swiotlb.used_ratio !== undefined}
                  <span class="kv">used={(dmaAuditData.swiotlb.used_ratio * 100).toFixed(1)}%</span>
                {/if}
              </div>
            {/if}
            <p style="margin: 4px 0;">{dmaAuditData.verdict.reason}</p>
            {#if dmaAuditData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.dma.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{dmaAuditData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(dmaAuditData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #48.1 ftrace audit (UI sprint 39) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.ftr.title")}</h4>
        <p class="muted">{i18n.t("integrations.ftr.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadFtraceAudit}>{i18n.t("integrations.ftr.refresh")}</button>
          {#if ftraceAuditData?.ok || ftraceAuditData?.requires_root}
            <span class="kv" style="margin-left: 12px;"
                  style:color={ftraceAuditData.verdict.verdict === 'tracer_left_on' ? 'var(--warn)' :
                             ['orphan_kprobes','orphan_uprobes','events_enabled','requires_root'].includes(ftraceAuditData.verdict.verdict) ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {i18n.t("integrations.ftr.verdict")} : <b>{ftraceAuditData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if ftraceAuditData}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        ftraceAuditData.verdict.verdict === 'tracer_left_on' ? 'var(--warn)' :
                        ['orphan_kprobes','orphan_uprobes','events_enabled','requires_root'].includes(ftraceAuditData.verdict.verdict) ? 'var(--accent)' :
                        'var(--text-dim)'};">
            {#if ftraceAuditData.state?.current_tracer}
              <div class="form-row" style="gap: 12px; flex-wrap: wrap;">
                <span class="kv">current_tracer=<b>{ftraceAuditData.state.current_tracer}</b></span>
                <span class="kv">tracing_on=<b>{ftraceAuditData.state.tracing_on ?? '?'}</b></span>
                {#if ftraceAuditData.state.kprobe_events}
                  <span class="kv">kprobes=<b>{ftraceAuditData.state.kprobe_events.length}</b></span>
                {/if}
              </div>
            {/if}
            <p style="margin: 4px 0;">{ftraceAuditData.verdict.reason}</p>
            {#if ftraceAuditData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.ftr.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{ftraceAuditData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(ftraceAuditData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #48.2 USB topology (UI sprint 39) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.usb.title")}</h4>
        <p class="muted">{i18n.t("integrations.usb.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadUsbTopologyAudit}>{i18n.t("integrations.usb.refresh")}</button>
          {#if usbTopologyAuditData?.ok}
            <span class="kv" style="margin-left: 12px;"
                  style:color={usbTopologyAuditData.verdict.verdict === 'power_budget_high' ? 'var(--warn)' :
                             ['speed_negotiated_low','autosuspend_unfriendly'].includes(usbTopologyAuditData.verdict.verdict) ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {usbTopologyAuditData.non_root_count ?? 0} dev · {usbTopologyAuditData.total_power_ma ?? 0} mA · {i18n.t("integrations.usb.verdict")} : <b>{usbTopologyAuditData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if usbTopologyAuditData?.ok}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        usbTopologyAuditData.verdict.verdict === 'power_budget_high' ? 'var(--warn)' :
                        ['speed_negotiated_low','autosuspend_unfriendly'].includes(usbTopologyAuditData.verdict.verdict) ? 'var(--accent)' :
                        'var(--text-dim)'};">
            {#if usbTopologyAuditData.devices.length > 0}
              <details style="margin-top: 4px;">
                <summary class="muted">Non-root-hub devices</summary>
                <ul style="font-size: 0.85em; margin: 4px 0; padding-left: 20px;">
                  {#each usbTopologyAuditData.devices.filter(d => !d.is_root_hub).slice(0, 10) as d}
                    <li>{d.product ?? d.name}: {d.idVendor ?? '?'}:{d.idProduct ?? '?'}, speed={d.speed_mbps ?? '?'}Mbps, power={d.bMaxPower_mA ?? 0}mA</li>
                  {/each}
                </ul>
              </details>
            {/if}
            <p style="margin: 4px 0;">{usbTopologyAuditData.verdict.reason}</p>
            {#if usbTopologyAuditData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.usb.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{usbTopologyAuditData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(usbTopologyAuditData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #48.4 journal audit (UI sprint 39) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.jr.title")}</h4>
        <p class="muted">{i18n.t("integrations.jr.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadJournalAudit}>{i18n.t("integrations.jr.refresh")}</button>
          {#if journalAuditData?.ok}
            <span class="kv" style="margin-left: 12px;"
                  style:color={['storage_disabled','rate_limit_risky','oversized'].includes(journalAuditData.verdict.verdict) ? 'var(--warn)' :
                             journalAuditData.verdict.verdict === 'no_persistent_storage' ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {journalAuditData.journal_gib} GiB · {i18n.t("integrations.jr.verdict")} : <b>{journalAuditData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if journalAuditData?.ok}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        ['storage_disabled','rate_limit_risky','oversized'].includes(journalAuditData.verdict.verdict) ? 'var(--warn)' :
                        journalAuditData.verdict.verdict === 'no_persistent_storage' ? 'var(--accent)' :
                        'var(--text-dim)'};">
            <div class="form-row" style="gap: 12px; flex-wrap: wrap;">
              <span class="kv">Storage=<b>{journalAuditData.config.Storage ?? 'auto'}</b></span>
              <span class="kv">SystemMaxUse=<b>{journalAuditData.config.SystemMaxUse ?? 'unset'}</b></span>
              <span class="kv">RateLimitBurst=<b>{journalAuditData.config.RateLimitBurst ?? 'default'}</b></span>
              <span class="kv">size=<b>{journalAuditData.journal_gib} GiB</b></span>
            </div>
            <p style="margin: 4px 0;">{journalAuditData.verdict.reason}</p>
            {#if journalAuditData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.jr.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{journalAuditData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(journalAuditData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #49.4 RTC clock (UI sprint 40) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.rtc.title")}</h4>
        <p class="muted">{i18n.t("integrations.rtc.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadRtcClockAudit}>{i18n.t("integrations.rtc.refresh")}</button>
          {#if rtcClockAuditData?.ok}
            <span class="kv" style="margin-left: 12px;"
                  style:color={rtcClockAuditData.verdict.verdict === 'rtc_drift_high' ? 'var(--warn)' :
                             rtcClockAuditData.verdict.verdict === 'hctosys_disabled' ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {rtcClockAuditData.rtc_count ?? 0} RTC · {i18n.t("integrations.rtc.verdict")} : <b>{rtcClockAuditData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if rtcClockAuditData?.ok}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        rtcClockAuditData.verdict.verdict === 'rtc_drift_high' ? 'var(--warn)' :
                        rtcClockAuditData.verdict.verdict === 'hctosys_disabled' ? 'var(--accent)' :
                        'var(--text-dim)'};">
            {#each rtcClockAuditData.rtcs as r}
              <div class="form-row" style="gap: 12px; flex-wrap: wrap;">
                <span class="kv">{r.name} ({r.rtc_name ?? '?'})</span>
                <span class="kv">since_epoch={r.since_epoch ?? '?'}</span>
                <span class="kv">hctosys=<b>{r.hctosys ?? '?'}</b></span>
              </div>
            {/each}
            <p style="margin: 4px 0;">{rtcClockAuditData.verdict.reason}</p>
            {#if rtcClockAuditData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.rtc.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{rtcClockAuditData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(rtcClockAuditData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #49.2 TPM (UI sprint 40) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.tpm.title")}</h4>
        <p class="muted">{i18n.t("integrations.tpm.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadTpmAudit}>{i18n.t("integrations.tpm.refresh")}</button>
          {#if tpmAuditData?.ok}
            <span class="kv" style="margin-left: 12px;"
                  style:color={tpmAuditData.verdict.verdict === 'tpm1_legacy' ? 'var(--warn)' :
                             tpmAuditData.verdict.verdict === 'measured_boot_missing' ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {tpmAuditData.tpm_count ?? 0} TPM · {i18n.t("integrations.tpm.verdict")} : <b>{tpmAuditData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if tpmAuditData?.ok}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        tpmAuditData.verdict.verdict === 'tpm1_legacy' ? 'var(--warn)' :
                        tpmAuditData.verdict.verdict === 'measured_boot_missing' ? 'var(--accent)' :
                        'var(--text-dim)'};">
            {#if tpmAuditData.tpms.length > 0}
              <ul style="font-size: 0.85em; margin: 4px 0; padding-left: 20px;">
                {#each tpmAuditData.tpms as t}
                  <li>{t.name}: TPM v<b>{t.tpm_version_major ?? '?'}</b>, locality={t.active_locality ?? '?'}</li>
                {/each}
              </ul>
            {/if}
            <div class="form-row" style="gap: 12px; flex-wrap: wrap;">
              <span class="kv">measured_boot=<b>{tpmAuditData.measured_boot.available ? `${tpmAuditData.measured_boot.size_bytes} B` : 'absent'}</b></span>
            </div>
            <p style="margin: 4px 0;">{tpmAuditData.verdict.reason}</p>
            {#if tpmAuditData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.tpm.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{tpmAuditData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(tpmAuditData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #49.3 WMI + vendor (UI sprint 40) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.wmi.title")}</h4>
        <p class="muted">{i18n.t("integrations.wmi.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadWmiVendorAudit}>{i18n.t("integrations.wmi.refresh")}</button>
          {#if wmiVendorAuditData?.ok}
            <span class="kv" style="margin-left: 12px;"
                  style:color={wmiVendorAuditData.verdict.verdict === 'battery_threshold_unset' ? 'var(--warn)' :
                             wmiVendorAuditData.verdict.verdict === 'vendor_driver_active' ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {wmiVendorAuditData.wmi_guid_count} WMI · {wmiVendorAuditData.vendor_drivers.length} vendor · {i18n.t("integrations.wmi.verdict")} : <b>{wmiVendorAuditData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if wmiVendorAuditData?.ok}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        wmiVendorAuditData.verdict.verdict === 'battery_threshold_unset' ? 'var(--warn)' :
                        wmiVendorAuditData.verdict.verdict === 'vendor_driver_active' ? 'var(--accent)' :
                        'var(--text-dim)'};">
            {#if wmiVendorAuditData.vendor_drivers.length > 0}
              <ul style="font-size: 0.85em; margin: 4px 0; padding-left: 20px;">
                {#each wmiVendorAuditData.vendor_drivers as vd}
                  <li>{vd.name}: charge=<b>{vd.charge_control_start_threshold ?? '?'}/{vd.charge_control_end_threshold ?? '?'}</b>, fan_mode={vd.fan_mode ?? '?'}</li>
                {/each}
              </ul>
            {/if}
            <p style="margin: 4px 0;">{wmiVendorAuditData.verdict.reason}</p>
            {#if wmiVendorAuditData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.wmi.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{wmiVendorAuditData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(wmiVendorAuditData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #49.1 kmsg (UI sprint 40) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.kmsg.title")}</h4>
        <p class="muted">{i18n.t("integrations.kmsg.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadKmsgAudit}>{i18n.t("integrations.kmsg.refresh")}</button>
          {#if kmsgAuditData?.ok}
            <span class="kv" style="margin-left: 12px;"
                  style:color={['ratelimit_drops','loud_kernel'].includes(kmsgAuditData.verdict.verdict) ? 'var(--warn)' :
                             kmsgAuditData.verdict.verdict === 'requires_root' ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {i18n.t("integrations.kmsg.verdict")} : <b>{kmsgAuditData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if kmsgAuditData?.ok}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        ['ratelimit_drops','loud_kernel'].includes(kmsgAuditData.verdict.verdict) ? 'var(--warn)' :
                        kmsgAuditData.verdict.verdict === 'requires_root' ? 'var(--accent)' :
                        'var(--text-dim)'};">
            <div class="form-row" style="gap: 12px; flex-wrap: wrap;">
              <span class="kv">console_loglevel=<b>{kmsgAuditData.printk.console_loglevel ?? '?'}</b></span>
              <span class="kv">ratelimit_burst=<b>{kmsgAuditData.printk_ratelimit_burst ?? '?'}</b></span>
              <span class="kv">dmesg_restrict=<b>{kmsgAuditData.dmesg_restrict ?? '?'}</b></span>
              <span class="kv">kmsg_readable=<b>{kmsgAuditData.kmsg.available}</b></span>
              {#if kmsgAuditData.kmsg.records_read > 0}
                <span class="kv">records={kmsgAuditData.kmsg.records_read}</span>
              {/if}
            </div>
            <p style="margin: 4px 0;">{kmsgAuditData.verdict.reason}</p>
            {#if kmsgAuditData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.kmsg.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{kmsgAuditData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(kmsgAuditData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #50.4 sock_pool (UI sprint 41) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.sock.title")}</h4>
        <p class="muted">{i18n.t("integrations.sock.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadSockPoolAudit}>{i18n.t("integrations.sock.refresh")}</button>
          {#if sockPoolAuditData?.ok}
            <span class="kv" style="margin-left: 12px;"
                  style:color={['time_wait_high','orphan_high'].includes(sockPoolAuditData.verdict.verdict) ? 'var(--warn)' :
                             sockPoolAuditData.verdict.verdict === 'unix_backlog' ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {i18n.t("integrations.sock.verdict")} : <b>{sockPoolAuditData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if sockPoolAuditData?.ok}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        ['time_wait_high','orphan_high'].includes(sockPoolAuditData.verdict.verdict) ? 'var(--warn)' :
                        sockPoolAuditData.verdict.verdict === 'unix_backlog' ? 'var(--accent)' :
                        'var(--text-dim)'};">
            <div class="form-row" style="gap: 12px; flex-wrap: wrap;">
              <span class="kv">TCP inuse=<b>{sockPoolAuditData.sockstat?.TCP?.inuse ?? 0}</b></span>
              <span class="kv">tw=<b>{sockPoolAuditData.sockstat?.TCP?.tw ?? 0}</b></span>
              <span class="kv">orphan=<b>{sockPoolAuditData.sockstat?.TCP?.orphan ?? 0}</b></span>
              <span class="kv">unix=<b>{sockPoolAuditData.unix_socket_count}</b></span>
              <span class="kv">tw_max={sockPoolAuditData.tcp_max_tw_buckets ?? '?'}</span>
            </div>
            <p style="margin: 4px 0;">{sockPoolAuditData.verdict.reason}</p>
            {#if sockPoolAuditData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.sock.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{sockPoolAuditData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(sockPoolAuditData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #50.3 IIO sensors (UI sprint 41) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.iio.title")}</h4>
        <p class="muted">{i18n.t("integrations.iio.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadIioSensorAudit}>{i18n.t("integrations.iio.refresh")}</button>
          {#if iioSensorAuditData}
            <span class="kv" style="margin-left: 12px;"
                  style:color={iioSensorAuditData.verdict.verdict === 'chassis_intrusion' ? 'var(--warn)' :
                             iioSensorAuditData.verdict.verdict === 'sensor_inventory' ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {iioSensorAuditData.device_count ?? 0} sensors · {i18n.t("integrations.iio.verdict")} : <b>{iioSensorAuditData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if iioSensorAuditData?.ok}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        iioSensorAuditData.verdict.verdict === 'chassis_intrusion' ? 'var(--warn)' :
                        iioSensorAuditData.verdict.verdict === 'sensor_inventory' ? 'var(--accent)' :
                        'var(--text-dim)'};">
            {#if iioSensorAuditData.devices.length > 0}
              <ul style="font-size: 0.85em; margin: 4px 0; padding-left: 20px;">
                {#each iioSensorAuditData.devices.slice(0, 8) as d}
                  <li>{d.name} ({d.driver_name ?? '?'}, type=<b>{d.sensor_type ?? 'other'}</b>)</li>
                {/each}
              </ul>
            {/if}
            <p style="margin: 4px 0;">{iioSensorAuditData.verdict.reason}</p>
            {#if iioSensorAuditData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.iio.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{iioSensorAuditData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(iioSensorAuditData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #50.1 DRM (UI sprint 41) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.drm.title")}</h4>
        <p class="muted">{i18n.t("integrations.drm.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadDrmAudit}>{i18n.t("integrations.drm.refresh")}</button>
          {#if drmAuditData?.ok}
            <span class="kv" style="margin-left: 12px;"
                  style:color={drmAuditData.verdict.verdict === 'connector_disconnected_active' ? 'var(--warn)' :
                             drmAuditData.verdict.verdict === 'no_displays' ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {drmAuditData.card_count ?? 0} cards · {drmAuditData.connector_count ?? 0} conn · {i18n.t("integrations.drm.verdict")} : <b>{drmAuditData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if drmAuditData?.ok}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        drmAuditData.verdict.verdict === 'connector_disconnected_active' ? 'var(--warn)' :
                        drmAuditData.verdict.verdict === 'no_displays' ? 'var(--accent)' :
                        'var(--text-dim)'};">
            <details style="margin-top: 4px;">
              <summary class="muted">Connectors</summary>
              <ul style="font-size: 0.85em; margin: 4px 0; padding-left: 20px;">
                {#each drmAuditData.connectors as c}
                  <li>{c.name}: status=<b
                    style:color={c.status === 'connected' ? 'var(--accent)' : 'inherit'}
                  >{c.status ?? '?'}</b> enabled={c.enabled ?? '?'} modes={c.mode_count}</li>
                {/each}
              </ul>
            </details>
            <p style="margin: 4px 0;">{drmAuditData.verdict.reason}</p>
            {#if drmAuditData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.drm.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{drmAuditData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(drmAuditData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #50.2 cgroup memevents (UI sprint 41) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.cmem.title")}</h4>
        <p class="muted">{i18n.t("integrations.cmem.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadCgroupMemeventsAudit}>{i18n.t("integrations.cmem.refresh")}</button>
          {#if cgroupMemeventsAuditData?.ok}
            <span class="kv" style="margin-left: 12px;"
                  style:color={cgroupMemeventsAuditData.verdict.verdict === 'oom_in_unit' ? 'var(--warn)' :
                             ['swap_failures','high_pressure'].includes(cgroupMemeventsAuditData.verdict.verdict) ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {cgroupMemeventsAuditData.unit_count ?? 0} units · {i18n.t("integrations.cmem.verdict")} : <b>{cgroupMemeventsAuditData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if cgroupMemeventsAuditData?.ok}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        cgroupMemeventsAuditData.verdict.verdict === 'oom_in_unit' ? 'var(--warn)' :
                        ['swap_failures','high_pressure'].includes(cgroupMemeventsAuditData.verdict.verdict) ? 'var(--accent)' :
                        'var(--text-dim)'};">
            <details style="margin-top: 4px;">
              <summary class="muted">Top units by peak RSS</summary>
              <ul style="font-size: 0.85em; margin: 4px 0; padding-left: 20px;">
                {#each cgroupMemeventsAuditData.top_units.slice(0, 8) as u}
                  <li>{u.path}: peak=<b>{u.peak_bytes ? (u.peak_bytes / 1024 / 1024 / 1024).toFixed(2) : '?'} GiB</b>
                    {#if (u.events?.oom_kill ?? 0) > 0}<span style:color="var(--warn)">oom_kill={u.events.oom_kill}</span>{/if}
                    {#if (u.events?.high ?? 0) > 0}<span style:color="var(--accent)">high={u.events.high}</span>{/if}</li>
                {/each}
              </ul>
            </details>
            <p style="margin: 4px 0;">{cgroupMemeventsAuditData.verdict.reason}</p>
            {#if cgroupMemeventsAuditData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.cmem.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{cgroupMemeventsAuditData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(cgroupMemeventsAuditData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #51.1 power supply (UI sprint 42) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.psu.title")}</h4>
        <p class="muted">{i18n.t("integrations.psu.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadPowerSupplyAudit}>{i18n.t("integrations.psu.refresh")}</button>
          {#if powerSupplyAuditData?.ok}
            <span class="kv" style="margin-left: 12px;"
                  style:color={powerSupplyAuditData.verdict.verdict === 'battery_degraded' ? 'var(--warn)' :
                             ['no_ac','charge_threshold_unset'].includes(powerSupplyAuditData.verdict.verdict) ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {powerSupplyAuditData.supply_count ?? 0} supplies · {i18n.t("integrations.psu.verdict")} : <b>{powerSupplyAuditData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if powerSupplyAuditData?.ok}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        powerSupplyAuditData.verdict.verdict === 'battery_degraded' ? 'var(--warn)' :
                        ['no_ac','charge_threshold_unset'].includes(powerSupplyAuditData.verdict.verdict) ? 'var(--accent)' :
                        'var(--text-dim)'};">
            <details style="margin-top: 4px;">
              <summary class="muted">Supplies</summary>
              <ul style="font-size: 0.85em; margin: 4px 0; padding-left: 20px;">
                {#each powerSupplyAuditData.supplies as s}
                  <li>{s.name} ({s.type ?? '?'}):
                    {#if s.capacity != null}capacity={s.capacity}%{/if}
                    {#if s.cycle_count != null} cycles={s.cycle_count}{/if}
                    {#if s.online != null} online={s.online}{/if}
                    {#if s.charge_full && s.charge_full_design}
                      wear=<b>{((s.charge_full / s.charge_full_design) * 100).toFixed(0)}%</b>
                    {/if}
                  </li>
                {/each}
              </ul>
            </details>
            <p style="margin: 4px 0;">{powerSupplyAuditData.verdict.reason}</p>
            {#if powerSupplyAuditData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.psu.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{powerSupplyAuditData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(powerSupplyAuditData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #51.4 typec (UI sprint 42) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.typec.title")}</h4>
        <p class="muted">{i18n.t("integrations.typec.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadTypecAudit}>{i18n.t("integrations.typec.refresh")}</button>
          {#if typecAuditData?.ok}
            <span class="kv" style="margin-left: 12px;"
                  style:color={typecAuditData.verdict.verdict === 'pd_no_contract' ? 'var(--warn)' :
                             typecAuditData.verdict.verdict === 'alt_mode_active' ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {typecAuditData.port_count ?? 0} ports · {i18n.t("integrations.typec.verdict")} : <b>{typecAuditData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if typecAuditData?.ok}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        typecAuditData.verdict.verdict === 'pd_no_contract' ? 'var(--warn)' :
                        typecAuditData.verdict.verdict === 'alt_mode_active' ? 'var(--accent)' :
                        'var(--text-dim)'};">
            <details style="margin-top: 4px;">
              <summary class="muted">Ports</summary>
              <ul style="font-size: 0.85em; margin: 4px 0; padding-left: 20px;">
                {#each typecAuditData.ports as p}
                  <li>{p.name}: data={p.data_role ?? '?'} power={p.power_role ?? '?'}
                    {#if p.usb_power_delivery_revision} pd={p.usb_power_delivery_revision}{/if}
                  </li>
                {/each}
              </ul>
            </details>
            <p style="margin: 4px 0;">{typecAuditData.verdict.reason}</p>
            {#if typecAuditData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.typec.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{typecAuditData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(typecAuditData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #51.3 perf PMU (UI sprint 42) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.pmu.title")}</h4>
        <p class="muted">{i18n.t("integrations.pmu.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadPerfPmuAudit}>{i18n.t("integrations.pmu.refresh")}</button>
          {#if perfPmuAuditData?.ok}
            <span class="kv" style="margin-left: 12px;"
                  style:color={perfPmuAuditData.verdict.verdict === 'no_pmu' ? 'var(--warn)' :
                             'var(--text-dim)'}>
              {perfPmuAuditData.pmu_count ?? 0} PMUs · {i18n.t("integrations.pmu.verdict")} : <b>{perfPmuAuditData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if perfPmuAuditData?.ok}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        perfPmuAuditData.verdict.verdict === 'no_pmu' ? 'var(--warn)' :
                        'var(--text-dim)'};">
            <details style="margin-top: 4px;">
              <summary class="muted">PMUs (top by event count)</summary>
              <ul style="font-size: 0.85em; margin: 4px 0; padding-left: 20px;">
                {#each [...perfPmuAuditData.pmus].sort((a,b)=>b.event_count-a.event_count).slice(0, 12) as p}
                  <li>{p.name} ({p.kind ?? '?'}): type={p.type ?? '?'} events={p.event_count} formats={p.format_count}</li>
                {/each}
              </ul>
            </details>
            <p style="margin: 4px 0;">{perfPmuAuditData.verdict.reason}</p>
            {#if perfPmuAuditData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.pmu.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{perfPmuAuditData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(perfPmuAuditData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #51.2 IOMEM + PCI (UI sprint 42) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.iomempci.title")}</h4>
        <p class="muted">{i18n.t("integrations.iomempci.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadIomemPciAudit}>{i18n.t("integrations.iomempci.refresh")}</button>
          {#if iomemPciAuditData?.ok}
            <span class="kv" style="margin-left: 12px;"
                  style:color={iomemPciAuditData.verdict.verdict === 'unbound_device' ? 'var(--warn)' :
                             ['reset_method_bus_only','iomem_masked'].includes(iomemPciAuditData.verdict.verdict) ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {iomemPciAuditData.pci_device_count ?? 0} PCI · {iomemPciAuditData.iomem.region_count} iomem · {i18n.t("integrations.iomempci.verdict")} : <b>{iomemPciAuditData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if iomemPciAuditData?.ok}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        iomemPciAuditData.verdict.verdict === 'unbound_device' ? 'var(--warn)' :
                        ['reset_method_bus_only','iomem_masked'].includes(iomemPciAuditData.verdict.verdict) ? 'var(--accent)' :
                        'var(--text-dim)'};">
            <details style="margin-top: 4px;">
              <summary class="muted">PCI devices (first 12)</summary>
              <ul style="font-size: 0.85em; margin: 4px 0; padding-left: 20px;">
                {#each iomemPciAuditData.pci_devices.slice(0, 12) as d}
                  <li>{d.bdf}: driver=<b
                    style:color={d.driver ? 'inherit' : 'var(--warn)'}
                  >{d.driver ?? 'none'}</b> reset={d.reset_method ?? '?'}</li>
                {/each}
              </ul>
            </details>
            <p style="margin: 4px 0;">{iomemPciAuditData.verdict.reason}</p>
            {#if iomemPciAuditData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.iomempci.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{iomemPciAuditData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(iomemPciAuditData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #52.1 KSM + THP (UI sprint 43) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.ksm.title")}</h4>
        <p class="muted">{i18n.t("integrations.ksm.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadKsmAudit}>{i18n.t("integrations.ksm.refresh")}</button>
          {#if ksmAuditData?.ok}
            <span class="kv" style="margin-left: 12px;"
                  style:color={['ksm_thrashing','thp_always_with_llm','thp_defrag_aggressive'].includes(ksmAuditData.verdict.verdict) ? 'var(--warn)' :
                             ksmAuditData.verdict.verdict === 'ksm_disabled_with_madvise' ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {i18n.t("integrations.ksm.verdict")} : <b>{ksmAuditData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if ksmAuditData?.ok}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        ['ksm_thrashing','thp_always_with_llm','thp_defrag_aggressive'].includes(ksmAuditData.verdict.verdict) ? 'var(--warn)' :
                        ksmAuditData.verdict.verdict === 'ksm_disabled_with_madvise' ? 'var(--accent)' :
                        'var(--text-dim)'};">
            <details style="margin-top: 4px;">
              <summary class="muted">KSM + THP knobs</summary>
              <ul style="font-size: 0.85em; margin: 4px 0; padding-left: 20px;">
                {#if ksmAuditData.ksm.available}
                  <li>KSM: run={ksmAuditData.ksm.run ?? '?'} sharing={ksmAuditData.ksm.pages_sharing ?? 0} scan={ksmAuditData.ksm.pages_to_scan ?? '?'} sleep={ksmAuditData.ksm.sleep_millisecs ?? '?'} ms</li>
                {/if}
                {#if ksmAuditData.thp.available}
                  <li>THP: enabled=<b>{ksmAuditData.thp.enabled ?? '?'}</b> defrag=<b>{ksmAuditData.thp.defrag ?? '?'}</b></li>
                {/if}
              </ul>
            </details>
            <p style="margin: 4px 0;">{ksmAuditData.verdict.reason}</p>
            {#if ksmAuditData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.ksm.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{ksmAuditData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(ksmAuditData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #52.2 I2C / SMBus / DDC (UI sprint 43) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.i2c.title")}</h4>
        <p class="muted">{i18n.t("integrations.i2c.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadI2cSmbusAudit}>{i18n.t("integrations.i2c.refresh")}</button>
          {#if i2cSmbusAuditData?.ok}
            <span class="kv" style="margin-left: 12px;"
                  style:color={i2cSmbusAuditData.verdict.verdict === 'ddc_bus_world_writable' ? 'var(--warn)' :
                             ['i2c_dev_module_absent','nvidia_ddc_missing','smbus_orphan_adapter'].includes(i2cSmbusAuditData.verdict.verdict) ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {i2cSmbusAuditData.adapter_count ?? 0} adapters · {i18n.t("integrations.i2c.verdict")} : <b>{i2cSmbusAuditData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if i2cSmbusAuditData?.ok}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        i2cSmbusAuditData.verdict.verdict === 'ddc_bus_world_writable' ? 'var(--warn)' :
                        ['i2c_dev_module_absent','nvidia_ddc_missing','smbus_orphan_adapter'].includes(i2cSmbusAuditData.verdict.verdict) ? 'var(--accent)' :
                        'var(--text-dim)'};">
            <details style="margin-top: 4px;">
              <summary class="muted">Adapters</summary>
              <ul style="font-size: 0.85em; margin: 4px 0; padding-left: 20px;">
                {#each i2cSmbusAuditData.adapters.slice(0, 12) as a}
                  <li>{a.id}: {a.name ?? '?'} · driver=<b
                    style:color={a.driver ? 'inherit' : 'var(--warn)'}
                  >{a.driver ?? 'none'}</b></li>
                {/each}
              </ul>
            </details>
            <p style="margin: 4px 0;">{i2cSmbusAuditData.verdict.reason}</p>
            {#if i2cSmbusAuditData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.i2c.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{i2cSmbusAuditData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(i2cSmbusAuditData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #52.3 Module integrity (UI sprint 43) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.modint.title")}</h4>
        <p class="muted">{i18n.t("integrations.modint.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadModuleIntegrityAudit}>{i18n.t("integrations.modint.refresh")}</button>
          {#if moduleIntegrityAuditData?.ok}
            <span class="kv" style="margin-left: 12px;"
                  style:color={['nvidia_version_mismatch','unsigned_modules_unexpected'].includes(moduleIntegrityAuditData.verdict.verdict) ? 'var(--warn)' :
                             ['modules_disabled','tainted_oot_nvidia_only'].includes(moduleIntegrityAuditData.verdict.verdict) ? 'var(--accent)' :
                             'var(--text-dim)'}>
              taint={moduleIntegrityAuditData.tainted_letters.join('') || '0'} · {i18n.t("integrations.modint.verdict")} : <b>{moduleIntegrityAuditData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if moduleIntegrityAuditData?.ok}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        ['nvidia_version_mismatch','unsigned_modules_unexpected'].includes(moduleIntegrityAuditData.verdict.verdict) ? 'var(--warn)' :
                        ['modules_disabled','tainted_oot_nvidia_only'].includes(moduleIntegrityAuditData.verdict.verdict) ? 'var(--accent)' :
                        'var(--text-dim)'};">
            <details style="margin-top: 4px;">
              <summary class="muted">Tainted modules + NVIDIA versions</summary>
              <ul style="font-size: 0.85em; margin: 4px 0; padding-left: 20px;">
                {#if moduleIntegrityAuditData.nvidia_loaded_version}
                  <li>nvidia loaded=<b>{moduleIntegrityAuditData.nvidia_loaded_version}</b>
                    {#if moduleIntegrityAuditData.nvidia_runtime_version}
                      runtime=<b style:color={moduleIntegrityAuditData.nvidia_loaded_version === moduleIntegrityAuditData.nvidia_runtime_version ? 'inherit' : 'var(--warn)'}>{moduleIntegrityAuditData.nvidia_runtime_version}</b>
                    {/if}
                  </li>
                {/if}
                {#each moduleIntegrityAuditData.tainted_modules.slice(0, 12) as m}
                  <li>{m.name}: taint={m.taint}</li>
                {/each}
              </ul>
            </details>
            <p style="margin: 4px 0;">{moduleIntegrityAuditData.verdict.reason}</p>
            {#if moduleIntegrityAuditData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.modint.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{moduleIntegrityAuditData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(moduleIntegrityAuditData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #53.1 PSI pressure (UI sprint 44) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.psi.title")}</h4>
        <p class="muted">{i18n.t("integrations.psi.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadPsiPressureAudit}>{i18n.t("integrations.psi.refresh")}</button>
          {#if psiPressureAuditData?.ok}
            <span class="kv" style="margin-left: 12px;"
                  style:color={['memory_full_stall_high','psi_disabled'].includes(psiPressureAuditData.verdict.verdict) ? 'var(--warn)' :
                             ['io_some_stall_high','cpu_some_stall_elevated'].includes(psiPressureAuditData.verdict.verdict) ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {i18n.t("integrations.psi.verdict")} : <b>{psiPressureAuditData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if psiPressureAuditData?.ok}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        ['memory_full_stall_high','psi_disabled'].includes(psiPressureAuditData.verdict.verdict) ? 'var(--warn)' :
                        ['io_some_stall_high','cpu_some_stall_elevated'].includes(psiPressureAuditData.verdict.verdict) ? 'var(--accent)' :
                        'var(--text-dim)'};">
            <details style="margin-top: 4px;">
              <summary class="muted">avg10 / avg60 / avg300</summary>
              <ul style="font-size: 0.85em; margin: 4px 0; padding-left: 20px;">
                {#if psiPressureAuditData.pressure.cpu?.some}
                  <li>cpu.some: {psiPressureAuditData.pressure.cpu.some.avg10.toFixed(2)}% / {psiPressureAuditData.pressure.cpu.some.avg60.toFixed(2)}% / {psiPressureAuditData.pressure.cpu.some.avg300.toFixed(2)}%</li>
                {/if}
                {#if psiPressureAuditData.pressure.memory?.full}
                  <li>memory.full: {psiPressureAuditData.pressure.memory.full.avg10.toFixed(2)}% / {psiPressureAuditData.pressure.memory.full.avg60.toFixed(2)}% / {psiPressureAuditData.pressure.memory.full.avg300.toFixed(2)}%</li>
                {/if}
                {#if psiPressureAuditData.pressure.io?.some}
                  <li>io.some: {psiPressureAuditData.pressure.io.some.avg10.toFixed(2)}% / {psiPressureAuditData.pressure.io.some.avg60.toFixed(2)}% / {psiPressureAuditData.pressure.io.some.avg300.toFixed(2)}%</li>
                {/if}
              </ul>
            </details>
            <p style="margin: 4px 0;">{psiPressureAuditData.verdict.reason}</p>
            {#if psiPressureAuditData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.psi.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{psiPressureAuditData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(psiPressureAuditData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #53.2 CPU vulnerabilities (UI sprint 44) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.cpuvuln.title")}</h4>
        <p class="muted">{i18n.t("integrations.cpuvuln.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadCpuVulnerabilitiesAudit}>{i18n.t("integrations.cpuvuln.refresh")}</button>
          {#if cpuVulnerabilitiesAuditData?.ok}
            <span class="kv" style="margin-left: 12px;"
                  style:color={['vulnerable_unmitigated','mitigation_disabled_via_cmdline','smt_forced_on_with_vuln'].includes(cpuVulnerabilitiesAuditData.verdict.verdict) ? 'var(--warn)' :
                             cpuVulnerabilitiesAuditData.verdict.verdict === 'partial_mitigation' ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {cpuVulnerabilitiesAuditData.vuln_count ?? 0} tracked · {i18n.t("integrations.cpuvuln.verdict")} : <b>{cpuVulnerabilitiesAuditData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if cpuVulnerabilitiesAuditData?.ok}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        ['vulnerable_unmitigated','mitigation_disabled_via_cmdline','smt_forced_on_with_vuln'].includes(cpuVulnerabilitiesAuditData.verdict.verdict) ? 'var(--warn)' :
                        cpuVulnerabilitiesAuditData.verdict.verdict === 'partial_mitigation' ? 'var(--accent)' :
                        'var(--text-dim)'};">
            <details style="margin-top: 4px;">
              <summary class="muted">Vulnerabilities</summary>
              <ul style="font-size: 0.85em; margin: 4px 0; padding-left: 20px;">
                {#each Object.entries(cpuVulnerabilitiesAuditData.vulnerabilities) as [vname, vstate]}
                  <li>{vname}: <span style:color={vstate.startsWith('Vulnerable') ? 'var(--warn)' : (vstate.startsWith('Mitigation') ? 'var(--accent)' : 'inherit')}>{vstate}</span></li>
                {/each}
              </ul>
            </details>
            <p style="margin: 4px 0;">SMT active={cpuVulnerabilitiesAuditData.smt.active ?? '?'} · cmdline-off={cpuVulnerabilitiesAuditData.cmdline_off_tokens.join(',') || 'none'}</p>
            <p style="margin: 4px 0;">{cpuVulnerabilitiesAuditData.verdict.reason}</p>
            {#if cpuVulnerabilitiesAuditData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.cpuvuln.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{cpuVulnerabilitiesAuditData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(cpuVulnerabilitiesAuditData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #53.3 IMA / SecureBoot (UI sprint 44) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.ima.title")}</h4>
        <p class="muted">{i18n.t("integrations.ima.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadImaIntegrityAudit}>{i18n.t("integrations.ima.refresh")}</button>
          {#if imaIntegrityAuditData?.ok}
            <span class="kv" style="margin-left: 12px;"
                  style:color={['evm_disabled_secureboot_on','ima_violations_nonzero'].includes(imaIntegrityAuditData.verdict.verdict) ? 'var(--warn)' :
                             ['ima_no_policy_loaded','measurement_log_stagnant','requires_root'].includes(imaIntegrityAuditData.verdict.verdict) ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {i18n.t("integrations.ima.verdict")} : <b>{imaIntegrityAuditData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if imaIntegrityAuditData?.ok}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        ['evm_disabled_secureboot_on','ima_violations_nonzero'].includes(imaIntegrityAuditData.verdict.verdict) ? 'var(--warn)' :
                        ['ima_no_policy_loaded','measurement_log_stagnant','requires_root'].includes(imaIntegrityAuditData.verdict.verdict) ? 'var(--accent)' :
                        'var(--text-dim)'};">
            <ul style="font-size: 0.85em; margin: 4px 0; padding-left: 20px;">
              <li>SecureBoot: {imaIntegrityAuditData.secureboot.present ? (imaIntegrityAuditData.secureboot.enabled ? 'enabled' : 'disabled') : 'absent'}</li>
              <li>IMA: {imaIntegrityAuditData.ima.available ? 'available' : 'absent'}
                {#if imaIntegrityAuditData.ima.violations != null} · violations={imaIntegrityAuditData.ima.violations}{/if}
                {#if imaIntegrityAuditData.ima.runtime_measurements_count != null} · measurements={imaIntegrityAuditData.ima.runtime_measurements_count}{/if}
              </li>
              <li>EVM: {imaIntegrityAuditData.evm.available ? (imaIntegrityAuditData.evm.armed ? 'armed' : 'off') : 'absent'}</li>
            </ul>
            <p style="margin: 4px 0;">{imaIntegrityAuditData.verdict.reason}</p>
            {#if imaIntegrityAuditData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.ima.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{imaIntegrityAuditData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(imaIntegrityAuditData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #53.4 RAPL + cpufreq (UI sprint 44) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.rapl.title")}</h4>
        <p class="muted">{i18n.t("integrations.rapl.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadRaplPowerCapAudit}>{i18n.t("integrations.rapl.refresh")}</button>
          {#if raplPowerCapAuditData?.ok}
            <span class="kv" style="margin-left: 12px;"
                  style:color={raplPowerCapAuditData.verdict.verdict === 'pl1_below_tdp_throttling' ? 'var(--warn)' :
                             ['governor_powersave_mixed','turbo_disabled_silently','psys_cap_active'].includes(raplPowerCapAuditData.verdict.verdict) ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {raplPowerCapAuditData.zone_count ?? 0} RAPL · {raplPowerCapAuditData.cpu_count ?? 0} CPUs · {i18n.t("integrations.rapl.verdict")} : <b>{raplPowerCapAuditData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if raplPowerCapAuditData?.ok}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        raplPowerCapAuditData.verdict.verdict === 'pl1_below_tdp_throttling' ? 'var(--warn)' :
                        ['governor_powersave_mixed','turbo_disabled_silently','psys_cap_active'].includes(raplPowerCapAuditData.verdict.verdict) ? 'var(--accent)' :
                        'var(--text-dim)'};">
            <details style="margin-top: 4px;">
              <summary class="muted">RAPL zones + governors</summary>
              <ul style="font-size: 0.85em; margin: 4px 0; padding-left: 20px;">
                {#each raplPowerCapAuditData.zones as z}
                  <li>{z.id} ({z.name ?? '?'}):
                    {#if z.constraint_0_power_limit_uw} PL1={(z.constraint_0_power_limit_uw/1e6).toFixed(1)}W{/if}
                    {#if z.max_power_range_uw} / max={(z.max_power_range_uw/1e6).toFixed(1)}W{/if}
                  </li>
                {/each}
                {#each Object.entries(raplPowerCapAuditData.governor_histogram) as [gov, n]}
                  <li>governor: {gov} × {n}</li>
                {/each}
                {#if raplPowerCapAuditData.turbo.intel_pstate_no_turbo != null}
                  <li>intel_pstate.no_turbo = {raplPowerCapAuditData.turbo.intel_pstate_no_turbo}</li>
                {/if}
                {#if raplPowerCapAuditData.turbo.cpufreq_boost != null}
                  <li>cpufreq.boost = {raplPowerCapAuditData.turbo.cpufreq_boost}</li>
                {/if}
              </ul>
            </details>
            <p style="margin: 4px 0;">{raplPowerCapAuditData.verdict.reason}</p>
            {#if raplPowerCapAuditData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.rapl.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{raplPowerCapAuditData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(raplPowerCapAuditData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #54.2 Swap tunables (UI sprint 45) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.swaptun.title")}</h4>
        <p class="muted">{i18n.t("integrations.swaptun.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadSwapTunablesAudit}>{i18n.t("integrations.swaptun.refresh")}</button>
          {#if swapTunablesAuditData?.ok}
            <span class="kv" style="margin-left: 12px;"
                  style:color={['swap_on_hdd','high_swappiness_with_gpu'].includes(swapTunablesAuditData.verdict.verdict) ? 'var(--warn)' :
                             ['tiny_min_free','page_cluster_default_on_zram'].includes(swapTunablesAuditData.verdict.verdict) ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {i18n.t("integrations.swaptun.verdict")} : <b>{swapTunablesAuditData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if swapTunablesAuditData?.ok}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        ['swap_on_hdd','high_swappiness_with_gpu'].includes(swapTunablesAuditData.verdict.verdict) ? 'var(--warn)' :
                        ['tiny_min_free','page_cluster_default_on_zram'].includes(swapTunablesAuditData.verdict.verdict) ? 'var(--accent)' :
                        'var(--text-dim)'};">
            <details style="margin-top: 4px;">
              <summary class="muted">vm knobs + swap devices</summary>
              <ul style="font-size: 0.85em; margin: 4px 0; padding-left: 20px;">
                <li>swappiness=<b>{swapTunablesAuditData.vm_knobs.swappiness ?? '?'}</b>
                  · page-cluster=<b>{swapTunablesAuditData.vm_knobs["page-cluster"] ?? '?'}</b>
                  · min_free_kbytes={swapTunablesAuditData.vm_knobs.min_free_kbytes ?? '?'}
                </li>
                <li>GPU present: {swapTunablesAuditData.gpu_present ? 'yes' : 'no'}
                  · zram active: {swapTunablesAuditData.zram_active.length ? swapTunablesAuditData.zram_active.join(',') : 'none'}
                </li>
                {#each swapTunablesAuditData.swaps as s}
                  <li>{s.path} ({s.type}): device={s.device ?? '?'} rotational={s.rotational ?? '?'}</li>
                {/each}
              </ul>
            </details>
            <p style="margin: 4px 0;">{swapTunablesAuditData.verdict.reason}</p>
            {#if swapTunablesAuditData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.swaptun.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{swapTunablesAuditData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(swapTunablesAuditData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #54.1 Hugepages (UI sprint 45) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.hugep.title")}</h4>
        <p class="muted">{i18n.t("integrations.hugep.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadHugepagesAudit}>{i18n.t("integrations.hugep.refresh")}</button>
          {#if hugepagesAuditData?.ok}
            <span class="kv" style="margin-left: 12px;"
                  style:color={['reserved_unused','exhausted'].includes(hugepagesAuditData.verdict.verdict) ? 'var(--warn)' :
                             ['numa_imbalance','overcommit_disabled'].includes(hugepagesAuditData.verdict.verdict) ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {i18n.t("integrations.hugep.verdict")} : <b>{hugepagesAuditData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if hugepagesAuditData?.ok}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        ['reserved_unused','exhausted'].includes(hugepagesAuditData.verdict.verdict) ? 'var(--warn)' :
                        ['numa_imbalance','overcommit_disabled'].includes(hugepagesAuditData.verdict.verdict) ? 'var(--accent)' :
                        'var(--text-dim)'};">
            <details style="margin-top: 4px;">
              <summary class="muted">Pools</summary>
              <ul style="font-size: 0.85em; margin: 4px 0; padding-left: 20px;">
                {#each hugepagesAuditData.pools as p}
                  <li>{p.size_kb} kB: nr={p.nr ?? '?'} free={p.free ?? '?'} resv={p.resv ?? '?'} overcommit={p.nr_overcommit ?? '?'}</li>
                {/each}
              </ul>
            </details>
            <p style="margin: 4px 0;">{hugepagesAuditData.verdict.reason}</p>
            {#if hugepagesAuditData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.hugep.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{hugepagesAuditData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(hugepagesAuditData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #54.3 KVM misc (UI sprint 45) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.kvm.title")}</h4>
        <p class="muted">{i18n.t("integrations.kvm.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadKvmMiscAudit}>{i18n.t("integrations.kvm.refresh")}</button>
          {#if kvmMiscAuditData?.ok}
            <span class="kv" style="margin-left: 12px;"
                  style:color={['kvm_disabled','nested_on_with_passthrough'].includes(kvmMiscAuditData.verdict.verdict) ? 'var(--warn)' :
                             ['halt_poll_excessive','group_perm_missing'].includes(kvmMiscAuditData.verdict.verdict) ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {i18n.t("integrations.kvm.verdict")} : <b>{kvmMiscAuditData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if kvmMiscAuditData?.ok}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        ['kvm_disabled','nested_on_with_passthrough'].includes(kvmMiscAuditData.verdict.verdict) ? 'var(--warn)' :
                        ['halt_poll_excessive','group_perm_missing'].includes(kvmMiscAuditData.verdict.verdict) ? 'var(--accent)' :
                        'var(--text-dim)'};">
            <ul style="font-size: 0.85em; margin: 4px 0; padding-left: 20px;">
              <li>kvm module: {kvmMiscAuditData.kvm_module_present ? 'loaded' : 'absent'}
                · variant: {kvmMiscAuditData.kvm_variant ?? '?'}
                · nested: {kvmMiscAuditData.nested ?? '?'}
                · vfio_pci: {kvmMiscAuditData.vfio_pci_loaded ? 'yes' : 'no'}
              </li>
              <li>halt_poll_ns: {kvmMiscAuditData.kvm_params.halt_poll_ns ?? '?'}</li>
              {#if kvmMiscAuditData.dev_kvm.present}
                <li>/dev/kvm mode=0{(kvmMiscAuditData.dev_kvm.mode ?? 0).toString(8)} group={kvmMiscAuditData.dev_kvm.group_name ?? '?'}</li>
              {/if}
            </ul>
            <p style="margin: 4px 0;">{kvmMiscAuditData.verdict.reason}</p>
            {#if kvmMiscAuditData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.kvm.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{kvmMiscAuditData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(kvmMiscAuditData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #54.4 io_uring runtime (UI sprint 45) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.iouring.title")}</h4>
        <p class="muted">{i18n.t("integrations.iouring.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadIoUringRuntimeAudit}>{i18n.t("integrations.iouring.refresh")}</button>
          {#if ioUringRuntimeAuditData?.ok}
            <span class="kv" style="margin-left: 12px;"
                  style:color={['disabled_systemwide','kernel_pre_cve_fix'].includes(ioUringRuntimeAuditData.verdict.verdict) ? 'var(--warn)' :
                             ['unrestricted_to_all_users','debugfs_locked_requires_root'].includes(ioUringRuntimeAuditData.verdict.verdict) ? 'var(--accent)' :
                             'var(--text-dim)'}>
              kernel={ioUringRuntimeAuditData.kernel_release} · {i18n.t("integrations.iouring.verdict")} : <b>{ioUringRuntimeAuditData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if ioUringRuntimeAuditData?.ok}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        ['disabled_systemwide','kernel_pre_cve_fix'].includes(ioUringRuntimeAuditData.verdict.verdict) ? 'var(--warn)' :
                        ['unrestricted_to_all_users','debugfs_locked_requires_root'].includes(ioUringRuntimeAuditData.verdict.verdict) ? 'var(--accent)' :
                        'var(--text-dim)'};">
            <ul style="font-size: 0.85em; margin: 4px 0; padding-left: 20px;">
              <li>io_uring_disabled = {ioUringRuntimeAuditData.io_uring_disabled ?? 'n/a'}
                · io_uring_group = {ioUringRuntimeAuditData.io_uring_group ?? 'n/a'}
              </li>
              <li>debugfs: {ioUringRuntimeAuditData.debugfs_present ? (ioUringRuntimeAuditData.debugfs_readable ? 'readable' : 'root-only') : 'absent'}</li>
            </ul>
            <p style="margin: 4px 0;">{ioUringRuntimeAuditData.verdict.reason}</p>
            {#if ioUringRuntimeAuditData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.iouring.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{ioUringRuntimeAuditData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(ioUringRuntimeAuditData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #55.1 EDAC ECC (UI sprint 46) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.edac.title")}</h4>
        <p class="muted">{i18n.t("integrations.edac.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadEdacEccAudit}>{i18n.t("integrations.edac.refresh")}</button>
          {#if edacEccAuditData?.ok}
            <span class="kv" style="margin-left: 12px;"
                  style:color={['ue_present','ce_rising'].includes(edacEccAuditData.verdict.verdict) ? 'var(--warn)' :
                             ['edac_absent','driver_missing'].includes(edacEccAuditData.verdict.verdict) ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {edacEccAuditData.controller_count ?? 0} controllers · {i18n.t("integrations.edac.verdict")} : <b>{edacEccAuditData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if edacEccAuditData?.ok}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        ['ue_present','ce_rising'].includes(edacEccAuditData.verdict.verdict) ? 'var(--warn)' :
                        ['edac_absent','driver_missing'].includes(edacEccAuditData.verdict.verdict) ? 'var(--accent)' :
                        'var(--text-dim)'};">
            <details style="margin-top: 4px;">
              <summary class="muted">Controllers + DIMMs</summary>
              <ul style="font-size: 0.85em; margin: 4px 0; padding-left: 20px;">
                {#each edacEccAuditData.controllers as c}
                  <li>{c.id} ({c.mc_name ?? '?'}): ce={c.ce_count ?? '?'} ue={c.ue_count ?? '?'}
                    {#if c.dimms.length}
                      <ul>
                        {#each c.dimms as d}
                          <li>{d.id} {d.label ?? ''}: ce={d.ce_count ?? '?'} ue={d.ue_count ?? '?'}</li>
                        {/each}
                      </ul>
                    {/if}
                  </li>
                {/each}
              </ul>
            </details>
            <p style="margin: 4px 0;">{edacEccAuditData.verdict.reason}</p>
            {#if edacEccAuditData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.edac.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{edacEccAuditData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(edacEccAuditData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #55.2 NUMA topology (UI sprint 46) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.numa.title")}</h4>
        <p class="muted">{i18n.t("integrations.numa.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadNumaTopologyAudit}>{i18n.t("integrations.numa.refresh")}</button>
          {#if numaTopologyAuditData?.ok}
            <span class="kv" style="margin-left: 12px;"
                  style:color={['gpu_numa_unset','cross_node_memory'].includes(numaTopologyAuditData.verdict.verdict) ? 'var(--warn)' :
                             numaTopologyAuditData.verdict.verdict === 'balancing_off_on_multi_node' ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {numaTopologyAuditData.node_count ?? 0} nodes · {i18n.t("integrations.numa.verdict")} : <b>{numaTopologyAuditData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if numaTopologyAuditData?.ok}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        ['gpu_numa_unset','cross_node_memory'].includes(numaTopologyAuditData.verdict.verdict) ? 'var(--warn)' :
                        numaTopologyAuditData.verdict.verdict === 'balancing_off_on_multi_node' ? 'var(--accent)' :
                        'var(--text-dim)'};">
            <ul style="font-size: 0.85em; margin: 4px 0; padding-left: 20px;">
              <li>numa_balancing = {numaTopologyAuditData.numa_balancing ?? '?'}</li>
              {#each numaTopologyAuditData.nvidia_gpus as g}
                <li>GPU {g.bdf}: numa_node=<b
                  style:color={g.numa_node === -1 ? 'var(--warn)' : 'inherit'}
                >{g.numa_node ?? '?'}</b> local_cpulist={g.local_cpulist ?? '?'}</li>
              {/each}
            </ul>
            <p style="margin: 4px 0;">{numaTopologyAuditData.verdict.reason}</p>
            {#if numaTopologyAuditData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.numa.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{numaTopologyAuditData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(numaTopologyAuditData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #55.3 hwmon (UI sprint 46) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.hwmon.title")}</h4>
        <p class="muted">{i18n.t("integrations.hwmon.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadHwmonSensorsAudit}>{i18n.t("integrations.hwmon.refresh")}</button>
          {#if hwmonSensorsAuditData?.ok}
            <span class="kv" style="margin-left: 12px;"
                  style:color={['voltage_alarm','fan_stall'].includes(hwmonSensorsAuditData.verdict.verdict) ? 'var(--warn)' :
                             ['pwm_manual_override','sensor_missing'].includes(hwmonSensorsAuditData.verdict.verdict) ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {hwmonSensorsAuditData.chip_count ?? 0} chips · {i18n.t("integrations.hwmon.verdict")} : <b>{hwmonSensorsAuditData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if hwmonSensorsAuditData?.ok}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        ['voltage_alarm','fan_stall'].includes(hwmonSensorsAuditData.verdict.verdict) ? 'var(--warn)' :
                        ['pwm_manual_override','sensor_missing'].includes(hwmonSensorsAuditData.verdict.verdict) ? 'var(--accent)' :
                        'var(--text-dim)'};">
            <details style="margin-top: 4px;">
              <summary class="muted">Chips</summary>
              <ul style="font-size: 0.85em; margin: 4px 0; padding-left: 20px;">
                {#each hwmonSensorsAuditData.chips as c}
                  <li>{c.id} ({c.name ?? '?'}): fans={Object.keys(c.fans).length} pwms={Object.keys(c.pwms).length} valarms={Object.keys(c.voltage_alarms).length}</li>
                {/each}
              </ul>
            </details>
            <p style="margin: 4px 0;">{hwmonSensorsAuditData.verdict.reason}</p>
            {#if hwmonSensorsAuditData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.hwmon.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{hwmonSensorsAuditData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(hwmonSensorsAuditData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #55.4 EFI boot order (UI sprint 46) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.efiboot.title")}</h4>
        <p class="muted">{i18n.t("integrations.efiboot.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadEfiBootOrderAudit}>{i18n.t("integrations.efiboot.refresh")}</button>
          {#if efiBootOrderAuditData?.ok}
            <span class="kv" style="margin-left: 12px;"
                  style:color={['bootnext_pinned','varstore_near_full'].includes(efiBootOrderAuditData.verdict.verdict) ? 'var(--warn)' :
                             ['dbx_absent_with_secureboot','bootorder_drift'].includes(efiBootOrderAuditData.verdict.verdict) ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {i18n.t("integrations.efiboot.verdict")} : <b>{efiBootOrderAuditData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if efiBootOrderAuditData?.ok}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        ['bootnext_pinned','varstore_near_full'].includes(efiBootOrderAuditData.verdict.verdict) ? 'var(--warn)' :
                        ['dbx_absent_with_secureboot','bootorder_drift'].includes(efiBootOrderAuditData.verdict.verdict) ? 'var(--accent)' :
                        'var(--text-dim)'};">
            <ul style="font-size: 0.85em; margin: 4px 0; padding-left: 20px;">
              {#if efiBootOrderAuditData.BootCurrent != null}
                <li>BootCurrent=0x{efiBootOrderAuditData.BootCurrent.toString(16).padStart(4,'0').toUpperCase()}</li>
              {/if}
              {#if efiBootOrderAuditData.BootOrder?.length}
                <li>BootOrder=[{efiBootOrderAuditData.BootOrder.map(v => '0x'+v.toString(16).padStart(4,'0').toUpperCase()).join(', ')}]</li>
              {/if}
              {#if efiBootOrderAuditData.BootNext != null}
                <li>BootNext=<b style:color="var(--warn)">0x{efiBootOrderAuditData.BootNext.toString(16).padStart(4,'0').toUpperCase()}</b></li>
              {/if}
              <li>SecureBoot: {efiBootOrderAuditData.SecureBoot ? 'on' : 'off'} · dbx_present: {efiBootOrderAuditData.dbx_present ? 'yes' : 'no'}</li>
              <li>varstore: {efiBootOrderAuditData.varstore_total_bytes != null ? (efiBootOrderAuditData.varstore_total_bytes / 1024).toFixed(1) + ' KiB' : '?'}</li>
            </ul>
            <p style="margin: 4px 0;">{efiBootOrderAuditData.verdict.reason}</p>
            {#if efiBootOrderAuditData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.efiboot.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{efiBootOrderAuditData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(efiBootOrderAuditData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #56.1 SATA ALPM (UI sprint 47) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.satapm.title")}</h4>
        <p class="muted">{i18n.t("integrations.satapm.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadSataLinkPmAudit}>{i18n.t("integrations.satapm.refresh")}</button>
          {#if sataLinkPmAuditData?.ok}
            <span class="kv" style="margin-left: 12px;"
                  style:color={['min_power','med_power_with_dipm'].includes(sataLinkPmAuditData.verdict.verdict) ? 'var(--warn)' :
                             sataLinkPmAuditData.verdict.verdict === 'medium_power' ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {sataLinkPmAuditData.host_count ?? 0} hosts · {i18n.t("integrations.satapm.verdict")} : <b>{sataLinkPmAuditData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if sataLinkPmAuditData?.ok}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        ['min_power','med_power_with_dipm'].includes(sataLinkPmAuditData.verdict.verdict) ? 'var(--warn)' :
                        sataLinkPmAuditData.verdict.verdict === 'medium_power' ? 'var(--accent)' :
                        'var(--text-dim)'};">
            <details style="margin-top: 4px;">
              <summary class="muted">Hosts</summary>
              <ul style="font-size: 0.85em; margin: 4px 0; padding-left: 20px;">
                {#each sataLinkPmAuditData.hosts as h}
                  <li>{h.id}: policy={h.policy ?? 'n/a'}</li>
                {/each}
              </ul>
            </details>
            <p style="margin: 4px 0;">{sataLinkPmAuditData.verdict.reason}</p>
            {#if sataLinkPmAuditData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.satapm.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{sataLinkPmAuditData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(sataLinkPmAuditData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #56.2 BDI writeback (UI sprint 47) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.bdi.title")}</h4>
        <p class="muted">{i18n.t("integrations.bdi.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadBdiWritebackAudit}>{i18n.t("integrations.bdi.refresh")}</button>
          {#if bdiWritebackAuditData?.ok}
            <span class="kv" style="margin-left: 12px;"
                  style:color={bdiWritebackAuditData.verdict.verdict === 'stuck_max_ratio_1' ? 'var(--warn)' :
                             ['readahead_below_128k_on_nvme','writeback_centisecs_above_3000'].includes(bdiWritebackAuditData.verdict.verdict) ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {bdiWritebackAuditData.bdi_count ?? 0} BDIs · {i18n.t("integrations.bdi.verdict")} : <b>{bdiWritebackAuditData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if bdiWritebackAuditData?.ok}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        bdiWritebackAuditData.verdict.verdict === 'stuck_max_ratio_1' ? 'var(--warn)' :
                        ['readahead_below_128k_on_nvme','writeback_centisecs_above_3000'].includes(bdiWritebackAuditData.verdict.verdict) ? 'var(--accent)' :
                        'var(--text-dim)'};">
            <details style="margin-top: 4px;">
              <summary class="muted">Real-device BDIs</summary>
              <ul style="font-size: 0.85em; margin: 4px 0; padding-left: 20px;">
                {#each bdiWritebackAuditData.bdis.filter(b => bdiWritebackAuditData!.device_map[b.id]) as b}
                  <li>{b.id} ({bdiWritebackAuditData.device_map[b.id]?.name ?? '?'}): ra={b.read_ahead_kb}kb max_ratio={b.max_ratio}</li>
                {/each}
              </ul>
            </details>
            <p style="margin: 4px 0;">writeback_centisecs={bdiWritebackAuditData.dirty_writeback_centisecs} · expire_centisecs={bdiWritebackAuditData.dirty_expire_centisecs}</p>
            <p style="margin: 4px 0;">{bdiWritebackAuditData.verdict.reason}</p>
            {#if bdiWritebackAuditData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.bdi.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{bdiWritebackAuditData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(bdiWritebackAuditData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #56.3 /proc/crypto (UI sprint 47) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.crypto.title")}</h4>
        <p class="muted">{i18n.t("integrations.crypto.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadProcCryptoAudit}>{i18n.t("integrations.crypto.refresh")}</button>
          {#if procCryptoAuditData?.ok}
            <span class="kv" style="margin-left: 12px;"
                  style:color={['fips_mode_on','selftest_failed_entry','aesni_missing_but_aes_used'].includes(procCryptoAuditData.verdict.verdict) ? 'var(--warn)' :
                             procCryptoAuditData.verdict.verdict === 'generic_only_aes' ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {procCryptoAuditData.entry_count ?? 0} entries · fips={procCryptoAuditData.fips_enabled ?? 'n/a'} · {i18n.t("integrations.crypto.verdict")} : <b>{procCryptoAuditData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if procCryptoAuditData?.ok}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        ['fips_mode_on','selftest_failed_entry','aesni_missing_but_aes_used'].includes(procCryptoAuditData.verdict.verdict) ? 'var(--warn)' :
                        procCryptoAuditData.verdict.verdict === 'generic_only_aes' ? 'var(--accent)' :
                        'var(--text-dim)'};">
            <p style="margin: 4px 0;">CPU AES-NI flag: {procCryptoAuditData.cpu_has_aes_flag ? 'yes' : 'no'} · {procCryptoAuditData.name_count ?? 0} distinct cipher name(s)</p>
            <p style="margin: 4px 0;">{procCryptoAuditData.verdict.reason}</p>
            {#if procCryptoAuditData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.crypto.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{procCryptoAuditData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(procCryptoAuditData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #56.4 wakeup_sources (UI sprint 47) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.wakeup.title")}</h4>
        <p class="muted">{i18n.t("integrations.wakeup.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadWakeupSourcesAudit}>{i18n.t("integrations.wakeup.refresh")}</button>
          {#if wakeupSourcesAuditData?.ok}
            <span class="kv" style="margin-left: 12px;"
                  style:color={['s2idle_wakeup_storm','gpe_chatter_above_1hz'].includes(wakeupSourcesAuditData.verdict.verdict) ? 'var(--warn)' :
                             wakeupSourcesAuditData.verdict.verdict === 'usb_hub_chatty' ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {wakeupSourcesAuditData.source_count ?? 0} sources · {i18n.t("integrations.wakeup.verdict")} : <b>{wakeupSourcesAuditData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if wakeupSourcesAuditData?.ok}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        ['s2idle_wakeup_storm','gpe_chatter_above_1hz'].includes(wakeupSourcesAuditData.verdict.verdict) ? 'var(--warn)' :
                        wakeupSourcesAuditData.verdict.verdict === 'usb_hub_chatty' ? 'var(--accent)' :
                        'var(--text-dim)'};">
            <details style="margin-top: 4px;">
              <summary class="muted">Top 5 by event count</summary>
              <ul style="font-size: 0.85em; margin: 4px 0; padding-left: 20px;">
                {#each wakeupSourcesAuditData.top_sources as s}
                  <li>{s.id} ({s.name ?? '?'}): events={s.event_count ?? '?'} active={s.active_count ?? '?'}</li>
                {/each}
              </ul>
            </details>
            <p style="margin: 4px 0;">uptime={wakeupSourcesAuditData.uptime_s != null ? (wakeupSourcesAuditData.uptime_s / 3600).toFixed(1) + ' h' : '?'} · debugfs={wakeupSourcesAuditData.debugfs_readable ? 'readable' : 'root-only'}</p>
            <p style="margin: 4px 0;">{wakeupSourcesAuditData.verdict.reason}</p>
            {#if wakeupSourcesAuditData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.wakeup.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{wakeupSourcesAuditData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(wakeupSourcesAuditData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #57.1 Live patches (UI sprint 48) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.livepatch.title")}</h4>
        <p class="muted">{i18n.t("integrations.livepatch.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadLivepatchAudit}>{i18n.t("integrations.livepatch.refresh")}</button>
          {#if livepatchAuditData?.ok}
            <span class="kv" style="margin-left: 12px;"
                  style:color={['stuck_transition','unsigned_patch'].includes(livepatchAuditData.verdict.verdict) ? 'var(--warn)' :
                             livepatchAuditData.verdict.verdict === 'disabled_patch' ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {livepatchAuditData.patch_count ?? 0} patches · {i18n.t("integrations.livepatch.verdict")} : <b>{livepatchAuditData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if livepatchAuditData?.ok}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        ['stuck_transition','unsigned_patch'].includes(livepatchAuditData.verdict.verdict) ? 'var(--warn)' :
                        livepatchAuditData.verdict.verdict === 'disabled_patch' ? 'var(--accent)' :
                        'var(--text-dim)'};">
            {#if livepatchAuditData.patches.length}
              <details style="margin-top: 4px;">
                <summary class="muted">Patches</summary>
                <ul style="font-size: 0.85em; margin: 4px 0; padding-left: 20px;">
                  {#each livepatchAuditData.patches as p}
                    <li>{p.name}: enabled={p.enabled ?? '?'} transition={p.transition ?? '?'} signed={p.has_signature ? 'yes' : 'no'}</li>
                  {/each}
                </ul>
              </details>
            {/if}
            <p style="margin: 4px 0;">{livepatchAuditData.verdict.reason}</p>
            {#if livepatchAuditData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.livepatch.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{livepatchAuditData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(livepatchAuditData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #57.2 pagetypeinfo (UI sprint 48) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.pageti.title")}</h4>
        <p class="muted">{i18n.t("integrations.pageti.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadPagetypeinfoAudit}>{i18n.t("integrations.pageti.refresh")}</button>
          {#if pagetypeinfoAuditData?.ok}
            <span class="kv" style="margin-left: 12px;"
                  style:color={['unmovable_in_movable','high_order_starved'].includes(pagetypeinfoAuditData.verdict.verdict) ? 'var(--warn)' :
                             ['moderate_frag','requires_root'].includes(pagetypeinfoAuditData.verdict.verdict) ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {i18n.t("integrations.pageti.verdict")} : <b>{pagetypeinfoAuditData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if pagetypeinfoAuditData?.ok}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        ['unmovable_in_movable','high_order_starved'].includes(pagetypeinfoAuditData.verdict.verdict) ? 'var(--warn)' :
                        ['moderate_frag','requires_root'].includes(pagetypeinfoAuditData.verdict.verdict) ? 'var(--accent)' :
                        'var(--text-dim)'};">
            <p style="margin: 4px 0;">free_page_rows={pagetypeinfoAuditData.free_page_rows} · block_rows={pagetypeinfoAuditData.block_rows} · extfrag_threshold={pagetypeinfoAuditData.extfrag_threshold ?? '?'}</p>
            <p style="margin: 4px 0;">{pagetypeinfoAuditData.verdict.reason}</p>
            {#if pagetypeinfoAuditData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.pageti.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{pagetypeinfoAuditData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(pagetypeinfoAuditData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #57.3 Backlight + PWM (UI sprint 48) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.blpwm.title")}</h4>
        <p class="muted">{i18n.t("integrations.blpwm.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadBacklightPwmAudit}>{i18n.t("integrations.blpwm.refresh")}</button>
          {#if backlightPwmAuditData?.ok}
            <span class="kv" style="margin-left: 12px;"
                  style:color={['panel_blanked','backlight_zero'].includes(backlightPwmAuditData.verdict.verdict) ? 'var(--warn)' :
                             backlightPwmAuditData.verdict.verdict === 'pwm_runaway' ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {backlightPwmAuditData.backlight_count ?? 0} bl / {backlightPwmAuditData.pwm_chip_count ?? 0} pwm · {i18n.t("integrations.blpwm.verdict")} : <b>{backlightPwmAuditData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if backlightPwmAuditData?.ok}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        ['panel_blanked','backlight_zero'].includes(backlightPwmAuditData.verdict.verdict) ? 'var(--warn)' :
                        backlightPwmAuditData.verdict.verdict === 'pwm_runaway' ? 'var(--accent)' :
                        'var(--text-dim)'};">
            {#if backlightPwmAuditData.backlights.length || backlightPwmAuditData.pwm_chips.length}
              <details style="margin-top: 4px;">
                <summary class="muted">Backlights + PWM chips</summary>
                <ul style="font-size: 0.85em; margin: 4px 0; padding-left: 20px;">
                  {#each backlightPwmAuditData.backlights as b}
                    <li>bl {b.name}: brightness={b.brightness}/{b.max_brightness} bl_power={b.bl_power} type={b.type ?? '?'}</li>
                  {/each}
                  {#each backlightPwmAuditData.pwm_chips as c}
                    <li>pwm {c.name}: npwm={c.npwm} channels={c.channels.length}</li>
                  {/each}
                </ul>
              </details>
            {/if}
            <p style="margin: 4px 0;">{backlightPwmAuditData.verdict.reason}</p>
            {#if backlightPwmAuditData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.blpwm.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{backlightPwmAuditData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(backlightPwmAuditData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #57.4 Loadavg pressure (UI sprint 48) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.loadavg.title")}</h4>
        <p class="muted">{i18n.t("integrations.loadavg.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadLoadavgPressureAudit}>{i18n.t("integrations.loadavg.refresh")}</button>
          {#if loadavgPressureAuditData?.ok}
            <span class="kv" style="margin-left: 12px;"
                  style:color={['rt_throttle_disabled','D_state_storm'].includes(loadavgPressureAuditData.verdict.verdict) ? 'var(--warn)' :
                             loadavgPressureAuditData.verdict.verdict === 'overcommitted' ? 'var(--accent)' :
                             'var(--text-dim)'}>
              la={loadavgPressureAuditData.loadavg_1m?.toFixed(2)} on {loadavgPressureAuditData.nr_cpus} CPUs · {i18n.t("integrations.loadavg.verdict")} : <b>{loadavgPressureAuditData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if loadavgPressureAuditData?.ok}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        ['rt_throttle_disabled','D_state_storm'].includes(loadavgPressureAuditData.verdict.verdict) ? 'var(--warn)' :
                        loadavgPressureAuditData.verdict.verdict === 'overcommitted' ? 'var(--accent)' :
                        'var(--text-dim)'};">
            <ul style="font-size: 0.85em; margin: 4px 0; padding-left: 20px;">
              <li>loadavg: {loadavgPressureAuditData.loadavg_1m?.toFixed(2)} / {loadavgPressureAuditData.loadavg_5m?.toFixed(2)} / {loadavgPressureAuditData.loadavg_15m?.toFixed(2)}</li>
              <li>procs: running={loadavgPressureAuditData.procs_running} blocked={loadavgPressureAuditData.procs_blocked}</li>
              <li>RT throttle: runtime={loadavgPressureAuditData.sched_rt_runtime_us ?? '?'} period={loadavgPressureAuditData.sched_rt_period_us ?? '?'}</li>
            </ul>
            <p style="margin: 4px 0;">{loadavgPressureAuditData.verdict.reason}</p>
            {#if loadavgPressureAuditData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.loadavg.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{loadavgPressureAuditData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(loadavgPressureAuditData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #58.1 cgroup v2 root (UI sprint 49) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.cgroot.title")}</h4>
        <p class="muted">{i18n.t("integrations.cgroot.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadCgroupRootAudit}>{i18n.t("integrations.cgroot.refresh")}</button>
          {#if cgroupRootAuditData?.ok}
            <span class="kv" style="margin-left: 12px;"
                  style:color={['hybrid_v1_v2','missing_controllers'].includes(cgroupRootAuditData.verdict.verdict) ? 'var(--warn)' :
                             cgroupRootAuditData.verdict.verdict === 'deep_nesting' ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {cgroupRootAuditData.controllers.length} controllers · {i18n.t("integrations.cgroot.verdict")} : <b>{cgroupRootAuditData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if cgroupRootAuditData?.ok}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        ['hybrid_v1_v2','missing_controllers'].includes(cgroupRootAuditData.verdict.verdict) ? 'var(--warn)' :
                        cgroupRootAuditData.verdict.verdict === 'deep_nesting' ? 'var(--accent)' :
                        'var(--text-dim)'};">
            <ul style="font-size: 0.85em; margin: 4px 0; padding-left: 20px;">
              <li>controllers: {cgroupRootAuditData.controllers.join(', ')}</li>
              <li>subtree_control: {cgroupRootAuditData.subtree_control.join(', ')}</li>
              {#if cgroupRootAuditData.hybrid_v1_dirs.length}
                <li style:color="var(--warn)">v1 dirs: {cgroupRootAuditData.hybrid_v1_dirs.join(', ')}</li>
              {/if}
              <li>own path: {cgroupRootAuditData.own_cgroup_path}</li>
              <li>descendants: {cgroupRootAuditData.stat.nr_descendants ?? '?'} · dying: {cgroupRootAuditData.stat.nr_dying_descendants ?? '?'}</li>
            </ul>
            <p style="margin: 4px 0;">{cgroupRootAuditData.verdict.reason}</p>
            {#if cgroupRootAuditData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.cgroot.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{cgroupRootAuditData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(cgroupRootAuditData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #58.2 Kernel build config (UI sprint 49) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.kcfg.title")}</h4>
        <p class="muted">{i18n.t("integrations.kcfg.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadKernelBuildConfigAudit}>{i18n.t("integrations.kcfg.refresh")}</button>
          {#if kernelBuildConfigAuditData?.ok}
            <span class="kv" style="margin-left: 12px;"
                  style:color={['debug_kernel_in_use','preempt_none_for_desktop'].includes(kernelBuildConfigAuditData.verdict.verdict) ? 'var(--warn)' :
                             kernelBuildConfigAuditData.verdict.verdict === 'thp_madvise_default_mismatch' ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {kernelBuildConfigAuditData.release} · {i18n.t("integrations.kcfg.verdict")} : <b>{kernelBuildConfigAuditData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if kernelBuildConfigAuditData?.ok}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        ['debug_kernel_in_use','preempt_none_for_desktop'].includes(kernelBuildConfigAuditData.verdict.verdict) ? 'var(--warn)' :
                        kernelBuildConfigAuditData.verdict.verdict === 'thp_madvise_default_mismatch' ? 'var(--accent)' :
                        'var(--text-dim)'};">
            <details style="margin-top: 4px;">
              <summary class="muted">CONFIG keys ({kernelBuildConfigAuditData.key_count})</summary>
              <ul style="font-size: 0.85em; margin: 4px 0; padding-left: 20px;">
                {#each Object.entries(kernelBuildConfigAuditData.interesting) as [k, v]}
                  <li>{k}={v}</li>
                {/each}
              </ul>
            </details>
            <p style="margin: 4px 0;">{kernelBuildConfigAuditData.verdict.reason}</p>
            {#if kernelBuildConfigAuditData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.kcfg.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{kernelBuildConfigAuditData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(kernelBuildConfigAuditData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #58.3 SCSI transport (UI sprint 49) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.scsi.title")}</h4>
        <p class="muted">{i18n.t("integrations.scsi.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadScsiTransportAudit}>{i18n.t("integrations.scsi.refresh")}</button>
          {#if scsiTransportAuditData?.ok}
            <span class="kv" style="margin-left: 12px;"
                  style:color={scsiTransportAuditData.verdict.verdict === 'write_cache_disabled' ? 'var(--warn)' :
                             ['queue_depth_starved','device_offline'].includes(scsiTransportAuditData.verdict.verdict) ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {scsiTransportAuditData.disk_count ?? 0} disks · {scsiTransportAuditData.device_count ?? 0} devs · {i18n.t("integrations.scsi.verdict")} : <b>{scsiTransportAuditData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if scsiTransportAuditData?.ok}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        scsiTransportAuditData.verdict.verdict === 'write_cache_disabled' ? 'var(--warn)' :
                        ['queue_depth_starved','device_offline'].includes(scsiTransportAuditData.verdict.verdict) ? 'var(--accent)' :
                        'var(--text-dim)'};">
            {#if scsiTransportAuditData.disks.length || scsiTransportAuditData.devices.length}
              <details style="margin-top: 4px;">
                <summary class="muted">SCSI disks + devices</summary>
                <ul style="font-size: 0.85em; margin: 4px 0; padding-left: 20px;">
                  {#each scsiTransportAuditData.disks as d}
                    <li>disk {d.id}: cache_type={d.cache_type ?? '?'} FUA={d.FUA ?? '?'}</li>
                  {/each}
                  {#each scsiTransportAuditData.devices as d}
                    <li>dev {d.id}: queue_depth={d.queue_depth ?? '?'} state={d.state ?? '?'} type={d.type ?? '?'}</li>
                  {/each}
                </ul>
              </details>
            {/if}
            <p style="margin: 4px 0;">{scsiTransportAuditData.verdict.reason}</p>
            {#if scsiTransportAuditData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.scsi.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{scsiTransportAuditData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(scsiTransportAuditData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #58.4 ALSA cards (UI sprint 49) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.alsa.title")}</h4>
        <p class="muted">{i18n.t("integrations.alsa.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadAlsaCardsAudit}>{i18n.t("integrations.alsa.refresh")}</button>
          {#if alsaCardsAuditData?.ok}
            <span class="kv" style="margin-left: 12px;"
                  style:color={alsaCardsAuditData.verdict.verdict === 'gpu_hda_runtime_pm_off' ? 'var(--warn)' :
                             ['orphan_hdmi_audio','conflicting_codec_drivers'].includes(alsaCardsAuditData.verdict.verdict) ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {alsaCardsAuditData.card_count ?? 0} cards · {i18n.t("integrations.alsa.verdict")} : <b>{alsaCardsAuditData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if alsaCardsAuditData?.ok}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        alsaCardsAuditData.verdict.verdict === 'gpu_hda_runtime_pm_off' ? 'var(--warn)' :
                        ['orphan_hdmi_audio','conflicting_codec_drivers'].includes(alsaCardsAuditData.verdict.verdict) ? 'var(--accent)' :
                        'var(--text-dim)'};">
            {#if alsaCardsAuditData.cards.length}
              <details style="margin-top: 4px;">
                <summary class="muted">Cards</summary>
                <ul style="font-size: 0.85em; margin: 4px 0; padding-left: 20px;">
                  {#each alsaCardsAuditData.cards as c}
                    <li>card{c.index} {c.id} ({c.name}): pm=<b
                      style:color={c.id.toLowerCase().includes('nvidia') && c.power_control !== 'auto' ? 'var(--warn)' : 'inherit'}
                    >{c.power_control ?? '?'}</b> · pcms={c.pcm_children.length}</li>
                  {/each}
                </ul>
              </details>
            {/if}
            <p style="margin: 4px 0;">{alsaCardsAuditData.verdict.reason}</p>
            {#if alsaCardsAuditData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.alsa.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{alsaCardsAuditData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(alsaCardsAuditData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #59.1 DMI / SMBIOS (UI sprint 50) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.dmi.title")}</h4>
        <p class="muted">{i18n.t("integrations.dmi.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadDmiSmbiosAudit}>{i18n.t("integrations.dmi.refresh")}</button>
          {#if dmiSmbiosAuditData?.ok}
            <span class="kv" style="margin-left: 12px;"
                  style:color={dmiSmbiosAuditData.verdict.verdict === 'bios_stale_gt_3y' ? 'var(--warn)' :
                             ['qemu_or_vm_detected','board_unknown','dmi_absent'].includes(dmiSmbiosAuditData.verdict.verdict) ? 'var(--accent)' :
                             'var(--text-dim)'}>
              vm={dmiSmbiosAuditData.is_vm ? 'yes' : 'no'} · {i18n.t("integrations.dmi.verdict")} : <b>{dmiSmbiosAuditData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if dmiSmbiosAuditData?.ok}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        dmiSmbiosAuditData.verdict.verdict === 'bios_stale_gt_3y' ? 'var(--warn)' :
                        ['qemu_or_vm_detected','board_unknown','dmi_absent'].includes(dmiSmbiosAuditData.verdict.verdict) ? 'var(--accent)' :
                        'var(--text-dim)'};">
            <ul style="font-size: 0.85em; margin: 4px 0; padding-left: 20px;">
              <li>vendor: {dmiSmbiosAuditData.dmi.sys_vendor ?? '?'}</li>
              <li>product: {dmiSmbiosAuditData.dmi.product_name ?? '?'}</li>
              <li>BIOS: {dmiSmbiosAuditData.dmi.bios_vendor ?? '?'} {dmiSmbiosAuditData.dmi.bios_version ?? ''} ({dmiSmbiosAuditData.dmi.bios_date ?? '?'})</li>
              <li>board: {dmiSmbiosAuditData.dmi.board_vendor ?? '?'} {dmiSmbiosAuditData.dmi.board_name ?? ''}</li>
            </ul>
            <p style="margin: 4px 0;">{dmiSmbiosAuditData.verdict.reason}</p>
            {#if dmiSmbiosAuditData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.dmi.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{dmiSmbiosAuditData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(dmiSmbiosAuditData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #59.2 IOMMU groups (UI sprint 50) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.iommu.title")}</h4>
        <p class="muted">{i18n.t("integrations.iommu.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadIommuGroupsAudit}>{i18n.t("integrations.iommu.refresh")}</button>
          {#if iommuGroupsAuditData?.ok || iommuGroupsAuditData}
            <span class="kv" style="margin-left: 12px;"
                  style:color={iommuGroupsAuditData?.verdict.verdict === 'gpu_shares_group_with_root_complex' ? 'var(--warn)' :
                             ['iommu_disabled','passthrough_off'].includes(iommuGroupsAuditData?.verdict.verdict ?? '') ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {iommuGroupsAuditData?.group_count ?? 0} groups · {i18n.t("integrations.iommu.verdict")} : <b>{iommuGroupsAuditData?.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if iommuGroupsAuditData}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        iommuGroupsAuditData.verdict.verdict === 'gpu_shares_group_with_root_complex' ? 'var(--warn)' :
                        ['iommu_disabled','passthrough_off'].includes(iommuGroupsAuditData.verdict.verdict) ? 'var(--accent)' :
                        'var(--text-dim)'};">
            <ul style="font-size: 0.85em; margin: 4px 0; padding-left: 20px;">
              <li>cmdline iommu tokens: {iommuGroupsAuditData.iommu_cmdline_tokens.length ? iommuGroupsAuditData.iommu_cmdline_tokens.join(' ') : 'none'}</li>
              {#each iommuGroupsAuditData.nvidia_gpus as g}
                <li>NVIDIA GPU {g}: group {iommuGroupsAuditData.gpu_groups[g] ?? '?'}</li>
              {/each}
            </ul>
            <p style="margin: 4px 0;">{iommuGroupsAuditData.verdict.reason}</p>
            {#if iommuGroupsAuditData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.iommu.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{iommuGroupsAuditData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(iommuGroupsAuditData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #59.4 PID rlimits (UI sprint 50) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.rlim.title")}</h4>
        <p class="muted">{i18n.t("integrations.rlim.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadPidRlimitsAudit}>{i18n.t("integrations.rlim.refresh")}</button>
          {#if pidRlimitsAuditData?.ok}
            <span class="kv" style="margin-left: 12px;"
                  style:color={['memlock_too_low_for_mmap_lock','nofile_lt_4096','as_capped'].includes(pidRlimitsAuditData.verdict.verdict) ? 'var(--warn)' :
                             pidRlimitsAuditData.verdict.verdict === 'nproc_capped' ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {pidRlimitsAuditData.candidate_count} candidates · {i18n.t("integrations.rlim.verdict")} : <b>{pidRlimitsAuditData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if pidRlimitsAuditData?.ok}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        ['memlock_too_low_for_mmap_lock','nofile_lt_4096','as_capped'].includes(pidRlimitsAuditData.verdict.verdict) ? 'var(--warn)' :
                        pidRlimitsAuditData.verdict.verdict === 'nproc_capped' ? 'var(--accent)' :
                        'var(--text-dim)'};">
            <details style="margin-top: 4px;">
              <summary class="muted">Candidates</summary>
              <ul style="font-size: 0.85em; margin: 4px 0; padding-left: 20px;">
                {#each pidRlimitsAuditData.candidates as c}
                  <li>{c.comm} (pid {c.pid}):
                    memlock={c['Max locked memory']?.[0] ?? '?'} ·
                    nofile={c['Max open files']?.[0] ?? '?'} ·
                    AS={c['Max address space']?.[0] ?? '?'}
                  </li>
                {/each}
              </ul>
            </details>
            <p style="margin: 4px 0;">{pidRlimitsAuditData.verdict.reason}</p>
            {#if pidRlimitsAuditData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.rlim.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{pidRlimitsAuditData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(pidRlimitsAuditData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #60.4 Virt guest detection (UI sprint 51) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.virt.title")}</h4>
        <p class="muted">{i18n.t("integrations.virt.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadVirtGuestDetectAudit}>{i18n.t("integrations.virt.refresh")}</button>
          {#if virtGuestDetectAuditData?.ok}
            <span class="kv" style="margin-left: 12px;"
                  style:color={virtGuestDetectAuditData.verdict.verdict.startsWith('running_as_kvm_guest_with_nvidia') ? 'var(--warn)' :
                             ['nested_virt_detected','running_as_guest_generic'].includes(virtGuestDetectAuditData.verdict.verdict) ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {i18n.t("integrations.virt.verdict")} : <b>{virtGuestDetectAuditData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if virtGuestDetectAuditData?.ok}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        virtGuestDetectAuditData.verdict.verdict.startsWith('running_as_kvm_guest_with_nvidia') ? 'var(--warn)' :
                        ['nested_virt_detected','running_as_guest_generic'].includes(virtGuestDetectAuditData.verdict.verdict) ? 'var(--accent)' :
                        'var(--text-dim)'};">
            <ul style="font-size: 0.85em; margin: 4px 0; padding-left: 20px;">
              <li>qemu_fw_cfg: {virtGuestDetectAuditData.qemu_fw_cfg_present ? 'yes' : 'no'}
                · cpu hypervisor flag: {virtGuestDetectAuditData.cpu_hypervisor_flag ? 'yes' : 'no'}
                · xen: {virtGuestDetectAuditData.xen_type ?? 'no'}
              </li>
              <li>virtio devices: {virtGuestDetectAuditData.virtio_device_count} · kvm loaded: {virtGuestDetectAuditData.kvm_loaded ? 'yes' : 'no'}</li>
              {#if virtGuestDetectAuditData.nvidia_gpus.length}
                <li>NVIDIA GPUs on PCI bus: {virtGuestDetectAuditData.nvidia_gpus.join(', ')}</li>
              {/if}
            </ul>
            <p style="margin: 4px 0;">{virtGuestDetectAuditData.verdict.reason}</p>
            {#if virtGuestDetectAuditData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.virt.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{virtGuestDetectAuditData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(virtGuestDetectAuditData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #61.3 Voltage regulator (UI sprint 52) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.regul.title")}</h4>
        <p class="muted">{i18n.t("integrations.regul.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadRegulatorAudit}>{i18n.t("integrations.regul.refresh")}</button>
          {#if regulatorAuditData?.ok}
            <span class="kv" style="margin-left: 12px;"
                  style:color={regulatorAuditData.verdict.verdict === 'orphan' ? 'var(--warn)' :
                             ['disabled_with_users','drifted_suspend_state'].includes(regulatorAuditData.verdict.verdict) ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {regulatorAuditData.regulator_count} regulators · {i18n.t("integrations.regul.verdict")} : <b>{regulatorAuditData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if regulatorAuditData?.ok}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        regulatorAuditData.verdict.verdict === 'orphan' ? 'var(--warn)' :
                        ['disabled_with_users','drifted_suspend_state'].includes(regulatorAuditData.verdict.verdict) ? 'var(--accent)' :
                        'var(--text-dim)'};">
            {#if regulatorAuditData.regulators.length}
              <details style="margin-top: 4px;">
                <summary class="muted">Regulators</summary>
                <ul style="font-size: 0.85em; margin: 4px 0; padding-left: 20px;">
                  {#each regulatorAuditData.regulators as r}
                    <li>{r.id} ({r.name ?? '?'}): users={r.num_users ?? '?'} µA={r.requested_microamps ?? '?'} runtime={r.runtime_status ?? '?'}</li>
                  {/each}
                </ul>
              </details>
            {/if}
            <p style="margin: 4px 0;">{regulatorAuditData.verdict.reason}</p>
            {#if regulatorAuditData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.regul.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{regulatorAuditData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(regulatorAuditData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #61.4 ALSA codec deep (UI sprint 52) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.codec.title")}</h4>
        <p class="muted">{i18n.t("integrations.codec.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadAlsaCodecDeepAudit}>{i18n.t("integrations.codec.refresh")}</button>
          {#if alsaCodecDeepAuditData?.ok}
            <span class="kv" style="margin-left: 12px;"
                  style:color={alsaCodecDeepAuditData.verdict.verdict === 'codec_powered_when_idle' ? 'var(--warn)' :
                             alsaCodecDeepAuditData.verdict.verdict === 'stuck_runtime' ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {alsaCodecDeepAuditData.codec_count} codecs · {i18n.t("integrations.codec.verdict")} : <b>{alsaCodecDeepAuditData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if alsaCodecDeepAuditData?.ok}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        alsaCodecDeepAuditData.verdict.verdict === 'codec_powered_when_idle' ? 'var(--warn)' :
                        alsaCodecDeepAuditData.verdict.verdict === 'stuck_runtime' ? 'var(--accent)' :
                        'var(--text-dim)'};">
            {#if alsaCodecDeepAuditData.codecs.length}
              <details style="margin-top: 4px;">
                <summary class="muted">Codecs</summary>
                <ul style="font-size: 0.85em; margin: 4px 0; padding-left: 20px;">
                  {#each alsaCodecDeepAuditData.codecs as c}
                    <li>card{c.card_index} {c.name ?? '?'}: power=<b
                      style:color={c.power_setting && c.power_setting !== 'D3' && !alsaCodecDeepAuditData!.pcm_open_per_card[String(c.card_index)] ? 'var(--warn)' : 'inherit'}
                    >{c.power_setting ?? '?'}</b>→{c.power_actual ?? '?'} · pcm_open={alsaCodecDeepAuditData.pcm_open_per_card[String(c.card_index)] ? 'yes' : 'no'} · pins={c.pins.length}</li>
                  {/each}
                </ul>
              </details>
            {/if}
            <p style="margin: 4px 0;">{alsaCodecDeepAuditData.verdict.reason}</p>
            {#if alsaCodecDeepAuditData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.codec.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{alsaCodecDeepAuditData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(alsaCodecDeepAuditData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #62.1 devfreq (UI sprint 53) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.devfreq.title")}</h4>
        <p class="muted">{i18n.t("integrations.devfreq.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadDevfreqAudit}>{i18n.t("integrations.devfreq.refresh")}</button>
          {#if devfreqAuditData?.ok}
            <span class="kv" style="margin-left: 12px;"
                  style:color={['stuck_min','stuck_max'].includes(devfreqAuditData.verdict.verdict) ? 'var(--warn)' :
                             ['userspace_governor','pinned_perf'].includes(devfreqAuditData.verdict.verdict) ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {devfreqAuditData.device_count} devs · {i18n.t("integrations.devfreq.verdict")} : <b>{devfreqAuditData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if devfreqAuditData?.ok}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        ['stuck_min','stuck_max'].includes(devfreqAuditData.verdict.verdict) ? 'var(--warn)' :
                        ['userspace_governor','pinned_perf'].includes(devfreqAuditData.verdict.verdict) ? 'var(--accent)' :
                        'var(--text-dim)'};">
            {#if devfreqAuditData.devices.length}
              <details style="margin-top: 4px;">
                <summary class="muted">Devices</summary>
                <ul style="font-size: 0.85em; margin: 4px 0; padding-left: 20px;">
                  {#each devfreqAuditData.devices as d}
                    <li>{d.name}: gov={d.governor ?? '?'} cur={d.cur_freq ?? '?'} min/max={d.min_freq ?? '?'}/{d.max_freq ?? '?'}</li>
                  {/each}
                </ul>
              </details>
            {/if}
            <p style="margin: 4px 0;">{devfreqAuditData.verdict.reason}</p>
            {#if devfreqAuditData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.devfreq.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{devfreqAuditData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(devfreqAuditData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #62.2 Intel ME / MEI (UI sprint 53) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.mei.title")}</h4>
        <p class="muted">{i18n.t("integrations.mei.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadMeiIntelMeAudit}>{i18n.t("integrations.mei.refresh")}</button>
          {#if meiIntelMeAuditData?.ok || meiIntelMeAuditData}
            <span class="kv" style="margin-left: 12px;"
                  style:color={meiIntelMeAuditData?.verdict.verdict === 'me_recovery_mode' ? 'var(--warn)' :
                             ['me_disabled_but_present','fw_status_error'].includes(meiIntelMeAuditData?.verdict.verdict ?? '') ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {meiIntelMeAuditData?.device_count ?? 0} mei · {i18n.t("integrations.mei.verdict")} : <b>{meiIntelMeAuditData?.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if meiIntelMeAuditData}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        meiIntelMeAuditData.verdict.verdict === 'me_recovery_mode' ? 'var(--warn)' :
                        ['me_disabled_but_present','fw_status_error'].includes(meiIntelMeAuditData.verdict.verdict) ? 'var(--accent)' :
                        'var(--text-dim)'};">
            {#if meiIntelMeAuditData.devices.length}
              <details style="margin-top: 4px;">
                <summary class="muted">MEI devices</summary>
                <ul style="font-size: 0.85em; margin: 4px 0; padding-left: 20px;">
                  {#each meiIntelMeAuditData.devices as d}
                    <li>{d.id}: fw_ver={d.fw_ver ?? '?'} dev_state={d.dev_state ?? '?'} fw_status={d.fw_status ?? '?'}</li>
                  {/each}
                </ul>
              </details>
            {/if}
            <p style="margin: 4px 0;">{meiIntelMeAuditData.verdict.reason}</p>
            {#if meiIntelMeAuditData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.mei.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{meiIntelMeAuditData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(meiIntelMeAuditData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #62.3 Memory hotplug (UI sprint 53) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.memhp.title")}</h4>
        <p class="muted">{i18n.t("integrations.memhp.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadMemoryHotplugAudit}>{i18n.t("integrations.memhp.refresh")}</button>
          {#if memoryHotplugAuditData?.ok}
            <span class="kv" style="margin-left: 12px;"
                  style:color={['offline_blocks_present','non_removable_in_movable'].includes(memoryHotplugAuditData.verdict.verdict) ? 'var(--warn)' :
                             memoryHotplugAuditData.verdict.verdict === 'movable_only_zone_skew' ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {memoryHotplugAuditData.block_count} blocks · {i18n.t("integrations.memhp.verdict")} : <b>{memoryHotplugAuditData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if memoryHotplugAuditData?.ok}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        ['offline_blocks_present','non_removable_in_movable'].includes(memoryHotplugAuditData.verdict.verdict) ? 'var(--warn)' :
                        memoryHotplugAuditData.verdict.verdict === 'movable_only_zone_skew' ? 'var(--accent)' :
                        'var(--text-dim)'};">
            <ul style="font-size: 0.85em; margin: 4px 0; padding-left: 20px;">
              <li>block_size: {memoryHotplugAuditData.block_size_bytes ?? '?'} bytes · MemTotal: {memoryHotplugAuditData.mem_total_kib != null ? (memoryHotplugAuditData.mem_total_kib / 1024 / 1024).toFixed(1) + ' GiB' : '?'}</li>
              {#if memoryHotplugAuditData.blocks_sample.length}
                <li>Sample blocks: {memoryHotplugAuditData.blocks_sample.slice(0, 3).map(b => b.id + '(' + b.state + ')').join(', ')}</li>
              {/if}
            </ul>
            <p style="margin: 4px 0;">{memoryHotplugAuditData.verdict.reason}</p>
            {#if memoryHotplugAuditData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.memhp.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{memoryHotplugAuditData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(memoryHotplugAuditData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #62.4 Task affinity (UI sprint 53) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.taskaff.title")}</h4>
        <p class="muted">{i18n.t("integrations.taskaff.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadProcTaskAffinityAudit}>{i18n.t("integrations.taskaff.refresh")}</button>
          {#if procTaskAffinityAuditData?.ok}
            <span class="kv" style="margin-left: 12px;"
                  style:color={['affinity_excludes_local_numa','mems_allowed_remote_only'].includes(procTaskAffinityAuditData.verdict.verdict) ? 'var(--warn)' :
                             procTaskAffinityAuditData.verdict.verdict === 'narrow_cpuset_high_nvcs' ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {procTaskAffinityAuditData.candidate_count} candidates · {i18n.t("integrations.taskaff.verdict")} : <b>{procTaskAffinityAuditData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if procTaskAffinityAuditData?.ok}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        ['affinity_excludes_local_numa','mems_allowed_remote_only'].includes(procTaskAffinityAuditData.verdict.verdict) ? 'var(--warn)' :
                        procTaskAffinityAuditData.verdict.verdict === 'narrow_cpuset_high_nvcs' ? 'var(--accent)' :
                        'var(--text-dim)'};">
            <details style="margin-top: 4px;">
              <summary class="muted">Candidates + GPUs</summary>
              <ul style="font-size: 0.85em; margin: 4px 0; padding-left: 20px;">
                {#each procTaskAffinityAuditData.candidates as c}
                  <li>{c.comm} (pid {c.pid}): cpus={c.Cpus_allowed_list ?? '?'} mems={c.Mems_allowed_list ?? '?'} nvcs={c.nonvoluntary_ctxt_switches ?? '?'}</li>
                {/each}
                {#each procTaskAffinityAuditData.gpus as g}
                  <li>GPU {g.bdf}: local_cpulist={g.local_cpulist} numa={g.numa_node}</li>
                {/each}
              </ul>
            </details>
            <p style="margin: 4px 0;">{procTaskAffinityAuditData.verdict.reason}</p>
            {#if procTaskAffinityAuditData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.taskaff.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{procTaskAffinityAuditData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(procTaskAffinityAuditData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #63.1 rfkill + Bluetooth (UI sprint 54) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.rfkill.title")}</h4>
        <p class="muted">{i18n.t("integrations.rfkill.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadRfkillBluetoothAudit}>{i18n.t("integrations.rfkill.refresh")}</button>
          {#if rfkillBluetoothAuditData?.ok}
            <span class="kv" style="margin-left: 12px;"
                  style:color={rfkillBluetoothAuditData.verdict.verdict === 'hw_kill_blocks' ? 'var(--warn)' :
                             ['soft_block_stuck','bt_autosuspend_churn'].includes(rfkillBluetoothAuditData.verdict.verdict) ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {rfkillBluetoothAuditData.rfkill_count} rfkill / {rfkillBluetoothAuditData.bluetooth_count} BT · {i18n.t("integrations.rfkill.verdict")} : <b>{rfkillBluetoothAuditData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if rfkillBluetoothAuditData?.ok}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        rfkillBluetoothAuditData.verdict.verdict === 'hw_kill_blocks' ? 'var(--warn)' :
                        ['soft_block_stuck','bt_autosuspend_churn'].includes(rfkillBluetoothAuditData.verdict.verdict) ? 'var(--accent)' :
                        'var(--text-dim)'};">
            <p style="margin: 4px 0;">{rfkillBluetoothAuditData.verdict.reason}</p>
            {#if rfkillBluetoothAuditData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.rfkill.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{rfkillBluetoothAuditData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(rfkillBluetoothAuditData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #63.3 LEDs (UI sprint 54) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.leds.title")}</h4>
        <p class="muted">{i18n.t("integrations.leds.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadLedsClassAudit}>{i18n.t("integrations.leds.refresh")}</button>
          {#if ledsClassAuditData?.ok}
            <span class="kv" style="margin-left: 12px;"
                  style:color={ledsClassAuditData.verdict.verdict === 'led_stuck_on' ? 'var(--warn)' :
                             ['led_flap','led_orphan'].includes(ledsClassAuditData.verdict.verdict) ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {ledsClassAuditData.led_count} LEDs · {i18n.t("integrations.leds.verdict")} : <b>{ledsClassAuditData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if ledsClassAuditData?.ok}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        ledsClassAuditData.verdict.verdict === 'led_stuck_on' ? 'var(--warn)' :
                        ['led_flap','led_orphan'].includes(ledsClassAuditData.verdict.verdict) ? 'var(--accent)' :
                        'var(--text-dim)'};">
            {#if ledsClassAuditData.leds.length}
              <details style="margin-top: 4px;">
                <summary class="muted">LEDs</summary>
                <ul style="font-size: 0.85em; margin: 4px 0; padding-left: 20px;">
                  {#each ledsClassAuditData.leds as l}
                    <li>{l.id}: trigger={l.active_trigger ?? '?'} brightness={l.brightness ?? '?'}/{l.max_brightness ?? '?'}</li>
                  {/each}
                </ul>
              </details>
            {/if}
            <p style="margin: 4px 0;">{ledsClassAuditData.verdict.reason}</p>
            {#if ledsClassAuditData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.leds.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{ledsClassAuditData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(ledsClassAuditData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #63.4 binfmt_misc (UI sprint 54) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.binfmt.title")}</h4>
        <p class="muted">{i18n.t("integrations.binfmt.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadBinfmtMiscAudit}>{i18n.t("integrations.binfmt.refresh")}</button>
          {#if binfmtMiscAuditData}
            <span class="kv" style="margin-left: 12px;"
                  style:color={binfmtMiscAuditData.verdict.verdict === 'qemu_user_interp_stale' ? 'var(--warn)' :
                             ['duplicate_registration','globally_disabled_with_buildx'].includes(binfmtMiscAuditData.verdict.verdict) ? 'var(--accent)' :
                             'var(--text-dim)'}>
              status={binfmtMiscAuditData.status_text ?? 'n/a'} · {binfmtMiscAuditData.registration_count ?? 0} regs · {i18n.t("integrations.binfmt.verdict")} : <b>{binfmtMiscAuditData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if binfmtMiscAuditData}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        binfmtMiscAuditData.verdict.verdict === 'qemu_user_interp_stale' ? 'var(--warn)' :
                        ['duplicate_registration','globally_disabled_with_buildx'].includes(binfmtMiscAuditData.verdict.verdict) ? 'var(--accent)' :
                        'var(--text-dim)'};">
            {#if binfmtMiscAuditData.registrations?.length}
              <details style="margin-top: 4px;">
                <summary class="muted">Registrations</summary>
                <ul style="font-size: 0.85em; margin: 4px 0; padding-left: 20px;">
                  {#each binfmtMiscAuditData.registrations as r}
                    <li>{r.name}: enabled={r.enabled ? 'yes' : 'no'} interpreter={r.interpreter ?? '?'} flags={r.flags ?? '-'}</li>
                  {/each}
                </ul>
              </details>
            {/if}
            <p style="margin: 4px 0;">{binfmtMiscAuditData.verdict.reason}</p>
            {#if binfmtMiscAuditData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.binfmt.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{binfmtMiscAuditData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(binfmtMiscAuditData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #63.2 PTP clocks (UI sprint 54) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.ptp.title")}</h4>
        <p class="muted">{i18n.t("integrations.ptp.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadPtpClockAudit}>{i18n.t("integrations.ptp.refresh")}</button>
          {#if ptpClockAuditData?.ok}
            <span class="kv" style="margin-left: 12px;"
                  style:color={ptpClockAuditData.verdict.verdict === 'max_adjustment_zero' ? 'var(--warn)' :
                             ptpClockAuditData.verdict.verdict === 'phc_unused' ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {ptpClockAuditData.phc_count} PHCs · {i18n.t("integrations.ptp.verdict")} : <b>{ptpClockAuditData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if ptpClockAuditData?.ok}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        ptpClockAuditData.verdict.verdict === 'max_adjustment_zero' ? 'var(--warn)' :
                        ptpClockAuditData.verdict.verdict === 'phc_unused' ? 'var(--accent)' :
                        'var(--text-dim)'};">
            {#if ptpClockAuditData.phcs.length}
              <details style="margin-top: 4px;">
                <summary class="muted">PHCs</summary>
                <ul style="font-size: 0.85em; margin: 4px 0; padding-left: 20px;">
                  {#each ptpClockAuditData.phcs as p}
                    <li>{p.id} ({p.clock_name ?? '?'}): max_adj={p.max_adjustment ?? '?'} pins={p.n_pins ?? '?'} pps={p.pps_available ?? '?'}</li>
                  {/each}
                </ul>
              </details>
            {/if}
            <p style="margin: 4px 0;">{ptpClockAuditData.verdict.reason}</p>
            {#if ptpClockAuditData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.ptp.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{ptpClockAuditData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(ptpClockAuditData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #64.2 MEI HDCP/PXP (UI sprint 55) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.mei2.title")}</h4>
        <p class="muted">{i18n.t("integrations.mei2.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadMeiHdcpPxpAudit}>{i18n.t("integrations.mei2.refresh")}</button>
          {#if meiHdcpPxpAuditData?.ok || meiHdcpPxpAuditData}
            <span class="kv" style="margin-left: 12px;"
                  style:color={meiHdcpPxpAuditData?.verdict.verdict === 'pxp_disabled_with_gpu' ? 'var(--warn)' :
                             ['hdcp_fw_mismatch','subclasses_no_consumer'].includes(meiHdcpPxpAuditData?.verdict.verdict ?? '') ? 'var(--accent)' :
                             'var(--text-dim)'}>
              hdcp={meiHdcpPxpAuditData?.hdcp_count ?? 0} pxp={meiHdcpPxpAuditData?.pxp_count ?? 0} · {i18n.t("integrations.mei2.verdict")} : <b>{meiHdcpPxpAuditData?.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if meiHdcpPxpAuditData}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        meiHdcpPxpAuditData.verdict.verdict === 'pxp_disabled_with_gpu' ? 'var(--warn)' :
                        ['hdcp_fw_mismatch','subclasses_no_consumer'].includes(meiHdcpPxpAuditData.verdict.verdict) ? 'var(--accent)' :
                        'var(--text-dim)'};">
            <p style="margin: 4px 0;">{meiHdcpPxpAuditData.verdict.reason}</p>
            {#if meiHdcpPxpAuditData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.mei2.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{meiHdcpPxpAuditData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(meiHdcpPxpAuditData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #64.4 EDD + MMC (UI sprint 55) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.eddmmc.title")}</h4>
        <p class="muted">{i18n.t("integrations.eddmmc.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadFirmwareEddMmcAudit}>{i18n.t("integrations.eddmmc.refresh")}</button>
          {#if firmwareEddMmcAuditData}
            <span class="kv" style="margin-left: 12px;"
                  style:color={firmwareEddMmcAuditData.verdict.verdict === 'emmc_wear_imminent' ? 'var(--warn)' :
                             ['edd_mbr_drift','mmc_clock_legacy'].includes(firmwareEddMmcAuditData.verdict.verdict) ? 'var(--accent)' :
                             'var(--text-dim)'}>
              edd={firmwareEddMmcAuditData.edd_count} mmc={firmwareEddMmcAuditData.mmc_count} · {i18n.t("integrations.eddmmc.verdict")} : <b>{firmwareEddMmcAuditData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if firmwareEddMmcAuditData}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        firmwareEddMmcAuditData.verdict.verdict === 'emmc_wear_imminent' ? 'var(--warn)' :
                        ['edd_mbr_drift','mmc_clock_legacy'].includes(firmwareEddMmcAuditData.verdict.verdict) ? 'var(--accent)' :
                        'var(--text-dim)'};">
            <p style="margin: 4px 0;">{firmwareEddMmcAuditData.verdict.reason}</p>
            {#if firmwareEddMmcAuditData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.eddmmc.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{firmwareEddMmcAuditData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(firmwareEddMmcAuditData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #64.1 devlink (UI sprint 55) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.devlink.title")}</h4>
        <p class="muted">{i18n.t("integrations.devlink.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadDevlinkSmartnicAudit}>{i18n.t("integrations.devlink.refresh")}</button>
          {#if devlinkSmartnicAuditData?.ok}
            <span class="kv" style="margin-left: 12px;"
                  style:color={devlinkSmartnicAuditData.verdict.verdict === 'supplier_not_ready' ? 'var(--warn)' :
                             ['consumer_unbinding','dormant_links_present'].includes(devlinkSmartnicAuditData.verdict.verdict) ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {devlinkSmartnicAuditData.link_count} links · {i18n.t("integrations.devlink.verdict")} : <b>{devlinkSmartnicAuditData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if devlinkSmartnicAuditData?.ok}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        devlinkSmartnicAuditData.verdict.verdict === 'supplier_not_ready' ? 'var(--warn)' :
                        ['consumer_unbinding','dormant_links_present'].includes(devlinkSmartnicAuditData.verdict.verdict) ? 'var(--accent)' :
                        'var(--text-dim)'};">
            <p style="margin: 4px 0;">Status histogram: {Object.entries(devlinkSmartnicAuditData.status_histogram).map(([k, v]) => k + '=' + v).join(', ')}</p>
            <p style="margin: 4px 0;">{devlinkSmartnicAuditData.verdict.reason}</p>
            {#if devlinkSmartnicAuditData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.devlink.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{devlinkSmartnicAuditData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(devlinkSmartnicAuditData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #64.3 proc_ns_mountinfo (UI sprint 55) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.procns.title")}</h4>
        <p class="muted">{i18n.t("integrations.procns.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadProcNsMountinfoAudit}>{i18n.t("integrations.procns.refresh")}</button>
          {#if procNsMountinfoAuditData?.ok}
            <span class="kv" style="margin-left: 12px;"
                  style:color={['cuda_pid_in_different_mnt_ns','netns_split_for_nccl'].includes(procNsMountinfoAuditData.verdict.verdict) ? 'var(--warn)' :
                             procNsMountinfoAuditData.verdict.verdict === 'nvidia_uvm_hidden_by_bind' ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {procNsMountinfoAuditData.candidate_count} candidates · {i18n.t("integrations.procns.verdict")} : <b>{procNsMountinfoAuditData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if procNsMountinfoAuditData?.ok}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        ['cuda_pid_in_different_mnt_ns','netns_split_for_nccl'].includes(procNsMountinfoAuditData.verdict.verdict) ? 'var(--warn)' :
                        procNsMountinfoAuditData.verdict.verdict === 'nvidia_uvm_hidden_by_bind' ? 'var(--accent)' :
                        'var(--text-dim)'};">
            <details style="margin-top: 4px;">
              <summary class="muted">Candidates + namespaces</summary>
              <ul style="font-size: 0.85em; margin: 4px 0; padding-left: 20px;">
                {#each procNsMountinfoAuditData.candidates as c}
                  <li>{c.comm} (pid {c.pid}): mnt={c.ns.mnt ?? '?'} net={c.ns.net ?? '?'} has_nvidia={c.has_nvidia ? 'yes' : 'no'}</li>
                {/each}
              </ul>
            </details>
            <p style="margin: 4px 0;">{procNsMountinfoAuditData.verdict.reason}</p>
            {#if procNsMountinfoAuditData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.procns.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{procNsMountinfoAuditData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(procNsMountinfoAuditData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #65.1 cpuidle residency (UI sprint 56) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.idleres.title")}</h4>
        <p class="muted">{i18n.t("integrations.idleres.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadCpuidleResidencyAudit}>{i18n.t("integrations.idleres.refresh")}</button>
          {#if cpuidleResidencyAuditData?.ok}
            <span class="kv" style="margin-left: 12px;"
                  style:color={['poll_dominant','c6_starved'].includes(cpuidleResidencyAuditData.verdict.verdict) ? 'var(--warn)' :
                             ['governor_mispredict','state_disabled_asymmetry'].includes(cpuidleResidencyAuditData.verdict.verdict) ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {cpuidleResidencyAuditData.cpu_count} CPUs · {i18n.t("integrations.idleres.verdict")} : <b>{cpuidleResidencyAuditData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if cpuidleResidencyAuditData?.ok}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        ['poll_dominant','c6_starved'].includes(cpuidleResidencyAuditData.verdict.verdict) ? 'var(--warn)' :
                        ['governor_mispredict','state_disabled_asymmetry'].includes(cpuidleResidencyAuditData.verdict.verdict) ? 'var(--accent)' :
                        'var(--text-dim)'};">
            <p style="margin: 4px 0;">{cpuidleResidencyAuditData.verdict.reason}</p>
            {#if cpuidleResidencyAuditData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.idleres.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{cpuidleResidencyAuditData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(cpuidleResidencyAuditData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #65.2 cpufreq residency (UI sprint 56) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.freqres.title")}</h4>
        <p class="muted">{i18n.t("integrations.freqres.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadCpufreqResidencyAudit}>{i18n.t("integrations.freqres.refresh")}</button>
          {#if cpufreqResidencyAuditData?.ok}
            <span class="kv" style="margin-left: 12px;"
                  style:color={['pinned_at_min','pinned_at_max'].includes(cpufreqResidencyAuditData.verdict.verdict) ? 'var(--warn)' :
                             ['transition_storm','boost_unreachable'].includes(cpufreqResidencyAuditData.verdict.verdict) ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {cpufreqResidencyAuditData.cpu_count} CPUs · {i18n.t("integrations.freqres.verdict")} : <b>{cpufreqResidencyAuditData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if cpufreqResidencyAuditData?.ok}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        ['pinned_at_min','pinned_at_max'].includes(cpufreqResidencyAuditData.verdict.verdict) ? 'var(--warn)' :
                        ['transition_storm','boost_unreachable'].includes(cpufreqResidencyAuditData.verdict.verdict) ? 'var(--accent)' :
                        'var(--text-dim)'};">
            <p style="margin: 4px 0;">{cpufreqResidencyAuditData.verdict.reason}</p>
            {#if cpufreqResidencyAuditData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.freqres.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{cpufreqResidencyAuditData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(cpufreqResidencyAuditData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #65.3 EFI runtime-map (UI sprint 56) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.efirt.title")}</h4>
        <p class="muted">{i18n.t("integrations.efirt.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadEfiRuntimeMapAudit}>{i18n.t("integrations.efirt.refresh")}</button>
          {#if efiRuntimeMapAuditData?.ok}
            <span class="kv" style="margin-left: 12px;"
                  style:color={['runtime_map_absent','runtime_pinned_large'].includes(efiRuntimeMapAuditData.verdict.verdict) ? 'var(--warn)' :
                             ['kexec_no_efi_rt','requires_root'].includes(efiRuntimeMapAuditData.verdict.verdict) ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {efiRuntimeMapAuditData.entry_count} entries · {i18n.t("integrations.efirt.verdict")} : <b>{efiRuntimeMapAuditData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if efiRuntimeMapAuditData?.ok}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        ['runtime_map_absent','runtime_pinned_large'].includes(efiRuntimeMapAuditData.verdict.verdict) ? 'var(--warn)' :
                        ['kexec_no_efi_rt','requires_root'].includes(efiRuntimeMapAuditData.verdict.verdict) ? 'var(--accent)' :
                        'var(--text-dim)'};">
            <p style="margin: 4px 0;">{efiRuntimeMapAuditData.verdict.reason}</p>
            {#if efiRuntimeMapAuditData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.efirt.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{efiRuntimeMapAuditData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(efiRuntimeMapAuditData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #65.4 devfreq event PMU (UI sprint 56) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.devfreqevt.title")}</h4>
        <p class="muted">{i18n.t("integrations.devfreqevt.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadDevfreqEventAudit}>{i18n.t("integrations.devfreqevt.refresh")}</button>
          {#if devfreqEventAuditData?.ok}
            <span class="kv" style="margin-left: 12px;"
                  style:color={['event_disabled','event_orphaned_governor'].includes(devfreqEventAuditData.verdict.verdict) ? 'var(--warn)' :
                             devfreqEventAuditData.verdict.verdict === 'class_absent' ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {devfreqEventAuditData.event_count} events · {i18n.t("integrations.devfreqevt.verdict")} : <b>{devfreqEventAuditData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if devfreqEventAuditData?.ok}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        ['event_disabled','event_orphaned_governor'].includes(devfreqEventAuditData.verdict.verdict) ? 'var(--warn)' :
                        devfreqEventAuditData.verdict.verdict === 'class_absent' ? 'var(--accent)' :
                        'var(--text-dim)'};">
            <p style="margin: 4px 0;">{devfreqEventAuditData.verdict.reason}</p>
            {#if devfreqEventAuditData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.devfreqevt.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{devfreqEventAuditData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(devfreqEventAuditData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #66.1 MTD flash (UI sprint 57) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.mtdflash.title")}</h4>
        <p class="muted">{i18n.t("integrations.mtdflash.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadMtdFlashAudit}>{i18n.t("integrations.mtdflash.refresh")}</button>
          {#if mtdFlashAuditData?.ok}
            <span class="kv" style="margin-left: 12px;"
                  style:color={['nor_bad_blocks','write_protect_drift'].includes(mtdFlashAuditData.verdict.verdict) ? 'var(--warn)' :
                             mtdFlashAuditData.verdict.verdict === 'unmapped_partition' ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {mtdFlashAuditData.mtd_count} MTD · {i18n.t("integrations.mtdflash.verdict")} : <b>{mtdFlashAuditData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if mtdFlashAuditData?.ok}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        ['nor_bad_blocks','write_protect_drift'].includes(mtdFlashAuditData.verdict.verdict) ? 'var(--warn)' :
                        mtdFlashAuditData.verdict.verdict === 'unmapped_partition' ? 'var(--accent)' :
                        'var(--text-dim)'};">
            <p style="margin: 4px 0;">{mtdFlashAuditData.verdict.reason}</p>
            {#if mtdFlashAuditData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.mtdflash.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{mtdFlashAuditData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(mtdFlashAuditData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #66.4 SPI + firmware loader (UI sprint 57) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.spifw.title")}</h4>
        <p class="muted">{i18n.t("integrations.spifw.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadSpiFirmwareLoaderAudit}>{i18n.t("integrations.spifw.refresh")}</button>
          {#if spiFirmwareLoaderAuditData?.ok}
            <span class="kv" style="margin-left: 12px;"
                  style:color={spiFirmwareLoaderAuditData.verdict.verdict === 'firmware_load_stuck' ? 'var(--warn)' :
                             ['profiling_enabled','spi_no_master'].includes(spiFirmwareLoaderAuditData.verdict.verdict) ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {spiFirmwareLoaderAuditData.spi_master_count} SPI · {spiFirmwareLoaderAuditData.firmware_request_count} fw · {i18n.t("integrations.spifw.verdict")} : <b>{spiFirmwareLoaderAuditData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if spiFirmwareLoaderAuditData?.ok}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        spiFirmwareLoaderAuditData.verdict.verdict === 'firmware_load_stuck' ? 'var(--warn)' :
                        ['profiling_enabled','spi_no_master'].includes(spiFirmwareLoaderAuditData.verdict.verdict) ? 'var(--accent)' :
                        'var(--text-dim)'};">
            <p style="margin: 4px 0;">{spiFirmwareLoaderAuditData.verdict.reason}</p>
            {#if spiFirmwareLoaderAuditData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.spifw.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{spiFirmwareLoaderAuditData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(spiFirmwareLoaderAuditData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #66.3 /proc/<pid> syscall + auxv + timerslack (UI sprint 57) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.procsysauxv.title")}</h4>
        <p class="muted">{i18n.t("integrations.procsysauxv.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadProcSyscallAuxvAudit}>{i18n.t("integrations.procsysauxv.refresh")}</button>
          {#if procSyscallAuxvAuditData?.ok}
            <span class="kv" style="margin-left: 12px;"
                  style:color={procSyscallAuxvAuditData.verdict.verdict === 'syscall_hang_long' ? 'var(--warn)' :
                             ['hwcap_drift','timerslack_battery_hostile'].includes(procSyscallAuxvAuditData.verdict.verdict) ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {procSyscallAuxvAuditData.sample_count} PIDs · {procSyscallAuxvAuditData.arch} · {i18n.t("integrations.procsysauxv.verdict")} : <b>{procSyscallAuxvAuditData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if procSyscallAuxvAuditData?.ok}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        procSyscallAuxvAuditData.verdict.verdict === 'syscall_hang_long' ? 'var(--warn)' :
                        ['hwcap_drift','timerslack_battery_hostile'].includes(procSyscallAuxvAuditData.verdict.verdict) ? 'var(--accent)' :
                        'var(--text-dim)'};">
            <p style="margin: 4px 0;">{procSyscallAuxvAuditData.verdict.reason}</p>
            {#if procSyscallAuxvAuditData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.procsysauxv.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{procSyscallAuxvAuditData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(procSyscallAuxvAuditData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #66.2 BTF + BPF (UI sprint 57) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.btfbpf.title")}</h4>
        <p class="muted">{i18n.t("integrations.btfbpf.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadBtfBpfAudit}>{i18n.t("integrations.btfbpf.refresh")}</button>
          {#if btfBpfAuditData?.ok}
            <span class="kv" style="margin-left: 12px;"
                  style:color={['vmlinux_btf_missing','stale_btf'].includes(btfBpfAuditData.verdict.verdict) ? 'var(--err)' :
                             btfBpfAuditData.verdict.verdict === 'module_btf_missing' ? 'var(--warn)' :
                             'var(--text-dim)'}>
              {btfBpfAuditData.module_btf_count}/{btfBpfAuditData.loaded_module_count} mod BTF · {i18n.t("integrations.btfbpf.verdict")} : <b>{btfBpfAuditData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if btfBpfAuditData?.ok}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        ['vmlinux_btf_missing','stale_btf'].includes(btfBpfAuditData.verdict.verdict) ? 'var(--err)' :
                        btfBpfAuditData.verdict.verdict === 'module_btf_missing' ? 'var(--warn)' :
                        'var(--text-dim)'};">
            <p style="margin: 4px 0;">{btfBpfAuditData.verdict.reason}</p>
            {#if btfBpfAuditData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.btfbpf.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{btfBpfAuditData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(btfBpfAuditData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #38.1 PCIe AER trend (UI sprint 29) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.aer.title")}</h4>
        <p class="muted">{i18n.t("integrations.aer.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadPcieAerTrend}>{i18n.t("integrations.aer.refresh")}</button>
          {#if pcieAerTrendData?.ok}
            <span class="kv" style="margin-left: 12px;"
                  style:color={['any_fatal','any_nonfatal','high_correctable'].includes(pcieAerTrendData.verdict.verdict) ? 'var(--warn)' :
                             pcieAerTrendData.verdict.verdict === 'low_correctable' ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {pcieAerTrendData.gpu_count} GPUs · {i18n.t("integrations.aer.verdict")} : <b>{pcieAerTrendData.verdict.verdict}</b>
            </span>
            {#if pcieAerTrendData.drift}
              <span class="kv"
                    style:color={pcieAerTrendData.drift.status === 'drift_detected' ? 'var(--warn)' : 'var(--text-dim)'}>
                {i18n.t("integrations.aer.drift")} : <b>{pcieAerTrendData.drift.status}</b>
              </span>
            {/if}
          {/if}
        </div>
        {#if pcieAerTrendData?.ok}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        ['any_fatal','any_nonfatal','high_correctable'].includes(pcieAerTrendData.verdict.verdict) ? 'var(--warn)' :
                        pcieAerTrendData.verdict.verdict === 'low_correctable' ? 'var(--accent)' :
                        'var(--text-dim)'};">
            <p style="margin: 4px 0;">{pcieAerTrendData.verdict.reason}</p>
            {#if pcieAerTrendData.drift?.deltas && Object.keys(pcieAerTrendData.drift.deltas).length > 0}
              <details style="margin-top: 4px;">
                <summary class="muted">deltas since baseline</summary>
                <ul style="font-size: 0.85em; margin: 4px 0; padding-left: 20px;">
                  {#each Object.entries(pcieAerTrendData.drift.deltas) as [bdf, dmap]}
                    <li><b>{bdf}</b>: {Object.entries(dmap).map(([k,v]) => `${k}=+${v}`).join(', ')}</li>
                  {/each}
                </ul>
              </details>
            {/if}
            {#if pcieAerTrendData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.aer.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{pcieAerTrendData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(pcieAerTrendData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #38.4 GPU IRQ affinity (UI sprint 29) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.irqaff.title")}</h4>
        <p class="muted">{i18n.t("integrations.irqaff.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadGpuIrqAffinity}>{i18n.t("integrations.irqaff.refresh")}</button>
          {#if gpuIrqAffinityData?.ok}
            <span class="kv" style="margin-left: 12px;"
                  style:color={['cpu0_concentrated','mismatch_local'].includes(gpuIrqAffinityData.verdict.verdict) ? 'var(--warn)' :
                             gpuIrqAffinityData.verdict.verdict === 'single_cpu_pin' ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {gpuIrqAffinityData.gpu_count} GPUs · {gpuIrqAffinityData.total_irqs} {i18n.t("integrations.irqaff.irqs")} · <b>{gpuIrqAffinityData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if gpuIrqAffinityData?.ok}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        ['cpu0_concentrated','mismatch_local'].includes(gpuIrqAffinityData.verdict.verdict) ? 'var(--warn)' :
                        gpuIrqAffinityData.verdict.verdict === 'single_cpu_pin' ? 'var(--accent)' :
                        'var(--text-dim)'};">
            {#each gpuIrqAffinityData.cards as c}
              <div class="form-row" style="gap: 12px; flex-wrap: wrap;">
                <b style="font-family: monospace;">{c.gpu_bdf}</b>
                <span class="kv">local : <b>{c.local_cpulist ?? '—'}</b></span>
                {#each c.irqs as irq}
                  <span class="kv">IRQ {irq.irq} → CPU {irq.effective ?? '—'}</span>
                {/each}
              </div>
            {/each}
            <p style="margin: 4px 0;">{gpuIrqAffinityData.verdict.reason}</p>
            {#if gpuIrqAffinityData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.irqaff.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{gpuIrqAffinityData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(gpuIrqAffinityData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #38.2 modprobe drift (UI sprint 29) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.mprb.title")}</h4>
        <p class="muted">{i18n.t("integrations.mprb.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadModprobeAudit}>{i18n.t("integrations.mprb.refresh")}</button>
          {#if modprobeAuditData?.ok && modprobeAuditData.verdict}
            <span class="kv" style="margin-left: 12px;"
                  style:color={modprobeAuditData.verdict.verdict === 'drift' ? 'var(--warn)' :
                             modprobeAuditData.verdict.verdict === 'synced' ? 'var(--text-dim)' :
                             'var(--accent)'}>
              {i18n.t("integrations.mprb.verdict")} : <b>{modprobeAuditData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if modprobeAuditData?.ok === false}
          <p class="muted" style="margin-top: 6px;">{i18n.t("integrations.mprb.unavailable")}</p>
        {/if}
        {#if modprobeAuditData?.ok && modprobeAuditData.verdict}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        modprobeAuditData.verdict.verdict === 'drift' ? 'var(--warn)' :
                        modprobeAuditData.verdict.verdict === 'synced' ? 'var(--text-dim)' :
                        'var(--accent)'};">
            <p style="margin: 4px 0;">{modprobeAuditData.verdict.reason}</p>
            {#if modprobeAuditData.drift_rows && modprobeAuditData.drift_rows.length > 0}
              <details style="margin-top: 4px;">
                <summary class="muted">{modprobeAuditData.drift_rows.length} drift row(s)</summary>
                <ul style="font-size: 0.85em; margin: 4px 0; padding-left: 20px;">
                  {#each modprobeAuditData.drift_rows as r}
                    <li>
                      <span style="font-family: monospace;">{r.module}/{r.param}</span>:
                      on-disk=<b>{r.on_disk}</b>, runtime=<b style:color="var(--warn)">{r.runtime}</b>
                    </li>
                  {/each}
                </ul>
              </details>
            {/if}
            {#if modprobeAuditData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.mprb.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{modprobeAuditData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(modprobeAuditData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #38.3 /proc/<pid>/maps (deleted) shared libs (UI sprint 29) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.maps.title")}</h4>
        <p class="muted">{i18n.t("integrations.maps.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadProcMapsLibs}>{i18n.t("integrations.maps.refresh")}</button>
          {#if procMapsLibsData}
            <span class="kv" style="margin-left: 12px;"
                  style:color={procMapsLibsData.worst_verdict === 'deleted_libs' ? 'var(--warn)' :
                             procMapsLibsData.worst_verdict === 'unreadable' ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {procMapsLibsData.process_count} procs · <b>{procMapsLibsData.worst_verdict}</b>
            </span>
          {/if}
        </div>
        {#if procMapsLibsData?.worst_verdict === 'no_llm_procs'}
          <p class="muted" style="margin-top: 6px;">{i18n.t("integrations.maps.no_procs")}</p>
        {/if}
        {#if procMapsLibsData?.processes && procMapsLibsData.processes.length > 0}
          {#each procMapsLibsData.processes as p}
            <div style="margin-top: 8px; padding: 8px;
                        border-left: 3px solid {
                          p.deleted_libs.length > 0 ? 'var(--warn)' :
                          !p.readable ? 'var(--accent)' :
                          'var(--text-dim)'};">
              <div class="form-row" style="gap: 12px; flex-wrap: wrap;">
                <b style="font-family: monospace;">{p.comm}</b>
                <span class="kv">pid <b>{p.pid}</b></span>
                <span class="kv">libs: <b>{p.libs.length}</b></span>
                {#if p.libs.length > 0}
                  <span class="kv">nvidia: <b>{p.libs.filter(l => l.is_nvidia).length}</b></span>
                {/if}
                {#if p.deleted_libs.length > 0}
                  <span class="kv" style:color="var(--warn)">(deleted): <b>{p.deleted_libs.length}</b></span>
                {/if}
                {#if !p.readable}
                  <span class="kv muted">(maps unreadable)</span>
                {/if}
              </div>
              {#if p.deleted_libs.length > 0}
                <p style="margin: 4px 0;">{p.deleted_libs.join(', ')}</p>
              {/if}
            </div>
          {/each}
        {/if}
        {#if procMapsLibsData?.verdict?.recommendation}
          <details style="margin-top: 4px;">
            <summary class="muted">{i18n.t("integrations.maps.recommend")}</summary>
            <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                         border-radius: 4px; overflow-x: auto;">{procMapsLibsData.verdict.recommendation}</pre>
            <button class="btn btn-small"
                    onclick={() => copyToClipboard(procMapsLibsData!.verdict!.recommendation)}>📋 Copy</button>
          </details>
        {/if}
      </div>

      <!-- R&D #37.1 CPU vulnerabilities mitigation cost (UI sprint 28) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.cpvulns.title")}</h4>
        <p class="muted">{i18n.t("integrations.cpvulns.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadCpuVulns}>{i18n.t("integrations.cpvulns.refresh")}</button>
          {#if cpuVulnsData?.ok && cpuVulnsData.verdict}
            <span class="kv" style="margin-left: 12px;"
                  style:color={cpuVulnsData.verdict.verdict === 'vulnerable' ? 'var(--warn)' :
                             cpuVulnsData.verdict.verdict === 'mitigated' ? 'var(--accent)' :
                             cpuVulnsData.verdict.verdict === 'clean' ? 'var(--text-dim)' :
                             'var(--text-dim)'}>
              {cpuVulnsData.vulnerability_count} vulns · {i18n.t("integrations.cpvulns.verdict")} : <b>{cpuVulnsData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if cpuVulnsData?.ok === false}
          <p class="muted" style="margin-top: 6px;">{i18n.t("integrations.cpvulns.unavailable")}</p>
        {/if}
        {#if cpuVulnsData?.ok}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        cpuVulnsData.verdict?.verdict === 'vulnerable' ? 'var(--warn)' :
                        cpuVulnsData.verdict?.verdict === 'mitigated' ? 'var(--accent)' :
                        'var(--text-dim)'};">
            {#if cpuVulnsData.counts}
              <div class="form-row" style="gap: 12px; flex-wrap: wrap;">
                <span class="kv">not_affected: <b>{cpuVulnsData.counts.not_affected}</b></span>
                <span class="kv">mitigated: <b>{cpuVulnsData.counts.mitigated}</b></span>
                {#if cpuVulnsData.counts.vulnerable > 0}
                  <span class="kv" style:color="var(--warn)">vulnerable: <b>{cpuVulnsData.counts.vulnerable}</b></span>
                {:else}
                  <span class="kv">vulnerable: <b>0</b></span>
                {/if}
              </div>
            {/if}
            <p style="margin: 4px 0;">{cpuVulnsData.verdict?.reason}</p>
            {#if cpuVulnsData.rows && cpuVulnsData.rows.length > 0}
              <details style="margin-top: 4px;">
                <summary class="muted">{cpuVulnsData.rows.length} entries</summary>
                <ul style="font-size: 0.85em; margin: 4px 0; padding-left: 20px;">
                  {#each cpuVulnsData.rows as r}
                    <li style:color={r.state === 'vulnerable' ? 'var(--warn)' :
                                       r.state === 'mitigated' ? 'var(--accent)' :
                                       ''}>
                      <span style="font-family: monospace;">{r.name}</span>:
                      <b>{r.state}</b>
                      {#if r.detail}<span class="muted"> — {r.detail.substring(0, 100)}</span>{/if}
                    </li>
                  {/each}
                </ul>
              </details>
            {/if}
            {#if cpuVulnsData.verdict?.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.cpvulns.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{cpuVulnsData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(cpuVulnsData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #37.3 Hardware watchdog (UI sprint 28) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.wdog.title")}</h4>
        <p class="muted">{i18n.t("integrations.wdog.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadHwWatchdog}>{i18n.t("integrations.wdog.refresh")}</button>
          {#if hwWatchdogData?.ok && hwWatchdogData.verdict}
            <span class="kv" style="margin-left: 12px;"
                  style:color={hwWatchdogData.verdict.verdict === 'bootstatus_set' ? 'var(--warn)' :
                             ['unpinged','unknown'].includes(hwWatchdogData.verdict.verdict) ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {hwWatchdogData.watchdog_count} watchdogs · {i18n.t("integrations.wdog.verdict")} : <b>{hwWatchdogData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if hwWatchdogData?.ok && hwWatchdogData.verdict}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        hwWatchdogData.verdict.verdict === 'bootstatus_set' ? 'var(--warn)' :
                        ['unpinged','unknown'].includes(hwWatchdogData.verdict.verdict) ? 'var(--accent)' :
                        'var(--text-dim)'};">
            {#if hwWatchdogData.watchdogs && hwWatchdogData.watchdogs.length > 0}
              {#each hwWatchdogData.watchdogs as w}
                <div class="form-row" style="gap: 12px; flex-wrap: wrap;">
                  <b style="font-family: monospace;">{w.watchdog}</b>
                  <span class="kv">{i18n.t("integrations.wdog.identity")} : <b>{w.identity}</b></span>
                  {#if w.timeout !== null}
                    <span class="kv">{i18n.t("integrations.wdog.timeout")} : <b>{w.timeout}s</b></span>
                  {/if}
                  {#if w.bootstatus !== null && w.bootstatus > 0}
                    <span class="kv" style:color="var(--warn)">bootstatus : <b>{w.bootstatus}</b></span>
                  {/if}
                </div>
              {/each}
            {/if}
            <p style="margin: 4px 0;">{hwWatchdogData.verdict.reason}</p>
            {#if hwWatchdogData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.wdog.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{hwWatchdogData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(hwWatchdogData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #37.2 GPU↔CPU affinity (UI sprint 28) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.gpuaff.title")}</h4>
        <p class="muted">{i18n.t("integrations.gpuaff.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadGpuCpuAffinity}>{i18n.t("integrations.gpuaff.refresh")}</button>
          {#if gpuCpuAffinityData?.ok && gpuCpuAffinityData.verdict}
            <span class="kv" style="margin-left: 12px;"
                  style:color={gpuCpuAffinityData.verdict.verdict === 'constrained_affinity' ? 'var(--warn)' :
                             ['unset','unknown'].includes(gpuCpuAffinityData.verdict.verdict) ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {gpuCpuAffinityData.gpu_count} GPUs · {i18n.t("integrations.gpuaff.verdict")} : <b>{gpuCpuAffinityData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if gpuCpuAffinityData?.ok && gpuCpuAffinityData.verdict}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        gpuCpuAffinityData.verdict.verdict === 'constrained_affinity' ? 'var(--warn)' :
                        ['unset','unknown'].includes(gpuCpuAffinityData.verdict.verdict) ? 'var(--accent)' :
                        'var(--text-dim)'};">
            {#each gpuCpuAffinityData.cards as c}
              <div class="form-row" style="gap: 12px; flex-wrap: wrap;">
                <b style="font-family: monospace;">{c.gpu_bdf}</b>
                <span class="kv">{i18n.t("integrations.gpuaff.local")} :
                  <b style="font-family: monospace;">{c.local_cpulist ?? '—'}</b>
                  ({c.local_cpus_count}/{gpuCpuAffinityData.total_cpus})
                </span>
                <span class="kv">{i18n.t("integrations.gpuaff.numa")} : <b>{c.numa_node ?? '—'}</b></span>
              </div>
            {/each}
            <p style="margin: 4px 0;">{gpuCpuAffinityData.verdict.reason}</p>
            {#if gpuCpuAffinityData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.gpuaff.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{gpuCpuAffinityData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(gpuCpuAffinityData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #37.4 L3 cache topology (UI sprint 28) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.cache.title")}</h4>
        <p class="muted">{i18n.t("integrations.cache.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadCacheTopology}>{i18n.t("integrations.cache.refresh")}</button>
          {#if cacheTopologyData?.ok && cacheTopologyData.verdict}
            <span class="kv" style="margin-left: 12px;"
                  style:color={cacheTopologyData.verdict.verdict === 'multi_l3_islands' ? 'var(--warn)' :
                             cacheTopologyData.verdict.verdict === 'no_l3' ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {cacheTopologyData.l3_island_count} {i18n.t("integrations.cache.islands")} · <b>{cacheTopologyData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if cacheTopologyData?.ok && cacheTopologyData.verdict}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        cacheTopologyData.verdict.verdict === 'multi_l3_islands' ? 'var(--warn)' :
                        cacheTopologyData.verdict.verdict === 'no_l3' ? 'var(--accent)' :
                        'var(--text-dim)'};">
            <div class="form-row" style="gap: 12px; flex-wrap: wrap;">
              {#each cacheTopologyData.islands as isl}
                <span class="kv">L3 {isl.cpu_list} : <b>{isl.size_mb} MiB</b></span>
              {/each}
              {#if cacheTopologyData.l1d_kb}
                <span class="kv">{i18n.t("integrations.cache.l1d")} : <b>{cacheTopologyData.l1d_kb} KiB</b></span>
              {/if}
              {#if cacheTopologyData.l2_kb}
                <span class="kv">{i18n.t("integrations.cache.l2")} : <b>{cacheTopologyData.l2_kb} KiB</b></span>
              {/if}
            </div>
            <p style="margin: 4px 0;">{cacheTopologyData.verdict.reason}</p>
            {#if cacheTopologyData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.cache.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{cacheTopologyData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(cacheTopologyData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- PAM memlock audit (bonus — UI sprint 28) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.pam.title")}</h4>
        <p class="muted">{i18n.t("integrations.pam.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadLimitsAudit}>{i18n.t("integrations.pam.refresh")}</button>
          {#if limitsAuditData?.ok && limitsAuditData.verdict}
            <span class="kv" style="margin-left: 12px;"
                  style:color={['explicit_low','default'].includes(limitsAuditData.verdict.verdict) ? 'var(--warn)' :
                             limitsAuditData.verdict.verdict === 'unknown' ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {i18n.t("integrations.pam.verdict")} : <b>{limitsAuditData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if limitsAuditData?.ok === false}
          <p class="muted" style="margin-top: 6px;">{i18n.t("integrations.pam.unavailable")}</p>
        {/if}
        {#if limitsAuditData?.ok && limitsAuditData.verdict}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        ['explicit_low','default'].includes(limitsAuditData.verdict.verdict) ? 'var(--warn)' :
                        limitsAuditData.verdict.verdict === 'unknown' ? 'var(--accent)' :
                        'var(--text-dim)'};">
            {#if limitsAuditData.files && limitsAuditData.files.length > 0}
              <span class="kv muted">{i18n.t("integrations.pam.files")} : {limitsAuditData.files.join(', ')}</span>
            {/if}
            <p style="margin: 4px 0;">{limitsAuditData.verdict.reason}</p>
            {#if limitsAuditData.memlock_rules && limitsAuditData.memlock_rules.length > 0}
              <details style="margin-top: 4px;">
                <summary class="muted">{limitsAuditData.memlock_rules.length} memlock rules</summary>
                <ul style="font-size: 0.85em; margin: 4px 0; padding-left: 20px;">
                  {#each limitsAuditData.memlock_rules as r}
                    <li><span style="font-family: monospace;">{r.domain} {r.type} {r.item} {r.value}</span></li>
                  {/each}
                </ul>
              </details>
            {/if}
            {#if limitsAuditData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.pam.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{limitsAuditData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(limitsAuditData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #36.3 Kernel taint audit (UI sprint 27) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.taint.title")}</h4>
        <p class="muted">{i18n.t("integrations.taint.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadKernelTaint}>{i18n.t("integrations.taint.refresh")}</button>
          {#if kernelTaintData?.ok && kernelTaintData.verdict}
            <span class="kv" style="margin-left: 12px;"
                  style:color={kernelTaintData.verdict.verdict === 'hardware_errors' ? 'var(--warn)' :
                             kernelTaintData.verdict.verdict === 'warnings' ? 'var(--accent)' :
                             kernelTaintData.verdict.verdict === 'mixed' ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {i18n.t("integrations.taint.value")} : <b>{kernelTaintData.value}</b>
              · <b>{kernelTaintData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if kernelTaintData?.ok === false}
          <p class="muted" style="margin-top: 6px;">{i18n.t("integrations.taint.unavailable")}</p>
        {/if}
        {#if kernelTaintData?.ok}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        kernelTaintData.verdict?.verdict === 'hardware_errors' ? 'var(--warn)' :
                        ['warnings','mixed'].includes(kernelTaintData.verdict?.verdict ?? '') ? 'var(--accent)' :
                        'var(--text-dim)'};">
            {#if kernelTaintData.flags && kernelTaintData.flags.length > 0}
              <div class="form-row" style="gap: 12px; flex-wrap: wrap;">
                {#each kernelTaintData.flags as f}
                  <span class="kv"><b style="font-family: monospace;">{f.code}</b> — {f.description}</span>
                {/each}
              </div>
            {/if}
            {#if kernelTaintData.uptime_seconds}
              <p class="muted" style="margin: 4px 0;">
                {i18n.t("integrations.taint.uptime")} :
                <b>{(kernelTaintData.uptime_seconds / 3600).toFixed(1)} h</b>
              </p>
            {/if}
            <p style="margin: 4px 0;">{kernelTaintData.verdict?.reason}</p>
            {#if kernelTaintData.verdict?.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.taint.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{kernelTaintData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(kernelTaintData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #36.1 CPU microcode audit (UI sprint 27) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.uc.title")}</h4>
        <p class="muted">{i18n.t("integrations.uc.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadCpuMicrocode}>{i18n.t("integrations.uc.refresh")}</button>
          {#if cpuMicrocodeData?.ok && cpuMicrocodeData.verdict}
            <span class="kv" style="margin-left: 12px;"
                  style:color={cpuMicrocodeData.verdict.verdict === 'drift' ? 'var(--warn)' :
                             cpuMicrocodeData.verdict.verdict === 'missing' ? 'var(--text-dim)' :
                             'var(--accent)'}>
              {i18n.t("integrations.uc.verdict")} : <b>{cpuMicrocodeData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if cpuMicrocodeData?.ok}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        cpuMicrocodeData.verdict?.verdict === 'drift' ? 'var(--warn)' :
                        cpuMicrocodeData.verdict?.verdict === 'missing' ? 'var(--text-dim)' :
                        'var(--accent)'};">
            <div class="form-row" style="gap: 12px; flex-wrap: wrap;">
              {#if cpuMicrocodeData.model_name}
                <b style="font-family: monospace;">{cpuMicrocodeData.model_name}</b>
              {/if}
              <span class="kv">{i18n.t("integrations.uc.cpu")} : <b>{cpuMicrocodeData.cpu_count}</b></span>
              <span class="kv">{i18n.t("integrations.uc.rev")} :
                <b style="font-family: monospace;">{(cpuMicrocodeData.distinct_microcodes ?? []).join(', ') || '—'}</b>
              </span>
            </div>
            <p style="margin: 4px 0;">{cpuMicrocodeData.verdict?.reason}</p>
            {#if cpuMicrocodeData.verdict?.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.uc.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{cpuMicrocodeData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(cpuMicrocodeData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #36.4 HWP EPP audit (UI sprint 27) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.epp.title")}</h4>
        <p class="muted">{i18n.t("integrations.epp.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadHwpEpp}>{i18n.t("integrations.epp.refresh")}</button>
          {#if hwpEppData?.ok && hwpEppData.verdict}
            <span class="kv" style="margin-left: 12px;"
                  style:color={['power_save','drift'].includes(hwpEppData.verdict.verdict) ? 'var(--warn)' :
                             ['default_mode','balanced'].includes(hwpEppData.verdict.verdict) ? 'var(--accent)' :
                             ['missing','unknown'].includes(hwpEppData.verdict.verdict) ? 'var(--text-dim)' :
                             'var(--accent)'}>
              {hwpEppData.cpu_count} CPUs · {i18n.t("integrations.epp.verdict")} : <b>{hwpEppData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if hwpEppData?.ok && hwpEppData.verdict}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        ['power_save','drift'].includes(hwpEppData.verdict.verdict) ? 'var(--warn)' :
                        ['default_mode','balanced'].includes(hwpEppData.verdict.verdict) ? 'var(--accent)' :
                        'var(--text-dim)'};">
            {#if hwpEppData.distinct_prefs && hwpEppData.distinct_prefs.length > 0}
              <div class="form-row" style="gap: 12px; flex-wrap: wrap;">
                <span class="kv">{i18n.t("integrations.epp.distinct")} :
                  <b style="font-family: monospace;">{hwpEppData.distinct_prefs.join(', ')}</b>
                </span>
                {#if hwpEppData.available && hwpEppData.available.length > 0}
                  <span class="kv muted">available : {hwpEppData.available.join(' / ')}</span>
                {/if}
              </div>
            {/if}
            <p style="margin: 4px 0;">{hwpEppData.verdict.reason}</p>
            {#if hwpEppData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.epp.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{hwpEppData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(hwpEppData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #36.2 cpuidle audit (UI sprint 27) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.cpuidle.title")}</h4>
        <p class="muted">{i18n.t("integrations.cpuidle.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadCpuidle}>{i18n.t("integrations.cpuidle.refresh")}</button>
          {#if cpuidleData?.ok && cpuidleData.verdict}
            <span class="kv" style="margin-left: 12px;"
                  style:color={cpuidleData.verdict.verdict === 'deep_states_active' ? 'var(--warn)' :
                             cpuidleData.verdict.verdict === 'balanced' ? 'var(--accent)' :
                             ['disabled_driver','unknown'].includes(cpuidleData.verdict.verdict) ? 'var(--text-dim)' :
                             'var(--accent)'}>
              {i18n.t("integrations.cpuidle.verdict")} : <b>{cpuidleData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if cpuidleData?.ok === false}
          <p class="muted" style="margin-top: 6px;">{i18n.t("integrations.cpuidle.unavailable")}</p>
        {/if}
        {#if cpuidleData?.ok && cpuidleData.verdict}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        cpuidleData.verdict.verdict === 'deep_states_active' ? 'var(--warn)' :
                        cpuidleData.verdict.verdict === 'balanced' ? 'var(--accent)' :
                        'var(--text-dim)'};">
            <div class="form-row" style="gap: 12px; flex-wrap: wrap;">
              <span class="kv">{i18n.t("integrations.cpuidle.driver")} :
                <b style="font-family: monospace;">{cpuidleData.driver ?? '—'}</b>
              </span>
              <span class="kv">{i18n.t("integrations.cpuidle.governor")} :
                <b style="font-family: monospace;">{cpuidleData.governor ?? '—'}</b>
              </span>
              {#if cpuidleData.max_latency !== null && cpuidleData.max_latency !== undefined}
                <span class="kv">{i18n.t("integrations.cpuidle.max_latency")} :
                  <b>{cpuidleData.max_latency} µs</b>
                </span>
              {/if}
            </div>
            <p style="margin: 4px 0;">{cpuidleData.verdict.reason}</p>
            {#if cpuidleData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.cpuidle.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{cpuidleData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(cpuidleData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #35.1 CPU turbo/boost audit (UI sprint 26) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.boost.title")}</h4>
        <p class="muted">{i18n.t("integrations.boost.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadCpuBoost}>{i18n.t("integrations.boost.refresh")}</button>
          {#if cpuBoostData?.ok && cpuBoostData.verdict}
            <span class="kv" style="margin-left: 12px;"
                  style:color={cpuBoostData.verdict.verdict === 'boost_disabled' ? 'var(--warn)' :
                             ['missing','passive','unknown'].includes(cpuBoostData.verdict.verdict) ? 'var(--text-dim)' :
                             'var(--accent)'}>
              {i18n.t("integrations.boost.verdict")} : <b>{cpuBoostData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if cpuBoostData?.ok && cpuBoostData.verdict}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        cpuBoostData.verdict.verdict === 'boost_disabled' ? 'var(--warn)' :
                        ['missing','passive','unknown'].includes(cpuBoostData.verdict.verdict) ? 'var(--text-dim)' :
                        'var(--accent)'};">
            <div class="form-row" style="gap: 12px; flex-wrap: wrap;">
              <span class="kv">{i18n.t("integrations.boost.mode")} :
                <b style="font-family: monospace;">{cpuBoostData.mode}</b>
              </span>
              {#if cpuBoostData.boost !== null}
                <span class="kv">{i18n.t("integrations.boost.boost")} : <b>{cpuBoostData.boost}</b></span>
              {/if}
              {#if cpuBoostData.no_turbo !== null}
                <span class="kv">{i18n.t("integrations.boost.no_turbo")} : <b>{cpuBoostData.no_turbo}</b></span>
              {/if}
            </div>
            <p style="margin: 4px 0;">{cpuBoostData.verdict.reason}</p>
            {#if cpuBoostData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.boost.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{cpuBoostData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(cpuBoostData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #35.2 LAN socket-buffer audit (UI sprint 26) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.netsysctl.title")}</h4>
        <p class="muted">{i18n.t("integrations.netsysctl.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadNetSysctl}>{i18n.t("integrations.netsysctl.refresh")}</button>
          {#if netSysctlData?.ok}
            <span class="kv" style="margin-left: 12px;"
                  style:color={netSysctlData.worst_severity === 'warn' ? 'var(--warn)' :
                             netSysctlData.worst_severity === 'unknown' ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {i18n.t("integrations.netsysctl.severity")} : <b>{netSysctlData.worst_severity}</b>
              · <b>{netSysctlData.flagged_count}</b> {i18n.t("integrations.netsysctl.flagged")}
            </span>
          {/if}
        </div>
        {#if netSysctlData?.ok === false}
          <p class="muted" style="margin-top: 6px;">{i18n.t("integrations.netsysctl.unavailable")}</p>
        {/if}
        {#if netSysctlData?.rows && netSysctlData.rows.length > 0}
          {#each netSysctlData.rows as r}
            <div style="margin-top: 8px; padding: 8px;
                        border-left: 3px solid {
                          r.severity === 'warn' ? 'var(--warn)' :
                          r.severity === 'unknown' ? 'var(--accent)' :
                          'var(--text-dim)'};">
              <div class="form-row" style="gap: 12px; flex-wrap: wrap;">
                <b style="font-family: monospace;">net.{r.name.replace('/', '.')}</b>
                <span class="kv">current : <b>{r.value ?? '—'}</b></span>
                {#if r.recommended !== null}
                  <span class="kv">recommended : <b>{r.recommended}</b></span>
                {/if}
                <span class="kv"><b>{r.severity}</b></span>
              </div>
              <p class="muted" style="margin: 4px 0;">{r.reason}</p>
            </div>
          {/each}
        {/if}
        {#if netSysctlData?.recipe}
          <details style="margin-top: 8px;">
            <summary class="muted">{i18n.t("integrations.netsysctl.recipe")}</summary>
            <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                         border-radius: 4px; overflow-x: auto;">{netSysctlData.recipe}</pre>
            <button class="btn btn-small"
                    onclick={() => copyToClipboard(netSysctlData!.recipe!)}>📋 Copy</button>
          </details>
        {/if}
      </div>

      <!-- R&D #35.4 SMT / offline-core audit (UI sprint 26) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.smt.title")}</h4>
        <p class="muted">{i18n.t("integrations.smt.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadSmtAudit}>{i18n.t("integrations.smt.refresh")}</button>
          {#if smtAuditData?.ok && smtAuditData.verdict}
            <span class="kv" style="margin-left: 12px;"
                  style:color={smtAuditData.verdict.verdict === 'cores_offline' ? 'var(--warn)' :
                             smtAuditData.verdict.verdict === 'smt_off' ? 'var(--accent)' :
                             smtAuditData.verdict.verdict === 'unknown' ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {i18n.t("integrations.smt.verdict")} : <b>{smtAuditData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if smtAuditData?.ok && smtAuditData.verdict}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        smtAuditData.verdict.verdict === 'cores_offline' ? 'var(--warn)' :
                        ['smt_off','unknown'].includes(smtAuditData.verdict.verdict) ? 'var(--accent)' :
                        'var(--text-dim)'};">
            <div class="form-row" style="gap: 12px; flex-wrap: wrap;">
              <span class="kv">{i18n.t("integrations.smt.control")} :
                <b style="font-family: monospace;">{smtAuditData.smt_control ?? '—'}</b>
              </span>
              <span class="kv">{i18n.t("integrations.smt.online")} :
                <b>{smtAuditData.online_count}/{smtAuditData.possible_count}</b>
              </span>
              {#if smtAuditData.offline_cores && smtAuditData.offline_cores.length > 0}
                <span class="kv" style:color="var(--warn)">
                  {i18n.t("integrations.smt.offline")} :
                  <b>{smtAuditData.offline_cores.join(', ')}</b>
                </span>
              {/if}
            </div>
            <p style="margin: 4px 0;">{smtAuditData.verdict.reason}</p>
            {#if smtAuditData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.smt.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{smtAuditData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(smtAuditData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #35.3 NUMA placement audit (UI sprint 26) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.numa.title")}</h4>
        <p class="muted">{i18n.t("integrations.numa.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadNumaPlacement}>{i18n.t("integrations.numa.refresh")}</button>
          {#if numaPlacementData?.ok && numaPlacementData.verdict}
            <span class="kv" style="margin-left: 12px;"
                  style:color={numaPlacementData.verdict.verdict === 'cross_node_split' ? 'var(--warn)' :
                             numaPlacementData.verdict.verdict === 'unknown' ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {i18n.t("integrations.numa.nodes")} : <b>{numaPlacementData.node_count}</b>
              · {i18n.t("integrations.numa.procs")} : <b>{numaPlacementData.process_count}</b>
              · <b>{numaPlacementData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if numaPlacementData?.ok === false}
          <p class="muted" style="margin-top: 6px;">{i18n.t("integrations.numa.unavailable")}</p>
        {/if}
        {#if numaPlacementData?.ok && numaPlacementData.verdict}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        numaPlacementData.verdict.verdict === 'cross_node_split' ? 'var(--warn)' :
                        numaPlacementData.verdict.verdict === 'unknown' ? 'var(--accent)' :
                        'var(--text-dim)'};">
            {#if numaPlacementData.nodes && numaPlacementData.nodes.length > 0}
              <div class="form-row" style="gap: 12px; flex-wrap: wrap;">
                {#each numaPlacementData.nodes as n}
                  <span class="kv">node {n.id} · {n.cpu_list ?? '—'} · {n.mem_total_kb ? ((n.mem_total_kb / 1024 / 1024).toFixed(1) + ' GiB') : '—'}</span>
                {/each}
              </div>
            {/if}
            <p style="margin: 4px 0;">{numaPlacementData.verdict.reason}</p>
            {#if numaPlacementData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.numa.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{numaPlacementData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(numaPlacementData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #34.1 Transparent Hugepage audit (UI sprint 25) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.thp.title")}</h4>
        <p class="muted">{i18n.t("integrations.thp.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadThpAudit}>{i18n.t("integrations.thp.refresh")}</button>
          {#if thpAuditData?.ok && thpAuditData.verdict}
            <span class="kv" style="margin-left: 12px;"
                  style:color={['disabled','aggressive_defrag'].includes(thpAuditData.verdict.verdict) ? 'var(--warn)' :
                             thpAuditData.verdict.verdict === 'madvise_default' ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {i18n.t("integrations.thp.verdict")} : <b>{thpAuditData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if thpAuditData?.ok === false}
          <p class="muted" style="margin-top: 6px;">{i18n.t("integrations.thp.unavailable")}</p>
        {/if}
        {#if thpAuditData?.ok && thpAuditData.verdict}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        ['disabled','aggressive_defrag'].includes(thpAuditData.verdict.verdict) ? 'var(--warn)' :
                        thpAuditData.verdict.verdict === 'madvise_default' ? 'var(--accent)' :
                        'var(--text-dim)'};">
            <div class="form-row" style="gap: 12px; flex-wrap: wrap;">
              <span class="kv">{i18n.t("integrations.thp.enabled")} :
                <b style="font-family: monospace;">{thpAuditData.enabled ?? '—'}</b>
              </span>
              <span class="kv">{i18n.t("integrations.thp.defrag")} :
                <b style="font-family: monospace;">{thpAuditData.defrag ?? '—'}</b>
              </span>
              {#if thpAuditData.khugepaged_scan_sleep_ms}
                <span class="kv muted">khugepaged_scan_sleep : {thpAuditData.khugepaged_scan_sleep_ms} ms</span>
              {/if}
            </div>
            <p style="margin: 4px 0;">{thpAuditData.verdict.reason}</p>
            {#if thpAuditData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.thp.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{thpAuditData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(thpAuditData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #34.2 Memory fragmentation (UI sprint 25) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.buddy.title")}</h4>
        <p class="muted">{i18n.t("integrations.buddy.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadBuddyinfo}>{i18n.t("integrations.buddy.refresh")}</button>
          {#if buddyinfoData?.ok && buddyinfoData.verdict}
            <span class="kv" style="margin-left: 12px;"
                  style:color={buddyinfoData.verdict.verdict === 'fragmented_severe' ? 'var(--warn)' :
                             buddyinfoData.verdict.verdict === 'fragmented_moderate' ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {i18n.t("integrations.buddy.verdict")} : <b>{buddyinfoData.verdict.verdict}</b>
            </span>
            {#if buddyinfoData.total_thp_blocks !== undefined}
              <span class="kv">{i18n.t("integrations.buddy.thp_blocks")} : <b>{buddyinfoData.total_thp_blocks}</b></span>
            {/if}
          {/if}
        </div>
        {#if buddyinfoData?.ok === false}
          <p class="muted" style="margin-top: 6px;">{i18n.t("integrations.buddy.unavailable")}</p>
        {/if}
        {#if buddyinfoData?.zones && buddyinfoData.zones.length > 0}
          {#each buddyinfoData.zones as z}
            <div style="margin-top: 8px; padding: 8px;
                        border-left: 3px solid var(--text-dim);">
              <div class="form-row" style="gap: 12px; flex-wrap: wrap;">
                <b style="font-family: monospace;">node {z.node} · {z.zone}</b>
                <span class="kv">free : <b>{z.total_free_mb} MiB</b></span>
                <span class="kv">order9 : <b>{z.order9_pages}</b></span>
                <span class="kv">order10 : <b>{z.order10_pages}</b></span>
              </div>
            </div>
          {/each}
        {/if}
        {#if buddyinfoData?.verdict?.recommendation}
          <details style="margin-top: 8px;">
            <summary class="muted">{i18n.t("integrations.buddy.recommend")}</summary>
            <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                         border-radius: 4px; overflow-x: auto;">{buddyinfoData.verdict.recommendation}</pre>
            <button class="btn btn-small"
                    onclick={() => copyToClipboard(buddyinfoData!.verdict!.recommendation)}>📋 Copy</button>
          </details>
        {/if}
      </div>

      <!-- R&D #34.4 Per-daemon scheduler stats (UI sprint 25) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.sched.title")}</h4>
        <p class="muted">{i18n.t("integrations.sched.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadProcSched}>{i18n.t("integrations.sched.refresh")}</button>
          {#if procSchedData}
            <span class="kv" style="margin-left: 12px;"
                  style:color={['contended','severely_contended'].includes(procSchedData.worst_verdict) ? 'var(--warn)' :
                             procSchedData.worst_verdict === 'unknown' ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {procSchedData.process_count} procs · {i18n.t("integrations.sched.verdict")} : <b>{procSchedData.worst_verdict}</b>
            </span>
          {/if}
        </div>
        {#if procSchedData?.worst_verdict === 'no_llm_procs'}
          <p class="muted" style="margin-top: 6px;">{i18n.t("integrations.sched.no_procs")}</p>
        {/if}
        {#if procSchedData?.processes && procSchedData.processes.length > 0}
          {#each procSchedData.processes as p}
            <div style="margin-top: 8px; padding: 8px;
                        border-left: 3px solid {
                          ['contended','severely_contended'].includes(p.verdict.verdict) ? 'var(--warn)' :
                          p.verdict.verdict === 'unknown' ? 'var(--accent)' :
                          'var(--text-dim)'};">
              <div class="form-row" style="gap: 12px; flex-wrap: wrap;">
                <b style="font-family: monospace;">{p.comm}</b>
                <span class="kv">pid <b>{p.pid}</b></span>
                {#if p.involuntary_ratio !== null}
                  <span class="kv">{i18n.t("integrations.sched.invol_ratio")} :
                    <b>{(p.involuntary_ratio * 100).toFixed(0)}%</b>
                  </span>
                {/if}
                {#if p.nr_migrations !== null}
                  <span class="kv">{i18n.t("integrations.sched.migrations")} :
                    <b>{p.nr_migrations.toLocaleString()}</b>
                  </span>
                {/if}
                {#if p.threads}
                  <span class="kv">{i18n.t("integrations.sched.threads")} : <b>{p.threads}</b></span>
                {/if}
                <span class="kv"><b>{p.verdict.verdict}</b></span>
              </div>
              <p style="margin: 4px 0;">{p.verdict.reason}</p>
              {#if p.verdict.recommendation}
                <details style="margin-top: 4px;">
                  <summary class="muted">{i18n.t("integrations.sched.recommend")}</summary>
                  <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                               border-radius: 4px; overflow-x: auto;">{p.verdict.recommendation}</pre>
                  <button class="btn btn-small"
                          onclick={() => copyToClipboard(p.verdict.recommendation)}>📋 Copy</button>
                </details>
              {/if}
            </div>
          {/each}
        {/if}
      </div>

      <!-- R&D #34.3 systemd-oomd correlator (UI sprint 25) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.oomd.title")}</h4>
        <p class="muted">{i18n.t("integrations.oomd.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadOomd}>{i18n.t("integrations.oomd.refresh")}</button>
          {#if oomdData}
            <span class="kv" style="margin-left: 12px;"
                  style:color={oomdData.verdict.verdict === 'active_killed_llm' ? 'var(--warn)' :
                             oomdData.verdict.verdict === 'active_killed_other' ? 'var(--accent)' :
                             oomdData.verdict.verdict === 'unknown' ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {i18n.t("integrations.oomd.state")} : <b>{oomdData.state}</b> · {oomdData.event_count} {i18n.t("integrations.oomd.events")} · <b>{oomdData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if oomdData?.verdict}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        oomdData.verdict.verdict === 'active_killed_llm' ? 'var(--warn)' :
                        oomdData.verdict.verdict === 'active_killed_other' ? 'var(--accent)' :
                        oomdData.verdict.verdict === 'unknown' ? 'var(--accent)' :
                        'var(--text-dim)'};">
            <p style="margin: 4px 0;">{oomdData.verdict.reason}</p>
            {#if oomdData.events && oomdData.events.length > 0}
              <details style="margin-top: 4px;">
                <summary class="muted">events ({oomdData.events.length})</summary>
                <ul style="font-size: 0.85em; margin: 4px 0; padding-left: 20px;">
                  {#each oomdData.events as e}
                    <li>
                      <span style="font-family: monospace;">{e.target}</span>
                      <span class="muted"> — {e.message.substring(0, 80)}...</span>
                    </li>
                  {/each}
                </ul>
              </details>
            {/if}
            {#if oomdData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.oomd.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{oomdData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(oomdData!.verdict.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #33.4 clocksource audit (UI sprint 24) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.clock.title")}</h4>
        <p class="muted">{i18n.t("integrations.clock.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadClocksource}>{i18n.t("integrations.clock.refresh")}</button>
          {#if clocksourceData?.ok && clocksourceData.verdict}
            <span class="kv" style="margin-left: 12px;"
                  style:color={['hpet_active','low_res','suboptimal_virt'].includes(clocksourceData.verdict.verdict) ? 'var(--warn)' :
                             clocksourceData.verdict.verdict === 'acceptable' ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {i18n.t("integrations.clock.verdict")} : <b>{clocksourceData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if clocksourceData?.ok === false}
          <p class="muted" style="margin-top: 6px;">{i18n.t("integrations.clock.unavailable")}</p>
        {/if}
        {#if clocksourceData?.ok && clocksourceData.verdict}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        ['hpet_active','low_res','suboptimal_virt'].includes(clocksourceData.verdict.verdict) ? 'var(--warn)' :
                        clocksourceData.verdict.verdict === 'acceptable' ? 'var(--accent)' :
                        'var(--text-dim)'};">
            <div class="form-row" style="gap: 12px; flex-wrap: wrap;">
              <span class="kv">{i18n.t("integrations.clock.current")} :
                <b style="font-family: monospace;">{clocksourceData.current ?? '—'}</b>
              </span>
              {#if clocksourceData.virt}
                <span class="kv">{i18n.t("integrations.clock.virt")} : <b>{clocksourceData.virt}</b></span>
              {/if}
              {#if clocksourceData.available}
                <span class="kv muted">available : {clocksourceData.available.join(' / ')}</span>
              {/if}
            </div>
            <p style="margin: 4px 0;">{clocksourceData.verdict.reason}</p>
            {#if clocksourceData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.clock.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{clocksourceData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(clocksourceData!.verdict!.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #33.1 LAN NIC health (UI sprint 24) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.nic.title")}</h4>
        <p class="muted">{i18n.t("integrations.nic.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadNicHealth}>{i18n.t("integrations.nic.refresh")}</button>
          {#if nicHealthData}
            <span class="kv" style="margin-left: 12px;"
                  style:color={['errors_present','link_down','drops_high'].includes(nicHealthData.worst_verdict) ? 'var(--warn)' :
                             nicHealthData.worst_verdict === 'speed_low' ? 'var(--warn)' :
                             nicHealthData.worst_verdict === 'unknown' ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {nicHealthData.interface_count} ifaces · {i18n.t("integrations.nic.verdict")} : <b>{nicHealthData.worst_verdict}</b>
            </span>
          {/if}
        </div>
        {#if nicHealthData?.worst_verdict === 'no_nics'}
          <p class="muted" style="margin-top: 6px;">{i18n.t("integrations.nic.no_nics")}</p>
        {/if}
        {#if nicHealthData?.interfaces && nicHealthData.interfaces.length > 0}
          {#each nicHealthData.interfaces as iface}
            <div style="margin-top: 8px; padding: 8px;
                        border-left: 3px solid {
                          ['errors_present','link_down','drops_high','speed_low'].includes(iface.verdict.verdict) ? 'var(--warn)' :
                          iface.verdict.verdict === 'unknown' ? 'var(--accent)' :
                          'var(--text-dim)'};">
              <div class="form-row" style="gap: 12px; flex-wrap: wrap;">
                <b style="font-family: monospace;">{iface.name}</b>
                <span class="kv">{i18n.t("integrations.nic.carrier")} : <b>{iface.carrier === '1' ? 'up' : 'down'}</b></span>
                {#if iface.speed !== null && iface.speed > 0}
                  <span class="kv">{i18n.t("integrations.nic.speed")} : <b>{iface.speed} Mbps</b></span>
                {/if}
                {#if iface.rx_dropped !== null && iface.rx_dropped > 0}
                  <span class="kv" style:color="var(--warn)">{i18n.t("integrations.nic.rx_dropped")} :
                    <b>{iface.rx_dropped.toLocaleString()}</b>
                  </span>
                {/if}
                {#if iface.tx_dropped !== null && iface.tx_dropped > 0}
                  <span class="kv" style:color="var(--warn)">{i18n.t("integrations.nic.tx_dropped")} :
                    <b>{iface.tx_dropped.toLocaleString()}</b>
                  </span>
                {/if}
                <span class="kv"><b>{iface.verdict.verdict}</b></span>
              </div>
              <p style="margin: 4px 0;">{iface.verdict.reason}</p>
              {#if iface.verdict.recommendation}
                <details style="margin-top: 4px;">
                  <summary class="muted">{i18n.t("integrations.nic.recommend")}</summary>
                  <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                               border-radius: 4px; overflow-x: auto;">{iface.verdict.recommendation}</pre>
                  <button class="btn btn-small"
                          onclick={() => copyToClipboard(iface.verdict.recommendation)}>📋 Copy</button>
                </details>
              {/if}
            </div>
          {/each}
        {/if}
      </div>

      <!-- R&D #33.2 Per-daemon IO accounting (UI sprint 24) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.procio.title")}</h4>
        <p class="muted">{i18n.t("integrations.procio.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadProcIo}>{i18n.t("integrations.procio.refresh")}</button>
          {#if procIoData}
            <span class="kv" style="margin-left: 12px;"
                  style:color={procIoData.worst_verdict === 'reread_thrash' ? 'var(--warn)' :
                             procIoData.worst_verdict === 'heavy_write' ? 'var(--warn)' :
                             procIoData.worst_verdict === 'unreadable' ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {procIoData.process_count} procs · {i18n.t("integrations.procio.verdict")} : <b>{procIoData.worst_verdict}</b>
            </span>
          {/if}
        </div>
        {#if procIoData?.worst_verdict === 'no_llm_procs'}
          <p class="muted" style="margin-top: 6px;">{i18n.t("integrations.procio.no_procs")}</p>
        {/if}
        {#if procIoData?.processes && procIoData.processes.length > 0}
          {#each procIoData.processes as p}
            <div style="margin-top: 8px; padding: 8px;
                        border-left: 3px solid {
                          ['reread_thrash','heavy_write'].includes(p.verdict.verdict) ? 'var(--warn)' :
                          p.verdict.verdict === 'unreadable' ? 'var(--accent)' :
                          'var(--text-dim)'};">
              <div class="form-row" style="gap: 12px; flex-wrap: wrap;">
                <b style="font-family: monospace;">{p.comm}</b>
                <span class="kv">pid <b>{p.pid}</b></span>
                <span class="kv">{i18n.t("integrations.procio.read")} :
                  <b>{(p.read_bytes / 1024**3).toFixed(1)} GiB</b>
                </span>
                <span class="kv">{i18n.t("integrations.procio.write")} :
                  <b>{(p.write_bytes / 1024**3).toFixed(1)} GiB</b>
                </span>
                {#if p.vm_rss_bytes}
                  <span class="kv">{i18n.t("integrations.procio.rss")} :
                    <b>{(p.vm_rss_bytes / 1024**3).toFixed(1)} GiB</b>
                  </span>
                {/if}
                <span class="kv"><b>{p.verdict.verdict}</b></span>
              </div>
              <p style="margin: 4px 0;">{p.verdict.reason}</p>
              {#if p.verdict.recommendation}
                <details style="margin-top: 4px;">
                  <summary class="muted">{i18n.t("integrations.procio.recipe")}</summary>
                  <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                               border-radius: 4px; overflow-x: auto;">{p.verdict.recommendation}</pre>
                  <button class="btn btn-small"
                          onclick={() => copyToClipboard(p.verdict.recommendation)}>📋 Copy</button>
                </details>
              {/if}
            </div>
          {/each}
        {/if}
      </div>

      <!-- R&D #33.6 cgroup CPU/IO priority (UI sprint 24) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.cgcpuio.title")}</h4>
        <p class="muted">{i18n.t("integrations.cgcpuio.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadCgroupCpuio}>{i18n.t("integrations.cgcpuio.refresh")}</button>
          {#if cgroupCpuioData}
            <span class="kv" style="margin-left: 12px;"
                  style:color={cgroupCpuioData.worst_verdict === 'cpu_quota_active' ? 'var(--warn)' :
                             cgroupCpuioData.worst_verdict === 'default_weight' ? 'var(--accent)' :
                             cgroupCpuioData.worst_verdict === 'unknown' ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {cgroupCpuioData.process_count} procs · {i18n.t("integrations.cgcpuio.verdict")} : <b>{cgroupCpuioData.worst_verdict}</b>
            </span>
          {/if}
        </div>
        {#if cgroupCpuioData?.worst_verdict === 'no_llm_procs'}
          <p class="muted" style="margin-top: 6px;">{i18n.t("integrations.cgcpuio.no_procs")}</p>
        {/if}
        {#if cgroupCpuioData?.processes && cgroupCpuioData.processes.length > 0}
          {#each cgroupCpuioData.processes as p}
            <div style="margin-top: 8px; padding: 8px;
                        border-left: 3px solid {
                          p.verdict.verdict === 'cpu_quota_active' ? 'var(--warn)' :
                          ['default_weight','unknown'].includes(p.verdict.verdict) ? 'var(--accent)' :
                          'var(--text-dim)'};">
              <div class="form-row" style="gap: 12px; flex-wrap: wrap;">
                <b style="font-family: monospace;">{p.comm}</b>
                <span class="kv">pid <b>{p.pid}</b></span>
                <span class="kv">{i18n.t("integrations.cgcpuio.cpuw")} : <b>{p.cpu_weight ?? '—'}</b></span>
                <span class="kv">{i18n.t("integrations.cgcpuio.iow")} : <b>{p.io_weight ?? '—'}</b></span>
                {#if p.cpu_max_quota !== null && p.cpu_max_quota !== undefined}
                  <span class="kv" style:color="var(--warn)">{i18n.t("integrations.cgcpuio.quota")} :
                    <b>{((p.cpu_max_quota / (p.cpu_max_period ?? 100000)) * 100).toFixed(0)}%</b>
                  </span>
                {/if}
                <span class="kv"><b>{p.verdict.verdict}</b></span>
              </div>
              <p style="margin: 4px 0;">{p.verdict.reason}</p>
              {#if p.verdict.recommendation}
                <details style="margin-top: 4px;">
                  <summary class="muted">{i18n.t("integrations.cgcpuio.recipe")}</summary>
                  <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                               border-radius: 4px; overflow-x: auto;">{p.verdict.recommendation}</pre>
                  <button class="btn btn-small"
                          onclick={() => copyToClipboard(p.verdict.recommendation)}>📋 Copy</button>
                </details>
              {/if}
            </div>
          {/each}
        {/if}
      </div>

      <!-- R&D #32.4 VM sysctl audit (UI sprint 23) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.vmsysctl.title")}</h4>
        <p class="muted">{i18n.t("integrations.vmsysctl.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadVmSysctl}>{i18n.t("integrations.vmsysctl.refresh")}</button>
          {#if vmSysctlData?.ok}
            <span class="kv" style="margin-left: 12px;"
                  style:color={vmSysctlData.worst_severity === 'warn' ? 'var(--warn)' :
                             vmSysctlData.worst_severity === 'unknown' ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {i18n.t("integrations.vmsysctl.severity")} : <b>{vmSysctlData.worst_severity}</b>
              · <b>{vmSysctlData.flagged_count}</b> {i18n.t("integrations.vmsysctl.flagged")}
            </span>
          {/if}
        </div>
        {#if vmSysctlData?.ok === false}
          <p class="muted" style="margin-top: 6px;">{i18n.t("integrations.vmsysctl.unavailable")}</p>
        {/if}
        {#if vmSysctlData?.rows && vmSysctlData.rows.length > 0}
          {#each vmSysctlData.rows as r}
            <div style="margin-top: 8px; padding: 8px;
                        border-left: 3px solid {
                          r.severity === 'warn' ? 'var(--warn)' :
                          r.severity === 'unknown' ? 'var(--accent)' :
                          'var(--text-dim)'};">
              <div class="form-row" style="gap: 12px; flex-wrap: wrap;">
                <b style="font-family: monospace;">vm.{r.name}</b>
                <span class="kv">current : <b>{r.value ?? '—'}</b></span>
                {#if r.recommended !== null}
                  <span class="kv">recommended : <b>{r.recommended}</b></span>
                {/if}
                <span class="kv"><b>{r.severity}</b></span>
              </div>
              <p class="muted" style="margin: 4px 0;">{r.reason}</p>
            </div>
          {/each}
        {/if}
        {#if vmSysctlData?.recipe}
          <details style="margin-top: 8px;">
            <summary class="muted">{i18n.t("integrations.vmsysctl.recipe")}</summary>
            <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                         border-radius: 4px; overflow-x: auto;">{vmSysctlData.recipe}</pre>
            <button class="btn btn-small"
                    onclick={() => copyToClipboard(vmSysctlData!.recipe!)}>📋 Copy</button>
          </details>
        {/if}
      </div>

      <!-- R&D #32.1 PSI pressure-stall correlator (UI sprint 23) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.psi.title")}</h4>
        <p class="muted">{i18n.t("integrations.psi.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadPsiPressure}>{i18n.t("integrations.psi.refresh")}</button>
          {#if psiPressureData?.ok}
            <span class="kv" style="margin-left: 12px;"
                  style:color={psiPressureData.worst_verdict === 'throttled' ? 'var(--warn)' :
                             psiPressureData.worst_verdict === 'elevated' ? 'var(--warn)' :
                             psiPressureData.worst_verdict === 'missing' ? 'var(--text-dim)' :
                             'var(--accent)'}>
              {i18n.t("integrations.psi.verdict")} : <b>{psiPressureData.worst_verdict}</b>
            </span>
          {/if}
        </div>
        {#if psiPressureData?.ok === false}
          <p class="muted" style="margin-top: 6px;">{i18n.t("integrations.psi.unavailable")}</p>
        {/if}
        {#if psiPressureData?.resources && psiPressureData.resources.length > 0}
          {#each psiPressureData.resources as r}
            <div style="margin-top: 8px; padding: 8px;
                        border-left: 3px solid {
                          r.verdict.verdict === 'throttled' ||
                          r.verdict.verdict === 'elevated' ? 'var(--warn)' :
                          r.verdict.verdict === 'missing' ? 'var(--text-dim)' :
                          'var(--accent)'};">
              <div class="form-row" style="gap: 12px; flex-wrap: wrap;">
                <b style="font-family: monospace;">{r.resource}</b>
                {#if r.psi.some}
                  <span class="kv">{i18n.t("integrations.psi.some")} :
                    <b>{r.psi.some.avg10.toFixed(2)}%</b>
                  </span>
                {/if}
                {#if r.psi.full}
                  <span class="kv">{i18n.t("integrations.psi.full")} :
                    <b>{r.psi.full.avg10.toFixed(2)}%</b>
                  </span>
                {/if}
                <span class="kv"><b>{r.verdict.verdict}</b></span>
              </div>
              <p style="margin: 4px 0;">{r.verdict.reason}</p>
              {#if r.verdict.recommendation}
                <details style="margin-top: 4px;">
                  <summary class="muted">{i18n.t("integrations.psi.recommend")}</summary>
                  <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                               border-radius: 4px; overflow-x: auto;">{r.verdict.recommendation}</pre>
                  <button class="btn btn-small"
                          onclick={() => copyToClipboard(r.verdict.recommendation)}>📋 Copy</button>
                </details>
              {/if}
            </div>
          {/each}
        {/if}
      </div>

      <!-- R&D #32.3 wchan + stack stuck debugger (UI sprint 23) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.wchan.title")}</h4>
        <p class="muted">{i18n.t("integrations.wchan.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadProcWchan}>{i18n.t("integrations.wchan.refresh")}</button>
          {#if procWchanData}
            <span class="kv" style="margin-left: 12px;"
                  style:color={['mem_pressure','page_cache_wait','io_bound','blocked'].includes(procWchanData.worst_verdict) ? 'var(--warn)' :
                             procWchanData.worst_verdict === 'zombie' ? 'var(--warn)' :
                             procWchanData.worst_verdict === 'unknown' ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {procWchanData.process_count} procs · {i18n.t("integrations.wchan.verdict")} : <b>{procWchanData.worst_verdict}</b>
            </span>
          {/if}
        </div>
        {#if procWchanData?.worst_verdict === 'no_llm_procs'}
          <p class="muted" style="margin-top: 6px;">{i18n.t("integrations.wchan.no_procs")}</p>
        {/if}
        {#if procWchanData?.processes && procWchanData.processes.length > 0}
          {#each procWchanData.processes as p}
            <div style="margin-top: 8px; padding: 8px;
                        border-left: 3px solid {
                          ['mem_pressure','page_cache_wait','io_bound','blocked','zombie'].includes(p.verdict.verdict) ? 'var(--warn)' :
                          p.verdict.verdict === 'unknown' ? 'var(--accent)' :
                          'var(--text-dim)'};">
              <div class="form-row" style="gap: 12px; flex-wrap: wrap;">
                <b style="font-family: monospace;">{p.comm}</b>
                <span class="kv">pid <b>{p.pid}</b></span>
                <span class="kv">{i18n.t("integrations.wchan.state")} : <b>{p.state ?? '—'}</b></span>
                <span class="kv">{i18n.t("integrations.wchan.wchan")} : <b style="font-family: monospace;">{p.wchan ?? '—'}</b></span>
                <span class="kv"><b>{p.verdict.verdict}</b></span>
              </div>
              <p style="margin: 4px 0;">{p.verdict.reason}</p>
              {#if p.stack && p.stack.length > 0}
                <details style="margin-top: 4px;">
                  <summary class="muted">{i18n.t("integrations.wchan.stack")} ({p.stack.length} frames)</summary>
                  <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                               border-radius: 4px; overflow-x: auto;">{p.stack.join('\n')}</pre>
                </details>
              {/if}
              {#if p.verdict.recommendation}
                <details style="margin-top: 4px;">
                  <summary class="muted">{i18n.t("integrations.wchan.recommend")}</summary>
                  <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                               border-radius: 4px; overflow-x: auto;">{p.verdict.recommendation}</pre>
                  <button class="btn btn-small"
                          onclick={() => copyToClipboard(p.verdict.recommendation)}>📋 Copy</button>
                </details>
              {/if}
            </div>
          {/each}
        {/if}
      </div>

      <!-- R&D #32.5 cgroup memory-cap scanner (UI sprint 23) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.cgmemcap.title")}</h4>
        <p class="muted">{i18n.t("integrations.cgmemcap.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadCgroupMemcap}>{i18n.t("integrations.cgmemcap.refresh")}</button>
          {#if cgroupMemcapData}
            <span class="kv" style="margin-left: 12px;"
                  style:color={['oom_killed','oom_capped','capped_below_model'].includes(cgroupMemcapData.worst_verdict) ? 'var(--warn)' :
                             ['swap_active','memory_high_throttle','capped_tight'].includes(cgroupMemcapData.worst_verdict) ? 'var(--warn)' :
                             cgroupMemcapData.worst_verdict === 'unknown' ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {cgroupMemcapData.process_count} procs · {i18n.t("integrations.cgmemcap.verdict")} : <b>{cgroupMemcapData.worst_verdict}</b>
            </span>
          {/if}
        </div>
        {#if cgroupMemcapData?.worst_verdict === 'no_llm_procs'}
          <p class="muted" style="margin-top: 6px;">{i18n.t("integrations.cgmemcap.no_procs")}</p>
        {/if}
        {#if cgroupMemcapData?.processes && cgroupMemcapData.processes.length > 0}
          {#each cgroupMemcapData.processes as p}
            <div style="margin-top: 8px; padding: 8px;
                        border-left: 3px solid {
                          ['oom_killed','oom_capped','capped_below_model','swap_active','memory_high_throttle','capped_tight'].includes(p.verdict.verdict) ? 'var(--warn)' :
                          p.verdict.verdict === 'unknown' ? 'var(--accent)' :
                          'var(--text-dim)'};">
              <div class="form-row" style="gap: 12px; flex-wrap: wrap;">
                <b style="font-family: monospace;">{p.comm}</b>
                <span class="kv">pid <b>{p.pid}</b></span>
                <span class="kv">{i18n.t("integrations.cgmemcap.max")} :
                  <b>{p.memory_max === null ? '—' :
                       p.memory_max >= 9_000_000_000_000_000_000 ? 'max' :
                       (p.memory_max / 1024**3).toFixed(1) + ' GiB'}</b>
                </span>
                <span class="kv">{i18n.t("integrations.cgmemcap.current")} :
                  <b>{p.memory_current === null ? '—' : (p.memory_current / 1024**3).toFixed(1) + ' GiB'}</b>
                </span>
                {#if p.memory_swap_current && p.memory_swap_current > 0}
                  <span class="kv" style:color="var(--warn)">
                    {i18n.t("integrations.cgmemcap.swap")} :
                    <b>{(p.memory_swap_current / 1024**2).toFixed(0)} MiB</b>
                  </span>
                {/if}
                <span class="kv"><b>{p.verdict.verdict}</b></span>
              </div>
              <p style="margin: 4px 0;">{p.verdict.reason}</p>
              {#if p.verdict.recommendation}
                <details style="margin-top: 4px;">
                  <summary class="muted">{i18n.t("integrations.cgmemcap.recipe")}</summary>
                  <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                               border-radius: 4px; overflow-x: auto;">{p.verdict.recommendation}</pre>
                  <button class="btn btn-small"
                          onclick={() => copyToClipboard(p.verdict.recommendation)}>📋 Copy</button>
                </details>
              {/if}
            </div>
          {/each}
        {/if}
      </div>

      <!-- R&D #31.4 OOM-priority for inference daemons (UI sprint 22) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.oom.title")}</h4>
        <p class="muted">{i18n.t("integrations.oom.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadOomPriority}>{i18n.t("integrations.oom.refresh")}</button>
          {#if oomPriorityData}
            <span class="kv" style="margin-left: 12px;"
                  style:color={oomPriorityData.worst_verdict === 'default' ? 'var(--warn)' :
                             oomPriorityData.worst_verdict === 'hardened' ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {oomPriorityData.process_count} procs · {i18n.t("integrations.oom.verdict")} : <b>{oomPriorityData.worst_verdict}</b>
            </span>
          {/if}
        </div>
        {#if oomPriorityData?.worst_verdict === 'no_llm_procs'}
          <p class="muted" style="margin-top: 6px;">{i18n.t("integrations.oom.no_procs")}</p>
        {/if}
        {#if oomPriorityData?.processes && oomPriorityData.processes.length > 0}
          {#each oomPriorityData.processes as p}
            <div style="margin-top: 8px; padding: 8px;
                        border-left: 3px solid {
                          p.verdict.verdict === 'default' ? 'var(--warn)' :
                          p.verdict.verdict === 'hardened' ? 'var(--accent)' :
                          'var(--text-dim)'};">
              <div class="form-row" style="gap: 12px; flex-wrap: wrap;">
                <b style="font-family: monospace;">{p.comm}</b>
                <span class="kv">pid <b>{p.pid}</b></span>
                <span class="kv">{i18n.t("integrations.oom.score")} : <b>{p.oom_score ?? '—'}</b></span>
                <span class="kv">{i18n.t("integrations.oom.adj")} : <b>{p.oom_score_adj ?? '—'}</b></span>
                <span class="kv">{i18n.t("integrations.oom.rss")} : <b>{p.vm_rss_bytes ? (p.vm_rss_bytes / 1024**3).toFixed(1) + ' GiB' : '—'}</b></span>
                <span class="kv"><b>{p.verdict.verdict}</b></span>
              </div>
              <p style="margin: 4px 0;">{p.verdict.reason}</p>
              {#if p.recipe}
                <details style="margin-top: 4px;">
                  <summary class="muted">{i18n.t("integrations.oom.recipe")}</summary>
                  <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                               border-radius: 4px; overflow-x: auto;">{p.recipe}</pre>
                  <button class="btn btn-small"
                          onclick={() => copyToClipboard(p.recipe)}>📋 Copy</button>
                </details>
              {/if}
            </div>
          {/each}
        {/if}
      </div>

      <!-- R&D #31.3 CPU topology + governor advisor (UI sprint 22) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.cpu.title")}</h4>
        <p class="muted">{i18n.t("integrations.cpu.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadCpuTopology}>{i18n.t("integrations.cpu.refresh")}</button>
          {#if cpuTopologyData}
            <span class="kv" style="margin-left: 12px;"
                  style:color={cpuTopologyData.verdict.verdict === 'powersave' ? 'var(--warn)' :
                             cpuTopologyData.verdict.verdict === 'hybrid_unaware' ? 'var(--warn)' :
                             cpuTopologyData.verdict.verdict === 'missing_cpufreq' ? 'var(--text-dim)' :
                             'var(--accent)'}>
              {i18n.t("integrations.cpu.verdict")} : <b>{cpuTopologyData.verdict.verdict}</b>
            </span>
          {/if}
        </div>
        {#if cpuTopologyData}
          <div style="margin-top: 8px; padding: 8px;
                      border-left: 3px solid {
                        cpuTopologyData.verdict.verdict === 'powersave' ||
                        cpuTopologyData.verdict.verdict === 'hybrid_unaware' ? 'var(--warn)' :
                        cpuTopologyData.verdict.verdict === 'missing_cpufreq' ? 'var(--text-dim)' :
                        'var(--accent)'};">
            <div class="form-row" style="gap: 12px; flex-wrap: wrap;">
              <span class="kv">{i18n.t("integrations.cpu.cpus")} : <b>{cpuTopologyData.cpu_count}</b></span>
              <span class="kv">{i18n.t("integrations.cpu.cores")} : <b>{cpuTopologyData.physical_cores}</b></span>
              <span class="kv">{i18n.t("integrations.cpu.smt")} : <b>{cpuTopologyData.smt_enabled ? 'on' : 'off'}</b></span>
              {#if cpuTopologyData.hybrid}
                <span class="kv" style:color="var(--warn)">{i18n.t("integrations.cpu.hybrid")} :
                  <b>{cpuTopologyData.hybrid.p_cores.length}P + {cpuTopologyData.hybrid.e_cores.length}E</b>
                </span>
              {/if}
              {#if cpuTopologyData.max_freq_mhz}
                <span class="kv">{i18n.t("integrations.cpu.max_freq")} : <b>{cpuTopologyData.max_freq_mhz} MHz</b></span>
              {/if}
            </div>
            <p style="margin: 4px 0;">{cpuTopologyData.verdict.reason}</p>
            {#if cpuTopologyData.verdict.recommendation}
              <details style="margin-top: 4px;">
                <summary class="muted">{i18n.t("integrations.cpu.recommend")}</summary>
                <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                             border-radius: 4px; overflow-x: auto;">{cpuTopologyData.verdict.recommendation}</pre>
                <button class="btn btn-small"
                        onclick={() => copyToClipboard(cpuTopologyData!.verdict.recommendation)}>📋 Copy</button>
              </details>
            {/if}
          </div>
        {/if}
      </div>

      <!-- R&D #31.2 smaps_rollup residence breakdown (UI sprint 22) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.smaps.title")}</h4>
        <p class="muted">{i18n.t("integrations.smaps.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadProcSmaps}>{i18n.t("integrations.smaps.refresh")}</button>
          {#if procSmapsData}
            <span class="kv" style="margin-left: 12px;"
                  style:color={procSmapsData.worst_verdict === 'swapping' ? 'var(--warn)' :
                             procSmapsData.worst_verdict === 'mmap_evicted' ? 'var(--warn)' :
                             procSmapsData.worst_verdict === 'unreadable' ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {procSmapsData.process_count} procs · {i18n.t("integrations.smaps.verdict")} : <b>{procSmapsData.worst_verdict}</b>
            </span>
          {/if}
        </div>
        {#if procSmapsData?.worst_verdict === 'no_llm_procs'}
          <p class="muted" style="margin-top: 6px;">{i18n.t("integrations.smaps.no_procs")}</p>
        {/if}
        {#if procSmapsData?.processes && procSmapsData.processes.length > 0}
          {#each procSmapsData.processes as p}
            <div style="margin-top: 8px; padding: 8px;
                        border-left: 3px solid {
                          p.verdict.verdict === 'swapping' ||
                          p.verdict.verdict === 'mmap_evicted' ? 'var(--warn)' :
                          p.verdict.verdict === 'unreadable' ? 'var(--accent)' :
                          'var(--text-dim)'};">
              <div class="form-row" style="gap: 12px; flex-wrap: wrap;">
                <b style="font-family: monospace;">{p.comm}</b>
                <span class="kv">pid <b>{p.pid}</b></span>
                <span class="kv">{i18n.t("integrations.smaps.rss")} : <b>{(p.rss_bytes / 1024**3).toFixed(1)} GiB</b></span>
                <span class="kv">{i18n.t("integrations.smaps.file")} : <b>{(p.pss_file_bytes / 1024**3).toFixed(1)} GiB</b></span>
                <span class="kv">{i18n.t("integrations.smaps.anon")} : <b>{(p.pss_anon_bytes / 1024**3).toFixed(1)} GiB</b></span>
                {#if p.swap_bytes > 0}
                  <span class="kv" style:color="var(--warn)">{i18n.t("integrations.smaps.swap")} :
                    <b>{(p.swap_bytes / 1024**3).toFixed(1)} GiB</b>
                  </span>
                {/if}
                <span class="kv"><b>{p.verdict.verdict}</b></span>
              </div>
              <p style="margin: 4px 0;">{p.verdict.reason}</p>
              {#if p.verdict.recommendation}
                <details style="margin-top: 4px;">
                  <summary class="muted">{i18n.t("integrations.smaps.recipe")}</summary>
                  <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                               border-radius: 4px; overflow-x: auto;">{p.verdict.recommendation}</pre>
                  <button class="btn btn-small"
                          onclick={() => copyToClipboard(p.verdict.recommendation)}>📋 Copy</button>
                </details>
              {/if}
            </div>
          {/each}
        {/if}
      </div>

      <!-- R&D #31.1 hwmon NVMe + chipset parity (UI sprint 22) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.hwmon.title")}</h4>
        <p class="muted">{i18n.t("integrations.hwmon.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadHwmonInventory}>{i18n.t("integrations.hwmon.refresh")}</button>
          {#if hwmonInventoryData}
            <span class="kv" style="margin-left: 12px;"
                  style:color={hwmonInventoryData.worst_verdict === 'cpu_hot' ||
                              hwmonInventoryData.worst_verdict === 'chipset_hot' ||
                              hwmonInventoryData.worst_verdict === 'nvme_hot' ? 'var(--warn)' :
                             hwmonInventoryData.worst_verdict === 'no_hwmon' ? 'var(--text-dim)' :
                             'var(--accent)'}>
              {hwmonInventoryData.device_count} devices · {i18n.t("integrations.hwmon.verdict")} : <b>{hwmonInventoryData.worst_verdict}</b>
            </span>
            {#if hwmonInventoryData.max_temp_c !== null}
              <span class="kv">{i18n.t("integrations.hwmon.max_temp")} : <b>{hwmonInventoryData.max_temp_c.toFixed(1)} °C</b></span>
            {/if}
          {/if}
        </div>
        {#if hwmonInventoryData?.worst_verdict === 'no_hwmon'}
          <p class="muted" style="margin-top: 6px;">{i18n.t("integrations.hwmon.no_hwmon")}</p>
        {/if}
        {#if hwmonInventoryData?.devices && hwmonInventoryData.devices.length > 0}
          {#each hwmonInventoryData.devices as d}
            <div style="margin-top: 8px; padding: 8px;
                        border-left: 3px solid var(--text-dim);">
              <div class="form-row" style="gap: 12px; flex-wrap: wrap;">
                <b style="font-family: monospace;">{d.name} ({d.kind})</b>
                {#each d.sensors as s}
                  <span class="kv"
                        style:color={s.value_c !== null && s.max_c !== null && s.value_c > s.max_c * 0.9 ? 'var(--warn)' : ''}>
                    {s.label ?? `ch${s.channel}`} : <b>{s.value_c?.toFixed(1) ?? '—'} °C</b>
                  </span>
                {/each}
                {#each d.fans as f}
                  <span class="kv">{f.label ?? `fan${f.channel}`} : <b>{f.rpm ?? '—'} RPM</b></span>
                {/each}
              </div>
            </div>
          {/each}
        {/if}
        {#if hwmonInventoryData?.verdict?.recommendation}
          <details style="margin-top: 8px;">
            <summary class="muted">{i18n.t("integrations.hwmon.recommend")}</summary>
            <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                         border-radius: 4px; overflow-x: auto;">{hwmonInventoryData.verdict.recommendation}</pre>
            <button class="btn btn-small"
                    onclick={() => copyToClipboard(hwmonInventoryData!.verdict!.recommendation)}>📋 Copy</button>
          </details>
        {/if}
      </div>

      <!-- R&D #30.1 MSI-X vector inventory (UI sprint 21) -->
      <div class="card-form" hidden={modal.section !== "integrations"}>
        <h4>{i18n.t("integrations.msi.title")}</h4>
        <p class="muted">{i18n.t("integrations.msi.desc")}</p>
        <div class="form-row">
          <button class="btn" onclick={loadMsiInventory}>{i18n.t("integrations.msi.refresh")}</button>
          {#if msiInventoryData}
            <span class="kv" style="margin-left: 12px;"
                  style:color={msiInventoryData.worst_verdict === 'legacy_irq' ? 'var(--warn)' :
                             msiInventoryData.worst_verdict === 'msi_active' ? 'var(--warn)' :
                             msiInventoryData.worst_verdict === 'unknown' ? 'var(--accent)' :
                             'var(--text-dim)'}>
              {msiInventoryData.device_count} GPU · {i18n.t("integrations.msi.verdict")} : <b>{msiInventoryData.worst_verdict}</b>
            </span>
          {/if}
        </div>
        {#if msiInventoryData?.cards && msiInventoryData.cards.length > 0}
          {#each msiInventoryData.cards as c}
            <div style="margin-top: 8px; padding: 8px;
                        border-left: 3px solid {
                          c.verdict.verdict === 'legacy_irq' ||
                          c.verdict.verdict === 'msi_active' ? 'var(--warn)' :
                          c.verdict.verdict === 'unknown' ? 'var(--accent)' :
                          'var(--text-dim)'};">
              <div class="form-row" style="gap: 12px; flex-wrap: wrap;">
                <b style="font-family: monospace;">{c.gpu_bdf}</b>
                <span class="kv">{i18n.t("integrations.msi.mode")} : <b>{c.mode}</b></span>
                <span class="kv">{i18n.t("integrations.msi.vectors")} : <b>{c.vector_count}</b></span>
                <span class="kv">{i18n.t("integrations.msi.interrupts")} : <b>{c.total_interrupts.toLocaleString()}</b></span>
                <span class="kv"><b>{c.verdict.verdict}</b></span>
              </div>
              <p style="margin: 4px 0;">{c.verdict.reason}</p>
              {#if c.verdict.recommendation}
                <details style="margin-top: 4px;">
                  <summary class="muted">{i18n.t("integrations.msi.recipe")}</summary>
                  <pre style="font-size: 0.8em; padding: 6px; background: var(--bg-2);
                               border-radius: 4px; overflow-x: auto;">{c.verdict.recommendation}</pre>
                  <button class="btn btn-small"
                          onclick={() => copyToClipboard(c.verdict.recommendation)}>📋 Copy</button>
                </details>
              {/if}
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
