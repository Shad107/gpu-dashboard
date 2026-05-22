"""Module throttle_cause — Thermal/power throttle root-cause classifier (R&D #19.2).

"Why is my GPU slow?" is the single biggest gaming/LLM support question.
NVIDIA exposes a throttle reason bitmask per GPU. This module reads it,
correlates with current clocks / temps / power-limit headroom, and
returns a one-line verdict :

  - applications_clocks_setting   (user-set fixed clocks)
  - sw_power_cap                  (power limit pulled clocks down)
  - hw_slowdown / hw_thermal_slowdown (catastrophic — driver pulled
                                       clocks to protect silicon)
  - hw_power_brake_slowdown       (PSU rail too weak)
  - sync_boost                    (multi-GPU sync)
  - sw_thermal_slowdown           (driver thermal throttle, less severe)

Cross-references current temp + GPU clock vs base clock to give a
specific recommendation (e.g. "increase fan curve", "raise power limit",
"check PSU").

stdlib only (subprocess + nvidia-smi).
"""
from __future__ import annotations

import shutil
import subprocess
from typing import Optional


NAME = "throttle_cause"


# nvidia-smi --query-gpu values, in the order we want to display them.
# Each maps to a (severity, human label, recommendation).
THROTTLE_REASONS = [
    ("clocks_throttle_reasons.hw_thermal_slowdown",
     "critical", "HW thermal slowdown (>95°C)",
     "GPU is at thermal HW limit. Clean dust, repaste, check airflow NOW."),
    ("clocks_throttle_reasons.hw_power_brake_slowdown",
     "critical", "HW power brake (PSU sag)",
     "PSU rail dropped — undersized or failing PSU. Check 12V rail."),
    ("clocks_throttle_reasons.hw_slowdown",
     "critical", "HW slowdown (driver emergency)",
     "Catastrophic driver-level slowdown. Inspect dmesg + XID."),
    ("clocks_throttle_reasons.sw_thermal_slowdown",
     "warn", "SW thermal slowdown",
     "Driver thermal throttling. Increase fan curve or lower TDP target."),
    ("clocks_throttle_reasons.sw_power_cap",
     "info", "SW power cap",
     "Power limit reached — clocks pulled down to honor it. "
     "Raise power limit if you want max perf."),
    ("clocks_throttle_reasons.applications_clocks_setting",
     "info", "User application clock setting",
     "Clocks are locked to an explicit nvidia-smi --applications-clocks value."),
    ("clocks_throttle_reasons.sync_boost",
     "info", "Sync boost",
     "Multi-GPU sync — slowest GPU dragging the rest down."),
    ("clocks_throttle_reasons.gpu_idle",
     "info", "GPU idle",
     "Card is idle — clocks dropped to save power. Nothing to fix."),
]


def _nvidia_smi_query(fields: list[str], timeout: float = 2.0) -> Optional[list[dict]]:
    """Run nvidia-smi --query-gpu=<fields> --format=csv,noheader,nounits."""
    if not shutil.which("nvidia-smi"):
        return None
    try:
        r = subprocess.run(
            ["nvidia-smi", f"--query-gpu={','.join(fields)}",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=timeout,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if r.returncode != 0 or not r.stdout.strip():
        return None
    rows: list[dict] = []
    for line in r.stdout.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < len(fields):
            continue
        rows.append(dict(zip(fields, parts)))
    return rows


def _parse_active(value: str) -> bool:
    """'Active', '1' → True; 'Not Active', '0' → False."""
    if not value:
        return False
    v = value.strip().lower()
    return v in ("active", "1", "true", "yes")


def classify_row(row: dict) -> dict:
    """Given one row of throttle / clock / temp data, return a verdict
    dict {severity, reason, recommendation, active_flags}."""
    active_flags: list[str] = []
    severity = "info"
    reason = "no throttle active"
    recommendation = ""
    for field, sev, label, rec in THROTTLE_REASONS:
        if _parse_active(row.get(field, "")):
            active_flags.append(label)
            # Keep the most severe first match for the headline verdict
            if reason == "no throttle active":
                severity = sev
                reason = label
                recommendation = rec
            else:
                # Promote severity if a harder reason is also active
                rank = {"info": 0, "warn": 1, "critical": 2}
                if rank.get(sev, 0) > rank.get(severity, 0):
                    severity = sev
                    reason = label
                    recommendation = rec
    return {
        "severity": severity,
        "reason": reason,
        "recommendation": recommendation,
        "active_flags": active_flags,
    }


def status(cfg=None) -> dict:
    """Aggregate snapshot per GPU."""
    fields = [
        "index", "name",
        "temperature.gpu", "clocks.current.graphics", "clocks.max.graphics",
        "power.draw", "power.limit",
    ] + [f for f, _, _, _ in THROTTLE_REASONS]
    rows = _nvidia_smi_query(fields)
    if rows is None:
        return {
            "ok": False,
            "reason": "nvidia-smi unreachable",
            "gpus": [],
        }
    gpus: list = []
    for r in rows:
        v = classify_row(r)
        gpus.append({
            "index": int(r.get("index", "0")) if r.get("index", "").isdigit() else 0,
            "name": r.get("name", "?"),
            "temp_c": _to_float(r.get("temperature.gpu")),
            "clock_mhz": _to_int(r.get("clocks.current.graphics")),
            "clock_max_mhz": _to_int(r.get("clocks.max.graphics")),
            "power_w": _to_float(r.get("power.draw")),
            "power_limit_w": _to_float(r.get("power.limit")),
            "verdict": v,
        })
    return {
        "ok": True,
        "gpus": gpus,
        "any_throttling": any(g["verdict"]["severity"] != "info" or
                               g["verdict"]["reason"] != "no throttle active"
                               for g in gpus),
    }


def _to_float(s: Optional[str]) -> Optional[float]:
    if s is None:
        return None
    s = s.strip()
    if not s or s.lower() in ("n/a", "[n/a]", "not supported"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _to_int(s: Optional[str]) -> Optional[int]:
    f = _to_float(s)
    return int(f) if f is not None else None
