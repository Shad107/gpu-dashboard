"""Module cpuidle_audit — cpuidle C-state exit-latency audit (R&D #36.2).

The kernel `cpuidle` subsystem decides which C-state a CPU enters
when idle. The deepest C-states (C7 / C8 / C10 on modern Intel,
C2 / C3 on AMD) cost ~1000 µs to exit. For LLM workloads with
CUDA host→device roundtrips that's death by a thousand cuts:
every kernel launch incurs a wake-up on a host core that may have
slept into a deep state since the last launch.

For low-latency inference the canonical fix is:

  - Switch the governor to `haltpoll` (CPU spins in polling halt
    before deciding to enter a deep C-state — great for VMs and
    inference). Available since kernel 5.0.
  - OR use `cpupower idle-set --disable-by-latency 1000` to mask
    out states with exit_latency > 1 ms.

This module reads:

  /sys/devices/system/cpu/cpuidle/current_driver
                                      → "intel_idle", "acpi_idle",
                                        "none", etc.
  /sys/devices/system/cpu/cpuidle/current_governor
                                      → "menu", "teo", "haltpoll",
                                        "ladder"
  /sys/devices/system/cpu/cpu0/cpuidle/state*/{name,latency,
                                                 residency,disable,
                                                 usage,time}

We use cpu0 as the representative — all CPUs in a package share the
same state table.

Verdicts:
  haltpoll_optimal     governor=haltpoll — best for inference
  shallow_only         max C-state exit-latency <= 100 µs — fine
  balanced             100 < max <= 500 µs — moderate
  deep_states_active   max > 500 µs — surfaces idle-set recipe
  disabled_driver      driver="none" — VM / kernel without
                       cpuidle ; nothing to tune
  unknown              cpuidle dir absent

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import Optional


NAME = "cpuidle_audit"


_CPU_ROOT = "/sys/devices/system/cpu"


def _read(p: str) -> Optional[str]:
    try:
        with open(p) as f:
            return f.read().strip()
    except OSError:
        return None


def read_current_driver(root: str = _CPU_ROOT) -> Optional[str]:
    return _read(os.path.join(root, "cpuidle", "current_driver"))


def read_current_governor(root: str = _CPU_ROOT) -> Optional[str]:
    return _read(os.path.join(root, "cpuidle", "current_governor"))


def read_available_governors(root: str = _CPU_ROOT) -> list:
    s = _read(os.path.join(root, "cpuidle", "available_governors"))
    return s.split() if s else []


def _read_int(p: str) -> Optional[int]:
    s = _read(p)
    if s is None:
        return None
    try:
        return int(s)
    except ValueError:
        return None


def read_cpu_states(root: str, cpu_id: int) -> list:
    base = os.path.join(root, f"cpu{cpu_id}", "cpuidle")
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return []
    out: list = []
    for n in names:
        m = re.match(r"^state(\d+)$", n)
        if not m:
            continue
        d = os.path.join(base, n)
        out.append({
            "state": int(m.group(1)),
            "name": _read(os.path.join(d, "name")) or "",
            "desc": _read(os.path.join(d, "desc")) or "",
            "latency": _read_int(os.path.join(d, "latency")),
            "residency": _read_int(os.path.join(d, "residency")),
            "disable": _read_int(os.path.join(d, "disable")) or 0,
            "usage": _read_int(os.path.join(d, "usage")),
            "time": _read_int(os.path.join(d, "time")),
        })
    return out


_SHALLOW_THRESHOLD = 100      # µs
_BALANCED_THRESHOLD = 500     # µs


_RECIPE_DISABLE_DEEP = (
    "# Mask deep C-states by latency cap (root, runtime):\n"
    "sudo cpupower idle-set --disable-by-latency 1000\n"
    "# OR switch governor to haltpoll (kernel 5.0+):\n"
    "sudo modprobe cpuidle_haltpoll force=1\n"
    "echo haltpoll | sudo tee /sys/devices/system/cpu/cpuidle/current_governor\n"
    "# Persistent via tuned-adm:\n"
    "sudo tuned-adm profile latency-performance"
)


def classify(driver: Optional[str], governor: Optional[str],
              max_latency: Optional[int]) -> dict:
    if driver is None and governor is None:
        return {"verdict": "unknown",
                "reason": "cpuidle subsystem not exposed.",
                "recommendation": ""}
    if driver == "none":
        return {"verdict": "disabled_driver",
                "reason": ("cpuidle current_driver=none — kernel has no "
                           "active C-state driver. Typical for VMs (the "
                           "hypervisor controls idle states) or kernels "
                           "booted with `cpuidle.off=1`."),
                "recommendation": ""}
    if governor == "haltpoll":
        return {"verdict": "haltpoll_optimal",
                "reason": ("Governor=haltpoll — CPU spins briefly before "
                           "entering a deep C-state, ideal for low-"
                           "latency CUDA roundtrips."),
                "recommendation": ""}
    if max_latency is None:
        return {"verdict": "unknown",
                "reason": (f"driver={driver}, governor={governor}, but "
                           f"no per-state latency data available."),
                "recommendation": ""}
    if max_latency <= _SHALLOW_THRESHOLD:
        return {"verdict": "shallow_only",
                "reason": (f"Max C-state exit-latency = {max_latency} µs "
                           f"≤ {_SHALLOW_THRESHOLD} µs — only shallow "
                           f"states active, no CUDA roundtrip tax."),
                "recommendation": ""}
    if max_latency <= _BALANCED_THRESHOLD:
        return {"verdict": "balanced",
                "reason": (f"Max exit-latency = {max_latency} µs — "
                           f"moderate. Acceptable for batch inference, "
                           f"may want to bound for real-time."),
                "recommendation": _RECIPE_DISABLE_DEEP}
    return {"verdict": "deep_states_active",
            "reason": (f"Max C-state exit-latency = {max_latency} µs — "
                       f"deep states active. Every CUDA kernel launch "
                       f"may pay this on the host wake-up path."),
            "recommendation": _RECIPE_DISABLE_DEEP}


def status(cfg=None) -> dict:
    if not os.path.isdir(os.path.join(_CPU_ROOT, "cpuidle")):
        return {"ok": False, "error": "cpuidle_unavailable",
                "reason": f"{_CPU_ROOT}/cpuidle absent."}
    driver = read_current_driver(_CPU_ROOT)
    governor = read_current_governor(_CPU_ROOT)
    available = read_available_governors(_CPU_ROOT)
    states = read_cpu_states(_CPU_ROOT, 0)
    max_latency = None
    if states:
        latencies = [s["latency"] for s in states
                       if s["latency"] is not None and s["disable"] == 0]
        if latencies:
            max_latency = max(latencies)
    verdict = classify(driver, governor, max_latency)
    return {
        "ok": True,
        "driver": driver,
        "governor": governor,
        "available_governors": available,
        "states": states,
        "max_latency": max_latency,
        "verdict": verdict,
    }
