"""Module pcie_dpc_audit — PCIe Downstream Port Containment
(DPC) auditor (R&D #90.4).

Three existing AER modules cover error *counters* :

  * pcie_aer.py             — per-device AER status snapshot
  * pcie_aer_trend.py       — counter trend
  * pcie_aer_fleet_audit.py — fleet rollup

None look at the DPC subsystem (/sys/bus/pci/devices/*/dpc/),
which is the PCIe root port's *automatic* containment
mechanism : when a downstream device generates a fatal AER
error, DPC immediately quarantines that link, preventing
cascading bus reset / system hang. The downside is the
contained device vanishes from the bus — silently, if the
admin isn't watching.

This audit owns the DPC posture and trigger detection.

Reads :

  /sys/bus/pci/devices/<bdf>/dpc/dpc_cap     capability bits
                                             (presence = port
                                             has DPC hardware)
  /sys/bus/pci/devices/<bdf>/dpc/dpc_ctl     control bits ;
                                             non-zero = DPC
                                             enabled
  /sys/bus/pci/devices/<bdf>/dpc/dpc_status  status bits ;
                                             non-zero = DPC
                                             trigger fired

Verdicts (worst-first) :

  dpc_triggered            err   any port's dpc_status bit 0
                                 set — downstream device
                                 quarantined ; check dmesg
                                 for the AER fatal.
  dpc_disabled_capable     accent ≥1 port has dpc_cap but
                                 dpc_ctl = 0 — fatal AER
                                 containment is OFF on a
                                 platform that supports it.
  ok                       DPC enabled and quiet on all
                           capable ports, OR no DPC-capable
                           ports at all.
  requires_root            dpc_status mode-600 (rare).
  unknown                  /sys/bus/pci/devices unreadable.

The proposed "dpc_enabled_no_recovery" warn verdict (kernel
lacks pcie_ports=native) was dropped — checking cmdline-vs-
default for native control of PCIe ports requires kernel-
version-aware logic that isn't worth the complexity at this
audit's scope.

stdlib only.
"""
from __future__ import annotations

import os
from typing import Optional

NAME = "pcie_dpc_audit"

DEFAULT_PCI_ROOT = "/sys/bus/pci/devices"


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read().strip()
    except (OSError, PermissionError):
        return None


def parse_dpc_value(text: str) -> Optional[int]:
    """Parse DPC register values. Kernel exposes them either
    as raw decimals ('0', '1') or as hex with 0x prefix.

    Returns None for unreadable / unparseable files."""
    if not text:
        return None
    t = text.strip()
    try:
        if t.startswith("0x") or t.startswith("0X"):
            return int(t, 16)
        return int(t)
    except ValueError:
        return None


def read_dpc(pci_root: str, bdf: str) -> dict:
    """Read all three DPC files for one device.

    Returns {'has_dpc': bool, 'cap': int|None, 'ctl': int|None,
    'status': int|None, 'status_readable': bool}."""
    dpc_dir = os.path.join(pci_root, bdf, "dpc")
    if not os.path.isdir(dpc_dir):
        return {"has_dpc": False, "cap": None, "ctl": None,
                "status": None, "status_readable": True}
    status_text = _read_text(os.path.join(dpc_dir, "dpc_status"))
    return {
        "has_dpc": True,
        "cap": parse_dpc_value(
            _read_text(os.path.join(dpc_dir, "dpc_cap")) or ""),
        "ctl": parse_dpc_value(
            _read_text(os.path.join(dpc_dir, "dpc_ctl")) or ""),
        "status": parse_dpc_value(status_text or ""),
        "status_readable": status_text is not None,
    }


def list_devices(pci_root: str = DEFAULT_PCI_ROOT) -> list:
    if not os.path.isdir(pci_root):
        return []
    try:
        return sorted(os.listdir(pci_root))
    except OSError:
        return []


def classify(devices: list) -> dict:
    if not devices:
        return {"verdict": "unknown",
                "reason": (
                    "/sys/bus/pci/devices unreadable — no "
                    "devices to inspect.")}

    dpc_devs = [d for d in devices if d.get("has_dpc")]
    if not dpc_devs:
        return {"verdict": "ok",
                "reason": (
                    f"{len(devices)} PCI device(s) inspected ; "
                    "no DPC-capable root ports found "
                    "(typical on consumer / desktop boards).")}

    # err — any port has DPC triggered (status bit 0 set)
    triggered = [
        d for d in dpc_devs
        if d.get("status") is not None and d["status"] != 0]
    if triggered:
        names = sorted(d["bdf"] for d in triggered)
        return {
            "verdict": "dpc_triggered",
            "reason": (
                f"{len(triggered)} root port(s) have DPC "
                f"triggered (dpc_status != 0): {names}. A "
                "downstream device has been contained ; check "
                "`dmesg -T | grep -i dpc` for the fatal AER."),
            "ports": names,
        }

    # requires_root — status unreadable on any DPC-capable port
    if any(not d.get("status_readable") for d in dpc_devs):
        return {"verdict": "requires_root",
                "reason": (
                    "/sys/bus/pci/devices/*/dpc/dpc_status "
                    "unreadable — re-run as root.")}

    # accent — port capable but DPC disabled
    disabled = [
        d for d in dpc_devs
        if d.get("ctl") is not None and d["ctl"] == 0]
    if disabled:
        names = sorted(d["bdf"] for d in disabled)
        return {
            "verdict": "dpc_disabled_capable",
            "reason": (
                f"{len(disabled)} root port(s) have DPC "
                f"capability but dpc_ctl=0 ({names[:3]}). "
                "Fatal-AER containment is off — a misbehaving "
                "device could hang the bus instead of being "
                "isolated."),
            "ports": names,
        }

    return {"verdict": "ok",
            "reason": (
                f"{len(dpc_devs)} DPC-capable root port(s) ; "
                "DPC enabled and quiet on all.")}


def status(config: Optional[dict] = None,
           pci_root: str = DEFAULT_PCI_ROOT) -> dict:
    bdfs = list_devices(pci_root)
    devices = []
    for bdf in bdfs:
        info = read_dpc(pci_root, bdf)
        info["bdf"] = bdf
        devices.append(info)
    verdict = classify(devices)
    dpc_count = sum(1 for d in devices if d["has_dpc"])
    return {
        "ok": verdict["verdict"] == "ok",
        "device_count": len(devices),
        "dpc_capable_count": dpc_count,
        "verdict": verdict,
    }
