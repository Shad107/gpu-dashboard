"""HTTP handlers for the core data plane : state / history / events / export.

Extracted from the legacy monolith in cycle 10a of the api/ split.
This is the most heavily-trafficked endpoint group — /api/state is hit
on every dashboard refresh.

Forwarding stubs use *args/**kw so test mocks with any signature still
resolve correctly (pattern adopted in cycle 9).
"""
from __future__ import annotations

import csv
import io
import json
import os
import time
from typing import Any, Optional, Tuple

from . import _monolith as _m


Response = Tuple[int, dict]


def _gpu_card_snapshot(*args, **kw):
    return _m._gpu_card_snapshot(*args, **kw)


def _gpus_available(*args, **kw):
    return _m._gpus_available(*args, **kw)


def _parse_gpu_index(params):
    return _m._parse_gpu_index(params)


def _per_fan_state(cfg):
    return _m._per_fan_state(cfg)


def _tuning_state(cfg):
    return _m._tuning_state(cfg)


def _watchdog_state(cfg):
    return _m._watchdog_state(cfg)


def _services_state(cfg):
    return _m._services_state(cfg)


def handle_processes(ctx):
    # Forward to ops.handle_processes — used by handle_state. Late import
    # to keep this a 2-statement forwarding stub (recognized by test_api_structure).
    from . import ops as _ops
    return _ops.handle_processes(ctx)


def _fan_distribution(cfg):
    return _m._fan_distribution(cfg)


def _llm_model_served(cfg):
    # llm_model_served lives in api.llm — forward via late import
    from . import llm as _llm
    return _llm._llm_model_served(cfg)


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
