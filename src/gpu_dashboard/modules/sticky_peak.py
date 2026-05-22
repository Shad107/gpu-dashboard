"""Sticky peak alerts — fire once when a lifetime extremum crosses a threshold.

Unlike the regular alert_monitor (which polls live metrics and alerts on each
hot-temp window), this module looks at the lifetime MAX recorded in storage
and emits exactly one alert per (gpu_index, metric, threshold) combination.

Use cases :
  - "Tell me if this GPU was EVER above 90°C — I'll know my cooling is undersized"
  - "Tell me if this card was EVER pulled more than 380W — I'll know my PSU was stressed"

The alert is sticky : once fired, won't fire again for the same threshold.
If the user raises the threshold, a new (higher) crossing can fire again.
If they lower the threshold and the lifetime peak still exceeds it, fires
once for the new threshold.
"""
from __future__ import annotations

import json
from typing import Optional


NAME = "sticky_peak"


def _peaks(storage, gpu_index: int) -> dict:
    """Return {peak_temp_c, peak_power_w} for a GPU, or empty dict if no samples."""
    try:
        cur = storage._conn.execute(
            "SELECT MAX(temp) AS peak_temp, MAX(power) AS peak_power "
            "FROM samples WHERE gpu_index = ?",
            (gpu_index,),
        )
        row = cur.fetchone()
        if row is None:
            return {}
        return {
            "peak_temp_c": row["peak_temp"],
            "peak_power_w": row["peak_power"],
        }
    except Exception:
        return {}


def _already_alerted(storage, gpu_index: int, metric: str, threshold: float) -> bool:
    """True if a sticky_peak alert with this exact (metric, threshold) already
    exists for this GPU."""
    try:
        cur = storage._conn.execute(
            "SELECT payload FROM events WHERE kind = 'sticky_peak'"
        )
        for r in cur.fetchall():
            try:
                p = json.loads(r["payload"]) if r["payload"] else {}
            except Exception:
                continue
            if (p.get("gpu_index") == gpu_index
                and p.get("metric") == metric
                and abs((p.get("threshold") or 0) - threshold) < 0.01):
                return True
    except Exception:
        pass
    return False


def check_and_alert(
    storage,
    *,
    gpu_index: int = 0,
    threshold_temp_c: float = 0,
    threshold_power_w: float = 0,
) -> list[dict]:
    """Inspect lifetime peaks ; emit sticky_peak events for each first crossing.

    Returns the list of newly-fired alerts (empty if none).

    A threshold of 0 (or negative) disables that metric.
    """
    if storage is None:
        return []
    fired: list[dict] = []
    peaks = _peaks(storage, gpu_index)

    if threshold_temp_c > 0 and peaks.get("peak_temp_c") is not None:
        observed = peaks["peak_temp_c"]
        if observed >= threshold_temp_c and not _already_alerted(
            storage, gpu_index, "temp", threshold_temp_c
        ):
            payload = {
                "gpu_index": gpu_index,
                "metric": "temp",
                "threshold": float(threshold_temp_c),
                "observed": float(observed),
            }
            try:
                storage.record_event("sticky_peak", payload)
                fired.append(payload)
            except Exception:
                pass

    if threshold_power_w > 0 and peaks.get("peak_power_w") is not None:
        observed = peaks["peak_power_w"]
        if observed >= threshold_power_w and not _already_alerted(
            storage, gpu_index, "power", threshold_power_w
        ):
            payload = {
                "gpu_index": gpu_index,
                "metric": "power",
                "threshold": float(threshold_power_w),
                "observed": float(observed),
            }
            try:
                storage.record_event("sticky_peak", payload)
                fired.append(payload)
            except Exception:
                pass

    return fired
