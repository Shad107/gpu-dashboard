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
  | { alive: false; name: string }
  | {
      alive: true;
      name: string;
      temp: number;
      fan_pct: number;
      power: number;
      power_limit: number;
      util_gpu: number;
      mem_used_mib: number;
      mem_total_mib: number;
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

export type State = {
  gpu: Gpu;
  metrics: Sample[];
  profile: { model: string } | null;
  fans: Fan[];
  tuning: Tuning;
  watchdog: Watchdog;
  services: Record<string, string>;
  fan_dist: Record<string, number>;
  llm_model: string;
};

export type AlertsConfig = {
  enabled: boolean;
  token: string;
  chat_id: string;
  on_drop: boolean;
  on_recover: boolean;
};

async function jsonOf<T>(r: Response): Promise<T> {
  if (!r.ok && r.status !== 400 && r.status !== 500) {
    throw new Error(`HTTP ${r.status}`);
  }
  return r.json();
}

export const api = {
  state: () => fetch("/api/state", { cache: "no-store" }).then(jsonOf<State>),

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
};
