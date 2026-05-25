"""Module hwp_dynamic_boost_audit — Intel HWP dynamic_boost
posture (R&D #104.1).

Intel Skylake+ CPUs with intel_pstate in active mode + HWP
expose a separate 'burst responsiveness' toggle:

  /sys/devices/system/cpu/intel_pstate/hwp_dynamic_boost
    0 = no burst bias
    1 = boost ramp-up bias for short, latency-sensitive tasks
    (helps gaming / Wayland compositor / input handling)

The existing cpu_boost module reads only `no_turbo` ; hwp_epp
covers Energy-Perf-Preference ; cpu_cppc_audit covers ACPI
CPPC. None touch hwp_dynamic_boost.

Reads :

  /sys/devices/system/cpu/intel_pstate/hwp_dynamic_boost
  /sys/devices/system/cpu/intel_pstate/status      (driver mode)
  /sys/devices/system/cpu/cpu0/cpufreq/energy_performance_preference

Verdicts (worst-first) :

  hwp_boost_off_on_desktop      warn    HWP active, boost=0 —
                                        interactive workloads
                                        lose 5-15 % short-burst
                                        headroom.
  hwp_boost_fights_epp          accent  boost=1 but EPP set
                                        to power (>= 192) —
                                        the two knobs disagree.
  ok                                    boost=1 or HWP not in
                                        use.
  requires_root                         hwp_dynamic_boost
                                        unreadable.
  unknown                               intel_pstate driver
                                        absent / HWP disabled.

stdlib only.
"""
from __future__ import annotations

import os
from typing import Optional

NAME = "hwp_dynamic_boost_audit"

DEFAULT_PSTATE = "/sys/devices/system/cpu/intel_pstate"
DEFAULT_EPP = (
    "/sys/devices/system/cpu/cpu0/cpufreq/"
    "energy_performance_preference")

_EPP_POWER_THRESHOLD = "power"


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except (OSError, PermissionError):
        return None


def _read_str(path: str) -> Optional[str]:
    t = _read_text(path)
    return t.strip() if t is not None else None


def _read_int(path: str) -> Optional[int]:
    t = _read_text(path)
    if t is None:
        return None
    try:
        return int(t.strip())
    except ValueError:
        return None


def classify(driver_present: bool,
             status: Optional[str],
             boost: Optional[int],
             epp: Optional[str]) -> dict:
    if not driver_present:
        return {"verdict": "unknown",
                "reason": (
                    "intel_pstate driver absent — non-Intel "
                    "CPU, virtualised host, or kernel "
                    "without CONFIG_X86_INTEL_PSTATE.")}
    # If status is readable but not active → HWP not actually
    # in use ; boost is irrelevant
    if status is not None and status != "active":
        return {"verdict": "unknown",
                "reason": (
                    f"intel_pstate status={status} — HWP "
                    "is not active, hwp_dynamic_boost has "
                    "no effect.")}
    if boost is None:
        return {"verdict": "requires_root",
                "reason": (
                    "hwp_dynamic_boost unreadable — "
                    "re-run as root.")}

    # warn — boost off on desktop
    if boost == 0:
        return {
            "verdict": "hwp_boost_off_on_desktop",
            "reason": (
                "hwp_dynamic_boost=0 with HWP active. "
                "Interactive bursts (input, compositor, "
                "gaming) lose 5-15 % responsiveness ; "
                "echo 1 to enable.")}

    # accent — boost on AND EPP biased to power
    if boost == 1 and epp and "power" in epp:
        return {
            "verdict": "hwp_boost_fights_epp",
            "reason": (
                f"hwp_dynamic_boost=1 but EPP={epp} — "
                "knobs disagree. EPP-power tells HWP to "
                "save power ; dynamic_boost asks it to "
                "ramp up. Pick one.")}

    return {"verdict": "ok",
            "reason": (
                f"hwp_dynamic_boost={boost} ; "
                f"EPP={epp} ; status={status}. Sane.")}


def status(config: Optional[dict] = None,
           pstate: str = DEFAULT_PSTATE,
           epp_path: str = DEFAULT_EPP) -> dict:
    driver_present = os.path.isdir(pstate)
    pstate_status = (
        _read_str(os.path.join(pstate, "status"))
        if driver_present else None)
    boost = (
        _read_int(os.path.join(pstate, "hwp_dynamic_boost"))
        if driver_present else None)
    epp = _read_str(epp_path)
    verdict = classify(driver_present, pstate_status,
                       boost, epp)
    return {
        "ok": verdict["verdict"] == "ok",
        "intel_pstate_status": pstate_status,
        "hwp_dynamic_boost": boost,
        "epp": epp,
        "verdict": verdict,
    }
