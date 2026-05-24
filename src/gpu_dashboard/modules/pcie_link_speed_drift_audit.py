"""Module pcie_link_speed_drift_audit — per-device PCIe link
speed/width drift for NVMe + peripherals (R&D #89.4).

CRITICAL scope check : pcie_width_watcher (R&D #?.?) already
owns the NVIDIA-GPU current_link_{speed,width} vs max axis
(downgraded_width / downgraded_speed / downgraded_both).
This audit DELIBERATELY skips NVIDIA GPUs (class 0x0300xx)
and owns the orthogonal surface :

  * NVMe controllers (class 0x010802) — the second most
    bandwidth-sensitive PCI device on a single-GPU homelab.
    A 3090 dropping to Gen3 x8 is caught by
    pcie_width_watcher. A 4 TB NVMe dropping to Gen3 x2
    because a PCIe ribbon shifted is just as common and
    nobody currently surfaces it.
  * "Peripheral" devices — network cards, capture cards,
    USB controllers, audio interfaces. A Realtek 2.5G NIC
    talking PCIe Gen1 x1 instead of Gen2 x1 explains 1.2
    Gbps throughput-cap mysteries.

Reads :

  /sys/bus/pci/devices/<bdf>/current_link_speed   "16.0 GT/s"
  /sys/bus/pci/devices/<bdf>/current_link_width   integer
  /sys/bus/pci/devices/<bdf>/max_link_speed       "16.0 GT/s"
  /sys/bus/pci/devices/<bdf>/max_link_width       integer
  /sys/bus/pci/devices/<bdf>/class                "0xXXXXXX"

Verdicts (worst-first) :

  nvme_link_downgraded        warn   NVMe device with
                                     current < max on either
                                     axis.
  peripheral_link_downgraded  accent any non-GPU non-NVMe
                                     device with current <
                                     max.
  links_at_max                ok
  unknown                     /sys/bus/pci/devices empty.

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import Optional

NAME = "pcie_link_speed_drift_audit"

DEFAULT_PCI_ROOT = "/sys/bus/pci/devices"

# Class prefixes :
# 0x0300xx  VGA/display controller (NVIDIA GPU under
#           pcie_width_watcher).
# 0x0302xx  3D controller (compute-only NVIDIA).
# 0x0108xx  NVMe controller (this module's primary signal).
# 0x06xxxx  PCI bridge family (host/PCI/ISA/PCMCIA/CardBus).
#           Bridges/root ports advertise wider links than
#           the negotiated downstream width — NOT a fault.
_GPU_CLASS_PREFIXES = ("0x030000", "0x030200")
_NVME_CLASS_PREFIX = "0x010802"
_BRIDGE_CLASS_PREFIX = "0x06"

_SPEED_RE = re.compile(r"([\d.]+)\s*GT/s")


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read().strip()
    except (OSError, PermissionError):
        return None


def parse_speed_gts(text: str) -> Optional[float]:
    """Parse '16.0 GT/s PCIe' → 16.0."""
    if not text:
        return None
    m = _SPEED_RE.search(text)
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def parse_width(text: str) -> Optional[int]:
    if not text:
        return None
    try:
        return int(text.strip())
    except ValueError:
        return None


def read_device(pci_root: str, bdf: str) -> dict:
    base = os.path.join(pci_root, bdf)
    return {
        "bdf": bdf,
        "class": _read_text(os.path.join(base, "class")) or "",
        "current_speed_gts": parse_speed_gts(
            _read_text(os.path.join(
                base, "current_link_speed")) or ""),
        "max_speed_gts": parse_speed_gts(
            _read_text(os.path.join(
                base, "max_link_speed")) or ""),
        "current_width": parse_width(
            _read_text(os.path.join(
                base, "current_link_width")) or ""),
        "max_width": parse_width(
            _read_text(os.path.join(
                base, "max_link_width")) or ""),
    }


def list_devices(pci_root: str = DEFAULT_PCI_ROOT) -> list:
    if not os.path.isdir(pci_root):
        return []
    try:
        bdfs = os.listdir(pci_root)
    except OSError:
        return []
    return sorted(bdfs)


def _is_gpu(cls: str) -> bool:
    return any(cls.startswith(p) for p in _GPU_CLASS_PREFIXES)


def _is_nvme(cls: str) -> bool:
    return cls.startswith(_NVME_CLASS_PREFIX)


def _is_bridge(cls: str) -> bool:
    return cls.startswith(_BRIDGE_CLASS_PREFIX)


def _has_link(d: dict) -> bool:
    """A device has a PCIe link if all four sysfs values
    parsed successfully. Many bridges/virtio devices expose
    the files but their values are zero or unparseable."""
    return all(d.get(k) for k in (
        "current_speed_gts", "max_speed_gts",
        "current_width", "max_width"))


def _is_degraded(d: dict) -> bool:
    return (d["current_speed_gts"] < d["max_speed_gts"]
            or d["current_width"] < d["max_width"])


def classify(devices: list) -> dict:
    linked = [d for d in devices if _has_link(d)]
    if not linked:
        return {"verdict": "unknown",
                "reason": (
                    "/sys/bus/pci/devices yielded no devices "
                    "with a readable PCIe link (virtio / "
                    "minimal kernel).")}

    nvme_bad = [
        d for d in linked
        if _is_nvme(d["class"]) and _is_degraded(d)]
    if nvme_bad:
        names = [d["bdf"] for d in nvme_bad]
        return {
            "verdict": "nvme_link_downgraded",
            "reason": (
                f"{len(nvme_bad)} NVMe device(s) running below "
                f"max PCIe link: {names}. Reseat the M.2 slot "
                "or check BIOS bifurcation."),
            "devices": names}

    peri_bad = [
        d for d in linked
        if not _is_gpu(d["class"])
        and not _is_nvme(d["class"])
        and not _is_bridge(d["class"])
        and _is_degraded(d)]
    if peri_bad:
        names = [d["bdf"] for d in peri_bad]
        return {
            "verdict": "peripheral_link_downgraded",
            "reason": (
                f"{len(peri_bad)} non-GPU non-NVMe PCIe "
                "device(s) below max link "
                f"(e.g. {names[:3]}). Often a ribbon / "
                "riser issue rather than thermal."),
            "devices": names}

    return {"verdict": "links_at_max",
            "reason": (
                f"{len(linked)} PCIe device(s) inspected "
                "(GPUs excluded — owned by "
                "pcie_width_watcher) ; all NVMe + peripheral "
                "links at max speed/width.")}


def status(config: Optional[dict] = None,
           pci_root: str = DEFAULT_PCI_ROOT) -> dict:
    bdfs = list_devices(pci_root)
    devices = [read_device(pci_root, bdf) for bdf in bdfs]
    verdict = classify(devices)
    return {
        "ok": verdict["verdict"] == "links_at_max",
        "device_count": len(devices),
        "linked_count": sum(1 for d in devices if _has_link(d)),
        "verdict": verdict,
    }
