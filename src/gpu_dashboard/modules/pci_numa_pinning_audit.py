"""Module pci_numa_pinning_audit — fleet-walk PCI device
numa_node mapping (R&D #109.4).

Existing modules (gpu_cpu_affinity, gpu_pci_bind) read numa_node
for the GPU BDF specifically. iomem_pci_audit reads it per-device
but its focus is BAR resources / reset_method, not NUMA distribution
or skew detection.

Reads :

  /sys/bus/pci/devices/*/numa_node
  /sys/devices/system/node/online            single vs multi-node

Verdicts (worst-first) :

  all_devices_node_minus_1_multinode_host  warn   Kernel sees > 1
                                                  NUMA node but
                                                  every PCI dev
                                                  maps to -1. ACPI
                                                  _PXM missing on
                                                  slots ; hot-path
                                                  NIC/NVMe will
                                                  cross-traverse.
  pci_numa_skew                            accent > 80% of PCI
                                                  devices on the
                                                  same node on a
                                                  multi-node host
                                                  — possible
                                                  unbalanced slot
                                                  layout.
  single_node_host                                ok (info) single-
                                                  socket.
  ok                                              healthy multi-
                                                  node distribution.
  requires_root                                   /sys/bus/pci
                                                  unreadable.
  unknown                                         no PCI bus.

stdlib only.
"""
from __future__ import annotations

import os
from typing import Optional

NAME = "pci_numa_pinning_audit"

DEFAULT_PCI_DEVS = "/sys/bus/pci/devices"
DEFAULT_NODE_ONLINE = "/sys/devices/system/node/online"


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except (OSError, PermissionError):
        return None


def _read_int(path: str) -> Optional[int]:
    t = _read_text(path)
    if t is None:
        return None
    try:
        return int(t.strip())
    except ValueError:
        return None


def is_multi_node(online_text: Optional[str]) -> bool:
    if not online_text:
        return False
    s = online_text.strip()
    return "-" in s or "," in s


def walk_pci_devices(pci_devs: str = DEFAULT_PCI_DEVS
                     ) -> dict:
    """Return {bdf: numa_node} for every PCI device."""
    out: dict = {}
    if not os.path.isdir(pci_devs):
        return out
    try:
        entries = sorted(os.listdir(pci_devs))
    except OSError:
        return out
    for bdf in entries:
        n = _read_int(
            os.path.join(pci_devs, bdf, "numa_node"))
        if n is not None:
            out[bdf] = n
    return out


def classify(pci_present: bool,
             devices: dict,
             multi_node: bool) -> dict:
    if not pci_present:
        return {"verdict": "unknown",
                "reason": (
                    "/sys/bus/pci/devices absent.")}
    if not devices:
        return {"verdict": "requires_root",
                "reason": (
                    "No numa_node files readable — "
                    "re-run as root.")}
    if not multi_node:
        return {"verdict": "ok",
                "reason": (
                    f"{len(devices)} PCI devices ; "
                    "single-node host — numa_node mapping "
                    "informational.")}

    # warn — all devices -1 on multi-node
    all_minus_1 = all(n == -1 for n in devices.values())
    if all_minus_1:
        return {
            "verdict": ("all_devices_node_minus_1_"
                        "multinode_host"),
            "reason": (
                f"{len(devices)} PCI device(s) all map to "
                "node=-1 on a multi-node host. ACPI _PXM "
                "missing for slots ; hot-path NIC/NVMe "
                "will cross-traverse for memory.")}

    # accent — skew (>80% same node)
    node_counts: dict = {}
    for n in devices.values():
        if n < 0:
            continue
        node_counts[n] = node_counts.get(n, 0) + 1
    if node_counts:
        total = sum(node_counts.values())
        max_node, max_count = max(
            node_counts.items(), key=lambda kv: kv[1])
        if total > 0 and max_count / total > 0.8:
            return {
                "verdict": "pci_numa_skew",
                "reason": (
                    f"{max_count}/{total} PCI devices on "
                    f"node {max_node} ({max_count / total:.0%}). "
                    "Slot layout heavily skewed — bias the "
                    "GPU / NVMe / NIC to balance.")}

    return {"verdict": "ok",
            "reason": (
                f"{len(devices)} PCI devices distributed "
                f"across {len(node_counts)} NUMA node(s).")}


def status(config: Optional[dict] = None,
           pci_devs: str = DEFAULT_PCI_DEVS,
           node_online: str = DEFAULT_NODE_ONLINE) -> dict:
    pci_present = os.path.isdir(pci_devs)
    devices = walk_pci_devices(pci_devs)
    multi = is_multi_node(_read_text(node_online))
    verdict = classify(pci_present, devices, multi)
    return {
        "ok": verdict["verdict"] == "ok",
        "device_count": len(devices),
        "multi_node": multi,
        "n_unpinned": sum(
            1 for n in devices.values() if n == -1),
        "verdict": verdict,
    }
