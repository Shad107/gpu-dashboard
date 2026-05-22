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
  let lifetimeStats = $state<Awaited<ReturnType<typeof api.lifetimeStats>> | null>(null);
  async function loadAbout() {
    try { aboutData = await api.about(); } catch { aboutData = null; }
    try { lifetimeStats = await api.lifetimeStats(); } catch { lifetimeStats = null; }
  }
  $effect(() => {
    if (modal.open && modal.section === "about" && !aboutData) loadAbout();
    if (modal.open && modal.section === "about") loadIdleAudit();
    if (modal.open && modal.section === "about") loadEccHealth();
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
          <pre class="logs-pre">{(logsData.lines || []).join("")}</pre>
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
