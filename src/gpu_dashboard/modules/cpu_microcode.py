"""Module cpu_microcode — CPU microcode revision audit (R&D #36.1).

Microcode patches Intel and AMD CPUs at boot time. They land via:
  - /sys/devices/system/cpu/microcode/version  (modern kernels)
  - per-CPU `microcode` field in /proc/cpuinfo (older fallback)
  - `cpu0/microcode/version` per-CPU (rare)

For an LLM rig, stale microcode means Spectre/MDS/Downfall
mitigations may be running in slow-path SW emulation instead of
hardware-fast paths — measurable cost on prompt processing. A
*drifted* microcode (different revisions across CPUs) is a sign
of a mid-flight initramfs update that hasn't been reapplied with
`update-initramfs -u && reboot`.

This module pulls the per-CPU microcode list from /proc/cpuinfo +
the canonical /sys/.../microcode/version when present, classifies:

  synced   all CPUs report the same revision — healthy
  drift    CPUs report different revisions — needs reboot
  missing  no `microcode` field in cpuinfo (kernel without
            CONFIG_MICROCODE, AMD with --noload, or guest VM
            where the hypervisor masks the MSR)
  unknown  cannot read cpuinfo

Recipe drops the canonical Debian/Ubuntu package name + initramfs
rebuild + reboot, per vendor.

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import Optional


NAME = "cpu_microcode"


_CPUINFO = "/proc/cpuinfo"
_SYS_MICROCODE = "/sys/devices/system/cpu/microcode"


_MICROCODE_RE = re.compile(r"^microcode\s*:\s*(\S+)", re.MULTILINE)
_VENDOR_RE = re.compile(r"^vendor_id\s*:\s*(\S+)", re.MULTILINE)
_FAMILY_RE = re.compile(r"^cpu family\s*:\s*(\S+)", re.MULTILINE)
_MODEL_RE = re.compile(r"^model\s*:\s*(\S+)", re.MULTILINE)
_MODEL_NAME_RE = re.compile(r"^model name\s*:\s*(.+)$", re.MULTILINE)


def parse_cpuinfo(text: str) -> dict:
    if not text:
        return {"microcodes": [], "vendor_id": None,
                "cpu_family": None, "model": None, "model_name": None}
    microcodes = [m.group(1) for m in _MICROCODE_RE.finditer(text)]
    vendor = (_VENDOR_RE.search(text).group(1)
              if _VENDOR_RE.search(text) else None)
    fam = (_FAMILY_RE.search(text).group(1)
           if _FAMILY_RE.search(text) else None)
    mdl = (_MODEL_RE.search(text).group(1)
           if _MODEL_RE.search(text) else None)
    mname = _MODEL_NAME_RE.search(text)
    mname = mname.group(1).strip() if mname else None
    return {
        "microcodes": microcodes,
        "vendor_id": vendor,
        "cpu_family": fam,
        "model": mdl,
        "model_name": mname,
    }


def _read(p: str) -> Optional[str]:
    try:
        with open(p) as f:
            return f.read()
    except OSError:
        return None


_RECIPE_INTEL = (
    "# Stale or drifted microcode. Fix on Debian/Ubuntu:\n"
    "sudo apt update && sudo apt install --reinstall intel-microcode\n"
    "sudo update-initramfs -u\n"
    "sudo reboot\n"
    "# Verify after reboot:\n"
    "grep -m1 microcode /proc/cpuinfo"
)

_RECIPE_AMD = (
    "# Stale or drifted microcode. Fix on Debian/Ubuntu:\n"
    "sudo apt update && sudo apt install --reinstall amd64-microcode\n"
    "sudo update-initramfs -u\n"
    "sudo reboot\n"
    "# Verify after reboot:\n"
    "grep -m1 microcode /proc/cpuinfo"
)


def classify(microcodes: list, vendor: Optional[str]) -> dict:
    if not microcodes:
        return {"verdict": "missing",
                "reason": ("No `microcode` field in /proc/cpuinfo — "
                           "kernel built without CONFIG_MICROCODE, or "
                           "guest VM where the hypervisor masks the "
                           "MSR. No drift detection possible."),
                "recommendation": ""}
    distinct = sorted(set(microcodes))
    if len(distinct) == 1:
        return {"verdict": "synced",
                "reason": (f"All {len(microcodes)} CPUs report "
                           f"microcode={distinct[0]} — healthy."),
                "recommendation": ""}
    recipe = _RECIPE_AMD if vendor == "AuthenticAMD" else _RECIPE_INTEL
    return {
        "verdict": "drift",
        "reason": (f"CPUs report mixed microcode revisions: "
                   f"{', '.join(distinct)}. Likely a mid-flight "
                   f"package update without an initramfs rebuild + "
                   f"reboot. Mitigations may behave inconsistently."),
        "recommendation": recipe,
    }


def status(cfg=None) -> dict:
    text = _read(_CPUINFO)
    if text is None:
        return {"ok": False, "error": "cpuinfo_unavailable",
                "reason": f"{_CPUINFO} not readable."}
    info = parse_cpuinfo(text)
    sys_version = _read(os.path.join(_SYS_MICROCODE, "version"))
    if sys_version:
        sys_version = sys_version.strip()
    proc_flags = _read(os.path.join(_SYS_MICROCODE,
                                          "processor_flags"))
    if proc_flags:
        proc_flags = proc_flags.strip()
    sys_microcode_present = os.path.isdir(_SYS_MICROCODE)
    verdict = classify(info["microcodes"], info["vendor_id"])
    return {
        "ok": True,
        "cpu_count": len(info["microcodes"]),
        "vendor_id": info["vendor_id"],
        "cpu_family": info["cpu_family"],
        "model": info["model"],
        "model_name": info["model_name"],
        "microcodes": info["microcodes"],
        "distinct_microcodes": sorted(set(info["microcodes"])),
        "sys_microcode_version": sys_version,
        "sys_processor_flags": proc_flags,
        "sys_microcode_dir_present": sys_microcode_present,
        "verdict": verdict,
    }
