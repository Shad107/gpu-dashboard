"""Module clocksource_audit — kernel clocksource auditor (R&D #33.4).

The kernel timer subsystem can fall back to slow clocksources (`hpet`,
`acpi_pm`, `jiffies`) when TSC validation fails — typically because
BIOS marks the TSC unstable, an X server reads CMOS at boot, or a
firmware quirk lists hpet first. The dashboard itself reads
nvidia-smi and sysfs at second cadence, so the per-read clock cost
matters: TSC = ~10 ns, hpet ~1 µs (100×), jiffies ~10 ms (1 000 000×).

The audit reads:

  /sys/devices/system/clocksource/clocksource0/current_clocksource
  /sys/devices/system/clocksource/clocksource0/available_clocksource

Verdicts:
  optimal          tsc on bare metal ; kvm-clock on KVM guest ;
                   xen on Xen guest ; hyperv_* on Hyper-V guest
  suboptimal_virt  raw tsc on a KVM/Xen guest (kvm-clock is more
                   stable across vCPU migrations + live migrate)
  acceptable       acpi_pm — slow but correct
  hpet_active     hpet currently selected with tsc available
  low_res          jiffies — millisecond resolution, breaks fine
                   metric collection
  unknown          /sys/devices/system/clocksource absent

stdlib only.
"""
from __future__ import annotations

import os
from typing import Optional


NAME = "clocksource_audit"


_CLOCK_ROOT = "/sys/devices/system/clocksource/clocksource0"


# Hypervisor-specific clocksources
_KVM_SOURCES = ("kvm-clock",)
_XEN_SOURCES = ("xen",)
_HYPERV_PREFIX = "hyperv_"


def _read(p: str) -> Optional[str]:
    try:
        with open(p) as f:
            return f.read().strip()
    except OSError:
        return None


def read_current(root: str = _CLOCK_ROOT) -> Optional[str]:
    return _read(os.path.join(root, "current_clocksource"))


def read_available(root: str = _CLOCK_ROOT) -> list:
    s = _read(os.path.join(root, "available_clocksource"))
    return s.split() if s else []


def detect_virt(available: list) -> Optional[str]:
    """Infer virtualization mode from the available clocksource list."""
    for src in available:
        if src in _KVM_SOURCES:
            return "kvm"
        if src in _XEN_SOURCES:
            return "xen"
        if src.startswith(_HYPERV_PREFIX):
            return "hyperv"
    return None


_RECIPE = (
    "# Switch to TSC immediately (root, sysfs is runtime-writable):\n"
    "echo tsc | sudo tee /sys/devices/system/clocksource/clocksource0/current_clocksource\n"
    "# Persist via GRUB cmdline:\n"
    "# Edit /etc/default/grub, add `clocksource=tsc` to GRUB_CMDLINE_LINUX_DEFAULT\n"
    "sudo update-grub && reboot\n"
    "# Verify after reboot:\n"
    "cat /sys/devices/system/clocksource/clocksource0/current_clocksource"
)


_RECIPE_KVM_CLOCK = (
    "# On a KVM guest, kvm-clock is more stable than raw TSC across vCPU\n"
    "# migrations and live-migrate. Switch:\n"
    "echo kvm-clock | sudo tee /sys/devices/system/clocksource/clocksource0/current_clocksource\n"
    "# Persist via GRUB cmdline:\n"
    "# Edit /etc/default/grub, add `clocksource=kvm-clock`\n"
    "sudo update-grub && reboot"
)


def classify(current: Optional[str], available: list,
              virt: Optional[str]) -> dict:
    if not current:
        return {"verdict": "unknown",
                "reason": "Could not read current_clocksource.",
                "recommendation": ""}
    if current == "jiffies":
        return {"verdict": "low_res",
                "reason": ("jiffies has millisecond resolution — kernel "
                           "fell back from TSC. Every dashboard tick "
                           "incurs a 10 ms clock-read cost."),
                "recommendation": _RECIPE if "tsc" in available else ""}
    if current == "hpet":
        return {"verdict": "hpet_active",
                "reason": ("hpet selected despite tsc being available. "
                           "Each clock-read costs ~1 µs vs ~10 ns for "
                           "TSC ; multiplied by the dashboard tick rate "
                           "this is measurable."),
                "recommendation": _RECIPE if "tsc" in available else ""}
    if current == "acpi_pm":
        return {"verdict": "acceptable",
                "reason": ("acpi_pm is correct but slow — used when TSC "
                           "+ HPET are both rejected by BIOS quirks."),
                "recommendation": (_RECIPE if "tsc" in available else "")}
    # virt-specific best paths
    if virt == "kvm":
        if current in _KVM_SOURCES:
            return {"verdict": "optimal",
                    "reason": (f"{current} on KVM guest — most stable "
                               f"across vCPU migrations + live migrate."),
                    "recommendation": ""}
        if current == "tsc":
            return {"verdict": "suboptimal_virt",
                    "reason": ("Raw TSC on a KVM guest. kvm-clock is "
                               "available and more stable across "
                               "vCPU migrations + live migrate."),
                    "recommendation": _RECIPE_KVM_CLOCK}
    if virt == "xen":
        if current in _XEN_SOURCES:
            return {"verdict": "optimal",
                    "reason": f"{current} on Xen guest — paravirt clock.",
                    "recommendation": ""}
    if virt == "hyperv":
        if current.startswith(_HYPERV_PREFIX):
            return {"verdict": "optimal",
                    "reason": (f"{current} on Hyper-V guest — enlightenment "
                               f"clock."),
                    "recommendation": ""}
    if current == "tsc":
        return {"verdict": "optimal",
                "reason": ("tsc on bare metal — fastest clock read "
                           "(~10 ns per call)."),
                "recommendation": ""}
    return {"verdict": "acceptable",
            "reason": f"Using {current} — uncommon but not slow.",
            "recommendation": ""}


def status(cfg=None) -> dict:
    if not os.path.isdir(_CLOCK_ROOT):
        return {"ok": False, "error": "clocksource_unavailable",
                "reason": (f"{_CLOCK_ROOT} not present on this system.")}
    current = read_current(_CLOCK_ROOT)
    available = read_available(_CLOCK_ROOT)
    virt = detect_virt(available)
    verdict = classify(current, available, virt)
    return {
        "ok": True,
        "current": current,
        "available": available,
        "virt": virt,
        "verdict": verdict,
    }
