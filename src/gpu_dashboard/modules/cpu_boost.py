"""Module cpu_boost — CPU turbo/boost runtime toggle audit (R&D #35.1).

LLM prompt-processing on llama.cpp is heavily CPU-bound — the
attention-mask + KV-cache setup runs on host cores before each
generation. When CPU turbo / boost is disabled (BIOS toggle, or
`echo 0 > /sys/devices/system/cpu/cpufreq/boost` set by a tuning
script, or `intel_pstate/no_turbo=1`), the CPU caps at the
non-turbo base frequency — typically 30-40 % below max — and
prompt-processing tokens/s drops correspondingly.

Three control surfaces exist depending on the platform:

  /sys/devices/system/cpu/cpufreq/boost            generic (AMD CPB,
                                                    or acpi-cpufreq
                                                    fallback)
                                                    0=disabled, 1=enabled
  /sys/devices/system/cpu/intel_pstate/no_turbo    Intel pstate driver
                                                    0=turbo enabled
                                                    1=turbo disabled
  /sys/devices/system/cpu/intel_pstate/status      active/passive/off
  /sys/devices/system/cpu/amd_pstate/status        AMD pstate driver

Verdicts:
  boost_enabled    turbo / boost is active
  boost_disabled   turbo / boost is disabled — 30-40 % perf loss on
                   prompt processing
  passive          intel_pstate=passive (acpi-cpufreq active instead)
  missing          no boost subsystem (VM, kernel without
                   CONFIG_X86_INTEL_PSTATE)
  unknown          inconsistent sysfs

Recipe surfaces:
  - Runtime: `echo 1 > .../cpufreq/boost` (or `0 > no_turbo`)
  - Persistent: tuned-adm profile latency-performance, or a
    systemd unit ExecStartPost line, or sysfsutils

stdlib only.
"""
from __future__ import annotations

import os
from typing import Optional


NAME = "cpu_boost"


_CPU_ROOT = "/sys/devices/system/cpu"


def _read(p: str) -> Optional[str]:
    try:
        with open(p) as f:
            return f.read().strip()
    except OSError:
        return None


def _read_int(p: str) -> Optional[int]:
    s = _read(p)
    if s is None:
        return None
    try:
        return int(s)
    except ValueError:
        return None


def read_cpufreq_boost(root: str = _CPU_ROOT) -> Optional[int]:
    return _read_int(os.path.join(root, "cpufreq", "boost"))


def read_intel_no_turbo(root: str = _CPU_ROOT) -> Optional[int]:
    return _read_int(os.path.join(root, "intel_pstate", "no_turbo"))


def read_intel_status(root: str = _CPU_ROOT) -> Optional[str]:
    return _read(os.path.join(root, "intel_pstate", "status"))


def read_amd_status(root: str = _CPU_ROOT) -> Optional[str]:
    return _read(os.path.join(root, "amd_pstate", "status"))


def detect_mode(root: str = _CPU_ROOT) -> str:
    """Pick which subsystem to trust.

    Precedence: intel_pstate > amd_pstate > generic cpufreq > missing.
    """
    if os.path.isdir(os.path.join(root, "intel_pstate")):
        return "intel_pstate"
    if os.path.isdir(os.path.join(root, "amd_pstate")):
        return "amd_pstate"
    if os.path.exists(os.path.join(root, "cpufreq", "boost")):
        return "cpufreq_boost"
    return "missing"


_RECIPE_BOOST = (
    "# Runtime — enables boost immediately:\n"
    "echo 1 | sudo tee /sys/devices/system/cpu/cpufreq/boost\n"
    "# Persistent — drop a sysfsutils rule or a systemd ExecStartPost\n"
    "echo \"devices/system/cpu/cpufreq/boost = 1\" | sudo tee "
    "/etc/sysfs.d/99-boost.conf"
)

_RECIPE_NO_TURBO = (
    "# Runtime — enables Intel Turbo (no_turbo=0):\n"
    "echo 0 | sudo tee /sys/devices/system/cpu/intel_pstate/no_turbo\n"
    "# If you have tuned-adm: `sudo tuned-adm profile latency-performance`"
)


def classify(mode: str, boost: Optional[int], no_turbo: Optional[int],
              intel_status: Optional[str],
              amd_status: Optional[str]) -> dict:
    if mode == "missing":
        return {"verdict": "missing",
                "reason": ("No /sys/devices/system/cpu/{cpufreq,intel_pstate,"
                           "amd_pstate} present — typical for VMs (the "
                           "hypervisor controls CPU frequency)."),
                "recommendation": ""}
    if mode == "intel_pstate":
        if intel_status and intel_status.strip().lower() == "passive":
            # acpi-cpufreq is active instead; fall back to cpufreq/boost
            if boost == 1:
                return {"verdict": "boost_enabled",
                        "reason": ("intel_pstate=passive, cpufreq/boost=1 — "
                                   "turbo via the generic driver."),
                        "recommendation": ""}
            if boost == 0:
                return {"verdict": "boost_disabled",
                        "reason": ("intel_pstate=passive AND cpufreq/boost=0 — "
                                   "turbo is OFF, prompt-processing capped "
                                   "at base freq."),
                        "recommendation": _RECIPE_BOOST}
            return {"verdict": "passive",
                    "reason": ("intel_pstate=passive but no cpufreq/boost "
                               "to confirm state."),
                    "recommendation": ""}
        if no_turbo == 0:
            return {"verdict": "boost_enabled",
                    "reason": "intel_pstate active, no_turbo=0 — turbo ON.",
                    "recommendation": ""}
        if no_turbo == 1:
            return {"verdict": "boost_disabled",
                    "reason": ("intel_pstate active, no_turbo=1 — Intel Turbo "
                               "is OFF, prompt-processing capped 30-40 % "
                               "below max."),
                    "recommendation": _RECIPE_NO_TURBO}
        return {"verdict": "unknown",
                "reason": "intel_pstate present but no_turbo unreadable.",
                "recommendation": ""}
    if mode == "amd_pstate":
        # AMD pstate uses CPB via cpufreq/boost
        if boost == 1:
            return {"verdict": "boost_enabled",
                    "reason": "amd_pstate active, cpufreq/boost=1 — CPB ON.",
                    "recommendation": ""}
        if boost == 0:
            return {"verdict": "boost_disabled",
                    "reason": ("amd_pstate active, cpufreq/boost=0 — Core "
                               "Performance Boost is OFF."),
                    "recommendation": _RECIPE_BOOST}
        return {"verdict": "unknown",
                "reason": "amd_pstate present but cpufreq/boost unreadable.",
                "recommendation": ""}
    # cpufreq_boost
    if boost == 1:
        return {"verdict": "boost_enabled",
                "reason": "cpufreq/boost=1 — turbo ON via generic driver.",
                "recommendation": ""}
    if boost == 0:
        return {"verdict": "boost_disabled",
                "reason": ("cpufreq/boost=0 — turbo OFF. Prompt-processing "
                           "loses 30-40 % of peak tokens/s."),
                "recommendation": _RECIPE_BOOST}
    return {"verdict": "unknown",
            "reason": "cpufreq/boost present but unreadable.",
            "recommendation": ""}


def status(cfg=None) -> dict:
    mode = detect_mode(_CPU_ROOT)
    boost = read_cpufreq_boost(_CPU_ROOT)
    no_turbo = read_intel_no_turbo(_CPU_ROOT)
    intel_status = read_intel_status(_CPU_ROOT)
    amd_status = read_amd_status(_CPU_ROOT)
    verdict = classify(mode, boost, no_turbo, intel_status, amd_status)
    return {
        "ok": True,
        "mode": mode,
        "boost": boost,
        "no_turbo": no_turbo,
        "intel_status": intel_status,
        "amd_status": amd_status,
        "verdict": verdict,
    }
