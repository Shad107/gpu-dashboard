"""Module hwp_epp — HWP Energy Performance Preference audit (R&D #36.4).

On Intel CPUs running intel_pstate=active (default since Skylake),
Hardware-controlled P-states (HWP) accept a per-CPU "Energy
Performance Preference" hint through
/sys/devices/system/cpu/cpu*/cpufreq/energy_performance_preference.

The string-mode values map to MSR 0x774 bits 24-31:

  performance          0   — max turbo, full performance
  balance_performance  85  — Intel's "p+e mix" default
  default              128 — same as balance_performance on most parts
  balance_power        192 — power-save bias (laptop default)
  power                255 — maximum power saving (worst for inference)

For an LLM inference rig, anything less than `performance` on the
P-cores wastes 10-20 % of prompt-processing throughput. The default
varies by distro and kernel version — recent Ubuntu desktop ships
`balance_performance`, Fedora ships `default`, laptops often default
to `balance_power`.

Verdicts:
  performance      all CPUs at "performance" — best for inference
  balanced         all CPUs at "balance_performance" or "default"
  power_save       any CPU at "balance_power" or "power" — warn
  drift            mixed prefs across CPUs (excluding power-save)
  default_mode     all CPUs at literal "default"
  missing          EPP files absent (VM, pre-Skylake, acpi-cpufreq
                   instead of intel_pstate)
  unknown          unrecognized string values

Recipe drops a runtime `echo performance > .../energy_performance_preference`
loop + persistent sysfsutils rule.

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import Optional


NAME = "hwp_epp"


_CPU_ROOT = "/sys/devices/system/cpu"


_POWER_SAVE_VALUES = {"balance_power", "power"}
_PERFORMANCE_VALUE = "performance"
_BALANCED_VALUES = {"balance_performance", "default"}


def _read(p: str) -> Optional[str]:
    try:
        with open(p) as f:
            return f.read().strip()
    except OSError:
        return None


def read_epp(root: str, n: int) -> Optional[str]:
    return _read(os.path.join(root, f"cpu{n}", "cpufreq",
                                  "energy_performance_preference"))


def read_available(root: str, n: int) -> list:
    s = _read(os.path.join(root, f"cpu{n}", "cpufreq",
                                 "energy_performance_available_preferences"))
    return s.split() if s else []


_CPU_RE = re.compile(r"^cpu(\d+)$")


def list_cpus_with_epp(root: str = _CPU_ROOT) -> list:
    try:
        names = os.listdir(root)
    except OSError:
        return []
    out: list = []
    for n in names:
        m = _CPU_RE.match(n)
        if not m:
            continue
        cpu_id = int(m.group(1))
        if read_epp(root, cpu_id) is not None:
            out.append(cpu_id)
    return sorted(out)


_RECIPE_PERFORMANCE = (
    "# Runtime — set all CPUs to performance EPP:\n"
    "for f in /sys/devices/system/cpu/cpu*/cpufreq/energy_performance_preference; do\n"
    "  echo performance | sudo tee \"$f\" >/dev/null\n"
    "done\n"
    "# Persist via /etc/sysfs.d/99-hwp-epp.conf:\n"
    "# Note: requires the `sysfsutils` package + a wildcard-aware\n"
    "# loader. Most users prefer tuned-adm:\n"
    "sudo tuned-adm profile latency-performance"
)


def classify(prefs: list) -> dict:
    if not prefs:
        return {"verdict": "missing",
                "reason": ("No /sys/devices/system/cpu/cpu*/cpufreq/"
                           "energy_performance_preference files — host "
                           "is on acpi-cpufreq (not intel_pstate), VM, "
                           "or pre-Skylake."),
                "recommendation": ""}
    distinct = sorted(set(prefs))
    # power-save anywhere → warn
    if any(p in _POWER_SAVE_VALUES for p in prefs):
        return {"verdict": "power_save",
                "reason": (f"At least one CPU is set to "
                           f"{[p for p in prefs if p in _POWER_SAVE_VALUES][0]} "
                           f"— HWP biases against turbo, capping prompt-"
                           f"processing throughput by 10-20 %."),
                "recommendation": _RECIPE_PERFORMANCE}
    if all(p == _PERFORMANCE_VALUE for p in prefs):
        return {"verdict": "performance",
                "reason": (f"All {len(prefs)} CPUs at "
                           f"energy_performance_preference=performance — "
                           f"max-turbo bias, best for inference."),
                "recommendation": ""}
    if all(p in _BALANCED_VALUES for p in prefs):
        if all(p == "default" for p in prefs):
            return {"verdict": "default_mode",
                    "reason": (f"All {len(prefs)} CPUs at "
                               f"energy_performance_preference=default — "
                               f"acceptable, but `performance` gives ~"
                               f"10-15% more on prompt-processing."),
                    "recommendation": _RECIPE_PERFORMANCE}
        return {"verdict": "balanced",
                "reason": (f"All {len(prefs)} CPUs at "
                           f"{distinct[0]} — acceptable, but "
                           f"`performance` gives more on inference."),
                "recommendation": _RECIPE_PERFORMANCE}
    # Mixed values
    if all(p in _PERFORMANCE_VALUE or p in _BALANCED_VALUES for p in prefs):
        return {"verdict": "drift",
                "reason": (f"CPUs report mixed EPP values "
                           f"({distinct}) — uneven HWP bias across "
                           f"cores."),
                "recommendation": _RECIPE_PERFORMANCE}
    return {"verdict": "unknown",
            "reason": f"Unrecognized EPP values: {distinct}.",
            "recommendation": ""}


def status(cfg=None) -> dict:
    cpus = list_cpus_with_epp(_CPU_ROOT)
    if not cpus:
        verdict = classify([])
        return {"ok": True, "cpu_count": 0, "prefs": [],
                "distinct_prefs": [], "available": [],
                "verdict": verdict}
    prefs = [read_epp(_CPU_ROOT, n) or "" for n in cpus]
    available = read_available(_CPU_ROOT, cpus[0])
    verdict = classify(prefs)
    return {
        "ok": True,
        "cpu_count": len(cpus),
        "prefs": prefs,
        "distinct_prefs": sorted(set(prefs)),
        "available": available,
        "verdict": verdict,
    }
