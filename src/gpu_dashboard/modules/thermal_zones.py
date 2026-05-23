"""Module thermal_zones — system thermal-zone correlator (R&D #28.5).

Linux exposes every system thermal sensor under
`/sys/class/thermal/thermal_zone*`. NVMe controllers, CPU package,
PCH, RAM DIMMs, even chassis fans all show up. None of this is in
NVIDIA telemetry — but when an NVMe under the GPU spikes to 78 °C,
that's the *real* reason throttle bits fire.

This module reads every thermal zone, classifies each as
cool/warm/hot/critical based on widely-accepted silicon thresholds,
and cross-references with the shipped throttle classifier (#19.2)
to produce *airflow* advice instead of generic "GPU is hot".

stdlib only.
"""
from __future__ import annotations

import os
from typing import Optional


NAME = "thermal_zones"


_THERMAL_ROOT = "/sys/class/thermal"


# Thresholds in millicelsius. Sourced from typical silicon datasheets :
#  - NVMe / SSD : Tj_max ≈ 70 °C, throttle ≈ 80 °C
#  - CPU         : Tj_max ≈ 95-100 °C, but anything > 85 sustained is hot
#  - DIMM        : 85 °C is critical
THRESHOLDS_MC = {
    "cool":     60_000,
    "warm":     75_000,
    "hot":      85_000,
    # > 85 °C → critical
}


def list_zones(root: str = _THERMAL_ROOT) -> list[str]:
    out: list[str] = []
    try:
        for name in sorted(os.listdir(root)):
            if name.startswith("thermal_zone") and name[len("thermal_zone"):].isdigit():
                out.append(os.path.join(root, name))
    except OSError:
        return []
    return out


def read_zone(zone_path: str) -> Optional[dict]:
    """Read type + temp for one zone. Returns None on failure."""
    try:
        with open(os.path.join(zone_path, "type")) as f:
            zone_type = f.read().strip()
        with open(os.path.join(zone_path, "temp")) as f:
            temp_mc = int(f.read().strip())
    except (OSError, ValueError):
        return None
    return {
        "name": os.path.basename(zone_path),
        "type": zone_type,
        "temp_mc": temp_mc,
        "temp_c": round(temp_mc / 1000.0, 1),
    }


def classify_zone(temp_mc: int) -> str:
    if temp_mc < THRESHOLDS_MC["cool"]:
        return "cool"
    if temp_mc < THRESHOLDS_MC["warm"]:
        return "warm"
    if temp_mc < THRESHOLDS_MC["hot"]:
        return "hot"
    return "critical"


def is_storage_zone(zone_type: str) -> bool:
    """Heuristic : zone type contains nvme / ssd."""
    low = zone_type.lower()
    return "nvme" in low or "ssd" in low or "composite" in low


def is_cpu_zone(zone_type: str) -> bool:
    low = zone_type.lower()
    return (low.startswith("x86_pkg")
            or "coretemp" in low
            or low.startswith("k10temp")
            or low == "cpu_thermal")


def cross_correlate(zones: list[dict],
                     gpu_throttled: bool) -> list[str]:
    """Generate human-readable advice based on hot non-GPU zones AND
    GPU thermal throttle state."""
    out: list[str] = []
    if not gpu_throttled:
        return out
    for z in zones:
        if z["category"] not in ("hot", "critical"):
            continue
        if is_storage_zone(z["type"]):
            out.append(
                f"NVMe/SSD '{z['type']}' is {z['temp_c']} °C — likely under "
                "or next to the GPU. Pre-heating the GPU intake. Move it or "
                "add a small fan over the M.2 slot."
            )
        elif is_cpu_zone(z["type"]):
            out.append(
                f"CPU '{z['type']}' is {z['temp_c']} °C — competing for "
                "case airflow. Check chassis fan curves."
            )
        else:
            out.append(
                f"Thermal zone '{z['type']}' is {z['temp_c']} °C "
                "(category : {z['category']}). Inspect airflow path."
            )
    return out


def status(cfg=None) -> dict:
    """Aggregate snapshot. gpu_thermal_throttle is best-effort pulled
    from the shipped throttle module."""
    zone_paths = list_zones()
    zones: list = []
    for p in zone_paths:
        z = read_zone(p)
        if z is None:
            continue
        z["category"] = classify_zone(z["temp_mc"])
        zones.append(z)
    if not zones:
        return {"ok": True,
                "zone_count": 0,
                "zones": [],
                "advice": [],
                "summary": ("No thermal zones exposed at "
                             "/sys/class/thermal. Likely a VM "
                             "or minimal kernel build.")}
    # Best-effort GPU throttle correlation (no hard dep)
    gpu_throttled = False
    try:
        from . import throttle_bits
        tb = throttle_bits.status(cfg)
        gpu_throttled = bool(tb and tb.get("any_critical"))
    except Exception:
        pass
    advice = cross_correlate(zones, gpu_throttled)
    cats = {"cool": 0, "warm": 0, "hot": 0, "critical": 0}
    for z in zones:
        cats[z["category"]] = cats.get(z["category"], 0) + 1
    return {
        "ok": True,
        "zone_count": len(zones),
        "zones": zones,
        "gpu_thermal_throttle": gpu_throttled,
        "category_counts": cats,
        "advice": advice,
        "summary": (f"{cats['hot'] + cats['critical']} zone(s) "
                     "hot/critical, "
                     f"{cats['warm']} warm, {cats['cool']} cool."),
    }
