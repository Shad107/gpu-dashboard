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
import subprocess
from typing import Any, Tuple

from . import detect
from .modules import power_limit as pl
from .modules import clock_offsets as co
from .modules import telegram_alerts as tg


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
             "utilization.gpu,memory.used,memory.total,temperature.memory,vbios_version",
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
        "mem_temp": mem_temp,         # GDDR junction temp, °C (None if unsupported)
        "vbios_version": vbios,       # str (None if unsupported)
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


def _llm_model_served(cfg) -> str:
    """If a local LLM server URL is configured, fetch the model id from /v1/models."""
    url = cfg.get("LLM_SERVER_URL", "")
    if not url:
        return ""
    import urllib.request, json as _json
    try:
        with urllib.request.urlopen(f"{url.rstrip('/')}/v1/models", timeout=2) as r:
            data = _json.loads(r.read().decode())
            models = data.get("data", [])
            if models:
                return models[0].get("id", "?")
    except Exception:
        pass
    return ""


# ────────────────── POST /api/set-power-limit ──────────────────────────────


def handle_set_power_limit(ctx: dict, payload: dict) -> Response:
    """Apply a new power-limit value via the sudoers wrapper."""
    try:
        watts = int(payload.get("watts", 0))
    except (ValueError, TypeError):
        return 400, {"ok": False, "error": "watts must be an integer"}

    profile = ctx.get("profile") or {}
    wrapper = ctx["config"].get("POWER_LIMIT_WRAPPER", "/usr/local/bin/set-power-limit")

    try:
        result = pl.apply_power_limit(profile, watts, wrapper_path=wrapper)
    except ValueError as e:
        return 400, {"ok": False, "error": str(e)}
    code = 200 if result.get("ok") else 500
    return code, result


# ───────────────────── POST /api/set-offsets ──────────────────────────────


def handle_set_offsets(ctx: dict, payload: dict) -> Response:
    """Apply GPU + memory clock offsets via nvidia-settings."""
    try:
        gpu = int(payload.get("gpu", 0))
        mem = int(payload.get("mem", 0))
    except (ValueError, TypeError):
        return 400, {"ok": False, "error": "gpu and mem must be integers"}

    profile = ctx.get("profile") or {}
    display = ctx["config"].get("CLOCK_OFFSETS_DISPLAY", ":0")
    xauth = ctx["config"].get("CLOCK_OFFSETS_XAUTHORITY") or None

    try:
        result = co.apply_offsets(profile, gpu=gpu, mem=mem, display=display, xauthority=xauth)
    except ValueError as e:
        return 400, {"ok": False, "error": str(e)}
    code = 200 if result.get("ok") else 500
    return code, result


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
    from .config import write_env_file
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


# ────────────────────────── GET /api/llm/lifetime ─────────────────────────


def handle_llm_lifetime(ctx: dict, params: Optional[dict] = None) -> Response:
    """Cumulative LLM stats since the first sample with a tokens count.

    Walks the samples table, sums positive deltas of tokens_total_snapshot
    (negative deltas = llama-server restart, ignored). Returns avg power
    and avg tokens-per-watt over the same window.

    Returns :
      ok: bool
      available: bool         — whether any sample had a tokens count
      since_ts: int | None    — first sample with tokens, in epoch seconds
      latest_snapshot: int    — last seen tokens_total_snapshot
      total_tokens_generated: int  — sum of positive deltas
      restart_count: int      — number of detected counter resets
      avg_power_watts: float
      avg_tokens_per_watt: float | None
    """
    storage = ctx.get("storage")
    if storage is None:
        return 503, {"ok": False, "error": "storage not available"}

    gpu = _parse_gpu_index(params or {})
    samples = storage.get_samples(from_ts=0, gpu_index=gpu)
    total = 0
    restarts = 0
    prev = None
    first_ts = None
    latest = None
    powers: list = []
    for s in samples:
        tok = s.get("tokens_total_snapshot")
        if tok is None:
            continue
        if first_ts is None:
            first_ts = s["ts"]
        latest = tok
        if prev is not None:
            delta = tok - prev
            if delta > 0:
                total += delta
            elif delta < 0:
                restarts += 1
        prev = tok
        if s.get("power"):
            powers.append(s["power"])

    available = first_ts is not None
    avg_power = (sum(powers) / len(powers)) if powers else 0.0

    avg_tpw = None
    if available and latest is not None and powers and total > 0:
        # span : last sample with tokens minus first sample with tokens
        span = max(1, samples[-1]["ts"] - first_ts)
        tps = total / span
        if avg_power > 0:
            avg_tpw = tps / avg_power

    return 200, {
        "ok": True,
        "available": available,
        "since_ts": first_ts,
        "latest_snapshot": latest or 0,
        "total_tokens_generated": total,
        "restart_count": restarts,
        "avg_power_watts": round(avg_power, 2),
        "avg_tokens_per_watt": round(avg_tpw, 4) if avg_tpw else None,
    }


# ────────────────────────── GET /api/llm/perf ─────────────────────────────


def handle_llm_perf(ctx: dict, params: Optional[dict] = None) -> Response:
    """Live + recent tokens-per-second across multiple rolling windows.

    Used by the Stats page sparklines + the LLM card live indicator.
    """
    storage = ctx.get("storage")
    if storage is None:
        return 503, {"ok": False, "error": "storage not available"}

    import time as _time
    now = int(_time.time())

    gpu = _parse_gpu_index(params or {})
    samples = storage.get_samples(from_ts=now - 86400, to_ts=now, gpu_index=gpu)
    token_samples = [s for s in samples if s.get("tokens_total_snapshot") is not None]
    if len(token_samples) < 2:
        return 200, {"ok": True, "available": False}

    def _avg_tps(from_ts: int) -> float:
        window = [s for s in token_samples if s["ts"] >= from_ts]
        if len(window) < 2:
            return 0.0
        total = 0
        for i in range(1, len(window)):
            d = window[i]["tokens_total_snapshot"] - window[i - 1]["tokens_total_snapshot"]
            if d > 0:
                total += d
        span = max(1, window[-1]["ts"] - window[0]["ts"])
        return total / span

    avg_tps_1m  = _avg_tps(now - 60)
    avg_tps_5m  = _avg_tps(now - 300)
    avg_tps_1h  = _avg_tps(now - 3600)
    avg_tps_24h = _avg_tps(now - 86400)

    # 60-bucket sparkline series : 1 min buckets over the last hour
    series = []
    peak_tps = 0.0
    peak_ts = 0
    for bucket_idx in range(60):
        bucket_start = now - (60 - bucket_idx) * 60
        bucket_end = bucket_start + 60
        in_bucket = [s for s in token_samples if bucket_start <= s["ts"] < bucket_end]
        if len(in_bucket) >= 2:
            d = in_bucket[-1]["tokens_total_snapshot"] - in_bucket[0]["tokens_total_snapshot"]
            span = max(1, in_bucket[-1]["ts"] - in_bucket[0]["ts"])
            tps = max(0.0, d / span)
            series.append(round(tps, 2))
            if tps > peak_tps:
                peak_tps = tps
                peak_ts = bucket_end
        else:
            series.append(0.0)

    return 200, {
        "ok": True,
        "available": True,
        "now": now,
        "avg_tps_1m":  round(avg_tps_1m, 2),
        "avg_tps_5m":  round(avg_tps_5m, 2),
        "avg_tps_1h":  round(avg_tps_1h, 2),
        "avg_tps_24h": round(avg_tps_24h, 2),
        "peak_tps":    round(peak_tps, 2),
        "peak_ts":     peak_ts,
        "series_1h":   series,
    }


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


# ────────────────────────── GET /api/power-stats ───────────────────────────


def handle_power_stats(ctx: dict, params: Optional[dict] = None) -> Response:
    """Power aggregates over 24h + 24-point downsampled series + cost today."""
    storage = ctx.get("storage")
    if storage is None:
        return 503, {"ok": False, "error": "storage not available"}
    cfg = ctx.get("config")

    import time as _time
    import datetime as _dt
    now = int(_time.time())
    gpu = _parse_gpu_index(params or {})

    today_start = int(_dt.datetime.fromtimestamp(now).replace(
        hour=0, minute=0, second=0, microsecond=0).timestamp())

    samples_24h = storage.get_samples(from_ts=now - 86400, to_ts=now, gpu_index=gpu)
    powers = [s["power"] for s in samples_24h if s.get("power") is not None]
    avg_w = sum(powers) / len(powers) if powers else 0
    peak_w = max(powers) if powers else 0
    peak_ts = 0
    for s in samples_24h:
        if s.get("power") == peak_w:
            peak_ts = s["ts"]
            break

    today_samples = [s for s in samples_24h if s["ts"] >= today_start and s.get("power") is not None]
    wh = 0.0
    for i in range(1, len(today_samples)):
        prev = today_samples[i - 1]
        cur = today_samples[i]
        dt = min(cur["ts"] - prev["ts"], 300)
        avg = (prev["power"] + cur["power"]) / 2
        wh += avg * dt / 3600
    kwh_today = wh / 1000

    price = 0.25
    currency = "EUR"
    if cfg is not None:
        try:
            price = float(cfg.get("ELECTRICITY_PRICE_EUR_PER_KWH", default="0.25"))
        except (ValueError, TypeError):
            price = 0.25
        currency = cfg.get("ELECTRICITY_CURRENCY", default="EUR") or "EUR"

    cost_today = round(kwh_today * price, 4)

    series = []
    for h in range(24):
        bucket_start = now - (24 - h) * 3600
        bucket_end = bucket_start + 3600
        in_bucket = [s["power"] for s in samples_24h
                     if bucket_start <= s["ts"] < bucket_end and s.get("power") is not None]
        series.append(round(sum(in_bucket) / len(in_bucket), 1) if in_bucket else 0)

    return 200, {
        "ok": True,
        "avg_watts_24h": round(avg_w, 1),
        "peak_watts_24h": round(peak_w, 1),
        "peak_ts": peak_ts,
        "kwh_today": round(kwh_today, 4),
        "cost_today": cost_today,
        "currency": currency,
        "price_per_kwh": price,
        "series_24h": series,
        "samples_count": len(samples_24h),
    }


# ────────────────────────── GET /api/llm/stats ────────────────────────────


def _parse_llamacpp_metrics(text: str) -> dict:
    """Parse a Prometheus-format text dump from llama-server's /metrics.

    Returns a dict of {metric_name_without_namespace: value} for the
    counters and gauges we care about. Ignores HELP/TYPE/comment lines.
    """
    result = {}
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # Lines look like : "llamacpp:tokens_predicted_total 67890"
        # Or with labels  : 'foo{bar="baz"} 1.0'
        parts = line.split()
        if len(parts) < 2:
            continue
        name = parts[0]
        try:
            val = float(parts[-1])
        except (ValueError, TypeError):
            continue
        # Strip namespace prefix (llamacpp:)
        if ":" in name:
            name = name.split(":", 1)[1]
        # Strip label suffix {foo="bar"}
        if "{" in name:
            name = name.split("{", 1)[0]
        # Integer if it looks like one
        result[name] = int(val) if val == int(val) else val
    return result


def _tokens_per_watt(tokens: float, avg_watts: float):
    """Compute tokens/W. Returns None if avg_watts == 0."""
    if avg_watts <= 0:
        return None
    return tokens / avg_watts


def handle_llm_stats(ctx: dict) -> Response:
    """Fetch llama-server /metrics if LLM_SERVER_URL is configured.

    Returns: {available, model, tokens_generated_total, prompt_tokens_total,
             tokens_per_watt_avg (if storage available)}
    """
    cfg = ctx.get("config")
    url = (cfg.get("LLM_SERVER_URL", "") if cfg else "").strip().rstrip("/")
    if not url:
        return 200, {"available": False, "reason": "LLM_SERVER_URL not configured"}

    import urllib.request
    import urllib.error
    try:
        with urllib.request.urlopen(f"{url}/metrics", timeout=2) as r:
            text = r.read().decode("utf-8", errors="replace")
    except Exception as e:
        return 200, {"available": False, "reason": f"unreachable: {e}"}

    parsed = _parse_llamacpp_metrics(text)
    if not parsed:
        return 200, {"available": False, "reason": "no recognized metrics"}

    tokens_gen = parsed.get("tokens_predicted_total", 0)
    tokens_prompt = parsed.get("prompt_tokens_total", 0)

    # tokens/W (efficiency) using last-hour avg power if storage is available
    tokens_per_watt = None
    storage = ctx.get("storage")
    if storage is not None and tokens_gen > 0:
        import time as _t
        now = int(_t.time())
        recent = storage.get_samples(from_ts=now - 3600, to_ts=now)
        powers = [s.get("power") for s in recent if s.get("power")]
        if powers:
            avg_w = sum(powers) / len(powers)
            tokens_per_watt = _tokens_per_watt(tokens_gen, avg_w)

    return 200, {
        "available": True,
        "tokens_generated_total": tokens_gen,
        "prompt_tokens_total": tokens_prompt,
        "tokens_per_watt": round(tokens_per_watt, 2) if tokens_per_watt else None,
        "raw_metrics_count": len(parsed),
    }


# ────────────────────────── GET /api/power-heatmap ────────────────────────


def handle_power_heatmap(ctx: dict, params: dict) -> Response:
    """24-bucket heatmap of avg power + cost by hour-of-day over last N days.

    Useful for spotting patterns like 'training runs every 5am eat €0.50/day'
    or 'weekday afternoons are when inference is most active'.

    Query params : days (default 7)
    Returns :
      days: int
      currency: str
      price_per_kwh: float
      hours: [{hour, avg_watts, kwh_per_hour, cost_per_hour, sample_count}, ...]  (length 24)
    """
    storage = ctx.get("storage")
    if storage is None:
        return 503, {"ok": False, "error": "storage not available"}
    cfg = ctx.get("config")

    try:
        days = int(params.get("days", 7))
    except (ValueError, TypeError):
        return 400, {"ok": False, "error": "days must be integer"}
    if days < 1 or days > 365:
        return 400, {"ok": False, "error": "days out of range [1, 365]"}

    import time as _time
    now = int(_time.time())
    from_ts = now - days * 86400

    # Use storage to query all samples in window (no resampling)
    gpu = _parse_gpu_index(params)
    samples = storage.get_samples(from_ts=from_ts, to_ts=now, gpu_index=gpu)

    # Bucket by hour-of-day using local time
    import datetime as _dt
    buckets = [{"watts_sum": 0.0, "count": 0} for _ in range(24)]
    for s in samples:
        if s.get("power") is None:
            continue
        ts = s.get("ts", 0)
        h = _dt.datetime.fromtimestamp(ts).hour
        buckets[h]["watts_sum"] += s["power"]
        buckets[h]["count"] += 1

    # Rate + currency
    price = 0.25
    currency = "EUR"
    if cfg is not None:
        try:
            price = float(cfg.get("ELECTRICITY_PRICE_EUR_PER_KWH", default="0.25"))
        except (ValueError, TypeError):
            price = 0.25
        currency = cfg.get("ELECTRICITY_CURRENCY", default="EUR") or "EUR"

    hours_out = []
    for h in range(24):
        b = buckets[h]
        avg_w = (b["watts_sum"] / b["count"]) if b["count"] > 0 else 0.0
        kwh = avg_w / 1000.0  # kWh consumed during 1 hour at this avg power
        cost = kwh * price
        hours_out.append({
            "hour": h,
            "avg_watts": round(avg_w, 1),
            "kwh_per_hour": round(kwh, 4),
            "cost_per_hour": round(cost, 4),
            "sample_count": b["count"],
        })

    return 200, {
        "ok": True,
        "days": days,
        "currency": currency,
        "price_per_kwh": price,
        "hours": hours_out,
    }


# ────────────────────────── POST /api/electricity/config ──────────────────


def handle_electricity_config(ctx: dict, payload: dict) -> Response:
    """Update electricity price + currency at runtime.

    Persists to config.env so the change survives restart. Also updates the
    in-memory Config so /api/electricity uses the new rate immediately
    (no restart required for this specific setting).
    """
    cfg = ctx.get("config")
    if cfg is None:
        return 500, {"ok": False, "error": "no config loaded"}

    try:
        price = float(payload.get("price_per_kwh"))
    except (ValueError, TypeError):
        return 400, {"ok": False, "error": "price_per_kwh must be a number"}
    if not (0 < price < 5):  # sanity check (0 < x < 5 €/kWh)
        return 400, {"ok": False, "error": "price_per_kwh out of reasonable range (0, 5)"}

    currency = str(payload.get("currency", "EUR")).strip().upper() or "EUR"
    if len(currency) > 4:
        return 400, {"ok": False, "error": "currency code too long"}

    # 1) Update in-memory Config (effective immediately)
    cfg.set("ELECTRICITY_PRICE_EUR_PER_KWH", price)
    cfg.set("ELECTRICITY_CURRENCY", currency)

    # 2) Persist to config.env so it survives restart
    config_path = ctx.get("config_path") or os.path.expanduser(
        "~/.config/gpu-dashboard/config.env"
    )
    try:
        os.makedirs(os.path.dirname(config_path), exist_ok=True)
        # Read existing, update / add the keys, write back
        existing = {}
        if os.path.isfile(config_path):
            from .config import parse_env_file
            existing = parse_env_file(config_path)
        existing["ELECTRICITY_PRICE_EUR_PER_KWH"] = str(price)
        existing["ELECTRICITY_CURRENCY"] = currency
        from .config import write_env_file
        write_env_file(config_path, existing,
                       header="# Auto-updated by gpu-dashboard /api/electricity/config")
    except OSError as e:
        return 500, {"ok": False, "error": f"could not write config.env: {e}"}

    return 200, {"ok": True, "price_per_kwh": price, "currency": currency,
                 "config_path": config_path}


# ────────────────────────── GET /api/electricity ──────────────────────────


def handle_electricity(ctx: dict, params: dict) -> Response:
    """Compute energy consumed + cost over a window from stored samples.

    Default window is the last 1 hour. Returns avg power, kWh consumed,
    cost in the configured currency, plus 24h + 30d extrapolations.
    """
    storage = ctx.get("storage")
    if storage is None:
        return 503, {"ok": False, "error": "storage not available"}
    cfg = ctx.get("config")
    try:
        window = int(params.get("since", 3600))
    except (ValueError, TypeError):
        return 400, {"ok": False, "error": "since must be integer (seconds)"}

    import time as _time
    now = int(_time.time())
    from_ts = now - window
    gpu = _parse_gpu_index(params)
    samples = storage.get_samples(from_ts=from_ts, to_ts=now, gpu_index=gpu)

    powers = [s.get("power") for s in samples if s.get("power") is not None]
    avg_w = (sum(powers) / len(powers)) if powers else 0.0
    # Energy = avg_W × duration_h
    kwh = (avg_w * window / 3600.0) / 1000.0  # kWh
    price = 0.25
    currency = "EUR"
    if cfg is not None:
        try:
            price = float(cfg.get("ELECTRICITY_PRICE_EUR_PER_KWH", default="0.25"))
        except (ValueError, TypeError):
            price = 0.25
        currency = cfg.get("ELECTRICITY_CURRENCY", default="EUR") or "EUR"
    cost = kwh * price

    # Extrapolations (assume avg power continues)
    daily_kwh = avg_w * 24 / 1000.0
    daily_cost = daily_kwh * price
    monthly_kwh = daily_kwh * 30
    monthly_cost = daily_cost * 30

    return 200, {
        "ok": True,
        "window_seconds": window,
        "samples": len(samples),
        "avg_power_watts": round(avg_w, 2),
        "kwh": round(kwh, 4),
        "cost": round(cost, 4),
        "currency": currency,
        "price_per_kwh": price,
        "daily_kwh": round(daily_kwh, 3),
        "daily_cost": round(daily_cost, 3),
        "monthly_kwh": round(monthly_kwh, 2),
        "monthly_cost": round(monthly_cost, 2),
    }


# ────────────────────────── GET /api/profile-stats ────────────────────────


def handle_profile_stats(ctx: dict, params: dict) -> Response:
    """Total time spent in each power profile since `since` seconds ago.

    Walks the `profile_switch` events from storage and computes durations:
      - For each consecutive pair (e1, e2), the interval [e1.ts, e2.ts]
        is attributed to e1.payload.to
      - The tail [last_switch.ts, now] is attributed to last_switch.payload.to
    """
    storage = ctx.get("storage")
    if storage is None:
        return 503, {"ok": False, "error": "storage not available"}

    import time as _time
    try:
        since = int(params.get("since", 0))
    except (ValueError, TypeError):
        return 400, {"ok": False, "error": "since must be integer"}

    now = int(_time.time())
    from_ts = max(0, now - since) if since > 0 else 0
    events = storage.get_events(from_ts=from_ts, kind="profile_switch")
    # Sort defensively (storage already orders by id, but be paranoid)
    events.sort(key=lambda e: e["ts"])

    # If since > 0, we cap the first event's start at from_ts (so durations
    # are measured WITHIN the window, not before it).
    totals: dict = {}
    for i, ev in enumerate(events):
        payload = ev.get("payload") or {}
        to = payload.get("to")
        if not to:
            continue
        start = max(ev["ts"], from_ts) if since > 0 else ev["ts"]
        if i + 1 < len(events):
            end = events[i + 1]["ts"]
        else:
            end = now
        dur = max(0, end - start)
        totals[to] = totals.get(to, 0) + dur

    return 200, {
        "ok": True,
        "totals": totals,
        "now": now,
        "since_seconds": since,
        "events_count": len(events),
    }


# ────────────────────────── GET /api/auto-profile ─────────────────────────


def handle_auto_profile_status(ctx: dict) -> Response:
    """Status of the auto-profile-switch daemon (current classification, etc.)."""
    daemon = ctx.get("auto_profile_daemon")
    enabled = ctx["config"].get_bool("MODULE_AUTO_PROFILE")
    if daemon is None:
        return 200, {"enabled": enabled, "running": False}
    status = daemon.status()
    status["enabled"] = enabled
    status["thresholds"] = {
        "idle": ctx["config"].get_int("AUTO_PROFILE_IDLE_THRESHOLD", default=5),
        "boost": ctx["config"].get_int("AUTO_PROFILE_BOOST_THRESHOLD", default=80),
    }
    return 200, status


# ────────────────────────── /api/power-profiles ───────────────────────────


# Each profile bundles power-limit + GPU offset + memory offset.
_POWER_PROFILES = ("silent", "sweet", "boost")


def _read_power_profile(cfg, name: str) -> Optional[dict]:
    """Read one of the SILENT / SWEET / BOOST profiles from config."""
    key = name.upper()
    try:
        watts = cfg.get_int(f"POWER_PROFILE_{key}_W", default=0)
    except Exception:
        return None
    if watts <= 0:
        return None
    return {
        "name": name,
        "watts": watts,
        "gpu_offset": cfg.get_int(f"POWER_PROFILE_{key}_GPU_OFFSET", default=0),
        "mem_offset": cfg.get_int(f"POWER_PROFILE_{key}_MEM_OFFSET", default=0),
    }


def handle_power_profiles_list(ctx: dict) -> Response:
    """List the 3 configurable power profiles : silent / sweet / boost.

    Returns {profiles: [{name, watts, gpu_offset, mem_offset}, ...]}.
    A profile is omitted if its <NAME>_W is not configured (0 or missing).
    """
    cfg = ctx["config"]
    profiles = []
    for name in _POWER_PROFILES:
        p = _read_power_profile(cfg, name)
        if p:
            profiles.append(p)
    return 200, {"profiles": profiles}


def handle_power_profile_apply(ctx: dict, name: str) -> Response:
    """Apply one of the named profiles : power-limit + offsets in a single call.

    Also logs a 'profile_switch' event to storage for the time-tracker.
    """
    name = (name or "").lower()
    if name not in _POWER_PROFILES:
        return 400, {"ok": False, "error": f"unknown profile: {name!r}. Use one of: {_POWER_PROFILES}"}

    cfg = ctx["config"]
    prof = _read_power_profile(cfg, name)
    if prof is None:
        return 400, {"ok": False, "error": f"profile {name!r} is not configured (POWER_PROFILE_{name.upper()}_W)"}

    gpu_profile = ctx.get("profile") or {}
    from .modules import power_limit as _pl
    from .modules import clock_offsets as _co

    wrapper = cfg.get("POWER_LIMIT_WRAPPER", "/usr/local/bin/set-power-limit")
    display = cfg.get("CLOCK_OFFSETS_DISPLAY", ":0")
    xauth = cfg.get("CLOCK_OFFSETS_XAUTHORITY") or None

    # 1) power-limit
    try:
        pl_result = _pl.apply_power_limit(gpu_profile, prof["watts"], wrapper_path=wrapper)
    except ValueError as e:
        return 400, {"ok": False, "error": f"power-limit: {e}"}

    # 2) offsets (only if changed from current ; we always apply for simplicity)
    co_result = _co.apply_offsets(
        gpu_profile,
        gpu=prof["gpu_offset"], mem=prof["mem_offset"],
        display=display, xauthority=xauth,
    ) if (prof["gpu_offset"] != 0 or prof["mem_offset"] != 0) else {"ok": True, "skipped": True}

    ok = pl_result.get("ok", False) and co_result.get("ok", True)

    # Log the switch for the time tracker (storage may be None if not started)
    storage = ctx.get("storage")
    if storage is not None and ok:
        try:
            storage.record_event("profile_switch", {"to": name, "watts": prof["watts"]})
        except Exception:
            pass

    return (200 if ok else 500), {
        "ok": ok,
        "applied_profile": name,
        "watts": prof["watts"],
        "gpu_offset": prof["gpu_offset"],
        "mem_offset": prof["mem_offset"],
        "power_limit_result": pl_result,
        "offsets_result": co_result,
    }


# ────────────────────────── GET /api/processes ────────────────────────────


def handle_processes(ctx: dict) -> Response:
    """Per-process GPU usage via `nvidia-smi --query-compute-apps`.

    Returns {available: bool, processes: [{pid, name, vram_mib}]} sorted by
    vram desc. Useful to identify which LLM process owns the VRAM.
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
        processes.append({
            "pid": int(parts[0]),
            "name": parts[1],
            "vram_mib": vram,
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

    return 200, "\n".join(lines) + "\n"


# ────────────────────────── /api/profile/save ─────────────────────────────


def handle_profile_save(ctx: dict, payload: dict) -> Response:
    """Save a user override for a GPU profile.

    Validates the payload against profiles/schema.json then writes it to
    `<overrides_dir>/<safe_model_name>.json`. The next reload picks it up
    automatically (via `profile.get_profile_for_gpu`'s override-dir param).
    """
    import re as _re
    from .profile import load_schema, validate_profile

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
    """Return the active fan curve + current target % + daemon status."""
    from .modules import fan_curve as _fc
    profile = ctx.get("profile") or {}
    curve = _fc.pick_curve(profile)
    daemon = ctx.get("fan_curve_daemon")
    return 200, {
        "enabled": ctx["config"].get_bool("MODULE_FAN_CURVE"),
        "running": daemon is not None and getattr(daemon, "_thread", None) is not None,
        "curve": curve,
        "current_target_pct": getattr(daemon, "_last_pct", None) if daemon else None,
    }


# ────────────────────────── GET /api/health ───────────────────────────────


def handle_health(ctx: dict) -> Response:
    """JSON health status for external monitoring (Uptime Kuma, Grafana, etc.).

    Returns 200 + {status: "ok"} if all critical components are healthy.
    Returns 503 + {status: "degraded"} if any one fails. The components dict
    lets the caller diagnose which subsystem is down.
    """
    import time as _time
    from . import __version__ as _ver

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

    all_ok = all(components.values())
    code = 200 if all_ok else 503
    return code, {
        "status": "ok" if all_ok else "degraded",
        "components": components,
        "uptime_seconds": max(0, int(_time.time() - (ctx.get("started_at") or _time.time()))),
        "version": _ver,
    }


# ────────────────────────── GET /api/about ────────────────────────────────


def handle_push_vapid(ctx: dict) -> Response:
    """Return the VAPID public key for browser push subscription.

    The frontend feeds this into PushManager.subscribe({applicationServerKey}).
    Private key stays server-side, never exposed.
    """
    from .modules import web_push
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
    from .modules import web_push
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
    from . import __version__ as _ver

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
        "reason": "no log source configured — set LOG_FILE or JOURNALCTL_UNIT in config.env",
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
    from .install import _gather_env, recommend_modules
    from .profile import get_profile_for_gpu

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
    from .modules import power_limit as _pl, clock_offsets as _co
    from . import detect as _detect

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
    from .install import generate_config_env

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
