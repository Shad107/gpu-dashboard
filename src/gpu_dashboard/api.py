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
    import datetime as _dt2
    import time as _t2
    year_start_ts = int(_dt2.datetime.fromtimestamp(_t2.time()).replace(
        month=1, day=1, hour=0, minute=0, second=0, microsecond=0).timestamp())
    total = 0
    total_this_year = 0
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
                if s["ts"] >= year_start_ts:
                    total_this_year += delta
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
        "total_tokens_this_year": total_this_year,
        "year_start_ts": year_start_ts,
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
    month_start = int(_dt.datetime.fromtimestamp(now).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0).timestamp())
    year_start = int(_dt.datetime.fromtimestamp(now).replace(
        month=1, day=1, hour=0, minute=0, second=0, microsecond=0).timestamp())

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

    # Yearly kWh — integrate over all year-to-date samples (single query)
    year_samples = storage.get_samples(from_ts=year_start, to_ts=now, gpu_index=gpu)
    year_wh = 0.0
    for i in range(1, len(year_samples)):
        prev_s = year_samples[i - 1]
        cur = year_samples[i]
        if prev_s.get("power") is None or cur.get("power") is None:
            continue
        dt = min(cur["ts"] - prev_s["ts"], 300)
        avg = (prev_s["power"] + cur["power"]) / 2
        year_wh += avg * dt / 3600
    kwh_year = year_wh / 1000
    cost_year = round(kwh_year * price, 4)  # match kwh_today precision

    # Monthly kWh — integrate over month-to-date samples
    month_samples = storage.get_samples(from_ts=month_start, to_ts=now, gpu_index=gpu)
    month_wh = 0.0
    for i in range(1, len(month_samples)):
        prev_s = month_samples[i - 1]
        cur = month_samples[i]
        if prev_s.get("power") is None or cur.get("power") is None:
            continue
        dt = min(cur["ts"] - prev_s["ts"], 300)
        avg = (prev_s["power"] + cur["power"]) / 2
        month_wh += avg * dt / 3600
    kwh_month = month_wh / 1000
    cost_month = round(kwh_month * price, 4)

    # Budget tracker — forecast end-of-month from linear extrapolation
    budget_kwh = 0.0
    if cfg is not None:
        try:
            budget_kwh = float(cfg.get("ELECTRICITY_MONTHLY_BUDGET_KWH", default="0") or 0)
        except (ValueError, TypeError):
            budget_kwh = 0.0
    # Days in current month for end-of-month timestamp
    import calendar as _cal
    cur_dt = _dt.datetime.fromtimestamp(now)
    days_in_month = _cal.monthrange(cur_dt.year, cur_dt.month)[1]
    month_end_ts = int(_dt.datetime(
        cur_dt.year, cur_dt.month, days_in_month, 23, 59, 59
    ).timestamp())
    month_total_s = max(1, month_end_ts - month_start)
    month_elapsed_s = max(1, min(month_total_s, now - month_start))
    month_progress_pct = round(month_elapsed_s / month_total_s * 100, 1)
    forecast_kwh = round(kwh_month / (month_elapsed_s / month_total_s), 2) if kwh_month > 0 else 0.0
    over_budget = budget_kwh > 0 and forecast_kwh > budget_kwh

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
        "kwh_year": round(kwh_year, 4),  # 4-decimal precision matches kwh_today
        "cost_year": cost_year,
        "year_start_ts": year_start,
        "kwh_month": round(kwh_month, 4),
        "cost_month": cost_month,
        "month_start_ts": month_start,
        "month_end_ts": month_end_ts,
        "month_progress_pct": month_progress_pct,
        "forecast_kwh": forecast_kwh,
        "budget_kwh": round(budget_kwh, 2),
        "over_budget": over_budget,
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

    # Optional monthly budget (cycle 121) — 0 disables tracking
    budget_kwh = 0.0
    if "budget_kwh" in payload:
        try:
            budget_kwh = float(payload.get("budget_kwh") or 0)
        except (ValueError, TypeError):
            return 400, {"ok": False, "error": "budget_kwh must be a number"}
        if budget_kwh < 0 or budget_kwh > 10000:
            return 400, {"ok": False, "error": "budget_kwh out of range (0, 10000)"}

    # 1) Update in-memory Config (effective immediately)
    cfg.set("ELECTRICITY_PRICE_EUR_PER_KWH", price)
    cfg.set("ELECTRICITY_CURRENCY", currency)
    if "budget_kwh" in payload:
        cfg.set("ELECTRICITY_MONTHLY_BUDGET_KWH", budget_kwh)

    # 2) Persist to config.env so it survives restart
    config_path = ctx.get("config_path") or os.path.expanduser(
        "~/.config/gpu-dashboard/config.env"
    )
    try:
        os.makedirs(os.path.dirname(config_path), exist_ok=True)
        existing = {}
        if os.path.isfile(config_path):
            from .config import parse_env_file
            existing = parse_env_file(config_path)
        existing["ELECTRICITY_PRICE_EUR_PER_KWH"] = str(price)
        existing["ELECTRICITY_CURRENCY"] = currency
        if "budget_kwh" in payload:
            existing["ELECTRICITY_MONTHLY_BUDGET_KWH"] = str(budget_kwh)
        from .config import write_env_file
        write_env_file(config_path, existing,
                       header="# Auto-updated by gpu-dashboard /api/electricity/config")
    except OSError as e:
        return 500, {"ok": False, "error": f"could not write config.env: {e}"}

    return 200, {"ok": True, "price_per_kwh": price, "currency": currency,
                 "budget_kwh": budget_kwh,
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

    # Budget tracker (cycle 121) — month-to-date actual + forecast
    budget_kwh = 0.0
    if cfg is not None:
        try:
            budget_kwh = float(cfg.get("ELECTRICITY_MONTHLY_BUDGET_KWH", default="0") or 0)
        except (ValueError, TypeError):
            budget_kwh = 0.0

    import datetime as _dtm
    import calendar as _cal
    cur_dt = _dtm.datetime.fromtimestamp(now)
    month_start = int(cur_dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp())
    days_in_month = _cal.monthrange(cur_dt.year, cur_dt.month)[1]
    month_end_ts = int(_dtm.datetime(cur_dt.year, cur_dt.month, days_in_month, 23, 59, 59).timestamp())
    month_total_s = max(1, month_end_ts - month_start)
    month_elapsed_s = max(1, min(month_total_s, now - month_start))
    month_progress_pct = round(month_elapsed_s / month_total_s * 100, 1)
    # Integrate month-to-date samples for actual kWh
    month_samples = storage.get_samples(from_ts=month_start, to_ts=now, gpu_index=gpu)
    month_wh = 0.0
    for i in range(1, len(month_samples)):
        prev_s = month_samples[i - 1]
        cur_s = month_samples[i]
        if prev_s.get("power") is None or cur_s.get("power") is None:
            continue
        dt = min(cur_s["ts"] - prev_s["ts"], 300)
        avg = (prev_s["power"] + cur_s["power"]) / 2
        month_wh += avg * dt / 3600
    kwh_month = month_wh / 1000.0
    cost_month = kwh_month * price
    forecast_kwh = (kwh_month / (month_elapsed_s / month_total_s)) if kwh_month > 0 else 0.0
    over_budget = budget_kwh > 0 and forecast_kwh > budget_kwh

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
        "kwh_month": round(kwh_month, 3),
        "cost_month": round(cost_month, 3),
        "month_progress_pct": month_progress_pct,
        "forecast_kwh": round(forecast_kwh, 2),
        "budget_kwh": round(budget_kwh, 2),
        "over_budget": over_budget,
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

    # Most-recent-first list of {ts, to} pairs for the About activity log
    recent_events = [
        {"ts": ev["ts"], "to": (ev.get("payload") or {}).get("to")}
        for ev in reversed(events)
        if (ev.get("payload") or {}).get("to")
    ][:50]

    return 200, {
        "ok": True,
        "totals": totals,
        "now": now,
        "since_seconds": since,
        "events_count": len(events),
        "recent_events": recent_events,
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
        handle_power_profile_apply(ctx, profile_name)

    from .modules.benchmark import run_segment, compare
    seg_a = run_segment(duration, a, _apply, sampler, price_per_kwh=price)
    seg_b = run_segment(duration, b, _apply, sampler, price_per_kwh=price)
    cmp = compare(seg_a, seg_b)

    return 200, {
        "ok": True,
        "segment_a": seg_a,
        "segment_b": seg_b,
        "comparison": cmp,
    }


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
            _, ps = handle_power_stats(ctx)
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
            _, ll = handle_llm_lifetime(ctx)
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
    from .modules import app_triggers as _at
    return 200, {"ok": True, "triggers": _at.load_triggers()}


def handle_app_triggers_post(ctx: dict, payload) -> Response:
    """Persist {app: profile} mapping. Validates each profile name."""
    from .modules import app_triggers as _at
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
    """Return the active fan curve + current target % + daemon status + hysteresis."""
    from .modules import fan_curve as _fc
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
    from .modules import fan_curve as _fc
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
    from . import __version__ as _ver
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
    from . import __version__ as _ver
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
            from .config import parse_env_file
            existing = parse_env_file(config_path)
        existing[key] = "1" if enabled else "0"
        from .config import write_env_file
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


# ─── R&D #6.7 — journalctl bridge with saved filters ─────────────────────────
# Pre-canned filters for GPU-relevant log noise. Each maps to a regex applied
# to journalctl messages.
_JOURNAL_FILTERS = {
    "nvidia":   r"nvidia|NVRM",
    "nvrm":     r"NVRM",
    "xid":      r"NVRM:.*Xid|\bXid\s*\(",
    "oom":      r"oom-killer|out of memory|OOM",
    "thermal":  r"thermal|throttle|over[- ]?temp",
    "pcieport": r"pcieport|PCIe|aer",
    "xorg":     r"\bXorg\b|nvidia-modeset",
    "all":      r".",
}


def handle_journal_tail(ctx: dict, params: Optional[dict] = None) -> Response:
    """Return recent journalctl entries matching a pre-canned filter.

    Query params :
      filter = nvidia (default) | nvrm | xid | oom | thermal | pcieport | xorg | all
      since  = '1h' (default), '15m', '24h', etc. (passed to --since)
      limit  = 100 (default), max 500
    """
    import re as _re
    params = params or {}
    fkey = params.get("filter", "nvidia")
    if fkey not in _JOURNAL_FILTERS:
        return 400, {"ok": False, "error": f"unknown filter '{fkey}'"}
    since = params.get("since", "1h")
    if not _re.match(r"^\d{1,4}(m|h|d|s)$", since):
        return 400, {"ok": False, "error": "since must be like '1h', '30m', '24h'"}
    try:
        limit = max(1, min(500, int(params.get("limit", 100))))
    except (ValueError, TypeError):
        limit = 100

    pattern = _JOURNAL_FILTERS[fkey]
    # Convert 'XX{m,h,d,s}' to journalctl-accepted 'X minute/hour/day/second ago'
    unit_map = {"m": "minutes", "h": "hours", "d": "days", "s": "seconds"}
    since_human = f"{since[:-1]} {unit_map[since[-1]]} ago"
    try:
        # Try kernel ring buffer first, fall back to all-journal on permission errors
        r = subprocess.run(
            ["journalctl", "-k", "--since", since_human, "-o", "short-iso", "--no-pager"],
            capture_output=True, text=True, timeout=4,
        )
        # If -k failed or returned nothing useful, retry without -k
        if r.returncode != 0 or "-- No entries --" in r.stdout:
            r = subprocess.run(
                ["journalctl", "--since", since_human, "-o", "short-iso", "--no-pager"],
                capture_output=True, text=True, timeout=4,
            )
    except (FileNotFoundError, subprocess.SubprocessError, OSError) as e:
        return 200, {"ok": True, "available": False, "reason": f"journalctl unavailable: {e}"}
    if r.returncode != 0:
        return 200, {"ok": True, "available": False, "reason": "journalctl returned non-zero"}

    rx = _re.compile(pattern, _re.IGNORECASE)
    entries: list = []
    for line in r.stdout.splitlines():
        if not rx.search(line):
            continue
        # Parse 'YYYY-MM-DDTHH:MM:SS+0200 host kernel: msg' shape
        parts = line.split(" ", 3)
        if len(parts) < 4:
            entries.append({"ts": "", "host": "", "source": "", "msg": line})
            continue
        ts, host, source, msg = parts
        entries.append({"ts": ts, "host": host, "source": source.rstrip(":"), "msg": msg})
    # Keep the most recent `limit`
    entries = entries[-limit:]

    # Detect Xid errors specifically — useful for chart markers
    xids: list = []
    rx_xid = _re.compile(r"\bXid\s*\([^)]*\):\s*(\d+),?\s*(.*)$")
    for e in entries:
        m = rx_xid.search(e["msg"])
        if m:
            xids.append({"ts": e["ts"], "xid_code": int(m.group(1)), "summary": m.group(2)})

    return 200, {
        "ok": True,
        "available": True,
        "filter": fkey,
        "since": since,
        "count": len(entries),
        "entries": entries,
        "xid_events": xids,
        "filters_available": list(_JOURNAL_FILTERS.keys()),
    }


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

    return 200, {
        "ok": True,
        "available": True,
        "current_temp_c": round(current_temp, 1),
        "slowdown_temp_c": slowdown_temp,
        "headroom_c": headroom_c,
        "slope_c_per_min": round(slope * 60, 3),
        "projected_throttle_s": projected_throttle_s,
        "suggested_fan_delta_pct": suggested_fan_delta_pct,
        "suggested_msg_key": suggested_msg_key,
        "sample_count": len(xs),
    }


# ─── R&D #5.2 — Driver/kernel drift detector ─────────────────────────────────
_DRIFT_FIELDS_CMD = ["driver_version", "vbios_version", "name",
                     "persistence_mode", "ecc.mode.current", "mig.mode.current"]


def _read_drift_snapshot() -> dict:
    """Capture the current driver+kernel+ECC/MIG fingerprint."""
    snap: dict = {"ts": int(time.time())}
    # nvidia-smi side
    try:
        r = subprocess.run(
            ["nvidia-smi", "-i", "0", f"--query-gpu={','.join(_DRIFT_FIELDS_CMD)}",
             "--format=csv,noheader"],
            capture_output=True, text=True, timeout=2,
        )
        if r.returncode == 0 and r.stdout.strip():
            parts = [p.strip() for p in r.stdout.strip().splitlines()[0].split(",")]
            for i, key in enumerate(_DRIFT_FIELDS_CMD):
                snap[key.replace(".", "_")] = parts[i] if i < len(parts) else ""
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        pass
    # Kernel side
    try:
        r = subprocess.run(["uname", "-r"], capture_output=True, text=True, timeout=1)
        if r.returncode == 0:
            snap["kernel_release"] = r.stdout.strip()
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        pass
    return snap


def _drift_snapshot_path() -> str:
    return os.path.expanduser("~/.config/gpu-dashboard/drift_baseline.json")


def _drift_history_path() -> str:
    return os.path.expanduser("~/.config/gpu-dashboard/drift_history.json")


def _diff_snapshots(old: dict, new: dict) -> list:
    """Return list of {field, old, new} for fields that changed.
    Ignores 'ts' (always different)."""
    diffs: list = []
    keys = set(old.keys()) | set(new.keys())
    keys.discard("ts")
    for k in sorted(keys):
        if old.get(k) != new.get(k):
            diffs.append({"field": k, "old": old.get(k), "new": new.get(k)})
    return diffs


def detect_drift_on_startup() -> Optional[list]:
    """Called once at server boot. Reads current snapshot, compares with
    saved baseline, and if anything changed records a history entry +
    updates the baseline. Returns the diff list (empty = no drift).

    Returns None if drift_baseline doesn't exist yet (first ever boot —
    baseline gets created silently).
    """
    new = _read_drift_snapshot()
    if not new or len(new) <= 1:  # only ts
        return None
    baseline_p = _drift_snapshot_path()
    os.makedirs(os.path.dirname(baseline_p), exist_ok=True)
    if not os.path.exists(baseline_p):
        with open(baseline_p, "w") as f:
            json.dump(new, f, indent=2)
        return None
    try:
        with open(baseline_p) as f:
            old = json.load(f)
    except (OSError, json.JSONDecodeError):
        old = {}
    diffs = _diff_snapshots(old, new)
    if diffs:
        # Append to history
        history_p = _drift_history_path()
        try:
            history = []
            if os.path.exists(history_p):
                with open(history_p) as f:
                    history = json.load(f)
            history.append({"ts": new["ts"], "diffs": diffs,
                            "old_snapshot": old, "new_snapshot": new})
            # Cap history at 100 entries
            history = history[-100:]
            with open(history_p, "w") as f:
                json.dump(history, f, indent=2)
        except (OSError, json.JSONDecodeError, TypeError):
            pass
        # Update baseline
        with open(baseline_p, "w") as f:
            json.dump(new, f, indent=2)
    return diffs


def handle_drift_check(ctx: dict) -> Response:
    """Return the most recent drift entry + current snapshot.

    Response :
      {ok, has_baseline, current: {...}, last_drift: {ts, diffs, ...} | null,
       history_count: int}
    """
    new = _read_drift_snapshot()
    baseline_p = _drift_snapshot_path()
    history_p = _drift_history_path()
    has_baseline = os.path.exists(baseline_p)
    last_drift = None
    history_count = 0
    if os.path.exists(history_p):
        try:
            with open(history_p) as f:
                history = json.load(f)
            history_count = len(history)
            if history:
                last_drift = history[-1]
        except (OSError, json.JSONDecodeError):
            pass
    return 200, {
        "ok": True,
        "has_baseline": has_baseline,
        "current": new,
        "last_drift": last_drift,
        "history_count": history_count,
    }


# ─── R&D #5.4 — Bar JSON for Waybar/polybar/i3blocks/tmux ────────────────────
def handle_bar(ctx: dict, params: Optional[dict] = None) -> Tuple[int, Any]:
    """One-line GPU status formatted for desktop bars.

    Query params :
      fmt = waybar (default) | polybar | i3blocks | tmux | plain

    Returns shape depends on fmt :
      waybar  → JSON {"text", "tooltip", "class", "percentage"}
      polybar → text "..."  (color tags %{Fxxx})
      i3blocks → text "...\n...\nCOLOR"  (3 lines)
      tmux    → text "..."  (uses #[fg=...] tags)
      plain   → text "..."
    """
    fmt = (params or {}).get("fmt", "waybar")
    snap = _gpu_card_snapshot(gpu_index=0)
    if not snap or not snap.get("alive"):
        if fmt == "waybar":
            return 200, {"text": "GPU N/A", "tooltip": "GPU offline", "class": "off", "percentage": 0}
        return 200, "GPU N/A"

    temp = snap.get("temp", 0)
    util = snap.get("util_gpu", 0)
    power = snap.get("power", 0)
    mem_used_g = (snap.get("mem_used_mib", 0) or 0) / 1024
    mem_total_g = (snap.get("mem_total_mib", 0) or 0) / 1024

    # Classify status
    if temp >= 85:
        klass = "critical"; color = "#f87171"; polycolor = "F87171"; tmuxcolor = "red"
    elif temp >= 75:
        klass = "warning"; color = "#fbbf24"; polycolor = "FBBF24"; tmuxcolor = "yellow"
    else:
        klass = "ok"; color = "#4ade80"; polycolor = "4ADE80"; tmuxcolor = "green"

    text = f"{temp}°C {util}% {power:.0f}W"
    tooltip = (
        f"GPU : {snap.get('name', 'GPU')}\n"
        f"Temp : {temp}°C · Util : {util}% · Power : {power:.0f} W\n"
        f"VRAM : {mem_used_g:.1f} / {mem_total_g:.1f} GiB"
    )

    if fmt == "waybar":
        return 200, {
            "text": text,
            "tooltip": tooltip,
            "class": klass,
            "percentage": int(util),
        }
    elif fmt == "polybar":
        return 200, f"%{{F#{polycolor}}}{text}%{{F-}}"
    elif fmt == "i3blocks":
        # full text \n short text \n color
        return 200, f"{text}\n{temp}°\n{color}"
    elif fmt == "tmux":
        return 200, f"#[fg={tmuxcolor}]{text}#[default]"
    else:  # plain
        return 200, text


# ─── R&D #4.3 — ECC + memory health audit ────────────────────────────────────
def _na(s: str) -> bool:
    """nvidia-smi uses '[N/A]' or 'N/A' for unsupported fields."""
    return s.strip().upper() in ("N/A", "[N/A]", "", "NOT SUPPORTED")


def handle_ecc_health(ctx: dict) -> Response:
    """ECC error counters + remapped rows health audit.

    Most consumer cards (RTX 3090/4090 etc.) don't expose ECC — returns
    available=false so the UI hides the panel. Datacenter cards (A100,
    H100, Tesla) expose the full counters.

    Response :
      {ok, available, ecc_mode, corrected_total, uncorrected_total,
       remapped_correctable, remapped_uncorrectable, remapped_pending,
       remapped_failure, verdict_kind ('ok'|'watch'|'failing'), verdict_msg}
    """
    fields = [
        "ecc.mode.current",
        "ecc.errors.corrected.aggregate.total",
        "ecc.errors.uncorrected.aggregate.total",
        "remapped_rows.correctable",
        "remapped_rows.uncorrectable",
        "remapped_rows.pending",
        "remapped_rows.failure",
    ]
    try:
        r = subprocess.run(
            ["nvidia-smi", "-i", "0", f"--query-gpu={','.join(fields)}",
             "--format=csv,noheader"],
            capture_output=True, text=True, timeout=2,
        )
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        return 200, {"ok": True, "available": False}
    if r.returncode != 0 or not r.stdout.strip():
        return 200, {"ok": True, "available": False}

    parts = [p.strip() for p in r.stdout.strip().splitlines()[0].split(",")]
    if len(parts) < 7:
        return 200, {"ok": True, "available": False}

    # Heuristic : if EVERY field is N/A, the card doesn't expose ECC at all
    if all(_na(p) for p in parts):
        return 200, {"ok": True, "available": False}

    def _i(s: str) -> Optional[int]:
        if _na(s):
            return None
        try:
            return int(s)
        except ValueError:
            return None

    ecc_mode = None if _na(parts[0]) else parts[0]
    corr_total = _i(parts[1])
    uncorr_total = _i(parts[2])
    rem_corr = _i(parts[3])
    rem_uncorr = _i(parts[4])
    rem_pending = _i(parts[5])
    rem_failure = _i(parts[6])

    # Verdict logic
    if rem_failure and rem_failure > 0:
        verdict_kind = "failing"
        verdict_msg = f"Row remap FAILED on {rem_failure} row(s). VRAM is exhausted of spare rows — replace the card."
    elif uncorr_total and uncorr_total > 0:
        verdict_kind = "failing"
        verdict_msg = f"{uncorr_total} uncorrectable ECC error(s). Memory degrading — back up data + replace soon."
    elif rem_uncorr and rem_uncorr > 0:
        verdict_kind = "watch"
        verdict_msg = f"{rem_uncorr} row(s) remapped due to uncorrectable errors. Monitor closely."
    elif rem_pending and rem_pending > 0:
        verdict_kind = "watch"
        verdict_msg = f"{rem_pending} row(s) pending remap (reboot required to apply)."
    elif corr_total and corr_total > 0:
        verdict_kind = "watch"
        verdict_msg = f"{corr_total} corrected ECC error(s) since last reset. Normal at low counts, alarming if growing fast."
    else:
        verdict_kind = "ok"
        verdict_msg = "No ECC errors, no remapped rows. Memory healthy."

    return 200, {
        "ok": True,
        "available": True,
        "ecc_mode": ecc_mode,
        "corrected_total": corr_total,
        "uncorrected_total": uncorr_total,
        "remapped_correctable": rem_corr,
        "remapped_uncorrectable": rem_uncorr,
        "remapped_pending": rem_pending,
        "remapped_failure": rem_failure,
        "verdict_kind": verdict_kind,
        "verdict_msg": verdict_msg,
    }


# ─── R&D #4.5 — Idle-state audit ─────────────────────────────────────────────
# Reference baselines : expected idle draw (W) per GPU family.
# Sources : community reports + datasheet idle figures, conservative bands.
_IDLE_BASELINES = [
    # (substring in name → (low_w, high_w, family_label))
    ("RTX 4090",  (15, 25,  "Ada/RTX 4090")),
    ("RTX 4080",  (12, 22,  "Ada/RTX 4080")),
    ("RTX 4070",  ( 8, 16,  "Ada/RTX 4070")),
    ("RTX 4060",  ( 6, 14,  "Ada/RTX 4060")),
    ("RTX 3090",  (15, 25,  "Ampere/RTX 3090")),
    ("RTX 3080",  (12, 22,  "Ampere/RTX 3080")),
    ("RTX 3070",  ( 8, 18,  "Ampere/RTX 3070")),
    ("RTX 3060",  ( 8, 16,  "Ampere/RTX 3060")),
    ("RTX 2080",  (10, 20,  "Turing/RTX 2080")),
    ("RTX 2070",  ( 8, 16,  "Turing/RTX 2070")),
    ("RTX 2060",  ( 7, 14,  "Turing/RTX 2060")),
    ("Tesla",     ( 8, 25,  "Tesla/datacenter")),
    ("A100",      (45, 75,  "A100 datacenter")),
    ("H100",      (60, 100, "H100 datacenter")),
]


def _baseline_for(name: str) -> Optional[tuple]:
    if not name:
        return None
    for needle, band in _IDLE_BASELINES:
        if needle.lower() in name.lower():
            return band
    return None


def handle_idle_audit(ctx: dict) -> Response:
    """Audit idle-state power draw vs expected baseline for this GPU family.

    Logic :
      1. Read current util + power + pstate via nvidia-smi
      2. If util > 5% → status="busy" (audit only meaningful at idle)
      3. Compare power against family baseline (low_w .. high_w)
      4. Return verdict + list of checklist items if verdict is "high"

    Designed to be polled by the UI About tab; cheap (one nvidia-smi call).
    """
    try:
        r = subprocess.run(
            ["nvidia-smi", "-i", "0",
             "--query-gpu=name,utilization.gpu,power.draw,pstate,persistence_mode",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=2,
        )
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        return 200, {"ok": True, "available": False}
    if r.returncode != 0 or not r.stdout.strip():
        return 200, {"ok": True, "available": False}

    parts = [p.strip() for p in r.stdout.strip().splitlines()[0].split(",")]
    if len(parts) < 5:
        return 200, {"ok": True, "available": False}
    name = parts[0]
    try:
        util_gpu = int(parts[1])
        power = float(parts[2])
    except ValueError:
        return 200, {"ok": True, "available": False}
    pstate = parts[3]
    persistence = parts[4]

    baseline = _baseline_for(name)
    if baseline is None:
        return 200, {
            "ok": True, "available": True, "status": "unknown",
            "name": name, "power": power, "pstate": pstate,
            "persistence_mode": persistence,
            "verdict": "no baseline for this GPU family",
            "checklist": [],
        }
    low_w, high_w, family = baseline

    if util_gpu > 5:
        return 200, {
            "ok": True, "available": True, "status": "busy",
            "name": name, "power": power, "util_gpu": util_gpu,
            "pstate": pstate, "persistence_mode": persistence,
            "baseline": {"low": low_w, "high": high_w, "family": family},
            "verdict": "GPU is busy — re-check when idle",
            "checklist": [],
        }

    # Idle ; compare against baseline
    if power <= high_w:
        verdict = "ok"
        msg = f"Idle power {power:.1f} W within expected {low_w}-{high_w} W for {family}."
        checklist = []
    else:
        verdict = "high"
        excess = power - high_w
        msg = f"Idle power {power:.1f} W is {excess:.1f} W above expected high ({high_w} W) for {family}."
        checklist = []
        if persistence and "Disabled" in persistence:
            checklist.append({
                "key": "persistence_mode",
                "label": "Enable persistence mode",
                "hint": "Run : sudo nvidia-smi -pm 1   (prevents driver re-init on every nvidia-smi call)",
            })
        if pstate and pstate != "P8":
            checklist.append({
                "key": "pstate_high",
                "label": f"P-state is {pstate}, expected P8 at idle",
                "hint": "Compositor or background app holding the GPU. Check Xorg, browser hardware accel, OBS.",
            })
        # General checklist items always shown for 'high'
        checklist.append({
            "key": "compositor",
            "label": "Check display compositor / hardware accel",
            "hint": "Disable HW accel in Chrome / Electron apps. Test with `nvidia-smi pmon -c 1`.",
        })
        checklist.append({
            "key": "modeset",
            "label": "Enable kernel modesetting",
            "hint": "Add `nvidia-drm.modeset=1` to kernel cmdline if not already set.",
        })

    return 200, {
        "ok": True, "available": True, "status": "idle",
        "name": name, "power": power, "util_gpu": util_gpu,
        "pstate": pstate, "persistence_mode": persistence,
        "baseline": {"low": low_w, "high": high_w, "family": family},
        "verdict": msg,
        "verdict_kind": verdict,
        "checklist": checklist,
    }


# ─── R&D #4.2 — Clocks-event-reasons decoder ────────────────────────────────
_CLOCK_EVENT_REASONS = [
    # field name in nvidia-smi → (key, label_short, mitigation_hint)
    ("clocks_event_reasons.gpu_idle",                ("gpu_idle",       "Idle",          "Normal — GPU is idle.")),
    ("clocks_event_reasons.applications_clocks_setting", ("apps_clocks", "Apps clocks",   "User-set application clocks limit.")),
    ("clocks_event_reasons.sw_power_cap",            ("sw_power_cap",   "Power cap",     "Power limit hit — raise --power-limit or reduce workload.")),
    ("clocks_event_reasons.hw_slowdown",             ("hw_slowdown",    "HW slowdown",   "Hardware throttle. Check temp + PSU + cables (over_temp / power_brake).")),
    ("clocks_event_reasons.sync_boost",              ("sync_boost",     "Sync boost",    "Boost is sync'd with another GPU in the same group.")),
    ("clocks_event_reasons.sw_thermal_slowdown",     ("sw_thermal",     "SW thermal",    "Driver-side thermal throttle. Improve cooling / lower clocks.")),
    ("clocks_event_reasons.hw_thermal_slowdown",     ("hw_thermal",     "HW thermal",    "Hardware thermal limit reached. Critical — clean fans, repaste, lower PL.")),
    ("clocks_event_reasons.hw_power_brake_slowdown", ("hw_power_brake", "Power brake",   "External PSU brake signal — bad cable / underspec'd PSU.")),
    # display_clock_setting removed — not exposed by driver 560+. Keep
    # the list to widely-supported fields to avoid 'invalid field' rc=2.
]


def handle_clock_events(ctx: dict) -> Response:
    """Decode current throttle reasons from nvidia-smi clocks_event_reasons.

    Returns a list of reasons currently 'Active' with a plain-language label
    + mitigation hint. Empty list means no throttle.

    Response :
      {ok: bool, available: bool, reasons: [{key, label, hint}], raw: {...}}
    """
    fields = [f for f, _ in _CLOCK_EVENT_REASONS]
    try:
        r = subprocess.run(
            ["nvidia-smi", f"--query-gpu={','.join(fields)}", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=2,
        )
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        return 200, {"ok": True, "available": False, "reasons": [], "raw": {}}
    if r.returncode != 0 or not r.stdout.strip():
        return 200, {"ok": True, "available": False, "reasons": [], "raw": {}}
    parts = [p.strip() for p in r.stdout.strip().splitlines()[0].split(",")]
    raw: dict = {}
    reasons: list = []
    for (field, (key, label, hint)), value in zip(_CLOCK_EVENT_REASONS, parts):
        active = value.lower() == "active"
        raw[key] = active
        if active:
            reasons.append({"key": key, "label": label, "hint": hint})
    return 200, {"ok": True, "available": True, "reasons": reasons, "raw": raw}


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
    from . import __version__ as VERSION

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
