"""HTTP handler for /api/hot-gpu-wizard (R&D #13.6).

Aggregates inputs from existing API endpoints + sampler buffer and runs
the 5-step diagnostic.
"""
from __future__ import annotations

from typing import Optional, Tuple

from . import _core as _m


Response = Tuple[int, dict]


def _gpu_card_snapshot(*args, **kw):
    return _m._gpu_card_snapshot(*args, **kw)


def handle_hot_gpu_wizard(ctx: dict, params: Optional[dict] = None) -> Response:
    """Run the wizard against the live GPU + recent samples + drift history."""
    from ..modules import hot_gpu_wizard as wiz

    # Live GPU temp
    snap = _gpu_card_snapshot(gpu_index=0)
    gpu_temp_c = snap.get("temp") if snap and snap.get("alive") else None

    # Fan state — first fan from sampler/_core helpers
    fan_state = None
    if snap and snap.get("alive"):
        # Construct fan_state from /api/state's fans[0] shape if available
        cfg = ctx.get("config")
        try:
            fans = _m._per_fan_state(cfg) if cfg else []
            if fans:
                fan_state = fans[0]
        except Exception:
            fan_state = None

    # Profile curve : try to read the active fan_curve
    profile_curve = None
    try:
        from ..modules import fan_curve as _fc
        curve_data = _fc.load_active_curve() if hasattr(_fc, "load_active_curve") else None
        if curve_data and isinstance(curve_data, list):
            profile_curve = curve_data
        elif isinstance(curve_data, dict) and isinstance(curve_data.get("points"), list):
            profile_curve = curve_data["points"]
    except Exception:
        profile_curve = None

    # Recent samples
    samples_recent: list = []
    sampler = ctx.get("sampler")
    if sampler:
        try:
            samples_recent = sampler.snapshot()[-120:]  # last ~10min @ 5s interval
        except Exception:
            samples_recent = []

    # Drift last entry — read from drift history
    last_drift = None
    try:
        from . import diagnostics as _diag
        # handle_drift_check returns (code, body)
        _, drift_body = _diag.handle_drift_check(ctx)
        last_drift = drift_body.get("last_drift") if isinstance(drift_body, dict) else None
    except Exception:
        last_drift = None

    # Throttle count last hour : query /api/clock-events. Simpler : count
    # samples whose throttle reasons were non-empty in the last hour.
    throttle_count_1h = 0
    try:
        if samples_recent:
            import time as _t
            cutoff = _t.time() - 3600
            for s in samples_recent:
                ts = s.get("ts")
                if isinstance(ts, (int, float)) and float(ts) >= cutoff:
                    reasons = s.get("throttle_reasons") or s.get("clock_event_reasons") or ""
                    if reasons:
                        throttle_count_1h += 1
    except Exception:
        throttle_count_1h = None

    result = wiz.run(
        gpu_temp_c=gpu_temp_c,
        fan_state=fan_state,
        profile_curve=profile_curve,
        samples_recent=samples_recent,
        last_drift=last_drift,
        throttle_count_1h=throttle_count_1h,
    )
    return 200, result
