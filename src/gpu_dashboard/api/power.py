"""HTTP handlers for power tuning : power-limit, clock offsets, profiles.

Extracted from the legacy monolith in cycle 5 of the api/ split.
Covers R&D #3 power-limit + clock-offset slider and profile presets.
"""
from __future__ import annotations

import json
import os
import time
from typing import Optional, Tuple

from . import _monolith as _m
from ..modules import power_limit as pl
from ..modules import clock_offsets as co


Response = Tuple[int, dict]


# Forwarding stubs so tests patching api._monolith.X take effect here too.
def _gpu_card_snapshot(gpu_index: int = 0):
    return _m._gpu_card_snapshot(gpu_index)


def _gpus_available():
    return _m._gpus_available()


def _parse_gpu_index(params):
    return _m._parse_gpu_index(params)


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


# ────────────────────────── GET /api/profile-stats ────────────────────────


def handle_profile_stats(ctx: dict, params: dict) -> Response:
    """Total time spent in each power profile since `since` seconds ago.

    Walks the `profile_switch` events from storage and computes durations:
      - For each consecutive pair (e1, e2), the interval [e1.ts, e2.ts]
        is attributed to e1.payload.to
      - The tail [last_switch.ts, now] is attributed to last_switch.payload.to
    """
    storage = ctx.get("storage")
    if storage is None:
        return 503, {"ok": False, "error": "storage not available"}

    import time as _time
    try:
        since = int(params.get("since", 0))
    except (ValueError, TypeError):
        return 400, {"ok": False, "error": "since must be integer"}

    now = int(_time.time())
    from_ts = max(0, now - since) if since > 0 else 0
    events = storage.get_events(from_ts=from_ts, kind="profile_switch")
    # Sort defensively (storage already orders by id, but be paranoid)
    events.sort(key=lambda e: e["ts"])

    # If since > 0, we cap the first event's start at from_ts (so durations
    # are measured WITHIN the window, not before it).
    totals: dict = {}
    for i, ev in enumerate(events):
        payload = ev.get("payload") or {}
        to = payload.get("to")
        if not to:
            continue
        start = max(ev["ts"], from_ts) if since > 0 else ev["ts"]
        if i + 1 < len(events):
            end = events[i + 1]["ts"]
        else:
            end = now
        dur = max(0, end - start)
        totals[to] = totals.get(to, 0) + dur

    # Most-recent-first list of {ts, to} pairs for the About activity log
    recent_events = [
        {"ts": ev["ts"], "to": (ev.get("payload") or {}).get("to")}
        for ev in reversed(events)
        if (ev.get("payload") or {}).get("to")
    ][:50]

    return 200, {
        "ok": True,
        "totals": totals,
        "now": now,
        "since_seconds": since,
        "events_count": len(events),
        "recent_events": recent_events,
    }


# ────────────────────────── GET /api/auto-profile ─────────────────────────


def handle_auto_profile_status(ctx: dict) -> Response:
    """Status of the auto-profile-switch daemon (current classification, etc.)."""
    daemon = ctx.get("auto_profile_daemon")
    enabled = ctx["config"].get_bool("MODULE_AUTO_PROFILE")
    if daemon is None:
        return 200, {"enabled": enabled, "running": False}
    status = daemon.status()
    status["enabled"] = enabled
    status["thresholds"] = {
        "idle": ctx["config"].get_int("AUTO_PROFILE_IDLE_THRESHOLD", default=5),
        "boost": ctx["config"].get_int("AUTO_PROFILE_BOOST_THRESHOLD", default=80),
    }
    return 200, status


_POWER_PROFILES = ("silent", "sweet", "boost")


def _read_power_profile(cfg, name: str) -> Optional[dict]:
    """Read one of the SILENT / SWEET / BOOST profiles from config."""
    key = name.upper()
    try:
        watts = cfg.get_int(f"POWER_PROFILE_{key}_W", default=0)
    except Exception:
        return None
    if watts <= 0:
        return None
    return {
        "name": name,
        "watts": watts,
        "gpu_offset": cfg.get_int(f"POWER_PROFILE_{key}_GPU_OFFSET", default=0),
        "mem_offset": cfg.get_int(f"POWER_PROFILE_{key}_MEM_OFFSET", default=0),
    }


def handle_power_profiles_list(ctx: dict) -> Response:
    """List the 3 configurable power profiles : silent / sweet / boost.

    Returns {profiles: [{name, watts, gpu_offset, mem_offset}, ...]}.
    A profile is omitted if its <NAME>_W is not configured (0 or missing).
    """
    cfg = ctx["config"]
    profiles = []
    for name in _POWER_PROFILES:
        p = _read_power_profile(cfg, name)
        if p:
            profiles.append(p)
    return 200, {"profiles": profiles}


def handle_power_profile_apply(ctx: dict, name: str) -> Response:
    """Apply one of the named profiles : power-limit + offsets in a single call.

    Also logs a 'profile_switch' event to storage for the time-tracker.
    """
    name = (name or "").lower()
    if name not in _POWER_PROFILES:
        return 400, {"ok": False, "error": f"unknown profile: {name!r}. Use one of: {_POWER_PROFILES}"}

    cfg = ctx["config"]
    prof = _read_power_profile(cfg, name)
    if prof is None:
        return 400, {"ok": False, "error": f"profile {name!r} is not configured (POWER_PROFILE_{name.upper()}_W)"}

    gpu_profile = ctx.get("profile") or {}
    from ..modules import power_limit as _pl
    from ..modules import clock_offsets as _co

    wrapper = cfg.get("POWER_LIMIT_WRAPPER", "/usr/local/bin/set-power-limit")
    display = cfg.get("CLOCK_OFFSETS_DISPLAY", ":0")
    xauth = cfg.get("CLOCK_OFFSETS_XAUTHORITY") or None

    # 1) power-limit
    try:
        pl_result = _pl.apply_power_limit(gpu_profile, prof["watts"], wrapper_path=wrapper)
    except ValueError as e:
        return 400, {"ok": False, "error": f"power-limit: {e}"}

    # 2) offsets (only if changed from current ; we always apply for simplicity)
    co_result = _co.apply_offsets(
        gpu_profile,
        gpu=prof["gpu_offset"], mem=prof["mem_offset"],
        display=display, xauthority=xauth,
    ) if (prof["gpu_offset"] != 0 or prof["mem_offset"] != 0) else {"ok": True, "skipped": True}

    ok = pl_result.get("ok", False) and co_result.get("ok", True)

    # Log the switch for the time tracker (storage may be None if not started)
    storage = ctx.get("storage")
    if storage is not None and ok:
        try:
            storage.record_event("profile_switch", {"to": name, "watts": prof["watts"]})
        except Exception:
            pass

    return (200 if ok else 500), {
        "ok": ok,
        "applied_profile": name,
        "watts": prof["watts"],
        "gpu_offset": prof["gpu_offset"],
        "mem_offset": prof["mem_offset"],
        "power_limit_result": pl_result,
        "offsets_result": co_result,
    }
