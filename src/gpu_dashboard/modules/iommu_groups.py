"""Module iommu_groups — IOMMU group + DMA-passthrough auditor (R&D #30.2).

Shipped vfio_sentinel handles the *post-passthrough* state (is vfio-pci
actually bound, is the GPU detached from nvidia properly). It cannot
tell you, ahead of time, whether your GPU can even be passed through
cleanly. The classic foot-gun:

    > "I built my dual-3090 LLM rig, tried to vfio-pci one of the cards,
    >  libvirt errored out — turns out my GPU shares an IOMMU group with
    >  the chipset USB / SATA controller, so passing the GPU requires
    >  passing the whole group (or patching the kernel with
    >  pcie_acs_override)."

This module walks /sys/kernel/iommu_groups/*/devices/, locates each
NVIDIA GPU's group, enumerates its siblings, and emits one of:

  - clean          GPU alone (or with parent bridge + its own onboard
                    HDA audio function .1) — passthrough-friendly
  - chipset_shared GPU shares group with USB / SATA / NVMe / Audio /
                    Network from the chipset — needs ACS override
  - unknown        cannot read iommu_groups for this BDF

When iommu_groups/ doesn't exist at all, we surface a distinct
error="iommu_disabled" with the GRUB cmdline fix.

Pure sysfs. stdlib only.
"""
from __future__ import annotations

import os
from typing import Optional


NAME = "iommu_groups"


_IOMMU_ROOT = "/sys/kernel/iommu_groups"
_PCI_ROOT = "/sys/bus/pci/devices"


def list_groups(root: str = _IOMMU_ROOT) -> list[int]:
    try:
        names = os.listdir(root)
    except OSError:
        return []
    out: list[int] = []
    for n in names:
        try:
            out.append(int(n))
        except ValueError:
            continue
    return sorted(out)


def list_devices_in_group(root: str, gnum: int) -> list[str]:
    p = os.path.join(root, str(gnum), "devices")
    try:
        return list(os.listdir(p))
    except OSError:
        return []


def find_nvidia_bdfs(pci_root: str = _PCI_ROOT) -> list[str]:
    """Return NVIDIA VGA controllers (class 0x03xxxx). The GPU's onboard
    HDA audio function (class 0x040300) is filtered out — same physical
    card, but for passthrough auditing we anchor on the VGA function."""
    out: list[str] = []
    try:
        for n in sorted(os.listdir(pci_root)):
            vp = os.path.join(pci_root, n, "vendor")
            cp = os.path.join(pci_root, n, "class")
            try:
                with open(vp) as f:
                    if f.read().strip().lower() != "0x10de":
                        continue
                with open(cp) as f:
                    klass = f.read().strip().lower()
                if klass.startswith("0x03"):
                    out.append(n)
            except OSError:
                continue
    except OSError:
        return []
    return out


def find_group_for_bdf(iommu_root: str, bdf: str) -> Optional[int]:
    for gn in list_groups(iommu_root):
        if bdf in list_devices_in_group(iommu_root, gn):
            return gn
    return None


# PCI class-code → human kind. Only the prefix matters for most.
_KIND_PREFIX = {
    "0x0301": "VGA",
    "0x0300": "VGA",
    "0x0302": "VGA",
    "0x0c03": "USB",
    "0x0106": "SATA",
    "0x0108": "NVMe",
    "0x0403": "Audio",
    "0x0401": "Audio",
    "0x0200": "Network",
    "0x0280": "Network",
    "0x0604": "Bridge",
    "0x0600": "Bridge",
    "0x0601": "Bridge",
    "0x0608": "Bridge",
}


def device_kind(pci_root: str, bdf: str) -> str:
    p = os.path.join(pci_root, bdf, "class")
    try:
        with open(p) as f:
            klass = f.read().strip().lower()
    except OSError:
        return "Unknown"
    if not klass.startswith("0x") or len(klass) < 6:
        return "Other"
    prefix = klass[:6]  # 0xCCSS — class+subclass
    return _KIND_PREFIX.get(prefix, "Other")


def _is_same_card(gpu_bdf: str, sib_bdf: str) -> bool:
    """The GPU's own audio function (.1 of same BDF) doesn't count."""
    return gpu_bdf.rsplit(".", 1)[0] == sib_bdf.rsplit(".", 1)[0]


def classify(gpu_bdf: str, siblings: list) -> dict:
    foreign = [s for s in siblings
               if s["kind"] != "Bridge"
               and not _is_same_card(gpu_bdf, s["bdf"])]
    if not foreign:
        return {
            "verdict": "clean",
            "reason": ("GPU is alone in its IOMMU group (modulo its own "
                       "PCIe bridge and onboard HDA audio function) — "
                       "VFIO passthrough will work as-is."),
            "recommendation": "",
        }
    kinds = sorted({s["kind"] for s in foreign})
    sib_descs = ", ".join(f"{s['bdf']} ({s['kind']})" for s in foreign)
    return {
        "verdict": "chipset_shared",
        "reason": (f"GPU shares its IOMMU group with non-GPU chipset "
                   f"devices: {sib_descs}. Passing the GPU to a VM "
                   f"requires passing the whole group, which would also "
                   f"detach your {', '.join(kinds)} from the host."),
        "recommendation": (
            "# Add to your GRUB cmdline (then update-grub + reboot):\n"
            "GRUB_CMDLINE_LINUX_DEFAULT=\"... "
            "pcie_acs_override=downstream,multifunction\"\n"
            "# Trade-off: pcie_acs_override is an out-of-tree-style "
            "workaround. Lone-GPU groups are always preferable; if "
            "your board offers it, replug the GPU into a slot wired "
            "directly to the CPU root complex (not via chipset)."
        ),
    }


_RANK = {"clean": 0, "unknown": 1, "chipset_shared": 2}


def status(cfg=None) -> dict:
    if not os.path.isdir(_IOMMU_ROOT):
        return {
            "ok": False,
            "error": "iommu_disabled",
            "reason": ("/sys/kernel/iommu_groups/ is absent — your kernel "
                       "is not running with intel_iommu=on (Intel) or "
                       "amd_iommu=on iommu=pt (AMD). Without IOMMU, no "
                       "VFIO passthrough is possible."),
            "recommendation": (
                "# Add to GRUB cmdline then update-grub + reboot:\n"
                "# Intel:  intel_iommu=on iommu=pt\n"
                "# AMD:    amd_iommu=on iommu=pt"
            ),
        }
    nvidia_bdfs = find_nvidia_bdfs(_PCI_ROOT)
    if not nvidia_bdfs:
        return {"ok": True, "device_count": 0,
                "cards": [], "worst_verdict": "no_gpus"}
    cards: list = []
    worst = "clean"
    for gpu in nvidia_bdfs:
        gnum = find_group_for_bdf(_IOMMU_ROOT, gpu)
        if gnum is None:
            cards.append({
                "gpu_bdf": gpu,
                "iommu_group": None,
                "siblings": [],
                "verdict": {
                    "verdict": "unknown",
                    "reason": (f"GPU {gpu} is not present in any "
                                f"IOMMU group — sysfs out of sync."),
                    "recommendation": "",
                },
            })
            if _RANK.get("unknown", 0) > _RANK.get(worst, 0):
                worst = "unknown"
            continue
        members = list_devices_in_group(_IOMMU_ROOT, gnum)
        sibs = [
            {"bdf": m, "kind": device_kind(_PCI_ROOT, m)}
            for m in sorted(members) if m != gpu
        ]
        v = classify(gpu, sibs)
        if _RANK.get(v["verdict"], 0) > _RANK.get(worst, 0):
            worst = v["verdict"]
        cards.append({
            "gpu_bdf": gpu,
            "iommu_group": gnum,
            "siblings": sibs,
            "verdict": v,
        })
    return {"ok": True, "device_count": len(cards),
            "cards": cards, "worst_verdict": worst}
