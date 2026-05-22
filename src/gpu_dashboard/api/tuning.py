"""HTTP handlers for the power-user tuning surface : fan curve, JSON profile
override, A/B benchmark, app triggers, web push, alerts test.

Extracted from the legacy monolith in cycle 10b of the api/ split.
"""
from __future__ import annotations

import json
import os
import time
from typing import Optional, Tuple

from . import _monolith as _m


Response = Tuple[int, dict]


def _gpu_card_snapshot(*args, **kw):
    return _m._gpu_card_snapshot(*args, **kw)


def _gpus_available(*args, **kw):
    return _m._gpus_available(*args, **kw)


def _parse_gpu_index(params):
    return _m._parse_gpu_index(params)


# ────────────────────────── /api/power-profiles ───────────────────────────


# Each profile bundles power-limit + GPU offset + memory offset.
# Handlers from L1040-L1040 moved to api/power.py (cycle 5)



# Handlers from L1043-L1057 moved to api/power.py (cycle 5)



# Handlers from L1060-L1072 moved to api/power.py (cycle 5)



def handle_benchmark_run(ctx: dict, payload) -> Response:
    """Run an A/B profile comparison synchronously (R&D #4, cycle 123).

    payload : {profile_a, profile_b, duration_s} — duration capped at 300s
              to avoid wedging the server.

    Returns {segment_a, segment_b, comparison} where comparison is the output
    of benchmark.compare(seg_a, seg_b).
    """
    if not isinstance(payload, dict):
        return 400, {"ok": False, "error": "payload must be an object"}
    a = str(payload.get("profile_a") or "").lower()
    b = str(payload.get("profile_b") or "").lower()
    try:
        duration = int(payload.get("duration_s") or 60)
    except (ValueError, TypeError):
        return 400, {"ok": False, "error": "duration_s must be an integer"}
    if duration < 5 or duration > 300:
        return 400, {"ok": False, "error": "duration_s must be in [5, 300]"}
    valid = {"silent", "sweet", "boost"}
    if a not in valid or b not in valid:
        return 400, {"ok": False,
                     "error": f"profiles must be in {sorted(valid)}"}
    if a == b:
        return 400, {"ok": False, "error": "profile_a and profile_b must differ"}

    sampler = ctx.get("sampler")
    if sampler is None:
        return 503, {"ok": False, "error": "sampler not available"}

    cfg = ctx.get("config")
    price = 0.25
    if cfg is not None:
        try:
            price = float(cfg.get("ELECTRICITY_PRICE_EUR_PER_KWH", default="0.25"))
        except (ValueError, TypeError):
            price = 0.25

    def _apply(profile_name: str) -> None:
        from .power import handle_power_profile_apply as _hppa  # cycle 5 late import
        _hppa(ctx, profile_name)

    from ..modules.benchmark import run_segment, compare
    seg_a = run_segment(duration, a, _apply, sampler, price_per_kwh=price)
    seg_b = run_segment(duration, b, _apply, sampler, price_per_kwh=price)
    cmp = compare(seg_a, seg_b)

    return 200, {
        "ok": True,
        "segment_a": seg_a,
        "segment_b": seg_b,
        "comparison": cmp,
    }

# ────────────────────────── /api/app-triggers ─────────────────────────────


def handle_app_triggers_get(ctx: dict) -> Response:
    """Return the user-configured per-app profile triggers map."""
    from ..modules import app_triggers as _at
    return 200, {"ok": True, "triggers": _at.load_triggers()}

def handle_app_triggers_post(ctx: dict, payload) -> Response:
    """Persist {app: profile} mapping. Validates each profile name."""
    from ..modules import app_triggers as _at
    if not isinstance(payload, dict):
        return 400, {"ok": False, "error": "payload must be an object"}
    triggers = payload.get("triggers")
    if not isinstance(triggers, dict):
        return 400, {"ok": False, "error": "triggers must be an object"}
    valid = {"silent", "sweet", "boost"}
    cleaned: dict = {}
    for k, v in triggers.items():
        if not isinstance(k, str) or not isinstance(v, str):
            continue
        k = k.strip()
        if not k:
            continue
        if v not in valid:
            return 400, {
                "ok": False,
                "error": f"profile '{v}' invalid (must be one of {sorted(valid)})",
            }
        cleaned[k] = v
    try:
        _at.save_triggers(cleaned)
    except OSError as e:
        return 500, {"ok": False, "error": f"save failed: {e}"}
    return 200, {"ok": True, "triggers": cleaned}

# ────────────────────────── /api/profile/save ─────────────────────────────


def handle_profile_save(ctx: dict, payload: dict) -> Response:
    """Save a user override for a GPU profile.

    Validates the payload against profiles/schema.json then writes it to
    `<overrides_dir>/<safe_model_name>.json`. The next reload picks it up
    automatically (via `profile.get_profile_for_gpu`'s override-dir param).
    """
    import re as _re
    from ..profile import load_schema, validate_profile

    if not isinstance(payload, dict):
        return 400, {"ok": False, "error": "payload must be an object"}

    profiles_dir = ctx.get("profiles_dir") or "profiles"
    schema = load_schema(profiles_dir)
    if schema is None:
        return 500, {"ok": False, "error": "schema not found"}
    try:
        validate_profile(payload, schema)
    except ValueError as e:
        return 400, {"ok": False, "error": str(e)}

    overrides_dir = ctx.get("overrides_dir") or os.path.expanduser(
        "~/.config/gpu-dashboard/profile-overrides"
    )
    os.makedirs(overrides_dir, exist_ok=True)

    # Safe filename : keep only letters/digits/dash/underscore, lowercase
    model = str(payload.get("model", "override"))
    safe = _re.sub(r"[^A-Za-z0-9_-]+", "-", model).strip("-").lower() or "override"
    path = os.path.join(overrides_dir, f"{safe}.json")
    try:
        import json as _json
        with open(path, "w", encoding="utf-8") as f:
            _json.dump(payload, f, indent=2)
    except OSError as e:
        return 500, {"ok": False, "error": f"write failed: {e}"}

    return 200, {"ok": True, "path": path, "model": model}

# ────────────────────────── /api/fan-curve ────────────────────────────────


def handle_fan_curve_get(ctx: dict) -> Response:
    """Return the active fan curve + current target % + daemon status + hysteresis."""
    from ..modules import fan_curve as _fc
    profile = ctx.get("profile") or {}
    curve = _fc.pick_curve(profile)
    daemon = ctx.get("fan_curve_daemon")
    cfg = ctx["config"]
    return 200, {
        "enabled": cfg.get_bool("MODULE_FAN_CURVE"),
        "running": daemon is not None and getattr(daemon, "_thread", None) is not None,
        "curve": curve,
        "current_target_pct": getattr(daemon, "_last_pct", None) if daemon else None,
        # R&D #4.4 — hysteresis settings (defaults match daemon defaults)
        "hysteresis_c": float(cfg.get("FAN_CURVE_HYSTERESIS_C", "3") or "3"),
        "hysteresis_s": float(cfg.get("FAN_CURVE_HYSTERESIS_S", "15") or "15"),
    }

def handle_fan_curve_post(ctx: dict, payload: dict) -> Response:
    """Save a user-edited fan curve to ~/.config/gpu-dashboard/fan_curve.json.

    Apply the new curve to the running daemon IMMEDIATELY (no waiting for
    the next tick) — user feedback : 'sauvegarde doit-être appliqué tout
    de suite'.
    """
    from ..modules import fan_curve as _fc
    curve = payload.get("curve") if isinstance(payload, dict) else None
    ok, err = _fc.validate_user_curve(curve)
    if not ok:
        return 400, {"ok": False, "error": err}

    # R&D #4.4 — optional hysteresis params (bounded sanity check)
    hys_c_raw = payload.get("hysteresis_c") if isinstance(payload, dict) else None
    hys_s_raw = payload.get("hysteresis_s") if isinstance(payload, dict) else None
    hysteresis_c = None
    hysteresis_s = None
    if hys_c_raw is not None:
        try:
            v = float(hys_c_raw)
            if 0 <= v <= 20:
                hysteresis_c = v
            else:
                return 400, {"ok": False, "error": "hysteresis_c out of range [0,20]"}
        except (TypeError, ValueError):
            return 400, {"ok": False, "error": "hysteresis_c must be a number"}
    if hys_s_raw is not None:
        try:
            v = float(hys_s_raw)
            if 0 <= v <= 600:
                hysteresis_s = v
            else:
                return 400, {"ok": False, "error": "hysteresis_s out of range [0,600]"}
        except (TypeError, ValueError):
            return 400, {"ok": False, "error": "hysteresis_s must be a number"}

    path = os.path.expanduser("~/.config/gpu-dashboard/fan_curve.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload_save: dict = {"curve": curve}
    if hysteresis_c is not None:
        payload_save["hysteresis_c"] = hysteresis_c
    if hysteresis_s is not None:
        payload_save["hysteresis_s"] = hysteresis_s
    with open(path, "w") as f:
        json.dump(payload_save, f, indent=2)

    # Apply the new curve to the running daemon NOW + force an immediate
    # tick so the fan speed updates within milliseconds of Save being clicked.
    daemon = ctx.get("fan_curve_daemon")
    applied_now = False
    current_target_pct = None
    if daemon is not None:
        try:
            daemon.update_curve(curve)
            # R&D #4.4 — apply hysteresis settings live to the running daemon
            if hysteresis_c is not None:
                daemon._hysteresis_c = hysteresis_c
            if hysteresis_s is not None:
                daemon._hysteresis_s = hysteresis_s
            # Force an immediate evaluation : read latest temp, interpolate,
            # apply via nvidia-settings.
            temp = daemon._read_temp() if hasattr(daemon, "_read_temp") else None
            if temp is not None:
                pct = _fc.interpolate(curve, temp)
                _fc.apply_fan_speed(pct, daemon._display, daemon._xauth)
                daemon._last_pct = pct
                current_target_pct = pct
                applied_now = True
        except Exception as e:
            return 200, {
                "ok": True, "path": path, "curve": curve,
                "applied_now": False,
                "warning": f"saved, but immediate apply failed: {e}",
            }

    return 200, {
        "ok": True, "path": path, "curve": curve,
        "applied_now": applied_now,
        "current_target_pct": current_target_pct,
    }

def handle_push_vapid(ctx: dict) -> Response:
    """Return the VAPID public key for browser push subscription.

    The frontend feeds this into PushManager.subscribe({applicationServerKey}).
    Private key stays server-side, never exposed.
    """
    from ..modules import web_push
    cfg_dir = os.path.expanduser("~/.config/gpu-dashboard")
    try:
        data = web_push.ensure_vapid_keys(cfg_dir)
    except Exception as e:
        return 500, {"ok": False, "error": f"VAPID generation failed: {e}"}
    return 200, {"ok": True, "public_key": data["public_key"]}

def handle_push_subscribe(ctx: dict, payload: dict) -> Response:
    """Save a browser's push subscription to the DB.

    Payload shape (from PushSubscription.toJSON()) :
      {endpoint: "...", keys: {p256dh: "...", auth: "..."}}
    """
    storage = ctx.get("storage")
    if storage is None:
        return 503, {"ok": False, "error": "storage not available"}

    endpoint = payload.get("endpoint")
    keys = payload.get("keys") or {}
    p256dh = keys.get("p256dh")
    auth = keys.get("auth")
    if not endpoint or not p256dh or not auth:
        return 400, {"ok": False, "error": "endpoint + keys.p256dh + keys.auth required"}

    storage.add_push_subscription(endpoint, p256dh, auth)
    return 200, {"ok": True}

def handle_push_unsubscribe(ctx: dict, payload: dict) -> Response:
    """Remove a subscription by endpoint."""
    storage = ctx.get("storage")
    if storage is None:
        return 503, {"ok": False, "error": "storage not available"}
    endpoint = payload.get("endpoint")
    if not endpoint:
        return 400, {"ok": False, "error": "endpoint required"}
    n = storage.remove_push_subscription(endpoint)
    return 200, {"ok": True, "removed": n}

def handle_push_status(ctx: dict) -> Response:
    """Return the count of active push subscriptions + the VAPID public key."""
    storage = ctx.get("storage")
    if storage is None:
        return 503, {"ok": False, "error": "storage not available"}
    from ..modules import web_push
    cfg_dir = os.path.expanduser("~/.config/gpu-dashboard")
    try:
        data = web_push.ensure_vapid_keys(cfg_dir)
        public_key = data["public_key"]
    except Exception:
        public_key = None
    return 200, {
        "ok": True,
        "count": len(storage.list_push_subscriptions()),
        "vapid_public_key": public_key,
    }

# ────────────────────────── /api/update/* ─────────────────────────────────


# L957-L967 moved to api/ops.py (cycle 9)



# L1378-L1425 moved to api/ops.py (cycle 9)



# L1428-L1451 moved to api/ops.py (cycle 9)



# L1454-L1493 moved to api/ops.py (cycle 9)



# L1496-L1518 moved to api/ops.py (cycle 9)



# L1521-L1568 moved to api/ops.py (cycle 9)



# L1571-L1593 moved to api/ops.py (cycle 9)



# L1596-L1630 moved to api/ops.py (cycle 9)



# L1633-L1675 moved to api/ops.py (cycle 9)



# L1678-L1718 moved to api/ops.py (cycle 9)



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
