"""Module cmdline_audit — /proc/cmdline boot-param auditor (R&D #39.1).

The kernel command line is set in GRUB / systemd-boot / similar and
fixed for the life of the boot. Misconfigured cmdline can:

  - Disable Spectre/MDS mitigations (`mitigations=off`) — explicit
    perf-vs-security trade-off, surface to the user.
  - Disable SMT/hyperthreading (`nosmt`) — sometimes intentional
    for inference, sometimes a leftover from a security paper.
  - Override CPU governor (`intel_pstate=disable`,
    `cpufreq.default_governor=performance`) — fights with sysfs
    audits from #35.1 / #36.4 / #36.2.
  - Pin CPUs out of the scheduler (`isolcpus=8-15`,
    `nohz_full=8-15`) — explicit topology hint.
  - Force THP (`transparent_hugepage=always`) — perf hint that
    bypasses #34.1 thp_audit's runtime check.
  - Disable MSI (`pci=nomsi`) — explains #30.1 msi_inventory
    returning legacy mode.

This module parses /proc/cmdline into key=value pairs, categorizes
each "interesting" flag, and emits:

  clean              no notable flags
  perf_tuned        ≥1 perf-oriented flag (isolcpus, idle=poll,
                     hugepage=always, intel_pstate=passive)
  safety_disabled    mitigations=off OR nosmt — explicit trade-off
                     ; surface so the user knows the cost
  power              CPU power-related flags
  virt_pinning       pci=nomsi or similar
  unknown            sysfs unreadable

stdlib only.
"""
from __future__ import annotations

import os
import re
import shlex
from typing import Optional


NAME = "cmdline_audit"


_CMDLINE_PATH = "/proc/cmdline"


def parse_cmdline(text: str) -> dict:
    if not text:
        return {}
    out: dict = {}
    try:
        tokens = shlex.split(text.strip())
    except ValueError:
        tokens = text.strip().split()
    for tok in tokens:
        if "=" in tok:
            k, v = tok.split("=", 1)
            out[k] = v
        else:
            out[tok] = True
    return out


_BORING = {
    "BOOT_IMAGE", "root", "ro", "rw", "rd.fstab", "init", "rootflags",
    "quiet", "splash", "vt.handoff", "vt_handoff",
    "console", "log_level", "loglevel",
    "crashkernel", "resume", "resume_offset",
    "rootfstype", "fsck.repair", "fsck.mode",
}


_SAFETY_KEYS = {
    "mitigations": {"off", "auto,nosmt"},
    "nosmt": True,
    "spectre_v2_user": {"off"},
    "spec_store_bypass_disable": {"off"},
    "spectre_v2": {"off"},
    "noxsave": True,
    "noexec": True,  # informational
}

_PERF_KEYS = {
    "isolcpus": "any",
    "nohz_full": "any",
    "rcu_nocbs": "any",
    "idle": {"poll"},
    "transparent_hugepage": {"always"},
    "hugepagesz": "any",
    "default_hugepagesz": "any",
    "processor.max_cstate": "any",
    "intel_idle.max_cstate": "any",
}

_POWER_KEYS = {
    "intel_pstate": {"disable", "passive"},
    "amd_pstate": {"disable", "passive"},
    "cpufreq.default_governor": "any",
    "cpuidle.off": True,
    "nohz": "any",
}

_VIRT_KEYS = {
    "pci": {"nomsi"},
    "iommu": "any",
    "intel_iommu": "any",
    "amd_iommu": "any",
}


def _match(value, spec) -> bool:
    if spec is True:
        return True
    if spec == "any":
        return True
    if isinstance(spec, set):
        return str(value) in spec
    return False


def categorize_flags(flags: dict) -> dict:
    out = {"safety_disabled": [], "perf_oriented": [],
           "power": [], "virt_pinning": []}
    for k, v in flags.items():
        if k in _BORING:
            continue
        if k in _SAFETY_KEYS and _match(v, _SAFETY_KEYS[k]):
            out["safety_disabled"].append({"key": k, "value": v})
        if k in _PERF_KEYS and _match(v, _PERF_KEYS[k]):
            out["perf_oriented"].append({"key": k, "value": v})
        if k in _POWER_KEYS and _match(v, _POWER_KEYS[k]):
            out["power"].append({"key": k, "value": v})
        if k in _VIRT_KEYS and _match(v, _VIRT_KEYS[k]):
            out["virt_pinning"].append({"key": k, "value": v})
    return out


_RECIPE_SAFETY = (
    "# Your boot cmdline disables CPU mitigations. Trade-off is\n"
    "# explicit ; companion to #37.1 cpu_vulns. To re-enable:\n"
    "# Edit /etc/default/grub, remove `mitigations=off`/`nosmt`\n"
    "# from GRUB_CMDLINE_LINUX_DEFAULT, then:\n"
    "sudo update-grub && reboot"
)

_RECIPE_PERF = (
    "# Cmdline has perf-oriented flags. Worth knowing for diag:\n"
    "# - `isolcpus` pins CPUs out of the scheduler (companion to\n"
    "#   #37.2 gpu_cpu_affinity)\n"
    "# - `idle=poll` keeps the CPU in C0 (companion to #36.2 cpuidle)\n"
    "# - `transparent_hugepage=always` (companion to #34.1 thp_audit)\n"
    "# Verify each is intentional. To roll back, edit /etc/default/grub."
)


def classify(categories: dict) -> dict:
    if categories.get("safety_disabled"):
        keys = [f["key"] for f in categories["safety_disabled"]]
        return {"verdict": "safety_disabled",
                "reason": (f"Boot cmdline contains safety-disabling "
                           f"flag(s): {', '.join(keys)}. Companion to "
                           f"#37.1 cpu_vulns — this is the upstream "
                           f"toggle for those mitigations."),
                "recommendation": _RECIPE_SAFETY}
    if categories.get("perf_oriented"):
        keys = [f["key"] for f in categories["perf_oriented"]]
        return {"verdict": "perf_tuned",
                "reason": (f"Boot cmdline has perf-oriented flag(s): "
                           f"{', '.join(keys)}. Informational — "
                           f"explains overrides seen by other audit "
                           f"modules."),
                "recommendation": _RECIPE_PERF}
    if categories.get("power") or categories.get("virt_pinning"):
        keys = []
        for cat in ("power", "virt_pinning"):
            keys += [f["key"] for f in categories.get(cat, [])]
        return {"verdict": "power_or_virt",
                "reason": (f"Boot cmdline has power/virt tuning: "
                           f"{', '.join(keys)}. Informational."),
                "recommendation": ""}
    return {"verdict": "clean",
            "reason": ("Boot cmdline contains only stock distro "
                       "boot params (root, ro, crashkernel, etc.)."),
            "recommendation": ""}


def _read(p: str) -> Optional[str]:
    try:
        with open(p) as f:
            return f.read().strip()
    except OSError:
        return None


def status(cfg=None) -> dict:
    raw = _read(_CMDLINE_PATH)
    if raw is None:
        return {"ok": False, "error": "cmdline_unavailable",
                "reason": f"{_CMDLINE_PATH} not readable."}
    flags = parse_cmdline(raw)
    categories = categorize_flags(flags)
    verdict = classify(categories)
    return {
        "ok": True,
        "raw": raw,
        "flags": flags,
        "categories": categories,
        "verdict": verdict,
    }
