"""Module iomem_pci_audit — IOMEM + PCI BAR inventory (R&D #51.2).

Parses /proc/iomem + /proc/ioports for the host memory-region
inventory and walks /sys/bus/pci/devices/* for per-device BAR
resources, reset_method, numa_node, and driver-binding state.

Distinct from shipped #40.1 gpu_pci_bind (NVIDIA-only driver +
power-control inventory) and shipped #38.1 pcie_aer (AER counter
trends) — this is the *generic IOMEM region + PCI BAR / reset*
view that surfaces :

  - kptr_restrict masking of /proc/iomem (modern distros redact
    base addresses, only labels remain visible)
  - PCI devices with no driver bound (orphan)
  - reset_method support (flr / af_flr / pm / bus / device_specific)
    — important when shipped #28.7 gpu_reset wants to recover a
    wedged GPU but the device only supports `bus` reset (which
    resets neighbors in the same slot too).

/sys/bus/pci/devices/<bdf>/resource format : 7+ lines, each
"<start> <end> <flags>" hex.

Verdicts (priority-ordered) :
  unbound_device          ≥1 PCI device with VID class != 0x0600
                          (host bridge) AND no driver bound.
  reset_method_bus_only   ≥1 PCI device with reset_method=="bus"
                          ONLY (no flr / af_flr / pm) → device
                          reset would also reset slot peers.
  iomem_masked            /proc/iomem addresses redacted (kptr
                          restriction) — info only, not a fault.
  ok                      every visible non-bridge device has a
                          driver + a non-bus reset method.
  unknown                 /proc/iomem unreadable.

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import Optional


NAME = "iomem_pci_audit"


_PROC_IOMEM = "/proc/iomem"
_PROC_IOPORTS = "/proc/ioports"
_SYS_BUS_PCI = "/sys/bus/pci/devices"


def _read(p: str) -> Optional[str]:
    try:
        with open(p) as f:
            return f.read()
    except OSError:
        return None


def _read_int(p: str) -> Optional[int]:
    t = _read(p)
    if t is None:
        return None
    try:
        return int(t.strip(), 0)  # autobase for '0x...' or decimal
    except ValueError:
        return None


_IOMEM_LINE_RE = re.compile(
    r"^(?P<indent>\s*)(?P<start>[0-9a-f]+)-(?P<end>[0-9a-f]+)"
    r"\s*:\s*(?P<label>.+)$"
)


def parse_iomem(text: Optional[str]) -> dict:
    """Returns {region_count, top_labels: [{label, addr_zero}], masked}."""
    out: dict = {"region_count": 0, "top_labels": [], "masked": False}
    if not text:
        return out
    labels: list = []
    masked_count = 0
    for line in text.splitlines():
        m = _IOMEM_LINE_RE.match(line)
        if not m:
            continue
        out["region_count"] += 1
        start = m.group("start")
        label = m.group("label").strip()
        depth = len(m.group("indent")) // 2
        labels.append({"label": label, "depth": depth})
        # All-zero start = kptr_restrict redaction.
        if start.strip("0") == "":
            masked_count += 1
    if masked_count > out["region_count"] * 0.5:
        out["masked"] = True
    # Only top-level labels (depth 0) for summary.
    out["top_labels"] = [l for l in labels if l["depth"] == 0][:20]
    return out


def list_pci_devices(sys_pci: str = _SYS_BUS_PCI) -> list:
    if not os.path.isdir(sys_pci):
        return []
    out: list = []
    try:
        for bdf in sorted(os.listdir(sys_pci)):
            d = os.path.join(sys_pci, bdf)
            if not os.path.isdir(d):
                continue
            class_int = _read_int(os.path.join(d, "class"))
            try:
                driver = os.path.basename(os.readlink(
                    os.path.join(d, "driver")))
            except OSError:
                driver = None
            reset_method = (_read(os.path.join(d, "reset_method"))
                                or "").strip() or None
            numa_node = _read_int(os.path.join(d, "numa_node"))
            enable = _read_int(os.path.join(d, "enable"))
            vendor = (_read(os.path.join(d, "vendor"))
                          or "").strip() or None
            device = (_read(os.path.join(d, "device"))
                          or "").strip() or None
            out.append({
                "bdf": bdf,
                "vendor": vendor,
                "device": device,
                "class": class_int,
                "driver": driver,
                "reset_method": reset_method,
                "numa_node": numa_node,
                "enable": enable,
            })
    except OSError:
        return []
    return out


def is_host_bridge(class_int: Optional[int]) -> bool:
    """PCI base class 0x06 = bridge ; subclass 0x00 = host bridge."""
    if class_int is None:
        return False
    base = (class_int >> 16) & 0xff
    return base == 0x06


_RECIPE_UNBOUND = (
    "# PCI device(s) with no kernel driver bound. May be intentional\n"
    "# (vfio passthrough, hardware not provisioned in this OS), or a\n"
    "# missing driver. Inspect with :\n"
    "lspci -k\n"
    "# Then either install/load the right module :\n"
    "sudo modprobe <DRIVER>\n"
    "# Or whitelist for vfio-pci (passthrough)."
)

_RECIPE_RESET_METHOD_BUS = (
    "# PCI device(s) only support `bus` reset (no FLR / AF-FLR / PM).\n"
    "# A bus reset propagates to every device in the same IOMMU group\n"
    "# — neighbors will also reset. Verify which devices share the\n"
    "# group :\n"
    "for g in /sys/kernel/iommu_groups/*/devices; do\n"
    "  echo \"Group $(basename $(dirname $g)) : $(ls $g)\"\n"
    "done\n"
    "# Workaround : ACS-override patch (security trade-off) or move\n"
    "# the device to a slot with its own IOMMU group."
)


def classify(iomem: dict, pci_devices: list) -> dict:
    if iomem.get("region_count", 0) == 0 and not pci_devices:
        return {"verdict": "unknown",
                "reason": ("/proc/iomem + /sys/bus/pci unreadable."),
                "recommendation": ""}
    # 1) Unbound devices (excluding host bridges).
    unbound = [d for d in pci_devices
                if not is_host_bridge(d.get("class"))
                and not d.get("driver")]
    if unbound:
        names = ", ".join(
            f"{d['bdf']} ({d['vendor']}:{d['device']})"
            for d in unbound[:5])
        return {"verdict": "unbound_device",
                "reason": (f"{len(unbound)} PCI device(s) with no "
                           f"kernel driver bound : {names}."),
                "recommendation": _RECIPE_UNBOUND}
    # 2) Reset method `bus` only.
    bus_only = [d for d in pci_devices
                  if d.get("reset_method")
                  and d["reset_method"].strip() == "bus"]
    if bus_only:
        names = ", ".join(d["bdf"] for d in bus_only[:5])
        return {"verdict": "reset_method_bus_only",
                "reason": (f"{len(bus_only)} PCI device(s) only "
                           f"support 'bus' reset (no FLR/PM) : "
                           f"{names}. Reset would propagate to "
                           f"IOMMU-group peers."),
                "recommendation": _RECIPE_RESET_METHOD_BUS}
    if iomem.get("masked"):
        return {"verdict": "iomem_masked",
                "reason": (f"/proc/iomem addresses redacted by "
                           f"kptr_restrict — labels visible but "
                           f"addresses zeroed (info only)."),
                "recommendation": ""}
    return {"verdict": "ok",
            "reason": (f"{iomem.get('region_count', 0)} iomem "
                       f"region(s), {len(pci_devices)} PCI device(s), "
                       f"all non-bridge devices have a driver + "
                       f"flexible reset method."),
            "recommendation": ""}


def status(cfg=None) -> dict:
    iomem = parse_iomem(_read(_PROC_IOMEM))
    pci_devices = list_pci_devices(_SYS_BUS_PCI)
    verdict = classify(iomem, pci_devices)
    return {
        "ok": iomem.get("region_count", 0) > 0 or bool(pci_devices),
        "iomem": iomem,
        "pci_device_count": len(pci_devices),
        "pci_devices": pci_devices,
        "verdict": verdict,
    }
