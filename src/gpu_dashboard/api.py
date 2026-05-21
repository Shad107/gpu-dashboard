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


def _gpu_card_snapshot() -> dict:
    """Fast nvidia-smi snapshot of the GPU card for the live tiles."""
    try:
        r = subprocess.run(
            ["nvidia-smi",
             "--query-gpu=name,temperature.gpu,fan.speed,power.draw,power.limit,"
             "utilization.gpu,memory.used,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=3,
        )
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        return {"alive": False, "name": "?"}

    if r.returncode != 0 or not r.stdout.strip():
        return {"alive": False, "name": "?"}

    parts = [p.strip() for p in r.stdout.strip().split(",")]
    if len(parts) < 8 or not parts[1].isdigit():
        return {"alive": False, "name": parts[0] if parts else "?"}

    return {
        "alive": True,
        "name": parts[0],
        "temp": int(parts[1]),
        "fan_pct": int(parts[2]),
        "power": float(parts[3]),
        "power_limit": float(parts[4]),
        "util_gpu": int(parts[5]),
        "mem_used_mib": int(parts[6]),
        "mem_total_mib": int(parts[7]),
    }


# ─────────────────────────── GET /api/state ────────────────────────────────


def handle_state(ctx: dict) -> Response:
    """Aggregates everything the frontend needs in a single payload.

    All optional fields default to None/empty so the UI can render gracefully
    when a module is disabled.
    """
    cfg = ctx["config"]
    body = {
        "gpu": _gpu_card_snapshot(),
        "metrics": ctx["sampler"].snapshot() if ctx.get("sampler") else [],
        "profile": ctx.get("profile"),
        "fans": _per_fan_state(cfg),
        "tuning": _tuning_state(cfg),
        "watchdog": _watchdog_state(cfg),
        "services": _services_state(cfg),
        "fan_dist": _fan_distribution(cfg),
        "llm_model": _llm_model_served(cfg),
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


def handle_history(ctx: dict, params: dict) -> Response:
    """Renvoie les samples historiques depuis SQLite.

    Query params : from (epoch, default 0), to (epoch, default now), step (seconds, optional)
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
    samples = storage.get_samples(from_ts=from_ts, to_ts=to_ts, step=step)
    return 200, {"ok": True, "samples": samples}


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
