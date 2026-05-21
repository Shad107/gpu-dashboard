# Changelog

All notable changes to gpu-dashboard. Format inspired by [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased / 0.3.0-dev]

### Added — LLM-specific killer features (v0.3 originals)
- **🪙 LLM throughput card** (`GET /api/llm/stats`) — fetches llama-server `/metrics` and computes **tokens/Watt** efficiency using the avg power from the last hour. *No other GPU monitoring tool on either platform surfaces this metric.* Positions gpu-dashboard as the LLM-focused dashboard.
- **⚡ Electricity cost widget** (`GET /api/electricity`) — computes kWh + €/month from stored samples × configured `ELECTRICITY_PRICE_EUR_PER_KWH`. Dashboard card shows daily kWh + monthly cost extrapolation.
- **Auto profile-switch daemon** (`modules/auto_profile.py`) — classifies load (silent/sweet/boost) from sampler buffer; switches profile after `MIN_STABLE` seconds of sustained classification. Opt-in via `MODULE_AUTO_PROFILE=1`.
- **3 power profile presets** (`/api/power-profiles/apply/<name>`) — Silent/Sweet/Boost bundles of power-limit + GPU offset + memory offset. One-click switching from the Power tab. MSI Afterburner-inspired but Linux-native.

### Added — Competitive parity (after reviewing GWE, nvtop, gpustat, MSI Afterburner, HWiNFO)
- **`GET /api/prom`** — Prometheus 0.0.4 text-format exporter (gauges + counter). Plug directly into Grafana / VictoriaMetrics / Uptime Kuma. Labels include `{gpu="N",name="..."}`.
- **`GET /api/processes`** — per-process GPU VRAM via `nvidia-smi --query-compute-apps`. Returns `[{pid, name, vram_mib}]` sorted by VRAM desc. A new "Compute processes" card appears in the dashboard when ≥1 process is using the GPU.
- **Memory junction temperature** (`temperature.memory`) + **vBIOS version** (`vbios_version`) now part of `_gpu_card_snapshot`. GDDR junction temp shown as a sub-line on the GPU card (the actual undervolt limiter on RTX 3080/3090/4090). vBIOS surfaced in the About section.
- **`docs/COMPETITORS.md`** — 170-line side-by-side feature comparison with 11 Linux + Windows tools, ranked v0.3+ roadmap.

### Settings polish (Phase 2 finishing touches)
- **Restart server** button + `POST /api/restart` with `os.execv` in-place restart; frontend auto-reloads on reconnect.
- **Stop server** button + `POST /api/stop` for graceful sys.exit.
- **Redo Setup Wizard** button — replays the 5-step wizard on demand (dismissable mode).
- **Backup snapshot** button + `GET /api/snapshot` returning a tar.gz of config.env + secrets.env + metrics.db.
- **Update check** — `GET /api/update/check` runs `git fetch` + behind count; **Pull + Restart** button chains `git pull --ff-only` → `os.execv` (refuses on dirty tree).
- **Diagnostics tab** + `GET /api/logs?tail=N` with two backends (LOG_FILE or JOURNALCTL_UNIT) — copy/paste-able tail viewer for support.
- **About section** — version, uptime, Python, platform, paths, license, vBIOS, repo link.
- **`GET /api/health`** — JSON status for external monitoring (gpu_alive, sampler_running, storage_connected). Returns 503 + degraded if anything fails.
- **Multi-GPU detection** — `gpus_available[]` + `selected_gpu_index` in `/api/state`. Header shows badge "N GPUs detected" with tooltip on how to switch via `GPU_INDEX`.

### Settings — UX enhancements
- **Auto-refresh toggle** in History tab (30s).
- **Events overlay** on History chart — drops/recoveries/pl_change/offset_change/alert_sent as colored vertical markers with hover tooltips.
- **Profile override editor** + `POST /api/profile/save` — textarea-based JSON editor with schema validation; writes to `~/.config/gpu-dashboard/profile-overrides/<safe-model>.json`.

### Added — fan_curve module (Phase 2 closing)
- **`src/gpu_dashboard/modules/fan_curve.py`** — pure-function interpolate + validate + apply (via nvidia-settings) + FanCurveDaemon thread. Reuses the sampler's buffer to avoid extra nvidia-smi calls. Opt-in via `MODULE_FAN_CURVE=1`. Default curve: `[[30,0],[50,30],[65,50],[75,70],[85,100]]`. `GET /api/fan-curve` exposes the current curve + target % for the UI.

## [Unreleased / 0.2.0-dev]

### Added — Settings polish
- **About section** — 9th tab in the settings modal showing version, uptime, Python version, platform, config + storage paths, license, repo link.
- **Restart button** — `POST /api/restart` with `os.execv`-based in-place restart, frontend polls until reconnect then auto-reloads.
- **History tab in modal** — quick range selectors (1h / 6h / 24h / 7d / 30d) + metric selector (power / temp / fan / util) + Export CSV button.
- **Setup wizard** (5 steps) — first-run web onboarding with live hardware detection, module selection, copy-paste sudo commands with live recheck, final config, done.

### Added — Backend
- **`src/gpu_dashboard/storage.py`** — SQLite layer with WAL, thread-safe writes, schema versioning, resampling-aware queries.
- **`src/gpu_dashboard/retention.py`** — daemon thread that purges samples/events older than `STORAGE_RETENTION_DAYS` (default 30) and runs `VACUUM` weekly.
- **New endpoints**:
  - `GET  /api/history?from=&to=&step=` — resampled metrics time-series.
  - `GET  /api/events?from=&kind=`       — filtered events list.
  - `GET  /api/export?since=`            — raw CSV download (Content-Disposition attachment).
  - `GET  /api/setup/detect`             — full env + module recommendations.
  - `GET  /api/setup/recheck/<module>`   — re-run can_enable for one module.
  - `POST /api/setup/save`               — write config.env from wizard choices.
  - `POST /api/restart`                  — gracefully stop threads + os.execv.
  - `GET  /api/about`                    — version + paths + uptime.

### Added — Scripts
- `scripts/get.sh` (116 lines) — one-line bootstrap (`curl | bash`): clones + pip installs jsonschema + starts the server.
- `scripts/install-power-limit-wrapper.sh` — installs the sudoers wrapper at `/usr/local/bin/set-power-limit` + targeted sudoers rule. Supports `--check` (passive verify) and `--print` (audit).
- `scripts/install-coolbits-xorg.sh` — installs the Coolbits Xorg drop-in. Supports `--headless` for VM/eGPU.
- `scripts/install-oculink-watchdog.sh` — installs the OcuLink monitoring systemd service.

### Added — Frontend (Svelte 5 + Vite)
- Migrated the entire UI from vanilla HTML/CSS/JS to **Svelte 5 with runes** (`$state`, `$derived`, `$effect`).
- TypeScript-typed API client (`src/lib/api.ts`) and state stores (`src/lib/stores.svelte.ts`).
- Reactive i18n (EN + FR) with typed keys (`src/lib/i18n/`).
- Catmull-Rom smooth SVG line charts (cooling + power live, plus the new History chart).

### Changed
- Sampler now writes each sample to SQLite in addition to the in-memory rolling buffer.
- Sampler extended to capture `power_limit`, `utilization.gpu`, `memory.used` (was 5 fields, now 8).
- Server detects missing `config.env` on startup and exposes `setup_required: true` in `/api/state` so the frontend auto-opens the wizard.

### Tests
- 290+ pytest tests, ~0.7s suite, no external services touched.
- Added test suites: `test_storage.py`, `test_retention.py`, `test_metrics_storage.py`, `test_api_history.py`, `test_api_setup.py`, `test_api_about.py`, `test_scripts.py`.

## [0.1.0] — 2026-05-21

### Added
- Initial public release.
- Core modules: `perf` (perf-curve interpolation), `config` (.env layered loader), `profile` (GPU profile matching with JSON Schema validation), `detect` (OS / NVIDIA / Coolbits / OcuLink / virt probing).
- Opt-in modules: `power_limit`, `clock_offsets`, `telegram_alerts`.
- 5 GPU profiles + `_generic` fallback (RTX 3090, 3090 Ti, 4090, 5090).
- Interactive CLI installer (`install.sh` → `gpu_dashboard.install`).
- Vanilla HTML/CSS/JS dashboard (replaced by Svelte in 0.2.0-dev).
- Bilingual docs (`README.md` / `README.fr.md`, `CONTRIBUTING.md` / `CONTRIBUTING.fr.md`, `profiles/SCHEMA.md` / `SCHEMA.fr.md`).
- 186 pytest tests.
