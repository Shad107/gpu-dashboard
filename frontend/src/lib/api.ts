// Typed bindings for the Python backend HTTP API.

export type Sample = {
  ts: string;
  temp: number;
  fan: number;
  clk_gpu: number;
  clk_mem: number;
  power: number;
  fan0_rpm?: number;
  fan1_rpm?: number;
};

export type Gpu =
  | { alive: false; name: string; index?: number }
  | {
      alive: true;
      index?: number;
      name: string;
      temp: number;
      fan_pct: number;
      power: number;
      power_limit: number;
      util_gpu: number;
      mem_used_mib: number;
      mem_total_mib: number;
      mem_temp?: number | null;
      vbios_version?: string | null;
      util_enc?: number | null;
      util_dec?: number | null;
      pcie_gen?: number | null;
      pcie_gen_max?: number | null;
      pcie_width?: number | null;
      pcie_width_max?: number | null;
    };

export type Fan = { idx: number; rpm?: number; pct?: number; target?: number };

export type Tuning = {
  clocks?: { gr_now?: number; mem_now?: number; gr_max?: number; mem_max?: number; pstate?: string };
  offsets?: {
    GPUGraphicsClockOffset?: number;
    GPUMemoryTransferRateOffset?: number;
    GPUGraphicsClockOffsetAllPerformanceLevels?: number;
    GPUMemoryTransferRateOffsetAllPerformanceLevels?: number;
  };
};

export type Watchdog =
  | { available: false }
  | { available: true; drops: number; last_uptime: string };

export type GpuInfo = {
  index: number;
  name: string;
  bus_id: string;
  vram_mib: number | null;
};

export type GpuProcess = {
  pid: number;
  name: string;
  vram_mib: number;
  cmdline?: string | null;
};

export type State = {
  gpu: Gpu;
  gpus_available?: GpuInfo[];
  selected_gpu_index?: number;
  metrics: Sample[];
  profile: { model: string } | null;
  fans: Fan[];
  tuning: Tuning;
  watchdog: Watchdog;
  services: Record<string, string>;
  fan_dist: Record<string, number>;
  llm_model: string;
  processes?: GpuProcess[];
  setup_required?: boolean;
};

export type ModuleRec = {
  name: string;
  available: boolean;
  recommend: boolean;
  reason: string;
};

export type SetupDetect = {
  ok: boolean;
  env: {
    os: { id: string | null; pretty_name: string | null; package_manager: string | null };
    nvidia: {
      available: boolean;
      driver_version: string | null;
      gpus: { name: string; bus_id: string; vram_mib: number; driver_version: string }[];
    };
    coolbits: { enabled: boolean; value: number | null; source: string | null };
    virt: { is_vm: boolean; type: string };
    external_gpu: { link_width: number | null; link_speed: string | null; likely_external: boolean };
    power_wrapper_exists: boolean;
  };
  modules: ModuleRec[];
  profile: { model: string } | null;
  setup_required: boolean;
};

export type AlertsConfig = {
  enabled: boolean;
  token: string;
  chat_id: string;
  on_drop: boolean;
  on_recover: boolean;
};

export type HistorySample = {
  ts: number;
  temp: number | null;
  fan_pct: number | null;
  fan0_rpm: number | null;
  fan1_rpm: number | null;
  clk_gpu: number | null;
  clk_mem: number | null;
  power: number | null;
  power_limit: number | null;
  util_gpu: number | null;
  mem_used_mib: number | null;
  tokens_total_snapshot?: number | null;
};

export type StoredEvent = {
  ts: number;
  kind: string;
  payload: any | null;
};

/**
 * Thrown when /api/* requests fail in a way that strongly suggests
 * the Python backend isn't running — typically the vite dev proxy
 * returning 502/503/504 because it can't reach localhost:9999, or
 * fetch() itself rejecting with a TypeError.
 *
 * Hardening #11 — surfaces a single clear message to existing
 * toast handlers (e.g. `toast.emit("✗ " + (e as Error).message)`)
 * so cards stop showing cryptic "HTTP 502" / "Unexpected token" /
 * "Failed to fetch" strings when the user forgets to start the
 * service.
 */
export class BackendOfflineError extends Error {
  constructor(detail?: string) {
    super(
      "Backend offline — is the gpu-dashboard service running?" +
        (detail ? ` (${detail})` : "")
    );
    this.name = "BackendOfflineError";
  }
}

async function jsonOf<T>(r: Response): Promise<T> {
  // 502/503/504 from the vite dev proxy or any reverse proxy in
  // front of the python backend almost always means the backend
  // process is down. Surface a friendly message.
  if (r.status === 502 || r.status === 503 || r.status === 504) {
    throw new BackendOfflineError(`HTTP ${r.status}`);
  }
  if (!r.ok && r.status !== 400 && r.status !== 500) {
    throw new Error(`HTTP ${r.status}`);
  }
  return r.json();
}

/**
 * Wraps fetch() so a TypeError ("Failed to fetch" / "NetworkError")
 * — which is what the browser throws when the server is genuinely
 * unreachable rather than returning a 5xx — becomes a
 * BackendOfflineError with the same friendly message.
 *
 * Existing callsites can opt in by replacing `fetch(...)` with
 * `safeFetch(...)`; the return type is identical so no other code
 * needs to change.
 */
export async function safeFetch(
  input: RequestInfo | URL,
  init?: RequestInit,
): Promise<Response> {
  try {
    return await fetch(input, init);
  } catch (e) {
    if (e instanceof TypeError) {
      throw new BackendOfflineError(e.message);
    }
    throw e;
  }
}

export const api = {
  state: (gpu = 0) => fetch("/api/state" + (gpu ? `?gpu_index=${gpu}` : ""), { cache: "no-store" }).then(jsonOf<State>),

  setPowerLimit: (watts: number) =>
    fetch("/api/set-power-limit", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ watts }),
    }).then(jsonOf<{ ok: boolean; watts: number; error?: string }>),

  setOffsets: (gpu: number, mem: number) =>
    fetch("/api/set-offsets", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ gpu, mem }),
    }).then(jsonOf<{ ok: boolean; gpu: number; mem: number; error?: string }>),

  alertsConfig: () => fetch("/api/alerts-config").then(jsonOf<AlertsConfig>),

  saveAlertsConfig: (c: AlertsConfig) =>
    fetch("/api/alerts-config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(c),
    }).then(jsonOf<{ ok: boolean; error?: string }>),

  testAlert: () =>
    fetch("/api/alerts-test", { method: "POST" }).then(
      jsonOf<{ ok: boolean; msg?: string; error?: string }>
    ),

  history: (from: number, to?: number, step?: number, gpu = 0) => {
    const q = new URLSearchParams({ from: String(from) });
    if (to !== undefined) q.set("to", String(to));
    if (step !== undefined) q.set("step", String(step));
    if (gpu) q.set("gpu_index", String(gpu));
    return fetch(`/api/history?${q.toString()}`).then(
      jsonOf<{ ok: boolean; samples: HistorySample[] }>
    );
  },

  events: (from: number, kind?: string) => {
    const q = new URLSearchParams({ from: String(from) });
    if (kind) q.set("kind", kind);
    return fetch(`/api/events?${q.toString()}`).then(
      jsonOf<{ ok: boolean; events: StoredEvent[] }>
    );
  },

  exportCsvUrl: (since: number) => `/api/export?since=${since}`,

  snapshotUrl: () => "/api/snapshot",

  setupDetect: () => fetch("/api/setup/detect").then(jsonOf<SetupDetect>),

  setupRecheck: (module: string) =>
    fetch(`/api/setup/recheck/${encodeURIComponent(module)}`).then(
      jsonOf<{ ok: boolean; reason: string }>
    ),

  setupSave: (payload: {
    modules: Record<string, boolean>;
    port?: number;
    bind?: string;
    power_default?: number;
    llm_server_url?: string;
  }) =>
    fetch("/api/setup/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }).then(jsonOf<{ ok: boolean; path?: string; error?: string }>),

  restart: () =>
    fetch("/api/restart", { method: "POST" }).then(
      jsonOf<{ ok: boolean; message?: string }>
    ),

  stop: () =>
    fetch("/api/stop", { method: "POST" }).then(
      jsonOf<{ ok: boolean; message?: string }>
    ),

  profileStats: (sinceSeconds = 86400) =>
    fetch(`/api/profile-stats?since=${sinceSeconds}`).then(jsonOf<{
      ok: boolean;
      totals: Record<string, number>;
      now: number;
      since_seconds: number;
      events_count: number;
      recent_events: { ts: number; to: string }[];
    }>),

  about: () =>
    fetch("/api/about").then(jsonOf<{
      version: string;
      uptime_seconds: number;
      python_version: string;
      platform: string;
      config_path: string;
      storage_path: string;
      license: string;
      repo_url: string;
      vbios_version: string | null;
    }>),

  updateCheck: () =>
    fetch("/api/update/check").then(jsonOf<{
      ok: boolean;
      current_sha?: string;
      remote_sha?: string;
      behind?: number | null;
      last_remote_msg?: string | null;
      error?: string;
    }>),

  updatePull: () =>
    fetch("/api/update/pull", { method: "POST" }).then(jsonOf<{
      ok: boolean;
      output?: string;
      error?: string;
      stderr?: string;
      dirty_files?: string[];
    }>),

  logs: (tail = 100) =>
    fetch(`/api/logs?tail=${tail}`).then(jsonOf<{
      ok: boolean;
      source?: "file" | "journalctl";
      path?: string;
      unit?: string;
      lines?: string[];
      reason?: string;
      total?: number;
    }>),

  profileSave: (profile: object) =>
    fetch("/api/profile/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(profile),
    }).then(jsonOf<{ ok: boolean; path?: string; model?: string; error?: string }>),

  powerProfilesList: () =>
    fetch("/api/power-profiles").then(jsonOf<{
      profiles: { name: "silent" | "sweet" | "boost"; watts: number; gpu_offset: number; mem_offset: number }[];
    }>),

  powerProfileApply: (name: string) =>
    fetch(`/api/power-profiles/apply/${encodeURIComponent(name)}`, {
      method: "POST",
    }).then(jsonOf<{
      ok: boolean;
      applied_profile?: string;
      watts?: number;
      gpu_offset?: number;
      mem_offset?: number;
      error?: string;
    }>),

  electricityConfigSave: (price_per_kwh: number, currency: string) =>
    fetch("/api/electricity/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ price_per_kwh, currency }),
    }).then(jsonOf<{
      ok: boolean;
      price_per_kwh?: number;
      currency?: string;
      error?: string;
    }>),

  // Helper : append &gpu_index=N to a URL if N > 0 (default 0 = back-compat, no query)
  // Multi-GPU query helpers — all data endpoints accept ?gpu_index=N

  llmStats: (gpu = 0) =>
    fetch("/api/llm/stats" + (gpu ? `?gpu_index=${gpu}` : "")).then(jsonOf<{
      available: boolean;
      tokens_generated_total?: number;
      prompt_tokens_total?: number;
      tokens_per_watt?: number | null;
      reason?: string;
    }>),

  pushVapid: () =>
    fetch("/api/push/vapid").then(jsonOf<{ ok: boolean; public_key: string }>),

  pushStatus: () =>
    fetch("/api/push/status").then(jsonOf<{
      ok: boolean;
      count: number;
      vapid_public_key: string | null;
    }>),

  pushSubscribe: (sub: any) =>
    fetch("/api/push/subscribe", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(sub),
    }).then(jsonOf<{ ok: boolean; error?: string }>),

  pushUnsubscribe: (endpoint: string) =>
    fetch("/api/push/unsubscribe", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ endpoint }),
    }).then(jsonOf<{ ok: boolean; removed?: number }>),

  llmPerf: (gpu = 0) =>
    fetch("/api/llm/perf" + (gpu ? `?gpu_index=${gpu}` : "")).then(jsonOf<{
      ok: boolean;
      available: boolean;
      now?: number;
      avg_tps_1m?: number;
      avg_tps_5m?: number;
      avg_tps_1h?: number;
      avg_tps_24h?: number;
      peak_tps?: number;
      peak_ts?: number;
      series_1h?: number[];
    }>),

  thermalStats: (gpu = 0) =>
    fetch("/api/thermal-stats" + (gpu ? `?gpu_index=${gpu}` : "")).then(jsonOf<{
      ok: boolean;
      avg_temp_24h: number;
      peak_temp_24h: number;
      time_above_80c_seconds: number;
      series_24h: number[];
      samples_count: number;
    }>),

  powerStats: (gpu = 0) =>
    fetch("/api/power-stats" + (gpu ? `?gpu_index=${gpu}` : "")).then(jsonOf<{
      ok: boolean;
      avg_watts_24h: number;
      peak_watts_24h: number;
      peak_ts: number;
      kwh_today: number;
      cost_today: number;
      kwh_year: number;
      cost_year: number;
      year_start_ts: number;
      kwh_month: number;
      cost_month: number;
      month_start_ts: number;
      month_end_ts: number;
      month_progress_pct: number;
      forecast_kwh: number;
      budget_kwh: number;
      over_budget: boolean;
      currency: string;
      price_per_kwh: number;
      series_24h: number[];
      samples_count: number;
    }>),

  llmLifetime: (gpu = 0) =>
    fetch("/api/llm/lifetime" + (gpu ? `?gpu_index=${gpu}` : "")).then(jsonOf<{
      ok: boolean;
      available: boolean;
      since_ts: number | null;
      latest_snapshot: number;
      total_tokens_generated: number;
      total_tokens_this_year: number;
      year_start_ts: number;
      restart_count: number;
      avg_power_watts: number;
      avg_tokens_per_watt: number | null;
    }>),

  getAppTriggers: () =>
    fetch("/api/app-triggers").then(jsonOf<{
      ok: boolean;
      triggers: Record<string, string>;
    }>),

  setAppTriggers: (triggers: Record<string, string>) =>
    fetch("/api/app-triggers", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ triggers }),
    }).then(jsonOf<{
      ok: boolean;
      triggers?: Record<string, string>;
      error?: string;
    }>),

  versionInfo: () =>
    fetch("/api/version").then(jsonOf<{
      ok: boolean;
      version: string;
      schema_version: number | null;
      modules_enabled: string[];
    }>),

  clockEvents: () =>
    fetch("/api/clock-events").then(jsonOf<{
      ok: boolean;
      available: boolean;
      reasons: Array<{ key: string; label: string; hint: string }>;
      raw: Record<string, boolean>;
    }>),

  idleAudit: () =>
    fetch("/api/idle-audit").then(jsonOf<{
      ok: boolean;
      available: boolean;
      status?: "idle" | "busy" | "unknown";
      verdict_kind?: "ok" | "high";
      verdict?: string;
      name?: string;
      power?: number;
      util_gpu?: number;
      pstate?: string;
      persistence_mode?: string;
      baseline?: { low: number; high: number; family: string };
      checklist?: Array<{ key: string; label: string; hint: string }>;
    }>),

  thermalCoach: () =>
    fetch("/api/thermal/coach").then(jsonOf<{
      ok: boolean;
      available: boolean;
      reason?: string;
      current_temp_c?: number;
      slowdown_temp_c?: number;
      headroom_c?: number;
      slope_c_per_min?: number;
      projected_throttle_s?: number | null;
      suggested_fan_delta_pct?: number;
      suggested_msg_key?: "stable" | "fan_can_be_gentler" | "fan_slight_gentler" | "fan_needs_help" | "warming_up";
      sample_count?: number;
    }>),

  drift: () =>
    fetch("/api/drift").then(jsonOf<{
      ok: boolean;
      has_baseline: boolean;
      current: Record<string, any>;
      last_drift: {
        ts: number;
        diffs: Array<{ field: string; old: any; new: any }>;
      } | null;
      history_count: number;
    }>),

  eccHealth: () =>
    fetch("/api/ecc-health").then(jsonOf<{
      ok: boolean;
      available: boolean;
      ecc_mode?: string | null;
      corrected_total?: number | null;
      uncorrected_total?: number | null;
      remapped_correctable?: number | null;
      remapped_uncorrectable?: number | null;
      remapped_pending?: number | null;
      remapped_failure?: number | null;
      verdict_kind?: "ok" | "watch" | "failing";
      verdict_msg?: string;
    }>),

  lifetimeStats: (gpu = 0) =>
    fetch("/api/lifetime-stats" + (gpu ? `?gpu_index=${gpu}` : "")).then(jsonOf<{
      ok: boolean;
      gpu_index: number;
      samples_count: number;
      first_ts: number | null;
      last_ts: number | null;
      peak_temp_c: number | null;
      peak_power_w: number | null;
      peak_fan_pct: number | null;
      peak_fan_rpm: number | null;
      lowest_idle_power_w: number | null;
    }>),

  runBenchmark: (opts: { profileA: string; profileB: string; durationS: number }) =>
    fetch("/api/benchmark/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        profile_a: opts.profileA, profile_b: opts.profileB, duration_s: opts.durationS,
      }),
    }).then(jsonOf<{
      ok: boolean;
      error?: string;
      segment_a: any;
      segment_b: any;
      comparison: {
        profile_a: string;
        profile_b: string;
        delta: Record<string, number>;
        winners: Record<string, string>;
      };
    }>),

  powerHeatmap: (days = 7, gpu = 0) =>
    fetch(`/api/power-heatmap?days=${days}` + (gpu ? `&gpu_index=${gpu}` : "")).then(jsonOf<{
      ok: boolean;
      days: number;
      currency: string;
      price_per_kwh: number;
      hours: {
        hour: number;
        avg_watts: number;
        kwh_per_hour: number;
        cost_per_hour: number;
        sample_count: number;
      }[];
    }>),

  // ── R&D #16 features (UI sprint cycle 7) ───────────────────────────────
  drBundleList: () =>
    fetch("/api/dr-bundle").then(jsonOf<{
      ok: boolean;
      bundles: Array<{ name: string; path: string; size_bytes: number; ts: number }>;
    }>),

  drBundleBuild: () =>
    fetch("/api/dr-bundle", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    }).then(jsonOf<{
      ok: boolean;
      path?: string; name?: string;
      size_bytes?: number; file_count?: number;
      snapshot_db_included?: boolean;
      error?: string;
    }>),

  drBundleDelete: (name: string) =>
    fetch(`/api/dr-bundle/delete/${encodeURIComponent(name)}`,
          { method: "POST" }).then(jsonOf<{ ok: boolean; deleted?: string }>),

  lmStudioInventory: () =>
    fetch("/api/lm-studio/inventory").then(jsonOf<{
      ok: boolean;
      available: boolean;
      reason?: string;
      models_dir?: string;
      models_count?: number;
      total_size_gib?: number;
      duplication_suspect_count?: number;
      duplication_suspect_gib?: number;
      models?: Array<{
        path: string; name: string; size_mib: number;
        is_gguf: boolean; quant?: string | null; dir_top: string;
      }>;
    }>),

  driverVaultStatus: () =>
    fetch("/api/driver-vault").then(jsonOf<{
      ok: boolean;
      current: { package: string; version: string } | null;
      vaulted: Array<{ name: string; size_bytes: number; ts: number }>;
      vault_max: number;
      recent_events: Array<{ start: string; action: string;
                              packages: Array<{ name: string; ver_from: string | null; ver_to: string | null }> }>;
    }>),

  driverVaultStash: () =>
    fetch("/api/driver-vault/stash", { method: "POST" }).then(jsonOf<{
      ok: boolean;
      vaulted_path?: string;
      current?: { package: string; version: string };
      reason?: string;
    }>),

  driverVaultRollbackScript: (name: string) =>
    fetch(`/api/driver-vault/rollback-script?name=${encodeURIComponent(name)}`)
      .then(jsonOf<{ ok: boolean; script?: string; target?: string;
                      current_package?: string; error?: string }>),

  // ── R&D #15 features (UI sprint cycle 6) ───────────────────────────────
  bootProfileStatus: () =>
    fetch("/api/boot-profile").then(jsonOf<{
      ok: boolean;
      configured: boolean;
      profile: { name: string; power_limit_w?: number;
                 persistence_mode?: boolean;
                 gpu_clock_offset_mhz?: number;
                 mem_clock_offset_mhz?: number;
                 fan_curve?: number[][] } | null;
      last_outcome: any | null;
      history_count: number;
    }>),

  bootProfileSave: (payload: Record<string, any>) =>
    fetch("/api/boot-profile", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }).then(jsonOf<{ ok: boolean; saved_name?: string; error?: string }>),

  bootProfileClear: () =>
    fetch("/api/boot-profile/clear", { method: "POST" })
      .then(jsonOf<{ ok: boolean; deleted?: boolean }>),

  bootProfileApplyNow: () =>
    fetch("/api/boot-profile/apply-now", { method: "POST" })
      .then(jsonOf<{ ok: boolean; ready_probe?: any; applied?: any; errors?: any[] }>),

  tariffStatus: () =>
    fetch("/api/tariff/status").then(jsonOf<{
      ok: boolean;
      available: boolean;
      reason?: string;
      current_hour?: number;
      current_eur_per_kwh?: number;
      day_min_eur_per_kwh?: number;
      day_max_eur_per_kwh?: number;
      day_avg_eur_per_kwh?: number;
      cheapest_hours?: number[];
      peak_hours?: number[];
    }>),

  tariffCheapest: (watts: number, duration_s: number, within_h = 24) =>
    fetch(`/api/tariff/cheapest?watts=${watts}&duration_s=${duration_s}&within_h=${within_h}`)
      .then(jsonOf<{
        ok: boolean;
        available: boolean;
        best?: { start_hour: number; hours_until_start: number; cost_eur: number; kwh: number };
        worst_for_comparison?: { cost_eur: number };
        absolute_savings_eur?: number;
        savings_pct?: number;
      }>),

  hfDedupPlan: () =>
    fetch("/api/hf-dedup/plan").then(jsonOf<{
      ok: boolean;
      available: boolean;
      reason?: string;
      files_scanned?: number;
      duplicate_groups?: number;
      reclaim_mib?: number;
      plan?: Array<{ keep: string; replace: string; size: number; sha: string }>;
      cross_device_skipped?: any[];
    }>),

  hfDedupExecute: (plan: any[], dry_run = true) =>
    fetch("/api/hf-dedup/execute", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ plan, dry_run }),
    }).then(jsonOf<{
      ok: boolean;
      dry_run: boolean;
      applied: number;
      errors: any[];
      bytes_reclaimed: number;
      reclaim_mib: number;
      report_path?: string;
    }>),

  discordRpcStatus: () =>
    fetch("/api/discord-rpc").then(jsonOf<{
      ok: boolean;
      enabled: boolean;
      discord_ipc_present: boolean;
      socket_path: string | null;
      app_id_configured: boolean;
    }>),

  // ── R&D #14 features (UI sprint cycle 5) ───────────────────────────────
  xidEvents: (since = "24h") =>
    fetch(`/api/xid?since=${encodeURIComponent(since)}`).then(jsonOf<{
      ok: boolean;
      available: boolean;
      total_24h: number;
      counts_by_severity: { info: number; warn: number; fail: number };
      worst_severity: "ok" | "info" | "warn" | "fail";
      events: Array<{
        code: number; name: string; cause?: string;
        severity: "info" | "warn" | "fail"; remediation?: string;
        known: boolean; gpu?: string; summary?: string; ts_iso?: string;
      }>;
    }>),

  hotSwapStatus: () =>
    fetch("/api/hot-swap").then(jsonOf<{
      ok: boolean;
      current: { ts: number; pci: Record<string, any>; drm: Record<string, string> };
      events: Array<{ kind: string; gpu?: string; target?: string;
                       before?: any; after?: any; ts?: number;
                       current?: any; max?: any }>;
      buffer_max: number;
    }>),

  inferenceCost: () =>
    fetch("/api/inference-cost").then(jsonOf<{
      ok: boolean;
      available: boolean;
      price_eur_per_kwh: number;
      headline_tok_per_wh: number | null;
      windows: Record<string, {
        window_s: number; tokens_delta: number; kwh: number; avg_watts: number;
        cost_gpu_eur: number; tok_per_wh_gpu: number | null;
        cost_per_1k_tokens_eur: number | null; restart_count: number;
        sample_count: number;
      }>;
    }>),

  labUsageLive: () =>
    fetch("/api/usage/users").then(jsonOf<{
      ts: number;
      total_vram_used_mib: number;
      watts_total: number | null;
      users: Array<{
        uid: number; name: string; pid_count: number;
        vram_used_mib: number; watts_share: number | null;
        processes: Array<{ pid: number; name: string; used_mib: number }>;
      }>;
    }>),

  // ── R&D #13 features (UI sprint cycle 4) ───────────────────────────────
  hotGpuWizard: () =>
    fetch("/api/hot-gpu-wizard").then(jsonOf<{
      ok: boolean;
      verdict: "pass" | "warn" | "fail" | "skip";
      steps: Array<{
        step: string;
        kind: "pass" | "warn" | "fail" | "skip";
        detail: string;
        fix?: string;
        [k: string]: any;
      }>;
      actions: string[];
      ts: number;
    }>),

  vramQuotaStatus: () =>
    fetch("/api/vram-quota").then(jsonOf<{
      ok: boolean;
      rules: Array<{ id: string; process_regex: string; max_vram_mib: number;
                     grace_s?: number; action: string }>;
      audit: Array<{ ts: number; pid: number; name: string;
                     used_mib: number; max_mib: number;
                     action: string; escalation: string;
                     breached_for_s: number; dry_run: boolean; rule_id: string }>;
      actions_supported: string[];
    }>),

  vramQuotaSave: (rules: any[]) =>
    fetch("/api/vram-quota", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ rules }),
    }).then(jsonOf<{ ok: boolean; count?: number; errors?: string[] }>),

  vramQuotaEvaluate: (dryRun = true) =>
    fetch(`/api/vram-quota/evaluate?dry_run=${dryRun ? 1 : 0}`).then(jsonOf<{
      ok: boolean; dry_run: boolean;
      fires: Array<{ rule_id: string; pid: number; name: string;
                     used_mib: number; max_mib: number; action: string;
                     escalation: string; breached_for_s: number; dry_run: boolean }>;
    }>),

  carbon: () =>
    fetch("/api/carbon").then(jsonOf<{
      ok: boolean;
      available: boolean;
      reason?: string;
      csv_path?: string;
      current_gco2_per_kwh?: number;
      current_hour?: number;
      gco2_today_g?: number;
      gco2_month_kg?: number;
      gco2_per_token_g?: number;
      day_min_gco2_per_kwh?: number;
      day_max_gco2_per_kwh?: number;
      day_avg_gco2_per_kwh?: number;
    }>),

  bestGpu: () =>
    fetch("/api/best-gpu").then(jsonOf<{
      ok: boolean;
      available: boolean;
      best_index?: number;
      best_score?: number;
      shell_export?: string;
      reasoning?: string;
      ranked?: Array<{ index: number; name: string; temp_c: number | null;
                       util_pct: number | null; vram_used_mib: number | null;
                       vram_total_mib: number | null; score: number }>;
    }>),

  // ── R&D #12.2 — disk health (UI sprint cycle 3) ────────────────────────
  diskHealth: () =>
    fetch("/api/disk-health").then(jsonOf<{
      ok: boolean;
      available: boolean;
      reason?: string;
      device_count?: number;
      worst_verdict?: "ok" | "warn" | "fail";
      disks?: Array<{
        device: string;
        available?: boolean;
        reason?: string;
        model?: string;
        is_nvme?: boolean;
        temp_c?: number | null;
        power_on_hours?: number | null;
        reallocated_sectors?: number | null;
        pending_sectors?: number | null;
        wearout_pct?: number | null;
        data_units_written_tb?: number | null;
        critical_warning_flags?: number | null;
        verdict?: { kind: "ok" | "warn" | "fail"; reasons: string[] };
      }>;
    }>),

  // ── R&D #12.7 — air-gap mode (UI sprint cycle 3) ───────────────────────
  airgapStatus: () =>
    fetch("/api/airgap/status").then(jsonOf<{
      ok: boolean;
      enabled: boolean;
      lan_allowed: boolean;
      blocked_count_24h: number;
      blocked_count_total: number;
    }>),

  airgapAudit: (limit = 50) =>
    fetch(`/api/airgap/audit?limit=${limit}`).then(jsonOf<{
      ok: boolean;
      count: number;
      blocked: Array<{ ts: number; url: string; reason: string }>;
    }>),

  // ── R&D #12.1 — wall-meter (UI sprint cycle 3) ─────────────────────────
  wallMeter: () =>
    fetch("/api/wall-meter").then(jsonOf<{
      ok: boolean;
      available: boolean;
      reason?: string;
      kind?: string;
      url?: string;
      wall_w?: number;
      baseline_w?: number;
      headroom_w?: number;
      gpu_w?: number | null;
      psu_efficiency_pct?: number | null;
    }>),

  // ── R&D #12.3 — LAN peers (UI sprint cycle 3) ──────────────────────────
  peers: () =>
    fetch("/api/peers").then(jsonOf<{
      ok: boolean;
      count: number;
      ttl_s: number;
      peers: Array<{
        host: string;
        ip: string;
        port: number;
        gpu_count: number;
        gpu_model: string;
        version: string;
        last_seen_ts: number;
      }>;
    }>),

  // ── R&D #6.1 — Notif Hub channels (UI sprint cycle 2) ──────────────────
  notifChannelsList: () =>
    fetch("/api/notif/channels").then(jsonOf<{
      ok: boolean;
      channels: Array<Record<string, any>>;
      types_supported: string[];
    }>),

  notifChannelSave: (payload: Record<string, any>) =>
    fetch("/api/notif/channels", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }).then(jsonOf<{ ok: boolean; id?: string; deleted?: string; error?: string }>),

  notifChannelDelete: (id: string) =>
    fetch("/api/notif/channels", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ delete: id }),
    }).then(jsonOf<{ ok: boolean; deleted?: string }>),

  notifChannelTest: (channel: Record<string, any>) =>
    fetch("/api/notif/test", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(channel),
    }).then(jsonOf<{ ok: boolean; msg: string }>),

  // ── R&D #9.3 — Auth tokens (UI sprint cycle 2) ─────────────────────────
  authTokensList: () =>
    fetch("/api/auth/tokens").then(jsonOf<{
      ok: boolean;
      tokens: Array<{ id: string; name: string; scope: string;
                      created_ts: number; expires_ts: number | null }>;
      scopes_supported: string[];
    }>),

  authTokenCreate: (payload: { name: string; scope: string; ttl_s?: number | null }) =>
    fetch("/api/auth/tokens", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }).then(jsonOf<{
      ok: boolean; id?: string; secret?: string; scope?: string;
      warning?: string; error?: string;
    }>),

  authTokenDelete: (id: string) =>
    fetch(`/api/auth/tokens/${encodeURIComponent(id)}/delete`, {
      method: "POST",
    }).then(jsonOf<{ ok: boolean; deleted?: string; error?: string }>),

  authShareCreate: (payload: { scope: string; ttl_s: number; sub?: string }) =>
    fetch("/api/auth/share", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }).then(jsonOf<{
      ok: boolean; share_token?: string; scope?: string;
      ttl_s?: number; sub?: string; error?: string;
    }>),

  // ── R&D #11.1b — Watchdog setup (UI sprint) ────────────────────────────
  watchdogStatus: () =>
    fetch("/api/watchdog/status").then(jsonOf<{
      ok: boolean;
      installed: boolean;
      active: boolean;
      service_path: string;
      timer_path: string;
    }>),

  watchdogEnable: (opts?: { strict?: boolean; interval_s?: number }) =>
    fetch("/api/watchdog/enable", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(opts || {}),
    }).then(jsonOf<{ ok: boolean; msg: string; installed: boolean; active: boolean }>),

  watchdogDisable: () =>
    fetch("/api/watchdog/disable", { method: "POST" })
      .then(jsonOf<{ ok: boolean; msg: string; installed: boolean; active: boolean }>),

  // ── R&D #11.4 — Service discovery (UI sprint) ───────────────────────────
  servicesDiscovered: () =>
    fetch("/api/services-discovered").then(jsonOf<{
      ok: boolean;
      available: boolean;
      services_count: number;
      services: Array<{
        service: string;
        category: string;
        pid: number | null;
        proc_name: string;
        ports: number[];
        primary_port: number | null;
        health?: { ok: boolean; status: number | null; ms: number | null };
      }>;
      unknown_count: number;
      unknown_listeners: Array<{ port: number; proc_name: string; cmdline_preview: string }>;
    }>),

  // ── R&D #9.4 — HF cache janitor (UI sprint) ─────────────────────────────
  hfJanitor: (limit = 20) =>
    fetch(`/api/hf-janitor?limit=${limit}`).then(jsonOf<{
      ok: boolean;
      available: boolean;
      reason?: string;
      dirs_scanned?: string[];
      files_total?: number;
      total_size_mib?: number;
      cold_size_mib?: number;
      hot_count?: number;
      top_cold?: Array<{
        path: string;
        size_mib: number;
        age_days: number;
        is_hot: boolean;
      }>;
    }>),

  electricity: (since = 3600, gpu = 0) =>
    fetch(`/api/electricity?since=${since}` + (gpu ? `&gpu_index=${gpu}` : "")).then(jsonOf<{
      ok: boolean;
      window_seconds: number;
      samples: number;
      avg_power_watts: number;
      kwh: number;
      cost: number;
      currency: string;
      price_per_kwh: number;
      daily_kwh: number;
      daily_cost: number;
      monthly_kwh: number;
      monthly_cost: number;
      kwh_month: number;
      cost_month: number;
      month_progress_pct: number;
      forecast_kwh: number;
      budget_kwh: number;
      over_budget: boolean;
    }>),

  // ── R&D #17.5 LLM hot-swap orchestrator (UI sprint 8) ──────────────────
  llmSwapStatus: () =>
    fetch("/api/llm-swap").then(jsonOf<{
      ok: boolean;
      loaded_count: number;
      loaded: Array<{
        name: string; source: "ollama" | "llamacpp";
        size_bytes?: number; vram_bytes?: number;
        n_ctx_train?: number; n_params?: number;
      }>;
      total_vram_bytes: number;
      total_vram_gib: number;
      pins: string[];
      timeline_count: number;
      recent_events: Array<{ kind: "load" | "unload"; name: string;
                              source: string; ts: number; vram_bytes?: number }>;
    }>),

  llmSwapPin: (name: string, action: "pin" | "unpin") =>
    fetch("/api/llm-swap/pin", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, action }),
    }).then(jsonOf<{ ok: boolean; pinned?: string; unpinned?: string | null;
                      error?: string }>),

  llmSwapSuggest: (neededBytes: number) =>
    fetch(`/api/llm-swap/suggest?needed_bytes=${neededBytes}`)
      .then(jsonOf<{
        to_evict: Array<{ name: string; source: string;
                           vram_bytes: number; last_seen: number }>;
        freed_bytes: number;
        sufficient: boolean;
        needed_bytes: number;
        reason: string;
      }>),

  // ── R&D #18 (UI sprint 9) ──────────────────────────────────────────────
  cudaAdvisorStatus: () =>
    fetch("/api/cuda-advisor").then(jsonOf<{
      ok: boolean;
      gpus: Array<{ index: number; uuid: string; name: string }>;
      gpu_count: number;
      process_count: number;
      drift_count: number;
      processes: Array<{
        pid: number; comm: string; raw: string; entries: string[];
        resolved: Array<{ entry: string; gpu: { index: number; uuid: string;
                                                  name: string } | null;
                          drift: boolean; reason: string }>;
        has_drift: boolean;
      }>;
      recommendation: string;
    }>),

  nvmeSwapStatus: () =>
    fetch("/api/nvme-swap").then(jsonOf<{
      ok: boolean;
      llm_processes: Array<{ pid: number; comm: string;
                              cmdline_short: string;
                              swap_bytes: number; rss_bytes: number }>;
      llm_total_swap_bytes: number;
      llm_total_swap_gib: number;
      nvme_devices: Array<{
        device: string;
        write_rate_mibps: number | null;
        data_units_written: number | null;
        endurance: {
          used_tb: number; rated_tb: number; remaining_tb: number;
          pct_used: number; days_remaining: number | null;
        };
      }>;
      warning: string | null;
    }>),

  cudaMatrixStatus: () =>
    fetch("/api/cuda-matrix").then(jsonOf<{
      ok: boolean;
      driver_version: string | null;
      cuda_toolkit: { version: string; name: string } | null;
      cudnn_version: string | null;
      compat: { ok: boolean | null; reason: string;
                 required_driver: number | null };
      cuda_min_driver_table: Record<string, number>;
    }>),

  pcieHistogramStatus: () =>
    fetch("/api/pcie-histogram").then(jsonOf<{
      ok: boolean;
      histogram_1h: {
        window_s: number; transition_count: number;
        transitions_per_min: number;
        buckets: Record<string, number>;
        verdict: "stable" | "intermittent" | "thrashing";
        first_event_ts: number | null; last_event_ts: number | null;
      };
      histogram_24h: {
        window_s: number; transition_count: number;
        transitions_per_min: number;
        buckets: Record<string, number>;
        verdict: "stable" | "intermittent" | "thrashing";
        first_event_ts: number | null; last_event_ts: number | null;
      };
      total_events_seen: number;
    }>),

  // ── R&D #19 (UI sprint 10) ────────────────────────────────────────────
  throttleCauseStatus: () =>
    fetch("/api/throttle-cause").then(jsonOf<{
      ok: boolean;
      reason?: string;
      any_throttling?: boolean;
      gpus: Array<{
        index: number; name: string;
        temp_c: number | null; clock_mhz: number | null;
        clock_max_mhz: number | null;
        power_w: number | null; power_limit_w: number | null;
        verdict: {
          severity: "info" | "warn" | "critical";
          reason: string; recommendation: string;
          active_flags: string[];
        };
      }>;
    }>),

  mpsHealthStatus: () =>
    fetch("/api/mps-health").then(jsonOf<{
      ok: boolean;
      state: "not_configured" | "not_running" | "stalled" | "running";
      pipe_dir: string;
      control_socket_present: boolean;
      control_binary_available: boolean;
      server_pids: number[];
      clients: Array<{ pid: number; uid?: number; name?: string }>;
      default_sm_share_pct: number | null;
      advice: string;
    }>),

  processNiceStatus: () =>
    fetch("/api/process-nice").then(jsonOf<{
      ok: boolean;
      reason?: string;
      needs_action_count: number;
      processes?: Array<{
        pid: number; comm: string; cmdline_short: string;
        class: string;
        current_nice: number | null;
        suggested_nice: number | null;
        needs_change: boolean;
        shell_command: string | null;
      }>;
    }>),

  warmupProfileStatus: () =>
    fetch("/api/warmup-profile").then(jsonOf<{
      ok: boolean;
      tracked_count: number;
      models: Array<{
        model: string; source: string;
        samples: Array<{ ts: number; ttft_ms: number; trigger: string }>;
        stats: {
          count: number; ttft_min?: number; ttft_max?: number;
          ttft_median?: number; cold_ttft_ms?: number;
          hot_median_ttft_ms?: number | null;
          cold_minus_hot_ms?: number | null;
        };
        recommendation: string;
      }>;
    }>),

  warmupProfileProbe: (body: { model: string; source: "llamacpp" | "ollama";
                                 host?: string; port?: number; prompt?: string }) =>
    fetch("/api/warmup-profile/probe", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then(jsonOf<{ ok: boolean; ttft_ms?: number; error?: string }>),

  // ── R&D #20 (UI sprint 11) ─────────────────────────────────────────────
  suspendGuardStatus: () =>
    fetch("/api/suspend-guard").then(jsonOf<{
      ok: boolean;
      compute_count: number;
      compute_processes: Array<{ pid: number; name: string; vram_mib: number }>;
      lid_state: "open" | "closed" | null;
      logind_action: string | null;
      verdict: { verdict: "safe" | "risky" | "blocked";
                  reason: string; recommendation: string };
      inhibit_snippet: string;
    }>),

  containerAuditStatus: () =>
    fetch("/api/container-audit").then(jsonOf<{
      ok: boolean;
      reason?: string;
      docker_socket: string;
      container_count: number;
      cpu_fallback_count: number;
      containers: Array<{
        id: string; names: string[]; state: string;
        verdict: "gpu_ok" | "cpu_fallback" | "partial" | "unknown";
        reason: string;
        has_gpu_devices: boolean;
        has_runtime_nvidia: boolean;
        visible_devices: string | null;
        image: string;
        runtime: string;
      }>;
    }>),

  upsRuntimeStatus: () =>
    fetch("/api/ups-runtime").then(jsonOf<{
      ok: boolean;
      ups_available: boolean;
      on_battery: boolean;
      low_battery: boolean;
      reported_runtime_s: number | null;
      adjusted_runtime_s: number | null;
      gpu_total_power_w: number | null;
      baseline_w: number;
      capacity_wh: number;
      verdict: {
        verdict: "on_grid" | "safe" | "pause_jobs" | "shutdown_now";
        reason: string;
        safe_runtime_s: number | null;
      };
    }>),

  vbiosDriftStatus: () =>
    fetch("/api/vbios-drift").then(jsonOf<{
      ok: boolean;
      reason?: string;
      drift_count: number;
      first_seen_count?: number;
      gpus: Array<{
        uuid: string; name: string; bdf: string;
        current_vbios: string;
        current_rom_sha256: string | null;
        baseline_vbios: string | null;
        baseline_rom_sha256: string | null;
        drift: boolean;
        reasons: string[];
      }>;
    }>),

  vbiosDriftRebaseline: () =>
    fetch("/api/vbios-drift/rebaseline", { method: "POST" })
      .then(jsonOf<{ ok: boolean; baseline_size?: number }>),

  // ── R&D #21 (UI sprint 12) ─────────────────────────────────────────────
  pstateAuditStatus: () =>
    fetch("/api/pstate-audit").then(jsonOf<{
      ok: boolean;
      reason?: string;
      downshift_count?: number;
      gpus: Array<{
        index: number; name: string;
        pstate: number | null;
        util_pct: number | null;
        clock_mhz: number | null;
        clock_max_mhz: number | null;
        power_w: number | null;
        power_limit_w: number | null;
        clock_locked: boolean;
        verdict: {
          verdict: "ok" | "silent_downshift" | "power_save_idle"
                   | "clock_locked" | "unknown";
          reason: string;
          advisory: string;
        };
      }>;
    }>),

  persistenceModeStatus: () =>
    fetch("/api/persistence-mode").then(jsonOf<{
      ok: boolean;
      reason?: string;
      daemon_running: boolean;
      daemon_socket?: string;
      daemon_pid?: number | null;
      gpus: Array<{ index: number; name: string;
                     enabled: boolean; raw: string }>;
      verdict?: { verdict: "ok" | "partial" | "off"
                            | "off_with_manual" | "unknown";
                   reason: string; advisory: string };
    }>),

  gspStatus: () =>
    fetch("/api/gsp-status").then(jsonOf<{
      ok: boolean;
      gpus: Array<{ index: number; name: string;
                     gsp_firmware_version: string }>;
      gsp_events: Array<{ kind: string; line: string }>;
      event_count: number;
      verdict: {
        verdict: "ok" | "warn" | "fallback" | "crashed" | "unknown";
        reason: string;
        recovery: string;
        gsp_in_use: boolean;
      };
    }>),

  sdCacheJanitorStatus: () =>
    fetch("/api/sd-cache-janitor").then(jsonOf<{
      ok: boolean;
      scanned_dirs: string[];
      scanned_count: number;
      total_bytes: number;
      total_gib: number;
      cold_bytes: number;
      cold_gib: number;
      cold_age_days: number;
      per_dir: Array<{
        path: string;
        total_mib: number; cold_mib: number;
        file_count: number; cold_count: number;
        oldest_ts: number | null;
        sample_old_files: Array<{ path: string; size_mib: number; age_days: number }>;
      }>;
      top_candidates: Array<{ path: string; size_mib: number; age_days: number }>;
    }>),

  // ── R&D #22 (UI sprint 13) ─────────────────────────────────────────────
  vramLeakStatus: () =>
    fetch("/api/vram-leak").then(jsonOf<{
      ok: boolean;
      window_s: number;
      process_count: number;
      leaking_count: number;
      growing_count: number;
      processes: Array<{
        pid: number; comm: string;
        current_mib: number; sample_count: number;
        verdict: {
          verdict: "warming" | "stable" | "growing" | "leaking";
          reason: string;
          slope_mib_per_hour: number | null;
          projected_oom_minutes: number | null;
        };
      }>;
    }>),

  gpuResetStatus: () =>
    fetch("/api/gpu-reset").then(jsonOf<{
      ok: boolean;
      card_count: number;
      cards: Array<{
        card: string; bdf: string;
        reset_count: number | null;
        delta_resets: number;
      }>;
      kernel_events: Array<{ kind: string; line: string }>;
      kernel_event_count: number;
      total_delta_resets: number;
      verdict: {
        verdict: "clean" | "occasional" | "frequent" | "rma";
        reason: string;
        recommendation: string;
      };
    }>),

  cudaInventoryStatus: () =>
    fetch("/api/cuda-inventory").then(jsonOf<{
      ok: boolean;
      install_count: number;
      collision_count: number;
      toolkits: Array<{ path: string; version: string | null; source: string }>;
      conda_envs: Array<{ path: string; version: string; source: string }>;
      ld_library_path: Array<{ path: string; versions: string[]; source: string }>;
      collisions: Array<{ kind: string; major?: string; majors?: string[];
                            count?: number; paths?: string[] }>;
      verdict: { verdict: "none" | "clean" | "duplicate" | "version_conflict";
                  reason: string };
    }>),

  driverFlavorStatus: () =>
    fetch("/api/driver-flavor").then(jsonOf<{
      ok: boolean;
      kernel_module_version: string | null;
      flavor: "open" | "proprietary" | "unknown";
      modinfo_license: string | string[] | null;
      modinfo_filename: string | string[] | null;
      gpus: Array<{
        index: number; name: string; compute_cap: string;
        arch: string; open_supported: boolean;
      }>;
      verdict: {
        verdict: "ok" | "wrong_flavor" | "could_upgrade" | "mixed" | "unknown";
        reason: string;
        recommendation: string;
      };
    }>),

  // ── R&D #23 (UI sprint 14) ─────────────────────────────────────────────
  procDeepStateStatus: () =>
    fetch("/api/proc-deep-state").then(jsonOf<{
      ok: boolean;
      gpu_count: number;
      drift_count: number;
      excluded_count: number;
      gpus: Array<{
        bdf: string; uuid: string;
        model: string; video_bios: string; gpu_firmware: string;
        dma_size: string; irq: string;
        excluded: boolean;
        first_seen: boolean;
        drift: Array<{ field: string;
                        before: string | null; after: string | null }>;
      }>;
      verdict: {
        verdict: "no_gpus" | "clean" | "excluded" | "firmware_drift"
                 | "vbios_drift" | "minor_drift";
        reason: string;
        severity: "info" | "warn" | "critical";
      };
    }>),

  pcieAspmStatus: () =>
    fetch("/api/pcie-aspm").then(jsonOf<{
      ok: boolean;
      policy: { active: string | null; options: string[]; raw: string } | null;
      board: { vendor: string | null; name: string | null };
      board_known_risky: boolean;
      nvidia_pci_devs: Array<Record<string, any>>;
      verdict: {
        verdict: "ok" | "warn" | "risky" | "unknown";
        reason: string;
        recommendation: string;
      };
    }>),

  fsMountAuditStatus: () =>
    fetch("/api/fs-mount-audit").then(jsonOf<{
      ok: boolean;
      audit_count: number;
      warn_count: number;
      fail_count: number;
      audits: Array<{
        directory: string; mountpoint: string;
        fstype: string; options: string[];
        severity: "ok" | "warn" | "fail";
        issues: Array<{ severity: string; label: string;
                         recommendation: string }>;
      }>;
      verdict: { verdict: "no_dirs" | "ok" | "warn" | "fail";
                  reason: string };
    }>),

  batchAdvisorStatus: () =>
    fetch("/api/batch-advisor").then(jsonOf<{
      ok: boolean;
      reason?: string;
      vram: { total_mib: number; used_mib: number; free_mib: number } | null;
      models: Array<{
        id: string;
        n_ctx_train: number | null;
        n_params: number | null;
        size_bytes: number | null;
        n_embd: number | null;
      }>;
      target_batch?: number;
      advisors: Array<{
        model: string;
        kv_per_token_bytes: number;
        headroom_bytes: number;
        headroom_mib: number;
        max_ctx_at_batch: number;
        max_batch_at_ctx_train: number;
        recommendation: string;
      }>;
    }>),

  // ── R&D #24 (UI sprint 15) ─────────────────────────────────────────────
  dkmsStatus: () =>
    fetch("/api/dkms-status").then(jsonOf<{
      ok: boolean;
      reason?: string;
      running_kernel: string;
      dkms_entries: Array<{
        module: string; version: string | null;
        kernel: string | null; arch: string | null;
        state: string;
      }>;
      ko_present: boolean;
      verdict: {
        verdict: "ok" | "rebuild_needed" | "no_nvidia_dkms"
                 | "dkms_missing" | "unknown";
        reason: string;
        recovery: string;
      };
    }>),

  pcieAerStatus: () =>
    fetch("/api/pcie-aer").then(jsonOf<{
      ok: boolean;
      device_count: number;
      devices: Array<{
        bdf: string;
        totals: { correctable: number; fatal: number; nonfatal: number };
        delta: Record<string, Record<string, number>>;
        first_seen: boolean;
        verdict: {
          verdict: "clean" | "low_correctable" | "high_correctable"
                   | "non_fatal" | "fatal";
          reason: string; recovery: string;
        };
      }>;
      aggregate_delta: Record<string, Record<string, number>>;
      verdict: {
        verdict: "clean" | "low_correctable" | "high_correctable"
                 | "non_fatal" | "fatal" | "no_gpus";
        reason: string; recovery: string;
      };
    }>),

  memTempDriftStatus: () =>
    fetch("/api/mem-temp-drift").then(jsonOf<{
      ok: boolean;
      reason?: string;
      gpu_count?: number;
      summary_verdict?: "ok" | "warming" | "improving"
                       | "pad_degraded" | "urgent";
      gpus: Array<{
        uuid: string; name: string;
        gpu_temp_c: number | null;
        mem_temp_c: number | null;
        delta_now: number | null;
        drift: {
          baseline_delta: number | null;
          recent_delta: number | null;
          drift_c: number | null;
          sample_count: number;
          baseline_sample_count?: number;
          recent_sample_count?: number;
        };
        verdict: { verdict: string; reason: string };
      }>;
    }>),

  accountingStatus: () =>
    fetch("/api/accounting").then(jsonOf<{
      ok: boolean;
      reason?: string;
      accounting_mode: string | null;
      enable_command?: string;
      advisory?: string;
      record_count?: number;
      records?: Array<{
        gpu_uuid: string; pid: number;
        gpu_util_pct: number | null;
        mem_util_pct: number | null;
        max_memory_mib: number | null;
        wall_time_ms: number | null;
        observed_at?: number; first_seen_at?: number;
      }>;
      by_command?: Array<{
        comm: string; count: number;
        total_wall_ms: number;
        max_memory_mib: number;
        mean_gpu_util_pct: number | null;
      }>;
    }>),

  // ── R&D #25 (UI sprint 16) ─────────────────────────────────────────────
  trimAuditStatus: () =>
    fetch("/api/trim-audit").then(jsonOf<{
      ok: boolean;
      audit_count: number;
      audits: Array<{
        directory: string; mountpoint: string; device: string;
        fstype: string;
        has_discard_mount: boolean; on_ssd: boolean | null;
      }>;
      fstrim_timer: { enabled: string | null; active: string | null };
      verdict: { verdict: "no_dirs" | "no_ssd" | "ok" | "no_trim";
                  reason: string; recommendation: string };
    }>),

  throttleBitsStatus: () =>
    fetch("/api/throttle-bits").then(jsonOf<{
      ok: boolean;
      reason?: string;
      any_critical?: boolean;
      gpus: Array<{
        index: number; name: string;
        active_count: number;
        bits: Array<{ field: string; label: string;
                       severity: "info" | "warn" | "critical";
                       meaning: string; active: boolean }>;
        verdict: { verdict: string; severity: string; reason: string };
      }>;
    }>),

  retiredPagesStatus: () =>
    fetch("/api/retired-pages").then(jsonOf<{
      ok: boolean;
      reason?: string;
      supported: boolean;
      worst_severity?: string;
      total_entries?: number;
      per_gpu: Array<{
        uuid: string; sbe: number; dbe: number; total: number;
        delta_sbe: number; delta_dbe: number;
        first_seen: boolean;
        verdict: { severity: string; label: string;
                    reason: string; recommendation: string };
      }>;
    }>),

  bugReportPrepStatus: () =>
    fetch("/api/bug-report-prep").then(jsonOf<{
      ok: boolean;
      context_summary: {
        kernel: string;
        xid_event_count: number;
        gsp_event_count: number;
        gpu_count: number;
        dkms_verdict?: string;
        driver_flavor?: string;
      };
      template_text: string;
      bug_report_command: string;
    }>),

  // ── R&D #26 (UI sprint 17) ─────────────────────────────────────────────
  pcieWidthWatcherStatus: () =>
    fetch("/api/pcie-width-watcher").then(jsonOf<{
      ok: boolean;
      device_count: number;
      worst_verdict: string;
      summary_reason?: string;
      devices: Array<{
        bdf: string;
        current_width: number | null;
        max_width: number | null;
        current_speed_gts: number | null;
        max_speed_gts: number | null;
        current_gen: number | null;
        max_gen: number | null;
        verdict: { verdict: string; reason: string; recovery: string };
      }>;
    }>),

  cudaCtxLeakStatus: () =>
    fetch("/api/cuda-ctx-leak").then(jsonOf<{
      ok: boolean;
      fd_holder_count: number;
      compute_pid_count: number;
      leak_count: number;
      leaks: Array<{
        pid: number; comm: string; cmdline_short: string;
        devices: string[]; kill_cmd: string;
      }>;
      verdict: { verdict: string; reason: string };
    }>),

  procStaticAuditStatus: () =>
    fetch("/api/proc-static-audit").then(jsonOf<{
      ok: boolean;
      card_count: number;
      worst_severity: string;
      cards: Array<{
        bdf: string;
        vendor_device: string;
        subsystem: string;
        irq: string | null;
        fingerprint: string;
        drift: Array<{ field: string; before?: any; after?: any }>;
        verdict: { verdict: string; reason: string; severity: string };
      }>;
    }>),

  memBwGaugeStatus: () =>
    fetch("/api/mem-bw-gauge").then(jsonOf<{
      ok: boolean;
      reason?: string;
      total_samples?: number;
      per_gpu: Array<{
        index: number;
        gpu_util_mean: number;
        mem_util_mean: number;
        ratio_mem_over_gpu: number | null;
        sample_count: number;
        verdict: { verdict: string; reason: string; recommendation: string };
      }>;
    }>),

  // ── R&D #27 (UI sprint 18) ─────────────────────────────────────────────
  powerEnvelopeDriftStatus: () =>
    fetch("/api/power-envelope-drift").then(jsonOf<{
      ok: boolean;
      reason?: string;
      gpu_count?: number;
      worst_severity?: string;
      gpus: Array<{
        uuid: string; name: string;
        current_w: number | null;
        default_w: number | null;
        baseline_w: number | null;
        verdict: { verdict: string; reason: string;
                    severity: string; delta_w: number | null };
        recovery_cmd: string;
      }>;
    }>),

  rebarAuditStatus: () =>
    fetch("/api/rebar-audit").then(jsonOf<{
      ok: boolean;
      card_count: number;
      worst_verdict: string;
      cards: Array<{
        bdf: string;
        bar1_bytes: number | null;
        bar1_mib: number | null;
        total_vram_bytes: number | null;
        total_vram_gib: number | null;
        verdict: { verdict: string; reason: string;
                    recommendation: string;
                    bar1_pct_of_vram: number | null };
      }>;
    }>),

  cpuRaplStatus: () =>
    fetch("/api/cpu-rapl").then(jsonOf<{
      ok: boolean;
      supported: boolean;
      reason?: string;
      total_watts: number | null;
      package_count?: number;
      samples: Array<{
        name: string; watts: number | null; error?: string;
      }>;
    }>),

  clockGapStatus: () =>
    fetch("/api/clock-gap").then(jsonOf<{
      ok: boolean;
      reason?: string;
      any_capped?: boolean;
      gpus: Array<{
        index: number; name: string;
        verdict: string; reason: string;
        gap_mhz: number | null;
        binding: string | null;
        current_clk: number | null;
        applied_clk: number | null;
        max_clk: number | null;
      }>;
    }>),

  // ── R&D #28 (UI sprint 19) ─────────────────────────────────────────────
  pcieRpmAuditStatus: () =>
    fetch("/api/pcie-rpm-audit").then(jsonOf<{
      ok: boolean;
      device_count: number;
      worst_verdict: string;
      cards: Array<{
        bdf: string;
        control: string | null;
        runtime_status: string | null;
        verdict: { verdict: string; reason: string; recommendation: string };
        udev_recipe: string;
      }>;
    }>),

  thermalZonesStatus: () =>
    fetch("/api/thermal-zones").then(jsonOf<{
      ok: boolean;
      zone_count: number;
      summary: string;
      gpu_thermal_throttle?: boolean;
      category_counts?: Record<string, number>;
      advice: string[];
      zones: Array<{
        name: string; type: string;
        temp_mc: number; temp_c: number;
        category: string;
      }>;
    }>),

  nvrmTailStatus: () =>
    fetch("/api/nvrm-tail").then(jsonOf<{
      ok: boolean;
      reason?: string;
      since?: string;
      entry_count?: number;
      category_counts?: Record<string, number>;
      entries: Array<{ category: string; ts: string; body: string }>;
    }>),

  nvlinkHealthStatus: () =>
    fetch("/api/nvlink-health").then(jsonOf<{
      ok: boolean;
      reason?: string;
      supported: boolean;
      verdict: {
        verdict: string; reason: string;
        replay_delta?: number; crc_delta?: number;
        link_down_count?: number; recommendation?: string;
      };
      statuses?: Record<string, Record<string, string>>;
    }>),

  // ── R&D #29 (UI sprint 20) ─────────────────────────────────────────────
  kmodParamsStatus: () =>
    fetch("/api/kmod-params").then(jsonOf<{
      ok: boolean;
      reason?: string;
      param_count?: number;
      footgun_count?: number;
      worst_severity?: string;
      params: Record<string, string>;
      footguns: Array<{
        param: string; current: string;
        recommended: string | null;
        severity: string; advice: string; recipe: string;
      }>;
    }>),

  d3coldPolicyStatus: () =>
    fetch("/api/d3cold-policy").then(jsonOf<{
      ok: boolean;
      device_count: number;
      worst_verdict: string;
      cards: Array<{
        gpu_bdf: string;
        gpu_control: string | null;
        bridge_bdf: string | null;
        bridge_d3cold_allowed: string | null;
        bridge_d3cold_delay_ms: string | null;
        verdict: { verdict: string; reason: string; recommendation: string };
      }>;
    }>),

  thermalSlowdownKindStatus: () =>
    fetch("/api/thermal-slowdown-kind").then(jsonOf<{
      ok: boolean;
      reason?: string;
      any_critical?: boolean;
      gpus: Array<{
        index: number; name: string;
        gpu_temp_c: number | null;
        mem_temp_c: number | null;
        power_w: number | null;
        verdict: { verdict: string; severity: string;
                    reason: string; recommendation: string };
      }>;
    }>),

  rlimitAuditStatus: () =>
    fetch("/api/rlimit-audit").then(jsonOf<{
      ok: boolean;
      process_count: number;
      worst_verdict: string;
      summary?: string;
      processes: Array<{
        pid: number; comm: string; cmdline_short: string;
        memlock_bytes: number | null;
        vm_lck_bytes: number | null;
        verdict: { verdict: string; reason: string; recommendation: string };
        recipe: string;
      }>;
    }>),

  // ── R&D #30 (UI sprint 21) ─────────────────────────────────────────────
  dmiBiosStatus: () =>
    fetch("/api/dmi-bios").then(jsonOf<{
      ok: boolean;
      error?: string;
      reason?: string;
      dmi?: Record<string, string | null>;
      bios_date_iso?: string | null;
      verdict?: { verdict: string; reason: string; recommendation: string };
      drift?: {
        status: string;
        from?: Record<string, string | null>;
        to?: Record<string, string | null>;
        reason?: string;
      };
      catalog_size?: number;
    }>),

  nvmeIoschedStatus: () =>
    fetch("/api/nvme-iosched").then(jsonOf<{
      ok: boolean;
      device_count: number;
      worst_verdict: string;
      devices: Array<{
        device: string;
        scheduler: string | null;
        scheduler_raw: string | null;
        read_ahead_kb: number | null;
        nr_requests: number | null;
        verdict: { verdict: string; reason: string; recommendation: string };
      }>;
    }>),

  iommuGroupsStatus: () =>
    fetch("/api/iommu-groups").then(jsonOf<{
      ok: boolean;
      error?: string;
      reason?: string;
      recommendation?: string;
      device_count?: number;
      worst_verdict?: string;
      cards?: Array<{
        gpu_bdf: string;
        iommu_group: number | null;
        siblings: Array<{ bdf: string; kind: string }>;
        verdict: { verdict: string; reason: string; recommendation: string };
      }>;
    }>),

  msiInventoryStatus: () =>
    fetch("/api/msi-inventory").then(jsonOf<{
      ok: boolean;
      device_count: number;
      worst_verdict: string;
      cards: Array<{
        gpu_bdf: string;
        vector_count: number;
        vectors: number[];
        legacy_irq: number | null;
        mode: string;
        controllers: string[];
        total_interrupts: number;
        verdict: { verdict: string; reason: string; recommendation: string };
      }>;
    }>),

  // ── R&D #31 (UI sprint 22) ─────────────────────────────────────────────
  oomPriorityStatus: () =>
    fetch("/api/oom-priority").then(jsonOf<{
      ok: boolean;
      process_count: number;
      worst_verdict: string;
      processes: Array<{
        pid: number; comm: string; cmdline_short: string;
        oom_score: number | null;
        oom_score_adj: number | null;
        vm_rss_bytes: number | null;
        verdict: { verdict: string; reason: string; recommendation: string };
        recipe: string;
      }>;
    }>),

  cpuTopologyStatus: () =>
    fetch("/api/cpu-topology").then(jsonOf<{
      ok: boolean;
      cpu_count: number;
      physical_cores: number;
      smt_enabled: boolean;
      hybrid: { p_cores: number[]; e_cores: number[] } | null;
      max_freq_mhz: number | null;
      cpus: Array<{
        id: number; core_id: number; package_id: number;
        governor: string | null; max_freq_khz: number | null;
      }>;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  procSmapsStatus: () =>
    fetch("/api/proc-smaps").then(jsonOf<{
      ok: boolean;
      process_count: number;
      worst_verdict: string;
      total_rss_bytes: number;
      processes: Array<{
        pid: number; comm: string; cmdline_short: string;
        rss_bytes: number; pss_bytes: number;
        pss_anon_bytes: number; pss_file_bytes: number;
        pss_shmem_bytes: number; anonymous_bytes: number;
        swap_bytes: number;
        verdict: { verdict: string; reason: string; recommendation: string };
      }>;
    }>),

  hwmonInventoryStatus: () =>
    fetch("/api/hwmon-inventory").then(jsonOf<{
      ok: boolean;
      device_count: number;
      worst_verdict: string;
      max_temp_c: number | null;
      verdict?: { verdict: string; reason: string; recommendation: string };
      devices: Array<{
        hwmon: string; name: string; kind: string;
        sensors: Array<{
          channel: number; label: string | null;
          value_c: number | null; max_c: number | null;
          kind: string;
        }>;
        fans: Array<{ channel: number; label: string | null; rpm: number | null }>;
      }>;
    }>),

  // ── R&D #32 (UI sprint 23) ─────────────────────────────────────────────
  vmSysctlStatus: () =>
    fetch("/api/vm-sysctl").then(jsonOf<{
      ok: boolean;
      error?: string;
      reason?: string;
      row_count?: number;
      worst_severity?: string;
      flagged_count?: number;
      recipe?: string;
      rows?: Array<{
        name: string; value: number | null; severity: string;
        reason: string; recommended: number | null;
      }>;
    }>),

  psiPressureStatus: () =>
    fetch("/api/psi-pressure").then(jsonOf<{
      ok: boolean;
      error?: string;
      reason?: string;
      worst_verdict?: string;
      resources?: Array<{
        resource: string;
        psi: {
          some?: { avg10: number; avg60: number; avg300: number; total_us: number };
          full?: { avg10: number; avg60: number; avg300: number; total_us: number };
        };
        verdict: { verdict: string; reason: string; recommendation: string };
      }>;
    }>),

  procWchanStatus: () =>
    fetch("/api/proc-wchan").then(jsonOf<{
      ok: boolean;
      process_count: number;
      worst_verdict: string;
      processes: Array<{
        pid: number; comm: string; cmdline_short: string;
        state: string | null; wchan: string | null;
        stack: string[] | null;
        verdict: { verdict: string; reason: string; recommendation: string };
      }>;
    }>),

  cgroupMemcapStatus: () =>
    fetch("/api/cgroup-memcap").then(jsonOf<{
      ok: boolean;
      process_count: number;
      worst_verdict: string;
      processes: Array<{
        pid: number; comm: string; cmdline_short: string;
        cgroup_path: string | null;
        memory_max: number | null;
        memory_high: number | null;
        memory_current: number | null;
        memory_swap_current: number | null;
        events: Record<string, number>;
        verdict: { verdict: string; reason: string; recommendation: string };
      }>;
    }>),

  // ── R&D #33 (UI sprint 24) ─────────────────────────────────────────────
  clocksourceStatus: () =>
    fetch("/api/clocksource").then(jsonOf<{
      ok: boolean;
      error?: string;
      reason?: string;
      current?: string | null;
      available?: string[];
      virt?: string | null;
      verdict?: { verdict: string; reason: string; recommendation: string };
    }>),

  nicHealthStatus: () =>
    fetch("/api/nic-health").then(jsonOf<{
      ok: boolean;
      interface_count: number;
      worst_verdict: string;
      total_rx_bytes: number;
      total_tx_bytes: number;
      interfaces: Array<{
        name: string;
        carrier: string | null;
        operstate: string | null;
        speed: number | null;
        rx_bytes: number | null;
        tx_bytes: number | null;
        rx_dropped: number | null;
        tx_dropped: number | null;
        rx_errors: number | null;
        tx_errors: number | null;
        verdict: { verdict: string; reason: string; recommendation: string };
      }>;
    }>),

  procIoStatus: () =>
    fetch("/api/proc-io").then(jsonOf<{
      ok: boolean;
      process_count: number;
      worst_verdict: string;
      total_read_bytes: number;
      total_write_bytes: number;
      processes: Array<{
        pid: number; comm: string; cmdline_short: string;
        read_bytes: number;
        write_bytes: number;
        rchar: number | null;
        wchar: number | null;
        syscr: number | null;
        syscw: number | null;
        vm_rss_bytes: number | null;
        verdict: { verdict: string; reason: string; recommendation: string };
      }>;
    }>),

  cgroupCpuioStatus: () =>
    fetch("/api/cgroup-cpuio").then(jsonOf<{
      ok: boolean;
      process_count: number;
      worst_verdict: string;
      processes: Array<{
        pid: number; comm: string; cmdline_short: string;
        cgroup_path: string | null;
        cpu_weight: number | null;
        io_weight: number | null;
        cpu_max_quota: number | null;
        cpu_max_period: number | null;
        verdict: { verdict: string; reason: string; recommendation: string };
      }>;
    }>),

  // ── R&D #34 (UI sprint 25) ─────────────────────────────────────────────
  thpAuditStatus: () =>
    fetch("/api/thp-audit").then(jsonOf<{
      ok: boolean;
      error?: string;
      reason?: string;
      enabled?: string | null;
      defrag?: string | null;
      khugepaged_scan_sleep_ms?: number | null;
      verdict?: { verdict: string; reason: string; recommendation: string };
    }>),

  buddyinfoStatus: () =>
    fetch("/api/buddyinfo").then(jsonOf<{
      ok: boolean;
      error?: string;
      reason?: string;
      worst_verdict?: string;
      total_thp_blocks?: number;
      zones?: Array<{
        node: number; zone: string;
        counts: number[];
        order9_pages: number; order10_pages: number;
        total_free_mb: number;
      }>;
      verdict?: { verdict: string; reason: string; recommendation: string };
    }>),

  procSchedStatus: () =>
    fetch("/api/proc-sched").then(jsonOf<{
      ok: boolean;
      process_count: number;
      worst_verdict: string;
      processes: Array<{
        pid: number; comm: string; cmdline_short: string;
        voluntary_switches: number | null;
        involuntary_switches: number | null;
        nr_migrations: number | null;
        nr_switches: number | null;
        sum_exec_runtime_ms: number | null;
        threads: number | null;
        involuntary_ratio: number | null;
        verdict: { verdict: string; reason: string; recommendation: string };
      }>;
    }>),

  oomdStatus: () =>
    fetch("/api/oomd").then(jsonOf<{
      ok: boolean;
      state: string;
      event_count: number;
      events: Array<{ message: string; target: string; timestamp_us: number }>;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  // ── R&D #35 (UI sprint 26) ─────────────────────────────────────────────
  cpuBoostStatus: () =>
    fetch("/api/cpu-boost").then(jsonOf<{
      ok: boolean;
      mode: string;
      boost: number | null;
      no_turbo: number | null;
      intel_status: string | null;
      amd_status: string | null;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  netSysctlStatus: () =>
    fetch("/api/net-sysctl").then(jsonOf<{
      ok: boolean;
      error?: string;
      reason?: string;
      row_count?: number;
      worst_severity?: string;
      flagged_count?: number;
      recipe?: string;
      tcp_rmem?: [number, number, number] | null;
      tcp_wmem?: [number, number, number] | null;
      rows?: Array<{
        name: string; value: number | null; severity: string;
        reason: string; recommended: number | null;
      }>;
    }>),

  smtAuditStatus: () =>
    fetch("/api/smt-audit").then(jsonOf<{
      ok: boolean;
      smt_control: string | null;
      smt_active: number | null;
      possible_count: number;
      online_count: number;
      offline_cores: number[];
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  numaPlacementStatus: () =>
    fetch("/api/numa-placement").then(jsonOf<{
      ok: boolean;
      error?: string;
      reason?: string;
      worst_verdict?: string;
      node_count?: number;
      process_count?: number;
      nodes?: Array<{
        id: number;
        cpu_list: string | null;
        distance: number[];
        mem_total_kb: number | null;
        mem_free_kb: number | null;
      }>;
      processes?: Array<{
        pid: number; comm: string; cmdline_short: string;
        per_node: Record<string, number>;
      }>;
      verdict?: { verdict: string; reason: string; recommendation: string };
    }>),

  // ── R&D #36 (UI sprint 27) ─────────────────────────────────────────────
  kernelTaintStatus: () =>
    fetch("/api/kernel-taint").then(jsonOf<{
      ok: boolean;
      error?: string;
      reason?: string;
      value?: number;
      flags?: Array<{ bit: number; code: string; description: string }>;
      uptime_seconds?: number | null;
      verdict?: { verdict: string; reason: string; recommendation: string };
    }>),

  cpuMicrocodeStatus: () =>
    fetch("/api/cpu-microcode").then(jsonOf<{
      ok: boolean;
      error?: string;
      reason?: string;
      cpu_count?: number;
      vendor_id?: string | null;
      cpu_family?: string | null;
      model?: string | null;
      model_name?: string | null;
      microcodes?: string[];
      distinct_microcodes?: string[];
      sys_microcode_version?: string | null;
      sys_processor_flags?: string | null;
      sys_microcode_dir_present?: boolean;
      verdict?: { verdict: string; reason: string; recommendation: string };
    }>),

  hwpEppStatus: () =>
    fetch("/api/hwp-epp").then(jsonOf<{
      ok: boolean;
      cpu_count: number;
      prefs: string[];
      distinct_prefs: string[];
      available: string[];
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  cpuidleStatus: () =>
    fetch("/api/cpuidle").then(jsonOf<{
      ok: boolean;
      error?: string;
      reason?: string;
      driver?: string | null;
      governor?: string | null;
      available_governors?: string[];
      max_latency?: number | null;
      states?: Array<{
        state: number; name: string; desc: string;
        latency: number | null; residency: number | null;
        disable: number; usage: number | null; time: number | null;
      }>;
      verdict?: { verdict: string; reason: string; recommendation: string };
    }>),

  // ── R&D #37 (UI sprint 28) + PAM limits (bonus) ──────────────────────
  cpuVulnsStatus: () =>
    fetch("/api/cpu-vulns").then(jsonOf<{
      ok: boolean;
      error?: string;
      vulnerability_count?: number;
      counts?: { not_affected: number; mitigated: number;
                  vulnerable: number; unknown: number };
      rows?: Array<{ name: string; state: string; detail: string;
                      raw: string }>;
      verdict?: { verdict: string; reason: string; recommendation: string };
    }>),

  hwWatchdogStatus: () =>
    fetch("/api/hw-watchdog").then(jsonOf<{
      ok: boolean;
      watchdog_count: number;
      watchdogs: Array<{
        watchdog: string; identity: string;
        timeout: number | null; bootstatus: number | null;
        nowayout: number | null; state: string | null;
        pretimeout: number | null; status: string | null;
      }>;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  gpuCpuAffinityStatus: () =>
    fetch("/api/gpu-cpu-affinity").then(jsonOf<{
      ok: boolean;
      gpu_count: number;
      total_cpus: number;
      cards: Array<{
        gpu_bdf: string; local_cpulist: string | null;
        local_cpus_count: number; numa_node: number | null;
      }>;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  cacheTopologyStatus: () =>
    fetch("/api/cache-topology").then(jsonOf<{
      ok: boolean;
      total_cpus: number;
      l3_island_count: number;
      islands: Array<{
        cpu_list: string; cpus: number[];
        size_bytes: number | null; size_mb: number;
      }>;
      l1d_kb: number | null;
      l2_kb: number | null;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  limitsAuditStatus: () =>
    fetch("/api/limits-audit").then(jsonOf<{
      ok: boolean;
      error?: string;
      files?: string[];
      memlock_rules?: Array<{ domain: string; type: string;
                                item: string; value: string }>;
      verdict?: { verdict: string; reason: string; recommendation: string };
    }>),

  // ── R&D #38 (UI sprint 29) ─────────────────────────────────────────────
  pcieAerTrendStatus: () =>
    fetch("/api/pcie-aer-trend").then(jsonOf<{
      ok: boolean;
      gpu_count: number;
      cards: Array<{
        gpu_bdf: string;
        correctable: Record<string, number>;
        fatal: Record<string, number>;
        nonfatal: Record<string, number>;
      }>;
      verdict: { verdict: string; reason: string; recommendation: string };
      drift: { status: string;
                deltas?: Record<string, Record<string, number>> };
    }>),

  gpuIrqAffinityStatus: () =>
    fetch("/api/gpu-irq-affinity").then(jsonOf<{
      ok: boolean;
      gpu_count: number;
      total_irqs: number;
      cards: Array<{
        gpu_bdf: string;
        local_cpulist: string | null;
        irqs: Array<{ irq: number; smp_list: string | null;
                       effective: string | null }>;
      }>;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  modprobeAuditStatus: () =>
    fetch("/api/modprobe-audit").then(jsonOf<{
      ok: boolean;
      error?: string;
      on_disk?: Record<string, { options: Record<string, string>;
                                    files: string[] }>;
      runtime?: Record<string, Record<string, string>>;
      drift_rows?: Array<{ module: string; param: string;
                            on_disk: string; runtime: string }>;
      verdict?: { verdict: string; reason: string; recommendation: string };
    }>),

  procMapsLibsStatus: () =>
    fetch("/api/proc-maps-libs").then(jsonOf<{
      ok: boolean;
      process_count: number;
      worst_verdict: string;
      processes: Array<{
        pid: number; comm: string; cmdline_short: string;
        readable: boolean;
        libs: Array<{ basename: string; path: string;
                       deleted: boolean; is_nvidia: boolean }>;
        deleted_libs: string[];
      }>;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  // ── R&D #39 (UI sprint 30) ─────────────────────────────────────────────
  cmdlineAuditStatus: () =>
    fetch("/api/cmdline-audit").then(jsonOf<{
      ok: boolean;
      error?: string;
      raw?: string;
      flags?: Record<string, string | boolean>;
      categories?: Record<string, Array<{ key: string; value: string | boolean }>>;
      verdict?: { verdict: string; reason: string; recommendation: string };
    }>),

  coredumpStatus: () =>
    fetch("/api/coredump").then(jsonOf<{
      ok: boolean;
      core_pattern: string;
      core_uses_pid: boolean;
      pattern_info: { kind: string; target: string;
                       has_pid: boolean; has_exe: boolean };
      process_count: number;
      processes: Array<{
        pid: number; comm: string; cmdline_short: string;
        filter: number | null;
        filter_value: number | null;
        filter_bits: Array<{ key: string; mask: number;
                              description: string }>;
      }>;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  hostClassStatus: () =>
    fetch("/api/host-class").then(jsonOf<{
      ok: boolean;
      error?: string;
      chassis_type?: number | null;
      chassis_kind?: string;
      sys_vendor?: string;
      product_name?: string;
      bios_vendor?: string;
      virt?: { is_virt: boolean; platform: string | null };
      verdict?: { verdict: string; reason: string; recommendation: string };
    }>),

  sysctlDAuditStatus: () =>
    fetch("/api/sysctl-d-audit").then(jsonOf<{
      ok: boolean;
      on_disk_count: number;
      on_disk: Record<string, { value: string; files: string[] }>;
      runtime: Record<string, string>;
      drift_rows: Array<{ key: string; on_disk: string;
                            runtime: string; files: string[] }>;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  // ── R&D #40 (UI sprint 31) ──
  ksmAdvisorStatus: () =>
    fetch("/api/ksm-advisor").then(jsonOf<{
      ok: boolean;
      state: Record<string, number | string>;
      process_count: number;
      processes: Array<{
        pid: number; comm: string; cmdline_short: string;
        ksm_merging_pages: number | null;
        ksm_rmap_items: number | null;
      }>;
      host_form_factor: string | null;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  vmTuningDeepStatus: () =>
    fetch("/api/vm-tuning-deep").then(jsonOf<{
      ok: boolean;
      knobs: Record<string, number>;
      swap_active: boolean;
      mem_total_kb: number | null;
      mem_available_kb: number | null;
      mem_pressure: number | null;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  gpuPciBindStatus: () =>
    fetch("/api/gpu-pci-bind").then(jsonOf<{
      ok: boolean;
      device_count: number;
      slot_count: number;
      devices: Array<{
        bdf: string; vendor: string; device_id: string;
        class_int: number; function_role: string;
        driver: string | null; enable: number | null;
        driver_override: string | null;
        numa_node: number | null; iommu_group: number | null;
        power_control: string | null;
      }>;
      slots: Record<string, string[]>;
      drivers_present: Record<string, boolean>;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  nicQueueAffinityStatus: () =>
    fetch("/api/nic-queue-affinity").then(jsonOf<{
      ok: boolean;
      device_count: number;
      devices: Array<{
        dev: string; operstate: string; carrier: number | null;
        rx_queue_count: number; tx_queue_count: number;
        mtu: number | null; tx_queue_len: number | null;
        rx_queues: Array<{ name: string; rps_cpus_hex: string;
                            rps_cpus: number[];
                            rps_flow_cnt: number | null }>;
        tx_queues: Array<{ name: string; xps_cpus_hex: string;
                            xps_cpus: number[];
                            bql_limit: number | null }>;
      }>;
      gpu_numa_cpus: number[];
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  // ── R&D #41 (UI sprint 32) ──
  panicPolicyStatus: () =>
    fetch("/api/panic-policy").then(jsonOf<{
      ok: boolean;
      knobs: Record<string, number>;
      host_form_factor: string | null;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  edacRamEccStatus: () =>
    fetch("/api/edac-ram-ecc").then(jsonOf<{
      ok: boolean;
      controller_count?: number;
      controllers: Array<{
        name: string; driver: string | null;
        ce_count: number; ue_count: number; size_mb: number | null;
        dimms: Array<{
          name: string; label: string | null;
          size_mb: number | null; ce_count: number; ue_count: number;
          mem_type: string | null; dev_type: string | null;
        }>;
      }>;
      ce_total: number;
      ue_total: number;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  inotifyAuditStatus: () =>
    fetch("/api/inotify-audit").then(jsonOf<{
      ok: boolean;
      limits: Record<string, number>;
      process_count: number;
      by_uid: Record<string, { watches: number; instances: number;
                                fanotify_watches: number;
                                fanotify_instances: number;
                                procs: number }>;
      top_processes: Array<{
        pid: number; comm: string; uid: number | null;
        inotify_instances: number; inotify_watches: number;
        fanotify_instances: number; fanotify_watches: number;
      }>;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  zswapZramStatus: () =>
    fetch("/api/zswap-zram").then(jsonOf<{
      ok: boolean;
      zswap: { available: boolean; enabled: boolean | null;
                compressor: string | null; zpool: string | null;
                max_pool_percent: number | null;
                accept_threshold_percent: number | null };
      zram_devices: Array<{ name: string; disksize: number | null;
                              comp_algorithm: string | null;
                              max_comp_streams: number | null;
                              mm_stat_raw: string | null }>;
      swap_devices: Array<{ path: string; type: string;
                              size_kb: number | null;
                              used_kb: number | null;
                              priority: string }>;
      mem_total_gb: number | null;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  // ── R&D #42 (UI sprint 33) ──
  cpuEpbStatus: () =>
    fetch("/api/cpu-epb").then(jsonOf<{
      ok: boolean;
      cpu_count: number;
      epb_exposed_count: number;
      per_cpu: Array<{ cpu: number; epb: number | null; label: string | null }>;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  coolingDevicesStatus: () =>
    fetch("/api/cooling-devices").then(jsonOf<{
      ok: boolean;
      cooling_device_count?: number;
      thermal_zone_count?: number;
      cooling_devices: Array<{
        name: string; index: number; type: string | null;
        cur_state: number | null; max_state: number | null;
      }>;
      thermal_zones: Array<{
        zone: string; type: string | null; trip_count: number;
        bindings: Array<{ cdev_slot: number; cdev_target: string | null;
                            cdev_index: number | null;
                            trip_point: number | null;
                            weight: number | null }>;
        cdevs_present_count: number;
      }>;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  hybridCpuTopoStatus: () =>
    fetch("/api/hybrid-cpu-topo").then(jsonOf<{
      ok: boolean;
      cpu_count: number;
      packages: number[];
      dies: number[];
      clusters: number[];
      freq_tiers_khz: number[];
      per_cpu: Array<{ cpu: number; package_id: number | null;
                         die_id: number | null;
                         cluster_id: number | null;
                         core_id: number | null;
                         max_freq_khz: number | null }>;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  fileLocksAuditStatus: () =>
    fetch("/api/file-locks-audit").then(jsonOf<{
      ok: boolean;
      lock_count: number;
      conflict_count: number;
      orphan_count: number;
      llm_lock_count: number;
      conflicts: Array<{
        inode_key: number[];
        paths: string[];
        is_llm: boolean;
        writers: Array<{ pid: number; comm: string | null;
                          access: string; path: string | null }>;
      }>;
      llm_locks: Array<{ pid: number; comm: string | null;
                          access: string; path: string | null;
                          inode: number; pid_alive: boolean }>;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  // ── R&D #43 (UI sprint 34) ──
  nicRingAuditStatus: () =>
    fetch("/api/nic-ring-audit").then(jsonOf<{
      ok: boolean;
      device_count?: number;
      devices: Array<{
        dev: string; operstate: string; carrier: number | null;
        mtu: number | null;
        rx_dropped?: number; rx_fifo_errors?: number;
        rx_missed_errors?: number; rx_crc_errors?: number;
        rx_frame_errors?: number; rx_packets?: number; rx_bytes?: number;
        tx_dropped?: number; tx_fifo_errors?: number;
        tx_packets?: number; tx_bytes?: number;
      }>;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  irqRatesAuditStatus: () =>
    fetch("/api/irq-rates-audit").then(jsonOf<{
      ok: boolean;
      cpu_count: number;
      irq_row_count: number;
      nonzero_irq_count: number;
      top_irqs: Array<{
        irq: string; counts: number[]; chip: string; device: string;
        total: number; hot_cpu: number; hot_share: number;
      }>;
      softirqs: Array<{ type: string; counts: number[]; total: number }>;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  zoneinfoAuditStatus: () =>
    fetch("/api/zoneinfo-audit").then(jsonOf<{
      ok: boolean;
      zone_count: number;
      zones: Array<{
        node: number; zone: string;
        free?: number; min?: number; low?: number;
        high?: number; managed?: number;
        nr_free_pages?: number;
      }>;
      vmstat: Record<string, number>;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  blockQueueAuditStatus: () =>
    fetch("/api/block-queue-audit").then(jsonOf<{
      ok: boolean;
      device_count?: number;
      devices: Array<{
        dev: string; scheduler: string | null;
        scheduler_available: string[];
        nr_requests: number | null;
        read_ahead_kb: number | null;
        rotational: number | null;
        nomerges: number | null; iostats: number | null;
        rq_affinity: number | null;
        max_sectors_kb: number | null;
        max_hw_sectors_kb: number | null;
        wbt_lat_usec: number | null;
        write_cache: string | null;
        logical_block_size: number | null;
        physical_block_size: number | null;
        model: string | null;
      }>;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  // ── R&D #44 (UI sprint 35) ──
  watchdogInventoryStatus: () =>
    fetch("/api/watchdog-inventory").then(jsonOf<{
      ok: boolean;
      device_count?: number;
      devices: Array<{
        name: string; identity: string | null;
        timeout: number | null; pretimeout: number | null;
        bootstatus: number | null; state: string | null;
        nowayout: number | null; fw_version: string | null;
        bootstatus_bits: Array<{ key: string; mask: number;
                                    description: string }>;
      }>;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  diskIoLatencyStatus: () =>
    fetch("/api/disk-io-latency").then(jsonOf<{
      ok: boolean;
      device_count?: number;
      devices: Array<{
        dev: string;
        reads_completed: number; writes_completed: number;
        read_ticks_ms: number; write_ticks_ms: number;
        avg_read_wait_ms: number; avg_write_wait_ms: number;
        ios_in_progress: number;
        inflight_read: number; inflight_write: number;
        inflight_total: number;
        rotational: number | null;
      }>;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  netProtoCountersStatus: () =>
    fetch("/api/net-proto-counters").then(jsonOf<{
      ok: boolean;
      headline: Record<string, number | null>;
      sockstat: Record<string, Record<string, number>>;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  slabAuditStatus: () =>
    fetch("/api/slab-audit").then(jsonOf<{
      ok: boolean;
      cache_count: number;
      top_caches: Array<{
        name: string;
        objects?: number; object_size?: number;
        slabs?: number; partial?: number; cpu_slabs?: number;
        objs_per_slab?: number; resident_kb?: number;
      }>;
      requires_root: boolean;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  // ── R&D #45 (UI sprint 36) ──
  entropyAuditStatus: () =>
    fetch("/api/entropy-audit").then(jsonOf<{
      ok: boolean;
      random: Record<string, number>;
      hwrng: { available: boolean; current?: string | null;
                available_list?: string[]; quality?: number };
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  nfConntrackAuditStatus: () =>
    fetch("/api/nf-conntrack-audit").then(jsonOf<{
      ok: boolean;
      sysctls: Record<string, number>;
      stats: Record<string, number>;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  sysvipcAuditStatus: () =>
    fetch("/api/sysvipc-audit").then(jsonOf<{
      ok: boolean;
      shm_count?: number; sem_count?: number; msg_count?: number;
      shm_total_bytes?: number;
      top_shm?: Array<{
        shmid?: number; size?: number; nattch?: number;
        ctime?: number; uid?: number;
      }>;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  mdraidHealthStatus: () =>
    fetch("/api/mdraid-health").then(jsonOf<{
      ok: boolean;
      personalities?: string[];
      array_count?: number;
      arrays: Array<{
        name: string; state: string; level: string;
        members: string[]; marker: string;
        resync: string | null;
        sysfs?: {
          array_state?: string | null;
          sync_action?: string | null;
          sync_speed?: number | null;
          mismatch_cnt?: number | null;
          degraded?: number | null;
        };
      }>;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  // ── R&D #46 (UI sprint 37) ──
  keyringAuditStatus: () =>
    fetch("/api/keyring-audit").then(jsonOf<{
      ok: boolean;
      user_count?: number; key_count?: number;
      users: Array<{
        uid: number; total: number; used: number; refs: number;
        keys: number; maxkeys: number;
        bytes: number; maxbytes: number;
      }>;
      type_counts?: Record<string, number>;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  securityPostureStatus: () =>
    fetch("/api/security-posture").then(jsonOf<{
      ok: boolean;
      sysctls: Record<string, number>;
      security: {
        lsm?: string[];
        lockdown?: string | null;
        lockdown_available?: string[];
      };
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  vfsLimitsAuditStatus: () =>
    fetch("/api/vfs-limits-audit").then(jsonOf<{
      ok: boolean;
      limits: {
        file_nr?: { allocated: number; free: number; max: number };
        file_max?: number; nr_open?: number;
        aio_nr?: number; aio_max_nr?: number;
        pipe_max_size?: number;
        pipe_user_pages_soft?: number;
        pipe_user_pages_hard?: number;
      };
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  // ── R&D #47 (UI sprint 38) ──
  nvidiaRmAuditStatus: () =>
    fetch("/api/nvidia-rm-audit").then(jsonOf<{
      ok: boolean;
      driver_present: boolean;
      version_proc: string | null;
      version_smi: string | null;
      params: Record<string, string>;
      capabilities: string[];
      capability_count: number;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  mceAuditStatus: () =>
    fetch("/api/mce-audit").then(jsonOf<{
      ok: boolean;
      cpu_count: number;
      uniform_across_cpus: boolean;
      cpu0_state: {
        cpu?: number; check_interval?: number;
        cmci_disabled?: number; ignore_ce?: number;
        dont_log_ce?: number; monarch_timeout?: number;
        tolerant?: number; print_all?: number;
        banks?: Record<string, number>;
      };
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  acpiAuditStatus: () =>
    fetch("/api/acpi-audit").then(jsonOf<{
      ok: boolean;
      platform_profile: {
        current?: string | null;
        choices?: string[];
        pm_profile?: number;
      };
      wakeup_count: number;
      wakeups_enabled: Array<{
        device: string; s_state: string;
        status: string; sysfs: string; enabled: boolean;
      }>;
      gpe_count: number;
      top_gpes: Array<{ name: string; count: number; flag: string }>;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  schedAuditStatus: () =>
    fetch("/api/sched-audit").then(jsonOf<{
      ok: boolean;
      schedstat_version: number | null;
      cpu_count: number;
      top_cpus_by_wait: Array<{
        cpu: number; rq_cpu_time_ns: number;
        run_delay_ns: number; pcount: number;
        avg_wait_ns: number;
      }>;
      features: Record<string, boolean>;
      features_readable: boolean;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  // ── R&D #48 (UI sprint 39) ──
  dmaAuditStatus: () =>
    fetch("/api/dma-audit").then(jsonOf<{
      ok: boolean;
      dma_engine_count?: number;
      dma_engines: Array<{
        name: string; bytes_transferred: number | null;
        in_use: number | null; memcpy_count: number | null;
      }>;
      swiotlb: {
        available: boolean; permission_error: boolean;
        io_tlb_nslabs?: number; io_tlb_used?: number;
        used_ratio?: number;
      };
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  ftraceAuditStatus: () =>
    fetch("/api/ftrace-audit").then(jsonOf<{
      ok: boolean;
      state: {
        available?: boolean;
        current_tracer?: string;
        tracing_on?: number;
        kprobe_events?: string[];
        uprobe_events?: string[];
        set_event_count?: number;
      };
      requires_root: boolean;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  usbTopologyAuditStatus: () =>
    fetch("/api/usb-topology-audit").then(jsonOf<{
      ok: boolean;
      device_count?: number;
      non_root_count?: number;
      total_power_ma?: number;
      devices: Array<{
        name: string; is_root_hub: boolean;
        idVendor: string | null; idProduct: string | null;
        manufacturer: string | null; product: string | null;
        speed_mbps: number | null; version: string | null;
        bMaxPower_mA: number | null; authorized: number | null;
        power_control: string | null;
        autosuspend_delay_ms: number | null;
        bcdDevice: string | null;
      }>;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  journalAuditStatus: () =>
    fetch("/api/journal-audit").then(jsonOf<{
      ok: boolean;
      config: Record<string, string>;
      journal_bytes: number;
      journal_gib: number;
      persistent_dir_exists: boolean;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  // ── R&D #49 (UI sprint 40) ──
  rtcClockAuditStatus: () =>
    fetch("/api/rtc-clock-audit").then(jsonOf<{
      ok: boolean;
      rtc_count?: number;
      rtcs: Array<{
        name: string; rtc_name: string | null;
        since_epoch: number | null;
        date: string | null; time: string | null;
        hctosys: number | null; wakealarm: string | null;
        max_user_freq: number | null;
      }>;
      pps_sources: string[];
      system_epoch?: number;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  tpmAuditStatus: () =>
    fetch("/api/tpm-audit").then(jsonOf<{
      ok: boolean;
      tpm_count?: number;
      tpms: Array<{
        name: string;
        tpm_version_major: number | null;
        active_locality: number | null;
        firmware_path: string | null;
        vendor_id_str: string | null;
      }>;
      measured_boot: { available: boolean;
                         permission_error: boolean;
                         size_bytes: number };
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  wmiVendorAuditStatus: () =>
    fetch("/api/wmi-vendor-audit").then(jsonOf<{
      ok: boolean;
      wmi_guid_count: number;
      wmi_guids: string[];
      vendor_drivers: Array<{
        name: string;
        charge_control_start_threshold?: number;
        charge_control_end_threshold?: number;
        fan_mode?: number;
        cooling_method?: number;
        battery_mode?: number;
      }>;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  kmsgAuditStatus: () =>
    fetch("/api/kmsg-audit").then(jsonOf<{
      ok: boolean;
      printk: Record<string, number>;
      printk_ratelimit_sec: number | null;
      printk_ratelimit_burst: number | null;
      dmesg_restrict: number | null;
      kmsg: {
        available: boolean; permission_error: boolean;
        records_read: number; suppressed_count: number;
        by_level: Record<string, number>;
      };
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  // ── R&D #50 (UI sprint 41) ──
  sockPoolAuditStatus: () =>
    fetch("/api/sock-pool-audit").then(jsonOf<{
      ok: boolean;
      sockstat: Record<string, Record<string, number>>;
      sockstat6: Record<string, Record<string, number>>;
      tcp_socket_count: number;
      tcp6_socket_count: number;
      unix_socket_count: number;
      tcp_max_tw_buckets: number | null;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  iioSensorAuditStatus: () =>
    fetch("/api/iio-sensor-audit").then(jsonOf<{
      ok: boolean;
      device_count?: number;
      devices: Array<{
        name: string; driver_name: string | null;
        sampling_frequency: number | null;
        sensor_type?: string;
        [attr: string]: unknown;
      }>;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  drmAuditStatus: () =>
    fetch("/api/drm-audit").then(jsonOf<{
      ok: boolean;
      card_count?: number;
      cards: string[];
      connector_count?: number;
      connectors: Array<{
        name: string; status: string | null;
        enabled: string | null; dpms: string | null;
        modes: string[]; mode_count: number;
        edid_bytes: number;
      }>;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  cgroupMemeventsAuditStatus: () =>
    fetch("/api/cgroup-memevents-audit").then(jsonOf<{
      ok: boolean;
      unit_count?: number;
      top_units: Array<{
        path: string;
        events: Record<string, number>;
        swap_events: Record<string, number>;
        peak_bytes: number | null;
      }>;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  // ── R&D #51 (UI sprint 42) ──
  powerSupplyAuditStatus: () =>
    fetch("/api/power-supply-audit").then(jsonOf<{
      ok: boolean;
      supply_count?: number;
      supplies: Array<{
        name: string; type: string | null; status?: string | null;
        capacity?: number | null; cycle_count?: number | null;
        charge_full?: number | null; charge_full_design?: number | null;
        charge_control_end_threshold?: number | null;
        online?: number | null;
      }>;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  typecAuditStatus: () =>
    fetch("/api/typec-audit").then(jsonOf<{
      ok: boolean;
      port_count?: number;
      ports: Array<{
        name: string; data_role?: string | null;
        power_role?: string | null; preferred_role?: string | null;
        usb_typec_revision?: string | null;
        usb_power_delivery_revision?: string | null;
        partner?: Record<string, unknown> | null;
      }>;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  perfPmuAuditStatus: () =>
    fetch("/api/perf-pmu-audit").then(jsonOf<{
      ok: boolean;
      pmu_count?: number;
      pmus: Array<{
        name: string; type: number | null;
        nr_addr_filters: number | null;
        event_count: number; format_count: number;
        kind?: string;
      }>;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  iomemPciAuditStatus: () =>
    fetch("/api/iomem-pci-audit").then(jsonOf<{
      ok: boolean;
      iomem: {
        region_count: number;
        top_labels: Array<{ label: string; depth?: number }>;
        masked: boolean;
      };
      pci_device_count?: number;
      pci_devices: Array<{
        bdf: string; vendor: string | null; device: string | null;
        class: number | null; driver: string | null;
        reset_method: string | null;
        numa_node: number | null; enable: number | null;
      }>;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  // ── R&D #52 (UI sprint 43) ──
  ksmAuditStatus: () =>
    fetch("/api/ksm-audit").then(jsonOf<{
      ok: boolean;
      ksm: {
        available: boolean;
        run?: number | null; pages_sharing?: number | null;
        pages_shared?: number | null; pages_to_scan?: number | null;
        sleep_millisecs?: number | null;
        merge_across_nodes?: number | null;
        use_zero_pages?: number | null;
      };
      thp: {
        available: boolean;
        enabled?: string | null; defrag?: string | null;
        khugepaged_defrag?: number | null;
        khugepaged_alloc_sleep_millisecs?: number | null;
      };
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  i2cSmbusAuditStatus: () =>
    fetch("/api/i2c-smbus-audit").then(jsonOf<{
      ok: boolean;
      adapter_count?: number;
      adapters: Array<{ id: string; name: string | null; driver: string | null }>;
      dev_node_count?: number;
      dev_nodes: Array<{ name: string; mode: number; uid: number; gid: number }>;
      i2c_dev_class: string[];
      nvidia_display: string[];
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  moduleIntegrityAuditStatus: () =>
    fetch("/api/module-integrity-audit").then(jsonOf<{
      ok: boolean;
      tainted_mask: number | null;
      tainted_letters: string[];
      modules_disabled: number | null;
      tainted_modules: Array<{ name: string; taint: string; srcversion: string | null }>;
      nvidia_loaded_version: string | null;
      nvidia_runtime_version: string | null;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  // ── R&D #53 (UI sprint 44) ──
  psiPressureAuditStatus: () =>
    fetch("/api/psi-pressure-audit").then(jsonOf<{
      ok: boolean;
      pressure: {
        available: boolean;
        cpu?: { some?: { avg10: number; avg60: number; avg300: number; total: number } };
        memory?: { some?: { avg10: number; avg60: number; avg300: number; total: number };
                   full?: { avg10: number; avg60: number; avg300: number; total: number } };
        io?: { some?: { avg10: number; avg60: number; avg300: number; total: number };
               full?: { avg10: number; avg60: number; avg300: number; total: number } };
      };
      sched_schedstats: number | null;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  cpuVulnerabilitiesAuditStatus: () =>
    fetch("/api/cpu-vulnerabilities-audit").then(jsonOf<{
      ok: boolean;
      vuln_count?: number;
      vulnerabilities: Record<string, string>;
      smt: { active: string | null; control: string | null };
      cmdline_off_tokens: string[];
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  imaIntegrityAuditStatus: () =>
    fetch("/api/ima-integrity-audit").then(jsonOf<{
      ok: boolean;
      ima: {
        available: boolean;
        runtime_measurements_count?: number | null;
        violations?: number | null;
        policy_readable?: boolean;
        policy_lines?: number | null;
        permission_denied?: boolean;
      };
      evm: { available: boolean; armed?: boolean | null;
             raw?: string | null; permission_denied?: boolean };
      secureboot: { present: boolean; enabled: boolean | null };
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  raplPowerCapAuditStatus: () =>
    fetch("/api/rapl-power-cap-audit").then(jsonOf<{
      ok: boolean;
      zone_count?: number;
      zones: Array<{
        id: string; name: string | null; enabled: number | null;
        constraint_0_power_limit_uw: number | null;
        constraint_0_time_window_us: number | null;
        constraint_1_power_limit_uw: number | null;
        max_power_range_uw: number | null;
      }>;
      cpu_count?: number;
      governor_histogram: Record<string, number>;
      turbo: {
        cpufreq_boost: number | null;
        intel_pstate_no_turbo?: number | null;
        intel_pstate_max_perf_pct?: number | null;
        intel_pstate_energy_efficiency?: number | null;
      };
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  // ── R&D #54 (UI sprint 45) ──
  swapTunablesAuditStatus: () =>
    fetch("/api/swap-tunables-audit").then(jsonOf<{
      ok: boolean;
      vm_knobs: {
        available: boolean;
        swappiness?: number | null;
        "page-cluster"?: number | null;
        watermark_scale_factor?: number | null;
        watermark_boost_factor?: number | null;
        min_free_kbytes?: number | null;
        extfrag_threshold?: number | null;
      };
      swap_mm: { available: boolean; vma_ra_enabled?: string | null };
      swaps: Array<{ path: string; type: string;
                       size_kib: number | null;
                       used_kib: number | null;
                       device: string | null;
                       rotational: number | null }>;
      zram_active: string[];
      gpu_present: boolean;
      mem_total_kib: number | null;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  hugepagesAuditStatus: () =>
    fetch("/api/hugepages-audit").then(jsonOf<{
      ok: boolean;
      pools: Array<{
        size_kb: number; nr: number | null; free: number | null;
        surplus: number | null; resv: number | null;
        nr_overcommit: number | null;
      }>;
      per_node: Record<string, Record<string, number>>;
      meminfo: Record<string, number>;
      vm_nr_hugepages: number | null;
      vm_nr_overcommit_hugepages: number | null;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  kvmMiscAuditStatus: () =>
    fetch("/api/kvm-misc-audit").then(jsonOf<{
      ok: boolean;
      kvm_module_present: boolean;
      kvm_variant: string | null;
      nested: string | null;
      kvm_params: { halt_poll_ns?: number | null;
                     kvmclock_periodic_sync?: string | null;
                     tdp_mmu?: string | null };
      vfio_pci_loaded: boolean;
      dev_kvm: { present: boolean; mode?: number;
                   uid?: number; gid?: number;
                   group_name?: string | null };
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  ioUringRuntimeAuditStatus: () =>
    fetch("/api/io-uring-runtime-audit").then(jsonOf<{
      ok: boolean;
      kernel_release: string;
      io_uring_disabled: number | null;
      io_uring_group: number | null;
      sysctl_present: boolean;
      debugfs_present: boolean;
      debugfs_readable: boolean;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  // ── R&D #55 (UI sprint 46) ──
  edacEccAuditStatus: () =>
    fetch("/api/edac-ecc-audit").then(jsonOf<{
      ok: boolean;
      edac_present: boolean;
      controller_count?: number;
      controllers: Array<{
        id: string; ue_count: number | null; ce_count: number | null;
        mc_name: string | null; size_mb: number | null;
        dimms: Array<{ id: string; ue_count: number | null;
                          ce_count: number | null;
                          label: string | null;
                          location: string | null;
                          size: number | null }>;
      }>;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  numaTopologyAuditStatus: () =>
    fetch("/api/numa-topology-audit").then(jsonOf<{
      ok: boolean;
      node_count?: number;
      nodes: Array<{ id: number; distance: string[];
                      cpulist: string | null;
                      numastat: Record<string, number> }>;
      numa_balancing: number | null;
      nvidia_gpus: Array<{ bdf: string; numa_node: number | null;
                            local_cpulist: string | null }>;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  hwmonSensorsAuditStatus: () =>
    fetch("/api/hwmon-sensors-audit").then(jsonOf<{
      ok: boolean;
      hwmon_present: boolean;
      chip_count?: number;
      chips: Array<{ id: string; name: string | null;
                       fans: Record<string, { input?: number | null;
                                                 alarm?: number | null }>;
                       voltage_alarms: Record<string, { alarm?: number | null }>;
                       pwms: Record<string, { duty?: number | null;
                                                 enable?: number | null }> }>;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  efiBootOrderAuditStatus: () =>
    fetch("/api/efi-boot-order-audit").then(jsonOf<{
      ok: boolean;
      present: boolean;
      BootCurrent?: number | null;
      BootOrder?: number[];
      BootNext?: number | null;
      BootEntries?: number[];
      SecureBoot?: boolean | null;
      dbx_present?: boolean;
      varstore_total_bytes?: number;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  // ── R&D #56 (UI sprint 47) ──
  sataLinkPmAuditStatus: () =>
    fetch("/api/sata-link-pm-audit").then(jsonOf<{
      ok: boolean;
      host_count?: number;
      hosts: Array<{ id: string; policy: string | null }>;
      link_count?: number;
      links: Array<{ id: string; sata_spd: string | null;
                      sata_spd_limit: string | null }>;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  bdiWritebackAuditStatus: () =>
    fetch("/api/bdi-writeback-audit").then(jsonOf<{
      ok: boolean;
      bdi_count?: number;
      bdis: Array<{ id: string; read_ahead_kb: number | null;
                     max_ratio: number | null;
                     min_ratio: number | null;
                     stable_pages_required: number | null }>;
      device_map: Record<string, { name: string; rotational: number | null;
                                     is_nvme: boolean }>;
      real_partitions: string[];
      dirty_writeback_centisecs: number | null;
      dirty_expire_centisecs: number | null;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  procCryptoAuditStatus: () =>
    fetch("/api/proc-crypto-audit").then(jsonOf<{
      ok: boolean;
      entry_count?: number;
      name_count?: number;
      name_histogram: Record<string, number>;
      fips_enabled: number | null;
      cpu_has_aes_flag: boolean;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  wakeupSourcesAuditStatus: () =>
    fetch("/api/wakeup-sources-audit").then(jsonOf<{
      ok: boolean;
      source_count?: number;
      top_sources: Array<{ id: string; name: string | null;
                            active_count: number | null;
                            event_count: number | null;
                            wakeup_count: number | null }>;
      uptime_s: number | null;
      wakeup_count: number | null;
      debugfs_readable: boolean;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  // ── R&D #57 (UI sprint 48) ──
  livepatchAuditStatus: () =>
    fetch("/api/livepatch-audit").then(jsonOf<{
      ok: boolean;
      livepatch_present: boolean;
      patch_count?: number;
      patches: Array<{ name: string; enabled: number | null;
                        transition: number | null;
                        has_signature: boolean }>;
      livepatch_replace: number | null;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  pagetypeinfoAuditStatus: () =>
    fetch("/api/pagetypeinfo-audit").then(jsonOf<{
      ok: boolean;
      permission_denied: boolean;
      free_page_rows: number;
      block_rows: number;
      extfrag_threshold: number | null;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  backlightPwmAuditStatus: () =>
    fetch("/api/backlight-pwm-audit").then(jsonOf<{
      ok: boolean;
      backlight_count?: number;
      backlights: Array<{ name: string; brightness: number | null;
                            max_brightness: number | null;
                            bl_power: number | null;
                            actual_brightness: number | null;
                            type: string | null }>;
      pwm_chip_count?: number;
      pwm_chips: Array<{ name: string; npwm: number | null;
                          channels: Array<{ name: string;
                                              enable: number | null;
                                              period: number | null;
                                              duty_cycle: number | null }> }>;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  loadavgPressureAuditStatus: () =>
    fetch("/api/loadavg-pressure-audit").then(jsonOf<{
      ok: boolean;
      loadavg_1m: number | null;
      loadavg_5m: number | null;
      loadavg_15m: number | null;
      procs_running: number | null;
      procs_blocked: number | null;
      nr_cpus: number;
      sched_rt_runtime_us: number | null;
      sched_rt_period_us: number | null;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  // ── R&D #58 (UI sprint 49) ──
  cgroupRootAuditStatus: () =>
    fetch("/api/cgroup-root-audit").then(jsonOf<{
      ok: boolean;
      controllers: string[];
      subtree_control: string[];
      hybrid_v1_dirs: string[];
      own_cgroup_path: string;
      stat: Record<string, number>;
      max_depth: string;
      max_descendants: string;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  kernelBuildConfigAuditStatus: () =>
    fetch("/api/kernel-build-config-audit").then(jsonOf<{
      ok: boolean;
      release: string;
      key_count: number;
      interesting: Record<string, string>;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  scsiTransportAuditStatus: () =>
    fetch("/api/scsi-transport-audit").then(jsonOf<{
      ok: boolean;
      disk_count?: number;
      disks: Array<{ id: string; cache_type: string | null;
                      FUA: number | null;
                      protection_type: number | null;
                      manage_start_stop: number | null;
                      allow_restart: number | null }>;
      device_count?: number;
      devices: Array<{ id: string; queue_depth: number | null;
                        state: string | null; type: number | null;
                        timeout: number | null;
                        eh_timeout: number | null }>;
      host_count?: number;
      hosts: Array<{ id: string; use_blk_mq: number | null;
                      can_queue: number | null;
                      cmd_per_lun: number | null }>;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  alsaCardsAuditStatus: () =>
    fetch("/api/alsa-cards-audit").then(jsonOf<{
      ok: boolean;
      card_count?: number;
      cards: Array<{ index: number; id: string; driver: string;
                      name: string;
                      power_control: string | null;
                      pcm_children: string[] }>;
      modules: Record<string, string>;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  // ── R&D #59 (UI sprint 50) ──
  dmiSmbiosAuditStatus: () =>
    fetch("/api/dmi-smbios-audit").then(jsonOf<{
      ok: boolean;
      dmi: Record<string, string | null>;
      is_vm: boolean;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  iommuGroupsAuditStatus: () =>
    fetch("/api/iommu-groups-audit").then(jsonOf<{
      ok: boolean;
      group_count: number;
      groups_sample: Record<string, string[]>;
      iommu_cmdline_tokens: string[];
      nvidia_gpus: string[];
      gpu_groups: Record<string, number>;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  pidRlimitsAuditStatus: () =>
    fetch("/api/pid-rlimits-audit").then(jsonOf<{
      ok: boolean;
      candidate_count: number;
      candidates: Array<{ pid: number; comm: string;
                            "Max locked memory": [string, string] | null;
                            "Max open files": [string, string] | null;
                            "Max processes": [string, string] | null;
                            "Max address space": [string, string] | null }>;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  // ── R&D #60 (UI sprint 51) ──
  virtGuestDetectAuditStatus: () =>
    fetch("/api/virt-guest-detect-audit").then(jsonOf<{
      ok: boolean;
      qemu_fw_cfg_present: boolean;
      xen_type: string | null;
      cpu_hypervisor_flag: boolean;
      virtio_device_count: number;
      virtio_devices: string[];
      kvm_loaded: boolean;
      nvidia_gpus: string[];
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  // ── R&D #61 (UI sprint 52) ──
  regulatorAuditStatus: () =>
    fetch("/api/regulator-audit").then(jsonOf<{
      ok: boolean;
      regulator_count: number;
      regulators: Array<{
        id: string; name: string | null; type: string | null;
        num_users: number | null;
        requested_microamps: number | null;
        suspend_mem_state: string | null;
        suspend_disk_state: string | null;
        suspend_standby_state: string | null;
        runtime_status: string | null;
      }>;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  alsaCodecDeepAuditStatus: () =>
    fetch("/api/alsa-codec-deep-audit").then(jsonOf<{
      ok: boolean;
      codec_count: number;
      codecs: Array<{
        card_index: number;
        codec_file: string;
        name: string | null;
        vendor_id: string | null;
        subsystem_id: string | null;
        power_setting: string | null;
        power_actual: string | null;
        pins: Array<{ jack: string; info: string }>;
      }>;
      pcm_open_per_card: Record<string, boolean>;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  // ── R&D #62 (UI sprint 53) ──
  devfreqAuditStatus: () =>
    fetch("/api/devfreq-audit").then(jsonOf<{
      ok: boolean;
      device_count: number;
      devices: Array<{
        name: string; governor: string | null;
        cur_freq: number | null; min_freq: number | null;
        max_freq: number | null;
        available_governors: string[];
        target_freq: number | null;
      }>;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  meiIntelMeAuditStatus: () =>
    fetch("/api/mei-intel-me-audit").then(jsonOf<{
      ok: boolean;
      device_count: number;
      devices: Array<{
        id: string; fw_status: string | null;
        fw_ver: string | null; hbm_ver: string | null;
        dev_state: string | null;
        tx_queue_limit: string | null;
      }>;
      dev_nodes: string[];
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  memoryHotplugAuditStatus: () =>
    fetch("/api/memory-hotplug-audit").then(jsonOf<{
      ok: boolean;
      sys_memory_present: boolean;
      block_size_bytes: number | null;
      block_count: number;
      blocks_sample: Array<{
        id: string; state: string | null;
        valid_zones: string | null; removable: number | null;
      }>;
      mem_total_kib: number | null;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  procTaskAffinityAuditStatus: () =>
    fetch("/api/proc-task-affinity-audit").then(jsonOf<{
      ok: boolean;
      candidate_count: number;
      candidates: Array<{
        pid: number; comm: string;
        Cpus_allowed_list: string | null;
        Mems_allowed_list: string | null;
        voluntary_ctxt_switches: number | null;
        nonvoluntary_ctxt_switches: number | null;
      }>;
      gpu_count: number;
      gpus: Array<{ bdf: string; local_cpulist: string;
                    numa_node: number | null }>;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  // ── R&D #63 (UI sprint 54) ──
  rfkillBluetoothAuditStatus: () =>
    fetch("/api/rfkill-bluetooth-audit").then(jsonOf<{
      ok: boolean;
      rfkill_count: number;
      rfkills: Array<{
        id: string; name: string | null; type: string | null;
        state: number | null; soft: number | null;
        hard: number | null; persistent: number | null;
      }>;
      bluetooth_count: number;
      bluetooths: Array<{
        id: string; address: string | null; type: string | null;
        power_control: string | null;
      }>;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  ledsClassAuditStatus: () =>
    fetch("/api/leds-class-audit").then(jsonOf<{
      ok: boolean;
      led_count: number;
      leds: Array<{
        id: string; trigger_raw: string | null;
        active_trigger: string | null;
        brightness: number | null;
        max_brightness: number | null;
      }>;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  binfmtMiscAuditStatus: () =>
    fetch("/api/binfmt-misc-audit").then(jsonOf<{
      ok: boolean;
      binfmt_present: boolean;
      status_text?: string;
      registration_count?: number;
      registrations?: Array<{
        name: string;
        enabled: boolean | null;
        interpreter: string | null;
        flags: string | null;
        offset: number | null;
        magic: string | null;
      }>;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  ptpClockAuditStatus: () =>
    fetch("/api/ptp-clock-audit").then(jsonOf<{
      ok: boolean;
      sys_ptp_present: boolean;
      phc_count: number;
      phcs: Array<{
        id: string; clock_name: string | null;
        max_adjustment: number | null;
        n_alarm: number | null;
        n_ext_ts: number | null;
        n_per_out: number | null;
        n_pins: number | null;
        pps_available: number | null;
      }>;
      dev_perms: Array<{ name: string; mode: number;
                          uid: number; gid: number }>;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  // ── R&D #64 (UI sprint 55) ──
  meiHdcpPxpAuditStatus: () =>
    fetch("/api/mei-hdcp-pxp-audit").then(jsonOf<{
      ok: boolean;
      hdcp_count: number;
      hdcp_clients: Array<{ id: string; state: string | null;
                             fw_status: string | null;
                             fw_ver: string | null;
                             hbm_ver: string | null }>;
      pxp_count: number;
      pxp_clients: Array<{ id: string; state: string | null;
                            fw_status: string | null;
                            fw_ver: string | null;
                            hbm_ver: string | null }>;
      intel_display_gpus: string[];
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  firmwareEddMmcAuditStatus: () =>
    fetch("/api/firmware-edd-mmc-audit").then(jsonOf<{
      ok: boolean;
      edd_count: number;
      edd_entries: Array<{
        id: string; mbr_signature: string | null;
        host_bus: string | null; interface: string | null;
      }>;
      mmc_count: number;
      mmc_devices: Array<{
        id: string; type: string | null; name: string | null;
        manfid: string | null; oemid: string | null;
        serial: string | null; life_time: string | null;
      }>;
      mmc_host_count: number;
      mmc_hosts: Array<{ id: string; clock: number | null }>;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  devlinkSmartnicAuditStatus: () =>
    fetch("/api/devlink-smartnic-audit").then(jsonOf<{
      ok: boolean;
      link_count: number;
      status_histogram: Record<string, number>;
      links_sample: Array<{
        id: string; status: string | null;
        runtime_pm: number | null;
        auto_remove_on: string | null;
        sync_state_only: number | null;
      }>;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  procNsMountinfoAuditStatus: () =>
    fetch("/api/proc-ns-mountinfo-audit").then(jsonOf<{
      ok: boolean;
      self_ns: Record<string, string | null>;
      candidate_count: number;
      candidates: Array<{
        pid: number; comm: string;
        ns: Record<string, string | null>;
        has_nvidia: boolean;
      }>;
      host_has_nvidia: boolean;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  // ── R&D #65 (UI sprint 56) ──
  cpuidleResidencyAuditStatus: () =>
    fetch("/api/cpuidle-residency-audit").then(jsonOf<{
      ok: boolean;
      cpu_count: number;
      state_count_per_cpu: number;
      sample_cpu_states: Array<{
        id: string; idx: number; name: string | null;
        disable: number | null; residency: number | null;
        time: number | null; usage: number | null;
        above: number | null; below: number | null;
      }>;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  cpufreqResidencyAuditStatus: () =>
    fetch("/api/cpufreq-residency-audit").then(jsonOf<{
      ok: boolean;
      cpu_count: number;
      sample_cpu_index: number | null;
      sample_stats: {
        time_in_state?: Array<[number, number]>;
        total_trans?: number | null;
      };
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  efiRuntimeMapAuditStatus: () =>
    fetch("/api/efi-runtime-map-audit").then(jsonOf<{
      ok: boolean;
      efi_present: boolean;
      runtime_map_present: boolean;
      entry_count: number;
      entries_sample: Array<{
        id: string; type: number | null;
        num_pages: number | null;
        attribute: number | null;
        phys_addr: string | null; virt_addr: string | null;
      }>;
      permission_denied: boolean;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  devfreqEventAuditStatus: () =>
    fetch("/api/devfreq-event-audit").then(jsonOf<{
      ok: boolean;
      event_class_present: boolean;
      devfreq_class_present: boolean;
      event_count: number;
      events: Array<{ id: string; name: string | null;
                       enable_count: number | null }>;
      devfreq_devices: Array<{ id: string; governor: string | null }>;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  // ── R&D #66 (UI sprint 57) ──
  mtdFlashAuditStatus: () =>
    fetch("/api/mtd-flash-audit").then(jsonOf<{
      ok: boolean;
      mtd_count: number;
      mtds: Array<{ id: string; name: string | null;
                    size: number | null; erasesize: number | null;
                    flags: number | null;
                    bad_blocks: number | null;
                    type: string | null }>;
      proc_mtd_present: boolean;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  spiFirmwareLoaderAuditStatus: () =>
    fetch("/api/spi-firmware-loader-audit").then(jsonOf<{
      ok: boolean;
      spi_master_count: number;
      spi_masters: Array<{ id: string }>;
      firmware_request_count: number;
      firmware_requests: Array<{ name: string;
                                   loading: number | null }>;
      profiling: number | null;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  procSyscallAuxvAuditStatus: () =>
    fetch("/api/proc-syscall-auxv-audit").then(jsonOf<{
      ok: boolean;
      sample_count: number;
      samples: Array<{ pid: number; state: string | null;
                          wchan: string | null;
                          syscall: string | null;
                          timerslack_ns: number | null }>;
      own_pid: number;
      own_hwcap: number | null;
      own_hwcap2: number | null;
      own_pagesz: number | null;
      own_secure: number | null;
      own_at_base: number | null;
      own_at_random_set: boolean;
      arch: string;
      battery_discharging: boolean;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  btfBpfAuditStatus: () =>
    fetch("/api/btf-bpf-audit").then(jsonOf<{
      ok: boolean;
      vmlinux_btf_bytes: number | null;
      module_btf_count: number;
      loaded_module_count: number;
      module_btf_coverage: number | null;
      bpf_pinfs: { present: boolean; readable: boolean;
                     entries: number | null };
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  // ── R&D #67 (UI sprint 58) ──
  efiEsrtAuditStatus: () =>
    fetch("/api/efi-esrt-audit").then(jsonOf<{
      ok: boolean;
      efi_present: boolean;
      esrt_present: boolean;
      fw_resource_count: number | null;
      fw_resource_count_max: number | null;
      fw_resource_version: number | null;
      entry_count: number;
      entries_sample: Array<{
        id: string;
        fw_class: string | null;
        fw_type: number | null;
        fw_version: number | null;
        lowest_supported_fw_version: number | null;
        capsule_flags: number | null;
        last_attempt_status: number | null;
        last_attempt_version: number | null;
      }>;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  vmallocinfoAuditStatus: () =>
    fetch("/api/vmallocinfo-audit").then(jsonOf<{
      ok: boolean;
      file_present: boolean;
      permission_denied: boolean;
      alloc_count: number;
      total_bytes: number;
      by_kind: Record<string, number>;
      top_callers: Array<{ caller: string; bytes: number }>;
      largest: { size: number; caller: string; kind: string } | null;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  fdinfoKindsAuditStatus: () =>
    fetch("/api/fdinfo-kinds-audit").then(jsonOf<{
      ok: boolean;
      pid_count: number;
      pids_with_anon: number;
      fdinfo_readable: number;
      all_kinds: Record<string, number>;
      iouring_in_nonroot: string[];
      eventfd_offenders: Array<{ pid: string; count: number }>;
      epoll_offenders: Array<{ pid: string; watches: number }>;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  timerListAuditStatus: () =>
    fetch("/api/timer-list-audit").then(jsonOf<{
      ok: boolean;
      timer_list_present: boolean;
      timer_list_permission_denied: boolean;
      active_hrtimers: number;
      broadcast_device_seen: boolean;
      tick_stopped_zero_count: number;
      cpus_seen: number;
      timer_stats_present: boolean;
      clocksource_current: string | null;
      clocksource_available: string[];
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  // ── R&D #68 (UI sprint 59) ──
  pstoreCrashlogAuditStatus: () =>
    fetch("/api/pstore-crashlog-audit").then(jsonOf<{
      ok: boolean;
      mounted: boolean;
      backend: string | null;
      directory_present: boolean;
      permission_denied: boolean;
      entry_count: number;
      entries: Array<{ name: string; size: number | null }>;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  lruGenMglruAuditStatus: () =>
    fetch("/api/lru-gen-mglru-audit").then(jsonOf<{
      ok: boolean;
      mglru_present: boolean;
      enabled: number | null;
      min_ttl_ms: number | null;
      swap_used_kib: number;
      psi_full_avg60: number | null;
      debug_lru_gen_readable: boolean | null;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  fsSpecificTunablesAuditStatus: () =>
    fetch("/api/fs-specific-tunables-audit").then(jsonOf<{
      ok: boolean;
      ext4_present: boolean;
      xfs_present: boolean;
      f2fs_present: boolean;
      ext4_devices: Array<{
        dev: string;
        errors_count: number | null;
        warning_count: number | null;
        first_error_time: number | null;
        lifetime_write_kbytes: number | null;
      }>;
      xfs_devices: Array<{
        dev: string; stats_present: boolean;
        metadata_corruption_counter: number;
      }>;
      f2fs_devices: Array<{
        dev: string; features: string | null;
        gc_idle: number | null;
      }>;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  dtMemmapFirmwareAuditStatus: () =>
    fetch("/api/dt-memmap-firmware-audit").then(jsonOf<{
      ok: boolean;
      arch: string;
      devicetree_present: boolean;
      memmap_entry_count: number;
      memmap_sample: Array<{
        id: string;
        start: string | null;
        end: string | null;
        type: string | null;
      }>;
      vmcoreinfo_present: boolean;
      vmcoreinfo_readable: boolean;
      vmcoreinfo_bytes: number;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  // ── R&D #69 (UI sprint 60) ──
  nvmemInventoryAuditStatus: () =>
    fetch("/api/nvmem-inventory-audit").then(jsonOf<{
      ok: boolean;
      present: boolean;
      listable: boolean;
      device_count: number;
      devices: Array<{
        id: string;
        type: string | null;
        force_ro: string | null;
        nvmem_size: number | null;
        nvmem_mode: number | null;
      }>;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  damonCmaAuditStatus: () =>
    fetch("/api/damon-cma-audit").then(jsonOf<{
      ok: boolean;
      cma_present: boolean;
      damon_present: boolean;
      cma_region_count: number;
      cma_regions: Array<{
        name: string;
        count: number | null;
        used: number | null;
        nr_pages: number | null;
        alloc_pages_success: number | null;
        alloc_pages_fail: number | null;
      }>;
      kdamond_count: number;
      kdamonds: Array<{
        id: string;
        scheme_count: number;
        quota_breach_total: number;
      }>;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  kpageflagsAuditStatus: () =>
    fetch("/api/kpageflags-audit").then(jsonOf<{
      ok: boolean;
      present: boolean;
      readable: boolean;
      pages_sampled: number;
      flag_counts: Record<string, number>;
      unprivileged_userfaultfd: number | null;
      block_dump: number | null;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  procStaticKernelRegistryAuditStatus: () =>
    fetch("/api/proc-static-kernel-registry-audit").then(jsonOf<{
      ok: boolean;
      module_count: number;
      tainting_module_count: number;
      character_major_count: number;
      block_major_count: number;
      misc_count: number;
      filesystems: string[];
      console_count: number;
      enabled_console_count: number;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  // ── R&D #70 (UI sprint 61) ──
  remoteprocCoprocessorAuditStatus: () =>
    fetch("/api/remoteproc-coprocessor-audit").then(jsonOf<{
      ok: boolean;
      path_present: boolean;
      remoteproc_count: number;
      remoteprocs: Array<{
        id: string;
        state: string | null;
        name: string | null;
        firmware: string | null;
        recovery: string | null;
        crash_count: number | null;
      }>;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  uioGpioUserlandAuditStatus: () =>
    fetch("/api/uio-gpio-userland-audit").then(jsonOf<{
      ok: boolean;
      uio_present: boolean;
      gpio_present: boolean;
      uio_count: number;
      uios: Array<{
        id: string;
        name: string | null;
        version: string | null;
        dev_node_present: boolean;
        dev_node_mode: number | null;
      }>;
      gpio_chip_count: number;
      gpio_chips: Array<{
        id: string;
        label: string | null;
        base: string | null;
        ngpio: string | null;
      }>;
      legacy_gpio_pin_count: number;
      legacy_gpio_pins: Array<{
        pin: number;
        value: string | null;
        direction: string | null;
      }>;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  devcoredumpInventoryAuditStatus: () =>
    fetch("/api/devcoredump-inventory-audit").then(jsonOf<{
      ok: boolean;
      capability_present: boolean;
      global_disabled: number | null;
      pending_count: number;
      pending_dumps: Array<{
        id: string;
        failing_device: string | null;
        data_present: boolean;
        data_size: number | null;
        disabled: number | null;
        is_gpu: boolean;
      }>;
      per_driver_opt_outs: Array<{ module: string; disabled: string | null }>;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  cxlDaxMemoryAuditStatus: () =>
    fetch("/api/cxl-dax-memory-audit").then(jsonOf<{
      ok: boolean;
      cxl_present: boolean;
      dax_present: boolean;
      nd_present: boolean;
      cxl_decoder_count: number;
      cxl_mem_count: number;
      cxl_port_count: number;
      dax_device_count: number;
      nd_region_count: number;
      cxl_decoders: Array<{ id: string; state: string | null; size: string | null }>;
      dax_devices: Array<{
        id: string;
        size: number | null;
        target_node: number | null;
        align: number | null;
      }>;
      nd_regions: Array<{ id: string; size: number | null; set_cookie: string | null }>;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  // ── R&D #71 (UI sprint 62) ──
  usbRoleSwitchAuditStatus: () =>
    fetch("/api/usb-role-switch-audit").then(jsonOf<{
      ok: boolean;
      usb_role_present: boolean;
      typec_present: boolean;
      intel_xhci_sw_present: boolean;
      usb_role_count: number;
      usb_roles: Array<{ id: string; role: string | null }>;
      typec_port_count: number;
      typec_ports: Array<{
        id: string;
        data_role: string | null;
        power_role: string | null;
      }>;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  pageIdleTrackingAuditStatus: () =>
    fetch("/api/page-idle-tracking-audit").then(jsonOf<{
      ok: boolean;
      page_idle_present: boolean;
      bitmap_present: boolean;
      bitmap_readable: boolean | null;
      kpagecount_present: boolean;
      kpagecount_readable: boolean | null;
      page_cluster: number | null;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  edacDimmCeTrendAuditStatus: () =>
    fetch("/api/edac-dimm-ce-trend-audit").then(jsonOf<{
      ok: boolean;
      edac_present: boolean;
      mc_count: number;
      dimm_count: number;
      dimms: Array<{
        mc: string;
        dimm: string;
        label: string | null;
        size_mb: number | null;
        ce_count: number | null;
        ue_count: number | null;
      }>;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  ataPortSataAuditStatus: () =>
    fetch("/api/ata-port-sata-audit").then(jsonOf<{
      ok: boolean;
      ata_present: boolean;
      port_count: number;
      link_count: number;
      device_count: number;
      ports: Array<{ id: string; port_no: number | null }>;
      links: Array<{
        id: string;
        sata_spd_text: string | null;
        sata_spd: number | null;
        sata_spd_limit_text: string | null;
        sata_spd_limit: number | null;
        hw_sata_spd_limit_text: string | null;
        hw_sata_spd_limit: number | null;
      }>;
      devices: Array<{
        id: string;
        class: string | null;
        dma_mode: string | null;
        xfer_mode: string | null;
        spdn_cnt: number | null;
      }>;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  // ── R&D #72 (UI sprint 63) ──
  fwCfgBlobAuditStatus: () =>
    fetch("/api/fw-cfg-blob-audit").then(jsonOf<{
      ok: boolean;
      present: boolean;
      rev: number | null;
      entry_count: number;
      names_readable: boolean;
      entries_sample: Array<{
        key: number;
        name: string | null;
        size: number | null;
      }>;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  ueventHelperAuditStatus: () =>
    fetch("/api/uevent-helper-audit").then(jsonOf<{
      ok: boolean;
      uevent_helper_present: boolean;
      uevent_helper_readable: boolean;
      uevent_helper_value: string | null;
      hotplug_present: boolean;
      hotplug_readable: boolean;
      hotplug_value: string | null;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  dmiEntriesRawAuditStatus: () =>
    fetch("/api/dmi-entries-raw-audit").then(jsonOf<{
      ok: boolean;
      path_present: boolean;
      listable: boolean;
      entry_count: number;
      distinct_type_count: number;
      type_counts: Record<string, number>;
      entries_sample: Array<{
        id: string;
        type: number;
        type_label: string;
        instance: number;
        handle: number | null;
        length: number | null;
        type_readable: boolean;
      }>;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  tracingEventsEnableAuditStatus: () =>
    fetch("/api/tracing-events-enable-audit").then(jsonOf<{
      ok: boolean;
      present: boolean;
      subsystem_count: number;
      subsystems_sample: string[];
      gpu_subsystems_present: string[];
      readable: boolean;
      total_enabled: number;
      enabled_by_subsys: Record<string, string[]>;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  // ── R&D #73 (UI sprint 64) ──
  processIdLimitsAuditStatus: () =>
    fetch("/api/process-id-limits-audit").then(jsonOf<{
      ok: boolean;
      pid_max: number | null;
      threads_max: number | null;
      max_map_count: number | null;
      active_pids: number;
      pid_usage_pct: number | null;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  sysctlDevSubtreeAuditStatus: () =>
    fetch("/api/sysctl-dev-subtree-audit").then(jsonOf<{
      ok: boolean;
      present: boolean;
      scsi_logging_level: number | null;
      i915_perf_stream_paranoid: number | null;
      hpet_max_user_freq: number | null;
      cdrom_autoclose: number | null;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  kernelNotesVmcoreinfoAuditStatus: () =>
    fetch("/api/kernel-notes-vmcoreinfo-audit").then(jsonOf<{
      ok: boolean;
      notes_present: boolean;
      notes_size: number | null;
      vmcoreinfo_present: boolean;
      vmcoreinfo_size: number | null;
      kexec_loaded: number | null;
      kexec_crash_loaded: number | null;
      kexec_crash_size: number | null;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  firmwareAttributesAuditStatus: () =>
    fetch("/api/firmware-attributes-audit").then(jsonOf<{
      ok: boolean;
      present: boolean;
      vendor_count: number;
      vendors: string[];
      attribute_count: number;
      attributes_sample: Array<{
        vendor: string;
        name: string;
        current_value: string | null;
        default_value: string | null;
        type: string | null;
      }>;
      pending_reboot: Array<{ vendor: string; value: number | null }>;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  // ── R&D #74 (UI sprint 65) ──
  cpuIsolationAuditStatus: () =>
    fetch("/api/cpu-isolation-audit").then(jsonOf<{
      ok: boolean;
      present: boolean;
      isolated: number[];
      nohz_full: number[];
      offline: number[];
      possible_count: number;
      present_count: number;
      kernel_max: number | null;
      cmdline_isolcpus: number[];
      cmdline_nohz_full: number[];
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  dmaHeapAuditStatus: () =>
    fetch("/api/dma-heap-audit").then(jsonOf<{
      ok: boolean;
      present: boolean;
      heap_count: number;
      heaps: Array<{
        name: string;
        dev_node_present: boolean;
        dev_node_mode: number | null;
      }>;
      gpu_present: boolean;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  abiCompatAuditStatus: () =>
    fetch("/api/abi-compat-audit").then(jsonOf<{
      ok: boolean;
      abi_present: boolean;
      abi_knobs: Record<string, number>;
      ia32_emulation: number | null;
      binfmt_misc_status: string | null;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  v4l2MediaAuditStatus: () =>
    fetch("/api/v4l2-media-audit").then(jsonOf<{
      ok: boolean;
      v4l_present: boolean;
      media_present: boolean;
      cec_present: boolean;
      v4l_count: number;
      media_count: number;
      cec_count: number;
      v4l_devices: Array<{ name: string; driver: string | null }>;
      media_devices: Array<{ name: string; driver: string | null }>;
      cec_devices: Array<{ name: string; driver: string | null }>;
      dev_root_only: string[];
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  // ── R&D #75 (UI sprint 66) ──
  miscChardevAuditStatus: () =>
    fetch("/api/misc-chardev-audit").then(jsonOf<{
      ok: boolean;
      misc_count: number;
      sysfs_misc_count: number;
      kvm_module_loaded: boolean;
      watched_devices: Array<{
        name: string;
        present: boolean;
        mode: number | null;
      }>;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  sgxEnclaveAuditStatus: () =>
    fetch("/api/sgx-enclave-audit").then(jsonOf<{
      ok: boolean;
      cpu_has_sgx: boolean;
      cpu_has_sgx_lc: boolean;
      sgx_sysfs_entries: string[];
      dev_nodes: Array<{
        name: string;
        present: boolean;
        mode: number | null;
      }>;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  lsmSubtreeAuditStatus: () =>
    fetch("/api/lsm-subtree-audit").then(jsonOf<{
      ok: boolean;
      security_present: boolean;
      lsm_stack: string[];
      subdirs: string[];
      lockdown: string | null;
      apparmor_profile_count: number | null;
      core_files_readable: boolean;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  ipv4ConfPerIfaceAuditStatus: () =>
    fetch("/api/ipv4-conf-per-iface-audit").then(jsonOf<{
      ok: boolean;
      present: boolean;
      iface_count: number;
      ifaces: string[];
      knobs: Record<string, Record<string, number | null>>;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  // ── R&D #76 (UI sprint 67) ──
  inputDeviceAuditStatus: () =>
    fetch("/api/input-device-audit").then(jsonOf<{
      ok: boolean;
      present: boolean;
      device_count: number;
      wakeup_enabled_count: number;
      inhibited_count: number;
      devices: Array<{
        id: string;
        name: string | null;
        inhibited: number | null;
        modalias: string | null;
        wakeup: string | null;
        wakeup_count: number | null;
      }>;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  ipv6ConfPerIfaceAuditStatus: () =>
    fetch("/api/ipv6-conf-per-iface-audit").then(jsonOf<{
      ok: boolean;
      present: boolean;
      iface_count: number;
      ifaces: string[];
      knobs: Record<string, Record<string, number | null>>;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  wmiBusAuditStatus: () =>
    fetch("/api/wmi-bus-audit").then(jsonOf<{
      ok: boolean;
      present: boolean;
      guid_count: number;
      expensive_count: number;
      bound_count: number;
      guids: Array<{
        bus: string;
        guid: string;
        instance_count: number | null;
        expensive: number | null;
        object_id: string | null;
        setable: number | null;
        driver: string | null;
        driver_dangling: boolean;
        modalias: string | null;
      }>;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  numaHmatAccessAuditStatus: () =>
    fetch("/api/numa-hmat-access-audit").then(jsonOf<{
      ok: boolean;
      present: boolean;
      node_count: number;
      nodes: number[];
      accesses: Record<string, Record<string, {
        present: boolean;
        read_bandwidth: number | null;
        read_latency: number | null;
        write_bandwidth: number | null;
        write_latency: number | null;
      }>>;
      has_cpu: string | null;
      has_memory: string | null;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  // ── R&D #77 (UI sprint 68) ──
  cpuThermalThrottleCountersAuditStatus: () =>
    fetch("/api/cpu-thermal-throttle-counters-audit").then(jsonOf<{
      ok: boolean;
      cpu_count: number;
      counters_by_cpu: Record<string, Record<string, number | null>>;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  pciSriovPostureAuditStatus: () =>
    fetch("/api/pci-sriov-posture-audit").then(jsonOf<{
      ok: boolean;
      sriov_capable_count: number;
      active_vf_count: number;
      vfio_module_loaded: boolean;
      devices: Array<{
        bdf: string;
        sriov_totalvfs: number | null;
        sriov_numvfs: number | null;
        sriov_drivers_autoprobe: number | null;
        sriov_offset: number | null;
        sriov_stride: number | null;
        sriov_vf_total_msix: number | null;
      }>;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  cpuCppcAuditStatus: () =>
    fetch("/api/cpu-cppc-audit").then(jsonOf<{
      ok: boolean;
      cpu_count: number;
      scaling_driver: string | null;
      sample_cpu_cppc: Record<string, number | null> | null;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  pcieAerFleetAuditStatus: () =>
    fetch("/api/pcie-aer-fleet-audit").then(jsonOf<{
      ok: boolean;
      device_count: number;
      totals: { correctable: number; fatal: number; nonfatal: number };
      by_kind: Record<string, number>;
      devices_sample: Array<{
        bdf: string;
        class_id: number | null;
        driver: string | null;
        kind: string;
        correctable: number;
        fatal: number;
        nonfatal: number;
      }>;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>),

  // ── R&D #78 (UI sprint 69) ──
  netIfaceCountersAuditStatus: () =>
    fetch("/api/net-iface-counters-audit").then(jsonOf<{
      ok: boolean;
      iface_count: number;
      ifaces: string[];
      verdict: { verdict: string; reason: string };
    }>),

  netStackingTopologyAuditStatus: () =>
    fetch("/api/net-stacking-topology-audit").then(jsonOf<{
      ok: boolean;
      iface_count: number;
      bonds: string[];
      bridges: string[];
      verdict: { verdict: string; reason: string };
    }>),

  procMapsAnomalyAuditStatus: () =>
    fetch("/api/proc-maps-anomaly-audit").then(jsonOf<{
      ok: boolean;
      pid_count_total: number;
      pid_count_scanned: number;
      verdict: { verdict: string; reason: string };
    }>),

  // ── R&D #79 (UI sprint 70) ──
  softnetStatAuditStatus: () =>
    fetch("/api/softnet-stat-audit").then(jsonOf<{
      ok: boolean;
      cpu_count: number;
      totals: {
        dropped: number;
        time_squeeze: number;
        cpu_collision: number;
        processed: number;
      };
      verdict: { verdict: string; reason: string };
    }>),

  routeTableAuditStatus: () =>
    fetch("/api/route-table-audit").then(jsonOf<{
      ok: boolean;
      v4_route_count: number;
      v6_route_count: number;
      default_v4_count: number;
      host_v4_count: number;
      verdict: { verdict: string; reason: string };
    }>),

  fbVtconsoleAuditStatus: () =>
    fetch("/api/fb-vtconsole-audit").then(jsonOf<{
      ok: boolean;
      fb_count: number;
      fbs: Array<{ id: number; name: string }>;
      verdict: { verdict: string; reason: string };
    }>),

  schedTunablesAuditStatus: () =>
    fetch("/api/sched-tunables-audit").then(jsonOf<{
      ok: boolean;
      tunables: Record<string, number | null>;
      features_readable: boolean;
      feature_count: number;
      verdict: { verdict: string; reason: string };
    }>),

  // ── R&D #80 (UI sprint 71) ──
  arpNeighborAuditStatus: () =>
    fetch("/api/arp-neighbor-audit").then(jsonOf<{
      ok: boolean;
      entries: number;
      incomplete_count: number;
      table_fulls: number;
      gc_thresholds: {
        gc_thresh1: number | null;
        gc_thresh2: number | null;
        gc_thresh3: number | null;
      };
      verdict: { verdict: string; reason: string };
    }>),

  snmp6IcmpAuditStatus: () =>
    fetch("/api/snmp6-icmp-audit").then(jsonOf<{
      ok: boolean;
      counter_count: number;
      sample: Record<string, number>;
      verdict: { verdict: string; reason: string };
    }>),

  btrfsAllocatorAuditStatus: () =>
    fetch("/api/btrfs-allocator-audit").then(jsonOf<{
      ok: boolean;
      fs_count: number;
      filesystems: Array<{
        uuid: string;
        allocation: Record<string, Record<string, Record<string, number | null>>>;
      }>;
      verdict: { verdict: string; reason: string };
    }>),

  procStatusCapsAuditStatus: () =>
    fetch("/api/proc-status-caps-audit").then(jsonOf<{
      ok: boolean;
      pid_count_total: number;
      pid_count_scanned: number;
      cap_last_cap: number | null;
      verdict: { verdict: string; reason: string };
    }>),

  // ── R&D #81 (UI sprint 72) ──
  xhciCompanionAuditStatus: () =>
    fetch("/api/xhci-companion-audit").then(jsonOf<{
      ok: boolean;
      hub_count: number;
      usb3_count: number;
      usb2_count: number;
      hubs: Array<{
        node: string;
        version: string | null;
        version_major: number | null;
        speed: number | null;
        maxchild: number | null;
        pci_bdf: string | null;
      }>;
      verdict: { verdict: string; reason: string };
    }>),

  bpfProgramInventoryAuditStatus: () =>
    fetch("/api/bpf-program-inventory-audit").then(jsonOf<{
      ok: boolean;
      bpffs_mounted: boolean;
      pin_count: number | null;
      pin_readable: boolean;
      prog_id_count: number;
      map_id_count: number;
      pids_scanned: number;
      verdict: { verdict: string; reason: string };
    }>),

  cgroupIoStatAuditStatus: () =>
    fetch("/api/cgroup-io-stat-audit").then(jsonOf<{
      ok: boolean;
      cgroup_count: number;
      root_pressure: {
        some?: { avg10: number; avg60: number; avg300: number; total: number };
        full?: { avg10: number; avg60: number; avg300: number; total: number };
      } | null;
      top_writers: Array<{
        path: string;
        wbytes: number;
        rbytes: number;
      }>;
      verdict: { verdict: string; reason: string };
    }>),

  thermalTripDriftAuditStatus: () =>
    fetch("/api/thermal-trip-drift-audit").then(jsonOf<{
      ok: boolean;
      zone_count: number;
      zones: Array<{
        zone: string;
        type: string | null;
        temp_c: number | null;
        policy: string | null;
        trip_count: number;
      }>;
      verdict: { verdict: string; reason: string };
    }>),

  // ── R&D #82 (UI sprint 73) ──
  sysrqMaskAuditStatus: () =>
    fetch("/api/sysrq-mask-audit").then(jsonOf<{
      ok: boolean;
      values: {
        sysrq: number | null;
        kexec_load_disabled: number | null;
        sysrq_always_enabled: number | null;
      };
      verdict: { verdict: string; reason: string };
    }>),

  cpuDmaLatencyQosAuditStatus: () =>
    fetch("/api/cpu-dma-latency-qos-audit").then(jsonOf<{
      ok: boolean;
      cpu_count: number;
      clamped_count: number;
      holders: Array<{ pid: number; comm: string }>;
      pids_scanned: number;
      pids_inaccessible: number;
      verdict: { verdict: string; reason: string };
    }>),

  rcuExpeditedAuditStatus: () =>
    fetch("/api/rcu-expedited-audit").then(jsonOf<{
      ok: boolean;
      state: {
        rcu_expedited: number | null;
        rcu_normal: number | null;
        rcu_cpu_stall_timeout: number | null;
        isolated_cpus: number[];
        isolcpus_cmd: string | null;
        nohz_full_cmd: string | null;
        rcu_nocbs_cmd: string | null;
      };
      verdict: { verdict: string; reason: string };
    }>),

  pageOwnerFragAuditStatus: () =>
    fetch("/api/page-owner-frag-audit").then(jsonOf<{
      ok: boolean;
      extfrag_zones: number;
      unusable_zones: number;
      page_owner_present: boolean;
      thp_defrag: string | null;
      verdict: { verdict: string; reason: string };
    }>),

  // ── R&D #83 (UI sprint 74) ──
  blockIntegrityAuditStatus: () =>
    fetch("/api/block-integrity-audit").then(jsonOf<{
      ok: boolean;
      device_count: number;
      capable_count: number;
      devices: Array<{
        device: string;
        capable: number | null;
        format: string | null;
        read_verify: number | null;
        write_generate: number | null;
        tag_size: number | null;
        protection_interval_bytes: number | null;
      }>;
      verdict: { verdict: string; reason: string };
    }>),

  clkSummaryAuditStatus: () =>
    fetch("/api/clk-summary-audit").then(jsonOf<{
      ok: boolean;
      clock_count: number;
      read_state: string;
      verdict: { verdict: string; reason: string };
    }>),

  nfsdStatsAuditStatus: () =>
    fetch("/api/nfsd-stats-audit").then(jsonOf<{
      ok: boolean;
      nfsd_present: boolean;
      threads: number | null;
      pool_count: number;
      cpu_count: number;
      reply_cache: Record<string, number>;
      verdict: { verdict: string; reason: string };
    }>),

  driDebugfsAuditStatus: () =>
    fetch("/api/dri-debugfs-audit").then(jsonOf<{
      ok: boolean;
      minor_count: number;
      read_state: string;
      minors: Array<{
        id: string;
        name: string;
        client_count: number;
        gem_count: number;
      }>;
      verdict: { verdict: string; reason: string };
    }>),

  // ── R&D #84 (UI sprint 75) ──
  suspendStatsAuditStatus: () =>
    fetch("/api/suspend-stats-audit").then(jsonOf<{
      ok: boolean;
      success: number | null;
      fail: number | null;
      last_failed_dev: string | null;
      last_failed_errno: number | null;
      last_failed_step: string | null;
      verdict: { verdict: string; reason: string };
    }>),

  loopDeviceAuditStatus: () =>
    fetch("/api/loop-device-audit").then(jsonOf<{
      ok: boolean;
      loop_count_total: number;
      loop_count_active: number;
      loops: Array<{
        name: string;
        size_sectors: number | null;
        ro: number | null;
        backing_file: string | null;
      }>;
      verdict: { verdict: string; reason: string };
    }>),

  kernelModuleParamsDriftAuditStatus: () =>
    fetch("/api/kernel-module-params-drift-audit").then(jsonOf<{
      ok: boolean;
      scanned: number;
      drifted: number;
      params: Array<{
        module: string;
        param: string;
        value: string;
        default: string;
        risk: string;
        non_default: boolean;
      }>;
      verdict: { verdict: string; reason: string };
    }>),

  ttySerialConsoleAuditStatus: () =>
    fetch("/api/tty-serial-console-audit").then(jsonOf<{
      ok: boolean;
      consoles: string[];
      usb_serial_count: number;
      usb_serial_devices: Array<{
        name: string;
        runtime_status: string | null;
      }>;
      verdict: { verdict: string; reason: string };
    }>),

  // ── R&D #85 (UI sprint 76) ──
  dynamicDebugAuditStatus: () =>
    fetch("/api/dynamic-debug-audit").then(jsonOf<{
      ok: boolean;
      read_state: string;
      total_sites: number;
      enabled_sites: number;
      verdict: { verdict: string; reason: string };
    }>),

  extconStateAuditStatus: () =>
    fetch("/api/extcon-state-audit").then(jsonOf<{
      ok: boolean;
      device_count: number;
      devices: Array<{
        node: string;
        label: string;
        cables: Array<{
          name: string;
          value: string;
          asserted: boolean;
          invalid?: boolean;
        }>;
      }>;
      verdict: { verdict: string; reason: string };
    }>),

  unixSocketInventoryAuditStatus: () =>
    fetch("/api/unix-socket-inventory-audit").then(jsonOf<{
      ok: boolean;
      total: number;
      abstract: number;
      named: number;
      unnamed: number;
      listening: number;
      verdict: { verdict: string; reason: string };
    }>),

  schedFeaturesDebugfsAuditStatus: () =>
    fetch("/api/sched-features-debugfs-audit").then(jsonOf<{
      ok: boolean;
      read_state: string;
      feature_count: number;
      tuning_count: number;
      tunings: Record<string, number>;
      verdict: { verdict: string; reason: string };
    }>),

  // ── R&D #86 (UI sprint 77) ──
  wolEthtoolAuditStatus: () =>
    fetch("/api/wol-ethtool-audit").then(jsonOf<{
      ok: boolean;
      iface_count: number;
      interfaces: Array<{
        name: string;
        operstate: string;
        carrier: number | null;
        duplex: string;
        speed: number | null;
        wakeup: string;
      }>;
      verdict: { verdict: string; reason: string };
    }>),

  thunderboltUsb4AuditStatus: () =>
    fetch("/api/thunderbolt-usb4-audit").then(jsonOf<{
      ok: boolean;
      bus_present: boolean;
      domain_count: number;
      device_count: number;
      domains: Array<{
        name: string;
        security: string;
        iommu_dma_protection: number | null;
      }>;
      devices: Array<{
        name: string;
        authorized: number | null;
        vendor_name: string;
        device_name: string;
      }>;
      verdict: { verdict: string; reason: string };
    }>),

  nvmeControllerStateAuditStatus: () =>
    fetch("/api/nvme-controller-state-audit").then(jsonOf<{
      ok: boolean;
      controller_count: number;
      controllers: Array<{
        name: string;
        state: string;
        firmware_rev: string;
        numa_node: number | null;
        transport: string;
        model: string;
      }>;
      verdict: { verdict: string; reason: string };
    }>),

  workqueueCpumaskAuditStatus: () =>
    fetch("/api/workqueue-cpumask-audit").then(jsonOf<{
      ok: boolean;
      wq_count: number;
      global_cpumask: string;
      isolated_cpus: string;
      cpu_count: number;
      verdict: { verdict: string; reason: string };
    }>),

  // ── R&D #87 (UI sprint 78) ──
  usbAuthorizedDefaultAuditStatus: () =>
    fetch("/api/usb-authorized-default-audit").then(jsonOf<{
      ok: boolean;
      hub_count: number;
      usbguard_present: boolean;
      hubs: Array<{
        name: string;
        authorized_default: string;
        interface_authorized_default: string;
      }>;
      verdict: { verdict: string; reason: string };
    }>),

  procLocksContentionAuditStatus: () =>
    fetch("/api/proc-locks-contention-audit").then(jsonOf<{
      ok: boolean;
      total: number;
      blocked: number;
      blocked_pids: Array<{ pid: number; comm: string }>;
      verdict: { verdict: string; reason: string };
    }>),

  cpuSmtControlAuditStatus: () =>
    fetch("/api/cpu-smt-control-audit").then(jsonOf<{
      ok: boolean;
      smt_control: string;
      smt_active: string;
      vulns_inspected: number;
      verdict: { verdict: string; reason: string };
    }>),

  interruptSkewAuditStatus: () =>
    fetch("/api/interrupt-skew-audit").then(jsonOf<{
      ok: boolean;
      irq_count: number;
      mismatch_count: number;
      verdict: { verdict: string; reason: string };
    }>),

  // ── R&D #88 (UI sprint 79) ──
  userspaceHardeningSysctlsAuditStatus: () =>
    fetch("/api/userspace-hardening-sysctls-audit").then(jsonOf<{
      ok: boolean;
      sysctls: Record<string, number>;
      verdict: { verdict: string; reason: string };
    }>),

  suspendModeSelectorAuditStatus: () =>
    fetch("/api/suspend-mode-selector-audit").then(jsonOf<{
      ok: boolean;
      state: string;
      mem_sleep: string;
      disk: string;
      pm_test: string;
      swap_present: boolean;
      verdict: { verdict: string; reason: string };
    }>),

  iommuReservedRegionsAuditStatus: () =>
    fetch("/api/iommu-reserved-regions-audit").then(jsonOf<{
      ok: boolean;
      group_count: number;
      gpu_group_count: number;
      verdict: { verdict: string; reason: string };
    }>),

  timerMigrationNohzDriftAuditStatus: () =>
    fetch("/api/timer-migration-nohz-drift-audit").then(jsonOf<{
      ok: boolean;
      timer_migration: number | null;
      nohz_full: number[];
      isolated: number[];
      cmdline_nohz_full: number[];
      cmdline_rcu_nocbs: number[];
      verdict: { verdict: string; reason: string };
    }>),

  // ── R&D #89 (UI sprint 80) ──
  tcpCongestionControlAuditStatus: () =>
    fetch("/api/tcp-congestion-control-audit").then(jsonOf<{
      ok: boolean;
      current_cc: string;
      available_cc: string[];
      tcp_fastopen: number | null;
      verdict: { verdict: string; reason: string };
    }>),

  namespaceLimitsAuditStatus: () =>
    fetch("/api/namespace-limits-audit").then(jsonOf<{
      ok: boolean;
      limits: Record<string, number>;
      verdict: { verdict: string; reason: string };
    }>),

  sysvipcLimitsAuditStatus: () =>
    fetch("/api/sysvipc-limits-audit").then(jsonOf<{
      ok: boolean;
      limits: Record<string, number | null>;
      mem_total: number | null;
      page_size: number;
      verdict: { verdict: string; reason: string };
    }>),

  pcieLinkSpeedDriftAuditStatus: () =>
    fetch("/api/pcie-link-speed-drift-audit").then(jsonOf<{
      ok: boolean;
      device_count: number;
      linked_count: number;
      verdict: { verdict: string; reason: string };
    }>),

  // ── R&D #90 (UI sprint 81) ──
  resctrlAuditStatus: () =>
    fetch("/api/resctrl-audit").then(jsonOf<{
      ok: boolean;
      mounted: boolean;
      ctrl_group_count: number;
      verdict: { verdict: string; reason: string };
    }>),

  procNetProtocolsAuditStatus: () =>
    fetch("/api/proc-net-protocols-audit").then(jsonOf<{
      ok: boolean;
      packet_socket_count: number;
      raw_socket_count: number;
      verdict: { verdict: string; reason: string };
    }>),

  cpufreqGovernorTunablesAuditStatus: () =>
    fetch("/api/cpufreq-governor-tunables-audit").then(jsonOf<{
      ok: boolean;
      policy_count: number;
      governors: string[];
      verdict: { verdict: string; reason: string };
    }>),

  pcieDpcAuditStatus: () =>
    fetch("/api/pcie-dpc-audit").then(jsonOf<{
      ok: boolean;
      device_count: number;
      dpc_capable_count: number;
      verdict: { verdict: string; reason: string };
    }>),

  // ── R&D #91 (UI sprint 82) ──
  cgroupPidsControllerAuditStatus: () =>
    fetch("/api/cgroup-pids-controller-audit").then(jsonOf<{
      ok: boolean;
      cgroup_with_cap_count: number;
      verdict: { verdict: string; reason: string };
    }>),

  dmaBufBufinfoAuditStatus: () =>
    fetch("/api/dma-buf-bufinfo-audit").then(jsonOf<{
      ok: boolean;
      exporter_count: number;
      total_bytes: number;
      mem_total: number | null;
      verdict: { verdict: string; reason: string };
    }>),

  nvmeHmbFeaturesAuditStatus: () =>
    fetch("/api/nvme-hmb-features-audit").then(jsonOf<{
      ok: boolean;
      controller_count: number;
      hmb_using_count: number;
      max_host_mem_size_mb: number | null;
      verdict: { verdict: string; reason: string };
    }>),

  vmstatReclaimPressureAuditStatus: () =>
    fetch("/api/vmstat-reclaim-pressure-audit").then(jsonOf<{
      ok: boolean;
      has_prev_snapshot: boolean;
      watermark_scale_factor: number | null;
      verdict: { verdict: string; reason: string };
    }>),

  // ── R&D #92 (UI sprint 83) ──
  iommuDmaStrictAuditStatus: () =>
    fetch("/api/iommu-dma-strict-audit").then(jsonOf<{
      ok: boolean;
      strict_intel: string | null;
      strict_amd: string | null;
      strict_generic: string | null;
      cmdline_passthrough: boolean;
      cmdline_strict: string | null;
      group_type_sample: string[];
      verdict: { verdict: string; reason: string };
    }>),

  kernelLockupWatchdogAuditStatus: () =>
    fetch("/api/kernel-lockup-watchdog-audit").then(jsonOf<{
      ok: boolean;
      watchdog: number | null;
      nmi_watchdog: number | null;
      watchdog_thresh: number | null;
      nmi_hw_supported: boolean;
      verdict: { verdict: string; reason: string };
    }>),

  khugepagedPressureAuditStatus: () =>
    fetch("/api/khugepaged-pressure-audit").then(jsonOf<{
      ok: boolean;
      has_prev_snapshot: boolean;
      khugepaged_present: boolean;
      max_ptes_none: number | null;
      scan_sleep_ms: number | null;
      verdict: { verdict: string; reason: string };
    }>),

  drmFdinfoEngineUsageAuditStatus: () =>
    fetch("/api/drm-fdinfo-engine-usage-audit").then(jsonOf<{
      ok: boolean;
      drm_client_count: number;
      total_vram_bytes: number;
      readable_files: number;
      unreadable_files: number;
      verdict: { verdict: string; reason: string };
    }>),

  // ── R&D #93 (UI sprint 84) ──
  pipeMqueueLimitsAuditStatus: () =>
    fetch("/api/pipe-mqueue-limits-audit").then(jsonOf<{
      ok: boolean;
      limits: Record<string, number | null>;
      mem_total: number | null;
      verdict: { verdict: string; reason: string };
    }>),

  cgroupV2MemoryPeakAuditStatus: () =>
    fetch("/api/cgroup-v2-memory-peak-audit").then(jsonOf<{
      ok: boolean;
      cgroup_count: number;
      verdict: { verdict: string; reason: string };
    }>),

  nfsMountstatsAuditStatus: () =>
    fetch("/api/nfs-mountstats-audit").then(jsonOf<{
      ok: boolean;
      nfs_mount_count: number;
      verdict: { verdict: string; reason: string };
    }>),

  bpfJitXdpBusyPollAuditStatus: () =>
    fetch("/api/bpf-jit-xdp-busy-poll-audit").then(jsonOf<{
      ok: boolean;
      bpf_jit_enable: number | null;
      busy_poll: number | null;
      xdp_attached_ifaces: string[];
      verdict: { verdict: string; reason: string };
    }>),

  // ── R&D #94 (UI sprint 85) ──
  hwpoisonMemoryFailureAuditStatus: () =>
    fetch("/api/hwpoison-memory-failure-audit").then(jsonOf<{
      ok: boolean;
      hardware_corrupted_kib: number | null;
      hwpoison_counter_count: number;
      edac_present: boolean;
      verdict: { verdict: string; reason: string };
    }>),

  fsAioFanotifyLimitsAuditStatus: () =>
    fetch("/api/fs-aio-fanotify-limits-audit").then(jsonOf<{
      ok: boolean;
      limits: Record<string, number | null>;
      mem_total: number | null;
      verdict: { verdict: string; reason: string };
    }>),

  drmTtmPagePoolAuditStatus: () =>
    fetch("/api/drm-ttm-page-pool-audit").then(jsonOf<{
      ok: boolean;
      ttm_present: boolean;
      params: Record<string, number | null>;
      mem_available: number | null;
      verdict: { verdict: string; reason: string };
    }>),

  lockdepLockstatAuditStatus: () =>
    fetch("/api/lockdep-lockstat-audit").then(jsonOf<{
      ok: boolean;
      lockdep_present: boolean;
      lockdep_dead: boolean;
      verdict: { verdict: string; reason: string };
    }>),

  // ── R&D #95 (UI sprint 86) ──
  mdioPhyEeeAuditStatus: () =>
    fetch("/api/mdio-phy-eee-audit").then(jsonOf<{
      ok: boolean;
      phy_iface_count: number;
      verdict: { verdict: string; reason: string };
    }>),

  kernelModuleRefcntAuditStatus: () =>
    fetch("/api/kernel-module-refcnt-audit").then(jsonOf<{
      ok: boolean;
      module_count: number;
      verdict: { verdict: string; reason: string };
    }>),

  tracingBufferFootprintAuditStatus: () =>
    fetch("/api/tracing-buffer-footprint-audit").then(jsonOf<{
      ok: boolean;
      buffer_total_size_kb: number | null;
      trace_clock: string;
      tracing_on: number | null;
      total_overrun: number;
      verdict: { verdict: string; reason: string };
    }>),

  perDeviceWakeupAttributionAuditStatus: () =>
    fetch("/api/per-device-wakeup-attribution-audit").then(jsonOf<{
      ok: boolean;
      device_count: number;
      enabled_count: number;
      verdict: { verdict: string; reason: string };
    }>),

  // ── R&D #96 (UI sprint 87) ──
  blockDiscardCapsAuditStatus: () =>
    fetch("/api/block-discard-caps-audit").then(jsonOf<{
      ok: boolean;
      device_count: number;
      verdict: { verdict: string; reason: string };
    }>),

  cpusetV2PartitionAuditStatus: () =>
    fetch("/api/cpuset-v2-partition-audit").then(jsonOf<{
      ok: boolean;
      non_default_count: number;
      verdict: { verdict: string; reason: string };
    }>),

  tracingInstancesAuditStatus: () =>
    fetch("/api/tracing-instances-audit").then(jsonOf<{
      ok: boolean;
      instance_count: number;
      verdict: { verdict: string; reason: string };
    }>),

  blockHoldersStackAuditStatus: () =>
    fetch("/api/block-holders-stack-audit").then(jsonOf<{
      ok: boolean;
      dm_count: number;
      md_count: number;
      verdict: { verdict: string; reason: string };
    }>),

  // ── R&D #97 (UI sprint 88) ──
  kvmMmuAuditStatus: () =>
    fetch("/api/kvm-mmu-audit").then(jsonOf<{
      ok: boolean;
      kvm_present: boolean;
      verdict: { verdict: string; reason: string };
    }>),

  zfsArcAuditStatus: () =>
    fetch("/api/zfs-arc-audit").then(jsonOf<{
      ok: boolean;
      zfs_loaded: boolean;
      verdict: { verdict: string; reason: string };
    }>),

  cgroupDelegateAuditStatus: () =>
    fetch("/api/cgroup-delegate-audit").then(jsonOf<{
      ok: boolean;
      slice_count: number;
      delegate_present: boolean;
      verdict: { verdict: string; reason: string };
    }>),

  pciD3coldRuntimeAuditStatus: () =>
    fetch("/api/pci-d3cold-runtime-audit").then(jsonOf<{
      ok: boolean;
      gpu_addr: string | null;
      gpu_d3cold_allowed?: number | null;
      gpu_control?: string | null;
      gpu_runtime_status?: string | null;
      upstream_count: number;
      verdict: { verdict: string; reason: string };
    }>),

  // ── R&D #98 (UI sprint 89) ──
  psiIrqFullAuditStatus: () =>
    fetch("/api/psi-irq-full-audit").then(jsonOf<{
      ok: boolean;
      irq_present: boolean;
      cpu_full: { a10?: number; a60?: number; a300?: number; total?: number };
      irq_full: { a10?: number; a60?: number; a300?: number; total?: number };
      verdict: { verdict: string; reason: string };
    }>),

  fsQuotaProjidAuditStatus: () =>
    fetch("/api/fs-quota-projid-audit").then(jsonOf<{
      ok: boolean;
      quota_mount_count: number;
      overlay_count: number;
      projects_present: boolean;
      tools_present: boolean;
      verdict: { verdict: string; reason: string };
    }>),

  fuseConnectionsAuditStatus: () =>
    fetch("/api/fuse-connections-audit").then(jsonOf<{
      ok: boolean;
      connection_count: number;
      max_waiting: number;
      verdict: { verdict: string; reason: string };
    }>),

  keyringLifecycleAuditStatus: () =>
    fetch("/api/keyring-lifecycle-audit").then(jsonOf<{
      ok: boolean;
      gc_delay: number | null;
      persistent_keyring_expiry: number | null;
      uid_count: number;
      verdict: { verdict: string; reason: string };
    }>),

  // ── R&D #99 (UI sprint 90) ──
  umwaitControlAuditStatus: () =>
    fetch("/api/umwait-control-audit").then(jsonOf<{
      ok: boolean;
      waitpkg: boolean;
      enable_c02: number | null;
      max_time: number | null;
      verdict: { verdict: string; reason: string };
    }>),

  splitLockDetectAuditStatus: () =>
    fetch("/api/split-lock-detect-audit").then(jsonOf<{
      ok: boolean;
      intel: boolean;
      cmdline_mode: string | null;
      sysctl_mitigate: number | null;
      verdict: { verdict: string; reason: string };
    }>),

  oomPolicySysctlAuditStatus: () =>
    fetch("/api/oom-policy-sysctl-audit").then(jsonOf<{
      ok: boolean;
      panic_on_oom: number | null;
      oom_kill_allocating_task: number | null;
      oom_dump_tasks: number | null;
      mem_total_kb: number | null;
      verdict: { verdict: string; reason: string };
    }>),

  rseqKernelAuditStatus: () =>
    fetch("/api/rseq-kernel-audit").then(jsonOf<{
      ok: boolean;
      uname: string;
      CONFIG_RSEQ: string | null;
      CONFIG_DEBUG_RSEQ: string | null;
      CONFIG_FUTEX_PI: string | null;
      verdict: { verdict: string; reason: string };
    }>),

  // ── R&D #100 (UI sprint 91) ──
  workqueuePowerEfficientAuditStatus: () =>
    fetch("/api/workqueue-power-efficient-audit").then(jsonOf<{
      ok: boolean;
      power_efficient: string | null;
      cpu_intensive_thresh_us: number | null;
      default_affinity_scope: string | null;
      cmdline_power_efficient: string | null;
      verdict: { verdict: string; reason: string };
    }>),

  bqlStallCountersAuditStatus: () =>
    fetch("/api/bql-stall-counters-audit").then(jsonOf<{
      ok: boolean;
      iface_count: number;
      queue_count: number;
      verdict: { verdict: string; reason: string };
    }>),

  perfSamplingLimitsAuditStatus: () =>
    fetch("/api/perf-sampling-limits-audit").then(jsonOf<{
      ok: boolean;
      perf_cpu_time_max_percent: number | null;
      perf_event_max_sample_rate: number | null;
      perf_event_mlock_kb: number | null;
      perf_event_max_stack: number | null;
      verdict: { verdict: string; reason: string };
    }>),

  zswapDeepPoolAuditStatus: () =>
    fetch("/api/zswap-deep-pool-audit").then(jsonOf<{
      ok: boolean;
      enabled: string | null;
      exclusive_loads: string | null;
      shrinker_enabled: string | null;
      pool_limit_hit: number | null;
      reject_compress_poor: number | null;
      verdict: { verdict: string; reason: string };
    }>),

  // ── R&D #101 (UI sprint 92) ──
  kfenceRuntimeAuditStatus: () =>
    fetch("/api/kfence-runtime-audit").then(jsonOf<{
      ok: boolean;
      sample_interval: number | null;
      skip_covered_thresh: number | null;
      config_sample_interval: number | null;
      verdict: { verdict: string; reason: string };
    }>),

  netQdiscDefaultAuditStatus: () =>
    fetch("/api/net-qdisc-default-audit").then(jsonOf<{
      ok: boolean;
      default_qdisc: string | null;
      netdev_budget: number | null;
      netdev_max_backlog: number | null;
      netdev_budget_usecs: number | null;
      verdict: { verdict: string; reason: string };
    }>),

  fscacheCachefilesAuditStatus: () =>
    fetch("/api/fscache-cachefiles-audit").then(jsonOf<{
      ok: boolean;
      module_loaded: boolean;
      cache_count: number;
      nfs_fsc_mount_count: number;
      cachefiles_backend_present: boolean;
      verdict: { verdict: string; reason: string };
    }>),

  ksmAdvisorAuditStatus: () =>
    fetch("/api/ksm-advisor-audit").then(jsonOf<{
      ok: boolean;
      run: number | null;
      advisor_mode: string | null;
      smart_scan: number | null;
      advisor_target_scan_time: number | null;
      verdict: { verdict: string; reason: string };
    }>),

  // ── R&D #102 (UI sprint 93) ──
  intelUncoreFreqAuditStatus: () =>
    fetch("/api/intel-uncore-freq-audit").then(jsonOf<{
      ok: boolean;
      die_count: number;
      dies: Array<{
        name: string;
        min_freq_khz: number | null;
        max_freq_khz: number | null;
        current_freq_khz: number | null;
        initial_max_freq_khz: number | null;
      }>;
      verdict: { verdict: string; reason: string };
    }>),

  modprobeBlacklistDriftAuditStatus: () =>
    fetch("/api/modprobe-blacklist-drift-audit").then(jsonOf<{
      ok: boolean;
      conf_file_count: number;
      blacklist_count: number;
      install_noop_count: number;
      verdict: { verdict: string; reason: string };
    }>),

  moduleSigEnforceAuditStatus: () =>
    fetch("/api/module-sig-enforce-audit").then(jsonOf<{
      ok: boolean;
      sig_enforce: string | null;
      lockdown: string | null;
      secure_boot: boolean | null;
      verdict: { verdict: string; reason: string };
    }>),

  bpfJitHardenAuditStatus: () =>
    fetch("/api/bpf-jit-harden-audit").then(jsonOf<{
      ok: boolean;
      bpf_jit_harden: number | null;
      bpf_jit_kallsyms: number | null;
      bpf_jit_limit: number | null;
      unprivileged_bpf_disabled: number | null;
      verdict: { verdict: string; reason: string };
    }>),

  // ── R&D #103 (UI sprint 94) ──
  kernelOopsWarnCounterAuditStatus: () =>
    fetch("/api/kernel-oops-warn-counter-audit").then(jsonOf<{
      ok: boolean;
      oops_count: number | null;
      warn_count: number | null;
      panic_on_oops: number | null;
      verdict: { verdict: string; reason: string };
    }>),

  ephemeralPortRangeAuditStatus: () =>
    fetch("/api/ephemeral-port-range-audit").then(jsonOf<{
      ok: boolean;
      port_range_lo: number | null;
      port_range_hi: number | null;
      port_window: number | null;
      ip_unprivileged_port_start: number | null;
      reserved_ports: string;
      tcp_socket_count: number;
      verdict: { verdict: string; reason: string };
    }>),

  zramWritebackRecompressAuditStatus: () =>
    fetch("/api/zram-writeback-recompress-audit").then(jsonOf<{
      ok: boolean;
      zram_count: number;
      zrams: Array<{
        name: string;
        disksize: number | null;
        backing_dev: string | null;
        recomp_algorithm: string | null;
        compr_size: number | null;
        bd_writes: number | null;
      }>;
      verdict: { verdict: string; reason: string };
    }>),

  cgroupV2UclampAuditStatus: () =>
    fetch("/api/cgroup-v2-uclamp-audit").then(jsonOf<{
      ok: boolean;
      slice_count: number;
      slices: Array<{
        path: string;
        uclamp_min: number | null;
        uclamp_max: number | null;
        zswap_max: string | null;
        zswap_writeback: number | null;
      }>;
      verdict: { verdict: string; reason: string };
    }>),

  // ── R&D #104 (UI sprint 95) ──
  hwpDynamicBoostAuditStatus: () =>
    fetch("/api/hwp-dynamic-boost-audit").then(jsonOf<{
      ok: boolean;
      intel_pstate_status: string | null;
      hwp_dynamic_boost: number | null;
      epp: string | null;
      verdict: { verdict: string; reason: string };
    }>),

  hungTaskDriftAuditStatus: () =>
    fetch("/api/hung-task-drift-audit").then(jsonOf<{
      ok: boolean;
      hung_task_warnings: number | null;
      hung_task_check_interval_secs: number | null;
      hung_task_timeout_secs: number | null;
      verdict: { verdict: string; reason: string };
    }>),

  firmwareLoaderPolicyAuditStatus: () =>
    fetch("/api/firmware-loader-policy-audit").then(jsonOf<{
      ok: boolean;
      timeout_s: number | null;
      force_sysfs_fallback: number | null;
      ignore_sysfs_fallback: number | null;
      verdict: { verdict: string; reason: string };
    }>),

  imaMeasurementFreshnessAuditStatus: () =>
    fetch("/api/ima-measurement-freshness-audit").then(jsonOf<{
      ok: boolean;
      runtime_measurements_count: number | null;
      log_line_count: number;
      has_boot_aggregate: boolean;
      verdict: { verdict: string; reason: string };
    }>),

  // ── R&D #105 (UI sprint 96) ──
  imaDigestListsAuditStatus: () =>
    fetch("/api/ima-digest-lists-audit").then(jsonOf<{
      ok: boolean;
      digest_lists_loaded: number | null;
      digest_list_file_count: number;
      ima_appraise_active: boolean;
      evm_enforced: boolean;
      verdict: { verdict: string; reason: string };
    }>),

  drmGtLoadStatusAuditStatus: () =>
    fetch("/api/drm-gt-load-status-audit").then(jsonOf<{
      ok: boolean;
      card_count: number;
      intel_present: boolean;
      amd_present: boolean;
      guc_status: string | null;
      huc_status: string | null;
      amdgpu_recovery: number | null;
      verdict: { verdict: string; reason: string };
    }>),

  powerAsyncSuspendAuditStatus: () =>
    fetch("/api/power-async-suspend-audit").then(jsonOf<{
      ok: boolean;
      pm_async: number | null;
      pm_freeze_timeout_ms: number | null;
      sync_on_suspend: number | null;
      pm_print_times: number | null;
      verdict: { verdict: string; reason: string };
    }>),

  vmCompactionProactiveAuditStatus: () =>
    fetch("/api/vm-compaction-proactive-audit").then(jsonOf<{
      ok: boolean;
      compaction_proactiveness: number | null;
      compact_unevictable_allowed: number | null;
      percpu_pagelist_high_fraction: number | null;
      thp_enabled: string | null;
      verdict: { verdict: string; reason: string };
    }>),

  // ── R&D #106 (UI sprint 97) ──
  ioDelayTypeAuditStatus: () =>
    fetch("/api/io-delay-type-audit").then(jsonOf<{
      ok: boolean;
      io_delay_type: number | null;
      verdict: { verdict: string; reason: string };
    }>),

  printkPacingAuditStatus: () =>
    fetch("/api/printk-pacing-audit").then(jsonOf<{
      ok: boolean;
      printk_delay_ms: number | null;
      printk_devkmsg: string | null;
      printk_ratelimit_burst: number | null;
      verdict: { verdict: string; reason: string };
    }>),

  cacheL2ImbalanceAuditStatus: () =>
    fetch("/api/cache-l2-imbalance-audit").then(jsonOf<{
      ok: boolean;
      cpu_count: number;
      l2_sizes_kib: number[];
      verdict: { verdict: string; reason: string };
    }>),

  cpufreqSetspeedDriftAuditStatus: () =>
    fetch("/api/cpufreq-setspeed-drift-audit").then(jsonOf<{
      ok: boolean;
      cpu_count: number;
      cpufreq_present: boolean;
      verdict: { verdict: string; reason: string };
    }>),

  // ── R&D #107 (UI sprint 98) ──
  vmNumaPolicyAuditStatus: () =>
    fetch("/api/vm-numa-policy-audit").then(jsonOf<{
      ok: boolean;
      numa_stat: number | null;
      numa_zonelist_order: string | null;
      multi_node: boolean;
      verdict: { verdict: string; reason: string };
    }>),

  sysrqCadPoweroffAuditStatus: () =>
    fetch("/api/sysrq-cad-poweroff-audit").then(jsonOf<{
      ok: boolean;
      ctrl_alt_del: number | null;
      poweroff_cmd: string | null;
      verdict: { verdict: string; reason: string };
    }>),

  vmDirtyBytesDriftAuditStatus: () =>
    fetch("/api/vm-dirty-bytes-drift-audit").then(jsonOf<{
      ok: boolean;
      dirty_bytes: number | null;
      dirty_background_bytes: number | null;
      dirty_ratio: number | null;
      dirty_background_ratio: number | null;
      verdict: { verdict: string; reason: string };
    }>),

  numaBalancingScanTuningAuditStatus: () =>
    fetch("/api/numa-balancing-scan-tuning-audit").then(jsonOf<{
      ok: boolean;
      numa_balancing: number | null;
      scan_delay_ms: number | null;
      scan_period_min_ms: number | null;
      scan_period_max_ms: number | null;
      scan_size_mb: number | null;
      verdict: { verdict: string; reason: string };
    }>),

  // ── R&D #108 (UI sprint 99) ──
  nvidiaDrmParamsAuditStatus: () =>
    fetch("/api/nvidia-drm-params-audit").then(jsonOf<{
      ok: boolean;
      modeset: boolean | null;
      fbdev: boolean | null;
      verdict: { verdict: string; reason: string };
    }>),

  overlayModuleParamsAuditStatus: () =>
    fetch("/api/overlay-module-params-audit").then(jsonOf<{
      ok: boolean;
      metacopy: boolean | null;
      redirect_dir: boolean | null;
      xino_auto: boolean | null;
      verdict: { verdict: string; reason: string };
    }>),

  dmModParamsAuditStatus: () =>
    fetch("/api/dm-mod-params-audit").then(jsonOf<{
      ok: boolean;
      use_blk_mq: boolean | null;
      dm_numa_node: number | null;
      verdict: { verdict: string; reason: string };
    }>),

  cgroupTreeLimitsAuditStatus: () =>
    fetch("/api/cgroup-tree-limits-audit").then(jsonOf<{
      ok: boolean;
      max_depth: number | null;
      max_descendants: number | null;
      nr_descendants: number | null;
      verdict: { verdict: string; reason: string };
    }>),

  // ── R&D #109 (UI sprint 100) ──
  numaDemotionEnabledAuditStatus: () =>
    fetch("/api/numa-demotion-enabled-audit").then(jsonOf<{
      ok: boolean;
      demotion_enabled: boolean | null;
      multi_node: boolean;
      verdict: { verdict: string; reason: string };
    }>),

  acpiBootAssetsAuditStatus: () =>
    fetch("/api/acpi-boot-assets-audit").then(jsonOf<{
      ok: boolean;
      bgrt_present: boolean;
      bgrt_status: number | null;
      bgrt_type: number | null;
      fpdt_present: boolean;
      verdict: { verdict: string; reason: string };
    }>),

  acpiTablesInventoryAuditStatus: () =>
    fetch("/api/acpi-tables-inventory-audit").then(jsonOf<{
      ok: boolean;
      table_count: number;
      dsdt_size: number | null;
      ssdt_count: number;
      has_srat: boolean;
      has_hmat: boolean;
      multi_node: boolean;
      verdict: { verdict: string; reason: string };
    }>),

  pciNumaPinningAuditStatus: () =>
    fetch("/api/pci-numa-pinning-audit").then(jsonOf<{
      ok: boolean;
      device_count: number;
      multi_node: boolean;
      n_unpinned: number;
      verdict: { verdict: string; reason: string };
    }>),

  // ── R&D #110 (UI sprint 101) ──
  swapPriorityTieringAuditStatus: () =>
    fetch("/api/swap-priority-tiering-audit").then(jsonOf<{
      ok: boolean;
      swap_count: number;
      swaps: Array<{
        filename: string;
        type: string;
        priority: number;
      }>;
      verdict: { verdict: string; reason: string };
    }>),

  xfsLogActivityAuditStatus: () =>
    fetch("/api/xfs-log-activity-audit").then(jsonOf<{
      ok: boolean;
      filesystem_count: number;
      filesystems: Array<{
        dev: string;
        rw_reads: number | null;
        rw_writes: number | null;
      }>;
      verdict: { verdict: string; reason: string };
    }>),

  // ── Hardening #2 (UI sprint 103) — fleet cold-start profile ──
  // Hardening #14 — optional per-call budget overrides.
  collectionProfileAuditStatus: (
    opts?: { slow_module_ms?: number; slow_total_ms?: number },
  ) => {
    const q = new URLSearchParams();
    if (opts?.slow_module_ms != null && opts.slow_module_ms > 0) {
      q.set("slow_module_ms", String(opts.slow_module_ms));
    }
    if (opts?.slow_total_ms != null && opts.slow_total_ms > 0) {
      q.set("slow_total_ms", String(opts.slow_total_ms));
    }
    const qs = q.toString();
    return fetch("/api/collection-profile-audit" + (qs ? "?" + qs : "")).then(jsonOf<{
      ok: boolean;
      module_count: number;
      total_ms: number;
      optimizable_total_ms: number;
      expected_slow_total_ms: number;
      p50_ms: number | null;
      p95_ms: number | null;
      slowest_ms: number | null;
      top_slowest: Array<{
        name: string;
        elapsed_ms: number;
        expected_slow: boolean;
      }>;
      skipped_count: number;
      error_count: number;
      slow_module_ms_budget: number;
      slow_total_ms_budget: number;
      verdict: { verdict: string; reason: string; recommendation: string };
    }>);
  },
};
