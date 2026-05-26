"""HTTP API handlers — independent of the HTTP framework for easier testing.

Each function returns (status_code, body_dict) so the server.py wrapping is trivial.
The functions take a `ctx` dict containing :
  - config:  Config instance
  - profile: loaded GPU profile dict
  - sampler: MetricsSampler instance (for /api/state)

Optional modules are looked up via ctx for testability.
"""
from __future__ import annotations

import json
import os
import time
import subprocess
from typing import Any, Tuple

from .. import detect
from ..modules import power_limit as pl
from ..modules import clock_offsets as co
from ..modules import telegram_alerts as tg


Response = Tuple[int, dict]


# ─────────────────────────────── helpers ───────────────────────────────────


def _gpu_card_snapshot(gpu_index: int = 0) -> dict:
    """Fast nvidia-smi snapshot for ONE GPU (default: index 0).

    Includes: temp, fan, power, util, memory.used/total + (optional, depends
    on driver/GPU support) memory.temp (junction temp) and vbios_version.
    """
    try:
        r = subprocess.run(
            ["nvidia-smi",
             "-i", str(int(gpu_index)),
             "--query-gpu=name,temperature.gpu,fan.speed,power.draw,power.limit,"
             "utilization.gpu,memory.used,memory.total,temperature.memory,vbios_version,"
             "utilization.encoder,utilization.decoder,"
             "pcie.link.gen.current,pcie.link.gen.max,"
             "pcie.link.width.current,pcie.link.width.max",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=3,
        )
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        return {"alive": False, "name": "?", "index": gpu_index}

    if r.returncode != 0 or not r.stdout.strip():
        return {"alive": False, "name": "?", "index": gpu_index}

    parts = [p.strip() for p in r.stdout.strip().split(",")]
    if len(parts) < 8 or not parts[1].isdigit():
        return {"alive": False, "name": parts[0] if parts else "?", "index": gpu_index}

    def _intish(s, default=None):
        try: return int(s)
        except (ValueError, TypeError): return default

    mem_temp = _intish(parts[8]) if len(parts) > 8 else None
    vbios = parts[9] if len(parts) > 9 else None
    util_enc = _intish(parts[10]) if len(parts) > 10 else None
    util_dec = _intish(parts[11]) if len(parts) > 11 else None
    pcie_gen     = _intish(parts[12]) if len(parts) > 12 else None
    pcie_gen_max = _intish(parts[13]) if len(parts) > 13 else None
    pcie_width     = _intish(parts[14]) if len(parts) > 14 else None
    pcie_width_max = _intish(parts[15]) if len(parts) > 15 else None

    return {
        "alive": True,
        "index": gpu_index,
        "name": parts[0],
        "temp": int(parts[1]),
        "fan_pct": int(parts[2]),
        "power": float(parts[3]),
        "power_limit": float(parts[4]),
        "util_gpu": int(parts[5]),
        "mem_used_mib": int(parts[6]),
        "mem_total_mib": int(parts[7]),
        "mem_temp": mem_temp,
        "vbios_version": vbios,
        "util_enc": util_enc,            # NVENC %, None if unsupported
        "util_dec": util_dec,            # NVDEC %, None if unsupported
        "pcie_gen": pcie_gen,            # 1-5
        "pcie_gen_max": pcie_gen_max,
        "pcie_width": pcie_width,        # ×1, ×4, ×8, ×16
        "pcie_width_max": pcie_width_max,
    }


def _gpus_available() -> list:
    """Quick list of all GPUs detected on this host (name + bus_id + index).

    Returns [] if nvidia-smi is unavailable or no GPU.
    """
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=index,name,pci.bus_id,memory.total",
             "--format=csv,noheader"],
            capture_output=True, text=True, timeout=3,
        )
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        return []
    if r.returncode != 0 or not r.stdout.strip():
        return []
    gpus = []
    for line in r.stdout.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 4 or not parts[0].isdigit():
            continue
        # memory.total is "24576 MiB" — extract the int
        vram_str = parts[3].split()[0] if parts[3] else "0"
        gpus.append({
            "index": int(parts[0]),
            "name": parts[1],
            "bus_id": parts[2],
            "vram_mib": int(vram_str) if vram_str.isdigit() else None,
        })
    return gpus


# L125-L162 moved to api/state.py (cycle 10a)



def _per_fan_state(cfg) -> list:
    """Per-fan {idx, pct, rpm, target} via nvidia-settings, requires X access."""
    if not cfg.get_bool("MODULE_CLOCK_OFFSETS"):
        return []
    display = cfg.get("CLOCK_OFFSETS_DISPLAY", ":0")
    xauth = cfg.get("CLOCK_OFFSETS_XAUTHORITY") or None
    env = os.environ.copy()
    env["DISPLAY"] = display
    if xauth:
        env["XAUTHORITY"] = xauth
    queries = []
    for i in range(4):
        queries += ["-q", f"[fan:{i}]/GPUCurrentFanSpeed",
                    "-q", f"[fan:{i}]/GPUCurrentFanSpeedRPM",
                    "-q", f"[fan:{i}]/GPUTargetFanSpeed"]
    try:
        r = subprocess.run(["nvidia-settings"] + queries,
                           capture_output=True, text=True, timeout=4, env=env)
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        return []
    import re
    fans = {}
    for line in r.stdout.split("\n"):
        m = re.search(r"'(GPUCurrentFanSpeed|GPUCurrentFanSpeedRPM|GPUTargetFanSpeed)'"
                      r" \(desktop:\d+\[fan:(\d+)\]\): (\d+)", line)
        if not m:
            continue
        attr, idx, val = m.group(1), int(m.group(2)), int(m.group(3))
        fans.setdefault(idx, {"idx": idx})
        if attr == "GPUCurrentFanSpeed":
            fans[idx]["pct"] = val
        elif attr == "GPUCurrentFanSpeedRPM":
            fans[idx]["rpm"] = val
        elif attr == "GPUTargetFanSpeed":
            fans[idx]["target"] = val
    return [fans[i] for i in sorted(fans)]


def _tuning_state(cfg) -> dict:
    """Current clocks + offsets via nvidia-smi + nvidia-settings."""
    if not cfg.get_bool("MODULE_CLOCK_OFFSETS"):
        return {}
    clocks = {}
    try:
        r = subprocess.run(
            ["nvidia-smi",
             "--query-gpu=clocks.current.graphics,clocks.current.memory,"
             "clocks.max.graphics,clocks.max.memory,pstate",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=4,
        )
        parts = [p.strip() for p in r.stdout.strip().split(",")] if r.stdout.strip() else []
        if len(parts) >= 5 and parts[0].isdigit():
            clocks = {
                "gr_now": int(parts[0]), "mem_now": int(parts[1]),
                "gr_max": int(parts[2]), "mem_max": int(parts[3]),
                "pstate": parts[4],
            }
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        pass

    offsets = {}
    display = cfg.get("CLOCK_OFFSETS_DISPLAY", ":0")
    xauth = cfg.get("CLOCK_OFFSETS_XAUTHORITY") or None
    env = os.environ.copy()
    env["DISPLAY"] = display
    if xauth:
        env["XAUTHORITY"] = xauth
    try:
        r = subprocess.run(
            ["nvidia-settings",
             "-q", "[gpu:0]/GPUGraphicsClockOffset",
             "-q", "[gpu:0]/GPUMemoryTransferRateOffset",
             "-q", "[gpu:0]/GPUGraphicsClockOffsetAllPerformanceLevels",
             "-q", "[gpu:0]/GPUMemoryTransferRateOffsetAllPerformanceLevels"],
            capture_output=True, text=True, timeout=4, env=env,
        )
        import re
        for line in r.stdout.split("\n"):
            m = re.search(r"'(\w+)' \(desktop:\d+\[gpu:\d+\]\): (-?\d+)", line)
            if m:
                offsets[m.group(1)] = int(m.group(2))
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        pass

    return {"clocks": clocks, "offsets": offsets}


_TS_RE = __import__("re").compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")

# F5.5b — when the watchdog log lies (says down while NVML reports
# healthy), we synthesize a "recovered" moment to anchor held_for.
# Module-level state lives for the lifetime of the dashboard process.
# Reset to None when the log eventually catches up (recovered entry
# appears, so log_says_down flips false) OR when NVML itself flips
# unhealthy (real new drop).
_NVML_OVERRIDE_FIRST_SEEN = None  # type: ignore[var-annotated]


def _fmt_duration(seconds: float) -> str:
    """Format seconds as `XhYYm` (>=1h), `YYmZZs` (<1h, >=1m),
    or `ZZs` (<1m)."""
    s = int(max(0, seconds))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h}h{m:02d}m"
    if m:
        return f"{m}m{sec:02d}s"
    return f"{sec}s"


def _watchdog_state(cfg) -> dict:
    """Parse the OcuLink watchdog log to surface the two clocks
    users actually want:

      held_for      duration of the *last* up streak (alive→drop,
                    or alive→now if currently up)
      dropped_since duration since the most recent DROP, if the
                    link is currently down. None if currently up.

    Returns {available: False} if disabled or log missing.

    Bug fixed by F4-followup: previous version showed a single
    `last_uptime` clock computed as `now - last_recovered_ts`,
    which kept growing both when the link was alive AND when it
    was down — so a user staring at "72h08m" couldn't tell if the
    link had been stable for 72h or had been broken for 72h."""
    if not cfg.get_bool("MODULE_OCULINK_WATCHDOG"):
        return {"available": False}
    log = cfg.get("OCULINK_WATCHDOG_LOG",
                  os.path.expanduser("~/gpu-watchdog.log"))
    import re, datetime
    drops = 0
    last_up_ts = None
    last_drop_ts = None
    # Track ordered events to know the *current* state from the
    # most recent transition.
    last_event_kind = None  # 'up' | 'down' | None
    try:
        with open(log) as f:
            for line in f:
                line = line.rstrip()
                m_ts = _TS_RE.match(line)
                ts = None
                if m_ts:
                    try:
                        ts = datetime.datetime.strptime(
                            m_ts.group(1), "%Y-%m-%d %H:%M:%S")
                    except ValueError:
                        ts = None
                is_drop = ("DROP" in line.upper() or
                           "DÉCROCHAGE" in line)
                is_up = bool(re.search(
                    r"state.*up|GPU recovered|recover",
                    line, re.IGNORECASE))
                if is_drop:
                    drops += 1
                    if ts:
                        last_drop_ts = ts
                        last_event_kind = "down"
                if is_up:
                    if ts:
                        last_up_ts = ts
                        last_event_kind = "up"
    except FileNotFoundError:
        return {"available": False}

    now = datetime.datetime.now()
    held_for_s = None
    dropped_since_s = None
    current_state = "unknown"

    if last_event_kind == "down" and last_drop_ts:
        current_state = "down"
        dropped_since_s = (now - last_drop_ts).total_seconds()
        # Held-for = duration of the up streak that just ended,
        # i.e. last_drop_ts - last_up_ts (if we saw an up before).
        if last_up_ts and last_up_ts < last_drop_ts:
            held_for_s = (last_drop_ts - last_up_ts).total_seconds()
    elif last_event_kind == "up" and last_up_ts:
        current_state = "up"
        held_for_s = (now - last_up_ts).total_seconds()
        # dropped_since_s stays None — link is currently up
    elif last_up_ts:
        # No drops ever recorded but we have an up event.
        current_state = "up"
        held_for_s = (now - last_up_ts).total_seconds()

    # F5.5 — Cross-check with NVML LIVE state. The watchdog daemon
    # may be uninstalled / stopped / lagging behind; the log can
    # show "down" while the GPU is actually healthy. NVML is the
    # authoritative real-time signal — when it reports an active
    # device handle, the link is up regardless of what the log says.
    gpu_live_ok = None
    try:
        from gpu_dashboard.modules import _nvml
        if _nvml.init():
            count = _nvml.device_count()
            if count > 0:
                # Try to get a real handle on at least one device.
                # Handle acquisition fails (rc=999) when the GPU is
                # PCI-visible but driver state is stuck.
                sample = _nvml.sample_device(0)
                gpu_live_ok = sample is not None
            else:
                gpu_live_ok = False
    except Exception:  # noqa: BLE001 — best-effort cross-check
        gpu_live_ok = None
    log_says_down = (current_state == "down")
    global _NVML_OVERRIDE_FIRST_SEEN
    if gpu_live_ok is True and log_says_down:
        # Reality wins. Flip to "up" but remember the watchdog said
        # otherwise — surfaced via `state_source` so the UI can
        # show a subtle "live" hint instead of pretending nothing
        # happened.
        current_state = "up"
        # We don't know exactly when the GPU came back — anchor to
        # the first moment WE noticed (this dashboard process).
        # Subsequent polls keep using the same anchor so the
        # held-for clock actually ticks up.
        if _NVML_OVERRIDE_FIRST_SEEN is None:
            _NVML_OVERRIDE_FIRST_SEEN = now
        held_for_s = (now - _NVML_OVERRIDE_FIRST_SEEN).total_seconds()
    else:
        # If conditions don't hold (log caught up OR NVML went bad),
        # forget the anchor so the next override starts fresh.
        _NVML_OVERRIDE_FIRST_SEEN = None

    out = {"available": True,
           "drops": drops,
           "current_state": current_state,
           "held_for_s": held_for_s,
           "dropped_since_s": dropped_since_s,
           "held_for": (_fmt_duration(held_for_s)
                          if held_for_s is not None else None),
           "dropped_since": (_fmt_duration(dropped_since_s)
                             if dropped_since_s is not None else None),
           "gpu_live_ok": gpu_live_ok,
           "state_source": ("nvml_live" if (gpu_live_ok is True
                                              and log_says_down)
                              else "watchdog_log")}
    # Back-compat alias: old field name still surfaced so any
    # external consumer doesn't break, but it now reflects the
    # current state (down → time since drop; up → up duration).
    if current_state == "down" and dropped_since_s is not None:
        out["last_uptime"] = _fmt_duration(dropped_since_s)
    elif held_for_s is not None:
        out["last_uptime"] = _fmt_duration(held_for_s)
    else:
        out["last_uptime"] = "?"
    return out


def _services_state(cfg) -> dict:
    """Status of configured systemd services. Comma-separated list in config."""
    services_list = cfg.get("DASHBOARD_SERVICES", "")
    if not services_list.strip():
        return {}
    result = {}
    for name in [s.strip() for s in services_list.split(",") if s.strip()]:
        # Try user-level then system-level
        for args in (["--user", "is-active", name], ["is-active", name]):
            try:
                r = subprocess.run(["systemctl"] + args, capture_output=True, text=True, timeout=2)
                if r.stdout.strip():
                    result[name] = r.stdout.strip()
                    break
            except (FileNotFoundError, subprocess.SubprocessError, OSError):
                pass
        result.setdefault(name, "unknown")
    return result


def _fan_distribution(cfg) -> dict:
    """Parse the fan-curve log for target distribution. Returns {} if not configured."""
    log = cfg.get("FAN_CURVE_LOG", "")
    if not log:
        return {}
    log = os.path.expanduser(log)
    import re
    counts = {}
    try:
        with open(log) as f:
            for line in f:
                m = re.search(r"fan=(\d+)%", line)
                if m:
                    counts[m.group(1)] = counts.get(m.group(1), 0) + 1
    except FileNotFoundError:
        pass
    return counts


# Handlers from L328-L342 moved to api/llm.py (cycle 4)



# Handlers from L332-L350 moved to api/power.py (cycle 5)



# Handlers from L353-L373 moved to api/power.py (cycle 5)



# L340-L351 moved to api/alerts.py (cycle 8)



# L354-L379 moved to api/alerts.py (cycle 8)



# ───────────────────────── GET /api/history ───────────────────────────────


def _parse_gpu_index(params: dict) -> int:
    """Parse ?gpu_index= from query params, default 0 (back-compat)."""
    try:
        return int(params.get("gpu_index", 0))
    except (ValueError, TypeError):
        return 0


# L359-L376 moved to api/state.py (cycle 10a)



# L379-L396 moved to api/state.py (cycle 10a)



# L399-L421 moved to api/state.py (cycle 10a)



# L424-L437 moved to api/state.py (cycle 10a)



# Handlers from L523-L601 moved to api/llm.py (cycle 4)



# Handlers from L604-L672 moved to api/llm.py (cycle 4)



# L482-L528 moved to api/diagnostics.py (cycle 7b)



# Handlers from L531-L662 moved to api/cost.py (cycle 6)



# Handlers from L858-L890 moved to api/llm.py (cycle 4)



# Handlers from L893-L897 moved to api/llm.py (cycle 4)



# Handlers from L900-L944 moved to api/llm.py (cycle 4)



# Handlers from L677-L754 moved to api/cost.py (cycle 6)



# Handlers from L757-L820 moved to api/cost.py (cycle 6)



# Handlers from L823-L919 moved to api/cost.py (cycle 6)



# Handlers from L958-L1015 moved to api/power.py (cycle 5)



# Handlers from L1018-L1033 moved to api/power.py (cycle 5)



# L387-L455 moved to api/tuning.py or integrations.py (cycle 10b)



# Handlers from L1129-L1182 moved to api/power.py (cycle 5)



# ────────────────────────── GET /api/processes ────────────────────────────


def _read_cmdline(pid: int) -> Optional[str]:
    """Best-effort read of /proc/<pid>/cmdline, NUL-separated → space-separated.

    Returns None on permission denied, pid disappeared, or non-Linux.
    Truncated to 200 chars to keep the API payload bounded.
    """
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            raw = f.read()
        # cmdline is NUL-separated, trailing NUL
        decoded = raw.replace(b"\x00", b" ").decode("utf-8", errors="replace").strip()
        return decoded[:200] if decoded else None
    except (OSError, IOError):
        return None


# L582-L620 moved to api/ops.py (cycle 9)



# L702-L793 moved to api/diagnostics.py (cycle 7b)



# L489-L495 moved to api/tuning.py or integrations.py (cycle 10b)



# L498-L524 moved to api/tuning.py or integrations.py (cycle 10b)



# L527-L568 moved to api/tuning.py or integrations.py (cycle 10b)



# L571-L589 moved to api/tuning.py or integrations.py (cycle 10b)



# L592-L672 moved to api/tuning.py or integrations.py (cycle 10b)



# L813-L903 moved to api/ops.py (cycle 9)



# L780-L831 moved to api/state.py (cycle 10a)



# L834-L839 moved to api/ops.py (cycle 9)



# L842-L856 moved to api/ops.py (cycle 9)



# L985-L1056 moved to api/ops.py (cycle 9)



# L1059-L1177 moved to api/ops.py (cycle 9)



# L1180-L1216 moved to api/ops.py (cycle 9)



# L703-L715 moved to api/tuning.py or integrations.py (cycle 10b)



# L718-L736 moved to api/tuning.py or integrations.py (cycle 10b)



# L739-L748 moved to api/tuning.py or integrations.py (cycle 10b)



# L1301-L1316 moved to api/alerts.py (cycle 8)



# L755-L771 moved to api/tuning.py or integrations.py (cycle 10b)



# L1290-L1327 moved to api/ops.py (cycle 9)



# L1330-L1355 moved to api/ops.py (cycle 9)



# L1541-L1621 moved to api/diagnostics.py (cycle 7b)



# L786-L843 moved to api/tuning.py or integrations.py (cycle 10b)



# Handlers from L2775-L2790 moved to api/integrations.py (cycle 3)



# Handlers from L2793-L2814 moved to api/integrations.py (cycle 3)



# Handlers from L2817-L2823 moved to api/integrations.py (cycle 3)



# Handlers from L2826-L2829 moved to api/integrations.py (cycle 3)



# Handlers from L2832-L2846 moved to api/integrations.py (cycle 3)



# Handlers from L2849-L2852 moved to api/integrations.py (cycle 3)



# Handlers from L2855-L2859 moved to api/integrations.py (cycle 3)



# Handlers from L2862-L2959 moved to api/integrations.py (cycle 3)



# Handlers from L2962-L2966 moved to api/integrations.py (cycle 3)



# Handlers from L2969-L3001 moved to api/integrations.py (cycle 3)



# L886-L912 moved to api/tuning.py or integrations.py (cycle 10b)



# L915-L919 moved to api/tuning.py or integrations.py (cycle 10b)



# L922-L993 moved to api/tuning.py or integrations.py (cycle 10b)



# ─── R&D #10.6 — ANSI/tldr endpoint for CLI users ────────────────────────────
# L997-L1008 moved to api/tuning.py or integrations.py (cycle 10b)



# L1011-L1014 moved to api/tuning.py or integrations.py (cycle 10b)



# L1017-L1024 moved to api/tuning.py or integrations.py (cycle 10b)



# L1027-L1042 moved to api/tuning.py or integrations.py (cycle 10b)



# L1045-L1119 moved to api/tuning.py or integrations.py (cycle 10b)



# ─── R&D #9.3 + #9.6 handlers moved to api/auth.py (cycle 2) ──────────────


# Handlers from L3243-L3254 moved to api/integrations.py (cycle 3)



# Handlers from L3257-L3261 moved to api/integrations.py (cycle 3)



# Handlers from L3062-L3089 moved to api/llm.py (cycle 4)



# Handlers from L3092-L3102 moved to api/llm.py (cycle 4)



# Handlers from L3105-L3163 moved to api/llm.py (cycle 4)



# L1145-L1157 moved to api/tuning.py or integrations.py (cycle 10b)



# L2100-L2103 moved to api/alerts.py (cycle 8)



# L2320-L2395 moved to api/diagnostics.py (cycle 7b)



# L2398-L2400 moved to api/diagnostics.py (cycle 7b)



# L1172-L1191 moved to api/tuning.py or integrations.py (cycle 10b)



# ─── R&D #6.8 — cgroup per-process GPU power accounting ──────────────────────
# /proc helper L2137-L2154 moved to api/diagnostics.py (cycle 7b)



# L2446-L2462 moved to api/diagnostics.py (cycle 7b)



# L2465-L2574 moved to api/diagnostics.py (cycle 7b)



# ─── R&D #6.3 — System-context sidecar (CPU/iowait/swap/load) ────────────────
# Cached previous readings to compute deltas between calls.
# L2579-L2579 moved to api/diagnostics.py (cycle 7b)

# L2580-L2580 moved to api/diagnostics.py (cycle 7b)

# L2581-L2581 moved to api/diagnostics.py (cycle 7b)

# L2582-L2582 moved to api/diagnostics.py (cycle 7b)



# /proc helper L2177-L2187 moved to api/diagnostics.py (cycle 7b)



# /proc helper L2190-L2197 moved to api/diagnostics.py (cycle 7b)



# /proc helper L2200-L2211 moved to api/diagnostics.py (cycle 7b)



# /proc helper L2214-L2228 moved to api/diagnostics.py (cycle 7b)



# L2639-L2707 moved to api/diagnostics.py (cycle 7b)



# L2181-L2195 moved to api/alerts.py (cycle 8)



# L2198-L2223 moved to api/alerts.py (cycle 8)



# L2226-L2234 moved to api/alerts.py (cycle 8)



# Handlers from L2766-L2778 moved to api/diagnostics.py (cycle 7a)



# Handlers from L2781-L2855 moved to api/diagnostics.py (cycle 7a)



# ─── R&D #6.2 — Deadman heartbeat (inbound + outbound) ───────────────────────
# _heartbeats_path L2152-L2153 moved to api/alerts.py



# L2250-L2263 moved to api/alerts.py (cycle 8)



# L2266-L2270 moved to api/alerts.py (cycle 8)



# L2273-L2298 moved to api/alerts.py (cycle 8)



# L2301-L2312 moved to api/alerts.py (cycle 8)



# L2315-L2352 moved to api/alerts.py (cycle 8)



# L2884-L2899 moved to api/diagnostics.py (cycle 7b)



# L2902-L2913 moved to api/diagnostics.py (cycle 7b)



# L2916-L3015 moved to api/diagnostics.py (cycle 7b)



# L3018-L3020 moved to api/diagnostics.py (cycle 7a)



# Helper from L3023-L3046 moved to api/diagnostics.py (cycle 7a)



# Helper from L3049-L3050 moved to api/diagnostics.py (cycle 7a)



# Helper from L3053-L3054 moved to api/diagnostics.py (cycle 7a)



# Handlers from L3141-L3150 moved to api/diagnostics.py (cycle 7a)



# Handlers from L3153-L3195 moved to api/diagnostics.py (cycle 7a)



# Handlers from L3198-L3226 moved to api/diagnostics.py (cycle 7a)



# Handlers from L3229-L3286 moved to api/diagnostics.py (cycle 7a)



# Helper from L3073-L3076 moved to api/diagnostics.py (cycle 7a)



# Handlers from L3295-L3383 moved to api/diagnostics.py (cycle 7a)



# _IDLE_BASELINES L3059-L3078 moved to api/diagnostics.py (cycle 7a)



# Helper from L3105-L3111 moved to api/diagnostics.py (cycle 7a)



# Handlers from L3417-L3515 moved to api/diagnostics.py (cycle 7a)



# L3071-L3084 moved to api/diagnostics.py (cycle 7a)



# Handlers from L3534-L3561 moved to api/diagnostics.py (cycle 7a)



# L3078-L3169 moved to api/diagnostics.py (cycle 7b)

