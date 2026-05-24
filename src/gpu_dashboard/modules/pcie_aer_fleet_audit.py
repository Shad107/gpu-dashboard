"""Module pcie_aer_fleet_audit — fleet-wide PCIe AER auditor
(R&D #77.3).

Existing pcie_aer / pcie_aer_trend modules check the NVIDIA VGA
device. This audit walks /sys/bus/pci/devices/* and reads
aer_dev_correctable / aer_dev_fatal / aer_dev_nonfatal on
EVERY endpoint that exposes them — NVMe, NICs, USB xHCI, root
ports, bridges, audio controllers.

A failing PCIe root port or NVMe in the same chassis as the
3090 frequently shows AER drift months before user-visible
failure ; the existing NVIDIA-only audit misses the NVMe
holding model weights or the 2.5GbE NIC pushing inference
traffic.

aer_dev_<class> file format (multi-line) :
  ErrorTypeName <count>
  ErrorTypeName <count>
  ...

Reads :
  /sys/bus/pci/devices/<bdf>/aer_dev_correctable
  /sys/bus/pci/devices/<bdf>/aer_dev_fatal
  /sys/bus/pci/devices/<bdf>/aer_dev_nonfatal
  /sys/bus/pci/devices/<bdf>/class               PCI class id
  /sys/bus/pci/devices/<bdf>/uevent              DRIVER=

Verdicts (priority order) :
  fleet_fatal                ≥1 device has aer_dev_fatal sum > 0.
  fleet_nonfatal             ≥1 device has aer_dev_nonfatal sum
                               > 0.
  bridge_correctable_storm   PCI bridge / root port with
                               correctable > 100 (CRC errors
                               eating capacity).
  nvme_or_nic_correctable    NVMe / NIC with correctable > 0
                               (early-warning signal months
                               before user-visible failure).
  ok                         all counters clean across the fleet.
  unknown                    /sys/bus/pci/devices absent OR no
                               device exposes AER counters.

stdlib only.
"""
from __future__ import annotations

import os
from typing import Dict, List, Optional


NAME = "pcie_aer_fleet_audit"


_SYS_PCI_DEVICES = "/sys/bus/pci/devices"


# PCI class prefixes (first 2 bytes of 0xCCSSPP)
_PCI_CLASS_BRIDGE = 0x06            # Bridge device
_PCI_CLASS_NETWORK = 0x02           # Network controller
_PCI_CLASS_NVME = 0x010802          # Mass storage / NVMe

_BRIDGE_STORM_THRESHOLD = 100


def _read(path: str) -> Optional[str]:
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return None


def _read_aer_sum(path: str) -> Optional[int]:
    """Each line of aer_dev_* is 'Name <int>'. Sum the ints."""
    txt = _read(path)
    if txt is None:
        return None
    total = 0
    seen = False
    for line in txt.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        try:
            total += int(parts[1])
            seen = True
        except ValueError:
            continue
    return total if seen else None


def _read_class(bdf_dir: str) -> Optional[int]:
    txt = _read(os.path.join(bdf_dir, "class"))
    if txt is None:
        return None
    txt = txt.strip()
    try:
        return int(txt, 0)
    except ValueError:
        return None


def _read_driver(bdf_dir: str) -> Optional[str]:
    txt = _read(os.path.join(bdf_dir, "uevent"))
    if not txt:
        return None
    for line in txt.splitlines():
        if line.startswith("DRIVER="):
            return line.split("=", 1)[1].strip()
    return None


def _classify_device(class_id: Optional[int],
                          driver: Optional[str]) -> str:
    """Returns one of 'bridge', 'nvme', 'nic', 'gpu', 'other'."""
    if class_id is None:
        return "other"
    top = (class_id >> 16) & 0xFF
    if top == _PCI_CLASS_BRIDGE:
        return "bridge"
    if top == _PCI_CLASS_NETWORK:
        return "nic"
    if class_id == _PCI_CLASS_NVME:
        return "nvme"
    if top == 0x03:  # Display controller
        return "gpu"
    return "other"


def list_aer_devices(sys_path: str = _SYS_PCI_DEVICES
                          ) -> List[dict]:
    if not os.path.isdir(sys_path):
        return []
    out: List[dict] = []
    try:
        names = sorted(os.listdir(sys_path))
    except OSError:
        return []
    for n in names:
        d = os.path.join(sys_path, n)
        corr = _read_aer_sum(
            os.path.join(d, "aer_dev_correctable"))
        if corr is None:
            continue
        fatal = _read_aer_sum(
            os.path.join(d, "aer_dev_fatal"))
        nonfatal = _read_aer_sum(
            os.path.join(d, "aer_dev_nonfatal"))
        class_id = _read_class(d)
        driver = _read_driver(d)
        out.append({
            "bdf": n,
            "class_id": class_id,
            "driver": driver,
            "kind": _classify_device(class_id, driver),
            "correctable": corr,
            "fatal": fatal or 0,
            "nonfatal": nonfatal or 0,
        })
    return out


def classify(present: bool, devices: List[dict]) -> dict:
    if not present:
        return {"verdict": "unknown",
                "reason": "/sys/bus/pci/devices absent.",
                "recommendation": ""}

    if not devices:
        return {"verdict": "unknown",
                "reason": ("/sys/bus/pci/devices present but no "
                          "device exposes aer_dev_* counters "
                          "(AER not enabled in kernel)."),
                "recommendation": _recipe_no_aer()}

    # 1) fleet_fatal
    fatal = [d for d in devices if d["fatal"] > 0]
    if fatal:
        sample = ", ".join(
            f"{d['bdf']}({d['kind']}) fatal={d['fatal']}"
                for d in fatal[:3])
        return {"verdict": "fleet_fatal",
                "reason": (f"{len(fatal)} PCIe device(s) report "
                          f"fatal AER errors : {sample}."),
                "recommendation": _recipe_fatal()}

    # 2) fleet_nonfatal
    nonfatal = [d for d in devices if d["nonfatal"] > 0]
    if nonfatal:
        sample = ", ".join(
            f"{d['bdf']}({d['kind']}) nonfatal={d['nonfatal']}"
                for d in nonfatal[:3])
        return {"verdict": "fleet_nonfatal",
                "reason": (f"{len(nonfatal)} PCIe device(s) "
                          f"report non-fatal AER errors : "
                          f"{sample}."),
                "recommendation": _recipe_nonfatal()}

    # 3) bridge_correctable_storm
    storm = [d for d in devices
                if d["kind"] == "bridge"
                  and d["correctable"]
                          > _BRIDGE_STORM_THRESHOLD]
    if storm:
        sample = ", ".join(
            f"{d['bdf']} correctable={d['correctable']}"
                for d in storm[:3])
        return {"verdict": "bridge_correctable_storm",
                "reason": (f"{len(storm)} PCIe bridge(s) with "
                          f">{_BRIDGE_STORM_THRESHOLD} "
                          f"correctable AER events : {sample}."),
                "recommendation": _recipe_bridge_storm()}

    # 4) nvme_or_nic_correctable
    storage_net = [d for d in devices
                          if d["kind"] in ("nvme", "nic")
                            and d["correctable"] > 0]
    if storage_net:
        sample = ", ".join(
            f"{d['bdf']}({d['kind']}) correctable="
            f"{d['correctable']}"
                for d in storage_net[:3])
        return {"verdict": "nvme_or_nic_correctable",
                "reason": (f"{len(storage_net)} NVMe/NIC "
                          f"device(s) recorded correctable AER "
                          f": {sample}."),
                "recommendation": _recipe_storage_net()}

    return {"verdict": "ok",
            "reason": (f"{len(devices)} PCIe device(s) audited ;"
                      f" all AER counters clean."),
            "recommendation": ""}


def status(config=None,
            sys_path: str = _SYS_PCI_DEVICES) -> dict:
    present = os.path.isdir(sys_path)
    devices = list_aer_devices(sys_path) if present else []
    verdict = classify(present, devices)
    return {"ok": present,
              "device_count": len(devices),
              "totals": {
                  "correctable": sum(d["correctable"]
                                            for d in devices),
                  "fatal": sum(d["fatal"] for d in devices),
                  "nonfatal": sum(d["nonfatal"]
                                          for d in devices),
              },
              "by_kind": {
                  k: sum(1 for d in devices if d["kind"] == k)
                      for k in ("bridge", "nvme", "nic", "gpu",
                                  "other")},
              "devices_sample": devices[:8],
              "verdict": verdict}


# ── recovery recipes ────────────────────────────────────────────

def _recipe_fatal() -> str:
    return ("# Fatal AER on a PCIe endpoint = link probably bad.\n"
            "# Inspect :\n"
            "for d in /sys/bus/pci/devices/*/aer_dev_fatal; do\n"
            "  sums=$(awk '{s+=$2} END{print s}' \"$d\")\n"
            "  [ \"${sums:-0}\" -gt 0 ] && echo \"$d : $sums\"\n"
            "done\n"
            "# Check dmesg for AER link-down events :\n"
            "sudo dmesg | grep -iE 'AER|PCIe Bus Error' | tail\n"
            "# Reseat cables, swap PCIe slot.\n")


def _recipe_nonfatal() -> str:
    return ("# Non-fatal AER usually = recoverable link errors.\n"
            "# Trend with :\n"
            "for d in /sys/bus/pci/devices/*/aer_dev_nonfatal; do\n"
            "  echo \"$d\"; cat \"$d\"\n"
            "done\n"
            "# Watch dmesg for repeated AER recoveries :\n"
            "sudo dmesg --since=1h | grep -ic AER\n")


def _recipe_bridge_storm() -> str:
    return ("# PCIe bridge with > 100 correctable errors = CRC\n"
            "# storm. Often a marginal riser cable or backplane.\n"
            "for d in /sys/bus/pci/devices/*/aer_dev_correctable; do\n"
            "  sums=$(awk '{s+=$2} END{print s}' \"$d\")\n"
            "  [ \"${sums:-0}\" -gt 100 ] && echo \"$d : $sums\"\n"
            "done\n"
            "# Force link retrain :\n"
            "echo 1 | sudo tee /sys/bus/pci/devices/<bdf>/reset\n")


def _recipe_storage_net() -> str:
    return ("# NVMe / NIC correctable AER = early warning months\n"
            "# before user-visible failure. Investigate :\n"
            "for d in /sys/bus/pci/devices/*; do\n"
            "  class=$(cat $d/class 2>/dev/null)\n"
            "  corr=$(awk '{s+=$2} END{print s}' \\\n"
            "         $d/aer_dev_correctable 2>/dev/null)\n"
            "  [ \"${corr:-0}\" -gt 0 ] && \\\n"
            "    echo \"$(basename $d) class=$class corr=$corr\"\n"
            "done\n"
            "# Replace cable / reseat / update firmware.\n")


def _recipe_no_aer() -> str:
    return ("# No PCI device exposes aer_dev_* counters. Either :\n"
            "#  - kernel built without CONFIG_PCIEAER\n"
            "#  - 'pci=noaer' on kernel cmdline\n"
            "grep CONFIG_PCIEAER /boot/config-$(uname -r)\n"
            "cat /proc/cmdline | tr ' ' '\\n' | grep -i aer\n")
