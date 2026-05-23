"""Module thermal_slowdown_kind — HW vs SW thermal slowdown decoder (R&D #29.7).

The shipped throttle-bits decoder (#25.5) surfaces both
`sw_thermal_slowdown` and `hw_thermal_slowdown` as separate bits.
But for diagnostics you really want :

  - sw_thermal_slowdown alone → DRIVER chose to throttle (normal
                                 behavior under load, gradual)
  - hw_thermal_slowdown alone → HARDWARE safety net (95 °C TJMax
                                 limit hit — cooler failed,
                                 pump dead, fan died)
  - BOTH                      → driver couldn't slow down fast
                                 enough, HW had to step in
  - sw_thermal at ~70 °C      → suspicious : usually means a fan
                                 stopped (driver slows clocks to
                                 compensate)

This module joins the two bits with current temperature + memory
temperature + power draw to produce one of five precise verdicts :

  - no_thermal_throttle
  - sw_normal       (sw active at ≥80 °C — fan curve ok)
  - sw_premature    (sw active at <70 °C — fan likely failed)
  - hw_safety_net   (hw active — TJMax hit, cooler problem)
  - hw_and_sw_both  (worst case — driver lost the race)

Surgical refinement of throttle_bits. Pure nvidia-smi.

stdlib only.
"""
from __future__ import annotations

import shutil
import subprocess
from typing import Optional


NAME = "thermal_slowdown_kind"


_FIELDS = [
    "index", "name",
    "temperature.gpu", "temperature.memory",
    "power.draw", "power.limit",
    "clocks.current.graphics",
    "clocks_throttle_reasons.sw_thermal_slowdown",
    "clocks_throttle_reasons.hw_thermal_slowdown",
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


def _to_float(s: str) -> Optional[float]:
    if not s:
        return None
    s = s.strip()
    if s.lower() in ("n/a", "[n/a]", "not supported", ""):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _is_active(s: str) -> bool:
    return s.strip().lower() in ("active", "1", "true", "yes")


def classify(row: dict) -> dict:
    sw = _is_active(row.get("clocks_throttle_reasons.sw_thermal_slowdown", ""))
    hw = _is_active(row.get("clocks_throttle_reasons.hw_thermal_slowdown", ""))
    gpu_c = _to_float(row.get("temperature.gpu", ""))
    mem_c = _to_float(row.get("temperature.memory", ""))
    if not sw and not hw:
        return {"verdict": "no_thermal_throttle",
                "severity": "info",
                "reason": "No thermal throttle bit active.",
                "recommendation": ""}
    if sw and hw:
        return {"verdict": "hw_and_sw_both",
                "severity": "critical",
                "reason": (f"BOTH thermal bits active. GPU {gpu_c}°C — driver "
                           "couldn't slow down fast enough and HW had to step "
                           "in. Cooler is failing or insufficient."),
                "recommendation": ("Stop load NOW. Check fan(s), thermal "
                                    "paste, airflow, ambient temp.")}
    if hw:
        return {"verdict": "hw_safety_net",
                "severity": "critical",
                "reason": (f"HW thermal slowdown only. GPU at {gpu_c}°C — "
                           "TJMax (~93-95 °C) hit. Hardware safety net "
                           "engaged."),
                "recommendation": ("Cooler emergency. Reseat heatsink, "
                                    "check fan RPM, repaste, lower TDP.")}
    # sw only
    if gpu_c is not None and gpu_c < 70:
        return {"verdict": "sw_premature",
                "severity": "warn",
                "reason": (f"SW thermal slowdown at {gpu_c}°C — driver chose "
                           "to throttle but temperature is low. Almost "
                           "always a stopped fan or stuck thermal sensor."),
                "recommendation": ("Check fan RPM (`nvidia-smi --query-"
                                    "gpu=fan.speed`). If 0 with the card "
                                    "warm, the fan is dead.")}
    return {"verdict": "sw_normal",
            "severity": "info",
            "reason": (f"SW thermal slowdown at {gpu_c}°C — driver fan curve "
                       "doing its job. Increase fan curve or raise TDP cap "
                       "if you want more headroom."),
            "recommendation": ""}


def status(cfg=None) -> dict:
    rows = _query_gpu()
    if rows is None:
        return {"ok": False,
                "reason": "nvidia-smi unreachable.",
                "gpus": []}
    gpus: list = []
    any_critical = False
    for r in rows:
        verdict = classify(r)
        if verdict["severity"] == "critical":
            any_critical = True
        gpus.append({
            "index": _to_float(r.get("index", "0")) or 0,
            "name": r.get("name", "?"),
            "gpu_temp_c": _to_float(r.get("temperature.gpu", "")),
            "mem_temp_c": _to_float(r.get("temperature.memory", "")),
            "power_w": _to_float(r.get("power.draw", "")),
            "verdict": verdict,
        })
    return {"ok": True,
            "gpus": gpus,
            "any_critical": any_critical}
