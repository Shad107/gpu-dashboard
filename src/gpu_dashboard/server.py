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
    "STORAGE_DB_PATH": "~/.local/share/gpu-dashboard/metrics.db",
    "STORAGE_RETENTION_DAYS": "30",
    "POWER_LIMIT_DEFAULT": "250",
    "POWER_LIMIT_WRAPPER": "/usr/local/bin/set-power-limit",
    "CLOCK_OFFSETS_DISPLAY": ":0",
    "TG_ENABLED": "0",
    "TG_TOKEN": "",
    "TG_CHAT": "",
    "ALERT_DROP": "1",
    "ALERT_RECOVER": "1",
}


def _load_context(config_path: Optional[str] = None, profiles_dir: str = "profiles") -> dict:
    """Build the context dict passed to api.* handlers."""
    config_files = []
    if config_path is None:
        # Defaults under ~/.config/gpu-dashboard/
        home = os.path.expanduser("~")
        for f in ("config.env", "secrets.env"):
            p = os.path.join(home, ".config/gpu-dashboard", f)
            if os.path.isfile(p):
                config_files.append(p)
    else:
        config_files.append(config_path)

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

    return {
        "config": cfg, "profile": profile,
        "sampler": sampler, "storage": storage, "retention": retention,
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
            if "storage" in ctx:
                ctx["storage"].close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
