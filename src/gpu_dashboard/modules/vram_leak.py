"""Module vram_leak — Per-process VRAM leak detector (R&D #22.3).

The #1 cause of 24/7 inference-rig restarts is a slowly-growing VRAM
allocator. PyTorch's cudaMallocAsync cache, long-context LLM KV
caches that never recompact, ComfyUI checkpoint-loader leaks — all
look the same from outside the process : VRAM creeps up by tens of
MiB an hour until an OOM kill, eight hours later.

This module samples the per-PID `used_gpu_memory` from nvidia-smi
every poll, persists a rolling window per PID, and fits a simple
linear regression to detect sustained growth. Verdict :

  - stable     (slope < 5 MiB / h)
  - growing    (5-50 MiB / h)
  - leaking    (>50 MiB / h OR >5% / h of process's own RSS)

Pure observation — never kills processes. Surfaces the leaking PID
and the projected OOM time at the current rate.

stdlib only.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from typing import Optional


NAME = "vram_leak"


_HISTORY_PATH = "~/.config/gpu-dashboard/vram_history.json"
_SAMPLES_PER_PID_MAX = 360  # ~1 hour at 10 s poll = 360 samples
_WINDOW_S_DEFAULT = 3600
_LEAK_SLOPE_MIB_PER_HOUR = 50.0
_GROWING_SLOPE_MIB_PER_HOUR = 5.0

_lock = threading.Lock()


def history_path() -> str:
    return os.path.expanduser(_HISTORY_PATH)


def load_history() -> dict:
    p = history_path()
    if not os.path.exists(p):
        return {}
    try:
        with open(p) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_history(data: dict) -> None:
    p = history_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f:
        json.dump(data, f)


def sample_now(timeout: float = 2.0) -> list[dict]:
    """`nvidia-smi --query-compute-apps=pid,process_name,used_gpu_memory`.
    Returns list of {pid, comm, vram_mib}."""
    if not shutil.which("nvidia-smi"):
        return []
    try:
        r = subprocess.run(
            ["nvidia-smi",
             "--query-compute-apps=pid,process_name,used_gpu_memory",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=timeout,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return []
    if r.returncode != 0:
        return []
    out: list[dict] = []
    for line in r.stdout.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 3 or not parts[0].isdigit():
            continue
        try:
            mib = int(parts[2])
        except ValueError:
            continue
        out.append({"pid": int(parts[0]), "comm": parts[1], "vram_mib": mib})
    return out


def record_samples(samples: list[dict], now_ts: Optional[float] = None) -> dict:
    """Append samples to per-PID history. Returns the updated history."""
    if now_ts is None:
        now_ts = time.time()
    ts = int(now_ts)
    with _lock:
        history = load_history()
        for s in samples:
            key = str(s["pid"])
            entry = history.get(key, {"comm": s["comm"], "samples": []})
            entry["comm"] = s["comm"]
            entry["samples"].append({"ts": ts, "vram_mib": s["vram_mib"]})
            entry["samples"] = entry["samples"][-_SAMPLES_PER_PID_MAX:]
            history[key] = entry
        # Prune PIDs not seen in this sample AND with no recent activity
        seen = {str(s["pid"]) for s in samples}
        for pid_key in list(history.keys()):
            if pid_key in seen:
                continue
            last = history[pid_key].get("samples", [])
            if not last or now_ts - last[-1]["ts"] > 86400:
                del history[pid_key]
        save_history(history)
    return history


def linear_slope(samples: list[dict]) -> Optional[float]:
    """Least-squares slope of vram_mib vs ts. Returns MiB per hour, or
    None if fewer than 3 samples."""
    if len(samples) < 3:
        return None
    n = len(samples)
    t0 = samples[0]["ts"]
    xs = [s["ts"] - t0 for s in samples]
    ys = [s["vram_mib"] for s in samples]
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    num = sum((xs[i] - mean_x) * (ys[i] - mean_y) for i in range(n))
    den = sum((x - mean_x) ** 2 for x in xs)
    if den == 0:
        return None
    slope_per_s = num / den
    return slope_per_s * 3600  # → per hour


def classify(slope_mib_per_h: Optional[float],
              current_mib: int) -> dict:
    """Return {verdict, slope, projected_oom_minutes}."""
    if slope_mib_per_h is None:
        return {"verdict": "warming",
                "reason": "need at least 3 samples to estimate slope.",
                "slope_mib_per_hour": None,
                "projected_oom_minutes": None}
    if slope_mib_per_h < _GROWING_SLOPE_MIB_PER_HOUR:
        return {"verdict": "stable",
                "reason": (f"VRAM grows at {slope_mib_per_h:.1f} MiB/h "
                           "— within noise."),
                "slope_mib_per_hour": round(slope_mib_per_h, 2),
                "projected_oom_minutes": None}
    growth_pct_per_h = (slope_mib_per_h / current_mib * 100
                         if current_mib > 0 else 0)
    if slope_mib_per_h < _LEAK_SLOPE_MIB_PER_HOUR and growth_pct_per_h < 5:
        return {"verdict": "growing",
                "reason": (f"VRAM grows at {slope_mib_per_h:.1f} MiB/h "
                           f"({growth_pct_per_h:.1f}%/h). Watch but not "
                           "actionable yet."),
                "slope_mib_per_hour": round(slope_mib_per_h, 2),
                "projected_oom_minutes": None}
    # Leaking — project OOM assuming 24 GiB ceiling
    headroom_mib = max(0, 24 * 1024 - current_mib)
    oom_minutes = (headroom_mib / slope_mib_per_h * 60
                   if slope_mib_per_h > 0 else None)
    return {
        "verdict": "leaking",
        "reason": (f"VRAM grows at {slope_mib_per_h:.1f} MiB/h "
                   f"({growth_pct_per_h:.1f}%/h of {current_mib} MiB). "
                   "Probably a leak — restart the process before OOM."),
        "slope_mib_per_hour": round(slope_mib_per_h, 2),
        "projected_oom_minutes": (round(oom_minutes, 1)
                                    if oom_minutes is not None else None),
    }


def analyze_history(history: dict,
                     window_s: int = _WINDOW_S_DEFAULT,
                     now_ts: Optional[float] = None) -> list[dict]:
    """For each PID, compute slope + verdict over the last window_s."""
    if now_ts is None:
        now_ts = time.time()
    cutoff = now_ts - window_s
    out: list[dict] = []
    for pid_key, entry in history.items():
        samples = [s for s in entry.get("samples", [])
                   if s["ts"] >= cutoff]
        if not samples:
            continue
        current_mib = samples[-1]["vram_mib"]
        slope = linear_slope(samples)
        verdict = classify(slope, current_mib)
        out.append({
            "pid": int(pid_key) if pid_key.isdigit() else pid_key,
            "comm": entry.get("comm", "?"),
            "current_mib": current_mib,
            "sample_count": len(samples),
            "verdict": verdict,
        })
    return out


def status(cfg=None) -> dict:
    """Aggregate snapshot. Also records a new sample on each call to
    populate history."""
    samples = sample_now()
    record_samples(samples)
    history = load_history()
    window = _WINDOW_S_DEFAULT
    if cfg:
        try:
            window = int(cfg.get("VRAM_LEAK_WINDOW_S", str(window)))
        except (ValueError, TypeError):
            pass
    procs = analyze_history(history, window_s=window)
    leakers = [p for p in procs if p["verdict"]["verdict"] == "leaking"]
    growers = [p for p in procs if p["verdict"]["verdict"] == "growing"]
    return {
        "ok": True,
        "window_s": window,
        "process_count": len(procs),
        "leaking_count": len(leakers),
        "growing_count": len(growers),
        "processes": procs,
    }
