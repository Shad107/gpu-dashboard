"""HTTP server — thin wrapper over http.server stdlib + api.py handlers.

Loads config + profile + starts MetricsSampler, then serves :
  - GET  /                       → static/index.html
  - GET  /static/<file>          → static/<file>
  - GET  /api/state              → live state
  - POST /api/set-power-limit    → power_limit module
  - POST /api/set-offsets        → clock_offsets module
  - GET  /api/alerts-config      → alerts config
  - POST /api/alerts-config      → save alerts config
  - POST /api/alerts-test        → send test Telegram
"""
from __future__ import annotations

import http.server
import json
import os
import socketserver
import sys
from typing import Optional
from urllib.parse import parse_qs, urlparse

from . import api
from .config import Config
from .metrics import MetricsSampler
from .profile import get_profile_for_gpu
from .retention import RetentionTask
from .storage import Storage


_STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


# ──────────────────────────── default config ───────────────────────────────


DEFAULTS = {
    "DASHBOARD_PORT": "9999",
    "DASHBOARD_BIND": "0.0.0.0",
    "DASHBOARD_REFRESH_INTERVAL": "5",
    "DASHBOARD_SAMPLE_KEEP": "720",
    "GPU_INDEX": "0",
    "MODULE_POWER_LIMIT": "0",
    "MODULE_CLOCK_OFFSETS": "0",
    "MODULE_TELEGRAM_ALERTS": "0",
    "MODULE_OCULINK_WATCHDOG": "0",
    "MODULE_FAN_CURVE": "0",
    "MODULE_AUTO_PROFILE": "0",
    "AUTO_PROFILE_INTERVAL": "30",
    "AUTO_PROFILE_WINDOW": "60",
    "AUTO_PROFILE_MIN_STABLE": "90",
    "AUTO_PROFILE_IDLE_THRESHOLD": "5",
    "AUTO_PROFILE_BOOST_THRESHOLD": "80",
    "ELECTRICITY_PRICE_EUR_PER_KWH": "0.25",
    "ELECTRICITY_CURRENCY": "EUR",
    "STORAGE_DB_PATH": "~/.local/share/gpu-dashboard/metrics.db",
    "STORAGE_RETENTION_DAYS": "30",
    "POWER_LIMIT_DEFAULT": "250",
    "POWER_PROFILE_SILENT_W": "180",
    "POWER_PROFILE_SILENT_GPU_OFFSET": "0",
    "POWER_PROFILE_SILENT_MEM_OFFSET": "0",
    "POWER_PROFILE_SWEET_W": "250",
    "POWER_PROFILE_SWEET_GPU_OFFSET": "75",
    "POWER_PROFILE_SWEET_MEM_OFFSET": "500",
    "POWER_PROFILE_BOOST_W": "350",
    "POWER_PROFILE_BOOST_GPU_OFFSET": "100",
    "POWER_PROFILE_BOOST_MEM_OFFSET": "750",
    "MODULE_ALERT_MONITOR": "0",
    "ALERT_GPU_TEMP_THRESHOLD": "85",
    "ALERT_MEM_TEMP_THRESHOLD": "95",
    "ALERT_FAN_PCT_THRESHOLD": "95",
    "ALERT_VRAM_PCT_THRESHOLD": "90",
    "ALERT_MIN_CONSECUTIVE": "3",
    "ALERT_COOLDOWN_SECONDS": "300",
    "ALERT_MONITOR_INTERVAL": "30",
    "WEBHOOK_URL": "",
    "WEBHOOK_ENABLED": "0",
    "POWER_LIMIT_WRAPPER": "/usr/local/bin/set-power-limit",
    "CLOCK_OFFSETS_DISPLAY": ":0",
    "TG_ENABLED": "0",
    "TG_TOKEN": "",
    "TG_CHAT": "",
    "ALERT_DROP": "1",
    "ALERT_RECOVER": "1",
}


def _default_config_path() -> str:
    return os.path.expanduser("~/.config/gpu-dashboard/config.env")


def _load_context(config_path: Optional[str] = None, profiles_dir: str = "profiles") -> dict:
    """Build the context dict passed to api.* handlers."""
    config_files = []
    # `setup_required` is True iff no config.env was found at the expected path.
    # Frontend uses this hint to auto-open the setup wizard on first launch.
    setup_required = False
    if config_path is None:
        # Defaults under ~/.config/gpu-dashboard/
        home = os.path.expanduser("~")
        config_env_path = os.path.join(home, ".config/gpu-dashboard", "config.env")
        secrets_env_path = os.path.join(home, ".config/gpu-dashboard", "secrets.env")
        if os.path.isfile(config_env_path):
            config_files.append(config_env_path)
        else:
            setup_required = True
        if os.path.isfile(secrets_env_path):
            config_files.append(secrets_env_path)
    else:
        config_files.append(config_path)
        setup_required = not os.path.isfile(config_path)

    cfg = Config(defaults=DEFAULTS, files=config_files)

    # Determine current GPU name + profile
    from . import detect
    nv = detect.detect_nvidia()
    gpu_name = nv["gpus"][0]["name"] if nv.get("gpus") else ""
    profile = get_profile_for_gpu(profiles_dir, gpu_name) if gpu_name else None

    # SQLite storage (persistence) — expandable path
    db_path = os.path.expanduser(cfg.get("STORAGE_DB_PATH", "~/.local/share/gpu-dashboard/metrics.db"))
    storage = Storage(db_path)

    # Start sampler with storage attached
    sampler = MetricsSampler(
        interval=float(cfg.get_int("DASHBOARD_REFRESH_INTERVAL", default=5)),
        maxlen=cfg.get_int("DASHBOARD_SAMPLE_KEEP", default=720),
        nvidia_settings_display=cfg.get("CLOCK_OFFSETS_DISPLAY"),
        nvidia_settings_xauthority=cfg.get("CLOCK_OFFSETS_XAUTHORITY") or None,
        storage=storage,
        llm_server_url=cfg.get("LLM_SERVER_URL", "") or None,
    )
    sampler.start()

    # R&D #7.4 — optional InfluxDB pusher (background, only if URL set)
    influxdb_pusher = None
    if cfg.get("INFLUXDB_URL"):
        try:
            from .modules.influxdb_push import InfluxPusher
            influxdb_pusher = InfluxPusher(
                sampler=sampler, cfg=cfg,
                interval_s=float(cfg.get("INFLUXDB_INTERVAL", "15") or "15"),
            )
            influxdb_pusher.start()
            print(f"[influxdb] pusher started → {cfg.get('INFLUXDB_URL')}")
        except Exception as e:
            print(f"[influxdb] pusher disabled : {e}")

    # R&D #5.2 — drift detection : capture driver/kernel/ECC fingerprint
    # on each startup and diff vs the saved baseline. Cheap (one nvidia-smi
    # call) so it runs unconditionally.
    try:
        from . import api as _api
        drifts = _api.detect_drift_on_startup()
        if drifts:
            for d in drifts:
                print(f"[drift] {d['field']} : {d['old']!r} → {d['new']!r}")
    except Exception as e:
        print(f"[drift] check skipped : {e}")

    # Retention daemon (purge + vacuum)
    retention = RetentionTask(
        storage,
        retention_days=cfg.get_int("STORAGE_RETENTION_DAYS", default=30),
    )
    retention.start()

    # alert_monitor daemon (optional) — fires Telegram + webhook alerts on thresholds
    alert_monitor_daemon = None
    tg_enabled = cfg.get_bool("TG_ENABLED")
    webhook_enabled = cfg.get_bool("WEBHOOK_ENABLED")
    # Web Push is auto-active whenever there's at least one subscription
    # (and alert_monitor module is enabled — the gate stays the same).
    if cfg.get_bool("MODULE_ALERT_MONITOR") and (tg_enabled or webhook_enabled):
        try:
            from .modules.alert_monitor import AlertMonitorDaemon
            from .modules.telegram_alerts import send_message as _tg_send
            from .modules.webhook import send as _wh_send
            from .modules import web_push as _wp

            tg_token = cfg.get("TG_TOKEN", "")
            tg_chat = cfg.get("TG_CHAT", "")
            webhook_url = cfg.get("WEBHOOK_URL", "")
            vapid_cfg_dir = os.path.expanduser("~/.config/gpu-dashboard")

            def _send_alert(text):
                """Multi-channel dispatch — Telegram + webhook + browser push."""
                results = []
                if tg_enabled and tg_token and tg_chat:
                    try:
                        ok, msg = _tg_send(token=tg_token, chat_id=tg_chat, text=text)
                        results.append(("telegram", ok, msg))
                    except Exception as e:
                        results.append(("telegram", False, str(e)))
                if webhook_enabled and webhook_url:
                    try:
                        ok, msg = _wh_send(url=webhook_url, text=text, kind="alert")
                        results.append(("webhook", ok, msg))
                    except Exception as e:
                        results.append(("webhook", False, str(e)))
                # Browser push : send to ALL stored subscriptions
                if storage is not None:
                    try:
                        subs = storage.list_push_subscriptions()
                        if subs:
                            vapid = _wp.ensure_vapid_keys(vapid_cfg_dir)
                            for sub in subs:
                                ok, msg = _wp.send_push(sub, vapid)
                                results.append((f"push:{sub['endpoint'][:30]}...", ok, msg))
                                # Drop expired subscriptions (404 / 410)
                                if not ok and ("404" in msg or "410" in msg):
                                    storage.remove_push_subscription(sub["endpoint"])
                    except Exception as e:
                        results.append(("push", False, str(e)))
                return results

            # Best-effort: probe nvidia-smi once to learn VRAM capacity
            mem_total_mib = None
            try:
                from . import api as _api
                snap = _api._gpu_card_snapshot(gpu_index=cfg.get_int("GPU_INDEX", default=0))
                if snap.get("alive"):
                    mem_total_mib = snap.get("mem_total_mib")
            except Exception:
                pass

            alert_monitor_daemon = AlertMonitorDaemon(
                sampler=sampler,
                telegram_send_fn=_send_alert,
                thresholds={
                    "gpu_temp": cfg.get_int("ALERT_GPU_TEMP_THRESHOLD", default=85),
                    "mem_temp": cfg.get_int("ALERT_MEM_TEMP_THRESHOLD", default=95),
                    "fan_pct":  cfg.get_int("ALERT_FAN_PCT_THRESHOLD", default=95),
                    "vram_pct": cfg.get_int("ALERT_VRAM_PCT_THRESHOLD", default=90),
                    "mem_total_mib": mem_total_mib,
                    "min_consecutive": cfg.get_int("ALERT_MIN_CONSECUTIVE", default=3),
                    "cooldown_seconds": cfg.get_int("ALERT_COOLDOWN_SECONDS", default=300),
                },
                interval=float(cfg.get_int("ALERT_MONITOR_INTERVAL", default=30)),
            )
            alert_monitor_daemon.start()
        except Exception as e:
            print(f"warning: alert_monitor daemon failed: {e}", file=sys.stderr)

    # auto_profile daemon (optional) — must come after sampler is started
    auto_profile_daemon = None
    if cfg.get_bool("MODULE_AUTO_PROFILE"):
        try:
            from .modules.auto_profile import AutoProfileDaemon
            def _apply_named(name: str):
                # Lazy import to avoid circular reference
                from . import api as _api
                # Construct a minimal ctx for the apply handler
                ctx_local = {"config": cfg, "profile": profile}
                _api.handle_power_profile_apply(ctx_local, name)
            auto_profile_daemon = AutoProfileDaemon(
                sampler=sampler,
                api_apply_callback=_apply_named,
                interval=float(cfg.get_int("AUTO_PROFILE_INTERVAL", default=30)),
                window_seconds=cfg.get_int("AUTO_PROFILE_WINDOW", default=60),
                min_stable_seconds=cfg.get_int("AUTO_PROFILE_MIN_STABLE", default=90),
                idle_threshold=cfg.get_int("AUTO_PROFILE_IDLE_THRESHOLD", default=5),
                boost_threshold=cfg.get_int("AUTO_PROFILE_BOOST_THRESHOLD", default=80),
            )
            auto_profile_daemon.start()
        except Exception as e:
            print(f"warning: auto_profile daemon failed: {e}", file=sys.stderr)

    # fan_curve daemon (optional)
    fan_curve_daemon = None
    if cfg.get_bool("MODULE_FAN_CURVE"):
        try:
            from .modules import fan_curve as _fc
            curve = _fc.pick_curve(profile=profile)
            display = cfg.get("CLOCK_OFFSETS_DISPLAY", ":0")
            xauth = cfg.get("CLOCK_OFFSETS_XAUTHORITY") or None
            fan_curve_daemon = _fc.FanCurveDaemon(
                curve=curve, display=display, xauthority=xauth,
                interval=float(cfg.get_int("FAN_CURVE_INTERVAL", default=5)),
                sampler=sampler,
            )
            fan_curve_daemon.start()
        except Exception as e:
            print(f"warning: fan_curve daemon failed to start: {e}", file=sys.stderr)

    import time as _t
    home = os.path.expanduser("~")

    # Best-effort repo_path detection: walk up from this file until .git found
    repo_path = None
    p = os.path.dirname(os.path.abspath(__file__))
    for _ in range(6):
        if os.path.isdir(os.path.join(p, ".git")):
            repo_path = p; break
        parent = os.path.dirname(p)
        if parent == p: break
        p = parent

    return {
        "config": cfg, "profile": profile,
        "sampler": sampler, "storage": storage, "retention": retention,
        "fan_curve_daemon": fan_curve_daemon,
        "auto_profile_daemon": auto_profile_daemon,
        "alert_monitor_daemon": alert_monitor_daemon,
        "influxdb_pusher": influxdb_pusher,
        "setup_required": setup_required,
        "profiles_dir": profiles_dir,
        "overrides_dir": os.path.join(home, ".config/gpu-dashboard/profile-overrides"),
        "started_at": _t.time(),
        "config_path": (config_files[0] if config_files else os.path.join(home, ".config/gpu-dashboard/config.env")),
        "secrets_path": os.path.join(home, ".config/gpu-dashboard/secrets.env"),
        "storage_path": db_path,
        "repo_path": repo_path,
    }


# ─────────────────────────────── handler ──────────────────────────────────


def make_handler(ctx: dict):
    """Return a BaseHTTPRequestHandler subclass closed over `ctx`."""

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass  # silent

        def _send_json(self, code: int, body) -> None:
            data = json.dumps(body).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _send_static(self, filename: str) -> None:
            full = os.path.join(_STATIC_DIR, filename)
            if not os.path.isfile(full) or not os.path.abspath(full).startswith(os.path.abspath(_STATIC_DIR)):
                self.send_response(404)
                self.end_headers()
                return
            ctype = "text/html; charset=utf-8"
            if filename.endswith(".css"):
                ctype = "text/css"
            elif filename.endswith(".js"):
                ctype = "application/javascript"
            elif filename.endswith(".svg"):
                ctype = "image/svg+xml"
            elif filename.endswith(".png"):
                ctype = "image/png"
            elif filename.endswith(".json"):
                ctype = "application/json"
            with open(full, "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            # Cache policy (cycle 144 user fb : 'j'ai bien l'ancienne version')
            # - assets/<hashed>.{js,css,svg,png} : immutable (vite hashes by content)
            # - index.html / sw.js / manifest.json : no-cache so updates land instantly
            is_hashed_asset = ("assets/" in filename or filename.startswith("assets/")) and any(
                filename.endswith(ext) for ext in (".js", ".css", ".woff2", ".png", ".jpg", ".svg")
            )
            if is_hashed_asset:
                self.send_header("Cache-Control", "public, max-age=31536000, immutable")
            else:
                self.send_header("Cache-Control", "no-cache, must-revalidate")
                self.send_header("Pragma", "no-cache")
            self.end_headers()
            self.wfile.write(body)

        def _send_csv(self, code: int, body: str, filename: str = "gpu-history.csv") -> None:
            data = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _send_binary(self, code: int, body: bytes, content_type: str, filename: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            parsed = urlparse(self.path)
            path = parsed.path
            params = {k: v[0] for k, v in parse_qs(parsed.query).items()}

            if path in ("/", "/index.html"):
                self._send_static("index.html")
                return
            if path.startswith("/static/"):
                self._send_static(path[len("/static/"):])
                return
            # Service Worker must be served from root scope to control / .
            # Cycle 139 (user feedback) : `sw.js` was returning 404, breaking
            # Web Push registration.
            if path == "/sw.js":
                self._send_static("sw.js")
                return
            if path == "/manifest.json":
                self._send_static("manifest.json")
                return
            if path == "/api/state":
                code, body = api.handle_state(ctx, params)
                self._send_json(code, body)
                return
            if path == "/api/alerts-config":
                code, body = api.handle_alerts_config_get(ctx)
                self._send_json(code, body)
                return
            if path == "/api/history":
                code, body = api.handle_history(ctx, params)
                self._send_json(code, body)
                return
            if path == "/api/events":
                code, body = api.handle_events(ctx, params)
                self._send_json(code, body)
                return
            if path == "/api/export":
                code, body = api.handle_export(ctx, params)
                if isinstance(body, str):
                    self._send_csv(code, body)
                else:
                    self._send_json(code, body)
                return
            if path == "/api/export/year":
                code, body = api.handle_export_year(ctx, params)
                if isinstance(body, str):
                    self._send_csv(code, body)
                else:
                    self._send_json(code, body)
                return
            if path == "/api/setup/detect":
                code, body = api.handle_setup_detect(ctx)
                self._send_json(code, body)
                return
            if path.startswith("/api/setup/recheck/"):
                module = path[len("/api/setup/recheck/"):]
                code, body = api.handle_setup_recheck(ctx, module)
                self._send_json(code, body)
                return
            if path == "/api/about":
                code, body = api.handle_about(ctx)
                self._send_json(code, body)
                return
            if path == "/api/version":
                code, body = api.handle_version(ctx)
                self._send_json(code, body)
                return
            if path == "/api/modules":
                code, body = api.handle_modules_list(ctx)
                self._send_json(code, body)
                return
            if path == "/api/sysreport":
                code, body = api.handle_sysreport(ctx)
                self._send_json(code, body)
                return
            if path == "/api/sysreport/bundle":
                result = api.handle_sysreport_bundle(ctx)
                if len(result) == 4:
                    code, payload, ctype, fname = result
                    self._send_binary(code, payload, ctype, fname)
                else:
                    self._send_json(result[0], result[1])
                return
            if path == "/api/lifetime-stats":
                code, body = api.handle_lifetime_stats(ctx, params)
                self._send_json(code, body)
                return
            if path == "/api/app-triggers":
                code, body = api.handle_app_triggers_get(ctx)
                self._send_json(code, body)
                return
            if path == "/api/push/vapid":
                code, body = api.handle_push_vapid(ctx)
                self._send_json(code, body)
                return
            if path == "/api/push/status":
                code, body = api.handle_push_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/alerts/latest":
                code, body = api.handle_alerts_latest(ctx)
                self._send_json(code, body)
                return
            if path == "/api/health":
                code, body = api.handle_health(ctx)
                self._send_json(code, body)
                return
            if path == "/api/clock-events":
                code, body = api.handle_clock_events(ctx)
                self._send_json(code, body)
                return
            if path == "/api/idle-audit":
                code, body = api.handle_idle_audit(ctx)
                self._send_json(code, body)
                return
            if path == "/api/ecc-health":
                code, body = api.handle_ecc_health(ctx)
                self._send_json(code, body)
                return
            if path == "/api/drift":
                code, body = api.handle_drift_check(ctx)
                self._send_json(code, body)
                return
            if path == "/api/thermal/coach":
                code, body = api.handle_thermal_coach(ctx)
                self._send_json(code, body)
                return
            if path == "/api/heartbeat":
                code, body = api.handle_heartbeat_list(ctx)
                self._send_json(code, body)
                return
            if path == "/api/journal/tail":
                code, body = api.handle_journal_tail(ctx, params)
                self._send_json(code, body)
                return
            if path == "/api/notif/channels":
                code, body = api.handle_notif_channels_list(ctx)
                self._send_json(code, body)
                return
            if path == "/api/sys-context":
                code, body = api.handle_sys_context(ctx)
                self._send_json(code, body)
                return
            if path == "/api/cgroup-power":
                code, body = api.handle_cgroup_power(ctx)
                self._send_json(code, body)
                return
            if path == "/api/influxdb/status":
                code, body = api.handle_influxdb_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/hf-card":
                code, body = api.handle_hf_card(ctx, params)
                self._send_json(code, body)
                return
            if path == "/api/vector-db":
                code, body = api.handle_vector_db(ctx)
                self._send_json(code, body)
                return
            if path == "/healthz":
                code, body = api.handle_healthz(ctx)
                self._send_json(code, body)
                return
            if path == "/readyz":
                code, body = api.handle_readyz(ctx, params)
                self._send_json(code, body)
                return
            if path == "/api/watchdog/status":
                code, body = api.handle_watchdog_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/services-discovered":
                code, body = api.handle_service_discovery(ctx, params)
                self._send_json(code, body)
                return
            if path == "/api/calendar/events.ics":
                code, ics_body = api.handle_ical_feed(ctx, params)
                data = ics_body.encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "text/calendar; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Content-Disposition", 'inline; filename="gpu-events.ics"')
                self.send_header("Cache-Control", "public, max-age=300")
                self.end_headers()
                self.wfile.write(data)
                return
            if path == "/api/report/weekly":
                code, body_text = api.handle_weekly_report(ctx, params)
                fmt = (params or {}).get("fmt", "html")
                data = body_text.encode("utf-8")
                self.send_response(code)
                if fmt == "text":
                    self.send_header("Content-Type", "text/plain; charset=utf-8")
                else:
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                self.wfile.write(data)
                return
            if path.startswith("/badge/") and path.endswith(".svg"):
                # R&D #10.7 — live README SVG badge
                metric = path[len("/badge/"):-len(".svg")]
                code, svg = api.handle_badge(ctx, metric)
                data = svg.encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "image/svg+xml; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "public, max-age=60")
                self.end_headers()
                self.wfile.write(data)
                return
            if path == "/idle.txt":
                # R&D #17.7 — one-liner idle probe
                code, text = api.handle_idle_txt(ctx, params)
                data = text.encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                self.wfile.write(data)
                return
            if path == "/idle.json":
                code, body = api.handle_idle_json(ctx, params)
                self._send_json(code, body)
                return
            if path == "/api/ecc-remap":
                # R&D #17.1 — ECC remap scrubber status
                code, body = api.handle_ecc_remap_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/tdp-auto":
                # R&D #17.3 — TDP profile auto-switch status
                code, body = api.handle_tdp_auto_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/llm-swap":
                # R&D #17.5 — LLM hot-swap orchestrator
                code, body = api.handle_llm_swap_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/llm-swap/suggest":
                code, body = api.handle_llm_swap_suggest(ctx, params)
                self._send_json(code, body)
                return
            if path == "/api/cuda-advisor":
                # R&D #18.3 — CUDA_VISIBLE_DEVICES UUID drift detector
                code, body = api.handle_cuda_advisor_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/nvme-swap":
                # R&D #18.1 — NVMe-as-VRAM-swap monitor
                code, body = api.handle_nvme_swap_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/cuda-matrix":
                # R&D #18.2 — CUDA / cuDNN / driver compatibility matrix
                code, body = api.handle_cuda_matrix_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/pcie-histogram":
                # R&D #18.6 — PCIe link-state thrasher histogram
                code, body = api.handle_pcie_histogram_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/throttle-cause":
                # R&D #19.2 — Thermal/power throttle root-cause classifier
                code, body = api.handle_throttle_cause_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/mps-health":
                # R&D #19.6 — CUDA MPS daemon health probe
                code, body = api.handle_mps_health_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/process-nice":
                # R&D #19.1 — GPU process nice/ionice advisor
                code, body = api.handle_process_nice_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/warmup-profile":
                # R&D #19.4 — Per-model warm-up profiler
                code, body = api.handle_warmup_profile_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/suspend-guard":
                # R&D #20.5 — Hibernate / suspend safety preflight
                code, body = api.handle_suspend_guard_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/container-audit":
                # R&D #20.1 — NVIDIA Container Toolkit GPU visibility audit
                code, body = api.handle_container_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/ups-runtime":
                # R&D #20.7 — UPS runtime vs GPU-load estimator
                code, body = api.handle_ups_runtime_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/vbios-drift":
                # R&D #20.2 — VBIOS / ROM drift tracker
                code, body = api.handle_vbios_drift_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/pstate-audit":
                # R&D #21.1 — P-state pinning advisor
                code, body = api.handle_pstate_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/persistence-mode":
                # R&D #21.2 — nvidia-persistenced state check
                code, body = api.handle_persistence_mode_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/gsp-status":
                # R&D #21.3 — GSP-RM crash + fallback surfacer
                code, body = api.handle_gsp_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/sd-cache-janitor":
                # R&D #21.5 — SD / ComfyUI cache janitor
                code, body = api.handle_sd_cache_janitor_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/vram-leak":
                # R&D #22.3 — per-process VRAM leak detector
                code, body = api.handle_vram_leak_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/gpu-reset":
                # R&D #22.1 — GPU reset counter / RMA-candidate detector
                code, body = api.handle_gpu_reset_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/cuda-inventory":
                # R&D #22.5 — CUDA toolkit inventory + collision detector
                code, body = api.handle_cuda_inventory_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/driver-flavor":
                # R&D #22.2 — Open vs proprietary driver advisor
                code, body = api.handle_driver_flavor_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/proc-deep-state":
                # R&D #23.6 — /proc/driver/nvidia/gpus deep-state diff
                code, body = api.handle_proc_deep_state_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/pcie-aspm":
                # R&D #23.4 — PCIe ASPM audit
                code, body = api.handle_pcie_aspm_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/fs-mount-audit":
                # R&D #23.2 — FS mount-option auditor
                code, body = api.handle_fs_mount_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/batch-advisor":
                # R&D #23.1 — Batch-size / ctx-length advisor
                code, body = api.handle_batch_advisor_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/dkms-status":
                # R&D #24.3 — DKMS rebuild status
                code, body = api.handle_dkms_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/pcie-aer":
                # R&D #24.2 — PCIe Advanced Error Reporting counter
                code, body = api.handle_pcie_aer_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/mem-temp-drift":
                # R&D #24.4 — VRAM thermal-pad drift detector
                code, body = api.handle_mem_temp_drift_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/accounting":
                # R&D #24.1 — NVML accounting harvester
                code, body = api.handle_accounting_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/trim-audit":
                # R&D #25.2 — TRIM / discard auditor
                code, body = api.handle_trim_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/throttle-bits":
                # R&D #25.5 — Per-bit throttle reason decoder
                code, body = api.handle_throttle_bits_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/retired-pages":
                # R&D #25.1 — retired-page / row-remap trend
                code, body = api.handle_retired_pages_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/bug-report-prep":
                # R&D #25.3 — NVIDIA bug-report ticket prepper
                code, body = api.handle_bug_report_prep_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/pcie-width-watcher":
                # R&D #26.5 — silent PCIe link-width downgrade watcher
                code, body = api.handle_pcie_width_watcher_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/cuda-ctx-leak":
                # R&D #26.2 — zombie CUDA-FD detector
                code, body = api.handle_cuda_ctx_leak_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/proc-static-audit":
                # R&D #26.1 — per-boot static PCI auditor
                code, body = api.handle_proc_static_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/mem-bw-gauge":
                # R&D #26.8 — memory-bandwidth saturation gauge
                code, body = api.handle_mem_bw_gauge_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/power-envelope-drift":
                # R&D #27.4 — silent power-limit reset detector
                code, body = api.handle_power_envelope_drift_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/rebar-audit":
                # R&D #27.1 — Resizable-BAR (ReBAR) auditor
                code, body = api.handle_rebar_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/cpu-rapl":
                # R&D #27.3 — CPU-package RAPL harvester
                code, body = api.handle_cpu_rapl_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/clock-gap":
                # R&D #27.7 — applied-vs-enforced clock gap detector
                code, body = api.handle_clock_gap_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/pcie-rpm-audit":
                # R&D #28.1 — PCIe runtime-PM auditor
                code, body = api.handle_pcie_rpm_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/thermal-zones":
                # R&D #28.5 — system thermal-zone correlator
                code, body = api.handle_thermal_zones_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/nvrm-tail":
                # R&D #28.7 — kernel-NVRM / GSP / NvKms log tailer
                code, body = api.handle_nvrm_tail_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/nvlink-health":
                # R&D #28.4 — NVLink CRC / replay error tracker
                code, body = api.handle_nvlink_health_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/kmod-params":
                # R&D #29.1 — NVIDIA kmod parameter auditor
                code, body = api.handle_kmod_params_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/thermal-slowdown-kind":
                # R&D #29.7 — HW vs SW thermal slowdown distinguisher
                code, body = api.handle_thermal_slowdown_kind_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/d3cold-policy":
                # R&D #29.3 — parent-bridge D3cold policy auditor
                code, body = api.handle_d3cold_policy_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/rlimit-audit":
                # R&D #29.8 — rlimit auditor for LLM daemons
                code, body = api.handle_rlimit_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/dmi-bios":
                # R&D #30.5 — DMI/BIOS revision tracker
                code, body = api.handle_dmi_bios_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/nvme-iosched":
                # R&D #30.3 — NVMe I/O scheduler tuner
                code, body = api.handle_nvme_iosched_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/iommu-groups":
                # R&D #30.2 — IOMMU group + DMA-passthrough auditor
                code, body = api.handle_iommu_groups_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/msi-inventory":
                # R&D #30.1 — MSI-X vector inventory
                code, body = api.handle_msi_inventory_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/oom-priority":
                # R&D #31.4 — OOM-priority auditor for inference daemons
                code, body = api.handle_oom_priority_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/cpu-topology":
                # R&D #31.3 — CPU topology + governor pinning advisor
                code, body = api.handle_cpu_topology_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/proc-smaps":
                # R&D #31.2 — smaps_rollup residence breakdown
                code, body = api.handle_proc_smaps_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/hwmon-inventory":
                # R&D #31.1 — hwmon NVMe + chipset parity
                code, body = api.handle_hwmon_inventory_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/vm-sysctl":
                # R&D #32.4 — VM sysctl LLM-rig sanity audit
                code, body = api.handle_vm_sysctl_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/psi-pressure":
                # R&D #32.1 — PSI pressure-stall correlator
                code, body = api.handle_psi_pressure_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/proc-wchan":
                # R&D #32.3 — wchan + stack inference-stuck debugger
                code, body = api.handle_proc_wchan_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/cgroup-memcap":
                # R&D #32.5 — cgroup-v2 memory-cap scanner
                code, body = api.handle_cgroup_memcap_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/clocksource":
                # R&D #33.4 — kernel clocksource audit
                code, body = api.handle_clocksource_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/nic-health":
                # R&D #33.1 — LAN NIC health correlator
                code, body = api.handle_nic_health_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/proc-io":
                # R&D #33.2 — /proc/<pid>/io per-daemon accounting
                code, body = api.handle_proc_io_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/cgroup-cpuio":
                # R&D #33.6 — cgroup-v2 CPU/IO weight scanner
                code, body = api.handle_cgroup_cpuio_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/thp-audit":
                # R&D #34.1 — transparent_hugepage auditor
                code, body = api.handle_thp_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/buddyinfo":
                # R&D #34.2 — memory fragmentation auditor
                code, body = api.handle_buddyinfo_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/proc-sched":
                # R&D #34.4 — per-daemon scheduler stats
                code, body = api.handle_proc_sched_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/oomd":
                # R&D #34.3 — systemd-oomd kill-event correlator
                code, body = api.handle_oomd_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/cpu-boost":
                # R&D #35.1 — CPU turbo/boost runtime toggle audit
                code, body = api.handle_cpu_boost_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/net-sysctl":
                # R&D #35.2 — LAN socket-buffer sysctl audit
                code, body = api.handle_net_sysctl_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/smt-audit":
                # R&D #35.4 — SMT toggle + offline-core audit
                code, body = api.handle_smt_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/numa-placement":
                # R&D #35.3 — NUMA placement auditor
                code, body = api.handle_numa_placement_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/kernel-taint":
                # R&D #36.3 — kernel taint flags + uptime correlator
                code, body = api.handle_kernel_taint_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/cpu-microcode":
                # R&D #36.1 — CPU microcode revision audit
                code, body = api.handle_cpu_microcode_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/hwp-epp":
                # R&D #36.4 — HWP EPP string-mode audit
                code, body = api.handle_hwp_epp_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/cpuidle":
                # R&D #36.2 — cpuidle C-state exit-latency audit
                code, body = api.handle_cpuidle_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/limits-audit":
                # bench-class — PAM limits memlock audit
                code, body = api.handle_limits_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/cpu-vulns":
                # R&D #37.1 — CPU vulnerabilities mitigation cost
                code, body = api.handle_cpu_vulns_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/hw-watchdog":
                # R&D #37.3 — hardware watchdog auditor
                code, body = api.handle_hw_watchdog_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/gpu-cpu-affinity":
                # R&D #37.2 — GPU↔CPU PCIe local_cpulist advisor
                code, body = api.handle_gpu_cpu_affinity_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/cache-topology":
                # R&D #37.4 — L3 cache topology placement advisor
                code, body = api.handle_cache_topology_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/pcie-aer-trend":
                # R&D #38.1 — PCIe AER counter trend tracker
                code, body = api.handle_pcie_aer_trend_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/gpu-irq-affinity":
                # R&D #38.4 — GPU MSI-X IRQ affinity advisor
                code, body = api.handle_gpu_irq_affinity_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/modprobe-audit":
                # R&D #38.2 — modprobe.d on-disk vs runtime drift
                code, body = api.handle_modprobe_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/proc-maps-libs":
                # R&D #38.3 — /proc/<pid>/maps shared-lib drift
                code, body = api.handle_proc_maps_libs_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/cmdline-audit":
                # R&D #39.1 — /proc/cmdline boot-param auditor
                code, body = api.handle_cmdline_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/coredump":
                # R&D #39.3 — coredump readiness auditor
                code, body = api.handle_coredump_ready_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/host-class":
                # R&D #39.4 — host_class chassis + virt + form-factor
                code, body = api.handle_host_class_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/sysctl-d-audit":
                # R&D #39.2 — sysctl.d on-disk vs runtime drift
                code, body = api.handle_sysctl_d_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/ksm-advisor":
                # R&D #40.2 — KSM hurts-LLM detector
                code, body = api.handle_ksm_advisor_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/vm-tuning-deep":
                # R&D #40.3 — page-cluster / kswapd / vfs reclaim
                code, body = api.handle_vm_tuning_deep_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/gpu-pci-bind":
                # R&D #40.1 — GPU PCIe driver-binding inventory
                code, body = api.handle_gpu_pci_bind_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/nic-queue-affinity":
                # R&D #40.4 — NIC RX/TX queue + RPS/XPS auditor
                code, body = api.handle_nic_queue_affinity_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/panic-policy":
                # R&D #41.3 — panic + hung_task + softlockup policy
                code, body = api.handle_panic_policy_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/edac-ram-ecc":
                # R&D #41.2 — EDAC RAM ECC + DIMM-label auditor
                code, body = api.handle_edac_ram_ecc_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/inotify-audit":
                # R&D #41.4 — inotify/fanotify watch auditor
                code, body = api.handle_inotify_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/zswap-zram":
                # R&D #41.1 — zswap + zram compressed-swap auditor
                code, body = api.handle_zswap_zram_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/cpu-epb":
                # R&D #42.4 — legacy MSR_IA32_ENERGY_PERF_BIAS audit
                code, body = api.handle_cpu_epb_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/cooling-devices":
                # R&D #42.3 — thermal cooling-device inventory
                code, body = api.handle_cooling_devices_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/hybrid-cpu-topo":
                # R&D #42.2 — hybrid CPU cluster/die topology
                code, body = api.handle_hybrid_cpu_topo_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/file-locks-audit":
                # R&D #42.1 — /proc/locks model-file contention
                code, body = api.handle_file_locks_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/nic-ring-audit":
                # R&D #43.4 — NIC ring-buffer drop + FIFO overrun
                code, body = api.handle_nic_ring_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/irq-rates-audit":
                # R&D #43.1 — IRQ rate + softirq imbalance
                code, body = api.handle_irq_rates_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/zoneinfo-audit":
                # R&D #43.3 — zoneinfo + vmstat reclaim/compaction
                code, body = api.handle_zoneinfo_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/block-queue-audit":
                # R&D #43.2 — block layer per-queue 15-knob audit
                code, body = api.handle_block_queue_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/watchdog-inventory":
                # R&D #44.3 — watchdog device enumeration
                code, body = api.handle_watchdog_inventory_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/net-proto-counters":
                # R&D #44.4 — TCP/UDP protocol counter auditor
                code, body = api.handle_net_proto_counters_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/disk-io-latency":
                # R&D #44.1 — /proc/diskstats latency histograms
                code, body = api.handle_disk_io_latency_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/slab-audit":
                # R&D #44.2 — SLUB slab-cache leak/fragmentation
                code, body = api.handle_slab_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/entropy-audit":
                # R&D #45.4 — entropy + hwrng auditor
                code, body = api.handle_entropy_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/nf-conntrack-audit":
                # R&D #45.1 — netfilter conntrack auditor
                code, body = api.handle_nf_conntrack_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/sysvipc-audit":
                # R&D #45.3 — SysV IPC leak detector
                code, body = api.handle_sysvipc_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/mdraid-health":
                # R&D #45.2 — software RAID array health
                code, body = api.handle_mdraid_health_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/keyring-audit":
                # R&D #46.4 — kernel keyring quota auditor
                code, body = api.handle_keyring_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/security-posture":
                # R&D #46.2 — LSM + lockdown + paranoia
                code, body = api.handle_security_posture_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/vfs-limits-audit":
                # R&D #46.3 — VFS + io_uring headroom
                code, body = api.handle_vfs_limits_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/nvidia-rm-audit":
                # R&D #47.3 — nvidia RM registry + capabilities
                code, body = api.handle_nvidia_rm_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/mce-audit":
                # R&D #47.4 — MCE bank + CMCI auditor
                code, body = api.handle_mce_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/acpi-audit":
                # R&D #47.2 — ACPI platform-profile + GPE
                code, body = api.handle_acpi_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/sched-audit":
                # R&D #47.1 — CFS runqueue-wait + features
                code, body = api.handle_sched_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/dma-audit":
                # R&D #48.3 — DMA engine + SWIOTLB
                code, body = api.handle_dma_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/ftrace-audit":
                # R&D #48.1 — ftrace orphan + tracer-left-on
                code, body = api.handle_ftrace_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/usb-topology-audit":
                # R&D #48.2 — USB tree power + speed + autosuspend
                code, body = api.handle_usb_topology_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/journal-audit":
                # R&D #48.4 — systemd journal config + storage
                code, body = api.handle_journal_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/rtc-clock-audit":
                # R&D #49.4 — RTC + PPS + hrtimer
                code, body = api.handle_rtc_clock_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/tpm-audit":
                # R&D #49.2 — TPM 1.2/2.0 inventory + measured boot
                code, body = api.handle_tpm_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/wmi-vendor-audit":
                # R&D #49.3 — WMI + vendor platform driver audit
                code, body = api.handle_wmi_vendor_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/kmsg-audit":
                # R&D #49.1 — /dev/kmsg + printk ratelimit
                code, body = api.handle_kmsg_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/sock-pool-audit":
                # R&D #50.4 — socket pool + TIME_WAIT
                code, body = api.handle_sock_pool_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/iio-sensor-audit":
                # R&D #50.3 — IIO sensor inventory
                code, body = api.handle_iio_sensor_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/drm-audit":
                # R&D #50.1 — DRM connector + EDID + modes
                code, body = api.handle_drm_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/cgroup-memevents-audit":
                # R&D #50.2 — cgroup v2 memory.events per unit
                code, body = api.handle_cgroup_memevents_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/power-supply-audit":
                # R&D #51.1 — battery/AC/UPS health
                code, body = api.handle_power_supply_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/typec-audit":
                # R&D #51.4 — USB-C alt-mode + PD contract
                code, body = api.handle_typec_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/perf-pmu-audit":
                # R&D #51.3 — perf PMU inventory
                code, body = api.handle_perf_pmu_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/iomem-pci-audit":
                # R&D #51.2 — IOMEM + PCI BAR + reset method auditor
                code, body = api.handle_iomem_pci_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/ksm-audit":
                # R&D #52.1 — KSM + THP mm-knob auditor
                code, body = api.handle_ksm_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/i2c-smbus-audit":
                # R&D #52.2 — I2C / SMBus / DDC auditor
                code, body = api.handle_i2c_smbus_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/module-integrity-audit":
                # R&D #52.3 — kernel module integrity auditor
                code, body = api.handle_module_integrity_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/psi-pressure-audit":
                # R&D #53.1 — PSI pressure stall auditor
                code, body = api.handle_psi_pressure_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/cpu-vulnerabilities-audit":
                # R&D #53.2 — CPU vulnerabilities + SMT auditor
                code, body = api.handle_cpu_vulnerabilities_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/rapl-power-cap-audit":
                # R&D #53.4 — RAPL + cpufreq throttling auditor
                code, body = api.handle_rapl_power_cap_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/ima-integrity-audit":
                # R&D #53.3 — IMA / EVM / SecureBoot auditor
                code, body = api.handle_ima_integrity_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/swap-tunables-audit":
                # R&D #54.2 — swap-pathway tunables
                code, body = api.handle_swap_tunables_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/hugepages-audit":
                # R&D #54.1 — explicit HugeTLB pool auditor
                code, body = api.handle_hugepages_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/io-uring-runtime-audit":
                # R&D #54.4 — io_uring runtime gates
                code, body = api.handle_io_uring_runtime_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/kvm-misc-audit":
                # R&D #54.3 — KVM runtime + nested + perms
                code, body = api.handle_kvm_misc_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/edac-ecc-audit":
                # R&D #55.1 — DRAM ECC counters via EDAC
                code, body = api.handle_edac_ecc_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/efi-boot-order-audit":
                # R&D #55.4 — EFI boot vars + dbx + varstore
                code, body = api.handle_efi_boot_order_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/numa-topology-audit":
                # R&D #55.2 — NUMA topology + GPU affinity
                code, body = api.handle_numa_topology_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/hwmon-sensors-audit":
                # R&D #55.3 — fans + voltages + PWM (hwmon)
                code, body = api.handle_hwmon_sensors_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/sata-link-pm-audit":
                # R&D #56.1 — SATA Aggressive Link PM
                code, body = api.handle_sata_link_pm_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/bdi-writeback-audit":
                # R&D #56.2 — per-BDI writeback + readahead
                code, body = api.handle_bdi_writeback_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/proc-crypto-audit":
                # R&D #56.3 — /proc/crypto + FIPS + AES-NI
                code, body = api.handle_proc_crypto_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/wakeup-sources-audit":
                # R&D #56.4 — kernel wakeup sources / GPE chatter
                code, body = api.handle_wakeup_sources_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/livepatch-audit":
                # R&D #57.1 — kernel live-patch state
                code, body = api.handle_livepatch_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/backlight-pwm-audit":
                # R&D #57.3 — backlight + PWM chip
                code, body = api.handle_backlight_pwm_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/loadavg-pressure-audit":
                # R&D #57.4 — loadavg + procs_blocked + RT throttle
                code, body = api.handle_loadavg_pressure_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/pagetypeinfo-audit":
                # R&D #57.2 — buddy allocator fragmentation
                code, body = api.handle_pagetypeinfo_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/cgroup-root-audit":
                # R&D #58.1 — cgroup v2 root delegation
                code, body = api.handle_cgroup_root_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/scsi-transport-audit":
                # R&D #58.3 — SCSI mid-layer transport
                code, body = api.handle_scsi_transport_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/alsa-cards-audit":
                # R&D #58.4 — ALSA cards + HDA runtime PM
                code, body = api.handle_alsa_cards_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/kernel-build-config-audit":
                # R&D #58.2 — static kernel build config
                code, body = api.handle_kernel_build_config_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/dmi-smbios-audit":
                # R&D #59.1 — DMI / SMBIOS / BIOS-age
                code, body = api.handle_dmi_smbios_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/pid-rlimits-audit":
                # R&D #59.4 — daemon + LLM-process rlimits
                code, body = api.handle_pid_rlimits_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/iommu-groups-audit":
                # R&D #59.2 — IOMMU groups + passthrough
                code, body = api.handle_iommu_groups_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/virt-guest-detect-audit":
                # R&D #60.4 — VM guest detection (qemu_fw_cfg + cpuinfo)
                code, body = api.handle_virt_guest_detect_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/regulator-audit":
                # R&D #61.3 — voltage regulator framework
                code, body = api.handle_regulator_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/alsa-codec-deep-audit":
                # R&D #61.4 — per-codec deep dump
                code, body = api.handle_alsa_codec_deep_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/devfreq-audit":
                # R&D #62.1 — devfreq DVFS scaling
                code, body = api.handle_devfreq_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/mei-intel-me-audit":
                # R&D #62.2 — Intel ME / MEI status
                code, body = api.handle_mei_intel_me_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/memory-hotplug-audit":
                # R&D #62.3 — memory hotplug blocks
                code, body = api.handle_memory_hotplug_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/proc-task-affinity-audit":
                # R&D #62.4 — task affinity vs GPU local CPUs
                code, body = api.handle_proc_task_affinity_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/rfkill-bluetooth-audit":
                # R&D #63.1 — rfkill + Bluetooth power gates
                code, body = api.handle_rfkill_bluetooth_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/leds-class-audit":
                # R&D #63.3 — /sys/class/leds inventory
                code, body = api.handle_leds_class_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/binfmt-misc-audit":
                # R&D #63.4 — binfmt_misc registrations
                code, body = api.handle_binfmt_misc_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/ptp-clock-audit":
                # R&D #63.2 — PTP hardware clocks
                code, body = api.handle_ptp_clock_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/mei-hdcp-pxp-audit":
                # R&D #64.2 — MEI HDCP + PXP subclasses
                code, body = api.handle_mei_hdcp_pxp_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/firmware-edd-mmc-audit":
                # R&D #64.4 — EDD + MMC/eMMC wear
                code, body = api.handle_firmware_edd_mmc_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/devlink-smartnic-audit":
                # R&D #64.1 — kernel device-link framework
                code, body = api.handle_devlink_smartnic_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/proc-ns-mountinfo-audit":
                # R&D #64.3 — per-PID namespaces + mountinfo
                code, body = api.handle_proc_ns_mountinfo_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/efi-runtime-map-audit":
                # R&D #65.3 — UEFI runtime map
                code, body = api.handle_efi_runtime_map_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/devfreq-event-audit":
                # R&D #65.4 — devfreq event PMU counters
                code, body = api.handle_devfreq_event_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/cpuidle-residency-audit":
                # R&D #65.1 — per-CPU per-state cpuidle residency
                code, body = api.handle_cpuidle_residency_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/cpufreq-residency-audit":
                # R&D #65.2 — cpufreq time_in_state per CPU
                code, body = api.handle_cpufreq_residency_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/mtd-flash-audit":
                # R&D #66.1 — /sys/class/mtd NOR/NAND
                code, body = api.handle_mtd_flash_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/spi-firmware-loader-audit":
                # R&D #66.4 — SPI + firmware loader + profiling
                code, body = api.handle_spi_firmware_loader_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/proc-syscall-auxv-audit":
                # R&D #66.3 — /proc/<pid>/{syscall,auxv,timerslack}
                code, body = api.handle_proc_syscall_auxv_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/btf-bpf-audit":
                # R&D #66.2 — /sys/kernel/btf + /sys/fs/bpf
                code, body = api.handle_btf_bpf_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/efi-esrt-audit":
                # R&D #67.1 — /sys/firmware/efi/esrt (fwupd capsule)
                code, body = api.handle_efi_esrt_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/vmallocinfo-audit":
                # R&D #67.3 — /proc/vmallocinfo (kernel virt space)
                code, body = api.handle_vmallocinfo_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/fdinfo-kinds-audit":
                # R&D #67.2 — /proc/*/fd anon_inode classification
                code, body = api.handle_fdinfo_kinds_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/timer-list-audit":
                # R&D #67.4 — /proc/timer_list hrtimers + NO_HZ + bcast
                code, body = api.handle_timer_list_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/pstore-crashlog-audit":
                # R&D #68.1 — /sys/fs/pstore persistent crash logs
                code, body = api.handle_pstore_crashlog_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/lru-gen-mglru-audit":
                # R&D #68.2 — /sys/kernel/mm/lru_gen MGLRU reclaim
                code, body = api.handle_lru_gen_mglru_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/dt-memmap-firmware-audit":
                # R&D #68.4 — devicetree/memmap/vmcoreinfo handoff
                code, body = api.handle_dt_memmap_firmware_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/fs-specific-tunables-audit":
                # R&D #68.3 — ext4/xfs/f2fs per-FS error counters
                code, body = api.handle_fs_specific_tunables_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/nvmem-inventory-audit":
                # R&D #69.1 — /sys/bus/nvmem inventory + perms
                code, body = api.handle_nvmem_inventory_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/damon-cma-audit":
                # R&D #69.3 — DAMON kdamonds + CMA region health
                code, body = api.handle_damon_cma_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/proc-static-kernel-registry-audit":
                # R&D #69.4 — modules+devices+misc+filesystems+consoles
                code, body = api.handle_proc_static_kernel_registry_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/kpageflags-audit":
                # R&D #69.2 — /proc/kpageflags per-page flag audit
                code, body = api.handle_kpageflags_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/remoteproc-coprocessor-audit":
                # R&D #70.1 — /sys/class/remoteproc coprocessor state
                code, body = api.handle_remoteproc_coprocessor_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/uio-gpio-userland-audit":
                # R&D #70.3 — /sys/class/{uio,gpio} userland I/O
                code, body = api.handle_uio_gpio_userland_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/devcoredump-inventory-audit":
                # R&D #70.4 — /sys/class/devcoredump pending dumps
                code, body = api.handle_devcoredump_inventory_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/cxl-dax-memory-audit":
                # R&D #70.2 — CXL + DAX + nvdimm tier-2 memory
                code, body = api.handle_cxl_dax_memory_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/usb-role-switch-audit":
                # R&D #71.2 — /sys/class/usb_role + typec roles
                code, body = api.handle_usb_role_switch_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/page-idle-tracking-audit":
                # R&D #71.3 — /sys/kernel/mm/page_idle bitmap
                code, body = api.handle_page_idle_tracking_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/edac-dimm-ce-trend-audit":
                # R&D #71.4 — per-DIMM EDAC CE/UE trend
                code, body = api.handle_edac_dimm_ce_trend_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/ata-port-sata-audit":
                # R&D #71.1 — /sys/class/ata_{port,link,device} SATA
                code, body = api.handle_ata_port_sata_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/fw-cfg-blob-audit":
                # R&D #72.1 — /sys/firmware/qemu_fw_cfg entries
                code, body = api.handle_fw_cfg_blob_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/uevent-helper-audit":
                # R&D #72.4 — /sys/kernel/uevent_helper hotplug
                code, body = api.handle_uevent_helper_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/dmi-entries-raw-audit":
                # R&D #72.2 — /sys/firmware/dmi/entries raw SMBIOS
                code, body = api.handle_dmi_entries_raw_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/tracing-events-enable-audit":
                # R&D #72.3 — /sys/kernel/tracing/events/* enable
                code, body = api.handle_tracing_events_enable_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/process-id-limits-audit":
                # R&D #73.1 — pid_max / threads-max / max_map_count
                code, body = api.handle_process_id_limits_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/sysctl-dev-subtree-audit":
                # R&D #73.2 — /proc/sys/dev knobs
                code, body = api.handle_sysctl_dev_subtree_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/kernel-notes-vmcoreinfo-audit":
                # R&D #73.4 — kexec / vmcoreinfo / notes kdump prep
                code, body = api.handle_kernel_notes_vmcoreinfo_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/firmware-attributes-audit":
                # R&D #73.3 — vendor BIOS attributes (Dell/Lenovo/HP)
                code, body = api.handle_firmware_attributes_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/cpu-isolation-audit":
                # R&D #74.1 — CPU isolation / nohz_full / offline
                code, body = api.handle_cpu_isolation_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/dma-heap-audit":
                # R&D #74.2 — /sys/class/dma_heap inventory + perms
                code, body = api.handle_dma_heap_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/abi-compat-audit":
                # R&D #74.3 — /proc/sys/abi + ia32_emulation + binfmt
                code, body = api.handle_abi_compat_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/v4l2-media-audit":
                # R&D #74.4 — /sys/class/{video4linux,media,cec}
                code, body = api.handle_v4l2_media_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/misc-chardev-audit":
                # R&D #75.2 — /proc/misc + /sys/class/misc dev nodes
                code, body = api.handle_misc_chardev_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/sgx-enclave-audit":
                # R&D #75.3 — Intel SGX enclave readiness
                code, body = api.handle_sgx_enclave_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/lsm-subtree-audit":
                # R&D #75.1 — /sys/kernel/security per-LSM subtree
                code, body = api.handle_lsm_subtree_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/ipv4-conf-per-iface-audit":
                # R&D #75.4 — /proc/sys/net/ipv4/conf per-iface knobs
                code, body = api.handle_ipv4_conf_per_iface_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/input-device-audit":
                # R&D #76.4 — /sys/class/input/event* wakeup posture
                code, body = api.handle_input_device_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/ipv6-conf-per-iface-audit":
                # R&D #76.2 — /proc/sys/net/ipv6/conf per-iface
                code, body = api.handle_ipv6_conf_per_iface_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/wmi-bus-audit":
                # R&D #76.1 — /sys/devices/virtual/wmi_bus per-instance
                code, body = api.handle_wmi_bus_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/numa-hmat-access-audit":
                # R&D #76.3 — NUMA HMAT/CDAT access matrix
                code, body = api.handle_numa_hmat_access_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/cpu-thermal-throttle-counters-audit":
                # R&D #77.2 — per-CPU thermal + RAPL throttle counters
                code, body = api.handle_cpu_thermal_throttle_counters_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/pci-sriov-posture-audit":
                # R&D #77.4 — PCI SR-IOV VF posture
                code, body = api.handle_pci_sriov_posture_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/cpu-cppc-audit":
                # R&D #77.1 — ACPI CPPC perf envelope
                code, body = api.handle_cpu_cppc_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/pcie-aer-fleet-audit":
                # R&D #77.3 — fleet-wide per-device PCIe AER
                code, body = api.handle_pcie_aer_fleet_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/net-iface-counters-audit":
                # R&D #78.1 — per-iface NIC error counters
                code, body = api.handle_net_iface_counters_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/net-stacking-topology-audit":
                # R&D #78.2 — bond/bridge stacking sanity
                code, body = api.handle_net_stacking_topology_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/proc-maps-anomaly-audit":
                # R&D #78.4 — /proc/<pid>/maps anomaly scan
                code, body = api.handle_proc_maps_anomaly_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/softnet-stat-audit":
                # R&D #79.1 — per-CPU softirq packet health
                code, body = api.handle_softnet_stat_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/route-table-audit":
                # R&D #79.2 — IPv4/IPv6 routing-table sanity
                code, body = api.handle_route_table_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/fb-vtconsole-audit":
                # R&D #79.3 — framebuffer / vtconsole handoff
                code, body = api.handle_fb_vtconsole_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/sched-tunables-audit":
                # R&D #79.4 — sched_* tunable safety
                code, body = api.handle_sched_tunables_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/arp-neighbor-audit":
                # R&D #80.1 — IPv4 ARP / neighbor-cache health
                code, body = api.handle_arp_neighbor_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/snmp6-icmp-audit":
                # R&D #80.2 — IPv6 ICMP / ND / MLD counters
                code, body = api.handle_snmp6_icmp_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/btrfs-allocator-audit":
                # R&D #80.3 — btrfs chunk allocator pressure
                code, body = api.handle_btrfs_allocator_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/proc-status-caps-audit":
                # R&D #80.4 — capability / hardening posture
                code, body = api.handle_proc_status_caps_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/xhci-companion-audit":
                # R&D #81.1 — xHCI USB 3 / USB 2 companion check
                code, body = api.handle_xhci_companion_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/bpf-program-inventory-audit":
                # R&D #81.2 — pinned BPF inventory + fdinfo scan
                code, body = api.handle_bpf_program_inventory_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/cgroup-io-stat-audit":
                # R&D #81.3 — per-cgroup io.stat + io.pressure
                code, body = api.handle_cgroup_io_stat_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/thermal-trip-drift-audit":
                # R&D #81.4 — thermal-zone trip configuration
                code, body = api.handle_thermal_trip_drift_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/sysrq-mask-audit":
                # R&D #82.1 — SysRq + kexec hardening posture
                code, body = api.handle_sysrq_mask_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/cpu-dma-latency-qos-audit":
                # R&D #82.2 — CPU PM-QoS + /dev/cpu_dma_latency
                code, body = api.handle_cpu_dma_latency_qos_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/rcu-expedited-audit":
                # R&D #82.3 — RCU expedited / isolation coherence
                code, body = api.handle_rcu_expedited_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/page-owner-frag-audit":
                # R&D #82.4 — extfrag/unusable index + page_owner
                code, body = api.handle_page_owner_frag_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/block-integrity-audit":
                # R&D #83.1 — T10-PI / DIF block-layer integrity
                code, body = api.handle_block_integrity_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/clk-summary-audit":
                # R&D #83.2 — Linux common-clock framework state
                code, body = api.handle_clk_summary_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/nfsd-stats-audit":
                # R&D #83.3 — NFS server thread pool + DRC
                code, body = api.handle_nfsd_stats_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/dri-debugfs-audit":
                # R&D #83.4 — DRM client / GEM handle scan
                code, body = api.handle_dri_debugfs_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/suspend-stats-audit":
                # R&D #84.1 — S3 / S2idle suspend success rate
                code, body = api.handle_suspend_stats_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/loop-device-audit":
                # R&D #84.2 — loop device backing-file audit
                code, body = api.handle_loop_device_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/kernel-module-params-drift-audit":
                # R&D #84.3 — module parameter drift from defaults
                code, body = api.handle_kernel_module_params_drift_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/tty-serial-console-audit":
                # R&D #84.4 — TTY / serial / console inventory
                code, body = api.handle_tty_serial_console_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/dynamic-debug-audit":
                # R&D #85.1 — kernel dyndbg call-site inventory
                code, body = api.handle_dynamic_debug_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/extcon-state-audit":
                # R&D #85.2 — extcon cable-state audit
                code, body = api.handle_extcon_state_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/unix-socket-inventory-audit":
                # R&D #85.3 — /proc/net/unix breakdown
                code, body = api.handle_unix_socket_inventory_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/sched-features-debugfs-audit":
                # R&D #85.4 — CFS feature flags + tuning knobs
                code, body = api.handle_sched_features_debugfs_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/wol-ethtool-audit":
                # R&D #86.1 — NIC WoL + speed downshift
                code, body = api.handle_wol_ethtool_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/thunderbolt-usb4-audit":
                # R&D #86.2 — Thunderbolt / USB4 DMA posture
                code, body = api.handle_thunderbolt_usb4_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/nvme-controller-state-audit":
                # R&D #86.3 — NVMe controller state + firmware
                code, body = api.handle_nvme_controller_state_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/workqueue-cpumask-audit":
                # R&D #86.4 — workqueue cpumask / isolation
                code, body = api.handle_workqueue_cpumask_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/usb-authorized-default-audit":
                # R&D #87.1 — per-hub USB authorization gate
                code, body = api.handle_usb_authorized_default_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/proc-locks-contention-audit":
                # R&D #87.2 — /proc/locks blocked-waiter + count
                code, body = api.handle_proc_locks_contention_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/cpu-smt-control-audit":
                # R&D #87.3 — SMT over-mitigation / still-vulnerable
                code, body = api.handle_cpu_smt_control_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/interrupt-skew-audit":
                # R&D #87.4 — affinity_hint vs smp_affinity drift
                code, body = api.handle_interrupt_skew_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/userspace-hardening-sysctls-audit":
                # R&D #88.1 — ASLR/protected_*/suid_dumpable posture
                code, body = api.handle_userspace_hardening_sysctls_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/suspend-mode-selector-audit":
                # R&D #88.2 — /sys/power selector files
                code, body = api.handle_suspend_mode_selector_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/iommu-reserved-regions-audit":
                # R&D #88.3 — IOMMU group type + reserved_regions
                code, body = api.handle_iommu_reserved_regions_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/timer-migration-nohz-drift-audit":
                # R&D #88.4 — timer_migration × nohz_full × rcu_nocbs
                code, body = api.handle_timer_migration_nohz_drift_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/tcp-congestion-control-audit":
                # R&D #89.1 — TCP CC selector + TFO posture
                code, body = api.handle_tcp_congestion_control_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/namespace-limits-audit":
                # R&D #89.2 — /proc/sys/user/max_*_namespaces caps
                code, body = api.handle_namespace_limits_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/sysvipc-limits-audit":
                # R&D #89.3 — kernel.shm* + RAM cross-ref
                code, body = api.handle_sysvipc_limits_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/pcie-link-speed-drift-audit":
                # R&D #89.4 — non-GPU PCIe link drift (NVMe + peripherals)
                code, body = api.handle_pcie_link_speed_drift_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/resctrl-audit":
                # R&D #90.1 — resctrl L3/MBA partition + throttle
                code, body = api.handle_resctrl_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/proc-net-protocols-audit":
                # R&D #90.2 — /proc/net AF_PACKET + raw socket leaks
                code, body = api.handle_proc_net_protocols_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/cpufreq-governor-tunables-audit":
                # R&D #90.3 — governor sub-knobs (rate_limit_us etc)
                code, body = api.handle_cpufreq_governor_tunables_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/pcie-dpc-audit":
                # R&D #90.4 — PCIe Downstream Port Containment
                code, body = api.handle_pcie_dpc_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/cgroup-pids-controller-audit":
                # R&D #91.1 — per-cgroup pids.max posture
                code, body = api.handle_cgroup_pids_controller_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/dma-buf-bufinfo-audit":
                # R&D #91.2 — per-exporter dma-buf inventory
                code, body = api.handle_dma_buf_bufinfo_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/nvme-hmb-features-audit":
                # R&D #91.3 — NVMe HMB feature posture
                code, body = api.handle_nvme_hmb_features_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/vmstat-reclaim-pressure-audit":
                # R&D #91.4 — vmstat delta tracking
                code, body = api.handle_vmstat_reclaim_pressure_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/iommu-dma-strict-audit":
                # R&D #92.1 — IOMMU strict/lazy + passthrough
                code, body = api.handle_iommu_dma_strict_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/kernel-lockup-watchdog-audit":
                # R&D #92.2 — soft/hard lockup detector posture
                code, body = api.handle_kernel_lockup_watchdog_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/khugepaged-pressure-audit":
                # R&D #92.3 — THP collapse failure deltas
                code, body = api.handle_khugepaged_pressure_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/drm-fdinfo-engine-usage-audit":
                # R&D #92.4 — per-PID DRM client VRAM attribution
                code, body = api.handle_drm_fdinfo_engine_usage_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/pipe-mqueue-limits-audit":
                # R&D #93.1 — pipe / POSIX-mqueue / epoll limits
                code, body = api.handle_pipe_mqueue_limits_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/cgroup-v2-memory-peak-audit":
                # R&D #93.2 — cgroup v2 memory.peak ratio + swap.peak
                code, body = api.handle_cgroup_v2_memory_peak_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/nfs-mountstats-audit":
                # R&D #93.3 — NFS RPC transport health
                code, body = api.handle_nfs_mountstats_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/bpf-jit-xdp-busy-poll-audit":
                # R&D #93.4 — BPF JIT + XDP + busy_poll
                code, body = api.handle_bpf_jit_xdp_busy_poll_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/hwpoison-memory-failure-audit":
                # R&D #94.1 — DRAM hwpoison / memory_failure
                code, body = api.handle_hwpoison_memory_failure_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/fs-aio-fanotify-limits-audit":
                # R&D #94.2 — aio-max-nr default + fanotify caps
                code, body = api.handle_fs_aio_fanotify_limits_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/drm-ttm-page-pool-audit":
                # R&D #94.3 — TTM page pool caps posture
                code, body = api.handle_drm_ttm_page_pool_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/lockdep-lockstat-audit":
                # R&D #94.4 — kernel lockdep posture + silent death
                code, body = api.handle_lockdep_lockstat_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/mdio-phy-eee-audit":
                # R&D #95.1 — Ethernet PHY + EEE state desync
                code, body = api.handle_mdio_phy_eee_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/kernel-module-refcnt-audit":
                # R&D #95.2 — module refcnt / holders / initstate
                code, body = api.handle_kernel_module_refcnt_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/tracing-buffer-footprint-audit":
                # R&D #95.3 — ftrace buffer footprint + overrun
                code, body = api.handle_tracing_buffer_footprint_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/per-device-wakeup-attribution-audit":
                # R&D #95.4 — per-device wakeup attribution
                code, body = api.handle_per_device_wakeup_attribution_audit_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/tdp-auto/evaluate":
                code, body = api.handle_tdp_auto_evaluate(ctx, params)
                self._send_json(code, body)
                return
            if path == "/api/tdp-auto/preview":
                code, body = api.handle_tdp_auto_preview(ctx, params)
                self._send_json(code, body)
                return
            if path == "/api/ecc-remap/record":
                code, body = api.handle_ecc_remap_record(ctx, params)
                self._send_json(code, body)
                return
            if path == "/api/ecc-remap/rma-report.csv":
                code, csv_text = api.handle_ecc_remap_rma_csv(ctx)
                data = csv_text.encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "text/csv; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header(
                    "Content-Disposition",
                    'attachment; filename="ecc-remap-rma-report.csv"',
                )
                self.end_headers()
                self.wfile.write(data)
                return
            if path == "/tldr":
                # R&D #10.6 — ANSI/tldr endpoint for CLI users
                req_headers = {k: v for k, v in self.headers.items()}
                code, text = api.handle_tldr(ctx, params, headers=req_headers)
                data = text.encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                self.wfile.write(data)
                return
            if path == "/api/dr-bundle":
                # R&D #16.8 — list existing DR bundles
                code, body = api.handle_dr_bundle_list(ctx)
                self._send_json(code, body)
                return
            if path == "/api/lm-studio/inventory":
                # R&D #16.7 — LM-Studio model inventory + dedup-suspect
                code, body = api.handle_lm_studio_inventory(ctx, params)
                self._send_json(code, body)
                return
            if path == "/api/driver-vault":
                # R&D #16.4 — driver rollback vault status
                code, body = api.handle_driver_vault_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/driver-vault/rollback-script":
                # R&D #16.4 — generate sudo rollback script (never auto-runs)
                code, body = api.handle_driver_vault_rollback_script(ctx, params)
                self._send_json(code, body)
                return
            if path == "/noc":
                # R&D #16.6 — NOC board for wall-mounted screens
                code, html_text = api.handle_noc(ctx, params)
                data = html_text.encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                self.wfile.write(data)
                return
            if path == "/embed" or path.startswith("/embed/"):
                # R&D #12.6 — iframe-friendly read-only HTML view
                card = path[len("/embed/"):] if path.startswith("/embed/") else "summary"
                if not card or "/" in card:
                    card = "summary"
                code, html_text = api.handle_embed(ctx, card, params)
                data = html_text.encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                if params and params.get("share"):
                    self.send_header("X-Frame-Options", "ALLOWALL")
                else:
                    self.send_header("X-Frame-Options", "SAMEORIGIN")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)
                return
            if path == "/api/ups":
                code, body = api.handle_ups_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/ci-tag":
                # R&D #12.5 — CI runner GPU labels endpoint
                code, text = api.handle_ci_tag(ctx, params)
                data = text.encode("utf-8")
                self.send_response(code)
                fmt = (params or {}).get("fmt", "text")
                ct = "application/json" if fmt == "json" else "text/plain"
                self.send_header("Content-Type", f"{ct}; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                self.wfile.write(data)
                return
            if path == "/api/wall-meter":
                # R&D #12.1 — smart-plug PSU reading + efficiency
                code, body = api.handle_wall_meter(ctx, params)
                self._send_json(code, body)
                return
            if path == "/api/rules":
                # R&D #12.4 — declarative rule engine list
                code, body = api.handle_rules_list(ctx)
                self._send_json(code, body)
                return
            if path == "/api/peers":
                # R&D #12.3 — LAN-discovered fleet peers
                code, body = api.handle_peers(ctx, params)
                self._send_json(code, body)
                return
            if path == "/api/airgap/status":
                # R&D #12.7 — air-gap mode status
                code, body = api.handle_airgap_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/disk-health":
                # R&D #12.2 — SMART disk health
                code, body = api.handle_disk_health(ctx)
                self._send_json(code, body)
                return
            if path == "/api/best-gpu":
                # R&D #13.7 — workload power-balancer (JSON)
                code, body = api.handle_best_gpu(ctx, params)
                self._send_json(code, body)
                return
            if path == "/api/vram-quota":
                # R&D #13.3 — VRAM quota enforcer status + rules
                code, body = api.handle_vram_quota_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/hot-gpu-wizard":
                # R&D #13.6 — diagnostic decision tree
                code, body = api.handle_hot_gpu_wizard(ctx, params)
                self._send_json(code, body)
                return
            if path == "/api/carbon":
                # R&D #13.4 — carbon intensity overlay (local CSV)
                code, body = api.handle_carbon(ctx, params)
                self._send_json(code, body)
                return
            if path == "/api/xid":
                # R&D #14.1 — Xid kernel-error decoder
                code, body = api.handle_xid(ctx, params)
                self._send_json(code, body)
                return
            if path == "/api/hot-swap":
                # R&D #14.5 — PCIe / DRM drift events
                code, body = api.handle_hot_swap_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/inference-cost":
                # R&D #14.4 — €/prompt + tok/Wh over rolling windows
                code, body = api.handle_inference_cost(ctx, params)
                self._send_json(code, body)
                return
            if path == "/api/usage/users":
                # R&D #14.2 — per-user lab accounting (single sample)
                code, body = api.handle_lab_usage_live(ctx, params)
                self._send_json(code, body)
                return
            if path == "/api/boot-profile":
                # R&D #15.8 — boot-time profile status
                code, body = api.handle_boot_profile_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/tariff/status":
                # R&D #15.2 — tariff-aware scheduler
                code, body = api.handle_tariff_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/hf-dedup/plan":
                # R&D #15.3 — HF cache dedup planner (read-only)
                code, body = api.handle_hf_dedup_plan(ctx, params)
                self._send_json(code, body)
                return
            if path == "/api/tariff/estimate":
                code, body = api.handle_tariff_estimate(ctx, params)
                self._send_json(code, body)
                return
            if path == "/api/tariff/cheapest":
                code, body = api.handle_tariff_cheapest(ctx, params)
                self._send_json(code, body)
                return
            if path == "/api/discord-rpc":
                # R&D #15.7 — Discord Rich Presence status
                code, body = api.handle_discord_rpc_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/hot-swap/evaluate":
                code, body = api.handle_hot_swap_evaluate(ctx, params)
                self._send_json(code, body)
                return
            if path == "/api/xid/decode":
                code, body = api.handle_xid_decode(ctx, params)
                self._send_json(code, body)
                return
            if path == "/api/vram-quota/evaluate":
                code, body = api.handle_vram_quota_evaluate(ctx, params)
                self._send_json(code, body)
                return
            if path == "/api/best-gpu/env":
                # R&D #13.7 — shell-friendly variant
                code, text = api.handle_best_gpu_env(ctx, params)
                data = text.encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                self.wfile.write(data)
                return
            if path == "/api/airgap/audit":
                code, body = api.handle_airgap_audit(ctx, params)
                self._send_json(code, body)
                return
            if path == "/api/rules/evaluate":
                code, body = api.handle_rules_evaluate(ctx, params)
                self._send_json(code, body)
                return
            if path == "/api/snapshot":
                code, body = api.handle_snapshot_at(ctx, params)
                self._send_json(code, body)
                return
            if path == "/api/jupyter/kernels":
                code, body = api.handle_jupyter_kernels(ctx)
                self._send_json(code, body)
                return
            if path == "/api/llamabench/status":
                code, body = api.handle_llamabench_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/vfio/status":
                code, body = api.handle_vfio_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/hf-janitor":
                code, body = api.handle_hf_janitor(ctx, params)
                self._send_json(code, body)
                return
            if path == "/api/audit-log":
                code, body = api.handle_audit_log(ctx, params)
                self._send_json(code, body)
                return
            if path == "/api/auth/tokens":
                code, body = api.handle_auth_tokens_list(ctx)
                self._send_json(code, body)
                return
            if path == "/api/alertmanager/rules.yaml":
                code, text_body = api.handle_alertmanager_rules(ctx)
                data = text_body.encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/x-yaml; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Content-Disposition", 'attachment; filename="gpu-dashboard-rules.yaml"')
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                self.wfile.write(data)
                return
            # /api/heartbeat/<token> — inbound ping from training scripts
            if path.startswith("/api/heartbeat/"):
                token = path[len("/api/heartbeat/"):].strip("/")
                code, body = api.handle_heartbeat_ping(ctx, token)
                self._send_json(code, body)
                return
            if path == "/api/bar":
                # R&D #5.4 — waybar/polybar/i3blocks/tmux/plain
                code, body = api.handle_bar(ctx, params)
                fmt = (params or {}).get("fmt", "waybar")
                if fmt == "waybar":
                    self._send_json(code, body)
                else:
                    data = str(body).encode("utf-8")
                    self.send_response(code)
                    self.send_header("Content-Type", "text/plain; charset=utf-8")
                    self.send_header("Content-Length", str(len(data)))
                    self.send_header("Cache-Control", "no-cache, no-store")
                    self.end_headers()
                    self.wfile.write(data)
                return
            if path == "/metrics":
                # Prometheus / OpenMetrics scrape endpoint (R&D #4.1)
                code, text_body = api.handle_prometheus_metrics(ctx)
                data = text_body.encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-cache, no-store")
                self.end_headers()
                self.wfile.write(data)
                return
            if path == "/api/update/check":
                code, body = api.handle_update_check(ctx)
                self._send_json(code, body)
                return
            if path == "/api/logs":
                code, body = api.handle_logs(ctx, params)
                self._send_json(code, body)
                return
            if path == "/api/fan-curve":
                code, body = api.handle_fan_curve_get(ctx)
                self._send_json(code, body)
                return
            if path == "/api/processes":
                code, body = api.handle_processes(ctx)
                self._send_json(code, body)
                return
            if path == "/api/power-profiles":
                code, body = api.handle_power_profiles_list(ctx)
                self._send_json(code, body)
                return
            if path == "/api/auto-profile":
                code, body = api.handle_auto_profile_status(ctx)
                self._send_json(code, body)
                return
            if path == "/api/profile-stats":
                code, body = api.handle_profile_stats(ctx, params)
                self._send_json(code, body)
                return
            if path == "/api/electricity":
                code, body = api.handle_electricity(ctx, params)
                self._send_json(code, body)
                return
            if path == "/api/power-heatmap":
                code, body = api.handle_power_heatmap(ctx, params)
                self._send_json(code, body)
                return
            if path == "/api/llm/stats":
                code, body = api.handle_llm_stats(ctx)
                self._send_json(code, body)
                return
            if path == "/api/llm/lifetime":
                code, body = api.handle_llm_lifetime(ctx, params)
                self._send_json(code, body)
                return
            if path == "/api/llm/perf":
                code, body = api.handle_llm_perf(ctx, params)
                self._send_json(code, body)
                return
            if path == "/api/thermal-stats":
                code, body = api.handle_thermal_stats(ctx, params)
                self._send_json(code, body)
                return
            if path == "/api/power-stats":
                code, body = api.handle_power_stats(ctx, params)
                self._send_json(code, body)
                return
            if path == "/api/prom":
                code, body = api.handle_prom(ctx)
                data = body.encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            if path == "/api/snapshot":
                code, body = api.handle_snapshot(ctx)
                if isinstance(body, bytes):
                    import datetime as _dt
                    fn = f"gpu-dashboard-snapshot-{_dt.datetime.now().strftime('%Y%m%d-%H%M%S')}.tar.gz"
                    self._send_binary(code, body, "application/gzip", fn)
                else:
                    self._send_json(code, body)
                return
            self.send_response(404)
            self.end_headers()

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0) or 0)
            try:
                raw = self.rfile.read(length).decode("utf-8") if length else "{}"
                payload = json.loads(raw) if raw else {}
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                self._send_json(400, {"ok": False, "error": f"bad request: {e}"})
                return

            if self.path == "/api/set-power-limit":
                code, body = api.handle_set_power_limit(ctx, payload)
                self._send_json(code, body)
                return
            if self.path == "/api/set-offsets":
                code, body = api.handle_set_offsets(ctx, payload)
                self._send_json(code, body)
                return
            if self.path == "/api/alerts-config":
                code, body = api.handle_alerts_config_post(ctx, payload)
                self._send_json(code, body)
                return
            if self.path == "/api/alerts-test":
                code, body = api.handle_alerts_test(ctx)
                self._send_json(code, body)
                return
            if self.path == "/api/push/subscribe":
                code, body = api.handle_push_subscribe(ctx, payload)
                self._send_json(code, body)
                return
            if self.path == "/api/push/unsubscribe":
                code, body = api.handle_push_unsubscribe(ctx, payload)
                self._send_json(code, body)
                return
            if self.path == "/api/fan-curve":
                code, body = api.handle_fan_curve_post(ctx, payload)
                self._send_json(code, body)
                return
            if self.path == "/api/setup/save":
                code, body = api.handle_setup_save(ctx, payload)
                self._send_json(code, body)
                return
            if self.path == "/api/restart":
                code, body = api.handle_restart(ctx)
                self._send_json(code, body)
                return
            if self.path == "/api/stop":
                code, body = api.handle_stop(ctx)
                self._send_json(code, body)
                return
            if self.path == "/api/update/pull":
                code, body = api.handle_update_pull(ctx)
                self._send_json(code, body)
                return
            if self.path == "/api/profile/save":
                code, body = api.handle_profile_save(ctx, payload)
                self._send_json(code, body)
                return
            if self.path == "/api/app-triggers":
                code, body = api.handle_app_triggers_post(ctx, payload)
                self._send_json(code, body)
                return
            if self.path == "/api/modules/toggle":
                code, body = api.handle_modules_toggle(ctx, payload)
                self._send_json(code, body)
                return
            if self.path == "/api/benchmark/run":
                code, body = api.handle_benchmark_run(ctx, payload)
                self._send_json(code, body)
                return
            if self.path.startswith("/api/power-profiles/apply/"):
                name = self.path[len("/api/power-profiles/apply/"):]
                code, body = api.handle_power_profile_apply(ctx, name)
                self._send_json(code, body)
                return
            if self.path == "/api/electricity/config":
                code, body = api.handle_electricity_config(ctx, payload)
                self._send_json(code, body)
                return
            if self.path == "/api/heartbeat/config":
                code, body = api.handle_heartbeat_config(ctx, payload)
                self._send_json(code, body)
                return
            if self.path == "/api/notif/channels":
                code, body = api.handle_notif_channel_save(ctx, payload)
                self._send_json(code, body)
                return
            if self.path == "/api/rules":
                # R&D #12.4 — save the whole rules list
                code, body = api.handle_rules_save(ctx, payload)
                self._send_json(code, body)
                return
            if self.path == "/api/vram-quota":
                # R&D #13.3 — save VRAM quota rules
                code, body = api.handle_vram_quota_save(ctx, payload)
                self._send_json(code, body)
                return
            if self.path == "/api/boot-profile":
                # R&D #15.8 — save boot profile
                code, body = api.handle_boot_profile_save(ctx, payload)
                self._send_json(code, body)
                return
            if self.path == "/api/hf-dedup/execute":
                # R&D #15.3 — execute a dedup plan (dry-run default)
                code, body = api.handle_hf_dedup_execute(ctx, payload)
                self._send_json(code, body)
                return
            if self.path == "/api/dr-bundle":
                # R&D #16.8 — build a fresh DR bundle
                code, body = api.handle_dr_bundle_create(ctx, payload)
                self._send_json(code, body)
                return
            if self.path.startswith("/api/dr-bundle/delete/"):
                name = self.path[len("/api/dr-bundle/delete/"):]
                code, body = api.handle_dr_bundle_delete(ctx, name)
                self._send_json(code, body)
                return
            if self.path == "/api/driver-vault/stash":
                # R&D #16.4 — capture the currently-installed driver .deb
                code, body = api.handle_driver_vault_stash(ctx)
                self._send_json(code, body)
                return
            if self.path == "/api/tdp-auto":
                # R&D #17.3 — save TDP auto config
                code, body = api.handle_tdp_auto_save(ctx, payload)
                self._send_json(code, body)
                return
            if self.path == "/api/llm-swap/pin":
                # R&D #17.5 — pin/unpin a model from LRU eviction
                code, body = api.handle_llm_swap_pin(ctx, payload)
                self._send_json(code, body)
                return
            if self.path == "/api/warmup-profile/probe":
                # R&D #19.4 — fire a TTFT probe and record sample
                code, body = api.handle_warmup_profile_probe(ctx, payload)
                self._send_json(code, body)
                return
            if self.path == "/api/vbios-drift/rebaseline":
                # R&D #20.2 — capture current VBIOS / ROM as new baseline
                code, body = api.handle_vbios_drift_rebaseline(ctx, payload)
                self._send_json(code, body)
                return
            if self.path == "/api/boot-profile/clear":
                code, body = api.handle_boot_profile_clear(ctx)
                self._send_json(code, body)
                return
            if self.path == "/api/boot-profile/apply-now":
                code, body = api.handle_boot_profile_apply_now(ctx)
                self._send_json(code, body)
                return
            if self.path == "/api/notif/test":
                code, body = api.handle_notif_channel_test(ctx, payload)
                self._send_json(code, body)
                return
            if self.path == "/api/auth/tokens":
                code, body = api.handle_auth_token_create(ctx, payload)
                self._send_json(code, body)
                return
            if self.path.startswith("/api/auth/tokens/") and self.path.endswith("/delete"):
                # POST /api/auth/tokens/<id>/delete
                token_id = self.path[len("/api/auth/tokens/"):-len("/delete")]
                code, body = api.handle_auth_token_delete(ctx, token_id)
                self._send_json(code, body)
                return
            if self.path == "/api/auth/share":
                code, body = api.handle_auth_share_create(ctx, payload)
                self._send_json(code, body)
                return
            if self.path == "/api/watchdog/enable":
                code, body = api.handle_watchdog_enable(ctx, payload)
                self._send_json(code, body)
                return
            if self.path == "/api/watchdog/disable":
                code, body = api.handle_watchdog_disable(ctx)
                self._send_json(code, body)
                return

            self.send_response(404)
            self.end_headers()

    return Handler


def main(argv: Optional[list] = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description="gpu-dashboard HTTP server")
    parser.add_argument("--config", default=None,
                        help="Path to config.env (default: ~/.config/gpu-dashboard/config.env)")
    parser.add_argument("--profiles-dir", default="profiles",
                        help="Directory containing JSON profiles")
    parser.add_argument("--status", action="store_true",
                        help="Print a one-shot TTY status summary then exit")
    args = parser.parse_args(argv)

    if args.status:
        from .cli_status import run_status
        return run_status(profiles_dir=args.profiles_dir)

    ctx = _load_context(config_path=args.config, profiles_dir=args.profiles_dir)
    cfg = ctx["config"]
    port = cfg.get_int("DASHBOARD_PORT", default=9999)
    bind = cfg.get("DASHBOARD_BIND", "0.0.0.0")

    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer((bind, port), make_handler(ctx)) as httpd:
        print(f"gpu-dashboard listening on http://{bind}:{port}")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\ngpu-dashboard stopped.")
            ctx["sampler"].stop()
            if "retention" in ctx:
                ctx["retention"].stop()
            if ctx.get("fan_curve_daemon"):
                ctx["fan_curve_daemon"].stop()
            if ctx.get("auto_profile_daemon"):
                ctx["auto_profile_daemon"].stop()
            if ctx.get("alert_monitor_daemon"):
                ctx["alert_monitor_daemon"].stop()
            if "storage" in ctx:
                ctx["storage"].close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
