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



# ─────────────── GET / POST /api/alerts-config ────────────────────────────


def handle_alerts_config_get(ctx: dict) -> Response:
    cfg = ctx["config"]
    return 200, {
        "enabled": cfg.get_bool("TG_ENABLED", default=False),
        "token": cfg.get("TG_TOKEN", ""),
        "chat_id": cfg.get("TG_CHAT", ""),
        "on_drop": cfg.get_bool("ALERT_DROP", default=True),
        "on_recover": cfg.get_bool("ALERT_RECOVER", default=True),
    }


def handle_alerts_config_post(ctx: dict, payload: dict) -> Response:
    """Save alerts config to secrets.env (chmod 600)."""
    cfg = ctx["config"]
    secrets_path = os.path.expanduser(
        cfg.get("ALERTS_SECRETS_PATH", "~/.config/gpu-dashboard/secrets.env")
    )

    token = str(payload.get("token", "")).strip()
    chat_id = str(payload.get("chat_id", "")).strip()
    enabled = bool(payload.get("enabled", False))

    if enabled and (not token or not chat_id):
        return 400, {"ok": False, "error": "token and chat_id required when enabled"}

    # Write secrets.env
    from ..config import write_env_file
    os.makedirs(os.path.dirname(secrets_path), exist_ok=True)
    write_env_file(secrets_path, {
        "TG_ENABLED": "1" if enabled else "0",
        "TG_TOKEN": token,
        "TG_CHAT": chat_id,
        "ALERT_DROP": "1" if payload.get("on_drop", True) else "0",
        "ALERT_RECOVER": "1" if payload.get("on_recover", True) else "0",
    }, header="# Auto-generated by gpu-dashboard. chmod 600.")
    os.chmod(secrets_path, 0o600)
    return 200, {"ok": True}


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



# ────────────────────────── GET /api/thermal-stats ─────────────────────────


def handle_thermal_stats(ctx: dict, params: Optional[dict] = None) -> Response:
    """Temperature aggregates over 24h + 24-point downsampled series."""
    storage = ctx.get("storage")
    if storage is None:
        return 503, {"ok": False, "error": "storage not available"}

    import time as _time
    now = int(_time.time())
    gpu = _parse_gpu_index(params or {})

    samples_24h = storage.get_samples(from_ts=now - 86400, to_ts=now, gpu_index=gpu)
    temps = [s["temp"] for s in samples_24h if s.get("temp") is not None]
    avg_temp = sum(temps) / len(temps) if temps else 0
    peak_temp = max(temps) if temps else 0

    samples_7d = storage.get_samples(from_ts=now - 7 * 86400, to_ts=now, gpu_index=gpu)
    above_80 = 0
    prev = None
    for s in samples_7d:
        if s.get("temp") is None:
            prev = None
            continue
        if s["temp"] > 80:
            if prev is not None and prev["temp"] > 80:
                dt = s["ts"] - prev["ts"]
                above_80 += min(dt, 300)
        prev = s

    series = []
    for h in range(24):
        bucket_start = now - (24 - h) * 3600
        bucket_end = bucket_start + 3600
        in_bucket = [s["temp"] for s in samples_24h
                     if bucket_start <= s["ts"] < bucket_end and s.get("temp") is not None]
        series.append(round(sum(in_bucket) / len(in_bucket), 1) if in_bucket else 0)

    return 200, {
        "ok": True,
        "avg_temp_24h": round(avg_temp, 1),
        "peak_temp_24h": peak_temp,
        "time_above_80c_seconds": above_80,
        "series_24h": series,
        "samples_count": len(samples_24h),
    }


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


def handle_processes(ctx: dict) -> Response:
    """Per-process GPU usage via `nvidia-smi --query-compute-apps`.

    Returns {available: bool, processes: [{pid, name, vram_mib, cmdline}]}
    sorted by vram desc. cmdline is best-effort from /proc/<pid>/cmdline.
    """
    cfg = ctx["config"]
    gpu_index = cfg.get_int("GPU_INDEX", default=0)
    try:
        r = subprocess.run(
            ["nvidia-smi", "-i", str(int(gpu_index)),
             "--query-compute-apps=pid,process_name,used_memory",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=3,
        )
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        return 200, {"available": False, "processes": []}

    if r.returncode != 0:
        return 200, {"available": True, "processes": []}

    processes = []
    for line in r.stdout.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 3 or not parts[0].isdigit():
            continue
        try:
            vram = int(parts[2].split()[0]) if parts[2] else 0
        except (ValueError, IndexError):
            vram = 0
        pid = int(parts[0])
        processes.append({
            "pid": pid,
            "name": parts[1],
            "vram_mib": vram,
            "cmdline": _read_cmdline(pid),
        })
    processes.sort(key=lambda p: p["vram_mib"], reverse=True)
    return 200, {"available": True, "processes": processes}


# ────────────────────────── GET /api/prom ─────────────────────────────────


def handle_prom(ctx: dict) -> Response:
    """Prometheus text-format exporter. Pluggable into Grafana, VictoriaMetrics,
    Uptime Kuma, blackbox-exporter, etc.

    Returns (200, str). The server wraps with Content-Type: text/plain.
    """
    cfg = ctx["config"]
    gpu_index = cfg.get_int("GPU_INDEX", default=0)
    gpu = _gpu_card_snapshot(gpu_index=gpu_index)
    label = f'{{gpu="{gpu_index}",name="{gpu.get("name", "?")}"}}'

    lines = []

    def metric(name, help_text, mtype, value, lbl=label):
        lines.append(f"# HELP {name} {help_text}")
        lines.append(f"# TYPE {name} {mtype}")
        lines.append(f"{name}{lbl} {value}")

    alive = 1 if gpu.get("alive") else 0
    metric("gpu_alive", "1 if nvidia-smi responded for this GPU", "gauge", alive)

    if gpu.get("alive"):
        metric("gpu_temp_celsius", "GPU temperature in Celsius",
               "gauge", gpu.get("temp", 0))
        metric("gpu_fan_percent", "GPU fan speed in percent",
               "gauge", gpu.get("fan_pct", 0))
        metric("gpu_power_watts", "GPU power draw in watts",
               "gauge", gpu.get("power", 0))
        metric("gpu_power_limit_watts", "GPU power limit in watts",
               "gauge", gpu.get("power_limit", 0))
        metric("gpu_util_percent", "GPU utilization in percent",
               "gauge", gpu.get("util_gpu", 0))
        # Memory in bytes (Prometheus convention) — convert from MiB
        mem_used = gpu.get("mem_used_mib", 0) * 1024 * 1024
        mem_total = gpu.get("mem_total_mib", 0) * 1024 * 1024
        metric("gpu_memory_used_bytes", "GPU VRAM in use, bytes",
               "gauge", mem_used)
        metric("gpu_memory_total_bytes", "GPU VRAM total, bytes",
               "gauge", mem_total)

    # OcuLink watchdog status if module is on
    if ctx.get("watchdog_drops") is not None:
        metric("gpu_oculink_drops_total", "OcuLink drop count since boot",
               "counter", ctx["watchdog_drops"])

    # Year-to-date energy + LLM tokens — for Grafana yearly-budget panels
    storage = ctx.get("storage")
    if storage is not None:
        try:
            from .cost import handle_power_stats as _hps  # cycle 6 late import
            _, ps = _hps(ctx)
            if ps.get("ok"):
                metric("gpu_dashboard_kwh_year",
                       "Cumulative energy since Jan 1 (kWh)", "gauge",
                       ps.get("kwh_year", 0))
                metric("gpu_dashboard_cost_year",
                       f"Cumulative cost since Jan 1 ({ps.get('currency','EUR')})",
                       "gauge", ps.get("cost_year", 0))
                metric("gpu_dashboard_kwh_today",
                       "Energy consumed today (kWh)", "gauge",
                       ps.get("kwh_today", 0))
            from .llm import handle_llm_lifetime as _hll  # cycle 4 — late import to avoid cycle
            _, ll = _hll(ctx)
            if ll.get("available"):
                metric("gpu_dashboard_tokens_year_total",
                       "LLM tokens generated since Jan 1", "counter",
                       ll.get("total_tokens_this_year", 0))
                metric("gpu_dashboard_tokens_lifetime_total",
                       "Cumulative LLM tokens since install", "counter",
                       ll.get("total_tokens_generated", 0))
        except Exception:
            pass  # never let metric collection break the endpoint

    # Latest alert age (seconds since most recent alert event), or -1 if none
    if storage is not None:
        try:
            import time as _t3
            alerts = storage.get_events(
                from_ts=int(_t3.time()) - 7 * 86400, kind="alert"
            )
            if alerts:
                age = int(_t3.time()) - max(a["ts"] for a in alerts)
                metric("gpu_dashboard_latest_alert_age_seconds",
                       "Seconds since the most recent alert (within last 7d)",
                       "gauge", age)
        except Exception:
            pass

    return 200, "\n".join(lines) + "\n"


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


# ────────────────────────── GET /api/health ───────────────────────────────


def handle_health(ctx: dict) -> Response:
    """JSON health status for external monitoring (Uptime Kuma, Grafana, etc.).

    Returns 200 + {status: "ok"} if all critical components are healthy.
    Returns 503 + {status: "degraded"} if any one fails. The components dict
    lets the caller diagnose which subsystem is down.
    """
    import time as _time
    from .. import __version__ as _ver

    components = {"gpu": False, "sampler": False, "storage": False}

    # GPU = nvidia-smi responds + returns a valid sample
    try:
        gpu = _gpu_card_snapshot()
        components["gpu"] = bool(gpu.get("alive"))
    except Exception:
        components["gpu"] = False

    # Sampler = the daemon thread is alive
    sampler = ctx.get("sampler")
    components["sampler"] = sampler is not None and getattr(sampler, "_thread", None) is not None

    # Storage = DB connection accepts a trivial query
    storage = ctx.get("storage")
    if storage is not None:
        try:
            _ = storage.schema_version()
            components["storage"] = True
        except Exception:
            components["storage"] = False

    # Recent alerts — last 5 from the events table (gives Uptime Kuma + Grafana
    # operational context without a second API call).
    recent_alerts = []
    if storage is not None:
        try:
            from_ts = max(0, int(_time.time()) - 7 * 86400)  # last 7 days max
            events = storage.get_events(from_ts=from_ts, kind="alert")
            recent_alerts = [
                {"ts": ev["ts"], "payload": ev.get("payload")}
                for ev in events[-5:][::-1]  # last 5, most recent first
            ]
        except Exception:
            recent_alerts = []

    # Uptime metrics for Uptime Kuma badges (cycle 136, R&D #3.3)
    up_minutes_24h = 0
    uptime_pct_24h = 0.0
    sample_restart_count = 0
    if storage is not None:
        try:
            cur = storage._conn.execute(
                "SELECT COUNT(DISTINCT ts/60) AS n FROM samples WHERE ts >= ?",
                (int(_time.time()) - 86400,),
            )
            row = cur.fetchone()
            up_minutes_24h = (row["n"] if row else 0) or 0
            uptime_pct_24h = round(up_minutes_24h / 1440 * 100, 1)

            # Restart count : ts gaps > 5 min in the last 24h indicate process
            # restarts (assuming the sampler runs continuously at <60s interval).
            cur = storage._conn.execute(
                "SELECT ts FROM samples WHERE ts >= ? "
                "AND gpu_index = 0 ORDER BY ts ASC",
                (int(_time.time()) - 86400,),
            )
            prev_ts = None
            for r in cur.fetchall():
                if prev_ts is not None and r["ts"] - prev_ts > 300:
                    sample_restart_count += 1
                prev_ts = r["ts"]
        except Exception:
            pass

    all_ok = all(components.values())
    code = 200 if all_ok else 503
    return code, {
        "status": "ok" if all_ok else "degraded",
        "components": components,
        "uptime_seconds": max(0, int(_time.time() - (ctx.get("started_at") or _time.time()))),
        "version": _ver,
        "recent_alerts": recent_alerts,
        "up_minutes_24h": up_minutes_24h,
        "uptime_pct_24h": uptime_pct_24h,
        "restart_count_24h": sample_restart_count,
        "sampler_alive": components.get("sampler", False),
    }


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


_REDACT_KEYS = (
    "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
    "WEBHOOK_URL",
    "VAPID_PRIVATE_KEY", "VAPID_PUBLIC_KEY",
    "PUSH_VAPID_PRIVATE", "PUSH_VAPID_PUBLIC",
)


def _redact_env_file(content: str) -> str:
    """Replace VALUE with '***REDACTED***' for any sensitive key."""
    out_lines = []
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            out_lines.append(line)
            continue
        if "=" in line:
            key, _ = line.split("=", 1)
            if any(redacted in key for redacted in _REDACT_KEYS):
                out_lines.append(f"{key}=***REDACTED***")
                continue
        out_lines.append(line)
    return "\n".join(out_lines) + "\n"


def handle_sysreport_bundle(ctx: dict) -> tuple:
    """Bundle sysreport + events + redacted config + recent logs into tar.gz.

    Returns (200, bytes, content_type, filename) for the server to wrap with
    _send_binary. On error returns (500, json_dict).
    """
    import io
    import tarfile
    import gzip
    import datetime as _dt
    import json as _json

    # 1) Gather pieces
    _, sysreport = handle_sysreport(ctx)
    sysreport_bytes = _json.dumps(sysreport, indent=2).encode("utf-8")

    events_bytes = b"[]"
    storage = ctx.get("storage")
    if storage is not None:
        try:
            events = storage.get_events(from_ts=0)
            events_bytes = _json.dumps(events[-100:], indent=2, default=str).encode("utf-8")
        except Exception:
            pass

    config_bytes = b""
    config_path = ctx.get("config_path") or os.path.expanduser(
        "~/.config/gpu-dashboard/config.env"
    )
    if os.path.isfile(config_path):
        try:
            with open(config_path, "r") as f:
                raw = f.read()
            config_bytes = _redact_env_file(raw).encode("utf-8")
        except OSError:
            pass

    log_bytes = b""
    cfg = ctx.get("config")
    log_file = (cfg.get("LOG_FILE") if cfg else "") or ""
    if log_file and os.path.isfile(log_file):
        try:
            with open(log_file, "rb") as f:
                # Read last ~64 KB and keep the last 500 lines
                f.seek(0, 2)
                size = f.tell()
                f.seek(max(0, size - 65536))
                tail = f.read().decode("utf-8", errors="replace")
            log_bytes = "\n".join(tail.splitlines()[-500:]).encode("utf-8")
        except OSError:
            pass

    # 2) Build tar.gz in memory
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        def _add(name: str, data: bytes):
            ti = tarfile.TarInfo(name)
            ti.size = len(data)
            ti.mtime = int(_dt.datetime.now().timestamp())
            ti.mode = 0o644
            tar.addfile(ti, io.BytesIO(data))

        _add("sysreport.json", sysreport_bytes)
        _add("events.json", events_bytes)
        if config_bytes:
            _add("config.env", config_bytes)
        if log_bytes:
            _add("recent.log", log_bytes)

    payload = buf.getvalue()
    ts = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    return 200, payload, "application/gzip", f"gpu-dashboard-sysreport-{ts}.tar.gz"


def handle_sysreport(ctx: dict) -> Response:
    """One-shot system info dump for support tickets / bug reports.

    Aggregates kernel, distro, NVIDIA driver, GPU list, disk free for the
    DB dir, RAM total. All sub-fields tolerate missing tools (returns None).
    """
    from .. import __version__ as _ver
    import datetime as _dt
    import sys as _sys
    import platform as _plat

    cfg = ctx.get("config")
    schema_version = None
    storage = ctx.get("storage")
    if storage is not None:
        try:
            schema_version = storage.schema_version()
        except Exception:
            pass

    # modules_enabled (mirrors /api/version logic)
    module_keys = [
        "MODULE_POWER_LIMIT", "MODULE_CLOCK_OFFSETS",
        "MODULE_TELEGRAM_ALERTS", "MODULE_OCULINK_WATCHDOG",
        "MODULE_FAN_CURVE", "MODULE_AUTO_PROFILE", "MODULE_ALERT_MONITOR",
    ]
    modules_enabled = []
    if cfg is not None:
        for k in module_keys:
            if cfg.get_bool(k):
                modules_enabled.append(k.replace("MODULE_", "").lower())

    # distro from /etc/os-release
    distro = None
    try:
        with open("/etc/os-release", "r") as f:
            kv = {}
            for line in f:
                if "=" in line:
                    k, v = line.strip().split("=", 1)
                    kv[k] = v.strip('"')
        if "PRETTY_NAME" in kv:
            distro = kv["PRETTY_NAME"]
        elif "NAME" in kv:
            distro = kv["NAME"] + " " + kv.get("VERSION_ID", "")
    except OSError:
        pass

    # NVIDIA driver + CUDA
    nv_driver = None
    nv_cuda = None
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=3,
        )
        if r.returncode == 0:
            nv_driver = r.stdout.strip().split("\n")[0].strip() or None
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        pass
    try:
        r = subprocess.run(["nvidia-smi"], capture_output=True, text=True, timeout=3)
        if r.returncode == 0:
            # Parse CUDA Version: 12.4 from the header
            import re as _re
            m = _re.search(r"CUDA Version:\s*([\d.]+)", r.stdout)
            if m:
                nv_cuda = m.group(1)
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        pass

    # GPU list (uses existing helper)
    gpus = _gpus_available()

    # Disk free for the DB dir
    disk_free_gb = None
    try:
        import shutil
        db_path = (cfg.get("DASHBOARD_DB_PATH", default="") if cfg else "") or \
                   os.path.expanduser("~/.local/share/gpu-dashboard/metrics.db")
        db_dir = os.path.dirname(db_path) or "/"
        if os.path.exists(db_dir):
            free_bytes = shutil.disk_usage(db_dir).free
            disk_free_gb = round(free_bytes / (1024**3), 2)
    except Exception:
        pass

    # RAM total
    ram_total_gb = None
    try:
        with open("/proc/meminfo", "r") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    kb = int(line.split()[1])
                    ram_total_gb = round(kb / (1024**2), 2)
                    break
    except (OSError, IOError, ValueError, IndexError):
        pass

    return 200, {
        "ok": True,
        "timestamp_iso": _dt.datetime.now().isoformat(timespec="seconds"),
        "dashboard_version": _ver,
        "schema_version": schema_version,
        "modules_enabled": modules_enabled,
        "system": {
            "kernel": _plat.release(),
            "distro": distro,
            "python": _sys.version.split()[0],
            "arch": _plat.machine(),
        },
        "nvidia": {
            "driver": nv_driver,
            "cuda": nv_cuda,
            "gpus": gpus,
        },
        "disk_free_gb_dashboard_data": disk_free_gb,
        "ram_total_gb": ram_total_gb,
    }


def handle_version(ctx: dict) -> Response:
    """Tiny endpoint for headless monitoring / CLI scripts.

    Returns {version, schema_version, modules_enabled}. Much smaller payload
    than /api/about — designed to be polled cheaply from scripts that just
    need to verify the dashboard is up and at the expected version.
    """
    from .. import __version__ as _ver
    cfg = ctx.get("config")
    schema_version = None
    storage = ctx.get("storage")
    if storage is not None:
        try:
            schema_version = storage.schema_version()
        except Exception:
            pass

    # Build modules_enabled list from config
    module_keys = [
        "MODULE_POWER_LIMIT", "MODULE_CLOCK_OFFSETS",
        "MODULE_TELEGRAM_ALERTS", "MODULE_OCULINK_WATCHDOG",
        "MODULE_FAN_CURVE", "MODULE_AUTO_PROFILE",
        "MODULE_ALERT_MONITOR",
    ]
    modules_enabled = []
    if cfg is not None:
        for k in module_keys:
            if cfg.get_bool(k):
                # Strip MODULE_ prefix and lowercase
                modules_enabled.append(k.replace("MODULE_", "").lower())

    return 200, {
        "ok": True,
        "version": _ver,
        "schema_version": schema_version,
        "modules_enabled": modules_enabled,
    }


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


def handle_alerts_latest(ctx: dict) -> Response:
    """Return the most-recent alert event.

    Called by the service worker on push receipt to fetch the alert text
    (avoids needing RFC 8291 encrypted payloads — push is just a 'wake up
    and check' signal, SW fetches details here).
    """
    storage = ctx.get("storage")
    if storage is None:
        return 503, {"ok": False, "error": "storage not available"}
    events = storage.get_events(from_ts=0, kind="alert")
    if not events:
        return 200, {"ok": True, "alert": None}
    # Most recent first
    latest = max(events, key=lambda e: e["ts"])
    return 200, {"ok": True, "alert": latest}


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


def handle_about(ctx: dict) -> Response:
    """Static info about the running server : version, paths, uptime, vBIOS."""
    import platform
    import sys as _sys
    import time as _time
    from .. import __version__ as _ver

    started = ctx.get("started_at") or _time.time()
    uptime = max(0, int(_time.time() - started))
    storage_path = ctx.get("config", None)
    if storage_path is not None:
        storage_path = os.path.expanduser(
            storage_path.get("STORAGE_DB_PATH", "~/.local/share/gpu-dashboard/metrics.db")
        )
    else:
        storage_path = os.path.expanduser("~/.local/share/gpu-dashboard/metrics.db")

    # Best-effort: pull vBIOS version from a fresh snapshot
    vbios = None
    try:
        cfg = ctx.get("config")
        gpu_index = cfg.get_int("GPU_INDEX", default=0) if cfg else 0
        snap = _gpu_card_snapshot(gpu_index=gpu_index)
        vbios = snap.get("vbios_version")
    except Exception:
        pass

    return 200, {
        "version": _ver,
        "uptime_seconds": uptime,
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "config_path": ctx.get("config_path") or os.path.expanduser("~/.config/gpu-dashboard/config.env"),
        "storage_path": storage_path,
        "license": "MIT",
        "repo_url": "https://github.com/Shad107/gpu-dashboard",
        "vbios_version": vbios,
    }


# ────────────────────────── POST /api/stop ────────────────────────────────


def handle_stop(ctx: dict) -> Response:
    """Stop the server gracefully via sys.exit(0).

    Response is flushed BEFORE the exit, so the frontend gets confirmation
    before connection drops. Unlike /api/restart, this does NOT re-launch
    — the user (or systemd) decides what to do after.
    """
    import sys
    import threading
    import time as _t

    def _stop():
        _t.sleep(0.5)
        try:
            if ctx.get("sampler"): ctx["sampler"].stop()
            if ctx.get("retention"): ctx["retention"].stop()
            if ctx.get("storage"): ctx["storage"].close()
        except Exception:
            pass
        sys.exit(0)

    threading.Thread(target=_stop, daemon=False).start()
    return 200, {"ok": True, "message": "stopping"}


# ────────────────────────── GET /api/logs ─────────────────────────────────


def handle_logs(ctx: dict, params: dict) -> Response:
    """Tail the dashboard log. Two backends in priority order :

    1. LOG_FILE  — plain text file (typical: stdout-redirected from get.sh)
    2. JOURNALCTL_UNIT — `journalctl --user -u <unit> -n <tail> --no-pager`

    Returns {ok, source, lines: [str]} or {ok: False, reason: str}.
    Tail defaults to 100 lines.
    """
    try:
        tail = int(params.get("tail", 100))
        if tail < 0:
            return 400, {"ok": False, "error": "tail must be >= 0"}
    except (ValueError, TypeError):
        return 400, {"ok": False, "error": "tail must be integer"}

    cfg = ctx.get("config")
    log_file = (cfg.get("LOG_FILE", "") if cfg else "").strip()
    journalctl_unit = (cfg.get("JOURNALCTL_UNIT", "") if cfg else "").strip()

    # Auto-detect : if neither is set, default to journalctl --user -u
    # gpu-dashboard.service (the canonical install path documented in README).
    # This avoids the "no log source configured" error for users who
    # installed via the standard systemd-user unit.
    if not log_file and not journalctl_unit:
        try:
            r = subprocess.run(
                ["systemctl", "--user", "is-active", "gpu-dashboard.service"],
                capture_output=True, text=True, timeout=2,
            )
            if r.returncode == 0 and r.stdout.strip() in ("active", "activating"):
                journalctl_unit = "gpu-dashboard.service"
        except (FileNotFoundError, subprocess.SubprocessError, OSError):
            pass

    if log_file:
        log_file = os.path.expanduser(log_file)
        if not os.path.isfile(log_file):
            return 200, {"ok": False, "reason": f"log file does not exist: {log_file}"}
        try:
            with open(log_file, "r", encoding="utf-8", errors="replace") as f:
                all_lines = f.readlines()
            return 200, {
                "ok": True,
                "source": "file",
                "path": log_file,
                "lines": all_lines[-tail:] if tail > 0 else [],
                "total": len(all_lines),
            }
        except OSError as e:
            return 200, {"ok": False, "reason": f"could not read log: {e}"}

    if journalctl_unit:
        try:
            r = subprocess.run(
                ["journalctl", "--user", "-u", journalctl_unit,
                 "-n", str(max(1, tail)), "--no-pager", "--output=short-iso"],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode != 0:
                return 200, {"ok": False, "reason": f"journalctl failed: {r.stderr.strip()}"}
            lines = [l + "\n" for l in r.stdout.splitlines() if l]
            return 200, {
                "ok": True,
                "source": "journalctl",
                "unit": journalctl_unit,
                "lines": lines[-tail:] if tail > 0 else [],
            }
        except (FileNotFoundError, subprocess.SubprocessError, OSError) as e:
            return 200, {"ok": False, "reason": f"journalctl unavailable: {e}"}

    return 200, {
        "ok": False,
        "reason": "no log source configured — neither LOG_FILE nor JOURNALCTL_UNIT is set, "
                  "and no `gpu-dashboard.service` user unit appears active. "
                  "Set LOG_FILE=/path/to/log or JOURNALCTL_UNIT=your-unit-name in "
                  "~/.config/gpu-dashboard/config.env, then restart the dashboard.",
    }


# ────────────────────────── /api/update/* ─────────────────────────────────


def _git(repo_path: str, *args, timeout=5):
    """Run git in `repo_path`, returns CompletedProcess. Never raises."""
    try:
        return subprocess.run(
            ["git", "-C", repo_path, *args],
            capture_output=True, text=True, timeout=timeout,
        )
    except (FileNotFoundError, subprocess.SubprocessError, OSError) as e:
        class _Fail:
            returncode = 127; stdout = ""; stderr = str(e)
        return _Fail()


def handle_update_check(ctx: dict) -> Response:
    """Check if the repo is behind the remote. Runs `git fetch` then computes
    behind count via `git rev-list HEAD..@{u} --count`.

    Returns:
      ok: bool
      current_sha: str (HEAD)
      remote_sha: str (origin/main) — None if no remote
      behind: int — commits behind. None if no upstream tracking.
      last_remote_msg: str — subject line of the remote HEAD
    """
    repo_path = ctx.get("repo_path") or ""
    if not repo_path or not os.path.isdir(os.path.join(repo_path, ".git")):
        return 400, {"ok": False, "error": "not a git repo"}

    # Get current SHA
    r_head = _git(repo_path, "rev-parse", "--short", "HEAD")
    current_sha = r_head.stdout.strip() if r_head.returncode == 0 else None

    # Try to fetch (silent on network failure)
    _git(repo_path, "fetch", "--quiet", timeout=15)

    # Get upstream SHA (may not exist if no tracking branch)
    r_up = _git(repo_path, "rev-parse", "--short", "@{u}")
    if r_up.returncode != 0:
        # No upstream — can't compute behind
        return 200, {
            "ok": True,
            "current_sha": current_sha,
            "remote_sha": None,
            "behind": None,
            "last_remote_msg": None,
        }
    remote_sha = r_up.stdout.strip()

    r_behind = _git(repo_path, "rev-list", "HEAD..@{u}", "--count")
    behind = int(r_behind.stdout.strip()) if r_behind.returncode == 0 and r_behind.stdout.strip().isdigit() else 0

    r_log = _git(repo_path, "log", "-1", "--format=%s", "@{u}")
    last_msg = r_log.stdout.strip() if r_log.returncode == 0 else None

    return 200, {
        "ok": True,
        "current_sha": current_sha,
        "remote_sha": remote_sha,
        "behind": behind,
        "last_remote_msg": last_msg,
    }


def handle_update_pull(ctx: dict) -> Response:
    """Run `git pull --ff-only`. Refuses if the working tree is dirty."""
    repo_path = ctx.get("repo_path") or ""
    if not repo_path or not os.path.isdir(os.path.join(repo_path, ".git")):
        return 400, {"ok": False, "error": "not a git repo"}

    # Check if working tree is clean
    r_status = _git(repo_path, "status", "--porcelain")
    if r_status.returncode == 0 and r_status.stdout.strip():
        return 409, {
            "ok": False,
            "error": "working tree is dirty (uncommitted changes). Commit or stash first.",
            "dirty_files": [line[3:] for line in r_status.stdout.splitlines()],
        }

    # Try pull --ff-only
    r_pull = _git(repo_path, "pull", "--ff-only", timeout=30)
    if r_pull.returncode != 0:
        return 500, {
            "ok": False,
            "error": "git pull failed",
            "stderr": r_pull.stderr.strip(),
        }
    return 200, {"ok": True, "output": r_pull.stdout.strip()}


# ────────────────────────── GET /api/snapshot ─────────────────────────────


def handle_snapshot(ctx: dict):
    """Bundle config.env + secrets.env + metrics.db into a tar.gz for download.

    Returns (code, bytes). Server wraps with Content-Type application/gzip +
    Content-Disposition attachment. Missing files are skipped silently — the
    snapshot tries to include everything it can find.
    """
    import io
    import tarfile
    import time as _time

    # Determine paths from ctx
    cfg = ctx.get("config")
    config_path = ctx.get("config_path") or (
        cfg.get("_CONFIG_PATH") if cfg else None
    ) or os.path.expanduser("~/.config/gpu-dashboard/config.env")
    secrets_path = ctx.get("secrets_path") or (
        cfg.get("_SECRETS_PATH") if cfg else None
    ) or os.path.expanduser("~/.config/gpu-dashboard/secrets.env")
    storage_path = ctx.get("storage_path") or (
        cfg.get("STORAGE_DB_PATH") if cfg else None
    ) or os.path.expanduser("~/.local/share/gpu-dashboard/metrics.db")
    storage_path = os.path.expanduser(storage_path)

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for src, arc in [
            (config_path,  "gpu-dashboard-snapshot/config.env"),
            (secrets_path, "gpu-dashboard-snapshot/secrets.env"),
            (storage_path, "gpu-dashboard-snapshot/metrics.db"),
        ]:
            if os.path.isfile(src):
                try:
                    tar.add(src, arcname=arc)
                except (OSError, tarfile.TarError):
                    pass
    return 200, buf.getvalue()


# ────────────────────────── POST /api/restart ─────────────────────────────


def handle_modules_list(ctx: dict) -> Response:
    """Returns the known opt-in modules and their current enabled state.

    Used by the Services tab to render toggle switches that auto-restart.
    """
    cfg = ctx.get("config")
    known = [
        ("MODULE_POWER_LIMIT",      "Power Limit",       "Slider + 3 named profiles in Settings"),
        ("MODULE_CLOCK_OFFSETS",    "Clock offsets",     "GPU + memory offset sliders"),
        ("MODULE_FAN_CURVE",        "Fan curve daemon",  "Custom fan curve daemon (otherwise driver default)"),
        ("MODULE_OCULINK_WATCHDOG", "OcuLink watchdog",  "Auto-detects + alerts on PCIe link drops"),
        ("MODULE_TELEGRAM_ALERTS",  "Telegram alerts",   "Send alerts to a Telegram bot"),
        ("MODULE_AUTO_PROFILE",     "Auto-profile",      "Switches profile by load (silent/sweet/boost)"),
        ("MODULE_ALERT_MONITOR",    "Alert monitor",     "Threshold-based alerts (temp, fan, VRAM)"),
    ]
    out = []
    for key, label, desc in known:
        enabled = cfg.get_bool(key) if cfg is not None else False
        out.append({"key": key, "label": label, "description": desc, "enabled": enabled})
    return 200, {"ok": True, "modules": out}


def handle_modules_toggle(ctx: dict, payload) -> Response:
    """Set MODULE_<NAME>=0/1 in config.env, then trigger a service restart.

    The frontend should poll /api/version until back up after this returns.
    """
    if not isinstance(payload, dict):
        return 400, {"ok": False, "error": "payload must be an object"}
    key = str(payload.get("key", "")).strip()
    enabled = bool(payload.get("enabled"))
    if not key.startswith("MODULE_") or not key.replace("_", "").isalnum():
        return 400, {"ok": False, "error": f"invalid module key {key!r}"}

    cfg = ctx.get("config")
    if cfg is None:
        return 500, {"ok": False, "error": "no config loaded"}

    cfg.set(key, "1" if enabled else "0")

    config_path = ctx.get("config_path") or os.path.expanduser(
        "~/.config/gpu-dashboard/config.env"
    )
    try:
        os.makedirs(os.path.dirname(config_path), exist_ok=True)
        existing = {}
        if os.path.isfile(config_path):
            from ..config import parse_env_file
            existing = parse_env_file(config_path)
        existing[key] = "1" if enabled else "0"
        from ..config import write_env_file
        write_env_file(config_path, existing,
                       header="# Auto-updated by gpu-dashboard /api/modules/toggle")
    except OSError as e:
        return 500, {"ok": False, "error": f"could not write config.env: {e}"}

    # Trigger restart in a background thread so we can return 200 first.
    # The frontend will poll /api/version until the new process answers.
    import threading as _th
    def _delayed_restart():
        import time as _t
        _t.sleep(0.5)
        try:
            handle_restart(ctx)
        except Exception:
            pass
    _th.Thread(target=_delayed_restart, daemon=True).start()

    return 200, {"ok": True, "key": key, "enabled": enabled,
                 "message": "config updated, restarting service…"}


def handle_restart(ctx: dict) -> Response:
    """Restart the server in-place via os.execv.

    Returns 200 immediately, then re-execs the Python process after a short
    delay so the response can be sent. Works for both interactive runs and
    systemd-managed services.
    """
    import sys
    import threading
    import time as _t

    def _restart():
        _t.sleep(0.5)  # let the HTTP response flush
        try:
            if ctx.get("sampler"): ctx["sampler"].stop()
            if ctx.get("retention"): ctx["retention"].stop()
            if ctx.get("storage"): ctx["storage"].close()
        except Exception:
            pass
        os.execv(sys.executable, [sys.executable] + sys.argv)

    threading.Thread(target=_restart, daemon=False).start()
    return 200, {"ok": True, "message": "restarting"}


# ────────────────────────── /api/setup/* ───────────────────────────────────


def handle_setup_detect(ctx: dict) -> Response:
    """Full environment detection + module recommendations for the wizard.

    Pure GET — never modifies state. The frontend calls this on wizard load
    and re-calls after the user reports they've executed sudo commands.
    """
    from ..install import _gather_env, recommend_modules
    from ..profile import get_profile_for_gpu

    try:
        env = _gather_env()
    except Exception as e:
        return 500, {"ok": False, "error": f"detection failed: {e}"}

    recs = recommend_modules(env)
    profile = None
    if env.get("nvidia", {}).get("gpus"):
        try:
            profile = get_profile_for_gpu(
                ctx.get("profiles_dir", "profiles"),
                env["nvidia"]["gpus"][0]["name"],
            )
        except Exception:
            profile = None

    return 200, {
        "ok": True,
        "env": env,
        "modules": recs,
        "profile": profile,
        "setup_required": bool(ctx.get("setup_required", False)),
    }


def handle_setup_recheck(ctx: dict, module_name: str) -> Response:
    """Re-run can_enable() for ONE module after the user reports sudo done.

    Used by the wizard's "I executed the command, recheck now" button.
    """
    from ..modules import power_limit as _pl, clock_offsets as _co
    from .. import detect as _detect

    if module_name == "power_limit":
        wrapper = ctx["config"].get("POWER_LIMIT_WRAPPER", "/usr/local/bin/set-power-limit")
        ok, reason = _pl.can_enable(wrapper_path=wrapper)
        return 200, {"ok": ok, "reason": reason}

    if module_name == "clock_offsets":
        coolbits_info = _detect.detect_coolbits()
        ok, reason = _co.can_enable(coolbits_info)
        return 200, {"ok": ok, "reason": reason}

    if module_name == "oculink_watchdog":
        # Heuristic: the systemd service exists & is active
        try:
            r = subprocess.run(
                ["systemctl", "is-active", "gpu-oculink-watchdog"],
                capture_output=True, text=True, timeout=3,
            )
            if r.stdout.strip() == "active":
                return 200, {"ok": True, "reason": "service active"}
        except (FileNotFoundError, subprocess.SubprocessError, OSError):
            pass
        # Fallback: log file exists at the configured path
        log = os.path.expanduser(ctx["config"].get("OCULINK_WATCHDOG_LOG", "~/gpu-watchdog.log"))
        if os.path.isfile(log):
            return 200, {"ok": True, "reason": f"watchdog log present at {log}"}
        return 200, {"ok": False, "reason": "service not active and no watchdog log found"}

    if module_name == "telegram_alerts":
        token = ctx["config"].get("TG_TOKEN", "")
        chat_id = ctx["config"].get("TG_CHAT", "")
        if token and chat_id:
            return 200, {"ok": True, "reason": "token and chat_id present in secrets.env"}
        return 200, {"ok": False, "reason": "token or chat_id missing — fill via Alerts tab"}

    return 400, {"ok": False, "error": f"unknown module: {module_name}"}


def handle_setup_save(ctx: dict, payload: dict) -> Response:
    """Save the user's wizard choices to ~/.config/gpu-dashboard/config.env.

    Payload schema:
      {
        "modules": {"power_limit": bool, "clock_offsets": bool, ...},
        "port": int (optional, default 9999),
        "bind": str (optional, default "0.0.0.0"),
        "power_default": int (optional, default 250),
      }
    """
    from ..install import generate_config_env

    choices = payload.get("modules") or {}
    if not isinstance(choices, dict):
        return 400, {"ok": False, "error": "modules must be a dict"}

    try:
        port = int(payload.get("port", 9999))
        power_default = int(payload.get("power_default", 250))
    except (ValueError, TypeError):
        return 400, {"ok": False, "error": "port and power_default must be integers"}

    bind = str(payload.get("bind", "0.0.0.0"))

    # Optional LLM_SERVER_URL — empty string = "Standard mode" (no LLM monitoring)
    llm_url_raw = payload.get("llm_server_url", "")
    llm_server_url = str(llm_url_raw).strip() if llm_url_raw else ""
    # Basic validation — must be http(s) URL if provided
    if llm_server_url and not (llm_server_url.startswith("http://") or llm_server_url.startswith("https://")):
        return 400, {"ok": False, "error": "llm_server_url must start with http:// or https://"}

    config_path = os.path.expanduser("~/.config/gpu-dashboard/config.env")
    os.makedirs(os.path.dirname(config_path), exist_ok=True)
    content = generate_config_env(
        choices, port=port, power_default=power_default, bind=bind,
        llm_server_url=llm_server_url,
    )
    with open(config_path, "w") as f:
        f.write(content)
    return 200, {"ok": True, "path": config_path}


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


# ─── R&D #7.10 — Prometheus AlertManager rules export ────────────────────────
def _alert_consecutive_to_for(min_n: int, interval_s: int) -> str:
    """Translate 'N consecutive samples at <interval>s' into Prom 'for: Xs'."""
    return f"{int(min_n) * int(interval_s)}s"


def build_alertmanager_rules_yaml(cfg) -> str:
    """Build a Prometheus rules.yaml from the current ALERT_* config.

    Maps each threshold to a corresponding alert rule on the metrics
    exposed by /metrics (R&D #4.1). Uses our gpu_<x> series names.

    Returns a complete YAML payload ready to drop into a Prom
    rule_files dir or referenced from prometheus.yml.
    """
    interval = cfg.get_int("ALERT_MONITOR_INTERVAL", default=30)
    min_n = cfg.get_int("ALERT_MIN_CONSECUTIVE", default=3)
    for_dur = _alert_consecutive_to_for(min_n, interval)

    gpu_temp = cfg.get_int("ALERT_GPU_TEMP_THRESHOLD", default=85)
    fan_pct = cfg.get_int("ALERT_FAN_PCT_THRESHOLD", default=95)
    vram_pct = cfg.get_int("ALERT_VRAM_PCT_THRESHOLD", default=90)

    lines = [
        "# Prometheus AlertManager rules — generated by gpu-dashboard (R&D #7.10).",
        "# Drop in prometheus rule_files or load with promtool check rules.",
        "groups:",
        "- name: gpu-dashboard",
        f"  interval: {interval}s",
        "  rules:",
        "",
        "  - alert: GpuTempHigh",
        f"    expr: gpu_temp_celsius > {gpu_temp}",
        f"    for: {for_dur}",
        "    labels:",
        "      severity: warning",
        "      source: gpu-dashboard",
        "    annotations:",
        f'      summary: "GPU {{{{ $labels.gpu }}}} temperature > {gpu_temp}°C ({{{{ $value }}}}°C)"',
        f'      description: "GPU {{{{ $labels.name }}}} has been above {gpu_temp}°C for {for_dur}. Check cooling / fan curve / workload."',
        "",
        "  - alert: GpuFanHigh",
        f"    expr: gpu_fan_speed_ratio > {fan_pct / 100.0:.2f}",
        f"    for: {for_dur}",
        "    labels:",
        "      severity: warning",
        "      source: gpu-dashboard",
        "    annotations:",
        f'      summary: "GPU {{{{ $labels.gpu }}}} fan > {fan_pct}% sustained"',
        f'      description: "Fan {{{{ $labels.fan }}}} on GPU {{{{ $labels.gpu }}}} stuck above {fan_pct}% — check thermal load."',
        "",
        "  - alert: GpuVramPctHigh",
        "    expr: (gpu_memory_used_bytes / gpu_memory_total_bytes) * 100 > " + str(vram_pct),
        f"    for: {for_dur}",
        "    labels:",
        "      severity: warning",
        "      source: gpu-dashboard",
        "    annotations:",
        f'      summary: "GPU {{{{ $labels.gpu }}}} VRAM > {vram_pct}%"',
        f'      description: "VRAM utilization above {vram_pct}% for {for_dur}. Risk of OOM-killed processes."',
        "",
        "  - alert: GpuOffBus",
        '    expr: absent(gpu_temp_celsius) or (rate(gpu_temp_celsius[1m]) == 0 and gpu_temp_celsius == 0)',
        "    for: 2m",
        "    labels:",
        "      severity: critical",
        "      source: gpu-dashboard",
        "    annotations:",
        '      summary: "GPU disappeared from bus or sampler stuck"',
        '      description: "No temperature sample in the last 2 minutes. nvidia-smi may have failed / PCIe link dropped."',
        "",
        "  - alert: GpuPcieDowngrade",
        '    expr: gpu_pcie_link_gen < on(gpu) group_left() (gpu_pcie_link_gen_max or vector(4))',
        "    for: 1m",
        "    labels:",
        "      severity: warning",
        "      source: gpu-dashboard",
        "    annotations:",
        '      summary: "PCIe link generation below maximum"',
        '      description: "GPU {{ $labels.gpu }} negotiated Gen {{ $value }} — investigate cable / slot / power-saving."',
    ]
    return "\n".join(lines) + "\n"


def handle_alertmanager_rules(ctx: dict) -> Tuple[int, str]:
    """GET /api/alertmanager/rules.yaml — text/yaml download."""
    return 200, build_alertmanager_rules_yaml(ctx["config"])


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
def _read_pid_cgroup(pid: int) -> Optional[str]:
    """Read /proc/<pid>/cgroup and return the cgroup v2 path (or v1 main path).

    Format examples :
      v2 : '0::/system.slice/llama-server.service'
      v1 : '12:memory:/user.slice/user-1000.slice/...'

    Returns the first sensible path or None on error.
    """
    try:
        with open(f"/proc/{pid}/cgroup") as f:
            for line in f:
                parts = line.strip().split(":", 2)
                if len(parts) >= 3:
                    return parts[2]
    except OSError:
        pass
    return None


def _normalize_cgroup(path: str) -> str:
    """Reduce a cgroup path to its meaningful slice / scope name.
    Examples :
      /system.slice/llama-server.service → system.slice/llama-server.service
      /user.slice/user-1000.slice/user@1000.service/app.slice/firefox.service
        → user.slice/firefox.service
      / (root) → root
    """
    if not path or path == "/":
        return "root"
    p = path.strip("/")
    # Collapse user-XXXX.slice / user@XXXX.service noise — keep first slice + final unit
    parts = p.split("/")
    if len(parts) == 1:
        return parts[0]
    # First slice (system.slice / user.slice / ...) + last segment
    return f"{parts[0]}/{parts[-1]}"


def handle_cgroup_power(ctx: dict) -> Response:
    """Attribute total GPU power to cgroups via per-PID SM% share.

    Algorithm :
      1. Read total board power via nvidia-smi
      2. Read per-PID SM% via nvidia-smi pmon
      3. For each PID with SM% > 0, resolve cgroup
      4. Sum SM% per cgroup → est_watts = total * (cgroup_sm / total_sm)
      5. If total SM% == 0 (all idle), attribute proportionally by VRAM
         (fb column) so the user still sees who is HOLDING the GPU

    Response :
      {ok, available, total_power_w, total_sm_pct,
       cgroups: [{name, pids: [...], sm_pct, vram_mib, est_watts}]}
    """
    # Read total power
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=power.draw", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=2,
        )
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        return 200, {"ok": True, "available": False}
    if r.returncode != 0 or not r.stdout.strip():
        return 200, {"ok": True, "available": False}
    try:
        total_power = float(r.stdout.strip().splitlines()[0])
    except (ValueError, IndexError):
        return 200, {"ok": True, "available": False}

    # Read per-PID pmon (sm + fb columns)
    try:
        r = subprocess.run(
            ["nvidia-smi", "pmon", "-c", "1", "-s", "um"],
            capture_output=True, text=True, timeout=2,
        )
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        return 200, {"ok": True, "available": False}
    if r.returncode != 0:
        return 200, {"ok": True, "available": False}

    # Aggregate by cgroup
    cgroups: dict = {}
    total_sm = 0.0
    for line in r.stdout.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # Columns : gpu pid type sm mem enc dec jpg ofa fb ccpm command
        parts = line.split()
        if len(parts) < 11:
            continue
        try:
            pid = int(parts[1])
        except ValueError:
            continue
        sm_pct = 0.0
        try:
            sm_pct = float(parts[3])
        except ValueError:
            pass  # "-" → 0
        vram_mib = 0
        try:
            vram_mib = int(parts[9])
        except ValueError:
            pass

        cg_path = _read_pid_cgroup(pid)
        cg_name = _normalize_cgroup(cg_path) if cg_path else f"pid:{pid}"
        cmd = parts[-1] if len(parts) > 11 else ""

        entry = cgroups.setdefault(cg_name, {
            "name": cg_name, "pids": [], "sm_pct": 0.0,
            "vram_mib": 0, "commands": [],
        })
        entry["pids"].append(pid)
        entry["sm_pct"] += sm_pct
        entry["vram_mib"] += vram_mib
        if cmd and cmd not in entry["commands"]:
            entry["commands"].append(cmd)
        total_sm += sm_pct

    # Compute est_watts
    total_vram = sum(c["vram_mib"] for c in cgroups.values()) or 1
    result_list = []
    for name, c in cgroups.items():
        if total_sm > 0:
            # Distribute by SM% share
            est_w = round(total_power * (c["sm_pct"] / total_sm), 2)
        else:
            # All idle — distribute by VRAM share so user sees who's holding
            est_w = round(total_power * (c["vram_mib"] / total_vram), 2)
        result_list.append({
            "name": name,
            "pids": sorted(c["pids"]),
            "commands": c["commands"],
            "sm_pct": round(c["sm_pct"], 2),
            "vram_mib": c["vram_mib"],
            "est_watts": est_w,
        })
    # Sort by est_watts desc
    result_list.sort(key=lambda x: x["est_watts"], reverse=True)

    return 200, {
        "ok": True,
        "available": True,
        "total_power_w": round(total_power, 2),
        "total_sm_pct": round(total_sm, 2),
        "cgroups": result_list,
    }


# ─── R&D #6.3 — System-context sidecar (CPU/iowait/swap/load) ────────────────
# Cached previous readings to compute deltas between calls.
_LAST_CPU_LINE: Optional[list] = None
_LAST_CPU_TS: float = 0.0
_LAST_VMSTAT: dict = {}
_LAST_VMSTAT_TS: float = 0.0


def _read_proc_stat_cpu() -> Optional[list]:
    """Return /proc/stat's aggregate cpu line as list of 10 ints, or None."""
    try:
        with open("/proc/stat") as f:
            line = f.readline()
        if not line.startswith("cpu "):
            return None
        parts = line.split()[1:]
        return [int(p) for p in parts[:10]]  # user nice system idle iowait irq softirq steal guest guest_nice
    except (OSError, ValueError):
        return None


def _read_proc_loadavg() -> Optional[tuple]:
    """Return (load1, load5, load15) or None."""
    try:
        with open("/proc/loadavg") as f:
            parts = f.read().split()
        return float(parts[0]), float(parts[1]), float(parts[2])
    except (OSError, ValueError, IndexError):
        return None


def _read_proc_vmstat() -> dict:
    """Parse /proc/vmstat — pswpin / pswpout / pgmajfault are interesting."""
    out = {}
    try:
        with open("/proc/vmstat") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 2:
                    out[parts[0]] = int(parts[1])
    except (OSError, ValueError):
        pass
    return out


def _read_proc_meminfo() -> dict:
    """Return current memory + swap usage in KB."""
    out = {}
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 2 and parts[0].endswith(":"):
                    try:
                        out[parts[0].rstrip(":")] = int(parts[1])
                    except ValueError:
                        pass
    except OSError:
        pass
    return out


def handle_sys_context(ctx: dict) -> Response:
    """Snapshot system metrics that correlate with GPU stalls.

    Returns CPU usage (incl iowait%), load avg, swap activity, RAM used.

    CPU% / iowait% computed as delta vs last call's /proc/stat snapshot.
    First call returns CPU%=null (no baseline yet) but everything else
    is fine.

    pswp_in_per_sec / pgmajfault_per_sec computed similarly from
    /proc/vmstat deltas.
    """
    global _LAST_CPU_LINE, _LAST_CPU_TS, _LAST_VMSTAT, _LAST_VMSTAT_TS

    now = time.time()
    cur_cpu = _read_proc_stat_cpu()
    cpu_pct = None
    iowait_pct = None
    if cur_cpu is not None and _LAST_CPU_LINE is not None:
        diffs = [cur - prev for cur, prev in zip(cur_cpu, _LAST_CPU_LINE)]
        total = sum(diffs)
        if total > 0:
            idle = diffs[3] if len(diffs) > 3 else 0
            iowait = diffs[4] if len(diffs) > 4 else 0
            cpu_pct = round(100.0 * (total - idle) / total, 1)
            iowait_pct = round(100.0 * iowait / total, 1)
    _LAST_CPU_LINE = cur_cpu
    _LAST_CPU_TS = now

    cur_vm = _read_proc_vmstat()
    pswpin_ps = None
    pgmajfault_ps = None
    if _LAST_VMSTAT and _LAST_VMSTAT_TS:
        elapsed = max(0.001, now - _LAST_VMSTAT_TS)
        if "pswpin" in cur_vm and "pswpin" in _LAST_VMSTAT:
            pswpin_ps = max(0, round((cur_vm["pswpin"] - _LAST_VMSTAT["pswpin"]) / elapsed, 2))
        if "pgmajfault" in cur_vm and "pgmajfault" in _LAST_VMSTAT:
            pgmajfault_ps = max(0, round((cur_vm["pgmajfault"] - _LAST_VMSTAT["pgmajfault"]) / elapsed, 2))
    _LAST_VMSTAT = {k: cur_vm.get(k, 0) for k in ("pswpin", "pswpout", "pgmajfault")}
    _LAST_VMSTAT_TS = now

    loadavg = _read_proc_loadavg()
    mem = _read_proc_meminfo()
    mem_total_kb = mem.get("MemTotal", 0)
    mem_free_kb = mem.get("MemAvailable", mem.get("MemFree", 0))
    mem_used_pct = None
    if mem_total_kb > 0:
        mem_used_pct = round(100.0 * (mem_total_kb - mem_free_kb) / mem_total_kb, 1)
    swap_total_kb = mem.get("SwapTotal", 0)
    swap_free_kb = mem.get("SwapFree", 0)
    swap_used_pct = None
    if swap_total_kb > 0:
        swap_used_pct = round(100.0 * (swap_total_kb - swap_free_kb) / swap_total_kb, 1)

    return 200, {
        "ok": True,
        "available": True,
        "cpu_pct": cpu_pct,
        "iowait_pct": iowait_pct,
        "loadavg_1": loadavg[0] if loadavg else None,
        "loadavg_5": loadavg[1] if loadavg else None,
        "loadavg_15": loadavg[2] if loadavg else None,
        "mem_used_pct": mem_used_pct,
        "mem_total_kb": mem_total_kb,
        "swap_used_pct": swap_used_pct,
        "swap_total_kb": swap_total_kb,
        "pswpin_per_sec": pswpin_ps,
        "pgmajfault_per_sec": pgmajfault_ps,
    }


# ─── R&D #6.1 — Unified notification hub (Apprise-style fanout) ──────────────
def handle_notif_channels_list(ctx: dict) -> Response:
    """Return all configured channels (with secrets masked)."""
    from ..modules import notif_hub as _nh
    channels = _nh.load_channels()
    out: list = []
    for ch in channels:
        masked = dict(ch)
        # Mask sensitive fields
        for k in ("token", "password", "user", "url"):
            if k in masked and isinstance(masked[k], str) and len(masked[k]) > 8:
                masked[k] = masked[k][:6] + "…" + masked[k][-3:]
        out.append(masked)
    return 200, {"ok": True, "channels": out,
                 "types_supported": list(_nh._ADAPTERS.keys())}


def handle_notif_channel_save(ctx: dict, payload: dict) -> Response:
    """Create or update a channel by id. Payload :
      {id, type, name, enabled, min_level, gpu_filter, quiet_hours, ...adapter-specific...}
    Or {delete: id}."""
    from ..modules import notif_hub as _nh
    if not isinstance(payload, dict):
        return 400, {"ok": False, "error": "payload must be a dict"}
    channels = _nh.load_channels()
    if "delete" in payload:
        target = str(payload["delete"])
        channels = [c for c in channels if c.get("id") != target]
        _nh.save_channels(channels)
        return 200, {"ok": True, "deleted": target}

    cid = str(payload.get("id", "")).strip()
    ctype = str(payload.get("type", "")).strip()
    if not cid:
        return 400, {"ok": False, "error": "id required"}
    if ctype not in _nh._ADAPTERS:
        return 400, {"ok": False, "error": f"unknown type. Available : {list(_nh._ADAPTERS.keys())}"}

    # Replace existing or append
    new_channel = {k: v for k, v in payload.items() if k != "_test"}
    channels = [c for c in channels if c.get("id") != cid] + [new_channel]
    _nh.save_channels(channels)
    return 200, {"ok": True, "id": cid}


def handle_notif_channel_test(ctx: dict, payload: dict) -> Response:
    """Fire a test notification to the channel specified in payload (no save).
    Useful before saving — user can validate the credentials inline."""
    from ..modules import notif_hub as _nh
    if not isinstance(payload, dict) or "type" not in payload:
        return 400, {"ok": False, "error": "payload requires 'type'"}
    ok, msg = _nh.send_test(payload)
    code = 200 if ok else 502
    return code, {"ok": ok, "msg": msg}


# Handlers from L2766-L2778 moved to api/diagnostics.py (cycle 7a)



# Handlers from L2781-L2855 moved to api/diagnostics.py (cycle 7a)



# ─── R&D #6.2 — Deadman heartbeat (inbound + outbound) ───────────────────────
def _heartbeats_path() -> str:
    return os.path.expanduser("~/.config/gpu-dashboard/heartbeats.json")


def _load_heartbeats() -> dict:
    """Persist shape : {tokens: {<token>: {name, interval_s, grace_s, last_seen_ts}}}.
    Returns {tokens: {}} if missing."""
    path = _heartbeats_path()
    if not os.path.exists(path):
        return {"tokens": {}}
    try:
        with open(path) as f:
            data = json.load(f)
        if not isinstance(data, dict) or "tokens" not in data:
            return {"tokens": {}}
        return data
    except (OSError, json.JSONDecodeError):
        return {"tokens": {}}


def _save_heartbeats(data: dict) -> None:
    path = _heartbeats_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def handle_heartbeat_list(ctx: dict) -> Response:
    """List all heartbeat tokens + their current status (ok/late/never)."""
    data = _load_heartbeats()
    now = int(time.time())
    items: list = []
    for token, hb in (data.get("tokens") or {}).items():
        last_seen = hb.get("last_seen_ts")
        interval = int(hb.get("interval_s", 3600))
        grace = int(hb.get("grace_s", 300))
        if last_seen is None:
            status = "never"
            age_s = None
        else:
            age_s = now - int(last_seen)
            status = "ok" if age_s <= interval + grace else "late"
        items.append({
            "token": token,
            "name": hb.get("name", token),
            "interval_s": interval,
            "grace_s": grace,
            "last_seen_ts": last_seen,
            "age_s": age_s,
            "status": status,
        })
    items.sort(key=lambda x: x.get("age_s") if x.get("age_s") is not None else -1, reverse=True)
    return 200, {"ok": True, "heartbeats": items}


def handle_heartbeat_ping(ctx: dict, token: str) -> Response:
    """Inbound : record a ping from a training script.

    Usage : `curl -fsS http://host/api/heartbeat/<token>`
    Token must already exist (created via POST /api/heartbeat/config).
    """
    data = _load_heartbeats()
    if token not in (data.get("tokens") or {}):
        return 404, {"ok": False, "error": "unknown heartbeat token"}
    data["tokens"][token]["last_seen_ts"] = int(time.time())
    _save_heartbeats(data)
    return 200, {"ok": True, "token": token, "ts": data["tokens"][token]["last_seen_ts"]}


def handle_heartbeat_config(ctx: dict, payload: dict) -> Response:
    """Create or update a heartbeat token.

    Payload :
      {token: str, name: str, interval_s: int, grace_s: int}
      OR {delete: token}
    """
    if not isinstance(payload, dict):
        return 400, {"ok": False, "error": "payload must be a dict"}
    data = _load_heartbeats()
    if "delete" in payload:
        token = str(payload["delete"])
        if token in data.get("tokens", {}):
            del data["tokens"][token]
            _save_heartbeats(data)
        return 200, {"ok": True, "deleted": token}
    token = str(payload.get("token", "")).strip()
    if not token or not all(c.isalnum() or c in "-_" for c in token):
        return 400, {"ok": False, "error": "token must be alphanumeric + - _"}
    name = str(payload.get("name", token)).strip()
    try:
        interval_s = int(payload.get("interval_s", 3600))
        grace_s = int(payload.get("grace_s", 300))
    except (TypeError, ValueError):
        return 400, {"ok": False, "error": "interval_s and grace_s must be integers"}
    if interval_s < 30 or interval_s > 7 * 86400:
        return 400, {"ok": False, "error": "interval_s out of range [30, 604800]"}
    if grace_s < 0 or grace_s > interval_s:
        return 400, {"ok": False, "error": "grace_s must be in [0, interval_s]"}
    existing = data.get("tokens", {}).get(token, {})
    data.setdefault("tokens", {})[token] = {
        "name": name,
        "interval_s": interval_s,
        "grace_s": grace_s,
        "last_seen_ts": existing.get("last_seen_ts"),  # preserve on edit
    }
    _save_heartbeats(data)
    return 200, {"ok": True, "token": token}


# ─── R&D #5.1 — Thermal headroom coach ───────────────────────────────────────
def _linear_fit(xs: list, ys: list) -> tuple:
    """Plain least-squares linear regression. Returns (slope, intercept).
    Returns (0, mean(ys)) for degenerate input (constant x or too few points)."""
    n = len(xs)
    if n < 2:
        return 0.0, (ys[0] if ys else 0.0)
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    var_x = sum((x - mean_x) ** 2 for x in xs)
    if var_x == 0:
        return 0.0, mean_y
    cov_xy = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    slope = cov_xy / var_x
    intercept = mean_y - slope * mean_x
    return slope, intercept


def _r_squared(xs: list, ys: list, slope: float, intercept: float) -> float:
    """Coefficient of determination R² for the linear fit (0=no fit, 1=perfect).
    Used as the prediction confidence indicator for R&D #8.2."""
    n = len(ys)
    if n < 2:
        return 0.0
    mean_y = sum(ys) / n
    ss_tot = sum((y - mean_y) ** 2 for y in ys)
    if ss_tot == 0:
        return 1.0  # constant y → perfect fit by convention (no variance to explain)
    ss_res = sum((y - (slope * x + intercept)) ** 2 for x, y in zip(xs, ys))
    return max(0.0, 1.0 - ss_res / ss_tot)


def handle_thermal_coach(ctx: dict) -> Response:
    """Surfaces a decision : headroom + projected time-to-throttle from the
    last 5min of samples.

    Logic :
      - Pull last 60 samples (~5 min at 5s interval) from the sampler
      - Linear regression on temp(t)
      - Slowdown temp default 83°C (RTX 3090/4090 typical) — could be read
        from NVML in a future iteration
      - Headroom = slowdown_temp - current_temp
      - Time-to-throttle = (slowdown_temp - last_temp) / slope_per_sec
        (clamped : ∞ if slope <= 0, < 0 if already over)
      - Suggested fan delta : if headroom > 15°C and trend is flat/falling,
        fans can be 5-10% gentler (acoustic win)
    """
    sampler = ctx.get("sampler")
    if not sampler:
        return 200, {"ok": True, "available": False, "reason": "no sampler"}

    snap = sampler.snapshot()
    if not snap or len(snap) < 5:
        return 200, {"ok": True, "available": False,
                     "reason": "not enough samples yet (need 5+)"}

    # Last 5 minutes : assume 5s sampling → 60 samples max
    recent = snap[-60:] if len(snap) > 60 else snap
    # The in-memory ring buffer uses "ts" as a 'HH:MM:SS' string (not epoch).
    # Use sample INDEX as the x-axis. Sampling interval defaults to 5s.
    # slope is then °C per sample ; multiply by (60/interval) for °C/min,
    # and time-to-throttle is in samples → seconds.
    sample_interval_s = float(ctx.get("config", None).get_int("DASHBOARD_REFRESH_INTERVAL", default=5)
                              if ctx.get("config") else 5)
    xs = []
    ys = []
    for i, s in enumerate(recent):
        temp = s.get("temp")
        if temp is None:
            continue
        xs.append(float(i))
        ys.append(float(temp))
    if len(xs) < 3:
        return 200, {"ok": True, "available": False, "reason": "need 3+ valid temp samples"}

    slope_per_sample, intercept = _linear_fit(xs, ys)
    slope = slope_per_sample / sample_interval_s  # °C per second
    confidence = round(_r_squared(xs, ys, slope_per_sample, intercept), 3)
    current_temp = ys[-1]
    slowdown_temp = 83.0  # default for consumer Ampere/Ada
    headroom_c = round(slowdown_temp - current_temp, 1)

    # Time-to-throttle projection
    if slope <= 0 or current_temp >= slowdown_temp:
        projected_throttle_s = None  # not heating (or already over)
    else:
        projected_throttle_s = int((slowdown_temp - current_temp) / slope)

    # Decision : suggest fan delta
    suggested_fan_delta_pct = 0
    suggested_msg_key = "stable"
    if headroom_c > 25 and slope <= 0.005:
        suggested_fan_delta_pct = -10
        suggested_msg_key = "fan_can_be_gentler"
    elif headroom_c > 15 and slope <= 0.01:
        suggested_fan_delta_pct = -5
        suggested_msg_key = "fan_slight_gentler"
    elif headroom_c < 5 or (projected_throttle_s is not None and projected_throttle_s < 120):
        suggested_fan_delta_pct = +10
        suggested_msg_key = "fan_needs_help"
    elif projected_throttle_s is not None and projected_throttle_s < 600:
        suggested_fan_delta_pct = +5
        suggested_msg_key = "warming_up"

    # R&D #8.2 — fire notification hub if imminent throttle + high confidence
    if (projected_throttle_s is not None and projected_throttle_s < 120
            and confidence > 0.5):
        try:
            from ..modules import notif_hub as _nh
            _nh.send(
                level="warning",
                title="⚠️ GPU throttle imminent",
                body=(f"Projected throttle in {projected_throttle_s}s "
                      f"(slope {round(slope * 60, 2)}°C/min, R²={confidence}). "
                      f"Current {round(current_temp, 1)}°C, headroom {headroom_c}°C."),
            )
        except Exception:
            pass  # don't fail the API call on notification errors

    return 200, {
        "ok": True,
        "available": True,
        "current_temp_c": round(current_temp, 1),
        "slowdown_temp_c": slowdown_temp,
        "headroom_c": headroom_c,
        "slope_c_per_min": round(slope * 60, 3),
        "projected_throttle_s": projected_throttle_s,
        "confidence": confidence,
        "suggested_fan_delta_pct": suggested_fan_delta_pct,
        "suggested_msg_key": suggested_msg_key,
        "sample_count": len(xs),
    }


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



# ─── R&D #4.1 — Prometheus /metrics endpoint ───────────────────────────────
def handle_prometheus_metrics(ctx: dict) -> Tuple[int, str]:
    """OpenMetrics-formatted live snapshot for every detected GPU.

    Returns plain text (not JSON). Designed to be scrape-target for Prometheus,
    Grafana Agent, VictoriaMetrics or any OpenMetrics-compatible collector.

    Series exposed (one sample per GPU) :
      - gpu_temp_celsius{gpu="0",name="…",uuid="…"}
      - gpu_util_ratio{gpu="0",...}                # 0..1
      - gpu_power_watts{gpu="0",...}
      - gpu_power_limit_watts{gpu="0",...}
      - gpu_memory_used_bytes{gpu="0",...}
      - gpu_memory_total_bytes{gpu="0",...}
      - gpu_fan_speed_ratio{gpu="0",fan="0",...}   # 0..1
      - gpu_fan_rpm{gpu="0",fan="0",...}
      - gpu_pcie_link_gen{gpu="0",...}
      - gpu_pcie_link_width{gpu="0",...}
      - gpu_dashboard_info{version="0.3.0"}        # gauge=1, info via labels

    Permissive : missing fields are silently skipped (don't break the scrape).
    """
    from .. import __version__ as VERSION

    lines: list[str] = []

    def add(name: str, help_text: str, type_: str = "gauge") -> None:
        lines.append(f"# HELP {name} {help_text}")
        lines.append(f"# TYPE {name} {type_}")

    def fmt_labels(d: dict) -> str:
        # Escape backslashes and double-quotes per OpenMetrics
        parts = []
        for k, v in d.items():
            sv = str(v).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
            parts.append(f'{k}="{sv}"')
        return "{" + ",".join(parts) + "}"

    # Per-GPU live snapshot
    gpus = _gpus_available()
    add("gpu_temp_celsius", "GPU core temperature in °C")
    add("gpu_util_ratio", "GPU utilization (0..1)")
    add("gpu_power_watts", "Current power draw in Watts")
    add("gpu_power_limit_watts", "Current power limit in Watts")
    add("gpu_memory_used_bytes", "VRAM used in bytes")
    add("gpu_memory_total_bytes", "VRAM total in bytes")
    add("gpu_pcie_link_gen", "Current PCIe link generation")
    add("gpu_pcie_link_width", "Current PCIe link width")
    add("gpu_fan_speed_ratio", "Fan speed (0..1)")
    add("gpu_fan_rpm", "Fan speed in RPM")
    add("gpu_dashboard_info", "Build info (labels carry version)", "gauge")

    for gpu in gpus:
        idx = gpu.get("index", 0)
        snap = _gpu_card_snapshot(gpu_index=idx)
        if not snap or not snap.get("alive"):
            continue
        labels = {
            "gpu": str(idx),
            "name": snap.get("name", "unknown"),
            "uuid": snap.get("uuid", ""),
        }
        L = fmt_labels(labels)
        if snap.get("temp") is not None:
            lines.append(f"gpu_temp_celsius{L} {snap['temp']}")
        if snap.get("util_gpu") is not None:
            lines.append(f"gpu_util_ratio{L} {snap['util_gpu'] / 100.0:.4f}")
        if snap.get("power") is not None:
            lines.append(f"gpu_power_watts{L} {snap['power']:.2f}")
        if snap.get("power_limit") is not None:
            lines.append(f"gpu_power_limit_watts{L} {snap['power_limit']:.2f}")
        if snap.get("mem_used_mib") is not None:
            lines.append(f"gpu_memory_used_bytes{L} {int(snap['mem_used_mib']) * 1024 * 1024}")
        if snap.get("mem_total_mib") is not None:
            lines.append(f"gpu_memory_total_bytes{L} {int(snap['mem_total_mib']) * 1024 * 1024}")
        if snap.get("pcie_gen") is not None:
            lines.append(f"gpu_pcie_link_gen{L} {snap['pcie_gen']}")
        if snap.get("pcie_width") is not None:
            lines.append(f"gpu_pcie_link_width{L} {snap['pcie_width']}")
        # Per-fan series — only the GPU at gpu_index has fan detail
        for f in (_per_fan_state(ctx["config"]) if idx == 0 else []):
            fan_labels = {**labels, "fan": str(f.get("idx", 0))}
            FL = fmt_labels(fan_labels)
            if f.get("pct") is not None:
                lines.append(f"gpu_fan_speed_ratio{FL} {f['pct'] / 100.0:.4f}")
            if f.get("rpm") is not None:
                lines.append(f"gpu_fan_rpm{FL} {f['rpm']}")

    info_labels = fmt_labels({"version": VERSION})
    lines.append(f"gpu_dashboard_info{info_labels} 1")
    lines.append("")  # trailing newline per OpenMetrics
    return 200, "\n".join(lines)
