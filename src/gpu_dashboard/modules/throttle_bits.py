"""Module throttle_bits — Per-bit throttle reason decoder (R&D #25.5).

Surgical upgrade over the shipped throttle classifier (#19.2). Where
#19.2 returns a single headline verdict, this module exposes every
individual throttle-reason bit nvidia-smi exposes, each with its own
severity tag. UI users can then see at a glance whether the throttle
is benign (display_clock_setting) or catastrophic
(hw_thermal_slowdown) — without losing the headline rollup.

Eight bits queried (NVML names, mapped to friendly labels) :

  gpu_idle                            info        — idle, expected
  applications_clocks_setting         info        — user locked
  sw_power_cap                        info        — power limit reached
  hw_slowdown                         critical    — driver emergency
  sync_boost                          info        — multi-GPU sync
  sw_thermal_slowdown                 warn        — driver thermal
  hw_thermal_slowdown                 critical    — 95 °C HW limit
  hw_power_brake_slowdown             critical    — PSU sag
  display_clock_setting               info        — display-locked

stdlib only.
"""
from __future__ import annotations

import shutil
import subprocess
from typing import Optional


NAME = "throttle_bits"


# Display order for the UI table. Each row :
#  (nvidia-smi field, friendly_label, severity, one-line meaning).
THROTTLE_BIT_TABLE = [
    ("clocks_throttle_reasons.gpu_idle",
     "GPU idle", "info",
     "Idle — clocks dropped to save power."),
    ("clocks_throttle_reasons.applications_clocks_setting",
     "Application clocks lock", "info",
     "User-set --applications-clocks holds the GPU at a fixed clock."),
    ("clocks_throttle_reasons.sw_power_cap",
     "SW power cap", "info",
     "Power limit reached. Raise the limit if you want more perf."),
    ("clocks_throttle_reasons.hw_slowdown",
     "HW slowdown", "critical",
     "Driver emergency. Check dmesg + XID."),
    ("clocks_throttle_reasons.sync_boost",
     "Sync boost", "info",
     "Multi-GPU sync — slowest GPU drags the rest down."),
    ("clocks_throttle_reasons.sw_thermal_slowdown",
     "SW thermal slowdown", "warn",
     "Driver thermal throttle. Fan curve / TDP."),
    ("clocks_throttle_reasons.hw_thermal_slowdown",
     "HW thermal slowdown", "critical",
     "95 °C HW limit. Clean, repaste, airflow."),
    ("clocks_throttle_reasons.hw_power_brake_slowdown",
     "HW power brake", "critical",
     "PSU 12 V rail sagged. Check / replace PSU."),
    ("clocks_throttle_reasons.display_clock_setting",
     "Display clock setting", "info",
     "Display kept GPU at higher clock for desktop comp."),
]


def _query_gpu(fields: list[str], timeout: float = 2.0) -> Optional[list[dict]]:
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
    if r.returncode != 0:
        return None
    out: list[dict] = []
    for line in r.stdout.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < len(fields):
            continue
        out.append(dict(zip(fields, parts)))
    return out


def _parse_active(value: str) -> bool:
    if not value:
        return False
    return value.strip().lower() in ("active", "1", "true", "yes")


def decode_bits(row: dict) -> list[dict]:
    """Decode one nvidia-smi row → list of bit dicts."""
    out: list[dict] = []
    for field, label, severity, meaning in THROTTLE_BIT_TABLE:
        active = _parse_active(row.get(field, ""))
        out.append({
            "field": field,
            "label": label,
            "severity": severity,
            "meaning": meaning,
            "active": active,
        })
    return out


def headline_verdict(bits: list[dict]) -> dict:
    """Pick the most severe active bit. None active → 'no_throttle'."""
    rank = {"info": 0, "warn": 1, "critical": 2}
    active = [b for b in bits if b["active"]]
    if not active:
        return {"verdict": "no_throttle",
                "severity": "info",
                "reason": "No throttle bits active."}
    worst = max(active, key=lambda b: rank.get(b["severity"], 0))
    return {"verdict": worst["label"],
            "severity": worst["severity"],
            "reason": worst["meaning"]}


def status(cfg=None) -> dict:
    """Aggregate snapshot, one row per GPU."""
    fields = ["index", "name"] + [f for f, _, _, _ in THROTTLE_BIT_TABLE]
    rows = _query_gpu(fields)
    if rows is None:
        return {"ok": False,
                "reason": "nvidia-smi unreachable.",
                "gpus": []}
    gpus: list = []
    any_critical = False
    for r in rows:
        bits = decode_bits(r)
        verdict = headline_verdict(bits)
        if verdict["severity"] == "critical":
            any_critical = True
        gpus.append({
            "index": int(r.get("index", "0")) if r.get("index", "").isdigit() else 0,
            "name": r.get("name", "?"),
            "bits": bits,
            "active_count": sum(1 for b in bits if b["active"]),
            "verdict": verdict,
        })
    return {
        "ok": True,
        "gpus": gpus,
        "any_critical": any_critical,
    }
