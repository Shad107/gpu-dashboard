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
    )
    sampler.start()

    # Retention daemon (purge + vacuum)
    retention = RetentionTask(
        storage,
        retention_days=cfg.get_int("STORAGE_RETENTION_DAYS", default=30),
    )
    retention.start()

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
            with open(full, "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
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
            if path == "/api/state":
                code, body = api.handle_state(ctx)
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
            if path == "/api/health":
                code, body = api.handle_health(ctx)
                self._send_json(code, body)
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
            if path == "/api/electricity":
                code, body = api.handle_electricity(ctx, params)
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
            if self.path.startswith("/api/power-profiles/apply/"):
                name = self.path[len("/api/power-profiles/apply/"):]
                code, body = api.handle_power_profile_apply(ctx, name)
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
    args = parser.parse_args(argv)

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
            if "storage" in ctx:
                ctx["storage"].close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
