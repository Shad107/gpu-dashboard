"""Module numa_balancing_scan_tuning_audit — NUMA-balancing
scan-rate knobs (R&D #107.4, strong pick).

The existing numa_topology_audit reads only the boolean
/proc/sys/kernel/numa_balancing toggle. The kernel exposes
four additional knobs that govern *how aggressively* it
walks PTE faults to migrate hot pages:

  numa_balancing_scan_delay_ms        first-scan delay
  numa_balancing_scan_period_min_ms   minimum period
  numa_balancing_scan_period_max_ms   maximum period
  numa_balancing_scan_size_mb         pages per scan tick

Mis-tuning hurts large-RSS LLM workloads: tiny period_min adds
fault-trap overhead ; huge period_max stalls promotion on
tiered / CXL setups ; small scan_size_mb wastes IPI churn on
big-VRAM hosts.

Reads :

  /proc/sys/kernel/numa_balancing                  (gate)
  /proc/sys/kernel/numa_balancing_scan_delay_ms
  /proc/sys/kernel/numa_balancing_scan_period_min_ms
  /proc/sys/kernel/numa_balancing_scan_period_max_ms
  /proc/sys/kernel/numa_balancing_scan_size_mb

Verdicts (worst-first) :

  aggressive_scan          warn    period_min < 500 ms — fault-
                                   trap overhead dominates on
                                   large RSS.
  lethargic_scan           warn    period_max > 60_000 ms —
                                   promotion stalls for tiered /
                                   CXL workloads.
  tiny_scan_chunk          accent  scan_size_mb < 64 — wasted
                                   IPI churn on big-VRAM hosts.
  drifted_from_defaults    accent  >= 2 of 4 knobs differ from
                                   kernel defaults.
  ok                               defaults intact OR balancing
                                   disabled.
  requires_root                    knobs unreadable.
  unknown                          knobs absent (NUMA-disabled
                                   kernel) or balancing=0.

stdlib only.
"""
from __future__ import annotations

import os
from typing import Optional

NAME = "numa_balancing_scan_tuning_audit"

DEFAULT_SYSCTL = "/proc/sys/kernel"

# Kernel defaults (since v5.x)
_DEFAULTS = {
    "scan_delay_ms": 1000,
    "scan_period_min_ms": 1000,
    "scan_period_max_ms": 60000,
    "scan_size_mb": 256,
}


def _read_int(path: str) -> Optional[int]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return int(fh.read().strip())
    except (OSError, PermissionError, ValueError):
        return None


def classify(balancing: Optional[int],
             knobs: dict) -> dict:
    if balancing is None or balancing == 0:
        return {"verdict": "unknown",
                "reason": (
                    "NUMA balancing disabled or knob absent "
                    "— scan tuning not applicable.")}

    if all(v is None for v in knobs.values()):
        return {"verdict": "requires_root",
                "reason": (
                    "scan tuning knobs unreadable — re-run "
                    "as root.")}

    period_min = knobs.get("scan_period_min_ms")
    period_max = knobs.get("scan_period_max_ms")
    scan_size = knobs.get("scan_size_mb")

    # warn — period_min too small
    if period_min is not None and period_min < 500:
        return {
            "verdict": "aggressive_scan",
            "reason": (
                f"numa_balancing_scan_period_min_ms="
                f"{period_min} (< 500). Fault-trap overhead "
                "dominates on large-RSS workloads.")}

    # warn — period_max too large
    if period_max is not None and period_max > 60_000:
        return {
            "verdict": "lethargic_scan",
            "reason": (
                f"numa_balancing_scan_period_max_ms="
                f"{period_max} (> 60 000). Promotion stalls "
                "for tiered / CXL memory.")}

    # accent — scan_size_mb too small
    if scan_size is not None and scan_size < 64:
        return {
            "verdict": "tiny_scan_chunk",
            "reason": (
                f"numa_balancing_scan_size_mb={scan_size} "
                "(< 64). IPI churn dominates on big-VRAM "
                "hosts ; bump for fewer ticks.")}

    # accent — >= 2 knobs drifted from defaults
    drift_count = 0
    drift_names: list = []
    for k, default_v in _DEFAULTS.items():
        v = knobs.get(k)
        if v is not None and v != default_v:
            drift_count += 1
            drift_names.append(f"{k}={v}")
    if drift_count >= 2:
        return {
            "verdict": "drifted_from_defaults",
            "reason": (
                f"{drift_count} of 4 scan knobs differ from "
                f"defaults: {drift_names}. Verify tuning "
                "was intentional.")}

    return {"verdict": "ok",
            "reason": (
                f"NUMA balancing on ; scan knobs at defaults "
                "or single-knob deviation.")}


def status(config: Optional[dict] = None,
           sysctl: str = DEFAULT_SYSCTL) -> dict:
    balancing = _read_int(
        os.path.join(sysctl, "numa_balancing"))
    knobs = {
        "scan_delay_ms": _read_int(
            os.path.join(
                sysctl, "numa_balancing_scan_delay_ms")),
        "scan_period_min_ms": _read_int(
            os.path.join(
                sysctl,
                "numa_balancing_scan_period_min_ms")),
        "scan_period_max_ms": _read_int(
            os.path.join(
                sysctl,
                "numa_balancing_scan_period_max_ms")),
        "scan_size_mb": _read_int(
            os.path.join(
                sysctl, "numa_balancing_scan_size_mb")),
    }
    verdict = classify(balancing, knobs)
    return {
        "ok": verdict["verdict"] == "ok",
        "numa_balancing": balancing,
        **knobs,
        "verdict": verdict,
    }
