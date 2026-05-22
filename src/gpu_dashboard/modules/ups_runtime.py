"""Module ups_runtime — UPS runtime vs GPU-load estimator (R&D #20.7).

When the grid drops, the UPS reports a 'battery.runtime' in seconds.
But that estimate assumes the current load — a 350 W RTX 3090 under
training will drain the UPS in half the time of an idle desktop.
Users need an actionable verdict, not raw seconds :

  - "you have 12 minutes — pause the training now"
  - "battery low, but at idle load you have an hour — finish the job"

This module reads :
  - UPS status (via the existing ups_nut module)
  - Current total GPU power draw (via nvidia-smi --query-gpu=power.draw)
  - UPS rated capacity (config UPS_CAPACITY_WH, defaults to 600 Wh)

Then computes :
  - effective_runtime_min : UPS-reported runtime adjusted for current draw
  - safe_runtime_min      : runtime - 10% safety buffer
  - verdict               : 'on_grid' / 'safe' / 'pause_jobs' / 'shutdown_now'

stdlib only.
"""
from __future__ import annotations

import shutil
import subprocess
from typing import Optional


NAME = "ups_runtime"


def gpu_total_power_w(timeout: float = 2.0) -> Optional[float]:
    """Sum power.draw across all NVIDIA GPUs."""
    if not shutil.which("nvidia-smi"):
        return None
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=power.draw",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=timeout,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if r.returncode != 0:
        return None
    total = 0.0
    seen = 0
    for line in r.stdout.splitlines():
        s = line.strip()
        if not s or s.lower() in ("n/a", "[n/a]", "not supported"):
            continue
        try:
            total += float(s)
            seen += 1
        except ValueError:
            continue
    return total if seen else None


def adjust_runtime(reported_s: int, gpu_w: float,
                    baseline_w: float, ups_capacity_wh: float) -> int:
    """If UPS reports `reported_s` at baseline load, and current load is
    baseline_w + gpu_w, scale the runtime accordingly.

    Conservatively bounded :
      - never extend beyond reported_s (the UPS knows its battery state)
      - clamp to 0 minimum
    """
    if gpu_w <= 0 or baseline_w <= 0 or ups_capacity_wh <= 0:
        return max(0, reported_s)
    base_drain_wh = baseline_w * (reported_s / 3600)
    current_total_w = baseline_w + gpu_w
    if current_total_w <= 0:
        return max(0, reported_s)
    # Energy still in battery at this moment :
    remaining_wh = min(base_drain_wh, ups_capacity_wh)
    new_runtime_s = int((remaining_wh / current_total_w) * 3600)
    return max(0, min(reported_s, new_runtime_s))


def classify(on_battery: bool, low_battery: bool,
              runtime_s: Optional[int],
              safe_buffer_pct: float = 10.0) -> dict:
    """Verdict logic. Returns {verdict, reason, safe_runtime_s}."""
    if not on_battery and not low_battery:
        return {
            "verdict": "on_grid",
            "reason": "Grid is up. UPS in stand-by. Nothing to do.",
            "safe_runtime_s": runtime_s,
        }
    if runtime_s is None:
        return {
            "verdict": "shutdown_now",
            "reason": "UPS reports no runtime estimate — assume imminent.",
            "safe_runtime_s": 0,
        }
    safe_s = int(runtime_s * (1 - safe_buffer_pct / 100))
    if low_battery or safe_s < 60:
        return {
            "verdict": "shutdown_now",
            "reason": (f"UPS low-battery state. Safe runtime "
                       f"~{max(0, safe_s)} s. Save & shut down NOW."),
            "safe_runtime_s": max(0, safe_s),
        }
    if safe_s < 300:  # < 5 min
        return {
            "verdict": "pause_jobs",
            "reason": (f"Only {safe_s // 60} min of safe runtime left. "
                       "Pause GPU jobs and prepare for shutdown."),
            "safe_runtime_s": safe_s,
        }
    return {
        "verdict": "safe",
        "reason": (f"On battery but {safe_s // 60} min of safe runtime "
                   "remain at current load. Workload can continue."),
        "safe_runtime_s": safe_s,
    }


def status(cfg=None) -> dict:
    """Aggregate snapshot."""
    capacity_wh = 600.0
    baseline_w = 80.0
    safe_buffer = 10.0
    if cfg:
        try:
            capacity_wh = float(cfg.get("UPS_CAPACITY_WH", "600"))
        except (ValueError, TypeError):
            pass
        try:
            baseline_w = float(cfg.get("UPS_BASELINE_W", "80"))
        except (ValueError, TypeError):
            pass
        try:
            safe_buffer = float(cfg.get("UPS_SAFE_BUFFER_PCT", "10"))
        except (ValueError, TypeError):
            pass
    # Pull UPS info from the existing ups_nut helper
    from . import ups_nut
    ups = ups_nut.query()
    gpu_w = gpu_total_power_w()
    raw_runtime = ups.get("runtime_s") if isinstance(ups, dict) else None
    adjusted_runtime = (
        adjust_runtime(raw_runtime, gpu_w, baseline_w, capacity_wh)
        if (raw_runtime is not None and gpu_w is not None)
        else raw_runtime
    )
    on_batt = bool(ups.get("on_battery")) if isinstance(ups, dict) else False
    low_batt = bool(ups.get("low_battery")) if isinstance(ups, dict) else False
    verdict = classify(on_batt, low_batt, adjusted_runtime, safe_buffer)
    return {
        "ok": True,
        "ups_available": bool(ups.get("available")) if isinstance(ups, dict) else False,
        "on_battery": on_batt,
        "low_battery": low_batt,
        "reported_runtime_s": raw_runtime,
        "adjusted_runtime_s": adjusted_runtime,
        "gpu_total_power_w": gpu_w,
        "baseline_w": baseline_w,
        "capacity_wh": capacity_wh,
        "verdict": verdict,
    }
