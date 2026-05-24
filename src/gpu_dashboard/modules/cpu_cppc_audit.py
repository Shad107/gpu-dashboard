"""Module cpu_cppc_audit — ACPI CPPC perf-envelope audit
(R&D #77.1).

ACPI Collaborative Processor Performance Control (CPPC, ACPI
5.0+) advertises the firmware-blessed performance envelope as
unitless `perf` values plus their MHz translations.

Per-CPU :
  /sys/devices/system/cpu/cpu<N>/acpi_cppc/{
      highest_perf            sustainable max ("turbo headroom"
                                ceiling)
      nominal_perf            advertised base frequency perf
      lowest_nonlinear_perf   best-effort low without losing
                                responsiveness
      lowest_perf             absolute lowest perf
      nominal_freq            nominal_perf in MHz
      lowest_freq             lowest_perf in MHz
      reference_perf          reference unit ("100" by convention)
      wraparound_time         counter wrap in seconds
  }

Distinct from existing cpufreq_residency_audit / hwp_epp_audit
which read cpufreq tables, not CPPC firmware tables.

Why on a homelab :

* `highest_perf == nominal_perf` (no turbo headroom) = firmware
  has clamped the perf envelope to base ; inference single-
  thread perf is capped quietly.
* `nominal_freq < lowest_freq` ("frequency inversion") = a
  buggy CPPC table where the floor is reported higher than the
  base, often after a BIOS-update glitch.
* `intel_pstate` or `amd_pstate` running but ignoring CPPC :
  scaling_driver is *not* `cppc_cpufreq` / `amd-pstate` while
  acpi_cppc dir is populated → kernel chose a different driver.

Verdicts (priority order) :
  cppc_clamped              highest_perf == nominal_perf on ≥1
                              CPU (no headroom).
  frequency_inversion       nominal_freq < lowest_freq on ≥1
                              CPU (buggy table).
  driver_ignoring_cppc      scaling_driver not in
                              {cppc_cpufreq, amd-pstate,
                              amd-pstate-epp, intel_pstate}
                              AND acpi_cppc present.
  cppc_absent               /sys/.../cpu0/acpi_cppc/ missing on
                              all CPUs.
  ok                        envelope healthy.
  unknown                   /sys/devices/system/cpu absent.

stdlib only.
"""
from __future__ import annotations

import os
from typing import Dict, List, Optional


NAME = "cpu_cppc_audit"


_SYS_CPU = "/sys/devices/system/cpu"


_KNOBS = (
    "highest_perf", "nominal_perf", "lowest_nonlinear_perf",
    "lowest_perf", "nominal_freq", "lowest_freq",
    "reference_perf", "wraparound_time",
)


_CPPC_DRIVERS = {"cppc_cpufreq", "amd-pstate",
                       "amd-pstate-epp", "intel_pstate"}


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
            if n.startswith("cpu") and n[3:].isdigit():
                out.append(int(n[3:]))
    except OSError:
        return []
    return sorted(out)


def read_cppc(sys_cpu: str, cpu: int
                  ) -> Dict[str, Optional[int]]:
    d = os.path.join(sys_cpu, f"cpu{cpu}", "acpi_cppc")
    return {k: _read_int(os.path.join(d, k)) for k in _KNOBS}


def read_scaling_driver(sys_cpu: str, cpu: int = 0
                              ) -> Optional[str]:
    return _read(os.path.join(sys_cpu, f"cpu{cpu}", "cpufreq",
                                       "scaling_driver"))


def classify(sys_present: bool,
              cppc_by_cpu: Dict[int, Dict[str, Optional[int]]],
              scaling_driver: Optional[str]) -> dict:
    if not sys_present:
        return {"verdict": "unknown",
                "reason": "/sys/devices/system/cpu absent.",
                "recommendation": ""}

    # Detect "cppc_absent" : every cpu's acpi_cppc dir empty.
    any_cppc_data = any(
        any(v is not None for v in cppc.values())
            for cppc in cppc_by_cpu.values())
    if not any_cppc_data:
        return {"verdict": "cppc_absent",
                "reason": ("/sys/devices/system/cpu/cpu*/"
                          "acpi_cppc absent. Firmware did not "
                          "publish CPPC, or kernel without "
                          "CONFIG_ACPI_CPPC_LIB."),
                "recommendation": _recipe_absent()}

    # 1) cppc_clamped — highest_perf == nominal_perf
    clamped = []
    for cpu, c in cppc_by_cpu.items():
        hp = c.get("highest_perf")
        np = c.get("nominal_perf")
        if hp is not None and np is not None \
                and hp > 0 and hp == np:
            clamped.append(cpu)
    if clamped:
        any_cpu = clamped[0]
        c = cppc_by_cpu[any_cpu]
        return {"verdict": "cppc_clamped",
                "reason": (f"{len(clamped)} CPU(s) have "
                          f"highest_perf == nominal_perf = "
                          f"{c['highest_perf']} : no turbo "
                          f"headroom advertised."),
                "recommendation": _recipe_clamped()}

    # 2) frequency_inversion — nominal_freq < lowest_freq
    inv = []
    for cpu, c in cppc_by_cpu.items():
        nf = c.get("nominal_freq")
        lf = c.get("lowest_freq")
        if (nf is not None and lf is not None
                and nf > 0 and lf > 0 and nf < lf):
            inv.append(cpu)
    if inv:
        any_cpu = inv[0]
        c = cppc_by_cpu[any_cpu]
        return {"verdict": "frequency_inversion",
                "reason": (f"{len(inv)} CPU(s) report "
                          f"nominal_freq={c['nominal_freq']} < "
                          f"lowest_freq={c['lowest_freq']}. "
                          f"Buggy CPPC table."),
                "recommendation": _recipe_inversion()}

    # 3) driver_ignoring_cppc
    if (scaling_driver is not None
            and scaling_driver not in _CPPC_DRIVERS):
        return {"verdict": "driver_ignoring_cppc",
                "reason": (f"CPPC table present but kernel "
                          f"scaling_driver = '{scaling_driver}' "
                          f"(not in {sorted(_CPPC_DRIVERS)}). "
                          f"Firmware perf hints ignored."),
                "recommendation":
                    _recipe_driver_ignoring(scaling_driver)}

    return {"verdict": "ok",
            "reason": (f"{len(cppc_by_cpu)} CPU(s) ; CPPC "
                      f"healthy ; scaling_driver="
                      f"{scaling_driver}."),
            "recommendation": ""}


def status(config=None, sys_cpu: str = _SYS_CPU) -> dict:
    sys_present = os.path.isdir(sys_cpu)
    cppc_by_cpu: Dict[int, Dict[str, Optional[int]]] = {}
    if sys_present:
        for cpu in list_cpus(sys_cpu):
            cppc_by_cpu[cpu] = read_cppc(sys_cpu, cpu)
    scaling_driver = (read_scaling_driver(sys_cpu)
                              if sys_present else None)
    verdict = classify(sys_present, cppc_by_cpu, scaling_driver)
    sample = None
    if cppc_by_cpu:
        first = sorted(cppc_by_cpu.keys())[0]
        sample = {"cpu": first, **cppc_by_cpu[first]}
    return {"ok": sys_present,
              "cpu_count": len(cppc_by_cpu),
              "scaling_driver": scaling_driver,
              "sample_cpu_cppc": sample,
              "verdict": verdict}


# ── recovery recipes ────────────────────────────────────────────

def _recipe_clamped() -> str:
    return ("# CPPC reports highest_perf == nominal_perf (no\n"
            "# turbo headroom). Likely cause :\n"
            "#  - BIOS 'C-States / Turbo Mode' set to disabled\n"
            "#  - undervolt-unlock or PL2 lock active\n"
            "# Confirm via :\n"
            "for c in /sys/devices/system/cpu/cpu*/acpi_cppc/highest_perf; do\n"
            "  echo \"$c = $(cat $c)\"\n"
            "done | head\n"
            "# Re-enable turbo in BIOS or unlock PL2 limits.\n")


def _recipe_inversion() -> str:
    return ("# nominal_freq < lowest_freq is a buggy CPPC table.\n"
            "# Confirm :\n"
            "for c in /sys/devices/system/cpu/cpu*/acpi_cppc; do\n"
            "  echo \"$c : nom=$(cat $c/nominal_freq) low=$(cat $c/lowest_freq)\"\n"
            "done\n"
            "# Update BIOS / disable CPPC autonomous mode :\n"
            "#  - Intel : 'HwP Autonomous' = Disabled\n"
            "#  - AMD  : 'CPPC Preferred Cores' = Disabled\n")


def _recipe_driver_ignoring(drv: Optional[str]) -> str:
    return (f"# scaling_driver = '{drv}' is not a CPPC-aware\n"
            f"# driver. Switch to a CPPC backend :\n"
            f"# Intel  : add 'intel_pstate=active' to cmdline\n"
            f"# AMD    : add 'amd_pstate=active' to cmdline\n"
            f"# Generic : load cppc_cpufreq :\n"
            f"sudo modprobe cppc_cpufreq\n"
            f"# Verify :\n"
            f"cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_driver\n")


def _recipe_absent() -> str:
    return ("# /sys/.../acpi_cppc absent. CPPC is exposed by\n"
            "# firmware via the ACPI _CPC method. On a KVM/Xen\n"
            "# guest this is expected. On bare metal :\n"
            "sudo dmesg | grep -iE 'cppc|cpc' | head\n"
            "grep CONFIG_ACPI_CPPC /boot/config-$(uname -r)\n")
