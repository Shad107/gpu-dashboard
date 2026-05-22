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


def _gpus_available():
    return _m._gpus_available()


def handle_ecc_health(ctx):
    return _m.handle_ecc_health(ctx)


def handle_drift_check(ctx):
    return _m.handle_drift_check(ctx)


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
