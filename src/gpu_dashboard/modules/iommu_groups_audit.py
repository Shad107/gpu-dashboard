"""Module iommu_groups_audit — IOMMU groups + passthrough (R&D #59.2).

Reads :
  /sys/kernel/iommu_groups/<gid>/devices/<bdf>
  /sys/class/iommu/<name>/
  /proc/cmdline                  intel_iommu= / amd_iommu= / iommu=pt

Why this matters on an LLM rig :

* IOMMU off → no VFIO passthrough for a future LLM worker VM,
  *and* the DMA-API debug overhead is paid on every NVMe / GPU
  transfer.
* `iommu=pt` (pass-through) is the recommended mode on a host
  that doesn't actually need IOMMU isolation between bare-metal
  PCI devices — slightly cheaper DMA.
* GPU sharing an IOMMU group with the chipset / root complex
  means vfio-pci will refuse to bind for passthrough.

Verdicts (priority-ordered) :
  iommu_disabled                  /sys/kernel/iommu_groups is
                                  absent or empty.
  passthrough_off                 IOMMU on but no `iommu=pt` in
                                  /proc/cmdline.
  gpu_shares_group_with_root_complex
                                  GPU's IOMMU group contains a
                                  host bridge / root complex (PCI
                                  base class 0x06).
  many_groups_ok                  ≥ 10 distinct IOMMU groups (a
                                  laptop chipset is well isolated).
  ok                              IOMMU on, GPU in its own group.
  unknown                         /sys/kernel/iommu_groups
                                  unreadable.

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import Dict, List, Optional


NAME = "iommu_groups_audit"


_SYS_IOMMU_GROUPS = "/sys/kernel/iommu_groups"
_SYS_CLASS_IOMMU = "/sys/class/iommu"
_PROC_CMDLINE = "/proc/cmdline"
_SYS_PCI = "/sys/bus/pci/devices"


_NVIDIA_VENDOR = "0x10de"
_DISPLAY_BASE_CLASS = 0x03


def _read(p: str) -> Optional[str]:
    try:
        with open(p) as f:
            return f.read().strip()
    except OSError:
        return None


def list_groups(sys_iommu: str = _SYS_IOMMU_GROUPS
                  ) -> Dict[int, List[str]]:
    """Returns {group_id: [bdf, ...]}."""
    if not os.path.isdir(sys_iommu):
        return {}
    out: Dict[int, List[str]] = {}
    for name in sorted(os.listdir(sys_iommu)):
        if not name.isdigit():
            continue
        gid = int(name)
        dev_dir = os.path.join(sys_iommu, name, "devices")
        if not os.path.isdir(dev_dir):
            out[gid] = []
            continue
        out[gid] = sorted(os.listdir(dev_dir))
    return out


def cmdline_iommu_tokens(proc_cmdline: str = _PROC_CMDLINE
                            ) -> List[str]:
    text = _read(proc_cmdline)
    if not text:
        return []
    tokens = text.split()
    return [t for t in tokens if "iommu" in t.lower()]


def find_nvidia_gpus(sys_pci: str = _SYS_PCI) -> List[str]:
    if not os.path.isdir(sys_pci):
        return []
    out: List[str] = []
    for bdf in sorted(os.listdir(sys_pci)):
        ddir = os.path.join(sys_pci, bdf)
        vendor = _read(os.path.join(ddir, "vendor"))
        klass = _read(os.path.join(ddir, "class"))
        if vendor != _NVIDIA_VENDOR or not klass:
            continue
        try:
            base = (int(klass, 16) >> 16) & 0xff
        except ValueError:
            continue
        if base == _DISPLAY_BASE_CLASS:
            out.append(bdf)
    return out


def device_class(bdf: str,
                    sys_pci: str = _SYS_PCI) -> Optional[int]:
    raw = _read(os.path.join(sys_pci, bdf, "class"))
    if not raw:
        return None
    try:
        return int(raw, 16)
    except ValueError:
        return None


def classify(groups: Dict[int, List[str]],
              iommu_tokens: List[str],
              nvidia_gpus: List[str],
              gpu_groups: Dict[str, int],
              group_classes: Dict[int, List[int]]) -> dict:
    if not groups:
        return {"verdict": "iommu_disabled",
                "reason": ("/sys/kernel/iommu_groups absent or "
                          "empty — kernel built without IOMMU or "
                          "BIOS option disabled."),
                "recommendation": _recipe_enable_iommu()}

    has_pt = any("=pt" in t for t in iommu_tokens)
    if not has_pt:
        return {"verdict": "passthrough_off",
                "reason": ("IOMMU enabled but 'iommu=pt' missing in "
                          "kernel cmdline. DMA-API overhead is "
                          "paid on every transfer."),
                "recommendation": _recipe_iommu_pt()}

    # GPU sharing a group with a host bridge (PCI base class 0x06)
    bad_gpus = []
    for bdf, gid in gpu_groups.items():
        siblings = [c for c in group_classes.get(gid, [])
                       if ((c >> 16) & 0xff) == 0x06]
        if siblings:
            bad_gpus.append(f"{bdf}->group{gid}")
    if bad_gpus:
        return {"verdict": "gpu_shares_group_with_root_complex",
                "reason": (f"NVIDIA GPU(s) share IOMMU group with a "
                          f"host/root bridge : {bad_gpus[0]}. "
                          f"vfio-pci will refuse passthrough."),
                "recommendation": _recipe_acs_override()}

    if len(groups) >= 10:
        return {"verdict": "many_groups_ok",
                "reason": (f"{len(groups)} IOMMU groups — chipset "
                          f"is well isolated. VFIO passthrough "
                          f"will pick clean candidates."),
                "recommendation": ""}

    return {"verdict": "ok",
            "reason": (f"{len(groups)} IOMMU groups, iommu=pt "
                      f"active."),
            "recommendation": ""}


def status(config=None,
            sys_iommu: str = _SYS_IOMMU_GROUPS,
            proc_cmdline: str = _PROC_CMDLINE,
            sys_pci: str = _SYS_PCI) -> dict:
    groups = list_groups(sys_iommu)
    iommu_tokens = cmdline_iommu_tokens(proc_cmdline)
    nvidia_gpus = find_nvidia_gpus(sys_pci)
    # Map GPU BDF → group id
    gpu_groups: Dict[str, int] = {}
    for gid, bdfs in groups.items():
        for b in bdfs:
            if b in nvidia_gpus:
                gpu_groups[b] = gid
    # Resolve class hex for every device in every group
    group_classes: Dict[int, List[int]] = {}
    for gid, bdfs in groups.items():
        classes: List[int] = []
        for b in bdfs:
            c = device_class(b, sys_pci)
            if c is not None:
                classes.append(c)
        group_classes[gid] = classes
    ok = bool(groups)
    verdict = classify(groups, iommu_tokens, nvidia_gpus,
                          gpu_groups, group_classes)
    return {"ok": ok,
              "group_count": len(groups),
              "groups_sample": dict(list(groups.items())[:8]),
              "iommu_cmdline_tokens": iommu_tokens,
              "nvidia_gpus": nvidia_gpus,
              "gpu_groups": gpu_groups,
              "verdict": verdict}


# ── recovery recipes ────────────────────────────────────────────

def _recipe_enable_iommu() -> str:
    return ("# Enable IOMMU in BIOS + kernel cmdline :\n"
            "# Intel platforms :\n"
            "#   add intel_iommu=on iommu=pt to GRUB_CMDLINE_LINUX\n"
            "# AMD platforms :\n"
            "#   add amd_iommu=on iommu=pt to GRUB_CMDLINE_LINUX\n"
            "sudo nano /etc/default/grub\n"
            "sudo update-grub && sudo reboot\n")


def _recipe_iommu_pt() -> str:
    return ("# Add `iommu=pt` for cheaper DMA on a host that\n"
            "# isn't doing per-device isolation between bare-metal\n"
            "# devices :\n"
            "sudo sed -i 's/\\(GRUB_CMDLINE_LINUX=\"[^\"]*\\)\"/\\1 iommu=pt\"/' /etc/default/grub\n"
            "sudo update-grub && sudo reboot\n")


def _recipe_acs_override() -> str:
    return ("# Your GPU shares an IOMMU group with a host bridge.\n"
            "# Inspect the group :\n"
            "for g in /sys/kernel/iommu_groups/*/devices; do\n"
            "  echo \"Group $(basename $(dirname $g)) :\"\n"
            "  ls $g\n"
            "done | head -40\n"
            "# Mitigation : ACS-override patch (security tradeoff)\n"
            "# or move the GPU to a slot served by a different\n"
            "# root port.\n")
