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
    """Fast nvidia-smi snapshot for ONE GPU (default: index 0)."""
    try:
        r = subprocess.run(
            ["nvidia-smi",
             "-i", str(int(gpu_index)),
             "--query-gpu=name,temperature.gpu,fan.speed,power.draw,power.limit,"
             "utilization.gpu,memory.used,memory.total",
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


def handle_state(ctx: dict) -> Response:
    """Aggregates everything the frontend needs in a single payload.

    All optional fields default to None/empty so the UI can render gracefully
    when a module is disabled.
    """
    cfg = ctx["config"]
    gpu_index = cfg.get_int("GPU_INDEX", default=0)
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


def handle_about(ctx: dict) -> Response:
    """Static info about the running server : version, paths, uptime."""
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

    return 200, {
        "version": _ver,
        "uptime_seconds": uptime,
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "config_path": ctx.get("config_path") or os.path.expanduser("~/.config/gpu-dashboard/config.env"),
        "storage_path": storage_path,
        "license": "MIT",
        "repo_url": "https://github.com/Shad107/gpu-dashboard",
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

    config_path = os.path.expanduser("~/.config/gpu-dashboard/config.env")
    os.makedirs(os.path.dirname(config_path), exist_ok=True)
    content = generate_config_env(choices, port=port, power_default=power_default, bind=bind)
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
