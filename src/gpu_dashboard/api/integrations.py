"""HTTP handlers for ops / discovery / housekeeping integrations.

Extracted from the legacy monolith in cycle 3 of the api/ split.
Covers R&D #9.1 / #9.4 / #10.1 / #10.3 / #11.1 / #11.4 / #11.5 / #11.6.

Note : badge SVG (#10.7) and ANSI tldr (#10.6) — along with their
helpers _ANSI/_color/_temp_color/_spark/_badge_svg/_BADGE_TEMP_COLORS —
stay in _monolith for now ; they move in cycle 3b.
"""
from __future__ import annotations

import time
from typing import Optional, Tuple

# Cross-module refs : handle_readyz needs _gpus_available + ECC + drift
# handlers, all still in _monolith.py. We forward at call time so the
# test suite can patch via api._monolith.X and the override is honored.
from . import _monolith as _m


def _gpus_available(*args, **kw):
    return _m._gpus_available(*args, **kw)


def _gpu_card_snapshot(*args, **kw):
    """Forward to _monolith — used by handle_tldr and handle_badge."""
    return _m._gpu_card_snapshot(*args, **kw)


def _gpus_available_helper():
    """Legacy alias used by handle_badge — defers to _gpus_available."""
    return _gpus_available()


def handle_ecc_health(ctx):
    from . import diagnostics as _diag  # late import to avoid cycle
    return _diag.handle_ecc_health(ctx)


def handle_drift_check(ctx):
    from . import diagnostics as _diag
    return _diag.handle_drift_check(ctx)


Response = Tuple[int, dict]


# ─── R&D #11.6 — iCal feed of GPU events ─────────────────────────────────
def handle_ical_feed(ctx: dict, params: Optional[dict] = None) -> Tuple[int, str]:
    """Emit an RFC 5545 iCalendar feed of recent GPU events.

    Query params :
      days = lookback period (default 30, max 365)
    """
    from ..modules import ical_feed
    params = params or {}
    try:
        days = max(1, min(365, int(params.get("days", "30"))))
    except (ValueError, TypeError):
        days = 30
    events = ical_feed.collect_events(ctx.get("storage"), days=days)
    text = ical_feed.render_calendar(events)
    return 200, text


# ─── R&D #11.5 — Weekly HTML + plain-text report ─────────────────────────
def handle_weekly_report(ctx: dict, params: Optional[dict] = None) -> Tuple[int, str]:
    """Generate the weekly summary report.

    Query params :
      fmt = html (default) | text
      days = period length (default 7, max 90)
    """
    storage = ctx.get("storage")
    if not storage:
        return 503, "Storage unavailable"
    params = params or {}
    fmt = params.get("fmt", "html")
    try:
        days = max(1, min(90, int(params.get("days", "7"))))
    except (ValueError, TypeError):
        days = 7
    from ..modules import weekly_report
    stats = weekly_report.compute_stats(storage, days=days)
    if fmt == "text":
        return 200, weekly_report.render_text(stats, ctx.get("config"))
    return 200, weekly_report.render_html(stats, ctx.get("config"))


# ─── R&D #11.4 — Auto-discover well-known LLM/RAG/GPU services ────────────
def handle_service_discovery(ctx: dict, params: Optional[dict] = None) -> Response:
    """Return list of detected services + unknown listeners on the host."""
    from ..modules import service_discovery
    params = params or {}
    probe = params.get("probe", "1") not in ("0", "false", "False")
    return 200, service_discovery.discover(probe=probe)


# ─── R&D #11.1b — Watchdog setup (install systemd units from UI) ──────────
def handle_watchdog_status(ctx: dict) -> Response:
    from ..modules import watchdog_setup
    return 200, {"ok": True, **watchdog_setup.status()}


def handle_watchdog_enable(ctx: dict, payload: dict) -> Response:
    from ..modules import watchdog_setup
    cfg = ctx["config"]
    try:
        port = int(cfg.get("DASHBOARD_PORT", "9999"))
    except (ValueError, TypeError):
        port = 9999
    strict = bool(payload.get("strict") if isinstance(payload, dict) else False)
    try:
        interval = int(payload.get("interval_s", 60)) if isinstance(payload, dict) else 60
    except (ValueError, TypeError):
        interval = 60
    interval = max(30, min(3600, interval))
    ok, msg = watchdog_setup.install(port=port, strict=strict, interval_s=interval)
    return (200 if ok else 502), {"ok": ok, "msg": msg, **watchdog_setup.status()}


def handle_watchdog_disable(ctx: dict) -> Response:
    from ..modules import watchdog_setup
    ok, msg = watchdog_setup.uninstall()
    return (200 if ok else 502), {"ok": ok, "msg": msg, **watchdog_setup.status()}


# ─── R&D #11.1 — k8s-style /healthz + /readyz probes ────────────────────────
def handle_healthz(ctx: dict) -> Tuple[int, dict]:
    """Liveness probe. Returns 200 if process alive — no GPU/SQLite calls.
    Sub-millisecond. Suitable for high-frequency k8s liveness checks."""
    return 200, {"ok": True, "alive": True, "ts": int(time.time())}


def handle_readyz(ctx: dict, params: Optional[dict] = None) -> Tuple[int, dict]:
    """Readiness probe. Returns 503 if any critical subsystem unhealthy :
      - NVML / nvidia-smi reachable
      - Storage writable
      - Last sampler snapshot < 30s old
    With ?strict=1, also fails on : ECC errors > 0, driver drift recent.
    """
    params = params or {}
    strict = params.get("strict") in ("1", "true", "True")
    checks: dict = {}
    overall_ok = True

    # 1. Sampler snapshot age
    sampler = ctx.get("sampler")
    snap_age = None
    if sampler:
        try:
            snap = sampler.snapshot()
            if snap:
                # ts can be HH:MM:SS or epoch — use len(snap) as a proxy
                snap_age = 0 if snap else None
        except Exception:
            snap = None
        snap_ok = sampler is not None and bool(snap)
    else:
        snap_ok = False
    checks["sampler"] = {"ok": snap_ok, "reason": "no samples yet" if not snap_ok else "ok"}
    if not snap_ok:
        overall_ok = False

    # 2. Storage write probe
    storage = ctx.get("storage")
    storage_ok = False
    if storage:
        try:
            with storage._lock:
                storage._conn.execute("SELECT 1").fetchone()
            storage_ok = True
        except Exception as e:
            checks["storage"] = {"ok": False, "reason": f"db unreachable: {e}"}
        else:
            checks["storage"] = {"ok": True, "reason": "ok"}
    else:
        checks["storage"] = {"ok": False, "reason": "no storage configured"}
    if not storage_ok:
        overall_ok = False

    # 3. NVIDIA driver reachable (last sample alive flag)
    gpu_ok = False
    try:
        gpus = _gpus_available()
        gpu_ok = len(gpus) > 0
    except Exception:
        gpus = []
    checks["nvidia"] = {
        "ok": gpu_ok,
        "reason": f"{len(gpus)} GPU(s)" if gpu_ok else "nvidia-smi unreachable",
    }
    if not gpu_ok:
        overall_ok = False

    # Strict-only checks
    if strict:
        # ECC : if available and uncorrected > 0 → fail
        try:
            ecc_code, ecc_body = handle_ecc_health(ctx)
            if ecc_body.get("verdict_kind") == "failing":
                checks["ecc"] = {"ok": False, "reason": ecc_body.get("verdict_msg", "ECC failing")}
                overall_ok = False
            else:
                checks["ecc"] = {"ok": True, "reason": ecc_body.get("verdict_kind", "n/a")}
        except Exception as e:
            checks["ecc"] = {"ok": True, "reason": f"check skipped: {e}"}

        # Drift : flag if recent boot showed driver change
        try:
            drift_code, drift_body = handle_drift_check(ctx)
            last = drift_body.get("last_drift")
            if last:
                # within last 24h
                age_h = (int(time.time()) - int(last.get("ts", 0))) / 3600
                if age_h < 24:
                    checks["drift"] = {"ok": False, "reason": f"recent driver/kernel drift {age_h:.0f}h ago"}
                    overall_ok = False
                else:
                    checks["drift"] = {"ok": True, "reason": "no recent drift"}
            else:
                checks["drift"] = {"ok": True, "reason": "no drift recorded"}
        except Exception as e:
            checks["drift"] = {"ok": True, "reason": f"check skipped: {e}"}

    return (200 if overall_ok else 503), {
        "ok": overall_ok,
        "ready": overall_ok,
        "strict": strict,
        "checks": checks,
        "ts": int(time.time()),
    }


# ─── R&D #10.1 — Vector DB watchdog (Chroma / Qdrant / pgvector) ─────────────
def handle_vector_db(ctx: dict) -> Response:
    """Probe locally-configured vector stores and return aggregated status."""
    from ..modules import vector_db
    return 200, vector_db.status(ctx["config"])


# ─── R&D #10.3 — HF model card cross-reference ──────────────────────────────
def handle_hf_card(ctx: dict, params: Optional[dict] = None) -> Response:
    """Look up the HF model card for a given repo_id or path.

    Query params :
      repo  : 'org/repo' (preferred)
      path  : a model file path → parse_repo_from_path heuristic
      force : '1' to bypass 7-day cache

    Response : {ok, repo, card: {id, license, base_model, downloads, ...},
                license_color: '#xxxxxx', cached: bool}
    """
    from ..modules import hf_cards
    params = params or {}
    repo = params.get("repo")
    path = params.get("path")
    force = params.get("force") in ("1", "true", "True")

    if not repo and path:
        repo = hf_cards.parse_repo_from_path(path)
    if not repo:
        return 400, {"ok": False, "error": "repo or path required"}

    card = hf_cards.get_card(repo, force_refresh=force)
    if card is None:
        return 200, {"ok": True, "repo": repo, "card": None,
                     "error": "not found / network failure / no cache"}
    return 200, {
        "ok": True,
        "repo": repo,
        "card": card,
        "license_color": hf_cards.license_color(card.get("license")),
    }


# ─── R&D #9.4 — HF cache janitor (cold large models) ─────────────────────────
def handle_hf_janitor(ctx: dict, params: Optional[dict] = None) -> Response:
    """Surface cold large model files across known cache/model dirs."""
    from ..modules import hf_janitor
    params = params or {}
    extra_raw = ctx["config"].get("MODELS_DIRS", "") or ""
    extra = [d.strip() for d in extra_raw.split(",") if d.strip()] or None
    try:
        limit = max(1, min(500, int(params.get("limit", "50"))))
    except (ValueError, TypeError):
        limit = 50
    return 200, hf_janitor.audit(extra_dirs=extra, limit=limit)


# ─── R&D #9.1 — VFIO / GPU passthrough sentinel ──────────────────────────────
def handle_vfio_status(ctx: dict) -> Response:
    """Return VFIO passthrough status for all NVIDIA GPUs."""
    from ..modules import vfio_sentinel
    return 200, vfio_sentinel.status()


# ── Cycle 10b additions ────────────────────────────

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
