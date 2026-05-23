"""Module mem_temp_drift — VRAM memory junction temperature drift (R&D #24.4).

The thermal pads behind the GDDR6X stacks on RTX 3090 / 3090 Ti /
4090 (and A6000 / L40) degrade over months of sustained inference
load. The card looks fine — GPU core temperature stays normal —
but the *memory junction* temperature creeps up 5-10 °C and
eventually triggers MEM_TEMP throttle, causing silent perf cliffs.

NVML exposes the memory temperature on Ampere+ cards via :
  nvidia-smi --query-gpu=temperature.memory --format=csv

This module :
  1. Samples gpu_temp + mem_temp on every poll
  2. Persists a rolling 30-day window of (ts, gpu_t, mem_t)
  3. Computes the *delta* = mem_t - gpu_t — this isolates the
     pad-degradation signal from ambient / fan variation
  4. Compares the recent 24h median delta vs the 30-day-old baseline
     median delta. Drift > +5 °C = warning, > +10 °C = urgent.

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


NAME = "mem_temp_drift"


_HISTORY_PATH = "~/.config/gpu-dashboard/mem_temp_history.json"
_MAX_SAMPLES = 2880   # 30 days × 4 samples/h × 24h = 2880
_WARN_DRIFT_C = 5.0
_URGENT_DRIFT_C = 10.0

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


def query_temps(timeout: float = 2.0) -> list[dict]:
    """Return [{uuid, name, gpu_temp_c, mem_temp_c}, ...]."""
    if not shutil.which("nvidia-smi"):
        return []
    try:
        r = subprocess.run(
            ["nvidia-smi",
             "--query-gpu=uuid,name,temperature.gpu,temperature.memory",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=timeout,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return []
    if r.returncode != 0:
        return []
    out: list[dict] = []
    for line in r.stdout.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 4:
            continue
        try:
            gpu_t = (float(parts[2])
                     if parts[2].replace(".", "").isdigit() else None)
        except ValueError:
            gpu_t = None
        try:
            mem_t = (float(parts[3])
                     if parts[3].replace(".", "").isdigit() else None)
        except ValueError:
            mem_t = None
        out.append({
            "uuid": parts[0],
            "name": parts[1],
            "gpu_temp_c": gpu_t,
            "mem_temp_c": mem_t,
        })
    return out


def record_sample(uuid: str, name: str,
                   gpu_t: Optional[float], mem_t: Optional[float],
                   now_ts: Optional[float] = None) -> dict:
    """Persist one sample per GPU. Returns the updated history."""
    if now_ts is None:
        now_ts = time.time()
    if gpu_t is None or mem_t is None:
        # Without both temps we can't compute drift
        return load_history()
    with _lock:
        hist = load_history()
        entry = hist.get(uuid, {"name": name, "samples": []})
        entry["name"] = name
        entry["samples"].append({
            "ts": int(now_ts),
            "gpu_t": round(gpu_t, 1),
            "mem_t": round(mem_t, 1),
            "delta": round(mem_t - gpu_t, 1),
        })
        entry["samples"] = entry["samples"][-_MAX_SAMPLES:]
        hist[uuid] = entry
        save_history(hist)
    return hist


def median(values: list[float]) -> Optional[float]:
    """Pure-stdlib median."""
    n = len(values)
    if n == 0:
        return None
    s = sorted(values)
    if n % 2:
        return s[n // 2]
    return (s[n // 2 - 1] + s[n // 2]) / 2


def compute_drift(samples: list[dict],
                   now_ts: Optional[float] = None) -> dict:
    """Compare recent 24h median delta vs the oldest 24h-window median.
    Returns {baseline_delta, recent_delta, drift_c, sample_count}.
    Returns None for drift when insufficient samples."""
    if now_ts is None:
        now_ts = time.time()
    if not samples:
        return {"baseline_delta": None, "recent_delta": None,
                "drift_c": None, "sample_count": 0}
    recent_cutoff = now_ts - 86400  # last 24h
    recent = [s for s in samples if s["ts"] >= recent_cutoff]
    earliest_ts = samples[0]["ts"]
    baseline_window_end = earliest_ts + 86400
    baseline = [s for s in samples
                if s["ts"] <= baseline_window_end]
    recent_med = median([s["delta"] for s in recent]) if recent else None
    base_med = median([s["delta"] for s in baseline]) if baseline else None
    drift = (recent_med - base_med
             if recent_med is not None and base_med is not None
             else None)
    return {
        "baseline_delta": base_med,
        "recent_delta": recent_med,
        "drift_c": (round(drift, 1) if drift is not None else None),
        "sample_count": len(samples),
        "baseline_sample_count": len(baseline),
        "recent_sample_count": len(recent),
    }


def classify(drift: dict) -> dict:
    """Return {verdict, reason}."""
    d = drift.get("drift_c")
    n = drift.get("sample_count", 0)
    if d is None or n < 10:
        return {"verdict": "warming",
                "reason": (f"Need more samples ({n}/10) before a "
                            "drift estimate is meaningful.")}
    if d >= _URGENT_DRIFT_C:
        return {"verdict": "urgent",
                "reason": (f"VRAM-to-GPU temp delta drifted +{d:.1f} °C "
                           "from baseline. Likely pad degradation — "
                           "consider repad before MEM_TEMP throttle hits.")}
    if d >= _WARN_DRIFT_C:
        return {"verdict": "pad_degraded",
                "reason": (f"VRAM delta drifted +{d:.1f} °C since first "
                           "observation. Watch for further growth.")}
    if d < 0:
        return {"verdict": "improving",
                "reason": (f"VRAM delta dropped {abs(d):.1f} °C vs "
                           "baseline (clean / better cooling).")}
    return {"verdict": "ok",
            "reason": (f"VRAM delta stable (drift {d:+.1f} °C)."
                       if d != 0 else "VRAM delta unchanged.")}


def status(cfg=None) -> dict:
    """Aggregate snapshot. Records new sample on each call."""
    temps = query_temps()
    if not temps:
        return {"ok": False,
                "reason": "nvidia-smi unreachable.",
                "gpus": []}
    hist_snapshot = load_history()
    for t in temps:
        record_sample(t["uuid"], t["name"], t["gpu_temp_c"], t["mem_temp_c"])
    hist_snapshot = load_history()
    gpus: list = []
    worst_verdict = "ok"
    rank = {"ok": 0, "warming": 0, "improving": 0,
            "pad_degraded": 1, "urgent": 2}
    for t in temps:
        entry = hist_snapshot.get(t["uuid"], {})
        drift = compute_drift(entry.get("samples", []))
        verdict = classify(drift)
        if rank.get(verdict["verdict"], 0) > rank.get(worst_verdict, 0):
            worst_verdict = verdict["verdict"]
        gpus.append({
            "uuid": t["uuid"],
            "name": t["name"],
            "gpu_temp_c": t["gpu_temp_c"],
            "mem_temp_c": t["mem_temp_c"],
            "delta_now": ((t["mem_temp_c"] - t["gpu_temp_c"])
                          if (t["mem_temp_c"] is not None and
                              t["gpu_temp_c"] is not None) else None),
            "drift": drift,
            "verdict": verdict,
        })
    return {
        "ok": True,
        "gpus": gpus,
        "gpu_count": len(gpus),
        "summary_verdict": worst_verdict,
    }
