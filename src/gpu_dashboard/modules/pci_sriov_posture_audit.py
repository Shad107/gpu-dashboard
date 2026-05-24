"""Module pci_sriov_posture_audit — PCI SR-IOV posture audit
(R&D #77.4).

SR-IOV (Single Root I/O Virtualization) lets one PCIe device
present multiple Virtual Functions to guest VMs. On a homelab
desktop the expected state is :

  sriov_totalvfs       capability advertised (usually 0 on
                         consumer hardware, > 0 on Mellanox /
                         Intel server NICs, some Xeon SoCs)
  sriov_numvfs         currently-active VFs (usually 0 on a
                         single-GPU desktop)
  sriov_drivers_autoprobe  1 = kernel auto-binds drivers to VFs

Why on a homelab :

* A leftover `echo 8 > sriov_numvfs` from a prior virtualization
  experiment leaves 8 VFs active across boots, costing ~200 ms
  per device on init + masking driver-binding bugs near the GPU.
* `sriov_drivers_autoprobe=0` set globally is a half-applied
  VFIO passthrough setup that breaks normal PCI binding.

Reads /sys/bus/pci/devices/<bdf>/{sriov_totalvfs, sriov_numvfs,
sriov_drivers_autoprobe, sriov_offset, sriov_stride,
sriov_vf_total_msix}.

Verdicts (priority order) :
  unexpected_vfs_active             ≥1 device with
                                      sriov_numvfs > 0 (active
                                      VFs).
  drivers_autoprobe_disabled_no_vfio sriov_drivers_autoprobe = 0
                                      on ≥1 device AND vfio
                                      module not loaded.
  sriov_capable_unused              ≥1 device has totalvfs > 0
                                      but numvfs == 0
                                      (informational).
  no_sriov_capable                   no device advertises
                                      sriov_totalvfs (typical
                                      consumer desktop).
  ok                                 SR-IOV configured cleanly.
  unknown                            /sys/bus/pci/devices absent.

stdlib only.
"""
from __future__ import annotations

import os
from typing import List, Optional


NAME = "pci_sriov_posture_audit"


_SYS_PCI_DEVICES = "/sys/bus/pci/devices"
_SYS_MODULE_VFIO = "/sys/module/vfio"


_KNOBS = (
    "sriov_totalvfs", "sriov_numvfs",
    "sriov_drivers_autoprobe", "sriov_offset",
    "sriov_stride", "sriov_vf_total_msix",
)


def _read(path: str) -> Optional[str]:
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def _read_int(path: str) -> Optional[int]:
    t = _read(path)
    if t is None:
        return None
    try:
        return int(t)
    except ValueError:
        return None


def list_sriov_devices(sys_path: str = _SYS_PCI_DEVICES
                            ) -> List[dict]:
    """Returns one entry per PCI device that exposes
    sriov_totalvfs (i.e. SR-IOV capable). Non-capable devices
    are skipped."""
    if not os.path.isdir(sys_path):
        return []
    out: List[dict] = []
    try:
        names = sorted(os.listdir(sys_path))
    except OSError:
        return []
    for n in names:
        d = os.path.join(sys_path, n)
        tv = _read_int(os.path.join(d, "sriov_totalvfs"))
        if tv is None:
            continue
        entry: dict = {"bdf": n}
        for k in _KNOBS:
            entry[k] = _read_int(os.path.join(d, k))
        out.append(entry)
    return out


def vfio_loaded(sys_module_vfio: str = _SYS_MODULE_VFIO) -> bool:
    return os.path.isdir(sys_module_vfio)


def classify(present: bool, devices: List[dict],
              vfio_present: bool) -> dict:
    if not present:
        return {"verdict": "unknown",
                "reason": "/sys/bus/pci/devices absent.",
                "recommendation": ""}

    if not devices:
        return {"verdict": "no_sriov_capable",
                "reason": ("No PCI device advertises "
                          "sriov_totalvfs — typical consumer "
                          "desktop hardware."),
                "recommendation": ""}

    # 1) unexpected_vfs_active
    active = [d for d in devices
                if (d.get("sriov_numvfs") or 0) > 0]
    if active:
        sample = ", ".join(
            f"{d['bdf']} numvfs={d['sriov_numvfs']}"
                for d in active[:3])
        return {"verdict": "unexpected_vfs_active",
                "reason": (f"{len(active)} PCI device(s) have "
                          f"active VFs : {sample}."),
                "recommendation": _recipe_active_vfs()}

    # 2) drivers_autoprobe_disabled_no_vfio
    autoprobe_off = [d for d in devices
                            if d.get("sriov_drivers_autoprobe")
                                == 0]
    if autoprobe_off and not vfio_present:
        sample = ", ".join(d["bdf"] for d in autoprobe_off[:3])
        return {"verdict":
                    "drivers_autoprobe_disabled_no_vfio",
                "reason": (f"{len(autoprobe_off)} device(s) "
                          f"have sriov_drivers_autoprobe=0 but "
                          f"vfio module is not loaded : "
                          f"{sample}. Half-applied VFIO setup."),
                "recommendation": _recipe_autoprobe()}

    # 3) sriov_capable_unused — informational
    unused = [d for d in devices
                  if (d.get("sriov_totalvfs") or 0) > 0
                    and (d.get("sriov_numvfs") or 0) == 0]
    if unused and len(unused) == len(devices):
        sample = ", ".join(
            f"{d['bdf']} totalvfs={d['sriov_totalvfs']}"
                for d in unused[:3])
        return {"verdict": "sriov_capable_unused",
                "reason": (f"{len(unused)} SR-IOV-capable PCI "
                          f"device(s) with no active VFs "
                          f"(informational) : {sample}."),
                "recommendation": _recipe_unused()}

    return {"verdict": "ok",
            "reason": (f"{len(devices)} SR-IOV-capable device(s)"
                      f" ; configuration clean."),
            "recommendation": ""}


def status(config=None,
            sys_path: str = _SYS_PCI_DEVICES,
            sys_module_vfio: str = _SYS_MODULE_VFIO) -> dict:
    present = os.path.isdir(sys_path)
    devices = list_sriov_devices(sys_path) if present else []
    vfio_present = vfio_loaded(sys_module_vfio)
    verdict = classify(present, devices, vfio_present)
    return {"ok": present,
              "sriov_capable_count": len(devices),
              "active_vf_count": sum(
                  (d.get("sriov_numvfs") or 0) for d in devices),
              "vfio_module_loaded": vfio_present,
              "devices": devices,
              "verdict": verdict}


# ── recovery recipes ────────────────────────────────────────────

def _recipe_active_vfs() -> str:
    return ("# Active VFs on a single-GPU desktop are usually a\n"
            "# leftover from a virtualization experiment.\n"
            "# Identify the device :\n"
            "for d in /sys/bus/pci/devices/*/sriov_numvfs; do\n"
            "  n=$(cat $d)\n"
            "  [ \"$n\" -gt 0 ] && echo \"$d = $n\"\n"
            "done\n"
            "# Disable VFs :\n"
            "echo 0 | sudo tee /sys/bus/pci/devices/<bdf>/sriov_numvfs\n")


def _recipe_autoprobe() -> str:
    return ("# sriov_drivers_autoprobe=0 without vfio module is\n"
            "# usually a stale VFIO setup. Either :\n"
            "#  - load vfio modules :\n"
            "sudo modprobe vfio vfio-pci\n"
            "#  - or re-enable autoprobe :\n"
            "for d in /sys/bus/pci/devices/*/sriov_drivers_autoprobe; do\n"
            "  echo 1 | sudo tee \"$d\" 2>/dev/null\n"
            "done\n")


def _recipe_unused() -> str:
    return ("# Informational : SR-IOV-capable devices with no\n"
            "# active VFs is the expected resting state for a\n"
            "# single-GPU desktop. No action needed unless you\n"
            "# intend to spawn VFs.\n")
