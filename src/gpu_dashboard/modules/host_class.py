"""Module host_class — chassis + virt + form-factor (R&D #39.4).

Many of the audit modules' recipes vary by host class:
  - Laptop → battery-aware power management + thermal envelope
  - Desktop → focus on quiet vs perf
  - Server (rack) → unattended reliability (#37.3 hw_watchdog,
                    #31.4 oom_priority)
  - VM → most kernel tuning is host-owned ; surface limited subset

This module reads /sys/class/dmi/id/{chassis_type,sys_vendor,
product_name,bios_vendor} + detects VMs via /sys/firmware/
qemu_fw_cfg or vendor strings, and emits a single classifier so
the UI can stop showing irrelevant recipes.

Verdicts:
  laptop          chassis_type in {8,9,10,11,14,30,31,32}
  desktop         chassis_type in {3,4,5,6,7,15,24}
  server          chassis_type in {17,23,25}
  aio             chassis_type=13 (all-in-one)
  mini_pc         chassis_type=28
  embedded        chassis_type=36
  vm              QEMU / VMware / Xen / Hyper-V detected, regardless
                  of chassis (often type=1 / 2 in guests)
  unknown         no clear classification

stdlib only.
"""
from __future__ import annotations

import os
from typing import Optional


NAME = "host_class"


_DMI_ROOT = "/sys/class/dmi/id"
_FIRMWARE_ROOT = "/sys/firmware"


# SMBIOS chassis type → broad kind
_CHASSIS_MAP = {
    3: "desktop", 4: "desktop", 5: "desktop", 6: "desktop", 7: "desktop",
    8: "laptop", 9: "laptop", 10: "laptop", 11: "laptop",
    13: "aio",
    14: "laptop", 15: "desktop",
    17: "server", 18: "server", 23: "server", 25: "server",
    24: "desktop", 28: "mini_pc",
    30: "laptop", 31: "laptop", 32: "laptop",
    36: "embedded",
}


def chassis_kind(chassis_type: int) -> str:
    return _CHASSIS_MAP.get(chassis_type, "unknown_kind")


_VIRT_VENDOR_HINTS = {
    "qemu": "qemu",
    "vmware": "vmware",
    "xen": "xen",
    "innotek": "virtualbox",          # VirtualBox
    "virtualbox": "virtualbox",
    "microsoft corporation": "hyperv",  # Hyper-V
    "bochs": "bochs",
    "google": "gce",                  # Google Compute Engine
}


def detect_virt(sys_vendor: str = "", bios_vendor: str = "",
                  firmware_root: str = _FIRMWARE_ROOT) -> dict:
    """Combine vendor strings + /sys/firmware/qemu_fw_cfg + /sys/hypervisor."""
    sv = (sys_vendor or "").strip().lower()
    bv = (bios_vendor or "").strip().lower()
    platform = None
    for hint, label in _VIRT_VENDOR_HINTS.items():
        if hint in sv or hint in bv:
            platform = label
            break
    # qemu_fw_cfg present → guest VM regardless of vendor strings
    if os.path.exists(os.path.join(firmware_root, "qemu_fw_cfg")):
        platform = platform or "qemu"
    # /sys/hypervisor on Xen/Hyper-V (read-only filesystem entry)
    if (platform is None and
            os.path.isdir(os.path.join(firmware_root, "..", "hypervisor"))):
        platform = "hypervisor"
    return {"is_virt": platform is not None, "platform": platform}


_RECIPE_LAPTOP = (
    "# Laptop-specific tuning (companion to other audits):\n"
    "# - #36.4 hwp_epp: pick `balance_performance` (NOT `power`)\n"
    "# - #36.2 cpuidle: governor=teo or haltpoll, not menu\n"
    "# - Monitor /sys/class/power_supply/<bat>/status — pause heavy\n"
    "#   inference when on battery (custom systemd ConditionACPower=).\n"
    "# - Consider tuned-adm profile laptop-ac-powersave."
)

_RECIPE_SERVER = (
    "# Server / rack tuning (unattended reliability):\n"
    "# - #37.3 hw_watchdog: enable + add userspace pinger\n"
    "# - #31.4 oom_priority: OOMScoreAdjust=-500 on inference units\n"
    "# - #29.8 rlimit_audit: LimitMEMLOCK=infinity on each unit\n"
    "# - #32.5 cgroup_memcap: MemoryMax=infinity, MemorySwapMax=0\n"
    "# - irqbalance install + enable (companion to #38.4)"
)

_RECIPE_VM = (
    "# In a VM ; host owns most low-level tuning. What you CAN tune:\n"
    "# - Kernel sysctls (#32.4 vm_sysctl, #35.2 net_sysctl, #39.2)\n"
    "# - Cgroup limits per service (#32.5, #33.6)\n"
    "# - mlock + rlimit per daemon (#29.8, PAM limits)\n"
    "# - swappiness, transparent_hugepage (#32.4, #34.1)\n"
    "# Skip the cpufreq / cstate / hwmon advice — host owns those."
)


def classify(chassis: str, virt: dict) -> dict:
    if virt.get("is_virt"):
        plat = virt.get("platform") or "unknown"
        return {"verdict": "vm",
                "reason": (f"Running in a {plat} virtual machine — most "
                           f"low-level kernel tuning is owned by the "
                           f"host hypervisor."),
                "recommendation": _RECIPE_VM}
    if chassis == "laptop":
        return {"verdict": "laptop",
                "reason": ("Laptop chassis — battery + thermal envelope "
                           "matter, push power-saving recipes off the "
                           "critical path."),
                "recommendation": _RECIPE_LAPTOP}
    if chassis == "server":
        return {"verdict": "server",
                "reason": ("Server chassis (rack / main-server) — "
                           "unattended reliability is the priority."),
                "recommendation": _RECIPE_SERVER}
    if chassis == "desktop":
        return {"verdict": "desktop",
                "reason": ("Desktop chassis — the dashboard's full "
                           "suite of perf/cost trade-offs apply."),
                "recommendation": ""}
    if chassis in ("aio", "mini_pc", "embedded"):
        return {"verdict": chassis,
                "reason": (f"Chassis type maps to `{chassis}` — usually "
                           f"thermal-bound, lean on the perf/cost "
                           f"recipes but check #28.5 thermal_zones first."),
                "recommendation": ""}
    return {"verdict": "unknown",
            "reason": ("Chassis type is `Other` / `Unknown` per SMBIOS "
                       "and no virt signature detected. Adaptive "
                       "recipes won't tailor to this host."),
            "recommendation": ""}


def _read(p: str) -> Optional[str]:
    try:
        with open(p) as f:
            return f.read().strip()
    except OSError:
        return None


def status(cfg=None) -> dict:
    if not os.path.isdir(_DMI_ROOT):
        return {"ok": False, "error": "dmi_unavailable",
                "reason": f"{_DMI_ROOT} not present."}
    chassis_raw = _read(os.path.join(_DMI_ROOT, "chassis_type")) or ""
    try:
        chassis_type = int(chassis_raw) if chassis_raw else None
    except ValueError:
        chassis_type = None
    sys_vendor = _read(os.path.join(_DMI_ROOT, "sys_vendor")) or ""
    product_name = _read(os.path.join(_DMI_ROOT, "product_name")) or ""
    bios_vendor = _read(os.path.join(_DMI_ROOT, "bios_vendor")) or ""
    kind = chassis_kind(chassis_type) if chassis_type is not None else "unknown_kind"
    virt = detect_virt(sys_vendor, bios_vendor, _FIRMWARE_ROOT)
    verdict = classify(kind, virt)
    return {
        "ok": True,
        "chassis_type": chassis_type,
        "chassis_kind": kind,
        "sys_vendor": sys_vendor,
        "product_name": product_name,
        "bios_vendor": bios_vendor,
        "virt": virt,
        "verdict": verdict,
    }
