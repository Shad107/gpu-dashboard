"""HTTP handlers for ops / services / wizard / update / snapshot.

Extracted from the legacy monolith in cycle 9 of the api/ split.
Covers Settings → Services CRUD, Setup Wizard (R&D #3), update pull,
snapshot bundle, sysreport, version/about/health endpoints.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from typing import Optional, Tuple

from . import _core as _m


Response = Tuple[int, dict]


# Forwarding stubs so tests patching api._core.X take effect here too.
# *args/**kw so test mocks with arbitrary signatures still resolve correctly.
def _gpu_card_snapshot(*args, **kw):
    return _m._gpu_card_snapshot(*args, **kw)


def _gpus_available():
    return _m._gpus_available()


def _read_cmdline(pid):
    return _m._read_cmdline(pid)


def _parse_gpu_index(params):
    return _m._parse_gpu_index(params)


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


# Helpers moved with the ops handlers ───────────────────────

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
