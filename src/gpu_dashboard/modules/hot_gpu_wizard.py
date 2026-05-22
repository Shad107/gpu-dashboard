"""Module hot_gpu_wizard — step-through diagnostic for 'why is my GPU hot' (R&D #13.6).

Walks 5 decision tree steps and produces a structured verdict the user
can act on. Reuses existing modules :

  1. **Ambient temperature** — read CPU/hwmon, compute GPU-ambient delta.
     Hot ambient (>30°C) raises the floor for GPU temp regardless of cooling.
  2. **Fan curve match** — current fan RPM vs configured fan curve.
     If RPM is way below the curve's expected % at this temp, fan controller
     may be hung or the curve is too soft.
  3. **Dust suspect** — compares current (temp - ambient) / power ratio to a
     saved baseline. A worsening ratio over weeks usually means dust.
  4. **Driver age** — checks days since last driver/kernel change. Old drivers
     occasionally have power management regressions ; very recent ones may have
     introduced a new bug.
  5. **Throttle history** — counts clock-event-throttle samples in last hour.

Each step returns {ok, kind: pass|warn|fail, detail, fix}. The wizard
aggregates these into a verdict with overall kind = worst-of-all-steps.

stdlib only (subprocess + json + glob).
"""
from __future__ import annotations

import glob
import os
import re
import subprocess
import time
from typing import Optional, Tuple


NAME = "hot_gpu_wizard"

_AMBIENT_HOT_C = 30
_DUST_BASELINE_PATH = "~/.config/gpu-dashboard/hot_gpu_baseline.json"


def _baseline_path() -> str:
    return os.path.expanduser(_DUST_BASELINE_PATH)


def _read_hwmon_ambient() -> Optional[float]:
    """Best-effort read of an ambient/case sensor via hwmon. Returns the
    coolest temp found that isn't the CPU (heuristic for ambient)."""
    candidates: list = []
    for sensor in glob.glob("/sys/class/hwmon/hwmon*/temp*_input"):
        try:
            with open(sensor) as f:
                raw = f.read().strip()
            milli = int(raw)
            # Skip implausible values
            if milli <= 0 or milli > 200_000:
                continue
            candidates.append(milli / 1000.0)
        except (OSError, ValueError):
            continue
    if not candidates:
        return None
    # Heuristic : ambient is the coolest reading
    return min(candidates)


def step_ambient(gpu_temp_c: Optional[float] = None) -> dict:
    """Compare ambient (via hwmon) and GPU temp. Return verdict."""
    ambient = _read_hwmon_ambient()
    if ambient is None:
        return {"step": "ambient", "kind": "skip",
                "detail": "no hwmon sensor found",
                "fix": "install lm-sensors / smartmontools to expose ambient"}
    delta = (gpu_temp_c - ambient) if gpu_temp_c is not None else None
    if ambient >= _AMBIENT_HOT_C:
        return {"step": "ambient", "kind": "warn",
                "detail": f"ambient {ambient:.1f}°C (hot room)",
                "delta_c": delta, "ambient_c": round(ambient, 1),
                "fix": "improve case airflow / open window / lower room AC"}
    return {"step": "ambient", "kind": "pass",
            "detail": f"ambient {ambient:.1f}°C OK",
            "delta_c": delta, "ambient_c": round(ambient, 1),
            "fix": ""}


def step_fan_curve(fan_state: Optional[dict], gpu_temp_c: Optional[float],
                   profile_curve: Optional[list] = None) -> dict:
    """Compare measured fan % with the curve's expected % at current temp.
    fan_state = {pct, rpm} from /api/state.fans[0]. profile_curve = list of
    [{temp, fan_pct}] from the active profile."""
    if not fan_state:
        return {"step": "fan_curve", "kind": "skip",
                "detail": "no fan data available", "fix": ""}
    measured_pct = fan_state.get("pct") if isinstance(fan_state, dict) else None
    if measured_pct is None or gpu_temp_c is None:
        return {"step": "fan_curve", "kind": "skip",
                "detail": "missing pct or temp", "fix": ""}
    # Interpolate the curve at current temp
    expected_pct = None
    if profile_curve and isinstance(profile_curve, list) and len(profile_curve) >= 2:
        sorted_curve = sorted(profile_curve, key=lambda p: p.get("temp", 0))
        for i in range(len(sorted_curve) - 1):
            t0 = sorted_curve[i].get("temp", 0)
            t1 = sorted_curve[i + 1].get("temp", 0)
            p0 = sorted_curve[i].get("fan_pct", sorted_curve[i].get("pct", 0))
            p1 = sorted_curve[i + 1].get("fan_pct", sorted_curve[i + 1].get("pct", 0))
            if t0 <= gpu_temp_c <= t1 and t1 > t0:
                expected_pct = p0 + (p1 - p0) * (gpu_temp_c - t0) / (t1 - t0)
                break
        if expected_pct is None:
            # outside curve range : pin to endpoint
            if gpu_temp_c < sorted_curve[0].get("temp", 0):
                expected_pct = sorted_curve[0].get("fan_pct", sorted_curve[0].get("pct", 0))
            else:
                expected_pct = sorted_curve[-1].get("fan_pct", sorted_curve[-1].get("pct", 100))
    if expected_pct is None:
        return {"step": "fan_curve", "kind": "skip",
                "detail": "no fan curve configured", "fix": ""}
    gap = measured_pct - expected_pct
    if gap < -10:
        return {"step": "fan_curve", "kind": "fail",
                "detail": f"fan at {measured_pct}% but curve says {expected_pct:.0f}% (deficit {-gap:.0f} pts)",
                "measured_pct": measured_pct, "expected_pct": round(expected_pct, 1),
                "fix": "check coolbits / fan controller / verify curve is applied"}
    if gap < -5:
        return {"step": "fan_curve", "kind": "warn",
                "detail": f"fan slightly below curve (gap {-gap:.0f} pts)",
                "measured_pct": measured_pct, "expected_pct": round(expected_pct, 1),
                "fix": "consider stiffening curve at this temp"}
    return {"step": "fan_curve", "kind": "pass",
            "detail": f"fan {measured_pct}% matches curve ({expected_pct:.0f}%)",
            "measured_pct": measured_pct, "expected_pct": round(expected_pct, 1),
            "fix": ""}


def step_dust_suspect(samples_recent: Optional[list]) -> dict:
    """Compare current (temp / power) ratio to a saved baseline. If the
    ratio drifted upward by > 15% it's suggestive of dust accumulation
    OR aging thermal paste."""
    if not samples_recent or len(samples_recent) < 30:
        return {"step": "dust", "kind": "skip",
                "detail": "need 30+ recent samples to compute ratio", "fix": ""}
    # Compute temp/power ratio over the recent window (filtered to powered samples)
    valid = [s for s in samples_recent
             if s.get("temp") and s.get("power") and float(s["power"]) > 50]
    if len(valid) < 10:
        return {"step": "dust", "kind": "skip",
                "detail": "not enough loaded samples (need >50W)", "fix": ""}
    ratios = [float(s["temp"]) / float(s["power"]) for s in valid]
    current_ratio = sum(ratios) / len(ratios)

    import json as _json
    baseline_p = _baseline_path()
    if not os.path.exists(baseline_p):
        try:
            os.makedirs(os.path.dirname(baseline_p), exist_ok=True)
            with open(baseline_p, "w") as f:
                _json.dump({"ratio": current_ratio, "ts": int(time.time())}, f)
        except OSError:
            pass
        return {"step": "dust", "kind": "pass",
                "detail": f"baseline saved ({current_ratio:.3f} °C/W)",
                "current_ratio": round(current_ratio, 3),
                "fix": ""}
    try:
        with open(baseline_p) as f:
            base = _json.load(f)
        baseline_ratio = float(base.get("ratio", 0))
    except (OSError, _json.JSONDecodeError, TypeError, ValueError):
        baseline_ratio = current_ratio
    if baseline_ratio <= 0:
        return {"step": "dust", "kind": "skip",
                "detail": "invalid baseline", "fix": ""}
    drift_pct = (current_ratio - baseline_ratio) / baseline_ratio * 100
    if drift_pct > 25:
        return {"step": "dust", "kind": "fail",
                "detail": f"temp/power ratio drifted +{drift_pct:.0f}% vs baseline (dust very likely)",
                "current_ratio": round(current_ratio, 3),
                "baseline_ratio": round(baseline_ratio, 3),
                "drift_pct": round(drift_pct, 1),
                "fix": "clean dust filters + GPU fan blades ; consider repasting after 2 years"}
    if drift_pct > 15:
        return {"step": "dust", "kind": "warn",
                "detail": f"temp/power ratio drifted +{drift_pct:.0f}% (mild dust suspect)",
                "current_ratio": round(current_ratio, 3),
                "baseline_ratio": round(baseline_ratio, 3),
                "drift_pct": round(drift_pct, 1),
                "fix": "schedule a dust filter check"}
    return {"step": "dust", "kind": "pass",
            "detail": f"temp/power ratio stable ({drift_pct:+.1f}% vs baseline)",
            "current_ratio": round(current_ratio, 3),
            "baseline_ratio": round(baseline_ratio, 3),
            "drift_pct": round(drift_pct, 1),
            "fix": ""}


def step_driver_age(last_drift: Optional[dict]) -> dict:
    """Read R&D #5.2 drift detector's last entry. If 'no recent change'
    AND uptime suggests we've been on the same driver for months, lean
    toward warn (driver may be carrying a regression). If a very recent
    change (<24h), it could also be the cause."""
    if not last_drift:
        return {"step": "driver_age", "kind": "skip",
                "detail": "no drift history yet", "fix": ""}
    ts = int(last_drift.get("ts", 0))
    if ts <= 0:
        return {"step": "driver_age", "kind": "skip", "detail": "no ts", "fix": ""}
    age_d = (time.time() - ts) / 86400
    if age_d < 1:
        return {"step": "driver_age", "kind": "warn",
                "detail": f"driver/kernel changed {age_d * 24:.0f}h ago",
                "age_days": round(age_d, 1),
                "fix": "if temps regressed after the change, consider rolling back the driver"}
    if age_d > 180:
        return {"step": "driver_age", "kind": "warn",
                "detail": f"driver unchanged for {age_d:.0f} days",
                "age_days": round(age_d, 1),
                "fix": "check release notes for known thermal/power fixes since"}
    return {"step": "driver_age", "kind": "pass",
            "detail": f"driver age {age_d:.0f} days OK",
            "age_days": round(age_d, 1), "fix": ""}


def step_throttle_history(throttle_count_1h: Optional[int]) -> dict:
    """Read /api/clock-events count in last hour or recent throttle samples."""
    if throttle_count_1h is None:
        return {"step": "throttle", "kind": "skip",
                "detail": "no throttle history", "fix": ""}
    if throttle_count_1h > 30:
        return {"step": "throttle", "kind": "fail",
                "detail": f"{throttle_count_1h} throttle events in last hour",
                "count_1h": throttle_count_1h,
                "fix": "this confirms thermal/power limiting — check power-limit + cooling"}
    if throttle_count_1h > 5:
        return {"step": "throttle", "kind": "warn",
                "detail": f"{throttle_count_1h} throttle events in last hour",
                "count_1h": throttle_count_1h,
                "fix": "monitor — if sustained, raise cooling or lower power limit"}
    return {"step": "throttle", "kind": "pass",
            "detail": f"{throttle_count_1h} throttle events in last hour OK",
            "count_1h": throttle_count_1h, "fix": ""}


def aggregate_verdict(steps: list) -> str:
    """Worst-of-all-steps : fail > warn > pass > skip."""
    rank = {"fail": 3, "warn": 2, "pass": 1, "skip": 0}
    worst = max((rank.get(s.get("kind", "skip"), 0) for s in steps), default=0)
    inv = {v: k for k, v in rank.items()}
    return inv.get(worst, "skip")


def run(gpu_temp_c: Optional[float] = None,
        fan_state: Optional[dict] = None,
        profile_curve: Optional[list] = None,
        samples_recent: Optional[list] = None,
        last_drift: Optional[dict] = None,
        throttle_count_1h: Optional[int] = None) -> dict:
    """Top-level entry. Caller supplies whatever they have ; missing inputs
    cause individual steps to return 'skip' rather than failing the wizard."""
    steps = [
        step_ambient(gpu_temp_c),
        step_fan_curve(fan_state, gpu_temp_c, profile_curve),
        step_dust_suspect(samples_recent),
        step_driver_age(last_drift),
        step_throttle_history(throttle_count_1h),
    ]
    verdict = aggregate_verdict(steps)
    actions = [s["fix"] for s in steps if s.get("fix") and s.get("kind") in ("warn", "fail")]
    return {
        "ok": True,
        "verdict": verdict,
        "steps": steps,
        "actions": actions,
        "ts": int(time.time()),
    }
