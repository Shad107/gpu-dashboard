"""Module iommu_reserved_regions_audit — IOMMU group type +
reserved_regions auditor (R&D #88.3).

Two existing modules cover the IOMMU presence/group surface :

  * iommu_groups (R&D #30.2) — GPU's group + chipset sharing
  * iommu_groups_audit (R&D #59.2) — group count + passthrough
                                     cmdline + many_groups_ok

Neither reads `/sys/kernel/iommu_groups/<gid>/type` nor
`/sys/kernel/iommu_groups/<gid>/reserved_regions`. This
audit owns those two files.

`type` determines how DMA from devices in the group is
translated :

  DMA       — translation enabled, default on older kernels.
  DMA-FQ    — translation enabled with flush queue (lower
              flush latency, default on 6.x).
  identity  — passthrough (no translation) — defeats DMA
              isolation, only used intentionally for VFIO.

`reserved_regions` lists IOVA ranges the IOMMU has carved
out of the device's address space. Format (one per line) :

  0xSTART 0xEND <type>   where <type> is one of:
    direct, msi, reserved, direct-relaxable

Reads :

  /sys/kernel/iommu_groups/<gid>/type
  /sys/kernel/iommu_groups/<gid>/reserved_regions
  /sys/bus/pci/devices/*/iommu_group   (symlink — find GPUs)
  /sys/bus/pci/devices/*/class         (0x030000 = VGA)
  /proc/cmdline                        (iommu enable tokens)

Verdicts (worst-first) :

  iommu_off_but_groups_present  err   groups dir non-empty yet
                                      cmdline lacks iommu enable
  direct_map_on_gpu_group       warn  GPU's group type=identity
                                      — DMA isolation defeated
  reserved_region_overlap_msi   warn  any group has overlapping
                                      'direct' and 'msi' regions
  dma_type_default              accent type=DMA on a 6.x kernel
                                      — DMA-FQ would lower flush
                                      latency
  iommu_dma_fq_ok               ok    all groups DMA-FQ
  unknown                       iommu_groups dir absent/empty.

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import Optional

NAME = "iommu_reserved_regions_audit"

DEFAULT_IOMMU_ROOT = "/sys/kernel/iommu_groups"
DEFAULT_PCI_ROOT = "/sys/bus/pci/devices"
DEFAULT_PROC_CMDLINE = "/proc/cmdline"

_IOMMU_ENABLE_TOKENS = (
    "intel_iommu=on", "amd_iommu=on", "iommu=pt", "iommu=on")

# /sys/bus/pci device class 0x030000 = VGA controller ;
# 0x030200 = 3D controller (compute-only NVIDIA cards).
_GPU_CLASS_PREFIXES = ("0x030000", "0x030200")


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except (OSError, PermissionError):
        return None


def _read_int_hex(path: str) -> Optional[int]:
    t = _read_text(path)
    if not t:
        return None
    try:
        return int(t.strip(), 16)
    except ValueError:
        return None


def list_groups(iommu_root: str) -> list:
    if not os.path.isdir(iommu_root):
        return []
    try:
        return sorted(os.listdir(iommu_root),
                      key=lambda n: int(n) if n.isdigit() else 0)
    except OSError:
        return []


def read_group_type(iommu_root: str, gid: str) -> str:
    t = _read_text(os.path.join(iommu_root, gid, "type"))
    return (t or "").strip()


def parse_reserved_regions(text: str) -> list:
    """Return [(start, end, type_str), ...] parsed from one
    /sys/kernel/iommu_groups/<g>/reserved_regions file."""
    if not text:
        return []
    out: list = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        try:
            start = int(parts[0], 16)
            end = int(parts[1], 16)
        except ValueError:
            continue
        out.append((start, end, parts[2]))
    return out


def read_reserved_regions(iommu_root: str, gid: str) -> list:
    text = _read_text(
        os.path.join(iommu_root, gid, "reserved_regions"))
    return parse_reserved_regions(text or "")


def find_gpu_groups(pci_root: str) -> set:
    """Resolve PCI VGA/3D devices to their IOMMU group IDs."""
    out: set = set()
    if not os.path.isdir(pci_root):
        return out
    try:
        bdfs = os.listdir(pci_root)
    except OSError:
        return out
    for bdf in bdfs:
        cls = _read_text(
            os.path.join(pci_root, bdf, "class")) or ""
        if not any(cls.strip().startswith(p)
                   for p in _GPU_CLASS_PREFIXES):
            continue
        link = os.path.join(pci_root, bdf, "iommu_group")
        try:
            target = os.readlink(link)
        except OSError:
            continue
        gid = os.path.basename(target)
        if gid:
            out.add(gid)
    return out


def parse_cmdline(text: str) -> list:
    return text.split() if text else []


def cmdline_has_iommu(tokens: list) -> bool:
    return any(any(tok.startswith(t) for t in tokens)
               for tokens in [_IOMMU_ENABLE_TOKENS])  # noqa


def _enabled(tokens: list) -> bool:
    return any(t in tokens for t in _IOMMU_ENABLE_TOKENS)


def regions_overlap_direct_msi(regions: list) -> bool:
    directs = [(s, e) for s, e, t in regions if t == "direct"]
    msis = [(s, e) for s, e, t in regions if t == "msi"]
    for ds, de in directs:
        for ms, me in msis:
            if ds <= me and ms <= de:
                return True
    return False


def classify(groups: list, gpu_gids: set,
             group_types: dict, group_regions: dict,
             cmdline_tokens: list) -> dict:
    if not groups:
        return {"verdict": "unknown",
                "reason": (
                    "/sys/kernel/iommu_groups absent or empty "
                    "— IOMMU not enabled or no hw support.")}

    if not _enabled(cmdline_tokens):
        return {
            "verdict": "iommu_off_but_groups_present",
            "reason": (
                f"{len(groups)} IOMMU group(s) present but "
                "/proc/cmdline lacks any iommu enable token "
                "({intel_iommu,amd_iommu}=on or iommu=pt). "
                "Stale sysfs state — reboot to apply boot "
                "settings."),
            "group_count": len(groups)}

    for gid in gpu_gids:
        if group_types.get(gid) == "identity":
            return {
                "verdict": "direct_map_on_gpu_group",
                "reason": (
                    f"GPU's IOMMU group {gid} has "
                    "type=identity — DMA isolation defeated "
                    "(intentional only for VFIO passthrough)."),
                "gpu_group_id": gid}

    for gid in groups:
        if regions_overlap_direct_msi(
                group_regions.get(gid, [])):
            return {
                "verdict": "reserved_region_overlap_msi",
                "reason": (
                    f"IOMMU group {gid} has overlapping "
                    "'direct' and 'msi' reserved regions — "
                    "MSI delivery may misbehave under "
                    "passthrough."),
                "group_id": gid}

    plain_dma = [gid for gid in groups
                 if group_types.get(gid) == "DMA"]
    if plain_dma:
        return {
            "verdict": "dma_type_default",
            "reason": (
                f"{len(plain_dma)} group(s) use type=DMA — "
                "DMA-FQ would lower IOTLB flush latency on "
                "6.x kernels. Set 'iommu.strict=0' on "
                "cmdline to enable flush queue."),
            "dma_group_count": len(plain_dma)}

    return {"verdict": "iommu_dma_fq_ok",
            "reason": (
                f"{len(groups)} IOMMU group(s) — all "
                "DMA-FQ ; reserved regions coherent.")}


def status(config: Optional[dict] = None,
           iommu_root: str = DEFAULT_IOMMU_ROOT,
           pci_root: str = DEFAULT_PCI_ROOT,
           proc_cmdline: str = DEFAULT_PROC_CMDLINE) -> dict:
    groups = list_groups(iommu_root)
    group_types: dict = {}
    group_regions: dict = {}
    for gid in groups:
        group_types[gid] = read_group_type(iommu_root, gid)
        group_regions[gid] = read_reserved_regions(
            iommu_root, gid)
    gpu_gids = find_gpu_groups(pci_root)
    cmdline = parse_cmdline(_read_text(proc_cmdline) or "")
    verdict = classify(groups, gpu_gids, group_types,
                       group_regions, cmdline)
    return {
        "ok": verdict["verdict"] == "iommu_dma_fq_ok",
        "group_count": len(groups),
        "gpu_group_count": len(gpu_gids),
        "verdict": verdict,
    }
