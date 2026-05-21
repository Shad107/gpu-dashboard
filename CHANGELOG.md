# Changelog

All notable changes to gpu-dashboard. Format inspired by [Keep a Changelog](https://keepachangelog.com/).

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
