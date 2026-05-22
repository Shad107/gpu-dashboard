"""A/B profile benchmark runner.

Compares the GPU behaviour under two profiles by running each for a fixed
duration and aggregating samples taken during the window.

Typical caller orchestration (lives in api.py / a daemon, not here) :

    seg_a = run_segment(60, "silent",   apply_callback, sampler)
    seg_b = run_segment(60, "boost",    apply_callback, sampler)
    delta = compare(seg_a, seg_b)
    # delta tells the user whether B was faster, hotter, more expensive...

This module is pure helper code — no threading, no I/O, no scheduling. The
HTTP handler that wraps it owns the run lifecycle.
"""
from __future__ import annotations

import time as _time
from typing import Callable, Optional


NAME = "benchmark"


def run_segment(
    duration_s: float,
    profile_name: str,
    apply_callback: Callable[[str], object],
    sampler,
    price_per_kwh: float = 0.25,
    sleep: Optional[Callable[[float], None]] = None,
    now: Optional[Callable[[], float]] = None,
) -> dict:
    """Apply ``profile_name``, sleep ``duration_s``, then return aggregated stats.

    Sleep + now are resolved lazily at call time so tests can monkeypatch
    `_time.sleep` after import without losing the override to a default-arg
    snapshot.
    """
    if sleep is None:
        sleep = _time.sleep
    if now is None:
        now = _time.time
    start_ts = int(now())
    try:
        apply_callback(profile_name)
    except Exception as e:
        # Don't crash the segment — record the failure but still gather stats
        # from whatever the GPU is doing right now.
        apply_error = str(e)
    else:
        apply_error = None

    sleep(duration_s)
    end_ts = int(now())

    # Take everything the sampler has, then filter to our window.
    buf = sampler.snapshot() if hasattr(sampler, "snapshot") else []
    window = [s for s in buf
              if isinstance(s, dict)
              and s.get("ts_epoch") is not None
              and start_ts <= s["ts_epoch"] <= end_ts]
    if not window:
        # Fallback : last N samples (no ts_epoch field — older samplers)
        # Take roughly duration_s / sampler.interval tail samples.
        try:
            tail_count = max(2, int(duration_s // max(1, sampler.interval)))
        except Exception:
            tail_count = 12
        window = list(buf)[-tail_count:]

    powers = [s["power"] for s in window if s.get("power") is not None]
    temps = [s["temp"] for s in window if s.get("temp") is not None]
    utils = [s["util_gpu"] for s in window if s.get("util_gpu") is not None]
    tok = [s.get("tokens_total_snapshot") for s in window]
    tok = [t for t in tok if t is not None]

    avg_power = sum(powers) / len(powers) if powers else 0.0
    peak_power = max(powers) if powers else 0.0
    avg_temp = sum(temps) / len(temps) if temps else 0.0
    avg_util = sum(utils) / len(utils) if utils else 0.0

    tokens_delta = (tok[-1] - tok[0]) if len(tok) >= 2 and tok[-1] >= tok[0] else 0

    kwh = avg_power * duration_s / 3600.0 / 1000.0
    cost = kwh * price_per_kwh

    tokens_per_s = (tokens_delta / duration_s) if duration_s > 0 and tokens_delta > 0 else 0.0
    tokens_per_kwh = (tokens_delta / kwh) if kwh > 0 and tokens_delta > 0 else 0.0

    return {
        "profile": profile_name,
        "start_ts": start_ts,
        "end_ts": end_ts,
        "duration_s": int(duration_s),
        "samples_count": len(window),
        "apply_error": apply_error,
        "avg_power_w": round(avg_power, 1),
        "peak_power_w": round(peak_power, 1),
        "avg_temp_c": round(avg_temp, 1),
        "avg_util_gpu": round(avg_util, 1),
        "tokens_delta": tokens_delta,
        "tokens_per_s": round(tokens_per_s, 2),
        "tokens_per_kwh": round(tokens_per_kwh, 1),
        "kwh": round(kwh, 4),
        "cost": round(cost, 4),
    }


def compare(seg_a: dict, seg_b: dict) -> dict:
    """Compute (B - A) deltas + per-criterion winners.

    For each metric : positive delta = B is higher. Winners are named for
    common readability ("cooler", "more_efficient", etc.) :

      cooler        : the segment with the lower avg_temp_c
      lower_power   : the segment with the lower avg_power_w
      higher_throughput : the segment with the higher tokens_per_s
      more_efficient    : the segment with the higher tokens_per_kwh
      cheaper       : the segment with the lower cost
    """
    def _delta(field):
        a = seg_a.get(field) or 0
        b = seg_b.get(field) or 0
        return round(b - a, 4)

    def _winner(field, low_wins: bool):
        a = seg_a.get(field) or 0
        b = seg_b.get(field) or 0
        if a == b:
            return "tie"
        if low_wins:
            return seg_a["profile"] if a < b else seg_b["profile"]
        return seg_a["profile"] if a > b else seg_b["profile"]

    return {
        "profile_a": seg_a["profile"],
        "profile_b": seg_b["profile"],
        "delta": {
            "avg_power_w": _delta("avg_power_w"),
            "peak_power_w": _delta("peak_power_w"),
            "avg_temp_c": _delta("avg_temp_c"),
            "avg_util_gpu": _delta("avg_util_gpu"),
            "tokens_delta": _delta("tokens_delta"),
            "tokens_per_s": _delta("tokens_per_s"),
            "tokens_per_kwh": _delta("tokens_per_kwh"),
            "kwh": _delta("kwh"),
            "cost": _delta("cost"),
        },
        "winners": {
            "cooler": _winner("avg_temp_c", low_wins=True),
            "lower_power": _winner("avg_power_w", low_wins=True),
            "higher_throughput": _winner("tokens_per_s", low_wins=False),
            "more_efficient": _winner("tokens_per_kwh", low_wins=False),
            "cheaper": _winner("cost", low_wins=True),
        },
    }
