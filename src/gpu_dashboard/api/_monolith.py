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


# ─────────────────────────── GET /api/state ────────────────────────────────


def handle_state(ctx: dict, params: Optional[dict] = None) -> Response:
    """Aggregates everything the frontend needs in a single payload.

    All optional fields default to None/empty so the UI can render gracefully
    when a module is disabled.

    Query params : gpu_index (default = config GPU_INDEX, then 0) selects which
    GPU's live snapshot to return (multi-GPU rigs).
    """
    cfg = ctx["config"]
    # Picker preference (URL param) wins over config default
    if params and "gpu_index" in params:
        try:
            gpu_index = int(params["gpu_index"])
        except (ValueError, TypeError):
            gpu_index = cfg.get_int("GPU_INDEX", default=0)
    else:
        gpu_index = cfg.get_int("GPU_INDEX", default=0)
    _, procs = handle_processes(ctx)
    body = {
        "gpu": _gpu_card_snapshot(gpu_index=gpu_index),
        "gpus_available": _gpus_available(),
        "selected_gpu_index": gpu_index,
        "metrics": ctx["sampler"].snapshot() if ctx.get("sampler") else [],
        "profile": ctx.get("profile"),
        "fans": _per_fan_state(cfg),
        "tuning": _tuning_state(cfg),
        "watchdog": _watchdog_state(cfg),
        "services": _services_state(cfg),
        "fan_dist": _fan_distribution(cfg),
        "llm_model": _llm_model_served(cfg),
        "processes": procs.get("processes", []) if procs.get("available") else [],
        "setup_required": bool(ctx.get("setup_required", False)),
    }
    return 200, body


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


def _watchdog_state(cfg) -> dict:
    """Parse the OcuLink watchdog log for uptime + drop count. Returns {available: False} if disabled."""
    if not cfg.get_bool("MODULE_OCULINK_WATCHDOG"):
        return {"available": False}
    log = cfg.get("OCULINK_WATCHDOG_LOG", os.path.expanduser("~/gpu-watchdog.log"))
    import re, datetime
    drops = 0
    last_heartbeat = ""
    last_up_ts = None
    try:
        with open(log) as f:
            for line in f:
                line = line.rstrip()
                if "DROP" in line.upper() or "DÉCROCHAGE" in line:
                    drops += 1
                if "heartbeat" in line:
                    last_heartbeat = line
                m_up = re.match(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*(?:state.*up|GPU recovered|recover)", line, re.IGNORECASE)
                if m_up:
                    try:
                        last_up_ts = datetime.datetime.strptime(m_up.group(1), "%Y-%m-%d %H:%M:%S")
                    except ValueError:
                        pass
    except FileNotFoundError:
        return {"available": False}

    if last_up_ts:
        delta = (datetime.datetime.now() - last_up_ts).total_seconds()
        h, mn = int(delta // 3600), int((delta % 3600) // 60)
        uptime = f"{h}h{mn:02d}m"
    else:
        m = re.search(r"depuis (\d+h\d+m)|since (\d+h\d+m)", last_heartbeat)
        uptime = (m.group(1) or m.group(2)) if m else "?"
    return {"available": True, "drops": drops, "last_uptime": uptime}


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


def handle_history(ctx: dict, params: dict) -> Response:
    """Renvoie les samples historiques depuis SQLite.

    Query params : from (epoch, default 0), to (epoch, default now),
                   step (seconds, optional), gpu_index (default 0)
    """
    storage = ctx.get("storage")
    if storage is None:
        return 503, {"ok": False, "error": "storage not available"}
    try:
        from_ts = int(params.get("from", 0))
        to_ts = int(params["to"]) if params.get("to") else None
        step = int(params["step"]) if params.get("step") else None
    except (ValueError, TypeError):
        return 400, {"ok": False, "error": "from/to/step must be integers"}
    gpu = _parse_gpu_index(params)
    samples = storage.get_samples(from_ts=from_ts, to_ts=to_ts, step=step, gpu_index=gpu)
    return 200, {"ok": True, "samples": samples, "gpu_index": gpu}


# ───────────────────────── GET /api/events ────────────────────────────────


def handle_events(ctx: dict, params: dict) -> Response:
    """Renvoie les événements horodatés.

    Query params : from (epoch, default 0), kind (optional filter)
    """
    storage = ctx.get("storage")
    if storage is None:
        return 503, {"ok": False, "error": "storage not available"}
    try:
        from_ts = int(params.get("from", 0))
    except (ValueError, TypeError):
        return 400, {"ok": False, "error": "from must be integer"}
    kind = params.get("kind") or None
    events = storage.get_events(from_ts=from_ts, kind=kind)
    return 200, {"ok": True, "events": events}


# ───────────────────────── GET /api/export ────────────────────────────────


def handle_export(ctx: dict, params: dict):
    """Exporte les samples au format CSV.

    Query params : format (csv only), since (epoch, default 0)
    Retourne (code, body) où body est :
      - dict JSON si erreur
      - string CSV brut si succès
    Le wrapper HTTP server.py sait gérer les 2.
    """
    storage = ctx.get("storage")
    if storage is None:
        return 503, {"ok": False, "error": "storage not available"}
    fmt = (params.get("format") or "csv").lower()
    if fmt != "csv":
        return 400, {"ok": False, "error": "only format=csv supported"}
    try:
        since = int(params.get("since", 0))
    except (ValueError, TypeError):
        return 400, {"ok": False, "error": "since must be integer"}
    return 200, storage.export_csv(from_ts=since)


def handle_export_year(ctx: dict, params: dict):
    """One-click year-to-date CSV export. Equivalent to
    /api/export?since=<Jan-1-of-current-year>.

    Convenient for January reports / spreadsheets.
    """
    storage = ctx.get("storage")
    if storage is None:
        return 503, {"ok": False, "error": "storage not available"}
    import time as _time
    import datetime as _dt
    year_start = int(_dt.datetime.fromtimestamp(_time.time()).replace(
        month=1, day=1, hour=0, minute=0, second=0, microsecond=0).timestamp())
    return 200, storage.export_csv(from_ts=year_start)


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



# ────────────────────────── /api/power-profiles ───────────────────────────


# Each profile bundles power-limit + GPU offset + memory offset.
# Handlers from L1040-L1040 moved to api/power.py (cycle 5)



# Handlers from L1043-L1057 moved to api/power.py (cycle 5)



# Handlers from L1060-L1072 moved to api/power.py (cycle 5)



def handle_benchmark_run(ctx: dict, payload) -> Response:
    """Run an A/B profile comparison synchronously (R&D #4, cycle 123).

    payload : {profile_a, profile_b, duration_s} — duration capped at 300s
              to avoid wedging the server.

    Returns {segment_a, segment_b, comparison} where comparison is the output
    of benchmark.compare(seg_a, seg_b).
    """
    if not isinstance(payload, dict):
        return 400, {"ok": False, "error": "payload must be an object"}
    a = str(payload.get("profile_a") or "").lower()
    b = str(payload.get("profile_b") or "").lower()
    try:
        duration = int(payload.get("duration_s") or 60)
    except (ValueError, TypeError):
        return 400, {"ok": False, "error": "duration_s must be an integer"}
    if duration < 5 or duration > 300:
        return 400, {"ok": False, "error": "duration_s must be in [5, 300]"}
    valid = {"silent", "sweet", "boost"}
    if a not in valid or b not in valid:
        return 400, {"ok": False,
                     "error": f"profiles must be in {sorted(valid)}"}
    if a == b:
        return 400, {"ok": False, "error": "profile_a and profile_b must differ"}

    sampler = ctx.get("sampler")
    if sampler is None:
        return 503, {"ok": False, "error": "sampler not available"}

    cfg = ctx.get("config")
    price = 0.25
    if cfg is not None:
        try:
            price = float(cfg.get("ELECTRICITY_PRICE_EUR_PER_KWH", default="0.25"))
        except (ValueError, TypeError):
            price = 0.25

    def _apply(profile_name: str) -> None:
        from .power import handle_power_profile_apply as _hppa  # cycle 5 late import
        _hppa(ctx, profile_name)

    from ..modules.benchmark import run_segment, compare
    seg_a = run_segment(duration, a, _apply, sampler, price_per_kwh=price)
    seg_b = run_segment(duration, b, _apply, sampler, price_per_kwh=price)
    cmp = compare(seg_a, seg_b)

    return 200, {
        "ok": True,
        "segment_a": seg_a,
        "segment_b": seg_b,
        "comparison": cmp,
    }


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



# ────────────────────────── /api/app-triggers ─────────────────────────────


def handle_app_triggers_get(ctx: dict) -> Response:
    """Return the user-configured per-app profile triggers map."""
    from ..modules import app_triggers as _at
    return 200, {"ok": True, "triggers": _at.load_triggers()}


def handle_app_triggers_post(ctx: dict, payload) -> Response:
    """Persist {app: profile} mapping. Validates each profile name."""
    from ..modules import app_triggers as _at
    if not isinstance(payload, dict):
        return 400, {"ok": False, "error": "payload must be an object"}
    triggers = payload.get("triggers")
    if not isinstance(triggers, dict):
        return 400, {"ok": False, "error": "triggers must be an object"}
    valid = {"silent", "sweet", "boost"}
    cleaned: dict = {}
    for k, v in triggers.items():
        if not isinstance(k, str) or not isinstance(v, str):
            continue
        k = k.strip()
        if not k:
            continue
        if v not in valid:
            return 400, {
                "ok": False,
                "error": f"profile '{v}' invalid (must be one of {sorted(valid)})",
            }
        cleaned[k] = v
    try:
        _at.save_triggers(cleaned)
    except OSError as e:
        return 500, {"ok": False, "error": f"save failed: {e}"}
    return 200, {"ok": True, "triggers": cleaned}


# ────────────────────────── /api/profile/save ─────────────────────────────


def handle_profile_save(ctx: dict, payload: dict) -> Response:
    """Save a user override for a GPU profile.

    Validates the payload against profiles/schema.json then writes it to
    `<overrides_dir>/<safe_model_name>.json`. The next reload picks it up
    automatically (via `profile.get_profile_for_gpu`'s override-dir param).
    """
    import re as _re
    from ..profile import load_schema, validate_profile

    if not isinstance(payload, dict):
        return 400, {"ok": False, "error": "payload must be an object"}

    profiles_dir = ctx.get("profiles_dir") or "profiles"
    schema = load_schema(profiles_dir)
    if schema is None:
        return 500, {"ok": False, "error": "schema not found"}
    try:
        validate_profile(payload, schema)
    except ValueError as e:
        return 400, {"ok": False, "error": str(e)}

    overrides_dir = ctx.get("overrides_dir") or os.path.expanduser(
        "~/.config/gpu-dashboard/profile-overrides"
    )
    os.makedirs(overrides_dir, exist_ok=True)

    # Safe filename : keep only letters/digits/dash/underscore, lowercase
    model = str(payload.get("model", "override"))
    safe = _re.sub(r"[^A-Za-z0-9_-]+", "-", model).strip("-").lower() or "override"
    path = os.path.join(overrides_dir, f"{safe}.json")
    try:
        import json as _json
        with open(path, "w", encoding="utf-8") as f:
            _json.dump(payload, f, indent=2)
    except OSError as e:
        return 500, {"ok": False, "error": f"write failed: {e}"}

    return 200, {"ok": True, "path": path, "model": model}


# ────────────────────────── /api/fan-curve ────────────────────────────────


def handle_fan_curve_get(ctx: dict) -> Response:
    """Return the active fan curve + current target % + daemon status + hysteresis."""
    from ..modules import fan_curve as _fc
    profile = ctx.get("profile") or {}
    curve = _fc.pick_curve(profile)
    daemon = ctx.get("fan_curve_daemon")
    cfg = ctx["config"]
    return 200, {
        "enabled": cfg.get_bool("MODULE_FAN_CURVE"),
        "running": daemon is not None and getattr(daemon, "_thread", None) is not None,
        "curve": curve,
        "current_target_pct": getattr(daemon, "_last_pct", None) if daemon else None,
        # R&D #4.4 — hysteresis settings (defaults match daemon defaults)
        "hysteresis_c": float(cfg.get("FAN_CURVE_HYSTERESIS_C", "3") or "3"),
        "hysteresis_s": float(cfg.get("FAN_CURVE_HYSTERESIS_S", "15") or "15"),
    }


def handle_fan_curve_post(ctx: dict, payload: dict) -> Response:
    """Save a user-edited fan curve to ~/.config/gpu-dashboard/fan_curve.json.

    Apply the new curve to the running daemon IMMEDIATELY (no waiting for
    the next tick) — user feedback : 'sauvegarde doit-être appliqué tout
    de suite'.
    """
    from ..modules import fan_curve as _fc
    curve = payload.get("curve") if isinstance(payload, dict) else None
    ok, err = _fc.validate_user_curve(curve)
    if not ok:
        return 400, {"ok": False, "error": err}

    # R&D #4.4 — optional hysteresis params (bounded sanity check)
    hys_c_raw = payload.get("hysteresis_c") if isinstance(payload, dict) else None
    hys_s_raw = payload.get("hysteresis_s") if isinstance(payload, dict) else None
    hysteresis_c = None
    hysteresis_s = None
    if hys_c_raw is not None:
        try:
            v = float(hys_c_raw)
            if 0 <= v <= 20:
                hysteresis_c = v
            else:
                return 400, {"ok": False, "error": "hysteresis_c out of range [0,20]"}
        except (TypeError, ValueError):
            return 400, {"ok": False, "error": "hysteresis_c must be a number"}
    if hys_s_raw is not None:
        try:
            v = float(hys_s_raw)
            if 0 <= v <= 600:
                hysteresis_s = v
            else:
                return 400, {"ok": False, "error": "hysteresis_s out of range [0,600]"}
        except (TypeError, ValueError):
            return 400, {"ok": False, "error": "hysteresis_s must be a number"}

    path = os.path.expanduser("~/.config/gpu-dashboard/fan_curve.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload_save: dict = {"curve": curve}
    if hysteresis_c is not None:
        payload_save["hysteresis_c"] = hysteresis_c
    if hysteresis_s is not None:
        payload_save["hysteresis_s"] = hysteresis_s
    with open(path, "w") as f:
        json.dump(payload_save, f, indent=2)

    # Apply the new curve to the running daemon NOW + force an immediate
    # tick so the fan speed updates within milliseconds of Save being clicked.
    daemon = ctx.get("fan_curve_daemon")
    applied_now = False
    current_target_pct = None
    if daemon is not None:
        try:
            daemon.update_curve(curve)
            # R&D #4.4 — apply hysteresis settings live to the running daemon
            if hysteresis_c is not None:
                daemon._hysteresis_c = hysteresis_c
            if hysteresis_s is not None:
                daemon._hysteresis_s = hysteresis_s
            # Force an immediate evaluation : read latest temp, interpolate,
            # apply via nvidia-settings.
            temp = daemon._read_temp() if hasattr(daemon, "_read_temp") else None
            if temp is not None:
                pct = _fc.interpolate(curve, temp)
                _fc.apply_fan_speed(pct, daemon._display, daemon._xauth)
                daemon._last_pct = pct
                current_target_pct = pct
                applied_now = True
        except Exception as e:
            return 200, {
                "ok": True, "path": path, "curve": curve,
                "applied_now": False,
                "warning": f"saved, but immediate apply failed: {e}",
            }

    return 200, {
        "ok": True, "path": path, "curve": curve,
        "applied_now": applied_now,
        "current_target_pct": current_target_pct,
    }


# L813-L903 moved to api/ops.py (cycle 9)



# ────────────────────────── GET /api/about ────────────────────────────────


def handle_lifetime_stats(ctx: dict, params: Optional[dict] = None) -> Response:
    """Lifetime extrema per GPU : peak temp/power/fan + lowest idle power.

    All computed on-the-fly with SQL aggregates — no schema bump, no
    background job. Cheap enough that we can re-run on every poll.

    Idle = util_gpu < 5%. Returns None for any field when no samples match.
    """
    storage = ctx.get("storage")
    if storage is None:
        return 503, {"ok": False, "error": "storage not available"}
    gpu = _parse_gpu_index(params or {})

    try:
        # Peak extrema (single query) + first/last sample timestamps
        cur = storage._conn.execute(
            "SELECT MAX(temp) AS peak_temp, MAX(power) AS peak_power, "
            "MAX(fan_pct) AS peak_fan_pct, MAX(fan0_rpm) AS peak_fan_rpm, "
            "MIN(ts) AS first_ts, MAX(ts) AS last_ts, COUNT(*) AS n "
            "FROM samples WHERE gpu_index = ?",
            (gpu,),
        )
        peaks = cur.fetchone()

        # Lowest idle power (util < 5% AND power > 0)
        cur = storage._conn.execute(
            "SELECT MIN(power) AS lowest_idle_w "
            "FROM samples WHERE gpu_index = ? AND util_gpu < 5 AND power > 0",
            (gpu,),
        )
        idle = cur.fetchone()
    except Exception as e:
        return 500, {"ok": False, "error": f"query failed: {e}"}

    def _val(row, key):
        return row[key] if row is not None and row[key] is not None else None

    return 200, {
        "ok": True,
        "gpu_index": gpu,
        "samples_count": _val(peaks, "n") or 0,
        "first_ts": _val(peaks, "first_ts"),
        "last_ts": _val(peaks, "last_ts"),
        "peak_temp_c": _val(peaks, "peak_temp"),
        "peak_power_w": _val(peaks, "peak_power"),
        "peak_fan_pct": _val(peaks, "peak_fan_pct"),
        "peak_fan_rpm": _val(peaks, "peak_fan_rpm"),
        "lowest_idle_power_w": _val(idle, "lowest_idle_w"),
    }


# L834-L839 moved to api/ops.py (cycle 9)



# L842-L856 moved to api/ops.py (cycle 9)



# L985-L1056 moved to api/ops.py (cycle 9)



# L1059-L1177 moved to api/ops.py (cycle 9)



# L1180-L1216 moved to api/ops.py (cycle 9)



def handle_push_vapid(ctx: dict) -> Response:
    """Return the VAPID public key for browser push subscription.

    The frontend feeds this into PushManager.subscribe({applicationServerKey}).
    Private key stays server-side, never exposed.
    """
    from ..modules import web_push
    cfg_dir = os.path.expanduser("~/.config/gpu-dashboard")
    try:
        data = web_push.ensure_vapid_keys(cfg_dir)
    except Exception as e:
        return 500, {"ok": False, "error": f"VAPID generation failed: {e}"}
    return 200, {"ok": True, "public_key": data["public_key"]}


def handle_push_subscribe(ctx: dict, payload: dict) -> Response:
    """Save a browser's push subscription to the DB.

    Payload shape (from PushSubscription.toJSON()) :
      {endpoint: "...", keys: {p256dh: "...", auth: "..."}}
    """
    storage = ctx.get("storage")
    if storage is None:
        return 503, {"ok": False, "error": "storage not available"}

    endpoint = payload.get("endpoint")
    keys = payload.get("keys") or {}
    p256dh = keys.get("p256dh")
    auth = keys.get("auth")
    if not endpoint or not p256dh or not auth:
        return 400, {"ok": False, "error": "endpoint + keys.p256dh + keys.auth required"}

    storage.add_push_subscription(endpoint, p256dh, auth)
    return 200, {"ok": True}


def handle_push_unsubscribe(ctx: dict, payload: dict) -> Response:
    """Remove a subscription by endpoint."""
    storage = ctx.get("storage")
    if storage is None:
        return 503, {"ok": False, "error": "storage not available"}
    endpoint = payload.get("endpoint")
    if not endpoint:
        return 400, {"ok": False, "error": "endpoint required"}
    n = storage.remove_push_subscription(endpoint)
    return 200, {"ok": True, "removed": n}


# L1301-L1316 moved to api/alerts.py (cycle 8)



def handle_push_status(ctx: dict) -> Response:
    """Return the count of active push subscriptions + the VAPID public key."""
    storage = ctx.get("storage")
    if storage is None:
        return 503, {"ok": False, "error": "storage not available"}
    from ..modules import web_push
    cfg_dir = os.path.expanduser("~/.config/gpu-dashboard")
    try:
        data = web_push.ensure_vapid_keys(cfg_dir)
        public_key = data["public_key"]
    except Exception:
        public_key = None
    return 200, {
        "ok": True,
        "count": len(storage.list_push_subscriptions()),
        "vapid_public_key": public_key,
    }


# L1290-L1327 moved to api/ops.py (cycle 9)



# L1330-L1355 moved to api/ops.py (cycle 9)



# L1541-L1621 moved to api/diagnostics.py (cycle 7b)



# ────────────────────────── /api/update/* ─────────────────────────────────


# L957-L967 moved to api/ops.py (cycle 9)



# L1378-L1425 moved to api/ops.py (cycle 9)



# L1428-L1451 moved to api/ops.py (cycle 9)



# L1454-L1493 moved to api/ops.py (cycle 9)



# L1496-L1518 moved to api/ops.py (cycle 9)



# L1521-L1568 moved to api/ops.py (cycle 9)



# L1571-L1593 moved to api/ops.py (cycle 9)



# L1596-L1630 moved to api/ops.py (cycle 9)



# L1633-L1675 moved to api/ops.py (cycle 9)



# L1678-L1718 moved to api/ops.py (cycle 9)



def handle_alerts_test(ctx: dict) -> Response:
    """Send a test Telegram message using current secrets.env values."""
    cfg = ctx["config"]
    token = cfg.get("TG_TOKEN", "")
    chat_id = cfg.get("TG_CHAT", "")
    if not token or not chat_id:
        return 400, {"ok": False, "error": "token or chat_id missing"}

    import datetime
    ok, msg = tg.send_message(
        token=token, chat_id=chat_id,
        text=f"🧪 *Test alert* from gpu-dashboard at {datetime.datetime.now().strftime('%H:%M:%S')}",
    )
    code = 200 if ok else 502
    return code, {"ok": ok, "msg": msg}


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



# ─── R&D #10.7 — Live README badge SVG generator ─────────────────────────────
def _badge_svg(label: str, value: str, color: str = "#4c1") -> str:
    """Return a shields.io-style SVG badge with the given label / value / color.
    No deps : just a stdlib f-string. Width auto-computed from char count
    (approximation : 7 px per char + paddings)."""
    # Cheap width estimate — for monospaceish look. shields.io uses ~7px/char.
    lw = len(label) * 6 + 10
    vw = len(value) * 7 + 10
    total = lw + vw
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{total}" height="20" role="img" aria-label="{label}: {value}">'
        f'<linearGradient id="s" x2="0" y2="100%">'
        f'<stop offset="0" stop-color="#bbb" stop-opacity=".1"/>'
        f'<stop offset="1" stop-opacity=".1"/>'
        f'</linearGradient>'
        f'<clipPath id="r"><rect width="{total}" height="20" rx="3"/></clipPath>'
        f'<g clip-path="url(#r)">'
        f'<rect width="{lw}" height="20" fill="#555"/>'
        f'<rect x="{lw}" width="{vw}" height="20" fill="{color}"/>'
        f'<rect width="{total}" height="20" fill="url(#s)"/>'
        f'</g>'
        f'<g fill="#fff" text-anchor="middle" font-family="DejaVu Sans,Verdana,Geneva,sans-serif" font-size="11">'
        f'<text x="{lw // 2}" y="14">{label}</text>'
        f'<text x="{lw + vw // 2}" y="14">{value}</text>'
        f'</g>'
        f'</svg>'
    )


_BADGE_TEMP_COLORS = {
    "ok": "#4c1",      # green for <70°C
    "warn": "#dfb317", # yellow 70-80°C
    "crit": "#e05d44", # red >=80°C
}


def handle_badge(ctx: dict, metric: str) -> Tuple[int, str]:
    """Generate a live SVG badge for the requested metric.

    Supported metrics : gpu-temp, power-now, tok-per-wh, uptime, top-model, util.
    Unknown metric → 404 with a fallback 'unknown' badge.
    """
    snap = _gpu_card_snapshot(gpu_index=0)
    alive = bool(snap and snap.get("alive"))

    if metric == "gpu-temp":
        if not alive:
            return 200, _badge_svg("temp", "offline", "#9f9f9f")
        t = int(snap.get("temp") or 0)
        color = (_BADGE_TEMP_COLORS["crit"] if t >= 80 else
                 _BADGE_TEMP_COLORS["warn"] if t >= 70 else
                 _BADGE_TEMP_COLORS["ok"])
        return 200, _badge_svg("temp", f"{t}°C", color)

    if metric == "power-now":
        if not alive:
            return 200, _badge_svg("power", "offline", "#9f9f9f")
        p = snap.get("power") or 0
        return 200, _badge_svg("power", f"{p:.0f} W", "#007ec6")

    if metric == "util":
        if not alive:
            return 200, _badge_svg("util", "offline", "#9f9f9f")
        u = int(snap.get("util_gpu") or 0)
        color = "#4c1" if u < 50 else "#dfb317" if u < 90 else "#e05d44"
        return 200, _badge_svg("util", f"{u}%", color)

    if metric == "tok-per-wh":
        # Try to read LLM perf from sampler / fallback to 0
        try:
            r = _gpus_available()  # noqa: re-uses nvidia probe
        except Exception:
            r = []
        # Read from /api/llm if available — best-effort
        try:
            from ..modules import llm_stats as _llm  # may or may not exist
            val = _llm.tokens_per_watt_hour()
        except Exception:
            val = None
        if val is None:
            return 200, _badge_svg("tok/Wh", "n/a", "#9f9f9f")
        return 200, _badge_svg("tok/Wh", f"{val:.0f}", "#a83f9f")

    if metric == "uptime":
        started = ctx.get("started_at")
        if started is None:
            return 200, _badge_svg("uptime", "n/a", "#9f9f9f")
        secs = int(time.time() - float(started))
        if secs < 60:
            txt = f"{secs}s"
        elif secs < 3600:
            txt = f"{secs // 60}m"
        elif secs < 86400:
            txt = f"{secs // 3600}h"
        else:
            txt = f"{secs // 86400}d"
        return 200, _badge_svg("uptime", txt, "#4c1")

    if metric == "top-model":
        d = (ctx.get("sampler").snapshot() if ctx.get("sampler") else [])
        # No direct top-model accessor — derive from llm_model in latest sample
        if alive and snap.get("name"):
            short = snap["name"].replace("NVIDIA ", "").replace("GeForce ", "")[:24]
            return 200, _badge_svg("gpu", short, "#76A8DC")
        return 200, _badge_svg("gpu", "offline", "#9f9f9f")

    # Unknown metric → 404 with a 'unknown' badge so the README still renders
    return 404, _badge_svg("badge", f"unknown:{metric}"[:24], "#9f9f9f")


# ─── R&D #10.6 — ANSI/tldr endpoint for CLI users ────────────────────────────
_ANSI = {
    "reset": "\x1b[0m",
    "bold": "\x1b[1m",
    "dim": "\x1b[2m",
    "red": "\x1b[31m",
    "green": "\x1b[32m",
    "yellow": "\x1b[33m",
    "blue": "\x1b[34m",
    "magenta": "\x1b[35m",
    "cyan": "\x1b[36m",
    "gray": "\x1b[90m",
}


def _color(text: str, c: str, enabled: bool = True) -> str:
    if not enabled or c not in _ANSI:
        return text
    return f"{_ANSI[c]}{text}{_ANSI['reset']}"


def _temp_color(t: float) -> str:
    if t >= 80:
        return "red"
    if t >= 70:
        return "yellow"
    if t >= 50:
        return "green"
    return "cyan"


def _spark(values: list, width: int = 12) -> str:
    """Unicode block sparkline. values clipped to [0,100]."""
    if not values:
        return ""
    blocks = " ▁▂▃▄▅▆▇█"
    # Resample to `width`
    if len(values) > width:
        step = len(values) / width
        sampled = [values[int(i * step)] for i in range(width)]
    else:
        sampled = values + [0] * (width - len(values))
    out = []
    for v in sampled:
        idx = max(0, min(8, int((v or 0) / 100 * 8)))
        out.append(blocks[idx])
    return "".join(out)


def handle_tldr(ctx: dict, params: Optional[dict] = None,
                headers: Optional[dict] = None) -> Tuple[int, str]:
    """ANSI-colored terminal-width-aware status card for CLI users.

    Query params :
      fmt    = tldr (default, multi-line) | oneline | full
      cols   = terminal width override (default 80)
    Headers :
      NO_COLOR = if set (any value), suppress ANSI codes (per no-color.org)
    """
    params = params or {}
    headers = headers or {}
    fmt = params.get("fmt", "tldr")
    try:
        cols = max(40, min(200, int(params.get("cols", "80"))))
    except (ValueError, TypeError):
        cols = 80
    color_on = "NO_COLOR" not in {k.upper() for k in headers}

    # Live snapshot — main GPU only
    snap = _gpu_card_snapshot(gpu_index=0)
    if not snap or not snap.get("alive"):
        return 200, "GPU offline\n"

    t = snap.get("temp", 0)
    util = snap.get("util_gpu", 0)
    power = snap.get("power", 0)
    plim = snap.get("power_limit", 0)
    vram_used = (snap.get("mem_used_mib", 0) or 0) / 1024
    vram_tot = (snap.get("mem_total_mib", 0) or 0) / 1024
    name = snap.get("name", "GPU")
    short_name = name.replace("NVIDIA GeForce ", "").replace("NVIDIA ", "")

    # Sampler history → util sparkline
    sampler = ctx.get("sampler")
    util_history: list = []
    if sampler:
        snap_buf = sampler.snapshot()
        util_history = [s.get("util_gpu", 0) or 0 for s in snap_buf[-30:]]
    spark = _spark(util_history, width=20)

    if fmt == "oneline":
        # Tiny one-line for prompt / motd
        line = (f"{_color(f'{t}°C', _temp_color(t), color_on)} "
                f"{_color(f'{util}%', 'cyan', color_on)} "
                f"{_color(f'{power:.0f}W', 'magenta', color_on)} "
                f"{_color(short_name, 'gray', color_on)}")
        return 200, line + "\n"

    if fmt == "full":
        # Multi-block layout
        lines = []
        lines.append(_color("─" * cols, "gray", color_on))
        lines.append(f" {_color('GreenWatts', 'bold', color_on)}  "
                     f"{_color(short_name, 'gray', color_on)}")
        lines.append(_color("─" * cols, "gray", color_on))
        lines.append(f" Temperature : {_color(f'{t}°C', _temp_color(t), color_on)}")
        lines.append(f" Utilization : {_color(f'{util}%', 'cyan', color_on)}  {spark}")
        lines.append(f" Power       : {_color(f'{power:.0f}W', 'magenta', color_on)} / {plim:.0f}W")
        lines.append(f" VRAM        : {_color(f'{vram_used:.1f}', 'yellow', color_on)} / {vram_tot:.1f} GiB")
        if snap.get("pcie_gen") is not None:
            lines.append(f" PCIe        : Gen {snap['pcie_gen']} ×{snap.get('pcie_width', '?')}")
        lines.append(_color("─" * cols, "gray", color_on))
        return 200, "\n".join(lines) + "\n"

    # default 'tldr' : compact 3-line block
    lines = []
    lines.append(f"{_color('GreenWatts', 'bold', color_on)}  "
                 f"{_color(short_name, 'gray', color_on)}")
    lines.append(f"  {_color(f'{t}°C', _temp_color(t), color_on)} · "
                 f"{_color(f'{util}%', 'cyan', color_on)} util  "
                 f"{spark}")
    lines.append(f"  {_color(f'{power:.0f}W', 'magenta', color_on)}/{plim:.0f}W · "
                 f"VRAM {_color(f'{vram_used:.1f}', 'yellow', color_on)}/{vram_tot:.1f}GiB")
    return 200, "\n".join(lines) + "\n"


# ─── R&D #9.3 + #9.6 handlers moved to api/auth.py (cycle 2) ──────────────


# Handlers from L3243-L3254 moved to api/integrations.py (cycle 3)



# Handlers from L3257-L3261 moved to api/integrations.py (cycle 3)



# Handlers from L3062-L3089 moved to api/llm.py (cycle 4)



# Handlers from L3092-L3102 moved to api/llm.py (cycle 4)



# Handlers from L3105-L3163 moved to api/llm.py (cycle 4)



# ─── R&D #7.5 — UPS/NUT awareness ────────────────────────────────────────────
def handle_ups_status(ctx: dict) -> Response:
    """Query the local NUT server and return the first UPS' state."""
    from ..modules import ups_nut
    cfg = ctx["config"]
    host = cfg.get("NUT_HOST", "localhost")
    try:
        port = int(cfg.get("NUT_PORT", "3493"))
    except (ValueError, TypeError):
        port = 3493
    ups_name = cfg.get("NUT_UPS") or None
    result = ups_nut.query(host=host, port=port, ups=ups_name, timeout=2.0)
    return 200, result


# L2100-L2103 moved to api/alerts.py (cycle 8)



# L2320-L2395 moved to api/diagnostics.py (cycle 7b)



# L2398-L2400 moved to api/diagnostics.py (cycle 7b)



# ─── R&D #7.4 — InfluxDB line protocol pusher status ─────────────────────────
def handle_influxdb_status(ctx: dict) -> Response:
    """Return the InfluxDB pusher's current status (last push ok/error)."""
    pusher = ctx.get("influxdb_pusher")
    cfg = ctx["config"]
    url = cfg.get("INFLUXDB_URL", "")
    if not url:
        return 200, {"ok": True, "enabled": False}
    if pusher is None:
        return 200, {"ok": True, "enabled": True, "running": False}
    s = pusher.status
    return 200, {
        "ok": True,
        "enabled": True,
        "running": True,
        "url": url,
        "bucket": cfg.get("INFLUXDB_BUCKET") or cfg.get("INFLUXDB_DATABASE", ""),
        "interval_s": float(cfg.get("INFLUXDB_INTERVAL", "15") or "15"),
        "last_push": s,
    }


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

