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
            if path == "/api/ups":
                code, body = api.handle_ups_status(ctx)
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
