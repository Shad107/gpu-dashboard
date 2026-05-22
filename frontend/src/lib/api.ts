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
};
