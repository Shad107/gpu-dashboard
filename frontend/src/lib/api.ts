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

async function jsonOf<T>(r: Response): Promise<T> {
  if (!r.ok && r.status !== 400 && r.status !== 500) {
    throw new Error(`HTTP ${r.status}`);
  }
  return r.json();
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

  updateCheck: () =>
    fetch("/api/update/check").then(jsonOf<{
      ok: boolean;
      error?: string;
      current_sha?: string;
      remote_sha?: string | null;
      behind?: number | null;
      last_remote_msg?: string | null;
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
};
