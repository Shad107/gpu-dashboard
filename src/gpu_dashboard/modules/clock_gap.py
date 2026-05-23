"""Module clock_gap — applied-vs-enforced clock gap detector (R&D #27.7).

`nvidia-smi --applications-clocks=<mem>,<gr>` is meant to lock the
GPU at a specific clock. It often *appears* to work but the device
actually runs slower because another throttle reason takes over —
power cap, thermal, HW slowdown, sync boost. Users misread this as
"-ac doesn't stick" and waste hours.

This module reads :

  clocks.applications.gr   (what user asked for ; 0 if not set)
  clocks.current.gr        (what's actually enforced this second)
  clocks.max.gr            (HW ceiling)

then queries the throttle bitmask to label the *binding constraint*.

Per-GPU verdict :
  - no_apps_clock      (user never used --applications-clocks)
  - applied            (within ±5 MHz of asked clock)
  - capped_by_power    (sw_power_cap is binding)
  - capped_by_thermal  (sw/hw thermal slowdown is binding)
  - capped_by_hw       (hw_slowdown / hw_power_brake / sync_boost)
  - throttled_unknown  (gap > 5 MHz but no throttle bit set ; quirk)

Surgical follow-up to the shipped throttle-bits decoder (#25.5) :
that one tells you which bits are active, this one ties them to
the user's *intended* clock to explain why -ac didn't stick.

stdlib only.
"""
from __future__ import annotations

import shutil
import subprocess
from typing import Optional


NAME = "clock_gap"


_FIELDS = [
    "index", "name",
    "clocks.applications.gr",
    "clocks.current.graphics",
    "clocks.max.graphics",
    "clocks_throttle_reasons.sw_power_cap",
    "clocks_throttle_reasons.sw_thermal_slowdown",
    "clocks_throttle_reasons.hw_thermal_slowdown",
    "clocks_throttle_reasons.hw_power_brake_slowdown",
    "clocks_throttle_reasons.hw_slowdown",
    "clocks_throttle_reasons.sync_boost",
    "clocks_throttle_reasons.applications_clocks_setting",
]


def _query_gpu(timeout: float = 2.0) -> Optional[list[dict]]:
    if not shutil.which("nvidia-smi"):
        return None
    try:
        r = subprocess.run(
            ["nvidia-smi", f"--query-gpu={','.join(_FIELDS)}",
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
        if len(parts) < len(_FIELDS):
            continue
        out.append(dict(zip(_FIELDS, parts)))
    return out


def _to_int(s: str) -> Optional[int]:
    if not s:
        return None
    s = s.strip()
    if s.lower() in ("n/a", "[n/a]", "not supported", ""):
        return None
    try:
        return int(float(s))
    except ValueError:
        return None


def _is_active(s: str) -> bool:
    return s.strip().lower() in ("active", "1", "true", "yes")


def classify(row: dict) -> dict:
    """Returns per-GPU verdict."""
    app_clk = _to_int(row.get("clocks.applications.gr", ""))
    cur_clk = _to_int(row.get("clocks.current.graphics", ""))
    max_clk = _to_int(row.get("clocks.max.graphics", ""))
    if app_clk is None or app_clk == 0:
        return {"verdict": "no_apps_clock",
                "reason": ("No --applications-clocks setting active. "
                           "Driver picks clocks dynamically."),
                "gap_mhz": None,
                "binding": None,
                "current_clk": cur_clk,
                "applied_clk": app_clk,
                "max_clk": max_clk}
    if cur_clk is None:
        return {"verdict": "unknown",
                "reason": "Could not read current clock.",
                "gap_mhz": None,
                "binding": None,
                "current_clk": None,
                "applied_clk": app_clk,
                "max_clk": max_clk}
    gap = app_clk - cur_clk
    if abs(gap) <= 5:
        return {"verdict": "applied",
                "reason": (f"Current clock {cur_clk} MHz matches the "
                           f"--applications-clocks setting ({app_clk} MHz)."),
                "gap_mhz": gap,
                "binding": "applications_clocks_setting",
                "current_clk": cur_clk,
                "applied_clk": app_clk,
                "max_clk": max_clk}
    # Gap > 5 MHz — find binding constraint from throttle bits
    binding = _binding_throttle(row)
    if binding == "sw_power_cap":
        return {"verdict": "capped_by_power",
                "reason": (f"Asked {app_clk} MHz, running {cur_clk} MHz "
                           f"({gap:+} MHz). Power limit is binding."),
                "gap_mhz": gap,
                "binding": binding,
                "current_clk": cur_clk,
                "applied_clk": app_clk,
                "max_clk": max_clk}
    if binding in ("sw_thermal_slowdown", "hw_thermal_slowdown"):
        return {"verdict": "capped_by_thermal",
                "reason": (f"Asked {app_clk} MHz, running {cur_clk} MHz "
                           f"({gap:+} MHz). Thermal limit is binding."),
                "gap_mhz": gap,
                "binding": binding,
                "current_clk": cur_clk,
                "applied_clk": app_clk,
                "max_clk": max_clk}
    if binding in ("hw_slowdown", "hw_power_brake_slowdown", "sync_boost"):
        return {"verdict": "capped_by_hw",
                "reason": (f"Asked {app_clk} MHz, running {cur_clk} MHz "
                           f"({gap:+} MHz). HW slowdown ({binding}) "
                           "overrides the user-set clock."),
                "gap_mhz": gap,
                "binding": binding,
                "current_clk": cur_clk,
                "applied_clk": app_clk,
                "max_clk": max_clk}
    return {"verdict": "throttled_unknown",
            "reason": (f"Asked {app_clk} MHz, running {cur_clk} MHz "
                       f"({gap:+} MHz) but no throttle bit is active. "
                       "Could be driver bug or a hidden ASPM downshift."),
            "gap_mhz": gap,
            "binding": None,
            "current_clk": cur_clk,
            "applied_clk": app_clk,
            "max_clk": max_clk}


def _binding_throttle(row: dict) -> Optional[str]:
    """Return the name of the first active throttle reason, in priority
    order : HW > SW thermal > SW power > sync."""
    priority = [
        "hw_thermal_slowdown",
        "hw_power_brake_slowdown",
        "hw_slowdown",
        "sw_thermal_slowdown",
        "sw_power_cap",
        "sync_boost",
    ]
    for bit in priority:
        key = f"clocks_throttle_reasons.{bit}"
        if _is_active(row.get(key, "")):
            return bit
    return None


def status(cfg=None) -> dict:
    """Aggregate snapshot."""
    rows = _query_gpu()
    if rows is None:
        return {"ok": False,
                "reason": "nvidia-smi unreachable.",
                "gpus": []}
    gpus: list = []
    any_capped = False
    for r in rows:
        verdict = classify(r)
        if verdict["verdict"].startswith("capped_"):
            any_capped = True
        gpus.append({
            "index": _to_int(r.get("index", "0")) or 0,
            "name": r.get("name", "?"),
            **verdict,
        })
    return {
        "ok": True,
        "gpus": gpus,
        "any_capped": any_capped,
    }
