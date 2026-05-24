"""Module cpu_thermal_throttle_counters_audit — per-CPU thermal
+ power-limit throttle counters (R&D #77.2).

The kernel maintains monotonic counters under
/sys/devices/system/cpu/cpu<N>/thermal_throttle/ that tick
whenever a CPU core or its package crosses a thermal trip
or a Running Average Power Limit (RAPL).

  core_throttle_count          monotonic core-level throttle hits
  core_throttle_max_time_ms    longest single core throttle event
  core_power_limit_count       RAPL-driven core power-clamp hits
  package_throttle_count       package-level thermal throttle
  package_throttle_max_time_ms longest single package throttle
  package_power_limit_count    package RAPL clamp hits

Why on a homelab :

* Existing throttle modules cover NVIDIA GPU bits only. CPU
  thermal throttle counters catch the *other* side of the
  pipeline — long llama.cpp prompt-prefill bursts ramp host
  tokenizers, hit the package thermal trip, and the GPU then
  sits idle waiting for tokens.
* In a tight desktop case with marginal airflow, both core and
  package counters tick up before any visible fan-curve change.

Verdicts (priority order) :
  package_throttling_active   ≥1 CPU has
                                package_throttle_count > 0 AND
                                max_time_ms > 0 (real throttle
                                events recorded since boot).
  core_throttling_active      ≥1 CPU has core_throttle_count > 0.
  power_limit_hit             ≥1 CPU has core_power_limit_count
                                > 0 OR package_power_limit_count
                                > 0 (RAPL clamped without
                                thermal throttle — usually
                                intentional but worth surfacing).
  counters_absent             /sys/.../cpu0/thermal_throttle
                                missing (KVM guest, ARM, no
                                thermal management driver).
  ok                          counters present, all zero.
  unknown                     /sys/devices/system/cpu absent
                                (test).

stdlib only.
"""
from __future__ import annotations

import os
from typing import Dict, List, Optional


NAME = "cpu_thermal_throttle_counters_audit"


_SYS_CPU = "/sys/devices/system/cpu"


_KNOBS = (
    "core_throttle_count", "core_throttle_max_time_ms",
    "core_power_limit_count",
    "package_throttle_count", "package_throttle_max_time_ms",
    "package_power_limit_count",
)


def _read(path: str) -> Optional[str]:
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def _read_int(path: str) -> Optional[int]:
    t = _read(path)
    if t is None:
        return None
    try:
        return int(t)
    except ValueError:
        return None


def list_cpus(sys_cpu: str = _SYS_CPU) -> List[int]:
    if not os.path.isdir(sys_cpu):
        return []
    out: List[int] = []
    try:
        for n in os.listdir(sys_cpu):
            if (n.startswith("cpu")
                    and n[3:].isdigit()):
                out.append(int(n[3:]))
    except OSError:
        return []
    return sorted(out)


def read_throttle_counters(sys_cpu: str, cpu: int
                                  ) -> Dict[str, Optional[int]]:
    d = os.path.join(sys_cpu, f"cpu{cpu}", "thermal_throttle")
    return {k: _read_int(os.path.join(d, k)) for k in _KNOBS}


def classify(sys_present: bool,
              counters_by_cpu: Dict[int, Dict[str, Optional[int]]]
              ) -> dict:
    if not sys_present:
        return {"verdict": "unknown",
                "reason": "/sys/devices/system/cpu absent.",
                "recommendation": ""}

    # Detect "counters_absent" : every cpu has all-None counters
    counters_present = any(
        any(v is not None for v in cnt.values())
            for cnt in counters_by_cpu.values())
    if not counters_present:
        return {"verdict": "counters_absent",
                "reason": ("/sys/devices/system/cpu/cpu*/"
                          "thermal_throttle/ absent. Common on "
                          "KVM guests, ARM, and Intel CPUs "
                          "without therm_throt driver."),
                "recommendation": _recipe_absent()}

    # 1) package_throttling_active
    pkg_thr = [cpu for cpu, c in counters_by_cpu.items()
                  if (c.get("package_throttle_count") or 0) > 0
                    and (c.get("package_throttle_max_time_ms")
                            or 0) > 0]
    if pkg_thr:
        # Find worst.
        worst = max(pkg_thr,
                       key=lambda cpu: counters_by_cpu[cpu][
                           "package_throttle_count"] or 0)
        w = counters_by_cpu[worst]
        return {"verdict": "package_throttling_active",
                "reason": (f"{len(pkg_thr)} CPU(s) recorded "
                          f"package thermal throttle ; worst : "
                          f"cpu{worst} count="
                          f"{w['package_throttle_count']} "
                          f"max_ms="
                          f"{w['package_throttle_max_time_ms']}."),
                "recommendation": _recipe_pkg_throttle()}

    # 2) core_throttling_active
    core_thr = [cpu for cpu, c in counters_by_cpu.items()
                    if (c.get("core_throttle_count") or 0) > 0]
    if core_thr:
        worst = max(core_thr,
                       key=lambda cpu: counters_by_cpu[cpu][
                           "core_throttle_count"] or 0)
        w = counters_by_cpu[worst]
        return {"verdict": "core_throttling_active",
                "reason": (f"{len(core_thr)} CPU(s) recorded "
                          f"core thermal throttle ; worst : "
                          f"cpu{worst} count="
                          f"{w['core_throttle_count']}."),
                "recommendation": _recipe_core_throttle()}

    # 3) power_limit_hit — RAPL clamp without thermal throttle
    plim = [cpu for cpu, c in counters_by_cpu.items()
              if (c.get("core_power_limit_count") or 0) > 0
                or (c.get("package_power_limit_count") or 0) > 0]
    if plim:
        worst = max(plim,
                       key=lambda cpu: (
                           counters_by_cpu[cpu]
                               .get("package_power_limit_count") or 0)
                           + (counters_by_cpu[cpu]
                                  .get("core_power_limit_count") or 0))
        w = counters_by_cpu[worst]
        return {"verdict": "power_limit_hit",
                "reason": (f"{len(plim)} CPU(s) hit RAPL power "
                          f"clamp ; worst : cpu{worst} "
                          f"pkg_plim="
                          f"{w.get('package_power_limit_count')} "
                          f"core_plim="
                          f"{w.get('core_power_limit_count')}."),
                "recommendation": _recipe_power_limit()}

    return {"verdict": "ok",
            "reason": (f"{len(counters_by_cpu)} CPU(s) ; all "
                      f"thermal + power-limit throttle counters "
                      f"are zero."),
            "recommendation": ""}


def status(config=None, sys_cpu: str = _SYS_CPU) -> dict:
    sys_present = os.path.isdir(sys_cpu)
    counters_by_cpu: Dict[int, Dict[str, Optional[int]]] = {}
    if sys_present:
        for cpu in list_cpus(sys_cpu):
            counters_by_cpu[cpu] = read_throttle_counters(
                sys_cpu, cpu)
    verdict = classify(sys_present, counters_by_cpu)
    return {"ok": sys_present,
              "cpu_count": len(counters_by_cpu),
              "counters_by_cpu": {str(c): v
                                          for c, v in counters_by_cpu.items()},
              "verdict": verdict}


# ── recovery recipes ────────────────────────────────────────────

def _recipe_pkg_throttle() -> str:
    return ("# Package-level thermal throttle recorded. Inspect :\n"
            "for c in /sys/devices/system/cpu/cpu*/"
            "thermal_throttle/package_throttle_count; do\n"
            "  echo \"$c = $(cat $c)\"\n"
            "done\n"
            "# Check current temp + tjMax :\n"
            "cat /sys/class/thermal/thermal_zone*/temp\n"
            "# Re-paste CPU TIM, clean fans, recheck airflow.\n")


def _recipe_core_throttle() -> str:
    return ("# Core-level thermal throttle. Identify worst core :\n"
            "for c in /sys/devices/system/cpu/cpu*/"
            "thermal_throttle/core_throttle_count; do\n"
            "  echo \"$c = $(cat $c)\"\n"
            "done | sort -k3 -nr\n"
            "# Inspect MSR_IA32_THERM_STATUS for the hot core :\n"
            "sudo rdmsr -p <cpu> 0x19c\n")


def _recipe_power_limit() -> str:
    return ("# RAPL power-limit clamp without thermal throttle.\n"
            "# Often intentional (PL1/PL2 set low by BIOS).\n"
            "cat /sys/class/powercap/intel-rapl/intel-rapl:0/"
            "constraint_*_power_limit_uw 2>/dev/null\n"
            "# Raise PL1 (long-term) on a desktop with headroom :\n"
            "sudo cpupower frequency-info\n")


def _recipe_absent() -> str:
    return ("# Per-CPU thermal_throttle counters missing. This\n"
            "# is normal on :\n"
            "#   - KVM / VirtualBox guests (no thermal hardware)\n"
            "#   - ARM (different thermal framework)\n"
            "#   - Intel CPUs older than Pentium-M without\n"
            "#     therm_throt driver loaded.\n"
            "# Modprobe coretemp if you expect counters :\n"
            "sudo modprobe coretemp\n")
