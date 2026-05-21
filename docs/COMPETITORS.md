# Competitor Analysis — Linux & Windows GPU tools

Comparison of feature sets across the major GPU monitoring & tuning tools, and
what gpu-dashboard could pick up from each. Reviewed 2026-05.

## Linux side

### GreenWithEnvy (GWE) — GTK desktop app
- ⭐ Manual overclock + fan curve editor (drag-and-drop points on a chart)
- ⭐ Power-limit slider with persistence
- Historical chart (last 60s in-RAM only — no SQLite)
- Single-host, GTK4 native app, requires desktop session
- ❌ No web UI (can't access remotely)
- ❌ No structured API
- ❌ No multi-host or telemetry
- ❌ No LLM-specific features (no power vs perf curve, no model awareness)

**What we could pick up:**
- The drag-and-drop fan curve editor (we have the textarea v1, this would be the v2)
- Their idle / load preset profiles (1-click switch between "silent" / "performance")

### nvtop — terminal TUI
- ⭐ Real-time process list with per-process GPU memory + util
- ⭐ Multi-GPU side-by-side display
- Encoder/decoder utilization (NVENC/NVDEC) — useful for streaming/transcoding
- ❌ Read-only (no tuning, no fan control)
- ❌ TTY only, no remote access
- ❌ No history (it's a live TUI)

**What we could pick up:**
- **Process list with per-process VRAM** — *high value*, shows which LLM process owns the memory
- NVENC/NVDEC utilization columns (niche but real for streaming setups)

### gpustat — minimalist CLI
- Single-line per GPU output, no UI
- Good for `watch gpustat`, cron pipes, prometheus exporters
- ❌ No tuning, no history

**What we could pick up:**
- `/api/prom` Prometheus exporter format (text/plain content-type, one line per metric) — *easy*, plugs into Grafana

### mission-center — modern GTK desktop app
- ⭐ Task Manager-style UX (CPU + GPU + RAM + Disk + Network in one app)
- Real-time charts
- ❌ No tuning at all
- ❌ Desktop-only

**What we could pick up:**
- Their layout for cards (more compact + denser info)

### btop / bashtop — system monitor TUI
- ⭐ Beautiful TUI with per-process resources
- GPU support recent (limited to util %, temp, power)
- ❌ TTY only

**What we could pick up:**
- Their color gradient for charts (subtle, professional)

### Direct nvidia-smi
- Industry standard CLI
- Verbose output for one-shot inspection
- ❌ No history, no UI, no tuning UI

---

## Windows side (for reference — not target, but instructive)

### MSI Afterburner — the gold standard
- ⭐ Per-card OC profile (3 slots, hotkeys)
- ⭐ On-screen display (OSD) overlay during games
- ⭐ Fan curve editor with smooth Bezier
- ⭐ Voltage curve editing (advanced undervolt — "VF curve")
- ⭐ Hardware sensor logging to CSV
- Built-in benchmark + screenshot
- ❌ Windows only
- ❌ Closed source

**What we could pick up:**
- **3 OC slots with hotkey switching** — for LLM rigs that swap between inference + training profiles
- Voltage curve editing (advanced — but the LLM crowd LOVES undervolting)

### EVGA Precision X1 — discontinued but iconic
- Similar to Afterburner, prettier UI
- Built-in scanner / auto-OC ("OC Scanner")

**What we could pick up:**
- "Auto-undervolt" feature: gradually lower power-limit, run a quick stress, find max stable -W

### NVIDIA Inspector — power-user tool
- ⭐ P-state by P-state clock control (granular)
- DLSS / G-Sync / Vsync tweaks
- ❌ Read most settings, edit some, but no GUI for everyday use

**What we could pick up:**
- Per-pstate clock display (we already show current pstate, but not per-pstate offsets)

### HWiNFO64 — sensor monster
- ⭐ 200+ sensors monitored (every chip on every component)
- CSV/SQLite logging
- Prometheus exporter
- ❌ Read-only

**What we could pick up:**
- Already have Prometheus exporter in plan (gpustat-inspired)
- Per-component temperature sensors (memory junction, hotspot) — `nvidia-smi --query-gpu=temperature.memory` exists

### GPU-Z — info dump
- BIOS info, sensor monitoring
- ❌ No tuning

**What we could pick up:**
- BIOS / vBIOS version display in About section

---

## Proposed roadmap from competitor gaps

Prioritized by value × effort × fit-with-our-niche (LLM rigs on Linux):

### 🟢 High value, low effort (next batch — ~1-2h each)

1. **`/api/prom` Prometheus exporter endpoint** — text/plain output of metrics.
   *Why:* one-line plug into Grafana / Uptime Kuma / VictoriaMetrics. Standard.
2. **Process list (`/api/processes`)** — wrap `nvidia-smi --query-compute-apps=pid,process_name,used_memory`.
   *Why:* see which LLM process owns the VRAM. Direct ask from anyone running multiple models.
3. **Memory junction temp (memory hotspot)** — `nvidia-smi --query-gpu=temperature.memory`.
   *Why:* trivial to add, GDDR6X gets HOT on 3090s — VERY useful for undervolters.
4. **vBIOS version in About** — `nvidia-smi --query-gpu=vbios_version`.
   *Why:* 1 line of code, instant pro polish.

### 🟡 Medium value, medium effort (~3-5h each)

5. **Drag-and-drop fan curve editor** — SVG with draggable points + live preview.
   *Why:* the textarea works but isn't the "premium" feel. Direct copy of GWE.
6. **3 power-limit / OC profiles with hotkey** — "Silent / Sweet-spot / Boost".
   *Why:* LLM rig owners switch between inference (250W) and training/finetune (350W). Killer feature.
7. **Auto-undervolt stress sweep** — gradually lower power-limit while running a workload, find min-stable.
   *Why:* what every LLM hobbyist wants but never has the patience to script.
8. **Per-process VRAM tracker over time** — store the process list at each sample.
   *Why:* answers "which model run made my VRAM spike at 3am?".

### 🔴 Cool but lower priority (v0.4+)

9. **OSD overlay** for fullscreen games / vulkan apps.
   *Why:* niche on Linux (gamescope/MangoHud already do it).
10. **Voltage / VF curve editing** — NVIDIA exposes some via nvidia-smi `--lock-gpu-clocks` and `--reset-gpu-clocks`.
    *Why:* advanced, easy to brick a card. Save for v1.0.
11. **Per-pstate offsets display** — we show current pstate, could show offsets per pstate.

### Won't do (out of scope)

- DLSS/G-Sync/Vsync tweaks (gaming, not LLM)
- BIOS flashing
- VR-specific monitoring
- Multi-OS support (we're Linux-only by design)

---

## Decision matrix — what to ship next

If we take **#1 + #2 + #3 + #4** from "high value, low effort" as a batch in v0.3,
that's about 4-6 hours total and unlocks:

- Prometheus + Grafana integration (the homelab crowd loves this)
- Per-process VRAM tracker (LLM-specific, no other tool does this well on Linux)
- Memory junction temp (essential for undervolters)
- vBIOS version (polish)

This positions gpu-dashboard as the **LLM/eGPU-focused** Linux dashboard, with
features that GWE, nvtop, and gpustat collectively don't have in one place.
