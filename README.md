# gpu-dashboard

> Lightweight NVIDIA GPU monitoring + tuning dashboard for Linux.
> Built for LLM rigs and eGPU/OcuLink setups. Pure Python stdlib + jsonschema.

🇬🇧 English · [🇫🇷 Français](README.fr.md)

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)
![Status](https://img.shields.io/badge/status-alpha-orange.svg)

![Dashboard](docs/screenshot.png)

## What it does

A small HTTP dashboard you point your browser at (`http://localhost:9999`) that shows:

- **Live GPU state** — temperature, fan RPMs, power draw, clocks, VRAM
- **OcuLink/eGPU watchdog** — uptime tracking, Telegram alerts on link drops
- **Power-limit slider** — adjust GPU wattage cap from the UI (with live perf-% estimate per card)
- **Clock-offset sliders** — undervolt or overclock with safe/moderate/aggressive/danger zones
- **GPU profiles per card** — RTX 3090, 3090 Ti, 4090, 5090 bundled, generic fallback;
  community contributes more via PR

Built specifically for headless/SSH'd Linux boxes running LLMs locally (Qwen, Llama, etc.)
on consumer NVIDIA cards — including marginal setups (eGPU over OcuLink/Thunderbolt).

## Why?

Existing tools fall short:
- `nvtop` is great in a TTY but offers no control, no alerting
- GreenWithEnvy needs a GTK desktop session running on the NVIDIA itself
- `nvidia-smi -pl` lives in a terminal, no history, no slider
- Nothing tracks OcuLink eGPU link drops with phone alerts

This tries to be the missing middle: **web UI, controllable, scriptable, alertable**.

## Hardware support

| GPU | Profile | Status |
|---|---|---|
| RTX 3090 | `rtx-3090.json` | ✅ Calibrated on real hardware |
| RTX 3090 Ti | `rtx-3090-ti.json` | ⚠ Estimated perf curve |
| RTX 4090 | `rtx-4090.json` | ⚠ Estimated perf curve |
| RTX 5090 | `rtx-5090.json` | ⚠ Based on published benchmarks |
| Others (NVIDIA) | `_generic.json` (fallback) | Conservative limits |

> Got a card not in the list? See [`profiles/SCHEMA.md`](profiles/SCHEMA.md) and open a PR.

## Install (alpha, manual)

Requires Linux + NVIDIA driver + Python 3.9+.

```bash
git clone https://github.com/Shad107/gpu-dashboard.git
cd gpu-dashboard
./install.sh
```

The installer probes your environment (OS, NVIDIA driver, X server, Coolbits, sudoers)
and only proposes modules that can actually work on your machine. **No silent sudo,
no auto-install of packages.** It tells you what it would do, you confirm.

Use `./install.sh --detect-only` to just see the report without installing anything.

## Architecture

```
gpu-dashboard/
├── src/gpu_dashboard/          # Python source
│   ├── perf.py                  # perf-curve interpolation
│   ├── config.py                # layered .env config loader
│   ├── profile.py               # GPU profile load + match + JSON Schema validation
│   ├── detect.py                # env probing (OS, NVIDIA, Coolbits, OcuLink…)
│   ├── install.py               # interactive installer logic
│   └── modules/
│       ├── power_limit.py       # sudoers wrapper for nvidia-smi -pl
│       ├── clock_offsets.py     # nvidia-settings, no sudo via Coolbits
│       └── telegram_alerts.py   # urllib stdlib, no `requests` dep
├── profiles/                    # JSON profiles + JSON Schema
└── tests/                       # pytest, 178 tests, no external services
```

## Optional modules

Each feature is opt-in — `install.sh` only proposes what your env supports.

| Module | Requirement | What it adds |
|---|---|---|
| **power_limit** | sudoers wrapper installed | UI slider 100-350W (or per-card max), live perf-% estimate |
| **clock_offsets** | Coolbits ≥ 8 in xorg.conf | Sliders for GPU/mem clock offsets with risk zones |
| **telegram_alerts** | bot token + chat ID | Push notifications on events |
| **oculink_watchdog** *(v0.2)* | eGPU detected (PCIe x4 link) | Tracks link uptime, alerts on drops |
| **fan_curve** *(v0.2)* | Headless Xorg :0 on NVIDIA | Custom fan curve replacing the stock NVIDIA one |

## Contributing

Profiles for new cards are **the highest-value contribution**. See
[`profiles/SCHEMA.md`](profiles/SCHEMA.md). Code contributions welcome too — see
[`CONTRIBUTING.md`](CONTRIBUTING.md).

## License

MIT. See [`LICENSE`](LICENSE).

## Roadmap

See [`docs/PLAN.md`](docs/PLAN.md) for the detailed plan, sequencing, and milestones.
