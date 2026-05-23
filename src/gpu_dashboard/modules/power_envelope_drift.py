"""Module power_envelope_drift — Silent power-limit reset detector (R&D #27.4).

Users routinely set `nvidia-smi -pl 350` to lift a 3090's 270 W
shipped cap, or `-pl 200` to undervolt a 4090 for inference. The
setting is *not* persistent — every nvidia driver upgrade can reset
it. Worse, it's also reset by a clean reboot when nvidia-persistenced
isn't running.

This module baselines the user-configured `power.limit` on first
observation per GPU UUID. Subsequent calls detect drift :

  - reset_to_default   limit == default and prev > default
                       (clear sign of upgrade reset)
  - drifted            limit changed by ≥5 W without matching default
  - clean              within ±2 W of baseline

Pairs naturally with shipped driver_vault (#16.x) and DKMS status
(#24.3) — if drift coincides with a recent kernel/driver upgrade,
the verdict points to that as the cause.

stdlib only.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from typing import Optional


NAME = "power_envelope_drift"


_BASELINE_PATH = "~/.config/gpu-dashboard/power_envelope_baseline.json"


def baseline_path() -> str:
    return os.path.expanduser(_BASELINE_PATH)


def load_baseline() -> dict:
    p = baseline_path()
    if not os.path.exists(p):
        return {}
    try:
        with open(p) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_baseline(data: dict) -> None:
    p = baseline_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f:
        json.dump(data, f, indent=2)


def query_envelope(timeout: float = 2.0) -> Optional[list[dict]]:
    """Return per-GPU power envelope :
    {uuid, name, current_w, default_w, min_w, max_w}."""
    if not shutil.which("nvidia-smi"):
        return None
    fields = ["uuid", "name", "power.limit", "power.default_limit",
              "power.min_limit", "power.max_limit"]
    try:
        r = subprocess.run(
            ["nvidia-smi", f"--query-gpu={','.join(fields)}",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=timeout,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if r.returncode != 0:
        return None
    out: list[dict] = []
    for line in r.stdout.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 6 or not parts[0].startswith("GPU-"):
            continue
        out.append({
            "uuid": parts[0],
            "name": parts[1],
            "current_w": _to_float(parts[2]),
            "default_w": _to_float(parts[3]),
            "min_w": _to_float(parts[4]),
            "max_w": _to_float(parts[5]),
        })
    return out


def _to_float(s: str) -> Optional[float]:
    s = s.strip()
    if not s or s.lower() in ("n/a", "[n/a]", "not supported"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def classify_drift(prev_w: Optional[float], curr_w: Optional[float],
                    default_w: Optional[float],
                    threshold_w: float = 5.0) -> dict:
    """Per-GPU verdict.

    - first_seen        baseline not present yet
    - clean             |curr - prev| ≤ 2 W
    - reset_to_default  curr == default AND prev > default + threshold
    - drifted           |curr - prev| > threshold (any direction)
    - unknown           curr or prev missing
    """
    if curr_w is None:
        return {"verdict": "unknown",
                "reason": "Could not read current power limit.",
                "severity": "info",
                "delta_w": None}
    if prev_w is None:
        return {"verdict": "first_seen",
                "reason": "Baseline recorded.",
                "severity": "info",
                "delta_w": None}
    delta = curr_w - prev_w
    if abs(delta) <= 2.0:
        return {"verdict": "clean",
                "reason": (f"Power limit stable at {curr_w:.0f} W "
                           "(within ±2 W of baseline)."),
                "severity": "info",
                "delta_w": round(delta, 1)}
    # Reset-to-default heuristic
    if (default_w is not None
            and abs(curr_w - default_w) <= 1.0
            and (prev_w - default_w) > threshold_w):
        return {"verdict": "reset_to_default",
                "reason": (f"Power limit dropped from {prev_w:.0f} W back to "
                           f"factory default {default_w:.0f} W. Almost certainly "
                           "a driver upgrade reset."),
                "severity": "warn",
                "delta_w": round(delta, 1)}
    if abs(delta) > threshold_w:
        direction = "raised" if delta > 0 else "lowered"
        return {"verdict": "drifted",
                "reason": (f"Power limit {direction} from {prev_w:.0f} W to "
                           f"{curr_w:.0f} W (Δ {delta:+.0f} W)."),
                "severity": "info" if delta > 0 else "warn",
                "delta_w": round(delta, 1)}
    return {"verdict": "clean",
            "reason": f"Power limit at {curr_w:.0f} W.",
            "severity": "info",
            "delta_w": round(delta, 1)}


def recovery_command(uuid: str, target_w: Optional[float]) -> str:
    """How to restore a previous power limit. nvidia-smi uses index,
    but accepts -i <uuid> too."""
    if target_w is None:
        return ""
    return f"sudo nvidia-smi -i {uuid} -pl {target_w:.0f}"


def status(cfg=None) -> dict:
    """Aggregate snapshot."""
    envelopes = query_envelope()
    if envelopes is None:
        return {"ok": False,
                "reason": "nvidia-smi unreachable.",
                "gpus": []}
    baseline = load_baseline()
    out: list = []
    worst_severity = "info"
    rank = {"info": 0, "warn": 1, "critical": 2}
    new_baseline = dict(baseline)
    for env in envelopes:
        uuid = env["uuid"]
        base = baseline.get(uuid)
        if base is None:
            new_baseline[uuid] = {
                "first_seen_ts": int(time.time()),
                "current_w": env["current_w"],
                "default_w": env["default_w"],
                "min_w": env["min_w"],
                "max_w": env["max_w"],
            }
            verdict = classify_drift(None, env["current_w"], env["default_w"])
        else:
            verdict = classify_drift(base.get("current_w"),
                                       env["current_w"],
                                       env["default_w"])
        if rank.get(verdict["severity"], 0) > rank.get(worst_severity, 0):
            worst_severity = verdict["severity"]
        recovery = (recovery_command(uuid, base["current_w"])
                    if base and verdict["verdict"] in ("reset_to_default", "drifted")
                    else "")
        out.append({
            **env,
            "baseline_w": base.get("current_w") if base else None,
            "verdict": verdict,
            "recovery_cmd": recovery,
        })
    if new_baseline != baseline:
        save_baseline(new_baseline)
    return {
        "ok": True,
        "gpus": out,
        "gpu_count": len(out),
        "worst_severity": worst_severity,
    }
